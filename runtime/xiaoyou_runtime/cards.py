"""事（card）：一个需求从提出到结束的全部内容。

主人提的每个需求是一件事。回答、进展、后来的补充都记在它自己那张卡上，客户端按卡
显示，不按到达的先后混排。卡有编号（c1、c2……）、标题、状态、谁在做、主人说的话、
小幽说的话、最近几行进展；交给帮手做的还有用的是哪一档努力程度（effort）、实际用的
模型（model）、到现在花了多少（cost）。

每次有卡变化，全局的序号加一并记在那张卡上：客户端记住看到的最大序号，带着它来
等（/v1/feed），就只会拿到之后变过的卡。消息（Service 里还在处理的那些话）变化时也从
这里取号（bump），所以卡和消息排在同一条序号上，客户端一个游标就能接着拉两样东西。

序号和这份记录的编号（epoch）都落盘：Runtime 重启后序号接着往上走；记录文件丢了、坏了
或者换了一份时 epoch 会变，客户端看到 epoch 变了就从头同步。

卡落盘在 state/cards.json，只留最近的若干张。Runtime 重启时还在做的事没法接着做，
启动时把它们标成没做完。
"""

import json
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .message_log import MessageLog
from .store import _write
from .artifacts import clean_attachments
from .task_titles import MAX_REQUEST_TITLE_CHARS, canonical_title, select_title

