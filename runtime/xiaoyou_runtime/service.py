"""收消息、排队、一条一条交给小幽，并让调用方能等到结果。

语音消息多一步：先把录音交给语音识别，得到文字后和打字的消息走同一条路。

每个对话一条线，线上的话一句一句处理：两句话不会同时去续同一个会话。这条线只做
快的事——小幽听懂、马上能答的答掉、要花时间的派到后台——所以一条消息在这里“处理
完”（status 变成 done）时，后台的事可能才刚开始；它的进展和结果出现在卡上，调用方
用 feed 等卡的变化。不同的对话互不等待。

别的 Runtime 转过来的话（hop 大于 0）是例外：那边在等最终结果，所以这样的消息要
等到那件事结束才算处理完。

收下的话先落盘（state/messages.json，见 ledger.py）才回“收到了”。Runtime 重启后：
还没开始处理的打字消息接着排队做；已经在处理（或者是还没识别的录音）的，不知道做到
哪一步了，不自动重做，标成没做完，并在对话里留一张卡说明，主人可以再说一次。

每条消息变化时从卡那里取一个序号（和卡共用），feed 带上 messages=1 时，一个游标就能
同时拿到变了的卡和变了的消息：另一台手机能看到这边刚说的话和正在生成的回复。
"""

import json
import sys
import threading
import time
import uuid
from collections import OrderedDict, deque
from typing import Any, Callable, Deque, Dict, List, Optional, Union

from .agents import AgentError
from .cards import ACTIVE
from .config import EFFORT_LEVELS
from .ledger import Ledger, content_hash, dedupe_key
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
# 等卡的时候隔这么久看一眼用量变没变。
FEED_USAGE_SECONDS = 5.0
# 一次 feed 最多带这么多字节的卡和消息，其余分几次给。
FEED_BUDGET_BYTES = 48 * 1024
FINISHED = ("done", "failed")
RESTART_NOTE = "Runtime 重启了，这句话没处理完，可以再说一次"
RESTART_VOICE_NOTE = "Runtime 重启了，这段录音没来得及识别，再说一次吧"


class RequestError(Exception):
    """调用方给的内容不对；消息可以直接返回给调用方。"""


