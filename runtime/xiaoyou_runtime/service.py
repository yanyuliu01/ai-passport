"""收消息、排队、一条一条交给小幽，并让调用方能等到结果。

语音消息多一步：先把录音交给语音识别，得到文字后和打字的消息走同一条路。

每个对话一条线，线上的话一句一句处理：两句话不会同时去续同一个会话。这条线只做
快的事——小幽听懂、马上能答的答掉、要花时间的派到后台——所以一条消息在这里“处理
完”（status 变成 done）时，后台的事可能才刚开始；它的进展和结果出现在卡上，调用方
用 feed 等卡的变化。不同的对话互不等待。

别的 Runtime 转过来的话（hop 大于 0）是例外：那边在等最终结果，所以这样的消息要
等到那件事结束才算处理完。
"""

import sys
import threading
import time
import uuid
from collections import OrderedDict, deque
from typing import Any, Callable, Deque, Dict, List, Optional, Union

from .agents import AgentError
from .cards import ACTIVE
from .stt import Stt, SttError, describe_wav
from .store import Store
from .xiaoyou import Xiaoyou

MAX_TEXT_CHARS = 8000
MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_KEPT_MESSAGES = 200
MAX_NAME_CHARS = 64
MAX_SHARED_TURNS = 30
MAX_SHARED_TEXT_CHARS = 4000
MAX_SHARED_REPLY_CHARS = 8000
MAX_EVENTS = 20
MAX_EVENT_CHARS = 200
MAX_HOP = 3
# 同时在处理的对话数上限；超过的对话等前面的让出位置。
MAX_BUSY_CONVERSATIONS = 4


class RequestError(Exception):
    """调用方给的内容不对；消息可以直接返回给调用方。"""


def _name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_NAME_CHARS:
        raise RequestError("%s 应该是 1 到 %d 个字符的字符串" % (what, MAX_NAME_CHARS))
    if any(ch.isspace() or ch in "/\\" or ord(ch) < 0x20 for ch in value):
        raise RequestError("%s 不能包含空白或斜杠" % what)
    return value


