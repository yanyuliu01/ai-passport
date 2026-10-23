"""收消息、排队、一条一条交给小幽，并让调用方能等到结果。

语音消息多一步：先把录音交给语音识别，得到文字后和打字的消息走同一条路。

每个对话一条线，线上的话一句一句处理：两句话不会同时去续同一个会话。这条线只做
快的事——小幽听懂、马上能答的答掉、要花时间的派到后台——所以一条消息在这里“处理
完”（status 变成 done）时，后台的事可能才刚开始；它的进展和结果出现在卡上，调用方
用 feed 等卡的变化。不同的对话互不等待。

别的 Runtime 转过来的话（hop 大于 0）是例外：那边在等最终结果，所以这样的消息要
等到那件事结束才算处理完。

收下的话先落盘（state/messages.json，见 ledger.py）才回“收到了”。Runtime 重启后：
还没开始处理的打字消息接着排队做（只做这一次）；已经在处理的，不知道做到哪一步了，
不自动重做，操作记成 unknown、消息标成没做完；还没识别的录音（录音启动时已清掉）标成
没做完。两种都在对话里留一张卡说明，主人可以再说一次。操作状态见 operations.py。

每条消息变化时从卡那里取一个序号（和卡共用），feed 带上 messages=1 时，一个游标就能
同时拿到变了的卡和变了的消息：另一台手机能看到这边刚说的话和正在生成的回复。
"""

import json
import re
import sys
import threading
import time
from pathlib import Path
import uuid
from collections import OrderedDict, deque
from typing import Any, Callable, Deque, Dict, List, Optional, Union

from .agents import AgentError
from .cards import ACTIVE
from .config import EFFORT_LEVELS
from .identity import CAPABILITIES, PROTOCOL, fingerprint, host_proof, runtime_id, subject_id
from .devices import Devices, KINDS as DEVICE_KINDS, hash_secret, new_credential
from .ledger import MAX_DEDUPE, Ledger, content_hash, dedupe_key
from . import operations
from .reports import ReportError, Reports
from .stt import Stt, SttError, describe_wav
from .store import Store
from .quick_review import QuickReview
from .iterate.events import EventStore
from .xiaoyou import Xiaoyou
from . import presentation

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
# /v1/sync 的增量一页最多这么多样（同时受 FEED_BUDGET_BYTES 限）。
SYNC_PAGE_ITEMS = 200
# 游标之后变了的超过这么多样：落后太多，一页页追不如直接给一份快照（有界保留窗口）。
SYNC_DELTA_WINDOW = 1000
# 快照只给“现在”：所有事，每段对话最近这么多句话，每件事最近这么多条进展；更早的历史
# 客户端自己缓存着，或者按需去翻（不再从头一页页追，见问题清单 N10）。
SNAPSHOT_MESSAGES = 50
SNAPSHOT_ACTIVITY = 20
SNAPSHOT_BUDGET_BYTES = 128 * 1024
FINISHED = ("done", "failed")
RESTART_NOTE = "Runtime 重启了，这句话没处理完，可以再说一次"
RESTART_VOICE_NOTE = "Runtime 重启了，这段录音没来得及识别，再说一次吧"
MESH_PREFIX = re.compile(r"^[0-9a-f]{12}\.(.+)$")


class RequestError(Exception):
    """调用方给的内容不对；消息可以直接返回给调用方。"""


class ConflictError(RequestError):
    """同一个对话里，同一个 client_id 带来了不一样的内容（HTTP 409）。"""


class UnavailableError(RequestError):
    """这句话没能落盘，所以没收下（HTTP 503）：调用方可以拿同一个 client_id 再试。"""


def _name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_NAME_CHARS:
        raise RequestError("%s 应该是 1 到 %d 个字符的字符串" % (what, MAX_NAME_CHARS))
    if any(ch.isspace() or ch in "/\\" or ord(ch) < 0x20 for ch in value):
        raise RequestError("%s 不能包含空白或斜杠" % what)
    return value


def _size(item: Dict[str, Any]) -> int:
    return len(json.dumps(item, ensure_ascii=False).encode("utf-8"))


def _page_items(items: List[Any]) -> Any:
    """增量一页：按序号，最多 SYNC_PAGE_ITEMS 样、FEED_BUDGET_BYTES 字节（至少一样）。"""
    size = 0
    for index, (_, item) in enumerate(items):
        size += _size(item)
        if index > 0 and (index >= SYNC_PAGE_ITEMS or size > FEED_BUDGET_BYTES):
            return items[:index], True
    return items, False


def _snapshot_items(tasks: List[Dict[str, Any]], messages: List[Dict[str, Any]]) -> Any:
    """快照：从新到旧放事和话，每段对话最多 SNAPSHOT_MESSAGES 句、每件事最多
    SNAPSHOT_ACTIVITY 条进展，放到 SNAPSHOT_BUDGET_BYTES 为止。还没结束、在等回答的事，
    和放进来的话所属的事，总放进来（不然客户端认不出那句话在哪件事里）。
    最后按序号排好。返回 (items, 有没有没放进来的)。"""
    picked: List[Any] = []
    size, counts, truncated = 0, {}, False
    pool = [("task", item) for item in tasks] + [("message", item) for item in messages]
    for kind, item in sorted(pool, key=lambda pair: pair[1]["seq"], reverse=True):
        cost = _size(item)
        if kind == "message":
            activity = item.get("kind") == "activity"
            key = ("activity", item.get("task")) if activity else ("chat", item.get("conversation"))
            if counts.get(key, 0) >= (SNAPSHOT_ACTIVITY if activity else SNAPSHOT_MESSAGES):
                truncated = True
                continue
        busy = kind == "task" and item.get("presentation", {}).get("group") in ("running", "waiting")
        if size + cost > SNAPSHOT_BUDGET_BYTES and picked and not busy:
            truncated = True
            continue
        if kind == "message":
            counts[key] = counts.get(key, 0) + 1
        size += cost
        picked.append((kind, item))
    have = {item["id"] for kind, item in picked if kind == "task"}
    wanted = {item.get("task") for kind, item in picked if kind == "message"}
    picked += [("task", item) for item in tasks if item["id"] in wanted - have]
    return sorted(picked, key=lambda pair: pair[1]["seq"]), truncated


