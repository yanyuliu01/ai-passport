"""World：从任务日志确定性地重建任务的当前状态（见 docs/development/runtime-refactor-design 的 R3）。

统一消息日志（cards.log.jsonl）只记消息和进展；任务本身的状态以前只在 cards.json 里。
这里给任务补一条只追加的日志 cards.tasks.jsonl：每次卡变了，只记和上一次相比变了什么
（差量）；卡被清掉时记一条 removed。rebuild() 是纯函数：同样的日志永远重建出同样的状态，
测试可以用固定种子随机操作、在任意位置截断日志来模拟崩溃。

R3.2 起 cards.json 只是定期快照：每条日志记录有递增的编号 n，快照里记着它已经包含到哪一条
（"journal": n）。启动时读快照、只重放 n 之后的记录（replay），就是崩溃前的样子；快照每攒
SNAPSHOT_EVERY 条才整份重写一次，写稳（fsync）之后把已经包含进去的记录从日志里压缩掉。
别的进程要读现在的卡，用 current()，不要直接读 cards.json（它可能落后几十条）。

崩溃落在任何一步都恢复得出来：日志写了快照没写——重放补上；快照写了一半——那是临时文件，
原来的快照还在，日志也还在；快照写了没压缩——编号不大于快照的记录跳过；压缩写了一半——
同样是临时文件。R3.1 写的快照没有 "journal"：那时 cards.json 是权威，不重放，日志按先后编号。
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

JOURNAL_SUFFIX = ".tasks.jsonl"
# 攒这么多条日志才整份重写一次快照（cards.json 有几百 KB，不能每次变化都重写）。
SNAPSHOT_EVERY = 64


def _plain(value: Any) -> Any:
    """深拷贝并规整成 JSON 能表示的样子：比较和重放都在这个形态上做。"""
    return json.loads(json.dumps(value, ensure_ascii=False))


def diff(seq: int, previous: Optional[Dict[str, Any]], card: Dict[str, Any],
         key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """card 相比 previous 变了什么；没变返回 None。previous 为 None 时记整张卡（整条）。
    key 是它在状态里的键，默认是它的 id（消息记录用带前缀的键，见 ledger.py）。"""
    card = _plain(card)
    record: Dict[str, Any] = {"seq": seq, "id": card["id"] if key is None else key}
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
    """把一条日志记录放进 state（就地改）。认不出的记录忽略。batch 是一起写下的几条：
    同在一行里，要么都在、要么都不在。"""
    if isinstance(record.get("batch"), list):
        for item in record["batch"]:
            if isinstance(item, dict):
                apply(state, item)
        return
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


def _number(record: Dict[str, Any], index: int) -> Optional[int]:
    n = record.get("n", index)  # R3.1 写的记录没有 n：按在文件里的先后编号
    return n if isinstance(n, int) and not isinstance(n, bool) else None


def journal_of(snapshot_path: Path) -> Path:
    snapshot_path = Path(snapshot_path)
    return snapshot_path.with_name(snapshot_path.stem + JOURNAL_SUFFIX)


def replay(snapshot: Any, records: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], int, int]:
    """快照 + 它之后的日志 → (现在的卡, 日志编号到了哪, 见过的最大卡号)。纯函数。"""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    cards = snapshot.get("cards") if isinstance(snapshot.get("cards"), list) else []
    state = {card["id"]: _plain(card) for card in cards
             if isinstance(card, dict) and isinstance(card.get("id"), str)}
    return replay_onto(state, snapshot.get("journal"), records)


def replay_onto(state: Dict[str, Dict[str, Any]], applied: Any,
                records: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], int, int]:
    """在快照的状态上重放编号大于 applied 的记录（就地改 state）。applied 不是整数：
    快照是 R3.2 之前写的、它就是权威，不重放，只数一数日志编号到了哪。"""
    legacy = not isinstance(applied, int) or isinstance(applied, bool)
    applied = 0 if legacy else applied
    last, highest = applied, 0
    for index, record in enumerate(records, 1):
        n = _number(record, index)
        if n is None:
            continue
        if n > applied and not legacy:
            apply(state, record)
        last = max(last, n)
        for item in record.get("batch") if isinstance(record.get("batch"), list) else [record]:
            card_id = item.get("id") if isinstance(item, dict) else None
            if isinstance(card_id, str) and card_id[1:].isdigit():
                highest = max(highest, int(card_id[1:]))
    return state, last, highest


def current(snapshot_path: Path) -> List[Dict[str, Any]]:
    """只读：现在的卡（快照 + 重放）。给不在 Runtime 进程里的读者用；不改任何文件。"""
    try:
        raw = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    state, _last, _highest = replay(raw, read(journal_of(snapshot_path)))
    return list(state.values())


def durable_write(path: Path, data: Any) -> None:
    """写稳再换：临时文件 fsync 后替换，再 fsync 目录。压缩日志之前快照必须已经落稳。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False) + "\n"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))
    try:
        folder = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return  # 有的系统不能打开目录（Windows）：替换本身已经是原子的
    try:
        os.fsync(folder)
    except OSError:
        pass
    finally:
        os.close(folder)


class Journal:
    """只追加日志的写入方（任务日志 cards.tasks.jsonl、消息日志 messages.jsonl）。
    调用方拿着自己的锁调用。

    base 是快照加重放得到的状态、n 是重放到的编号：日志里的差量都相对它算。"""

    def __init__(self, path: Path, base: Dict[str, Dict[str, Any]], n: int,
                 cards: Dict[str, Dict[str, Any]], seq: int):
        self._path = Path(path)
        self._last = _plain(base)
        self.n = n
        self.pending = 0  # 快照之后又记了几条
        self.sync(cards, seq)

    def sync(self, cards: Dict[str, Dict[str, Any]], seq: int) -> None:
        """让日志的状态等于 cards：没经 record 记上的改动（启动时的重启标记、清理）补一条差量，
        已经不在的卡记 removed。写快照之前调用，快照就和日志编号对得上。"""
        entries = [entry for entry in (diff(seq, self._last.get(key), card, key)
                                       for key, card in cards.items()) if entry is not None]
        entries += [{"seq": seq, "id": key, "removed": True} for key in self._last if key not in cards]
        if not entries:
            return
        # 一次对齐的几条写在同一行：断电时不会只剩一半（比如消息在、它的去重项不在）。
        self._append(entries[0] if len(entries) == 1 else {"seq": seq, "batch": entries})
        for entry in entries:
            apply(self._last, entry)

    def record(self, card: Dict[str, Any], seq: Optional[int] = None, key: Optional[str] = None) -> None:
        key = card["id"] if key is None else key
        entry = diff(card.get("seq", 0) if seq is None else seq, self._last.get(key), card, key)
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

    def compact(self, applied: int) -> None:
        """快照已经落稳、包含到 applied：日志只留它之后的记录（一般是一条不剩）。"""
        kept = [record for index, record in enumerate(read(self._path), 1)
                if (_number(record, index) or 0) > applied]
        durable_write(self._path, "".join(json.dumps(record, ensure_ascii=False) + "\n"
                                          for record in kept))
        self.pending = len(kept)

    def _append(self, entry: Dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        entry = dict(entry, n=self.n + 1)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        self.n += 1
        self.pending += 1

