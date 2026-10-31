"""事（card）：一个需求从提出到结束的全部内容。

主人提的每个需求是一件事。回答、进展、后来的补充都记在它自己那张卡上，客户端按卡
显示，不按到达的先后混排。卡有编号（c1、c2……）、标题、状态、谁在做、主人说的话、
小鱼说的话、最近几行进展；交给帮手做的还有用的是哪一档努力程度（effort）、实际用的
模型（model）、到现在花了多少（cost）。

每次有卡变化，全局的序号加一并记在那张卡上：客户端记住看到的最大序号，带着它来
等（/v1/feed），就只会拿到之后变过的卡。消息（Service 里还在处理的那些话）变化时也从
这里取号（bump），所以卡和消息排在同一条序号上，客户端一个游标就能接着拉两样东西。

序号和这份记录的编号（epoch）都落盘：Runtime 重启后序号接着往上走；记录文件丢了、坏了
或者换了一份时 epoch 会变，客户端看到 epoch 变了就从头同步。

卡的每次变化先按差量追加进 state/cards.tasks.jsonl，state/cards.json 是定期快照
（world.py），只留最近的若干张。Runtime 重启时还在做的事没法接着做，
启动时把它们标成没做完。
"""

import json
import sys
import threading
import time
import uuid
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from .task_audit import TaskAudit
from .conversations import ConversationCatalog
from ..events.message_log import MessageLog
from . import world
from ..events import operations
from .world import Journal
from .store import _write
from .artifacts import clean_attachments
from .task_titles import MAX_REQUEST_TITLE_CHARS, canonical_title, select_title

# talking 留给“小鱼正在处理这句话”，现在 Runtime 自己不会把卡放在这个状态。
STATES = ("talking", "working", "waiting", "done", "failed", "cancelled", "unknown")
ACTIVE = ("talking", "working", "waiting")
MAX_CARDS = 50
# 做完的任务单独留这么多（最近的在前），不被别的卡挤掉：第三屏的历史靠它。
MAX_HISTORY = 15
MAX_ENTRIES = 40
MAX_ENTRY_CHARS = 8000
MAX_TITLE_CHARS = MAX_REQUEST_TITLE_CHARS
MAX_PROGRESS_LINES = 5
MAX_PROGRESS_CHARS = 200
# 序号一次预留这么多写进文件：消息的序号变得很快（生成中的回复每变一次取一个号），
# 不能每次都写盘；但重启后不能从比客户端手里还小的号重新数，所以先把一段号“占下”，
# 用到头了再占下一段。重启后从占下的那段的末尾往后数：会跳一段，但不会往回走。
SEQ_BLOCK = 1000
RESTART_NOTE = "Runtime 重启了，这件事没做完"
# 一件事花了多少：token（新读的、写的、从缓存读的），和订阅额度两个窗口各少了几个百分点。
COST_KEYS = ("in", "out", "cached", "d5", "d7")

Listener = Callable[[Dict[str, Any]], None]


def title_from(text: str) -> str:
    """没有人起标题时，用这句话的开头当标题。"""
    return select_title(request=text).title