# talking 留给“小幽正在处理这句话”，现在 Runtime 自己不会把卡放在这个状态。
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
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}  # 丢了只是看不到旧卡，不值得为它拒绝启动
        if isinstance(raw, dict):
            for card in raw.get("cards", []):
                if isinstance(card, dict) and isinstance(card.get("id"), str):
                    self._cards[card["id"]] = self._clean(card)
            if isinstance(raw.get("next"), int) and not isinstance(raw.get("next"), bool):
                self._next = max(1, raw["next"])
            saved_seq = raw.get("seq")
            self._seq = max([card["seq"] for card in self._cards.values()]
                            + [saved_seq if isinstance(saved_seq, int)
                               and not isinstance(saved_seq, bool) else 0, 0])
            if isinstance(raw.get("epoch"), str):
                self._epoch = raw["epoch"]
        self._reserved = self._seq
        fresh_epoch = not self._epoch
        if fresh_epoch:
            # 旧版本写的文件没有 epoch：第一次补上。客户端原来手里的序号仍然有效（序号没变），
            # 只是从这以后它能认出“换了一份记录”。
            self._epoch = uuid.uuid4().hex
        # 消息日志是每句话的权威记录（见 message_log.py）；卡上的 entries 是副本。
        self._log = MessageLog(self._path.with_name(self._path.stem + ".log.jsonl"), self._issue)
        # cards.json 丢了、日志还在时：序号要从日志里见过的最大号往后数，不能重复发号。
        if self._log.max_seq() > self._seq:
            self._seq = self._log.max_seq()
            self._reserved = self._seq
        interrupted = [card for card in self._cards.values() if card["state"] in ACTIVE]
        for card in interrupted:
            self._seq += 1
            if card.get("monitoring_only"):
                card.update(state="unknown", desktop_status="unknown", stale=True,
                            brief="电脑会话状态尚未更新", approval=None, queued=False,
                            updated_at=time.time(), finished_at=None, seq=self._seq)
                continue
            card["entries"].append({"role": "xiaoyou", "text": RESTART_NOTE, "at": time.time(),
                                    "kind": "note"})
            card.update(state="failed", brief=RESTART_NOTE, mood="oops", approval=None,
                        queued=False, updated_at=time.time(), finished_at=time.time(),
                        seq=self._seq)
        migrated = self._adopt_log()
        if interrupted or fresh_epoch or migrated:
            self._save()

    def _adopt_log(self) -> bool:
        """启动时：卡上的副本以日志为准；日志里还没有的（旧版本的卡）补进日志。"""
        changed = False
        for card in self._cards.values():
            for entry in card["entries"]:
                record = self._log.get(entry["id"]) if entry.get("id") else None
                if record is not None and (record["text"] != entry["text"]
                                           or record["attachments"] != entry["attachments"]):
                    entry.update(text=record["text"],
                                 attachments=[dict(item) for item in record["attachments"]])
                    changed = True
            before = [entry.get("id") for entry in card["entries"]]
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
             "attachments": clean_attachments(entry.get("attachments"))}
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
            "source": text("source"), "project": text("project"),
            "session_id": text("session_id"),
            "monitoring_only": card.get("monitoring_only") is True,
            "desktop_status": text("desktop_status"),
            "observed_at": number("observed_at"),
            "stale": card.get("stale") is True,
            "content_scope": text("content_scope"),
            "content_truncated": card.get("content_truncated") is True,
            "result_ready": card.get("result_ready") is True,
            "summary_partial": text("summary_partial"),
            "run_id": text("run_id"),
        }

    @staticmethod
    def _copy(card: Dict[str, Any]) -> Dict[str, Any]:
        copy = dict(card)
        copy["entries"] = [dict(entry, attachments=[dict(item) for item in entry.get("attachments", [])])
                           for entry in card["entries"]]
        copy["attachments"] = [dict(item) for item in card.get("attachments", [])]
        copy["progress"] = list(card["progress"])
        copy["cost"] = dict(card["cost"]) if card.get("cost") else None
        return copy

    def _save(self) -> None:
        reserved = self._reserved
        if self._seq >= reserved:
            reserved = self._seq + SEQ_BLOCK
        _write(self._path, {"next": self._next, "seq": reserved, "epoch": self._epoch,
                            "cards": list(self._cards.values())})
        # 写成功了才算占下：写失败时下一次变化还会再试。
        self._reserved = reserved

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

    def request(self, message: Dict[str, Any]) -> None:
        """Service 收下的一次请求变了（见 MessageLog.request）。"""
        with self._changed:
            self._log.request(message)
            self._changed.notify_all()

    @staticmethod
    def task_view(card: Dict[str, Any]) -> Dict[str, Any]:
        """同步接口里的“事”：卡去掉消息副本，换成成员消息的编号。"""
        view = Cards._copy(card)
        view["members"] = [entry["id"] for entry in view.pop("entries")]
        return view

    def sync_since(self, conversation: str, after: int) -> Any:
        """（现在的序号, 变了的事, 变了的消息, floor）：同一刻、同一把锁里取的，不会漏。"""
        with self._changed:
            tasks = [self.task_view(card) for card in self._cards.values()
                     if (card["conversation"] == conversation or card.get("monitoring_only"))
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
        self._log.observe(card)
        self._seq += 1
        card["seq"] = self._seq
        card["updated_at"] = time.time()
        self._log.retain(entry["id"] for item in self._cards.values() for entry in item["entries"])
        self._save()
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

    def _prune(self) -> None:
        """调用时已经拿着锁：只丢已经结束的旧卡，还在做的不能丢；最近做完的
        MAX_HISTORY 件任务要留着，不被别的卡挤掉。"""
        if len(self._cards) <= MAX_CARDS:
            return
        finished = [card for card in self._cards.values()
                    if card["agent"] and card["state"] not in ACTIVE + ("unknown",)]
        finished.sort(key=lambda card: card.get("finished_at") or card["updated_at"],
                      reverse=True)
        protected = {card["id"] for card in finished[:MAX_HISTORY]}
        observed = [card for card in self._cards.values()
                    if card.get("monitoring_only") and card["state"] == "unknown"]
        observed.sort(key=lambda card: card.get("observed_at", 0), reverse=True)
        protected.update(card["id"] for card in observed[:32])
        for card_id in list(self._cards):
            if len(self._cards) <= MAX_CARDS:
                break
            card = self._cards[card_id]
            if card["state"] in ACTIVE or card_id in protected:
                continue
            del self._cards[card_id]

    def open(self, conversation: str, title: str, state: str = "done",
             agent: Optional[str] = None, *, title_source: str = "request") -> Dict[str, Any]:
        with self._changed:
            now = time.time()
            name = canonical_title(title, title_source)
            if not name.title:
                name = select_title()
            card = {
                "id": "c%d" % self._next, "conversation": conversation,
                **name.fields(), "state": state, "agent": agent,
                "created_at": now, "updated_at": now, "finished_at": None,
                "entries": [], "attachments": [], "brief": "", "mood": "idle",
                "progress": [], "started_at": None, "edits": 0, "approval": None,
                "queued": False, "effort": None, "model": None, "model_asked": None,
                "cost": None, "seq": 0,
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

    def update(self, card_id: str, say: Optional[str] = None, said: Optional[str] = None,
               progress: Optional[str] = None, fresh: bool = False,
               progress_lines: Optional[List[str]] = None,
               attachments: Optional[List[Dict[str, Any]]] = None,
               turn_id: Optional[str] = None, scope: str = "main", kind: str = "reply",
               author: str = "xiaoyou",
               **fields: Any) -> Optional[Dict[str, Any]]:
        """改一张卡。said 是主人说的一句，say 是小幽说的一句，progress 是一行进展；
        fresh 表示新的一轮开始了，之前的进展清掉。"""
        with self._changed:
            card = self._cards.get(card_id)
            if card is None:
                return None
            now = time.time()
            if said is not None:
                card["entries"].append({"role": "you", "text": said[:MAX_ENTRY_CHARS], "at": now,
                                        "id": turn_id + ":you" if turn_id else "", "scope": scope,
                                        "kind": "user", "author": "you"})
            if say is not None:
                card["entries"].append(
                    {"role": "xiaoyou", "text": say[:MAX_ENTRY_CHARS], "at": now,
                     "id": turn_id + ":reply" if turn_id else "", "scope": scope,
                     "kind": kind, "author": author})
            if attachments is not None:
                incoming = clean_attachments(attachments)
                self._merge_attachments(card, incoming)
                if say is not None:
                    card["entries"][-1]["attachments"] = incoming
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
            card.update(fields)
            # 交给过帮手的事走到结束：记下完成时间，好按天给第三屏分组。又接着做的
            # （补充、改要求）不算完，下次结束时再记。
            if card["state"] in ACTIVE:
                card["finished_at"] = None
            elif card["state"] == "unknown":
                card["finished_at"] = None
            elif card["agent"] and not card.get("finished_at"):
                card["finished_at"] = now
            snapshot = self._touch(card)
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