class ConflictError(RequestError):
    """同一个对话里，同一个 client_id 带来了不一样的内容（HTTP 409）。"""


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
        self._ledger = Ledger(store.state_dir / "messages.json")
        messages, dedupe, seq = self._ledger.load()
        self._messages = messages  # type: OrderedDict[str, Dict[str, Any]]
        # 对话 + client_id → 那句话的编号、内容指纹（见 ledger.py）
        self._dedupe = dedupe  # type: OrderedDict[str, Dict[str, Any]]
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
        # 序号接着上次往上走：卡和消息两边见过的最大值之后。
        if seq > xiaoyou.cards.seq:
            xiaoyou.cards.bump(at_least=seq)
        self._recover()

    def _recover(self) -> None:
        """Runtime 刚启动：上次收下但没处理完的话。"""
        requeue, unfinished = [], []
        for message in self._messages.values():
            if message["status"] in FINISHED:
                continue
            if message["status"] == "queued" and message["kind"] == "text":
                requeue.append(message["id"])
            else:
                unfinished.append(message["id"])
        for message_id in unfinished:
            message = self._messages[message_id]
            voice = message["kind"] == "voice" and not message["text"]
            note = RESTART_VOICE_NOTE if voice else RESTART_NOTE
            card = self._xiaoyou.cards.open(message["conversation"],
                                            message["text"] or "语音消息", "failed")
            self._xiaoyou.cards.update(
                card["id"], said=message["text"] or "（语音）", say=note, brief=note,
                mood="oops", turn_id=message_id,
                scope="task" if message.get("pin") else "main")
            message.update(status="failed", error=note, mood="oops", stage=None, helper=None,
                           card=card["id"], partial="", finished_at=time.time())
            message["rev"] += 1
            message["seq"] = self._xiaoyou.cards.bump()
            self._remember(message)
        with self._changed:
            for message_id in requeue:
                self._line_up(self._messages[message_id]["conversation"], message_id)
            if unfinished or requeue:
                self._save()

    @property
    def artifacts(self):
        return self._xiaoyou.artifacts

    @property
    def card_store(self):
        return self._xiaoyou.cards

    def agents(self) -> List[Dict[str, Any]]:
        """这台 Runtime 上小幽能用的代理，给客户端显示和点名用。

        model 是它实际用的模型（跑过才知道，没跑过是配置里写的，都没有是 None）；effort 是
        平时的努力程度，efforts / models 是派活时可以选的。"""
        tiers = {entry["name"]: entry for entry in self._xiaoyou.usage.snapshot()["agents"]}
        return [
            {
                "name": agent.name, "type": agent.type, "description": agent.description,
                "speaks": agent.speaks, "default": agent.name == self._xiaoyou.default_agent,
                "model": tiers[agent.name]["model"] if agent.name in tiers else agent.spec.model,
                "effort": agent.spec.effort, "efforts": list(agent.spec.efforts),
                "models": list(agent.spec.models),
            }
            for agent in self._xiaoyou.agents()
        ]

    def usage(self, refresh: bool = False) -> Dict[str, Any]:
        """订阅额度和每个帮手的模型、档位。refresh 为 True 时现在就去问一次再答（慢）。"""
        if refresh:
            self._xiaoyou.usage.refresh()
        else:
            self._xiaoyou.usage.poke()
        return self._xiaoyou.usage.snapshot()

    def submit(self, text: Any, conversation: Any = "default", client_id: Any = None,
               agent: Any = None, hop: Any = 0, card: Any = None,
               pin: Any = False, effort: Any = None, model: Any = None) -> Dict[str, Any]:
        """登记一条消息并立刻返回；client_id 相同的重复提交返回同一条，不会重做。

        pin 为 True 表示主人是打开 card 那件事、在它里面说的这句话：一定归到它。
        effort / model 是给这句话选的努力程度和模型，交给帮手时用。"""
        if not isinstance(text, str) or not text.strip():
            raise RequestError("text 不能为空")
        if len(text) > MAX_TEXT_CHARS:
            raise RequestError("text 不能超过 %d 个字符" % MAX_TEXT_CHARS)
        return self._enqueue("text", text, None, conversation, client_id, agent, hop, card,
                             pin is True, effort, model)

    def submit_voice(self, audio: Any, conversation: Any = "default",
                     client_id: Any = None, agent: Any = None,
                     card: Any = None, pin: Any = False, effort: Any = None,
                     model: Any = None) -> Dict[str, Any]:
        """登记一条语音消息：audio 是 16 位单声道 WAV 的全部字节。识别在排队处理时进行。"""
        if not isinstance(audio, (bytes, bytearray)) or not audio:
            raise RequestError("录音是空的")
        if len(audio) > MAX_AUDIO_BYTES:
            raise RequestError("录音不能超过 %d MB" % (MAX_AUDIO_BYTES // (1024 * 1024)))
        if self._stt.name == "none":
            raise RequestError(
                "Runtime 还没有配置语音识别。在 config.json 里设置 stt（见 README 的“语音”一节）"
            )
        return self._enqueue("voice", "", bytes(audio), conversation, client_id, agent, 0, card,
                             pin is True, effort, model)

    def _enqueue(self, kind: str, text: str, audio: Optional[bytes], conversation: Any,
                 client_id: Any, agent: Any, hop: Any, card: Any = None,
                 pin: bool = False, effort: Any = None, model: Any = None) -> Dict[str, Any]:
        conversation = _name(conversation, "conversation")
        if card is not None:
            # 主人说这句话时屏幕上的那件事。认不出的编号不算错：当作没带。
            card = _name(card, "card")
            observed = self._xiaoyou.cards.get(card)
            if observed is not None and observed.get("monitoring_only"):
                raise RequestError("这是电脑端同步的会话，请在电脑端继续对话")
        if client_id is not None:
            client_id = _name(client_id, "client_id")
        if agent is not None:
            agent = _name(agent, "agent")
            if not self._xiaoyou.has(agent):
                raise RequestError("没有叫 %s 的代理（现在有：%s）" % (
                    agent, "、".join(item["name"] for item in self.agents())))
        if isinstance(hop, bool) or not isinstance(hop, int) or not 0 <= hop <= MAX_HOP:
            raise RequestError("hop 应该是 0 到 %d 的整数" % MAX_HOP)
        effort, model = self._tier(agent, effort, model)
        fingerprint = content_hash(kind, text, audio)
        with self._changed:
            if self._closed:
                raise RequestError("Runtime 正在关闭")
            if client_id is not None:
                seen = self._dedupe.get(dedupe_key(conversation, client_id))
                if seen is not None:
                    if seen.get("hash") != fingerprint:
                        raise ConflictError(
                            "client_id %s 在这个对话里已经用过，内容不一样" % client_id)
                    return self._seen(seen)
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
                # 回复还在生成时，已经生成的那部分（整段）；没有流式、或者已经说完时是空串
                "partial": "",
                "brief": None,
                "mood": "busy",
                "error": None,
                # 调用方指定的代理；没指定就是 None，由小幽决定
                "asked": agent,
                # 主人说这句话时屏幕上的那件事；处理完后是这句话归到的那张卡
                "card": card,
                # 主人是在那件事里面说的：这句话一定归到它
                "pin": pin and card is not None,
                # 给这句话选的努力程度和模型；没选就是 None
                "effort": effort,
                "model": model,
                # 接这句话的代理；交给了帮手时是那个帮手
                "agent": None,
                # 正在替小幽干活的帮手；没有转交、或者帮手已经交回结果时是 None
                "helper": None,
                # 给人看的一句话：现在走到哪一步了
                "stage": None,
                "events": [],
                # 这条记录每变一次加一：调用方带着上次看到的值来等，有变化就能立刻知道
                "rev": 0,
                # 这条记录最近一次变化时拿到的序号（和卡共用一条），feed 靠它接着拉
                "seq": self._xiaoyou.cards.bump(),
                "hop": hop,
                "created_at": time.time(),
                "finished_at": None,
            }
            self._messages[message["id"]] = message
            if client_id is not None:
                self._dedupe[dedupe_key(conversation, client_id)] = {
                    "conversation": conversation, "client_id": client_id, "id": message_id,
                    "hash": fingerprint, "status": "queued", "card": card,
                    "at": message["created_at"],
                }
            self._trim()
            # 先落盘再回“收到了”：之后 Runtime 重启，这句话也还在。
            self._save()
            snapshot = self._snapshot(message)
            self._line_up(conversation, message_id)
        return snapshot

    def _seen(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """调用时已经拿着锁：重复提交。消息还留着就给它；已经被挤掉了，给一份最小的。"""
        message = self._messages.get(entry["id"])
        if message is not None:
            return self._snapshot(message)
        return {
            "id": entry["id"], "client_id": entry["client_id"],
            "conversation": entry["conversation"], "kind": "text",
            "status": entry.get("status") or "done", "text": "", "reply": None, "partial": "",
            "brief": None, "mood": "idle", "error": None, "asked": None,
            "card": entry.get("card"), "pin": False, "effort": None, "model": None,
            "agent": None, "helper": None, "stage": None, "events": [], "rev": 0, "seq": 0,
            "hop": 0, "created_at": entry.get("at") or 0, "finished_at": None,
            "event_stream": True, "expired": True,
        }

    def _remember(self, message: Dict[str, Any]) -> None:
        """调用时已经拿着锁（或者还没有别的线程）：去重表里跟着记最终状态和卡。"""
        if message.get("client_id") is None:
            return
        entry = self._dedupe.get(dedupe_key(message["conversation"], message["client_id"]))
        if entry is not None and entry["id"] == message["id"]:
            entry["status"] = message["status"]
            entry["card"] = message.get("card")

    def _save(self) -> None:
        """调用时已经拿着锁：把消息和去重表写下去。写不了不能让工作线程死掉。"""
        try:
            self._ledger.save(self._messages, self._dedupe, self._xiaoyou.cards.seq)
        except OSError as error:
            sys.stderr.write("消息记录写不下去：%s\n" % error)

    def _tier(self, agent: Optional[str], effort: Any, model: Any) -> Any:
        """检查调用方选的努力程度和模型。点了帮手时必须是那个帮手能选的。"""
        specs = {item.name: item.spec for item in self._xiaoyou.agents()}
        if effort is not None:
            if effort not in EFFORT_LEVELS:
                raise RequestError("effort 只能是 %s 之一" % "、".join(EFFORT_LEVELS))
            if agent is not None and effort not in specs[agent].efforts:
                raise RequestError("%s 不能选 %s 这一档（它可选的：%s）" % (
                    agent, effort, "、".join(specs[agent].efforts) or "没有"))
        if model is not None:
            model = _name(model, "model")
            if agent is not None and model != specs[agent].model and model not in specs[agent].models:
                raise RequestError("%s 不能换成 %s（它可换的：%s）" % (
                    agent, model, "、".join(specs[agent].models) or "没有"))
        return effort, model

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

    def feed(self, conversation: Any, after: Any = 0, wait: float = 0.0,
             usage: Any = None, messages: bool = False) -> Dict[str, Any]:
        """这个对话里序号比 after 大的卡，和还在等回答的授权；没有变化时最多等 wait 秒。

        每次都带上现在的用量。usage 是调用方手里那份用量的 rev：带了它，用量变了也会提前返回。
        messages 为 True 时还带上序号比 after 大的消息（这个对话里收下的话，包括正在生成的
        回复）；不带时和以前完全一样，旧客户端不会因为消息变化被频繁叫醒。

        epoch 是这份记录的编号：和客户端上次看到的不一样，说明 Runtime 换了一份记录，客户端
        应该丢掉游标从 0 重新拉。"""
        conversation = _name(conversation, "conversation")
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise RequestError("after 应该是不小于 0 的整数")
        if usage is not None and (isinstance(usage, bool) or not isinstance(usage, int)):
            raise RequestError("usage 应该是整数")
        meter = self._xiaoyou.usage
        meter.poke()  # 有人在看：太久没问过额度就在后台问一次
        cards_store = self._xiaoyou.cards
        deadline = time.monotonic() + max(0.0, wait)
        while True:
            # 先在卡的锁里同时拿到现在的序号和变了的卡，再取序号不超过它的消息：消息取号
            # 和记号是在消息的锁里一起做的，所以序号不超过它的消息此时都已经记好了。
            seq, cards = cards_store.since(conversation, after, include_desktop=True)
            changed = self._messages_since(conversation, after, seq) if messages else []
            remaining = deadline - time.monotonic()
            if (cards or changed or remaining <= 0
                    or (usage is not None and meter.rev != usage)):
                break
            # 等任何东西变（序号超过 seq）；同时隔一会儿看一眼用量变没变。
            cards_store.wait_beyond(seq, remaining if usage is None
                                    else min(remaining, FEED_USAGE_SECONDS))
        result = self._page(seq, cards, changed, messages)
        result["epoch"] = cards_store.epoch
        # 授权出现和有了答案都会让那张卡变一次，所以等卡就等到了授权。
        result["approvals"] = self._xiaoyou.approvals.pending(conversation, include_desktop=True)
        result["usage"] = meter.snapshot()
        return result

    def _messages_since(self, conversation: str, after: int, upto: int) -> List[Dict[str, Any]]:
        with self._changed:
            return [self._snapshot(message) for message in self._messages.values()
                    if message["conversation"] == conversation
                    and after < message.get("seq", 0) <= upto]

    @staticmethod
    def _page(seq: int, cards: List[Dict[str, Any]], messages: List[Dict[str, Any]],
              with_messages: bool) -> Dict[str, Any]:
        """按序号排好，一次最多 FEED_BUDGET_BYTES（至少一样）：剩下的标 more，序号停在
        给出的最后一样上，客户端照常拿这个序号再要就接着往下给。"""
        items = sorted([("card", card) for card in cards]
                       + [("message", message) for message in messages],
                       key=lambda item: item[1]["seq"])
        size = 0
        for index, (_, item) in enumerate(items):
            size += len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
            if size > FEED_BUDGET_BYTES and index > 0:
                result = {"seq": items[index - 1][1]["seq"], "more": True,
                          "cards": [obj for kind, obj in items[:index] if kind == "card"]}
                if with_messages:
                    result["messages"] = [obj for kind, obj in items[:index] if kind == "message"]
                return result
        # 卡被清掉、或者 Runtime 换了一份记录之后，客户端手里的序号可能比这里的大：
        # 把现在的序号给回去，它就知道要从头再同步。
        result = {"seq": seq, "cards": cards}
        if with_messages:
            result["messages"] = messages
        return result

    def approve(self, approval_id: str, decision: Any) -> Optional[bool]:
        """回答一个授权：allow 或 deny。没有这个授权返回 None，已经答过了返回 False。"""
        if decision not in ("allow", "deny"):
            raise RequestError("decision 只能是 allow 或 deny")
        return self._xiaoyou.approvals.answer(approval_id, decision)

    def cards(self, conversation: Any = "default") -> List[Dict[str, Any]]:
        return self._xiaoyou.cards.recent(_name(conversation, "conversation"), 50,
                                         include_desktop=True)

    def cancel(self, card_id: str) -> Optional[Dict[str, Any]]:
        """取消一件事。没有这张卡返回 None；它已经不在做了就原样返回。"""
        observed = self._xiaoyou.cards.get(card_id)
        if observed is None:
            return None
        if observed.get("monitoring_only"):
            raise RequestError("这是电脑端同步的会话，请在电脑端停止任务")
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
        snapshot["event_stream"] = True
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
        # 只丢已经处理完的旧消息，还在排队的不能丢。去重表另外留着（见 ledger.py）。
        for message_id in list(self._messages):
            if len(self._messages) <= MAX_KEPT_MESSAGES:
                break
            if self._messages[message_id]["status"] in FINISHED:
                del self._messages[message_id]

    def _update(self, message_id: str, **fields: Any) -> None:
        with self._changed:
            message = self._messages.get(message_id)
            if message is not None:
                message.update(fields)
                message["rev"] += 1
                message["seq"] = self._xiaoyou.cards.bump()
                self._remember(message)
                self._save()
            self._changed.notify_all()

    def _report(self, message_id: str, kind: str, agent: Optional[str], text: str) -> None:
        """小幽报告这一轮走到了哪一步。"""
        with self._changed:
            message = self._messages.get(message_id)
            if message is None:
                return
            if kind == "partial":
                if text and message.get("first_partial_at") is None:
                    message["first_partial_at"] = time.time()
                # 回复还在生成：到目前为止的那段，整段替换（不是追加），空串是收回。
                if message["status"] == "running" and message["partial"] != text:
                    message["partial"] = text
                    message["rev"] += 1
                    message["seq"] = self._xiaoyou.cards.bump()
                    self._changed.notify_all()
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
            message["seq"] = self._xiaoyou.cards.bump()
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
            effort, model = message.get("effort"), message.get("model")
            audio = self._audio.pop(message_id, None)
            message["processing_started_at"] = time.time()
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
                effort=effort, model=model,
                report=lambda kind, agent, detail: self._report(message_id, kind, agent, detail),
            )
        except AgentError as error:
            self._fail(message_id, conversation, text, str(error), pin)
            return
        except Exception as error:  # 工作线程不能死：记下来，继续处理下一条
            self._fail(message_id, conversation, text,
                       "Runtime 内部出错：%s: %s" % (type(error).__name__, error), pin)
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
            agent=turn.agent, card=turn.card, stage=None, helper=None, partial="",
            finished_at=time.time(),
        )

    def _fail(self, message_id: str, conversation: str, text: str, error: str, pin: bool = False) -> None:
        """这句话没接住：消息记成失败，并留一张卡，对话里看得到这句话和没成的原因。"""
        card = self._xiaoyou.cards.open(conversation, text, "failed")
        self._xiaoyou.cards.update(card["id"], said=text, say=error, brief=error, mood="oops",
                                  turn_id=message_id, scope="task" if pin else "main")
        self._update(
            message_id, status="failed", error=error, mood="oops", stage=None, helper=None,
            card=card["id"], partial="", finished_at=time.time(),
        )