class Cards:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._changed = threading.Condition()
        self._cards: Dict[str, Dict[str, Any]] = {}
        self._next = 1
        self._seq = 0
        self._reserved = 0  # 文件里记着的序号上限：重启后从它往后数
        self._epoch = ""
        self._listeners: List[Listener] = []
        # 这个线程上正在写的东西是谁、从哪来、因为什么（Intake 设，见 intake.py）。
        self._provenance = threading.local()
        self.audit = TaskAudit(self._path.with_name("audit"))
        self.catalog_seq = 0
        self._retained = {}
        self.conversations = ConversationCatalog(self._path.with_name("conversations.json"))
        from .conversation_directory import DirectoryMetadata
        self.directory = DirectoryMetadata(self._path.with_name("conversation-directory.json"))
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}  # 丢了只是看不到旧卡，不值得为它拒绝启动
        raw = raw if isinstance(raw, dict) else {}
        # cards.json 是定期快照：再重放它之后的任务日志，才是崩溃（或停掉）前的样子（world.py）。
        records = world.read(world.journal_of(self._path))
        replayed, applied, highest = world.replay(raw, records)
        for card in replayed.values():
            self._cards[card["id"]] = self._clean(card)
        if isinstance(raw.get("next"), int) and not isinstance(raw.get("next"), bool):
            self._next = max(1, raw["next"])
        self._next = max(self._next, highest + 1)
        saved_seq = raw.get("seq")
        self._seq = max([card["seq"] for card in self._cards.values()]
                        + [record["seq"] for record in records
                           if isinstance(record.get("seq"), int) and not isinstance(record.get("seq"), bool)]
                        + [saved_seq if isinstance(saved_seq, int)
                           and not isinstance(saved_seq, bool) else 0, 0])
        if isinstance(raw.get("epoch"), str):
            self._epoch = raw["epoch"]
        self._reserved = self._seq
        # 差量都相对 replayed 算；之后写快照前都先 sync，快照和日志编号总对得上。
        self._journal = Journal(world.journal_of(self._path), replayed, applied,
                                    self._cards, self._seq)
        fresh_epoch = not self._epoch
        if fresh_epoch:
            # 旧版本写的文件没有 epoch：第一次补上。客户端原来手里的序号仍然有效（序号没变），
            # 只是从这以后它能认出“换了一份记录”。
            self._epoch = uuid.uuid4().hex
        for card in self._cards.values():
            card['record_epoch'] = card.get('record_epoch') or self._epoch
        retained_folder = self._path.with_suffix('.retained')
        for path in retained_folder.glob('*.json'):
            try:
                item = json.loads(path.read_text('utf-8'))
                if isinstance(item, dict) and isinstance(item.get('id'), str) and isinstance(item.get('record_epoch'), str):
                    self._retained[path.stem] = item
            except (OSError, ValueError):
                continue
        # 消息日志是每句话的权威记录（见 message_log.py）；卡上的 entries 是副本。
        self._log = MessageLog(self._path.with_name(self._path.stem + ".log.jsonl"), self._issue)
        self._log.hidden_conversation = lambda name: bool((self.conversations.get(name) or {}).get('deleted'))
        # cards.json 丢了、日志还在时：序号要从日志里见过的最大号往后数，不能重复发号。
        if self._log.max_seq() > self._seq:
            self._seq = self._log.max_seq()
            self._reserved = self._seq
        # 别的 Runtime 的事（副本）不归这台管：它的状态等那台再报，不在这里标成没做完。
        interrupted = [card for card in self._cards.values()
                       if card["state"] in ACTIVE and not card.get("mesh_origin")]
        for card in interrupted:
            self._seq += 1
            if card.get("monitoring_only"):
                card.update(state="unknown", desktop_status="unknown", stale=True,
                            brief="电脑会话状态尚未更新", approval=None, queued=False,
                            updated_at=time.time(), finished_at=None, seq=self._seq)
                continue
            dispatch = card.get("dispatch")
            if operations.valid_dispatch(dispatch) and dispatch["run_id"] == card.get("run_id"):
                began = any(row.get("run_id") == dispatch["run_id"]
                            for row in card.get("execution_attempts", []))
                if dispatch["state"] == "pending" and not began:
                    card.update(state="working", queued=True, started_at=None, approval=None,
                                brief="排队中：等待恢复执行", finished_at=None, seq=self._seq)
                    continue
                dispatch["state"] = "unknown"
                card.update(state="unknown", queued=False, approval=None, finished_at=None,
                            brief="上次执行结果尚未确认，不会自动重做", seq=self._seq)
                continue
            if dispatch is not None:
                card.update(state="unknown", queued=False, approval=None, finished_at=None,
                            brief="执行记录无法核对，不会自动重做", seq=self._seq)
                continue
            if card.get("relay_state") == "relay_queued":
                card["relay_state"] = "interrupted"
            card["entries"].append({"role": "xiaoyou", "text": RESTART_NOTE, "at": time.time(),
                                    "kind": "note"})
            card.update(state="failed", brief=RESTART_NOTE, mood="oops", approval=None,
                        queued=False, updated_at=time.time(), finished_at=time.time(),
                        seq=self._seq)
        self._adopt_log()
        # 启动时的改动（重启标记、补进日志、清理）由 _save 先 sync 进日志，再写快照、压缩日志。
        self._save()

    def _adopt_log(self) -> bool:
        """启动时：卡上的副本以日志为准；日志里还没有的（旧版本的卡）补进日志。"""
        changed = False
        for card in self._cards.values():
            if card.get("mesh_origin"):
                continue  # 副本的消息由 mesh.py 原样写进日志，不从卡上的条目反推
            for entry in card["entries"]:
                record = self._log.get(entry["id"]) if entry.get("id") else None
                if record is not None and (record["text"] != entry["text"]
                                           or record["attachments"] != entry["attachments"]):
                    entry.update(text=record["text"],
                                 attachments=[dict(item) for item in record["attachments"]])
                    changed = True
            before = [entry.get("id") for entry in card["entries"]]
            card["record_epoch"] = card.get("record_epoch") or self._epoch
            self._log.observe(card)
            changed = changed or before != [entry["id"] for entry in card["entries"]]
        return changed

    def _issue(self) -> int:
        """调用时已经拿着锁：发一个新序号；用到预留的那段号的末尾时先占下下一段。"""
        self._seq += 1
        if self._seq >= self._reserved:
            try:
                self._save()
            except OSError as error:
                # 照样往下走；下一次取号再试着占下一段。磁盘一直坏着时，断电重启后
                # 序号可能比客户端手里的小：那种情况下客户端靠“返回的序号变小”从头同步。
                sys.stderr.write("卡片记录写不下去：%s\n" % error)
        return self._seq

    @staticmethod
    def _clean(card: Dict[str, Any]) -> Dict[str, Any]:
        def text(key: str, default: str = "") -> str:
            return card[key] if isinstance(card.get(key), str) else default

        def number(key: str) -> Any:
            value = card.get(key)
            return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

        entries = [
            {"role": entry["role"], "text": entry["text"],
             "at": entry.get("at") if isinstance(entry.get("at"), (int, float)) else 0,
             **{key: entry.get(key, "") if isinstance(entry.get(key, ""), str) else ""
                for key in ("id", "scope", "kind", "author")},
             "attachments": clean_attachments(entry.get("attachments")),
             **Cards._provenance_field(entry, "provenance")}
            for entry in card.get("entries", [])
            if isinstance(entry, dict) and entry.get("role") in ("you", "xiaoyou")
            and isinstance(entry.get("text"), str)
        ] if isinstance(card.get("entries"), list) else []
        state = text("state", "done")
        title = canonical_title(text("title"), text("title_source", "task"))
        return {
            "id": card["id"],
            "conversation": text("conversation", "default"),
            "title": title.title,
            "title_source": title.source,
            "title_truncated": title.truncated or card.get("title_truncated") is True,
            "state": state if state in STATES else "done",
            "agent": card.get("agent") if isinstance(card.get("agent"), str) else None,
            "created_at": number("created_at"),
            "updated_at": number("updated_at"),
            "finished_at": card["finished_at"]
            if isinstance(card.get("finished_at"), (int, float))
            and not isinstance(card.get("finished_at"), bool) else None,
            "entries": entries,
            **Cards._provenance_field(card, "changed_by"),
            **{key: card[key] for key in ("cancelled_by", "cancel_reason") if isinstance(card.get(key), str)},
            "attachments": clean_attachments(card.get("attachments")),
            "brief": text("brief"),
            "mood": text("mood", "idle"),
            "progress": [line for line in card.get("progress", []) if isinstance(line, str)]
            if isinstance(card.get("progress"), list) else [],
            "started_at": card.get("started_at")
            if isinstance(card.get("started_at"), (int, float)) else None,
            "edits": int(number("edits")),
            "approval": None,
            "queued": False,
            "effort": card.get("effort") if isinstance(card.get("effort"), str) else None,
            "model": card.get("model") if isinstance(card.get("model"), str) else None,
            "model_asked": card.get("model_asked")
            if isinstance(card.get("model_asked"), str) else None,
            "cost": {key: int(value) for key, value in card["cost"].items()
                     if key in COST_KEYS and isinstance(value, (int, float))
                     and not isinstance(value, bool)}
            if isinstance(card.get("cost"), dict) else None,
            "seq": int(number("seq")),
            "source": text("source"), "project": text("project"), "record_epoch": text("record_epoch"),
            "session_id": text("session_id"), "desktop_cwd": text("desktop_cwd"),
            "monitoring_only": card.get("monitoring_only") is True,
            "desktop_status": text("desktop_status"),
            "client_open": card.get("client_open") is True,
            "observed_at": number("observed_at"),
            "stale": card.get("stale") is True,
            "content_scope": text("content_scope"),
            "content_truncated": card.get("content_truncated") is True,
            "result_ready": card.get("result_ready") is True,
            "summary_partial": text("summary_partial"),
            "run_id": text("run_id"),
            **({"dispatch": deepcopy(card["dispatch"])} if isinstance(card.get("dispatch"), dict) else {}),
            **({"progress_watch": deepcopy(card["progress_watch"])} if "progress_watch" in card else {}),
            **{key: text(key) for key in ("native_scope", "worker_scope", "worker_session", "worker_cwd", "worker_agent", "origin_message", "relay_state", "borrowed_session", "borrowed_cwd", "borrowed_scope", "desktop_card")},
            "binding_error": text("binding_error"),
            "last_meaningful_at": number("last_meaningful_at"),
            "execution_attempts": [dict(i) for i in card.get("execution_attempts", []) if isinstance(i, dict)][-64:],
            "supplements": [dict(item) for item in card.get("supplements", []) if isinstance(item, dict) and isinstance(item.get("text"), str)],
            **Cards._mesh_fields(card),
        }

    @staticmethod
    def _provenance_field(item, key):
        value = item.get(key)
        if not isinstance(value, dict):
            return {}
        kept = {name: text for name, text in value.items()
                if name in ("source", "actor", "cause") and isinstance(text, str)}
        return {key: kept} if kept else {}

    @staticmethod
    def _mesh_fields(card: Dict[str, Any]) -> Dict[str, Any]:
        """小鱼网络（mesh.py）留在卡上的字段：别的 Runtime 的事在这里的副本记着它从哪来，
        转给别的 Runtime 做的事记着交给了谁、替谁做。没有的不带。"""
        kept: Dict[str, Any] = {}
        for key in ("mesh_origin", "mesh_epoch", "mesh_id", "origin_name"):
            if isinstance(card.get(key), str) and card[key]:
                kept[key] = card[key]
        for key in ("delegated_from", "delegated_to"):
            value = card.get(key)
            if isinstance(value, dict) and isinstance(value.get("runtime"), str):
                kept[key] = {name: item for name, item in value.items()
                             if name in ("runtime", "card", "name") and isinstance(item, str)}
        return kept

    @staticmethod
    def _copy(card: Dict[str, Any], private: bool = False) -> Dict[str, Any]:
        copy = dict(card)
        if private and "dispatch" in card:
            copy["dispatch"] = deepcopy(card["dispatch"])
        else:
            copy.pop("dispatch", None)
        if private and "progress_watch" in card:
            copy["progress_watch"] = deepcopy(card["progress_watch"])
        else:
            copy.pop("progress_watch", None)
        copy["execution_attempts"] = [dict(item) for item in card.get("execution_attempts", [])]
        copy["entries"] = [dict(entry, **({"attachments": [dict(item) for item in entry.get("attachments", [])]}
                           if not private or "attachments" in entry else {})) for entry in card["entries"]]
        copy["attachments"] = [dict(item) for item in card.get("attachments", [])]
        copy["progress"] = list(card["progress"])
        copy["supplements"] = [dict(item) for item in card.get("supplements", [])]
        copy["cost"] = dict(card["cost"]) if card.get("cost") else None
        return copy

    def watches(self):
        with self._changed:
            return [(self._copy(card), deepcopy(card.get("progress_watch")))
                    for card in self._cards.values() if card.get("run_id")
                    and card["state"] in ("working", "waiting")
                    and not card.get("mesh_origin") and not card.get("monitoring_only")]

    def checkpoint_watch(self, card_id, run_id, previous, current):
        with self._changed:
            card = self._cards.get(card_id)
            if (not card or card.get("run_id") != run_id or card["state"] not in ("working", "waiting")
                    or card.get("mesh_origin") or card.get("monitoring_only")
                    or card.get("progress_watch") != previous
                    or not operations.watch_transition(previous, current, run_id)):
                return None
            original = card
            card = self._copy(card, private=True)
            card["progress_watch"] = deepcopy(current)
            self._cards[card_id] = card
            try:
                snapshot = self._touch(card)
            except OSError:
                self._cards[card_id] = original
                raise
        return self._tell(snapshot)

    def dispatches(self):
        """Private recovery inputs; never part of feed/sync or mesh payloads."""
        with self._changed:
            return [(self._copy(card), deepcopy(card["dispatch"]))
                    for card in self._cards.values()
                    if not card.get("mesh_origin") and not card.get("monitoring_only")
                    and card["state"] in ACTIVE + ("unknown",)
                    and isinstance(card.get("dispatch"), dict)]

    def dispatch(self, card_id):
        with self._changed:
            card = self._cards.get(card_id)
            if card is None or card.get("mesh_origin") or card.get("monitoring_only"):
                return None
            value = card.get("dispatch")
            return deepcopy(value) if isinstance(value, dict) else None

    def _save(self) -> None:
        """写一份快照（连同它包含到哪条任务日志），落稳后压缩日志。"""
        reserved = self._reserved
        if self._seq >= reserved:
            reserved = self._seq + SEQ_BLOCK
        self._journal.sync(self._cards, self._seq)
        journal = self._journal.n
        world.durable_write(self._path, {"next": self._next, "seq": reserved, "epoch": self._epoch,
                                         "journal": journal, "cards": list(self._cards.values())})
        # 写成功了才算占下：写失败时下一次变化还会再试。
        self._reserved = reserved
        self._journal.compact(journal)

    def set_origin(self, runtime_id: str) -> None:
        """本机产生的消息记录都写上这台 Runtime 的编号（见 MessageLog.set_origin）。"""
        with self._changed:
            self._log.set_origin(runtime_id)
            self.conversations.origin = runtime_id

    @property
    def epoch(self) -> str:
        """这份记录的编号：记录换了（文件丢了、坏了、换了一台）就不一样。"""
        return self._epoch

    @property
    def seq(self) -> int:
        with self._changed:
            return self._seq

    def bump(self, at_least: int = 0) -> int:
        """别的东西（消息）变了，要一个新序号：叫醒在等的人，返回这个号。

        at_least 用在启动时：消息记录里见过的最大序号比这里大时，从它往后接着发。
        平时不落盘；只有用到了预留的那段号的末尾时才写一次文件，占下下一段（见 SEQ_BLOCK）。"""
        with self._changed:
            self._seq = max(self._seq, at_least)
            seq = self._issue()
            self._changed.notify_all()
            return seq

    def since(self, conversation: str, after: int,
              include_desktop: bool = False) -> Any:
        """现在的序号，和这个对话里序号比 after 大的卡（按序号排）。两样是同一刻的。"""
        with self._changed:
            cards = [self._copy(card) for card in self._cards.values()
                     if (card["conversation"] == conversation
                         or (include_desktop and card.get("monitoring_only")))
                     and card["seq"] > after]
            cards.sort(key=lambda card: card["seq"])
            return self._seq, cards

    @property
    def log_floor(self) -> int:
        with self._changed:
            return self._log.floor

    def message(self, message_id: str) -> Optional[Dict[str, Any]]:
        """日志里的一条消息（没有返回 None）。"""
        with self._changed:
            return self._log.get(message_id)

    def session_targets(self, conversation=None):
        """Gateway metadata only; searching must not copy every retained transcript."""
        fields = ('id', 'conversation', 'title', 'state', 'agent', 'seq', 'project', 'source',
                  'monitoring_only', 'session_id', 'native_scope', 'desktop_cwd', 'desktop_status', 'client_open',
                  'worker_cwd', 'worker_scope', 'worker_session', 'mesh_origin', 'mesh_id',
                  'origin_name', 'available', 'stale', 'updated_at', 'approval', 'relay_state',
                  'borrowed_session', 'borrowed_scope')
        with self._changed:
            result = []
            for card in self._cards.values():
                if conversation is not None and card['conversation'] != conversation and not card.get('monitoring_only'):
                    continue
                item = {key: card[key] for key in fields if key in card}
                item['dispatch'] = {'state': (card.get('dispatch') or {}).get('state')}
                result.append(item)
            return result

    def context_snapshot(self, conversation):
        """Model history and attribution from one read lock, without UI copies."""
        with self._changed:
            tasks = {card["id"]: {"id": card["id"], "title": card["title"]}
                     for card in self._cards.values()
                     if card["conversation"] == conversation and not card.get("monitoring_only")}
            return tasks, self._log.since(conversation, 0, self._seq)

    def request(self, message: Dict[str, Any]) -> None:
        """Service 收下的一次请求变了（见 MessageLog.request）。"""
        with self._changed:
            self._log.request(message)
            self._changed.notify_all()

    def erase_conversation(self, conversation):
        with self._changed:
            from .conversation_directory import uid
            keys = {uid('chat',conversation)}
            for card in [*self._cards.values(),*self._retained.values()]:
                if card.get('conversation') == conversation:
                    keys.add(uid('task',card.get('mesh_origin') or self.conversations.origin,
                                 card.get('mesh_epoch') or card.get('record_epoch') or self._epoch,
                                 card.get('mesh_id') or card['id']))
            self.directory.erase(keys)
            for key, card in list(self._cards.items()):
                if card.get('conversation') == conversation:
                    del self._cards[key]
            for key, card in list(self._retained.items()):
                if card.get('conversation') == conversation:
                    self._path.with_suffix('.retained').joinpath(key + '.json').unlink(missing_ok=True)
                    del self._retained[key]
            self.catalog_seq = self._issue()
            self._save()
            self._changed.notify_all()

    def replicate_message(self, record: Dict[str, Any]) -> bool:
        """别的 Runtime 的一条消息在这里的副本（见 MessageLog.replicate）；变了返回 True。"""
        with self._changed:
            if (self.conversations.get(record.get('conversation')) or {}).get('deleted'):
                return False
            changed = self._log.replicate(record)
            if changed:
                self._changed.notify_all()
            return changed

    def request_changed(self, message: Dict[str, Any], revise: bool = True) -> None:
        """修订号加一（revise）、取序号、写进日志、叫醒等待的人：一把锁里做完（见 record.py）。"""
        with self._changed:
            provenance = self.current_provenance()
            if provenance:
                message["provenance"] = provenance
            if revise:
                message["rev"] = message.get("rev", 0) + 1
            message["seq"] = self._issue()
            self._log.request(message)
            self._changed.notify_all()

    def activity(self, card_id: str, author: str, phase: str, text: str, event_id: str,
                 run_id: Optional[str] = None) -> None:
        with self._changed:
            card = self._cards.get(card_id)
            if card is None or card.get("mesh_origin"):
                return
            if run_id is not None and card.get("run_id") != run_id:
                return  # A replaced run's late output is not this card's activity.
            if not self._log.activity(card, author, phase, text, event_id, time.time(), self.current_provenance()):
                return
            event = self._log.get(event_id)
            if event:
                self.audit.append(event)
            if phase in ("dispatch", "message", "tool_start", "tool_result", "completed", "failed", "cancelled"):
                card["last_meaningful_at"] = time.time()
                self._touch(card)
            # This event is not a conversation entry or a card state change.
            # Its log sequence wakes sync; do not rewrite every card for each
            # output chunk. The App derives its activity revision from messages.
            self._log.retain(entry["id"] for item in self._cards.values() for entry in item["entries"])
            self._changed.notify_all()

    @staticmethod
    def task_view(card: Dict[str, Any], unavailable: bool = True) -> Dict[str, Any]:
        """同步接口里的“事”：卡去掉消息副本，换成成员消息的编号。

        电脑上的会话说完一轮（desktop_status 是 idle）在卡上记成 done，好让旧 App 照常显示；
        同步接口里如实给 turn_ended：这一轮说完了，那件事本身没完。
        观察的会话或外部上报很久没更新（stale）、还没结束的，卡上留着最后知道的状态（或 unknown），
        同步接口里给 unavailable：现在看不到它的状态，不是做完了。认得这个状态的客户端才给
        （unavailable 为 False 时照旧给卡上的状态和 stale）。"""
        view = Cards._copy(card)
        view["members"] = [entry["id"] for entry in view.pop("entries")]
        if view.get("monitoring_only") and view.get("desktop_status") == "idle" and view.get("state") == "done":
            view["state"] = "turn_ended"
        elif (unavailable and (view.get("monitoring_only") or view.get("mesh_origin"))
              and view.get("stale") is True
              and view.get("state") in ("working", "waiting", "talking", "unknown")):
            # 别的 Runtime 的事很久没有消息（那台离线了）也一样：看不到，不是做完了。
            view["state"] = "unavailable"
        return view

    def replica(self, card_id: Optional[str], fields: Dict[str, Any],
                entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """别的 Runtime 的一件事在这里的副本（mesh.py）：没有 card_id 就开一张新的本地卡。

        字段和条目整段换成那台报来的样子；条目对应的消息已经由 mesh.py 原样写进日志
        （MessageLog.replicate），这里不再从条目反推消息。"""
        with self._changed:
            if (self.conversations.get(fields.get('conversation')) or {}).get('deleted'):
                return {}
            card = self._cards.get(card_id) if card_id else None
            if card is None:
                now = time.time()
                card = {"id": "c%d" % self._next, "conversation": "default", "title": "",
                        "title_source": "task", "title_truncated": False, "state": "done",
                        "agent": None, "created_at": now, "updated_at": now, "finished_at": None,
                        "entries": [], "attachments": [], "brief": "", "mood": "idle",
                        "progress": [], "started_at": None, "edits": 0, "approval": None,
                        "queued": False, "effort": None, "model": None, "model_asked": None,
                        "cost": None, "seq": 0}
                self._next += 1
                self._cards[card["id"]] = card
            for key, value in fields.items():
                if key not in ("id", "seq", "updated_at", "entries"):
                    card[key] = value
            card["entries"] = [dict(entry, attachments=clean_attachments(entry.get("attachments")))
                               for entry in entries][-MAX_ENTRIES:]
            self._prune()
            snapshot = self._touch(card)
        return self._tell(snapshot)

    def own_since(self, after: int) -> Any:
        """（现在的序号, 这台自己的事, 这台自己的消息）：序号比 after 大的，所有对话。
        小鱼网络把它们交给别的 Runtime（副本不算：那是别人的）。"""
        with self._changed:
            tasks = [self.task_view(card, unavailable=False) for card in self._cards.values()
                     if card["seq"] > after and not card.get("mesh_origin")]
            messages = self._log.own_since(after, self._seq)
            return self._seq, tasks, messages

    def sync_since(self, conversation: Optional[str], after: int, unavailable: bool = True) -> Any:
        """（现在的序号, 变了的事, 变了的消息, floor）：同一刻、同一把锁里取的，不会漏。"""
        with self._changed:
            tasks = [self.task_view(card, unavailable) for card in self._cards.values()
                     if (conversation is None or card["conversation"] == conversation or card.get("monitoring_only"))
                     and card["seq"] > after]
            tasks.sort(key=lambda task: task["seq"])
            messages = self._log.since(conversation, after, self._seq, include_desktop=True)
            return self._seq, tasks, messages, self._log.floor

    def wait_beyond(self, seq: int, timeout: float) -> int:
        """等到序号超过 seq（任何卡或消息变了），最多等 timeout 秒；返回现在的序号。"""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._changed:
            while self._seq <= seq:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._changed.wait(remaining)
            return self._seq

    def _touch(self, card: Dict[str, Any]) -> Dict[str, Any]:
        """调用时已经拿着锁：记下这张卡变了（连同它的消息），落盘，叫醒在等的人。"""
        provenance = self.current_provenance()
        if provenance:
            card["changed_by"] = provenance
        if not card.get("mesh_origin"):
            card["record_epoch"] = card.get("record_epoch") or self._epoch
            self._log.observe(card)
        self._seq += 1
        card["seq"] = self._seq
        card["updated_at"] = time.time()
        self._journal.record(card)  # 记进任务日志就算落盘了；快照攒够了才重写
        self._log.retain(entry["id"] for item in self._cards.values() for entry in item["entries"])
        if self._journal.pending >= world.SNAPSHOT_EVERY or self._seq >= self._reserved:
            try:
                self._save()
            except OSError as error:
                # The journal above is already durable. A failed checkpoint
                # must not turn an accepted input into an ambiguous failure.
                sys.stderr.write("任务快照写入失败，已保存的日志仍然有效：%s\n" % error)
        self._changed.notify_all()
        return self._copy(card)

    def _tell(self, snapshot: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if snapshot is not None:
            for listener in list(self._listeners):
                listener(snapshot)
        return snapshot

    def subscribe(self, listener: Listener) -> None:
        """每次有卡变化就调用一次（在锁外面，拿到的是那一刻的副本）。"""
        self._listeners.append(listener)

    def bind_execution(self, card_id, attempt, fields, *, required=False):
        with self._changed:
            card = self._cards.get(card_id)
            if card is None or card.get('mesh_origin'):
                return False
            if required and (card['state'] not in ACTIVE or not fields.get('run_id')
                             or card.get('run_id') != fields['run_id']):
                return False
            dispatch = card.get("dispatch")
            if required and dispatch is not None and (
                    not operations.valid_dispatch(dispatch)
                    or dispatch["run_id"] != fields["run_id"]
                    or dispatch["state"] not in ("pending", "running")):
                return False
            rows = [dict(i) for i in card.get('execution_attempts', [])]
            existing = next((i for i in rows if i.get('attempt_id') == attempt), None)
            if existing is None:
                existing = {'attempt_id':attempt}
                rows.append(existing)
            existing.update(fields)
            seq = self._issue()
            self.audit.append({'id':'binding-'+attempt+'-'+str(seq), 'task':card_id, 'seq':seq,
                               'phase':'binding', 'text':'Native execution association',
                               'binding':dict(existing), 'created_at':time.time()})
            previous = self._copy(card, private=True)
            try:
                card['execution_attempts'] = rows[-64:]
                if required and dispatch is not None:
                    card["dispatch"] = dict(dispatch, state="running")
                snapshot = self._touch(card)
            except OSError:
                self._cards[card_id] = previous
                raise
        self._tell(snapshot)
        return True

    def retained(self):
        """Immutable canonical snapshots evicted from the live sync window."""
        with self._changed:
            return [self._copy(card) for card in self._retained.values()]

    def _seal_card(self, card):
        if not card.get('agent') and not card.get('worker_agent') and not card.get('monitoring_only'):
            return
        import hashlib
        origin = card.get('mesh_origin') or self.conversations.origin
        epoch = card.get('mesh_epoch') or card.get('record_epoch') or self._epoch
        key = hashlib.sha256(('%s/%s/%s' % (origin,epoch,card['id'])).encode()).hexdigest()
        if key in self._retained:
            return
        snapshot = self._copy(card)
        snapshot['record_epoch'] = epoch
        snapshot['retained_only'] = True
        snapshot['canonical_message_ids'] = [m['id'] for m in self._log._messages.values() if m.get('task') == card['id']]
        folder = self._path.with_suffix('.retained')
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        _write(folder / (key+'.json'), snapshot, 2)
        self._retained[key] = snapshot

    def _prune(self) -> None:
        """调用时已经拿着锁：只丢已经结束的旧卡，还在做的不能丢；最近做完的
        MAX_HISTORY 件任务要留着，不被别的卡挤掉。"""
        # 每台 Runtime 的事各算各的：网络里别的 Runtime 的副本不挤掉这台自己的卡。
        groups: Dict[str, List[str]] = {}
        for card_id, card in self._cards.items():
            groups.setdefault(card.get("mesh_origin", ""), []).append(card_id)
        for ids in groups.values():
            self._prune_group(ids)

    def _prune_group(self, ids: List[str]) -> None:
        group = [self._cards[card_id] for card_id in ids]
        if len(group) <= MAX_CARDS:
            return
        finished = [card for card in group
                    if card["agent"] and card["state"] not in ACTIVE + ("unknown",)]
        finished.sort(key=lambda card: card.get("finished_at") or card["updated_at"],
                      reverse=True)
        protected = {card["id"] for card in finished[:MAX_HISTORY]}
        observed = [card for card in group
                    if card.get("monitoring_only") and card["state"] == "unknown"]
        observed.sort(key=lambda card: card.get("observed_at", 0), reverse=True)
        protected.update(card["id"] for card in observed[:32])
        left = len(group)
        for card in group:
            if left <= MAX_CARDS:
                break
            if card["state"] in ACTIVE or card["id"] in protected or (
                    card["state"] == "unknown" and card.get("dispatch")):
                continue
            self._seal_card(card)
            self._journal.remove(card["id"], self._seq)
            del self._cards[card["id"]]
            left -= 1

    def open(self, conversation: str, title: str, state: str = "done",
             agent: Optional[str] = None, *, title_source: str = "request",
             **extra: Any) -> Dict[str, Any]:
        """开一张卡。extra 只收小鱼网络的字段（delegated_from 这类），开卡时就带上，
        别的 Runtime 第一次看到这张卡时就知道它是替谁做的。"""
        with self._changed:
            now = time.time()
            name = canonical_title(title, title_source)
            if not name.title:
                name = select_title()
            card = {
                **({"changed_by": self.current_provenance()} if self.current_provenance() else {}),
                "id": "c%d" % self._next, "conversation": conversation,
                **name.fields(), "state": state, "agent": agent,
                "created_at": now, "updated_at": now, "finished_at": None,
                "entries": [], "attachments": [], "brief": "", "mood": "idle",
                "progress": [], "started_at": None, "edits": 0, "approval": None,
                "queued": False, "effort": None, "model": None, "model_asked": None,
                "cost": None, "seq": 0, **self._mesh_fields(extra),
                **({"monitoring_only": True} if extra.get("monitoring_only") is True else {}),
            }
            self._next += 1
            self._cards[card["id"]] = card
            self._prune()
            snapshot = self._touch(card)
        return self._tell(snapshot)

    def get(self, card_id: str) -> Optional[Dict[str, Any]]:
        with self._changed:
            card = self._cards.get(card_id)
            return self._copy(card) if card is not None else None

    @contextmanager
    def provenance(self, **fields: Any) -> Iterator[None]:
        """这段时间里、这个线程上的写入都记上来源（source、actor、cause）；里层的覆盖外层的。"""
        previous = getattr(self._provenance, "value", None)
        merged = {key: value for key, value in fields.items() if value}
        self._provenance.value = merged or None
        try:
            yield
        finally:
            self._provenance.value = previous

    def current_provenance(self) -> Optional[Dict[str, Any]]:
        value = getattr(self._provenance, "value", None)
        return dict(value) if value else None

    def directory_changed(self, card_ids):
        snapshots = []
        with self._changed:
            for key in dict.fromkeys(card_ids):
                if key in self._cards:
                    snapshots.append(self._touch(self._cards[key]))
        for snapshot in snapshots:
            self._tell(snapshot)

    def update(self, card_id: str, say: Optional[str] = None, said: Optional[str] = None,
               progress: Optional[str] = None, fresh: bool = False,
               progress_lines: Optional[List[str]] = None,
               attachments: Optional[List[Dict[str, Any]]] = None,
               turn_id: Optional[str] = None, scope: str = "main", kind: str = "reply",
               author: str = "xiaoyou",
               expected_run=None, expected_execution=None, expected_active_run=None,
               expected_run_id=None, interrupted: bool = False,
               **fields: Any) -> Optional[Dict[str, Any]]:
        """改一张卡。said 是主人说的一句，say 是小鱼说的一句，progress 是一行进展；
        fresh 表示新的一轮开始了，之前的进展清掉。expected_run_id 只要求卡还是这次派发
        （停掉了也算），用来记这次派发自己的事实。"""
        with self._changed:
            card = self._cards.get(card_id)
            if card is None:
                return None
            if expected_run_id is not None and card.get("run_id") != expected_run_id:
                return None
            if expected_active_run is not None and (
                    card.get("run_id") != expected_active_run or card["state"] not in ACTIVE):
                return None
            if expected_run is not None and (
                    card.get("run_id") != expected_run[0]
                    or (card.get("dispatch") or {}).get("state") != expected_run[1]
                    or card["state"] not in ACTIVE + ("unknown",)):
                return None
            if expected_execution is not None:
                attempts = card.get("execution_attempts", [])
                if expected_run is None or not attempts or attempts[-1] != expected_execution:
                    return None
            previous = card
            if expected_run is not None or expected_active_run is not None:
                card = self._copy(card, private=True)
                self._cards[card_id] = card
            now = time.time()
            provenance = self.current_provenance()
            first_new = len(card["entries"])
            if said is not None:
                card["entries"].append({"role": "you", "text": said[:MAX_ENTRY_CHARS], "at": now,
                                        "id": turn_id + ":you" if turn_id else "", "scope": scope,
                                        "kind": "user", "author": "you"})
            if say is not None:
                card["entries"].append(
                    {"role": "xiaoyou", "text": say[:MAX_ENTRY_CHARS], "at": now,
                     "id": turn_id + ":reply" if turn_id else "", "scope": scope,
                     "kind": kind, "author": author,
                     # The owner cut in: only part of this reply was said.
                     **({"interrupted": True} if interrupted else {})})
            if attachments is not None:
                incoming = clean_attachments(attachments)
                self._merge_attachments(card, incoming)
                if say is not None:
                    card["entries"][-1]["attachments"] = incoming
            if provenance:
                for entry in card["entries"][first_new:]:
                    entry.setdefault("provenance", provenance)
                card["changed_by"] = provenance
            del card["entries"][:-MAX_ENTRIES]
            if fresh:
                card["progress"] = []
            if progress is not None:
                card["progress"].append(" ".join(progress.split())[:MAX_PROGRESS_CHARS])
                del card["progress"][:-MAX_PROGRESS_LINES]
            if progress_lines is not None:
                card["progress"] = [" ".join(line.split())[:MAX_PROGRESS_CHARS]
                                    for line in progress_lines[-MAX_PROGRESS_LINES:]]
            if "title" in fields:
                name = canonical_title(fields["title"],
                                       fields.get("title_source", card.get("title_source", "task")))
                fields.update(name.fields(), title_truncated=name.truncated or
                              fields.get("title_truncated") is True)
            old_run = card.get("run_id")
            card.update(fields)
            if card.get("run_id") != old_run:
                card.pop("progress_watch", None)
            if isinstance(card.get("dispatch"), dict):
                terminal = operations.FROM_STATUS.get(fields.get("state"))
                if terminal in operations.TERMINAL:
                    card["dispatch"] = dict(card["dispatch"], state=terminal)
            # 交给过帮手的事走到结束：记下完成时间，好按天给第三屏分组。又接着做的
            # （补充、改要求）不算完，下次结束时再记。
            if card["state"] in ACTIVE:
                card["finished_at"] = None
            elif card["state"] == "unknown":
                card["finished_at"] = None
            elif card["agent"] and not card.get("finished_at"):
                card["finished_at"] = now
            try:
                snapshot = self._touch(card)
            except OSError:
                if expected_run is not None or expected_active_run is not None:
                    self._cards[card_id] = previous
                raise
        return self._tell(snapshot)

    @staticmethod
    def _merge_attachments(card: Dict[str, Any], incoming: List[Dict[str, Any]]) -> None:
        items = {item["id"]: item for item in card.get("attachments", [])}
        for item in incoming:
            items[item["id"]] = dict(item)
        card["attachments"] = list(items.values())

    def add_attachments(self, card_id: str, attachments: List[Dict[str, Any]],
                        entry_index: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Attach registered outputs and wake feed clients even without a text edit."""
        with self._changed:
            card = self._cards.get(card_id)
            if card is None:
                return None
            incoming = clean_attachments(attachments)
            self._merge_attachments(card, incoming)
            if entry_index is None:
                entry_index = next((index for index in range(len(card["entries"]) - 1, -1, -1)
                                    if card["entries"][index]["role"] == "xiaoyou"), None)
            if entry_index is not None and 0 <= entry_index < len(card["entries"]):
                entry = card["entries"][entry_index]
                items = {item["id"]: item for item in entry.get("attachments", [])}
                items.update({item["id"]: dict(item) for item in incoming})
                entry["attachments"] = list(items.values())
            snapshot = self._touch(card)
        return self._tell(snapshot)

    def recent(self, conversation: Optional[str], limit: int = 30,
               include_desktop: bool = False) -> List[Dict[str, Any]]:
        """最近的卡，按开卡的先后。"""
        with self._changed:
            cards = [card for card in self._cards.values()
                     if conversation is None or card["conversation"] == conversation
                     or (include_desktop and card.get("monitoring_only"))]
            return [self._copy(card) for card in cards[-limit:]]

    def changed(self, conversation: str, after: int = 0, wait: float = 0.0,
                include_desktop: bool = False, budget: int = 0) -> Dict[str, Any]:
        """序号比 after 大的卡；一张都没有时最多等 wait 秒。

        budget 大于 0 时一次最多给这么多字节（至少一张）：剩下的标 more，序号停在给出的
        最后一张，客户端照常拿这个序号再要，就接着往下给。慢线路上一次几百 KB 常传不完。"""
        deadline = time.monotonic() + max(0.0, wait)
        with self._changed:
            while True:
                cards = [self._copy(card) for card in self._cards.values()
                         if (card["conversation"] == conversation
                             or (include_desktop and card.get("monitoring_only")))
                         and card["seq"] > after]
                remaining = deadline - time.monotonic()
                if cards or remaining <= 0:
                    cards.sort(key=lambda card: card["seq"])
                    if budget > 0:
                        size = 0
                        for index, card in enumerate(cards):
                            size += len(json.dumps(card, ensure_ascii=False).encode("utf-8"))
                            if size > budget and index > 0:
                                return {"seq": cards[index - 1]["seq"], "cards": cards[:index],
                                        "more": True}
                    # 卡被清掉、或者 Runtime 换了一份记录之后，客户端手里的序号可能比这里的大：
                    # 把现在的序号给回去，它就知道要从头再同步。
                    return {"seq": self._seq, "cards": cards}
                self._changed.wait(remaining)

    def settle(self, card_id: str, timeout: float) -> Optional[Dict[str, Any]]:
        """等一张卡结束（做完、没成或取消）；超时返回那一刻的样子。"""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._changed:
            while True:
                card = self._cards.get(card_id)
                remaining = deadline - time.monotonic()
                if card is None or card["state"] not in ACTIVE or remaining <= 0:
                    return self._copy(card) if card is not None else None
                self._changed.wait(remaining)
