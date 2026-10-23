"""World：从任务日志确定性地重建任务的当前状态（见 docs/development/runtime-refactor-design 的 R3）。

统一消息日志（cards.log.jsonl）只记消息和进展；任务本身的状态以前只在 cards.json 里。
这里给任务补一条只追加的日志 cards.tasks.jsonl：每次卡变了，只记和上一次相比变了什么
（差量）；卡被清掉时记一条 removed。rebuild() 是纯函数：同样的日志永远重建出同样的状态，
测试可以用固定种子随机操作、在任意位置截断日志来模拟崩溃。

现在（R3.1）cards.json 仍是启动时的权威：TaskJournal 启动时先重放日志，再和 cards.json
对齐（缺的补一条差量），所以两边总是一致的。R3.2 才把启动改成“快照 + 重放”。
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

JOURNAL_SUFFIX = ".tasks.jsonl"


def _plain(value: Any) -> Any:
    """深拷贝并规整成 JSON 能表示的样子：比较和重放都在这个形态上做。"""
    return json.loads(json.dumps(value, ensure_ascii=False))


def diff(seq: int, previous: Optional[Dict[str, Any]], card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """card 相比 previous 变了什么；没变返回 None。previous 为 None 时记整张卡。"""
    card = _plain(card)
    record: Dict[str, Any] = {"seq": seq, "id": card["id"]}
    if previous is None:
        record["card"] = card
        return record
    changed = {key: value for key, value in card.items()
               if key != "entries" and previous.get(key, _MISSING) != value}
    gone = sorted(key for key in previous if key != "entries" and key not in card)
    before, after = previous.get("entries", []), card.get("entries", [])
    if changed:
        record["set"] = changed
    if gone:
        record["unset"] = gone
    if after != before:
        record.update(_entries_delta(before, after))
    return record if len(record) > 2 else None


def _entries_delta(before: List[Any], after: List[Any]) -> Dict[str, Any]:
    """条目多半只是在后面加了几条（满了从前面挤掉几条）：只记挤掉几条、加了哪些。"""
    for drop in range(len(before) + 1):
        kept = before[drop:]
        if after[:len(kept)] == kept:
            delta: Dict[str, Any] = {"append": after[len(kept):]}
            if drop:
                delta["drop"] = drop
            return delta
    return {"entries": after}


_MISSING = object()


def apply(state: Dict[str, Dict[str, Any]], record: Dict[str, Any]) -> None:
    """把一条日志记录放进 state（就地改）。认不出的记录忽略。"""
    card_id = record.get("id")
    if not isinstance(card_id, str):
        return
    if record.get("removed"):
        state.pop(card_id, None)
        return
    if "card" in record:
        state[card_id] = _plain(record["card"])
        return
    card = state.get(card_id)
    if card is None:
        return  # 差量指向一张不认识的卡：日志开头被截掉了，等下一次整卡记录
    card.update(_plain(record.get("set", {})))
    for key in record.get("unset", []):
        card.pop(key, None)
    if "entries" in record:
        card["entries"] = _plain(record["entries"])
    elif "append" in record or "drop" in record:
        entries = card.get("entries", [])[int(record.get("drop", 0)):]
        card["entries"] = entries + _plain(record.get("append", []))


def rebuild(records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    state: Dict[str, Dict[str, Any]] = {}
    for record in records:
        apply(state, record)
    return state


def read(path: Path) -> List[Dict[str, Any]]:
    """读日志。最后一行写了一半（崩溃）时丢掉它；中间坏掉的行跳过。"""
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return []
    records = []
    # 按行解码：崩溃可能截在一个汉字中间，那一行解不出来就当没写过，不能让整份读失败。
    for raw in data.splitlines():
        if not raw.strip():
            continue
        try:
            item = json.loads(raw.decode("utf-8"))
        except ValueError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


class TaskJournal:
    """cards.tasks.jsonl 的写入方。调用方（Cards）拿着自己的锁调用。"""

    def __init__(self, path: Path, cards: Dict[str, Dict[str, Any]], seq: int):
        self._path = Path(path)
        self._last = rebuild(read(self._path))
        # 和 cards.json 对齐：启动时改过的卡（重启标记、补进日志）、崩溃时没记上的变化，
        # 都在这里补一条，之后日志重建出来的就是现在的样子。
        for card_id, card in cards.items():
            self.record(card, seq)
        for card_id in [key for key in self._last if key not in cards]:
            self.remove(card_id, seq)

    def record(self, card: Dict[str, Any], seq: Optional[int] = None) -> None:
        entry = diff(card.get("seq", 0) if seq is None else seq, self._last.get(card["id"]), card)
        if entry is None:
            return
        self._append(entry)
        apply(self._last, entry)

    def remove(self, card_id: str, seq: int) -> None:
        if card_id not in self._last:
            return
        entry = {"seq": seq, "id": card_id, "removed": True}
        self._append(entry)
        apply(self._last, entry)

    def _append(self, entry: Dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