class Service:
    def __init__(self, xiaoyou: Xiaoyou, store: Store, stt: Optional[Stt] = None):
        self._xiaoyou = xiaoyou
        self._store = store
        self._stt = stt if stt is not None else Stt()
        self._audio: Dict[str, Any] = {}  # 消息编号 → 还没识别的录音文件
        self._changed = threading.Condition()
        self._messages = OrderedDict()  # type: OrderedDict[str, Dict[str, Any]]
        self._by_client_id: Dict[str, str] = {}
        # 对话名 → 这条线上还没处理的东西：消息编号，或者一件要在这条线上做的事（转述
        # 帮手的结果）。有工作线程在处理的对话记在 _busy 里。
        self._lanes: Dict[str, Deque[Union[str, Callable[[], None]]]] = {}
        self._busy: Dict[str, threading.Thread] = {}
        self._slots = threading.Semaphore(MAX_BUSY_CONVERSATIONS)
        self._closed = False
        # 卡的编号 → 等这件事结束才算处理完的消息（别的 Runtime 转过来的）。
        self._held: Dict[str, str] = {}
        xiaoyou.defer = self._defer
        xiaoyou.cards.subscribe(self._card_changed)

    def agents(self) -> List[Dict[str, Any]]:
        """这台 Runtime 上小幽能用的代理，给客户端显示和点名用。"""
        return [
            {
                "name": agent.name, "type": agent.type, "description": agent.description,
                "speaks": agent.speaks, "default": agent.name == self._xiaoyou.default_agent,
            }
            for agent in self._xiaoyou.agents()
        ]

    def submit(self, text: Any, conversation: Any = "default", client_id: Any = None,
               agent: Any = None, hop: Any = 0, card: Any = None,
               pin: Any = False) -> Dict[str, Any]:
        """登记一条消息并立刻返回；client_id 相同的重复提交返回同一条，不会重做。

        pin 为 True 表示主人是打开 card 那件事、在它里面说的这句话：一定归到它。"""
        if not isinstance(text, str) or not text.strip():
            raise RequestError("text 不能为空")
        if len(text) > MAX_TEXT_CHARS:
            raise RequestError("text 不能超过 %d 个字符" % MAX_TEXT_CHARS)
        return self._enqueue("text", text, None, conversation, client_id, agent, hop, card,
                             pin is True)

    def submit_voice(self, audio: Any, conversation: Any = "default",
                     client_id: Any = None, agent: Any = None,
                     card: Any = None) -> Dict[str, Any]:
        """登记一条语音消息：audio 是 16 位单声道 WAV 的全部字节。识别在排队处理时进行。"""
        if not isinstance(audio, (bytes, bytearray)) or not audio:
            raise RequestError("录音是空的")
        if len(audio) > MAX_AUDIO_BYTES:
            raise RequestError("录音不能超过 %d MB" % (MAX_AUDIO_BYTES // (1024 * 1024)))
        if self._stt.name == "none":
            raise RequestError(
                "Runtime 还没有配置语音识别。在 config.json 里设置 stt（见 README 的“语音”一节）"
            )
        return self._enqueue("voice", "", bytes(audio), conversation, client_id, agent, 0, card)

    def _enqueue(self, kind: str, text: str, audio: Optional[bytes], conversation: Any,
                 client_id: Any, agent: Any, hop: Any, card: Any = None,
                 pin: bool = False) -> Dict[str, Any]:
        conversation = _name(conversation, "conversation")
        if card is not None:
            # 主人说这句话时屏幕上的那件事。认不出的编号不算错：当作没带。
            card = _name(card, "card")
        if client_id is not None:
            client_id = _name(client_id, "client_id")
        if agent is not None:
            agent = _name(agent, "agent")
            if not self._xiaoyou.has(agent):
                raise RequestError("没有叫 %s 的代理（现在有：%s）" % (
                    agent, "、".join(item["name"] for item in self.agents())))
        if isinstance(hop, bool) or not isinstance(hop, int) or not 0 <= hop <= MAX_HOP:
            raise RequestError("hop 应该是 0 到 %d 的整数" % MAX_HOP)
        with self._changed:
            if self._closed:
                raise RequestError("Runtime 正在关闭")
            if client_id is not None and client_id in self._by_client_id:
                return self._snapshot(self._messages[self._by_client_id[client_id]])
            message_id = uuid.uuid4().hex
            if audio is not None:
                # 先落盘再登记：格式不对的录音直接拒绝，不进队列。
                path = self._store.voice_file(message_id)
                path.write_bytes(audio)
                try:
                    describe_wav(path)
                except SttError as error:
                    path.unlink()
                    raise RequestError(str(error))
                self._audio[message_id] = path
            message = {
                "id": message_id,
                "client_id": client_id,
                "conversation": conversation,
                "kind": kind,
                "status": "queued",
                "text": text,
                "reply": None,
                "brief": None,
                "mood": "busy",
                "error": None,
                # 调用方指定的代理；没指定就是 None，由小幽决定
                "asked": agent,
                # 主人说这句话时屏幕上的那件事；处理完后是这句话归到的那张卡
                "card": card,
                # 主人是在那件事里面说的：这句话一定归到它
                "pin": pin and card is not None,
                # 接这句话的代理；交给了帮手时是那个帮手
                "agent": None,
                # 正在替小幽干活的帮手；没有转交、或者帮手已经交回结果时是 None
                "helper": None,
                # 给人看的一句话：现在走到哪一步了
                "stage": None,
                "events": [],
                # 这条记录每变一次加一：调用方带着上次看到的值来等，有变化就能立刻知道
                "rev": 0,
                "hop": hop,
                "created_at": time.time(),
                "finished_at": None,
            }
            self._messages[message["id"]] = message
            if client_id is not None:
                self._by_client_id[client_id] = message["id"]
            self._trim()
            snapshot = self._snapshot(message)
            self._line_up(conversation, message_id)
        return snapshot

    def _line_up(self, conversation: str, item: Union[str, Callable[[], None]]) -> None:
        """调用时已经拿着锁：排到这个对话的线上，线上没人在处理就起一个。"""
        self._lanes.setdefault(conversation, deque()).append(item)
        if conversation not in self._busy:
            worker = threading.Thread(
                target=self._drain, args=(conversation,), daemon=True,
                name="xiaoyou-%s" % conversation,
            )
            self._busy[conversation] = worker
            worker.start()

    def _defer(self, conversation: str, work: Callable[[], None]) -> None:
        """小幽要在这个对话的线上做一件事（转述帮手的结果）：排在已经到的话后面。"""
        with self._changed:
            if not self._closed:
                self._line_up(conversation, work)

    # ---- 卡 ----

    def feed(self, conversation: Any, after: Any = 0, wait: float = 0.0) -> Dict[str, Any]:
        """这个对话里序号比 after 大的卡，和还在等回答的授权；没有变化时最多等 wait 秒。"""
        conversation = _name(conversation, "conversation")
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise RequestError("after 应该是不小于 0 的整数")
        result = self._xiaoyou.cards.changed(conversation, after, wait)
        # 授权出现和有了答案都会让那张卡变一次，所以等卡就等到了授权。
        result["approvals"] = self._xiaoyou.approvals.pending(conversation)
        return result

    def approve(self, approval_id: str, decision: Any) -> Optional[bool]:
        """回答一个授权：allow 或 deny。没有这个授权返回 None，已经答过了返回 False。"""
        if decision not in ("allow", "deny"):
            raise RequestError("decision 只能是 allow 或 deny")
        return self._xiaoyou.approvals.answer(approval_id, decision)

    def cards(self, conversation: Any = "default") -> List[Dict[str, Any]]:
        return self._xiaoyou.cards.recent(_name(conversation, "conversation"), 30)

    def cancel(self, card_id: str) -> Optional[Dict[str, Any]]:
        """取消一件事。没有这张卡返回 None；它已经不在做了就原样返回。"""
        if self._xiaoyou.cards.get(card_id) is None:
            return None
        self._xiaoyou.cancel(card_id)
        return self._xiaoyou.cards.get(card_id)

    def _card_changed(self, card: Dict[str, Any]) -> None:
        if card["state"] in ACTIVE:
            return
        with self._changed:
            message_id = self._held.pop(card["id"], None)
        if message_id is not None:
            self._settle(message_id, card)

    def _settle(self, message_id: str, card: Dict[str, Any]) -> None:
        """等着一件事结束的消息：那件事结束了，把最终的话填进去。"""
        said = [entry["text"] for entry in card["entries"] if entry["role"] == "xiaoyou"]
        reply = said[-1] if said else card["brief"]
        if card["state"] == "done":
            self._update(message_id, status="done", reply=reply, brief=card["brief"],
                         mood=card["mood"], stage=None, helper=None, finished_at=time.time())
        else:
            self._update(message_id, status="failed", error=reply or "这件事没做成",
                         mood="oops", stage=None, helper=None, finished_at=time.time())

    @staticmethod
    def _snapshot(message: Dict[str, Any]) -> Dict[str, Any]:
        snapshot = dict(message)
        snapshot["events"] = [dict(event) for event in message["events"]]
        return snapshot

    def get(self, message_id: str, wait: float = 0.0,
            rev: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """查一条消息；wait 大于 0 时最多等这么多秒，直到它处理完。

        带上 rev（上次看到的那条记录里的 rev）时，记录一有变化就返回，不必等到处理完：
        这样调用方能及时看到“交给了谁”。
        """
        deadline = time.monotonic() + max(0.0, wait)
        with self._changed:
            while True:
                message = self._messages.get(message_id)
                if message is None:
                    return None
                remaining = deadline - time.monotonic()
                if message["status"] in ("done", "failed") or remaining <= 0:
                    return self._snapshot(message)
                if rev is not None and message["rev"] != rev:
                    return self._snapshot(message)
                self._changed.wait(remaining)

    def reset(self, conversation: Any) -> bool:
        """让一个对话从头开始：下一句话每个代理都会开新会话。"""
        return self._xiaoyou.reset(_name(conversation, "conversation"))

    def share(self, conversation: Any, turns: Any) -> int:
        """手机带来在别的 Runtime 上发生的对话；返回这台之前不知道的轮数。

        不立刻打扰模型：等这个对话的下一句话到来时一并告诉接话的代理。
        """
        conversation = _name(conversation, "conversation")
        if not isinstance(turns, list) or len(turns) > MAX_SHARED_TURNS:
            raise RequestError("turns 应该是最多 %d 项的列表" % MAX_SHARED_TURNS)
        cleaned = []
        for turn in turns:
            if not isinstance(turn, dict):
                raise RequestError("turns 里的每一项应该是对象")
            text, reply, at = turn.get("text"), turn.get("reply"), turn.get("at", 0)
            if (not isinstance(text, str) or not isinstance(reply, str) or not text.strip()
                    or not reply.strip() or isinstance(at, bool)
                    or not isinstance(at, (int, float))):
                raise RequestError("turns 里的每一项要有非空的 text 和 reply")
            cleaned.append({
                "id": _name(turn.get("id"), "turns[].id"),
                "text": text[:MAX_SHARED_TEXT_CHARS],
                "reply": reply[:MAX_SHARED_REPLY_CHARS],
                "at": at,
            })
        return self._xiaoyou.share(conversation, cleaned)

    def close(self) -> None:
        with self._changed:
            self._closed = True
            workers = list(self._busy.values())
        self._xiaoyou.close()
        for worker in workers:
            worker.join(timeout=5)

    def _trim(self) -> None:
        # 只丢已经处理完的旧消息，还在排队的不能丢。
        for message_id in list(self._messages):
            if len(self._messages) <= MAX_KEPT_MESSAGES:
                break
            old = self._messages[message_id]
            if old["status"] in ("done", "failed"):
                del self._messages[message_id]
                if old["client_id"] is not None:
                    self._by_client_id.pop(old["client_id"], None)

    def _update(self, message_id: str, **fields: Any) -> None:
        with self._changed:
            message = self._messages.get(message_id)
            if message is not None:
                message.update(fields)
                message["rev"] += 1
            self._changed.notify_all()

    def _report(self, message_id: str, kind: str, agent: Optional[str], text: str) -> None:
        """小幽报告这一轮走到了哪一步。"""
        with self._changed:
            message = self._messages.get(message_id)
            if message is None:
                return
            if len(message["events"]) < MAX_EVENTS:
                message["events"].append({
                    "at": time.time(), "kind": kind, "agent": agent,
                    "text": text[:MAX_EVENT_CHARS],
                })
            if kind in ("route", "handoff"):
                message["agent"] = agent
            if kind in ("handoff", "result"):
                message["stage"] = text[:MAX_EVENT_CHARS]
                message["helper"] = agent if kind == "handoff" else None
            message["rev"] += 1
            self._changed.notify_all()

    def _drain(self, conversation: str) -> None:
        """处理一个对话里排着的消息，直到排空。"""
        with self._slots:
            while True:
                with self._changed:
                    lane = self._lanes.get(conversation)
                    if not lane:
                        self._lanes.pop(conversation, None)
                        self._busy.pop(conversation, None)
                        return
                    item = lane.popleft()
                if isinstance(item, str):
                    self._handle(item)
                    continue
                try:
                    item()
                except Exception as error:  # 工作线程不能死
                    sys.stderr.write("这条线上的一件事出错了：%s: %s\n" % (
                        type(error).__name__, error))

    def _handle(self, message_id: str) -> None:
        with self._changed:
            message = self._messages.get(message_id)
            if message is None:
                return
            text, conversation = message["text"], message["conversation"]
            asked, hop, card = message["asked"], message["hop"], message["card"]
            pin = bool(message.get("pin"))
            audio = self._audio.pop(message_id, None)
        if audio is not None:
            self._update(message_id, status="transcribing")
            try:
                text = self._stt.transcribe(audio)
                if not text:
                    raise SttError("没听清，再说一次吧")
            except SttError as error:
                self._update(
                    message_id, status="failed", error=str(error), mood="oops",
                    finished_at=time.time(),
                )
                return
            except Exception as error:
                self._update(
                    message_id, status="failed", mood="oops", finished_at=time.time(),
                    error="语音识别出错：%s: %s" % (type(error).__name__, error),
                )
                return
            finally:
                try:
                    audio.unlink()
                except OSError:
                    pass
            text = text[:MAX_TEXT_CHARS]
        self._update(message_id, status="running", text=text)
        try:
            turn = self._xiaoyou.hear(
                text, conversation, message_id, asked=asked, card=card, hop=hop, pin=pin,
                report=lambda kind, agent, detail: self._report(message_id, kind, agent, detail),
            )
        except AgentError as error:
            self._fail(message_id, conversation, text, str(error))
            return
        except Exception as error:  # 工作线程不能死：记下来，继续处理下一条
            self._fail(message_id, conversation, text,
                       "Runtime 内部出错：%s: %s" % (type(error).__name__, error))
            return
        if hop > 0 and turn.started:
            # 那台 Runtime 在等最终结果：这条消息等那件事结束才算完。
            self._update(message_id, card=turn.card, agent=turn.agent, reply=turn.reply,
                         brief=turn.brief, mood=turn.mood)
            with self._changed:
                self._held[turn.card] = message_id
            current = self._xiaoyou.cards.get(turn.card)
            if current is not None and current["state"] not in ACTIVE:
                self._card_changed(current)
            return
        self._update(
            message_id, status="done", reply=turn.reply, brief=turn.brief, mood=turn.mood,
            agent=turn.agent, card=turn.card, stage=None, helper=None, finished_at=time.time(),
        )

    def _fail(self, message_id: str, conversation: str, text: str, error: str) -> None:
        """这句话没接住：消息记成失败，并留一张卡，对话里看得到这句话和没成的原因。"""
        card = self._xiaoyou.cards.open(conversation, text, "failed")
        self._xiaoyou.cards.update(card["id"], said=text, say=error, brief=error, mood="oops")
        self._update(
            message_id, status="failed", error=error, mood="oops", stage=None, helper=None,
            card=card["id"], finished_at=time.time(),
        )
