"""事（card）：一个需求从提出到结束的全部内容。

主人提的每个需求是一件事。回答、进展、后来的补充都记在它自己那张卡上，客户端按卡
显示，不按到达的先后混排。卡有编号（c1、c2……）、标题、状态、谁在做、主人说的话、
小幽说的话、最近几行进展；交给帮手做的还有用的是哪一档努力程度（effort）、实际用的
模型（model）、到现在花了多少（cost）。

每次有卡变化，全局的序号加一并记在那张卡上：客户端记住看到的最大序号，带着它来
等（/v1/feed），就只会拿到之后变过的卡。

卡落盘在 state/cards.json，只留最近的若干张。Runtime 重启时还在做的事没法接着做，
启动时把它们标成没做完。
"""

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .store import _write

# talking 留给“小幽正在处理这句话”，现在 Runtime 自己不会把卡放在这个状态。
STATES = ("talking", "working", "waiting", "done", "failed", "cancelled")
ACTIVE = ("talking", "working", "waiting")
MAX_CARDS = 50
# 做完的任务单独留这么多（最近的在前），不被别的卡挤掉：第三屏的历史靠它。
MAX_HISTORY = 15
MAX_ENTRIES = 40
MAX_ENTRY_CHARS = 8000
MAX_TITLE_CHARS = 24
MAX_PROGRESS_LINES = 5
MAX_PROGRESS_CHARS = 200
RESTART_NOTE = "Runtime 重启了，这件事没做完"
# 一件事花了多少：token（新读的、写的、从缓存读的），和订阅额度两个窗口各少了几个百分点。
COST_KEYS = ("in", "out", "cached", "d5", "d7")

Listener = Callable[[Dict[str, Any]], None]


def title_from(text: str) -> str:
    """没有人起标题时，用这句话的开头当标题。"""
    return " ".join(text.split())[:MAX_TITLE_CHARS] or "（没有标题）"


class Cards:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._changed = threading.Condition()
        self._cards: Dict[str, Dict[str, Any]] = {}
        self._next = 1
        self._seq = 0
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
            self._seq = max([card["seq"] for card in self._cards.values()] + [0])
        interrupted = [card for card in self._cards.values() if card["state"] in ACTIVE]
        for card in interrupted:
            self._seq += 1
            card["entries"].append({"role": "xiaoyou", "text": RESTART_NOTE, "at": time.time()})
            card.update(state="failed", brief=RESTART_NOTE, mood="oops", approval=None,
                        queued=False, updated_at=time.time(), finished_at=time.time(),
                        seq=self._seq)
        if interrupted:
            self._save()

    @staticmethod
    def _clean(card: Dict[str, Any]) -> Dict[str, Any]:
        def text(key: str, default: str = "") -> str:
            return card[key] if isinstance(card.get(key), str) else default

        def number(key: str) -> Any:
            value = card.get(key)
            return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

        entries = [
            {"role": entry["role"], "text": entry["text"],
             "at": entry.get("at") if isinstance(entry.get("at"), (int, float)) else 0}
            for entry in card.get("entries", [])
            if isinstance(entry, dict) and entry.get("role") in ("you", "xiaoyou")
            and isinstance(entry.get("text"), str)
        ] if isinstance(card.get("entries"), list) else []
        state = text("state", "done")
        return {
            "id": card["id"],
            "conversation": text("conversation", "default"),
            "title": text("title"),
            "state": state if state in STATES else "done",
            "agent": card.get("agent") if isinstance(card.get("agent"), str) else None,
            "created_at": number("created_at"),
            "updated_at": number("updated_at"),
            "finished_at": card["finished_at"]
            if isinstance(card.get("finished_at"), (int, float))
            and not isinstance(card.get("finished_at"), bool) else None,
            "entries": entries,
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
        }

    @staticmethod
    def _copy(card: Dict[str, Any]) -> Dict[str, Any]:
        copy = dict(card)
        copy["entries"] = [dict(entry) for entry in card["entries"]]
        copy["progress"] = list(card["progress"])
        copy["cost"] = dict(card["cost"]) if card.get("cost") else None
        return copy

    def _save(self) -> None:
        _write(self._path, {"next": self._next, "cards": list(self._cards.values())})

    def _touch(self, card: Dict[str, Any]) -> Dict[str, Any]:
        """调用时已经拿着锁：记下这张卡变了，落盘，叫醒在等的人。"""
        self._seq += 1
        card["seq"] = self._seq
        card["updated_at"] = time.time()
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
                    if card["agent"] and card["state"] not in ACTIVE]
        finished.sort(key=lambda card: card.get("finished_at") or card["updated_at"],
                      reverse=True)
        protected = {card["id"] for card in finished[:MAX_HISTORY]}
        for card_id in list(self._cards):
            if len(self._cards) <= MAX_CARDS:
                break
            card = self._cards[card_id]
            if card["state"] in ACTIVE or card_id in protected:
                continue
            del self._cards[card_id]

    def open(self, conversation: str, title: str, state: str = "done",
             agent: Optional[str] = None) -> Dict[str, Any]:
        with self._changed:
            now = time.time()
            card = {
                "id": "c%d" % self._next, "conversation": conversation,
                "title": title_from(title), "state": state, "agent": agent,
                "created_at": now, "updated_at": now, "finished_at": None,
                "entries": [], "brief": "", "mood": "idle",
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
               **fields: Any) -> Optional[Dict[str, Any]]:
        """改一张卡。said 是主人说的一句，say 是小幽说的一句，progress 是一行进展；
        fresh 表示新的一轮开始了，之前的进展清掉。"""
        with self._changed:
            card = self._cards.get(card_id)
            if card is None:
                return None
            now = time.time()
            if said is not None:
                card["entries"].append({"role": "you", "text": said[:MAX_ENTRY_CHARS], "at": now})
            if say is not None:
                card["entries"].append(
                    {"role": "xiaoyou", "text": say[:MAX_ENTRY_CHARS], "at": now})
            del card["entries"][:-MAX_ENTRIES]
            if fresh:
                card["progress"] = []
            if progress is not None:
                card["progress"].append(" ".join(progress.split())[:MAX_PROGRESS_CHARS])
                del card["progress"][:-MAX_PROGRESS_LINES]
            card.update(fields)
            # 交给过帮手的事走到结束：记下完成时间，好按天给第三屏分组。又接着做的
            # （补充、改要求）不算完，下次结束时再记。
            if card["state"] in ACTIVE:
                card["finished_at"] = None
            elif card["agent"] and not card.get("finished_at"):
                card["finished_at"] = now
            snapshot = self._touch(card)
        return self._tell(snapshot)

    def recent(self, conversation: Optional[str], limit: int = 30) -> List[Dict[str, Any]]:
        """最近的卡，按开卡的先后。"""
        with self._changed:
            cards = [card for card in self._cards.values()
                     if conversation is None or card["conversation"] == conversation]
            return [self._copy(card) for card in cards[-limit:]]

    def changed(self, conversation: str, after: int = 0, wait: float = 0.0) -> Dict[str, Any]:
        """序号比 after 大的卡；一张都没有时最多等 wait 秒。"""
        deadline = time.monotonic() + max(0.0, wait)
        with self._changed:
            while True:
                cards = [self._copy(card) for card in self._cards.values()
                         if card["conversation"] == conversation and card["seq"] > after]
                remaining = deadline - time.monotonic()
                if cards or remaining <= 0:
                    cards.sort(key=lambda card: card["seq"])
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