class Service:
    def __init__(self, xiaoyou: Xiaoyou, store: Store, stt: Optional[Stt] = None):
        self._xiaoyou = xiaoyou
        self._owner_writer = xiaoyou.intake.writer("owner", actor="client")
        self._system_writer = xiaoyou.intake.writer("system", actor="runtime")
        self._reply_writer = xiaoyou.intake.writer("xiaoyou", actor="xiaoyou")
        self._store = store
        self._stt = stt if stt is not None else Stt()
        self._audio: Dict[str, Any] = {}  # 消息编号 → 还没识别的录音文件
        self._changed = threading.Condition()
        # 这台 Runtime 的固定编号：客户端拿它和消息编号拼成全局编号。
        self.runtime_id = runtime_id(store.state_dir)
        # 小幽这个主体的编号（统一主体的预留）：和电脑无关，跟着 state 目录走。
        self.subject_id = subject_id(store.state_dir)
        # 每台设备自己的凭据、移出的电脑（devices.py）；名单以权限权威（Mac）为准。
        self.devices = Devices(store.state_dir / "devices.json")
        xiaoyou.cards.set_origin(self.runtime_id)
        # 不在这台电脑上的会话报进来的进展（POST /v1/reports）
        self._reports = Reports(xiaoyou.cards)
        self._ledger = Ledger(store.state_dir / "messages.json")
        messages, dedupe, seq = self._ledger.load()
        self._messages = messages  # type: OrderedDict[str, Dict[str, Any]]
        # 对话 + client_id → 那句话的编号、内容指纹（见 ledger.py）
        self._dedupe = dedupe  # type: OrderedDict[str, Dict[str, Any]]
        # 写盘在锁外做（整份 JSON 可能上 MB，不能挡住 feed 和查询）：锁里只拍一份快照并编号，
        # 写的时候比编号，旧快照不会盖掉新快照。
        self._write_lock = threading.RLock()
        self._version = 0
        self._written = 0
        # 对话名 → 这条线上还没处理的东西：消息编号，或者一件要在这条线上做的事（转述
        # 帮手的结果）。有工作线程在处理的对话记在 _busy 里。
        self._lanes: Dict[str, Deque[Union[str, Callable[[], None]]]] = {}
        self._busy: Dict[str, threading.Thread] = {}
        # 已经被一个工作线程领走的消息：同一句话重复排进队也只处理一次。
        self._claimed: set = set()
        # 在等负责的电脑（连不上，或者还没同步到它的绑定）的话：联系上、绑定变了时再排一次。
        self._parked: Dict[str, str] = {}  # 消息编号 → 对话
        self._slots = threading.Semaphore(MAX_BUSY_CONVERSATIONS)
        self._closed = False
        # 卡的编号 → 等这件事结束才算处理完的消息（别的 Runtime 转过来的）。
        self._held: Dict[str, str] = {}
        # 小幽网络（mesh.py）：加入了网络时由 Mesh 设上。在别的 Runtime 的事里说的话转交给那台。
        self.mesh: Optional[Any] = None
        xiaoyou.defer = self._defer
        xiaoyou.cards.subscribe(self._card_changed)
        # 序号接着上次往上走：卡和消息两边见过的最大值之后。
        if seq > xiaoyou.cards.seq:
            self._system_writer.sequence(at_least=seq)
        self.events = EventStore(store.state_dir, origin=self.runtime_id)
        self._review_startup_events: List[Dict[str, Any]] = []
        self._review_loading = True
        self.review = QuickReview(xiaoyou.approvals, store.state_dir, event_sink=self._review_event)
        self._review_loading = False
        # Recovered queued work may request a tool immediately. Restore its
        # durable policy and event sink before starting any recovery workers.
        from .conversation_metadata import ConversationMetadata
        self.metadata = ConversationMetadata(xiaoyou._config, self._reply_writer)
        source_seq = max([int(v.get('source_revision', 0)) for v in xiaoyou.cards.directory.source_snapshot().values() if v.get('authority') == self.runtime_id] + [xiaoyou.cards.seq])
        if source_seq > xiaoyou.cards.seq:
            self._system_writer.sequence(at_least=source_seq)
        self._recover()
        from .directory_discovery import Discovery
        self.discovery = Discovery(xiaoyou._config, xiaoyou.intake.writer("desktop", actor="discovery"), self.runtime_id, owned=store.session_ids,
            bound_titles=lambda: {r['uid']: r['original_title'] for r in self.directory()['entries'] if r['kind']=='native' and r.get('bound')})
        if xiaoyou._config.desktop_sync:
            self.discovery.start()
        else:
            self.discovery.status = {'phase': 'disabled', 'indexed_files': 0, 'pending_files': 0}

    def _review_event(self, event: Dict[str, Any]) -> None:
        from . import __version__
        fields = {key: value for key, value in event.items() if key not in ("at", "epoch", "id")}
        if "id" in event:
            fields["approval_id"] = event["id"]
        fields["action"] = event.get("change", event["action"])
        fields["result"] = {"storage_failed": "failed", "rejected": "failed", "requested": "pending",
                            "stale": "stale"}.get(event["action"], "success")
        result = self.events.accept({"events": [{"id": uuid.uuid4().hex, "at": event["at"] * 1000,
                "type": "review", "client": {"kind": "runtime", "id": self.runtime_id,
                "version": __version__}, "fields": fields}]})
        if self.mesh is not None:
            self.mesh.publish_events(result["events"])
        elif self._review_loading:
            self._review_startup_events.extend(result["events"])

    def publish_review_startup(self) -> None:
        if self.mesh is not None and self._review_startup_events:
            self.mesh.publish_events(self._review_startup_events)
            self._review_startup_events = []

    def _recover(self) -> None:
        """Runtime 刚启动：上次收下但没处理完的话。"""
        requeue, unfinished = [], []
        for message in self._messages.values():
            if message["status"] in FINISHED or message["operation_state"] in operations.TERMINAL:
                continue
            if message.get("relay_to") and message["status"] == "running":
                # 转交给别的 Runtime 的话：那台可能已经在做，结果到了 mesh.py 会接上。
                continue
            if message["operation_state"] == "pending" and message["kind"] == "text":
                requeue.append(message["id"])
            else:
                unfinished.append(message["id"])
        cards = self._xiaoyou.cards
        writer = self._system_writer
        answered = {}  # 消息编号 → (卡, 小幽那句回复)：回复已经记在卡上的
        for card in cards.recent(None, 10 ** 6):
            for entry in card["entries"]:
                if entry.get("id", "").endswith(":reply"):
                    answered[entry["id"][:-len(":reply")]] = (card["id"], entry["text"])
        for message_id in requeue + unfinished:
            if message_id in answered and self._messages[message_id].get("hop", 0) == 0:
                # 回复已经记在卡上，只是消息记录没来得及写完：这句话其实接住了。
                message = self._messages[message_id]
                card_id, reply = answered[message_id]
                # 结果已经提交、只是投递中断：用存下的结果，不重做。
                message.update(status="done", operation_state="succeeded", reply=reply, card=card_id,
                               stage=None, helper=None, partial="", finished_at=time.time())
                self._system_writer.by(cause=message["id"]).request_changed(message)  # 修订号、序号、日志一起变
                self._remember(message)
                (requeue if message_id in requeue else unfinished).remove(message_id)
        for message_id in unfinished:
            message = self._messages[message_id]
            voice = message["kind"] == "voice" and not message["text"]
            note = RESTART_VOICE_NOTE if voice else RESTART_NOTE
            said = message["text"] or "（语音）"
            existing = cards.get(message["card"]) if message.get("card") else None
            if existing is not None and message.get("hop", 0) > 0:
                # 别的 Runtime 转来、在等那件事结束的：那张卡启动时已经标成没做完了，
                # 不再另开一张，消息跟着它。
                card_id = existing["id"]
            elif existing is not None and message.get("pin"):
                # 主人是在那件事里面说的：没做完的原因也记在那件事上。
                writer.exchange(existing["id"], said=said, say=note, turn_id=message_id,
                             scope="task", kind="note")
                card_id = existing["id"]
            else:
                card = writer.task_open(message["conversation"], message["text"] or "语音消息",
                                  "failed")
                writer.exchange(card["id"], said=said, say=note, brief=note, mood="oops",
                             turn_id=message_id, scope="main", kind="note")
                card_id = card["id"]
            # 已经开始处理的：模型或工具可能已经动了，不知道到哪一步，记 unknown，核对后才能重做。
            started = message["operation_state"] == "running"
            message.update(status="failed", operation_state="unknown" if started else "failed",
                           error=note, mood="oops", stage=None, helper=None,
                           card=card_id, partial="", finished_at=time.time())
            self._system_writer.by(cause=message["id"]).request_changed(message)  # 修订号、序号、日志一起变
            self._remember(message)
        if answered or unfinished or requeue:
            with self._changed:
                snapshot = self._capture()
            self._flush(snapshot)
        with self._changed:
            for message_id in requeue:
                self._line_up(self._messages[message_id]["conversation"], message_id)

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
               pin: Any = False, effort: Any = None, model: Any = None,
               mesh: Optional[Dict[str, Any]] = None, image_ids: Any = None, reply_scope: Any = None) -> Dict[str, Any]:
        """登记一条消息并立刻返回；client_id 相同的重复提交返回同一条，不会重做。

        pin 为 True 表示主人是打开 card 那件事、在它里面说的这句话：一定归到它。
        effort / model 是给这句话选的努力程度和模型，交给帮手时用。
        mesh 只由 mesh.py 给（网络里别的 Runtime 交来的话，不经 HTTP）：{"message_id"：用那台
        给的编号，两边同一句话同一个编号；"delegated_from"：那台的哪件事交来的}。"""
        from .image_inputs import resolve
        from .artifacts import ArtifactError
        try:
            attachments = resolve(self.artifacts, image_ids) if image_ids is not None else []
        except ArtifactError as error:
            raise RequestError(str(error)) from None
        if attachments:
            if text is None:
                text = ""
            if pin or card is not None or mesh:
                raise RequestError("带图消息请从主对话发送；任务续聊和跨电脑转发暂不支持图片")
            if not self._xiaoyou.image_agent(agent):
                raise RequestError("这台 Runtime 没有可看图的 Codex 代理，请先配置后再发送")
        if not isinstance(text, str) or (not text.strip() and not attachments):
            raise RequestError("text 不能为空")
        text = text if text.strip() else "请看看这些图片"
        if len(text) > MAX_TEXT_CHARS:
            raise RequestError("text 不能超过 %d 个字符" % MAX_TEXT_CHARS)
        return self._enqueue("text", text, None, conversation, client_id, agent, hop, card,
                             pin is True, effort, model, mesh or {}, attachments=attachments, reply_scope=reply_scope)

    def submit_voice(self, audio: Any, conversation: Any = "default",
                     client_id: Any = None, agent: Any = None,
                     card: Any = None, pin: Any = False, effort: Any = None,
                     model: Any = None, reply_scope: Any = None) -> Dict[str, Any]:
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
                             pin is True, effort, model, reply_scope=reply_scope)

    def _enqueue(self, kind: str, text: str, audio: Optional[bytes], conversation: Any,
                 client_id: Any, agent: Any, hop: Any, card: Any = None,
                 pin: bool = False, effort: Any = None, model: Any = None,
                 mesh: Optional[Dict[str, Any]] = None, attachments=None, reply_scope=None) -> Dict[str, Any]:
        conversation = _name(conversation, "conversation")
        mesh = mesh or {}
        relay = None
        if card is not None:
            # 主人说这句话时屏幕上的那件事。认不出的编号不算错：当作没带。
            card = _name(card, "card")
            observed = self._xiaoyou.cards.get(card)
            if observed is not None and observed.get("mesh_origin") and pin:
                # 别的 Runtime 的事：这句话交给那台，在那件事里说（mesh.py 转交）。
                if self.mesh is None:
                    raise RequestError("这是别的电脑上的事，这台现在不在小幽网络里，转交不了")
                relay = observed
            if observed is not None and observed.get("monitoring_only") and pin \
                    and observed.get("source") == "external":
                raise RequestError("这是别处上报来的会话，只能查看，请在它原来的地方继续")
            # 电脑上的会话在这里不再拦：看着它说的话先给小幽；要它接着做时能不能接（比如电脑上
            # 这一轮还在跑）由小幽那边判断并回答（xiaoyou.py _continue_desktop）。
        if client_id is not None:
            client_id = _name(client_id, "client_id")
        if agent is not None:
            agent = _name(agent, "agent")
            if relay is None and not self._xiaoyou.has(agent):
                raise RequestError("没有叫 %s 的代理（现在有：%s）" % (
                    agent, "、".join(item["name"] for item in self.agents())))
        if isinstance(hop, bool) or not isinstance(hop, int) or not 0 <= hop <= MAX_HOP:
            raise RequestError("hop 应该是 0 到 %d 的整数" % MAX_HOP)
        if relay is None:
            effort, model = self._tier(agent, effort, model)  # 转交的由那台检查
        if reply_scope not in (None, "main"):
            raise RequestError("reply_scope 不支持")
        if reply_scope == "main" and card is not None:
            current = self._xiaoyou.cards.get(card)
            if current and not current.get("monitoring_only") and current["conversation"] != conversation:
                raise RequestError("补充要求须发回任务的来源聊天")
        fingerprint = content_hash(kind, text, audio)
        if reply_scope == "main":
            fingerprint = content_hash("chat-supplement", fingerprint + "\0" + str(card) + "\0" + str(pin), None)
        if attachments:
            fingerprint = content_hash("images", fingerprint + json.dumps(
                [(item["id"], item["sha256"]) for item in attachments]), None)
        # 一次只收一句：收下（落盘）的先后就是排队的先后；同一个 client_id 的重试要等前一次
        # 写完（或者写失败、撤回）才看得到结果，不会对一句还没写下去的话回“收到了”。
        with self._write_lock:
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
                message_id = mesh.get("message_id") or uuid.uuid4().hex
                if message_id in self._messages:
                    return self._snapshot(self._messages[message_id])  # 同一个转交又来了一次
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
                    # 处理这句话这个操作：收下时就有身份，状态跟着 status 变（operations.py）
                    "operation_id": operations.operation_id(message_id),
                    "operation_state": "pending",
                    "text": text,
                    "attachments": attachments or [],
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
                    "reply_scope": reply_scope,
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
                    # 这条记录最近一次变化时拿到的序号（和卡共用一条），feed 靠它接着拉。
                    # 写下去之前是 0：别的客户端看不到一句可能还会撤回的话。
                    "seq": 0,
                    "hop": hop,
                    "created_at": time.time(),
                    "finished_at": None,
                }
                if relay is not None:
                    # 转交给别的 Runtime：那台的哪件事。这句话的记录以那台为准（同一个编号）。
                    message.update(relay_to=relay["mesh_origin"], relay_card=relay.get("mesh_id"),
                                   relay_name=relay.get("origin_name") or "")
                if mesh.get("delegated_from"):
                    message["delegated_from"] = dict(mesh["delegated_from"])
                routed = mesh.get("routed_generation")
                if isinstance(routed, int) and not isinstance(routed, bool):
                    message["routed_generation"] = routed  # 按负责人转来的（_route 不再转回去）
                self._messages[message["id"]] = message
                if client_id is not None:
                    self._dedupe[dedupe_key(conversation, client_id)] = {
                        "conversation": conversation, "client_id": client_id, "id": message_id,
                        "hash": fingerprint, "status": "queued", "card": card,
                        "at": message["created_at"],
                    }
                    while len(self._dedupe) > MAX_DEDUPE:
                        self._dedupe.popitem(last=False)
                self._trim()
                captured = self._capture()
            # 先落盘再回“收到了”：之后 Runtime 重启，这句话也还在。写不下去就不算收下。
            try:
                self._flush(captured, strict=True)
            except OSError as error:
                with self._changed:
                    if self._messages.get(message_id, {}).get("status") == "queued":
                        del self._messages[message_id]
                        if client_id is not None:
                            key = dedupe_key(conversation, client_id)
                            if self._dedupe.get(key, {}).get("id") == message_id:
                                del self._dedupe[key]
                        path = self._audio.pop(message_id, None)
                        if path is not None:
                            try:
                                path.unlink()
                            except OSError:
                                pass
                    self._changed.notify_all()
                raise UnavailableError("消息记录写不下去，这句话没收下，稍后用同一个 client_id 再试：%s"
                                       % error)
            with self._changed:
                if message_id in self._messages:
                    self._owner_writer.by(cause=message["id"]).request_changed(message, revise=False)  # 序号、日志一起变
                    if not self._closed:
                        self._line_up(conversation, message_id)
                snapshot = self._snapshot(message)
                self._changed.notify_all()
            if hop == 0 and not message.get("relay_to"):
                # 主人插话：她要是还在说上一句，先停下来听这一句（说出的部分照样留着）。
                self._xiaoyou.interrupt(conversation)
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

    def _capture(self) -> Any:
        """调用时已经拿着锁：拍一份要写下去的快照（消息浅拷贝，写盘在锁外做）。"""
        self._version += 1
        messages = OrderedDict((key, dict(value, events=list(value.get("events", []))))
                               for key, value in self._messages.items())
        dedupe = OrderedDict((key, dict(value)) for key, value in self._dedupe.items())
        return self._version, messages, dedupe, self._xiaoyou.cards.seq

    def _flush(self, captured: Any, strict: bool = False) -> None:
        """不拿着消息的锁：写下一份快照。比它新的已经写过了就不写。

        strict 为 False（工作线程里）时写不下去只记一笔，不能让工作线程死掉；为 True（收消息
        时）把错误交给调用方，这句话就不算收下。"""
        version, messages, dedupe, seq = captured
        with self._write_lock:
            if version <= self._written:
                return
            try:
                self._ledger.save(messages, dedupe, seq)
            except OSError as error:
                if strict:
                    raise
                sys.stderr.write("消息记录写不下去：%s\n" % error)
                return
            self._written = version

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
        self._sweep_reports()
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

    def _sweep_reports(self) -> None:
        """太久没报的外部会话不再显示“在做”。"""
        reports = getattr(self, "_reports", None)
        if reports is not None:
            reports.sweep()

    def report(self, payload: Any) -> Dict[str, Any]:
        """别处的会话报一次进展（见 reports.py）。"""
        try:
            return self._reports.report(payload)
        except ReportError as error:
            raise RequestError(str(error))

    def conversations(self):
        from .conversation_directory import uid
        preferences = self.card_store.directory.snapshot()
        values = self.card_store.conversations.list()
        for item in values:
            key = uid('chat', item['id'])
            metadata = preferences.get(key, {})
            if metadata.get('origin') != (item.get('origin') or self.runtime_id):
                metadata = {}
            item.update(directory_id=key, metadata_revision=metadata.get('revision', 0))
            item.update({k: metadata[k] for k in ('title', 'pinned', 'archived', 'project') if k in metadata})
        return {"conversations": values}

    def directory(self, include_retained=True):
        seq = self.card_store.seq
        cache = getattr(self, '_directory_cache', {})
        cached = cache.get(include_retained)
        if cached and cached[0] == seq:
            return dict(cached[1], discovery=dict(getattr(getattr(self, 'discovery', None), 'status', {})))
        from .conversation_directory import project, home_scope
        config = self._xiaoyou._config
        homes = {kind: home_scope(path or Path.home() / default) for kind, path, default in (
            ('codex', config.desktop_codex_dir, '.codex'), ('claude', config.desktop_claude_dir, '.claude'))}
        cards = self.card_store.recent(None, 10 ** 6)
        if include_retained:
            cards += self.card_store.retained()
        for card in cards:
            if not card.get('monitoring_only') and not card.get('worker_session') and card.get('agent'):
                session = self._store.session('%s/%s' % (card.get('conversation', 'default'), card['id']), card['agent'])
                if session:
                    card.update(worker_session=session, worker_agent=card['agent'])
        rows = project(cards, self.card_store.conversations.list(),
                       self.card_store.directory.snapshot(), self.runtime_id,
                       getattr(config, 'name', ''), config.agents, self.card_store.epoch, homes,
                       self.card_store.directory.source_snapshot())
        owned = self._store.session_ids()
        for row in rows:
            if row['kind'] == 'native':
                row['native_open'] = self._native_open_capability(row, owned, cards)
                row['native_presentation'] = dict(presentation.policy()['native_surfaces'].get(row.get('agent'),
                    {'status': 'incomplete', 'reason': 'native-rendering-not-verified'}),
                    label='原生窗口显示尚未统一；共享记录视图不代表原生端已完成')
        generated = self.card_store.directory.generated_snapshot()
        for row in rows:
            if row['uid'] in generated and generated[row['uid']].get('origin') == row['authority']:
                value = dict(generated[row['uid']])
                value['stale'] = value.get('input_version') != str(row.get('source_revision') or row.get('updated_at') or 0) or value.get('base_revision') != row['metadata_revision']
                row['generated'] = value
        result = {"entries": rows, "policy": "shared-directory-v1", "seq": seq, "storage_errors": sorted(self.card_store.directory.errors),
                  'discovery': dict(getattr(getattr(self, 'discovery', None), 'status', {}))}
        cache[include_retained] = (seq, result)
        self._directory_cache = cache
        return result

    def _native_open_capability(self, row, owned, cards):
        import sys
        from .conversation_directory import home_scope
        reason = ''
        if row['authority'] != self.runtime_id:
            reason = '请在来源电脑的原生客户端打开；此处保留共享记录'
        elif sys.platform != 'darwin' or row.get('agent') != 'codex':
            reason = '来源尚未验证原生打开适配器；可查看公开记录'
        elif row.get('native_session_id') in owned or row.get('bound'):
            reason = 'Runtime 管理的会话尚未验证控制权交接；请通过任务补充要求'
        elif row.get('native_scope') != home_scope(Path.home()/'.codex'):
            reason = '此会话使用隔离配置目录，原生应用无法保证接入同一份记录'
        elif row.get('available') is False or row.get('state') not in ('turn_ended','done','failed','cancelled'):
            reason = '来源正在执行、离线或状态未知；暂不切换控制入口'
        elif any(c.get('borrowed_session') == row.get('native_session_id') and c.get('state') in ('working','talking','waiting') for c in cards):
            reason = 'Runtime 正在使用此原生会话，请等待本轮结束'
        return {'supported': not bool(reason), 'reason': reason, 'effect': 'navigate-existing-session'}

    def directory_open(self, key):
        import re
        import subprocess
        row = next((r for r in self.directory()['entries'] if r['uid'] == key), None)
        if row is None or not row.get('native_open',{}).get('supported'):
            raise RequestError((row or {}).get('native_open',{}).get('reason') or '此记录没有可用的原生打开入口')
        sid = row.get('native_session_id')
        if not isinstance(sid,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}',sid):
            raise RequestError('原生会话身份无效')
        try:
            result = subprocess.run(['open','codex://threads/'+sid],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3)
        except (OSError,subprocess.TimeoutExpired):
            raise RequestError('未能请求原生应用打开，请检查来源电脑安装状态')
        if result.returncode:
            raise RequestError('原生打开请求失败，来源记录仍保留')
        return {'status':'launch_requested','uid':key,'native_session_id':sid,'verified_visible':False}

    def directory_page(self, cursor=None, query='', limit=100):
        import base64
        import hashlib
        import json
        if not isinstance(query, str) or len(query) > 200 or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise RequestError('Invalid directory query')
        query = query.strip().casefold()
        scope = hashlib.sha256(json.dumps([self.runtime_id,self.card_store.epoch,'chat-task-native-v1',query]).encode()).hexdigest()
        last = ''
        if cursor:
            try:
                if len(cursor) > 1024:
                    raise ValueError()
                token = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
                if token['scope'] != scope or not isinstance(token['last'], str):
                    raise ValueError()
                last = token['last']
            except (ValueError, KeyError, TypeError):
                raise RequestError('目录来源、顺序或查询已变化，请刷新后继续')
        result = dict(self.directory())
        def order(key):
            # Immutable identity kind keeps pages stable while ensuring old native
            # sources cannot displace the chats/goals the owner manages now.
            rank = 0 if key.startswith('chat-') else 1 if key.startswith('task-') else 2
            return rank, key
        rows = sorted((r for r in result['entries'] if (not last or order(r['uid']) > order(last)) and
                       (not query or query in ' '.join(str(r.get(k) or '') for k in ('title','original_title','project','source_name','agent')).casefold())), key=lambda r: order(r['uid']))
        more = len(rows) > limit
        result['entries'], result['more'] = [{k:v for k,v in row.items() if k != 'legacy_message_ids'} for row in rows[:limit]], more
        result['cursor'] = base64.urlsafe_b64encode(json.dumps({'scope':scope,'last':rows[limit-1]['uid']}).encode()).decode().rstrip('=') if more else None
        return result

    def directory_edit(self, key, body):
        if not isinstance(body, dict) or set(body) - {'fields', 'revision'} or not isinstance(body.get('fields'), dict):
            raise RequestError('Invalid display metadata request')
        row = next((item for item in self.directory()['entries'] if item['uid'] == key), None)
        if row is None:
            raise RequestError('Directory entry not found')
        if row['authority'] != self.runtime_id:
            if self.mesh is None:
                raise RequestError('Source Runtime is unavailable; no local copy was changed')
            command = self.mesh.command(row['authority'], 'directory', {'uid': key, **body})
            return {'status': 'queued', 'command_id': command, 'authority': row['authority']}
        if row['kind'] == 'conversation' and row['conversation_id'] == 'default' and ('title' in body.get('fields', {}) or body.get('fields', {}).get('archived')):
            raise RequestError('The main chat keeps its fixed navigation entry')
        try:
            result = self._owner_writer.directory_edit(key, body.get('fields'), self.runtime_id, body.get('revision'),
                                                       [row['task_id']] if row.get('task_id') and not row.get('retained_only') else [])
        except ValueError as error:
            raise RequestError(str(error))
        return {'status': 'applied', 'metadata': result}

    def _directory_tasks(self, tasks):
        rows = {row.get('task_id'): row for row in self.directory(False)['entries'] if row.get('task_id')}
        for task in tasks:
            row = rows.get(task['id'])
            if row:
                task.update(directory_id=row['uid'], original_title=task['title'], title=row['title'],
                            title_source=row['title_source'], shared_pinned=row['pinned'],
                            shared_archived=row['archived'], display_project=row.get('project', ''),
                            metadata_revision=row['metadata_revision'], native_sources=row.get('native_sources', []))
        return tasks

    def directory_history(self, key, cursor=None):
        row = next((item for item in self.directory()['entries'] if item['uid'] == key), None)
        if row is None:
            raise RequestError('Directory source not found')
        if row['authority'] != self.runtime_id and row['kind'] != 'conversation':
            if self.mesh is None:
                raise RequestError('The source computer is unavailable; no new execution was started')
            command = self.mesh.command(row['authority'], 'directory_history', {'uid': key, 'cursor': cursor})
            return {'status': 'queued', 'command_id': command, 'authority': row['authority']}
        try:
            if row['kind'] == 'native':
                from .native_history import read
                native = read(self._xiaoyou._config, row, cursor)
                if native is not None:
                    return presentation.history(dict(native, uid=key, title=row['title']), self.runtime_id)
            from .message_history import page
            def matches(message):
                if row['kind'] == 'conversation':
                    return message.get('conversation') == row['conversation_id'] and message.get('scope') == 'main'
                if row['kind'] == 'task':
                    return message.get('task') == row['task_id'] and (message.get('task_epoch') == row['origin_epoch'] if message.get('task_epoch') else message.get('id') in row.get('legacy_message_ids', []))
                source = message.get('source') or {}
                return source.get('session_id') == row.get('native_session_id') and not message.get('mesh_origin') and (source.get('native_scope') == row.get('native_scope') if source.get('native_scope') else bool(row.get('task_id')) and message.get('task') == row['task_id'])
            return presentation.history(dict(page(self.card_store._log._path, matches, key + '@' + self.runtime_id, cursor), uid=key, title=row['title']), self.runtime_id)
        except (ValueError, OSError) as error:
            raise RequestError(str(error))

    def directory_metadata(self, key):
        row = next((r for r in self.directory()['entries'] if r['uid'] == key), None)
        if row is None:
            raise RequestError('Directory source not found')
        if row['authority'] != self.runtime_id:
            if self.mesh is None:
                raise RequestError('Source Runtime unavailable')
            command = self.mesh.command(row['authority'], 'directory_metadata', {'uid':key})
            return {'status':'queued','command_id':command,'authority':row['authority']}
        def read_metadata_source():
            if row['kind'] == 'native':
                from .native_history import recent
                return recent(self._xiaoyou._config,row)
            return self.directory_history(key)
        return self.metadata.directory_request(row, read_metadata_source,
            lambda: next((r for r in self.directory()['entries'] if r['uid'] == key), None))

    def directory_operation(self, command, authority):
        if self.mesh is None:
            raise RequestError('Source Runtime unavailable')
        result = self.mesh.outcome(command, authority, 'accepted')
        return dict(result or {}, status='applied' if result and result.get('ok') else 'failed' if result else 'queued')

    def conversation_create(self, body):
        if not isinstance(body, dict):
            raise RequestError("请求体应为对象")
        from .desktop_content import redact_credentials
        name = body.get("id")
        if name is not None:
            name = _name(name, "id")
            if not name.startswith("side-"):
                raise RequestError("旁聊编号应以 side- 开头")
        context = None
        if body.get("from_message"):
            original = self.card_store.message(_name(body["from_message"], "from_message"))
            if original is None or original.get("kind") == "activity":
                raise RequestError("来源消息已不在可读历史里")
            context = {key: original.get(key) for key in ("id", "conversation", "author", "source")}
            context["text"] = redact_credentials(original.get("text") or "")[:4000]
        return self._owner_writer.conversation_create(name, context)

    def conversation_rename(self, name, title):
        try:
            name = _name(name, 'conversation')
            from .conversation_directory import uid
            existing = self.card_store.conversations.get(name)
            if existing and existing.get('origin') and existing['origin'] != self.runtime_id:
                return self.directory_edit(uid('chat',name),{'fields':{'title':title}})
            # Keep legacy clients on the same shared owner alias. The catalog's
            # original manual rename also fences any late generated title.
            self._owner_writer.conversation_rename(name,title)
            from .conversation_directory import uid
            result = self.directory_edit(uid('chat',name),{'fields':{'title':title}})
            if result.get('status') != 'applied':
                return result
            return next(c for c in self.conversations()['conversations'] if c['id']==name)
        except ValueError as error:
            raise RequestError(str(error))

    def task_audit(self, task, before=None):
        task = _name(task, "task")
        try:
            return self.card_store.audit.page(task, int(before) if before else None)
        except (ValueError, TypeError):
            raise RequestError("before 应为事件序号")

    def info(self, nonce: Any = None, key_hash: Optional[str] = None) -> Dict[str, Any]:
        """这台 Runtime 是谁、记录是哪一份、会说哪一版同步协议。

        nonce：客户端带来的随机数；key_hash 是它用的凭据的哈希（server.py 按请求算）。带了就
        回 host_proof，客户端在任何线路上都能核对连到的是持有它凭据的 Runtime（设计第 6 节）。"""
        capabilities = list(CAPABILITIES) + ["quick_review", "devices", "host_proof"] \
            + (["mesh"] if self.mesh is not None else [])
        result = {"subject_id": self.subject_id, "runtime_id": self.runtime_id, "epoch": self._xiaoyou.cards.epoch,
                  "protocol": PROTOCOL, "capabilities": capabilities,
                  "presentation_policy": presentation.policy(),
                  "host": {"id": self.runtime_id, "fingerprint": fingerprint(self.runtime_id)}}
        if self.mesh is not None:
            result["routes"] = self.mesh.routes(list(self.mesh._config.mesh_advertise))
        if isinstance(nonce, str) and 16 <= len(nonce) <= 128 and key_hash:
            result["host"]["proof"] = host_proof(key_hash, self.runtime_id, nonce)
        return result

    # ---- 设备和权限（devices.py，设计第 6 节） ----

    def authority(self) -> str:
        """权限权威。不在网络里、或者网络里还没人配置 mesh.home 时就是这台自己。"""
        found = self.mesh.authority() if self.mesh is not None else None
        return found or self.runtime_id

    def permission_problem(self) -> Optional[str]:
        """敏感操作前：拿不到最新的设备名单（权威不在线）就不做。"""
        authority = self.authority()
        if authority == self.runtime_id or self.mesh.online(authority):
            return None
        name = next((peer["name"] for peer in self.mesh.status()["peers"] if peer["runtime_id"] == authority), "") \
            or authority[:6]
        return "「%s」不在线，拿不到最新的设备名单，现在不能做这件事" % name

    def device_add(self, name: Any, kind: Any = "phone") -> Dict[str, Any]:
        """配对一台设备：凭据在这里生成、只给这一次；名单由权威记下。"""
        name = _name(name, "name") if name is not None else "设备"
        if kind not in DEVICE_KINDS:
            raise RequestError("kind 应该是 %s 之一" % "、".join(DEVICE_KINDS))
        problem = self.permission_problem()
        if problem:
            return {"status": "refused", "reason": problem}
        device_id, credential, secret_hash = new_credential()
        args = {"id": device_id, "name": name, "kind": kind, "hash": secret_hash}
        result = self.permission_command("device_add", args) if self.authority() == self.runtime_id \
            else self._ask_authority("device_add", args)
        return dict(result, credential=credential) if result.get("status") != "refused" else result

    def device_revoke(self, device_id: Any) -> Dict[str, Any]:
        problem = self.permission_problem()
        if problem:
            return {"status": "refused", "reason": problem}
        args = {"id": _name(device_id, "device")}
        if self.authority() != self.runtime_id:
            return self._ask_authority("device_revoke", args)
        return self.permission_command("device_revoke", args)

    def member_remove(self, runtime: Any) -> Dict[str, Any]:
        problem = self.permission_problem()
        if problem:
            return {"status": "refused", "reason": problem}
        if self.mesh is None:
            raise RequestError("这台不在小幽网络里")
        target = self.mesh.peer_id(runtime)
        if target is None or target == self.runtime_id:
            raise RequestError("不认识电脑 %s" % runtime)
        args = {"runtime": target}
        if self.authority() != self.runtime_id:
            return self._ask_authority("member_remove", args)
        return self.permission_command("member_remove", args)

    def _ask_authority(self, kind: str, args: Dict[str, Any]) -> Dict[str, Any]:
        authority = self.authority()
        problem = self.mesh._reachable(authority)
        if problem:
            return {"status": "refused", "reason": problem}
        self.mesh.command(authority, kind, args)
        return {"status": "queued", "authority": authority}

    def permission_command(self, kind: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """在权威上执行（本机调用，或者别的电脑发来的指令）。"""
        if self.authority() != self.runtime_id:
            raise RequestError("这台不是权限权威")
        if kind == "device_add":
            try:
                device = self.devices.add(str(args.get("id")), str(args.get("name") or ""),
                                          str(args.get("kind") or "device"), str(args.get("hash")))
            except ValueError as error:
                raise RequestError(str(error))
            self._publish_device(device)
            return {"status": "applied", "device": self.devices.view(device)}
        if kind == "device_revoke":
            device = self.devices.revoke(str(args.get("id")))
            if device is None:
                raise RequestError("没有这台设备")
            self._publish_device(device)
            return {"status": "applied", "device": self.devices.view(device)}
        if kind == "member_remove":
            runtime = str(args.get("runtime"))
            if runtime == self.runtime_id:
                raise RequestError("不能移出权威自己")
            try:
                item = self.devices.remove_member(runtime)
            except ValueError as error:
                raise RequestError(str(error))
            if self.mesh is not None:
                self.mesh.publish_permission("member", runtime, {"at": item["at"], "name": item["name"]})
            return {"status": "applied", "removed": runtime}
        raise RequestError("不认识的权限操作 %s" % kind)

    def _publish_device(self, device: Dict[str, Any]) -> None:
        if self.mesh is not None:
            self.mesh.publish_permission("device", device["id"], self.devices.record(device["id"]))

    def credential_hash(self, token: str, legacy: bool) -> Optional[str]:
        """这次请求用的凭据的哈希（host_proof 的钥匙）：设备凭据，或者旧的共享令牌。"""
        return hash_secret(token) if legacy else self.devices.key_hash(token)

    def sync(self, conversation: Any, after: Any = 0, epoch: Any = None,
             wait: float = 0.0, usage: Any = None, states: Any = (), all_conversations: bool = False) -> Dict[str, Any]:
        """统一同步接口（见 docs/development/unified-message-model）。

        返回序号比 after 大的消息（messages）和事（tasks，卡去掉 entries、换成 members），
        以及现在等回答的授权。after 为 0 是全量快照。客户端带来的 epoch 和这里不一样、
        游标比这里现在的序号还大（记录换过）、或者比日志的 floor 还旧时，reset 为 true，
        并且从 0 给全量快照：客户端应该丢掉这台 Runtime 的游标（缓存的消息按编号合并即可）。
        没有变化时最多等 wait 秒。

        和 /v1/feed 一样每次带上现在的用量；usage 是调用方手里那份用量的 rev，带了它，
        用量变了也会提前返回。

        states 是客户端声明认得的新状态。现在只有 unavailable：观察的会话很久没更新时给它；
        没声明的旧 App 照旧拿到卡上的状态（unknown 或最后知道的状态）和 stale，免得不认识的
        状态被当成做完了。"""
        conversation = _name(conversation, "conversation")
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise RequestError("after 应该是不小于 0 的整数")
        if usage is not None and (isinstance(usage, bool) or not isinstance(usage, int)):
            raise RequestError("usage 应该是整数")
        meter = self._xiaoyou.usage
        meter.poke()
        self._sweep_reports()
        if epoch is not None and (not isinstance(epoch, str) or len(epoch) > MAX_NAME_CHARS):
            raise RequestError("epoch 应该是字符串")
        cards_store = self._xiaoyou.cards
        reset = False
        if after > 0 and ((epoch and epoch != cards_store.epoch) or after > cards_store.seq
                          or after < cards_store.log_floor):
            after, reset = 0, True
        deadline = time.monotonic() + max(0.0, wait)
        while True:
            seq, tasks, messages, floor = cards_store.sync_since(
                None if all_conversations else conversation, after, unavailable="unavailable" in states)
            if after > 0 and (after < floor or len(tasks) + len(messages) > SYNC_DELTA_WINDOW):
                # 游标之后的增量已经不全（日志挤掉了），或者落后太多：改给快照。
                after, reset = 0, True
                continue
            # 授权和消息、卡共用一个计数器：只是授权变了（比如电脑上答掉了）也要叫醒。
            approvals = self._xiaoyou.approvals.since(None if all_conversations else conversation, after)
            remaining = deadline - time.monotonic()
            if (tasks or messages or approvals or reset or cards_store.catalog_seq > after or remaining <= 0
                    or (usage is not None and meter.rev != usage)):
                break
            cards_store.wait_beyond(seq, remaining if usage is None
                                    else min(remaining, FEED_USAGE_SECONDS))
        tasks = [presentation.task(item) for item in tasks]
        messages = [presentation.message(item, self.runtime_id) for item in messages]
        if after == 0:
            # 快照固定在 seq：同一把锁里取的，之后的变化客户端拿 seq 接着要增量。
            items, truncated = _snapshot_items(tasks, messages)
            more = False
        else:
            items, truncated = sorted([("task", item) for item in tasks] + [("message", item) for item in messages],
                                      key=lambda pair: pair[1]["seq"]), False
            items, more = _page_items(items)
            if more:
                seq = items[-1][1]["seq"]
        return {
            "protocol": PROTOCOL, "subject_id": self.subject_id, "runtime_id": self.runtime_id,
            "epoch": cards_store.epoch,
            "seq": seq, "reset": reset, "more": more,
            # 快照（after 为 0 或者被改成快照）：固定在 seq；truncated 说明更早的话没放进来。
            **({"snapshot": {"seq": seq, "truncated": truncated}} if after == 0 else {}),
            "presentation_policy": presentation.policy(),
            "conversations": self.conversations()['conversations'],
            "messages": [item for kind, item in items if kind == "message"],
            "tasks": self._directory_tasks([item for kind, item in items if kind == "task"]),
            # 序号比 after 大的授权变化，含已经有结局的（status：pending / allowed / denied / withdrawn）。
            "approvals": approvals,
            # 主动删除的消息（现在还没有会删消息的操作）；裁剪不算删除，不出现在这里。
            "removed": [],
            "usage": meter.snapshot(),
        }

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
        return [presentation.card(card, self.runtime_id, self.card_store.message)
                for card in self._xiaoyou.cards.recent(_name(conversation, "conversation"), 50,
                                                     include_desktop=True)]

    def cancel(self, card_id: str, actor: str = "client", reason: str = "",
               announce: bool = True) -> Optional[Dict[str, Any]]:
        """取消一件事。没有这张卡返回 None；它已经不在做了就原样返回。
        actor / reason 记在卡上：谁停的、为什么（见 Xiaoyou.cancel）。"""
        observed = self._xiaoyou.cards.get(card_id)
        if observed is None:
            return None
        if observed.get("monitoring_only"):
            raise RequestError("这是电脑端同步的会话，请在电脑端停止任务")
        self._xiaoyou.cancel(card_id, actor=actor, reason=reason, announce=announce)
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

    def relayed(self) -> List[Dict[str, Any]]:
        """转交给别的 Runtime、还在等那台结果的话（mesh.py 启动时接上它们）。"""
        with self._changed:
            # 按负责人转、还排着没交出去的不算（它们在等联系上，不在等结果）。
            return [self._snapshot(message) for message in self._messages.values()
                    if message.get("relay_to") and message["status"] not in FINISHED
                    and not (message.get("routed") and message["status"] == "queued")]

    def relay_done(self, message_id: str, ok: bool, text: str, card: Optional[str] = None,
                   unknown: bool = False) -> None:
        """转交给别的 Runtime 的话有了结果（mesh.py 调用）：ok 时 text 是那台的回答，否则是原因。
        unknown：交过去了但那台没了音讯，做没做不知道。"""
        with self._changed:
            message = self._messages.get(message_id)
            if message is None or message["status"] in FINISHED:
                return
        fields: Dict[str, Any] = dict(stage=None, helper=None, partial="", finished_at=time.time())
        if card:
            fields["card"] = card
        if ok:
            self._update(message_id, status="done", reply=text, brief=text[:120], mood="idle", **fields)
        else:
            self._update(message_id, status="failed", error=text or "那台没说明原因", mood="oops",
                         **(dict(fields, operation_state="unknown") if unknown else fields))

    def _snapshot(self, message: Dict[str, Any]) -> Dict[str, Any]:
        snapshot = dict(message)
        snapshot["presentation"] = presentation.response(message, self.runtime_id)
        # Current replies must not wait behind a paginated history replay. These
        # are the same log records/revisions as /sync, never request-local revs.
        snapshot["message_snapshot"] = {
            "runtime_id": self.runtime_id, "subject_id": self.subject_id,
            "epoch": self.card_store.epoch,
            "messages": [presentation.message(record, self.runtime_id)
                         for suffix in (":you", ":reply")
                         for record in [self.card_store.message(message["id"] + suffix)]
                         if record is not None],
        }
        snapshot["event_stream"] = True
        snapshot["events"] = [dict(event) for event in message["events"]]
        snapshot["attachments"] = [dict(item) for item in message.get("attachments", [])]
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
            turn_id = _name(turn.get("id"), "turns[].id")
            # 小幽网络里别的 Runtime 的话在这里带着产地前缀（mesh.local_id）；去掉前缀就是那句话
            # 本来的编号，这台的对话记录按它认出已经知道的，不会把同一轮记两次。
            prefixed = MESH_PREFIX.match(turn_id)
            cleaned.append({
                "id": prefixed.group(1) if prefixed else turn_id,
                "text": text[:MAX_SHARED_TEXT_CHARS],
                "reply": reply[:MAX_SHARED_REPLY_CHARS],
                "at": at,
            })
        return self._xiaoyou.share(conversation, cleaned)

    def close(self) -> None:
        self.discovery.close()
        self.metadata.pool.shutdown(wait=False, cancel_futures=True)
        self.review.close()
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

    def _update(self, message_id: str, strict: bool = False, **fields: Any) -> None:
        """改一条消息并写下去。strict 时写不下去就把错误交给调用方。"""
        # 落盘的改动和收消息排成一队；但写盘不占着 _changed，轮询、取消这些读的人不会卡住。
        with self._write_lock:
            with self._changed:
                message = self._messages.get(message_id)
                changes = operations.transition(message, fields) if message is not None else None
                if message is None or changes is None:
                    if message is not None:
                        sys.stderr.write("消息 %s 已经结束，忽略改成 %s\n" % (message_id, fields.get("status")))
                    return
                captured = self._capture()
                candidate = captured[1][message_id]
                candidate.update(changes)
                if candidate.get('client_id') is not None:
                    entry = captured[2].get(dedupe_key(candidate['conversation'], candidate['client_id']))
                    if entry is not None and entry['id'] == message_id:
                        entry.update(status=candidate['status'], card=candidate.get('card'))
            try:
                self._flush(captured, strict=True)
            except OSError as error:
                if strict:
                    raise
                sys.stderr.write("消息记录写不下去，保留上次已保存的状态：%s\n" % error)
                return
            with self._changed:
                message.update(changes)
                self._reply_writer.by(cause=message["id"]).request_changed(message)  # 修订号、序号、日志一起变
                self._remember(message)
                captured = self._capture()
                self._changed.notify_all()
            # 再把投影的新修订号写下去；操作状态和结果在上面公开之前已经落盘了。
            self._flush(captured, strict=strict)

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
                    self._reply_writer.by(cause=message["id"]).request_changed(message)  # 修订号、序号、日志一起变
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
            self._reply_writer.by(cause=message["id"]).request_changed(message)  # 修订号、序号、日志一起变
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

    # ---- 对话负责人（ownership.py，设计第 6 节） ----

    def _route(self, message: Dict[str, Any]) -> bool:
        """调用时拿着锁、刚要领取：这台不是负责人就把这句话改成转给负责人。返回能不能接着处理。

        只管主人直接说的话（hop 为 0、不是在某件事里面补充）：替别的电脑执行的派活、进行中
        的事留在执行它的那台。检查和领取在同一把锁里，移交（handoff）也要这把锁，中间没有窗口。"""
        if self.mesh is None or message.get("hop", 0) or message.get("pin") \
                or (message.get("relay_to") and not message.get("routed")):
            return True
        view = self.mesh.owner_of(message["conversation"])
        if view.get('unresolved'):
            self._parked[message['id']] = message['conversation']
            return False
        owner = view["owner"]
        if owner is None or owner == self.runtime_id:
            message.pop("relay_to", None)
            message.pop("routed", None)
            return True
        sent = message.get("routed_generation")
        if isinstance(sent, int) and view["generation"] <= sent:
            # 转来的一方认为这台是负责人，这台却还没收到那代绑定：等它同步过来，不转回去。
            self._parked[message["id"]] = message["conversation"]
            return False
        message.update(relay_to=owner, routed=True, owner_generation=view["generation"],
                       relay_name=view["name"])
        return True

    def _routed(self, message_id: str) -> bool:
        with self._changed:
            return bool(self._messages.get(message_id, {}).get("routed"))

    def retry_parked(self) -> None:
        """在等负责人的话：再排一次（还是连不上就又停下）。"""
        with self._changed:
            if self._closed or not self._parked:
                return
            parked, self._parked = self._parked, {}
            for message_id, conversation in parked.items():
                if self._messages.get(message_id, {}).get("operation_state") == "pending":
                    self._line_up(conversation, message_id)

    def owner(self, conversation: Any) -> Dict[str, Any]:
        conversation = _name(conversation, "conversation")
        if self.mesh is None:
            return {"conversation": conversation, "owner": self.runtime_id, "generation": 0,
                    "name": "", "online": True, "conflict": False}
        return dict(self.mesh.owner_of(conversation), conversation=conversation)

    def handoff(self, conversation: Any, to: Any) -> Dict[str, Any]:
        """把一段对话交给另一台（两台都在线、这段对话空闲时）。返回 {"status", ...}。"""
        conversation = _name(conversation, "conversation")
        if self.mesh is None:
            raise RequestError("这台不在小幽网络里，没有别的电脑可以交")
        problem = self.permission_problem()
        if problem:
            return {"status": "refused", "reason": problem}
        target = self.mesh.peer_id(to)
        if target is None:
            raise RequestError("不认识电脑 %s" % to)
        with self._changed:
            view = self.mesh.owner_of(conversation)
            owner = view["owner"]
            if owner is None:
                raise RequestError("这段对话还没有负责的电脑")
            if owner == target:
                return {"status": "applied", "owner": target, "generation": view["generation"]}
            if owner != self.runtime_id:
                problem = None
            else:
                if not self.mesh.online(target):
                    return {"status": "refused", "reason": "「%s」现在不在线" % to}
                busy = self._busy_reason(conversation)
                if busy:
                    return {"status": "refused", "reason": busy}
                written = self.mesh.hand_off(conversation, target, view["generation"])
                return {"status": "applied", "owner": target, "generation": written["generation"]}
        # 不是负责人：请负责人来交（锁外发指令）。
        problem = self.mesh.request_handoff(owner, conversation, target)
        if problem:
            return {"status": "refused", "reason": problem}
        return {"status": "queued", "owner": owner}

    def _busy_reason(self, conversation: str) -> Optional[str]:
        """调用时拿着锁：这段对话现在能不能交出去。"""
        if self._lanes.get(conversation) or conversation in self._busy:
            return "这段对话还有话在处理，等处理完再交"
        if any(self._messages.get(item, {}).get("conversation") == conversation for item in self._claimed) \
                or conversation in self._parked.values():
            return "这段对话还有话在处理，等处理完再交"
        active = [card for card in self._xiaoyou.cards.recent(conversation, 10 ** 6)
                  if card["state"] in ACTIVE and not card.get("mesh_origin") and not card.get("monitoring_only")]
        if active:
            return "这段对话里还有事在做（%s），做完再交" % active[0]["title"]
        return None

    def _handle(self, message_id: str) -> None:
        with self._changed:
            message = self._messages.get(message_id)
            # 只领还没开始的：同一句话排了两次、或者已经结束了，都不再处理。
            if (message is None or message["operation_state"] != "pending"
                    or message_id in self._claimed):
                return
            self._parked.pop(message_id, None)
            routed = self._route(message)
            if routed:
                self._claimed.add(message_id)
        if not routed:
            # 写“在等负责人”的说明时不占着 _changed；已经开始的并发重试不会被改回排队。
            self._update(message_id, status='queued', stage='排队中：等待对话负责人同步', mood='busy')
            return
        try:
            self._process(message_id)
        finally:
            with self._changed:
                self._claimed.discard(message_id)

    def _process(self, message_id: str) -> None:
        with self._changed:
            message = self._messages.get(message_id)
            if message is None:
                return
            text, conversation = message["text"], message["conversation"]
            asked, hop, card = message["asked"], message["hop"], message["card"]
            pin = bool(message.get("pin"))
            if message.get("reply_scope") == "main" and pin and card:
                bound = self._xiaoyou.cards.get(card)
                if bound and not bound.get("monitoring_only") and bound.get("agent"):
                    asked = bound["agent"]
            effort, model = message.get("effort"), message.get("model")
            relay_to, delegated = message.get("relay_to"), message.get("delegated_from")
            audio = self._audio.pop(message_id, None)
            message["processing_started_at"] = time.time()
        if audio is not None:
            self._update(message_id, status="transcribing")
            failure = None
            try:
                text = self._stt.transcribe(audio)
                if not text:
                    raise SttError("没听清，再说一次吧")
            except SttError as error:
                failure = str(error)
            except Exception as error:
                failure = "语音识别出错：%s: %s" % (type(error).__name__, error)
            finally:
                # 先删录音再记结果：调用方看到结果时，录音已经不在了。
                try:
                    audio.unlink()
                except OSError:
                    pass
            if failure is not None:
                self._update(message_id, status="failed", error=failure, mood="oops",
                             finished_at=time.time())
                return
            text = text[:MAX_TEXT_CHARS]
        if relay_to and self._routed(message_id):
            problem = self.mesh._reachable(relay_to) if self.mesh is not None else "这台不在小幽网络里"
            if problem:
                # 负责的电脑连不上：不替它答，保留原编号排队，它回来再交过去（设计第 6 节）。
                with self._changed:
                    name = (self.mesh.owner_of(conversation)["name"] if self.mesh is not None else "") or "负责的电脑"
                    self._parked[message_id] = conversation
                self._update(message_id, text=text, stage="排队中：负责的电脑「%s」连不上" % name, mood="busy")
                return
        try:
            self.metadata.first_message(conversation, text)
        except (OSError, ValueError) as error:
            sys.stderr.write("会话标题更新失败：%s\n" % type(error).__name__)
        try:
            # 交给模型之前，“开始处理”必须已经写下去：否则断电重启后会把它当成没开始，再做一遍。
            self._update(message_id, strict=True, status="running", text=text)
        except OSError as error:
            self._update(message_id, status="failed", mood="oops", finished_at=time.time(),
                         error="消息记录写不下去，这句话没处理：%s" % error)
            return
        if relay_to:
            # 在别的 Runtime 的事里说的：交给那台。结果由 mesh.py 收到那台的记录后填回来。
            mesh = self.mesh
            problem = mesh.relay(self.get(message_id)) if mesh is not None else "这台不在小幽网络里"
            if problem:
                self._update(message_id, status="failed", error=problem, mood="oops", stage=None,
                             finished_at=time.time())
            else:
                with self._changed:
                    name = self._messages.get(message_id, {}).get("relay_name") or "别的电脑"
                self._update(message_id, stage="交给 %s 了" % name, mood="busy")
            return
        try:
            with self._reply_writer.opening(**({"delegated_from": delegated} if delegated else {}), reply_scope=message.get("reply_scope")):
                if message.get("attachments"):
                    turn = self._xiaoyou.hear_images(text, conversation, message_id,
                        message["attachments"], asked=asked, hop=hop)
                else:
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
        card = self._reply_writer.by(cause=message_id).task_open(conversation, text, "failed")
        self._reply_writer.by(cause=message_id).exchange(card["id"], said=text, say=error, brief=error, mood="oops",
                                  turn_id=message_id, scope="task" if pin else "main")
        self._update(
            message_id, status="failed", error=error, mood="oops", stage=None, helper=None,
            card=card["id"], partial="", finished_at=time.time(),
        )
