"""自动迭代的数据，都在 state/iterate/ 下：

  app-events/<日期>.jsonl   手机报来的使用事件（POST /v1/app/events），只追加
  signals/<日期>.jsonl      各 source 插件产生的信号，只追加；超过保留天数整天删掉
  cursors.json              每个 source 读到哪了
  issues.json               问题簿：fingerprint → Issue，跨轮累计
  runs/<编号>/              每一轮：run.json、issues.json、plan.json、report.md
  state.json                上一轮什么时候跑的、之后新来了多少信号
  lock                      同一时间只跑一轮
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .objects import Issue, Signal

EVENT_TYPES = ("screen", "rage_tap", "error", "crash", "slow", "update")
MAX_EVENTS_PER_POST = 200
EVENT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
# 每种事件留哪些字段、各自多长；别的字段丢掉（不收正文、输入内容）。
EVENT_FIELDS: Dict[str, Dict[str, Any]] = {
    "screen": {"name": 40, "ms": int},
    "rage_tap": {"screen": 40, "region": 16, "count": int},
    "error": {"screen": 40, "message": 120},
    "crash": {"exception": 160, "frames": list, "thread": 40},
    "slow": {"metric": 40, "ms": int, "screen": 40},
    "update": {"result": 40, "version": 32},
}
MAX_FRAMES = 8
LOCK_STALE_SECONDS = 2 * 3600


def _day(at: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(at))


def _append(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    if not lines:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return len(lines)


def _read_lines(path: Path) -> List[Dict[str, Any]]:
    rows = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # 写到一半断电的最后一行
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=1)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


class EventError(ValueError):
    pass


def clean_event(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise EventError("事件应该是对象")
    kind = raw.get("type")
    if kind not in EVENT_TYPES:
        raise EventError("不认识的事件类型 %r" % kind)
    event_id = raw.get("id")
    if not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id):
        raise EventError("事件要有 id（字母、数字、_ -，最多 64 个）")
    at = raw.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)) or at <= 0:
        raise EventError("事件要有 at（毫秒时间戳）")
    event: Dict[str, Any] = {"id": event_id, "type": kind, "at": float(at) / 1000.0}
    for key, limit in (("app_version", 32), ("session", 64)):
        value = raw.get(key)
        if isinstance(value, str):
            event[key] = value[:limit]
    for key, rule in EVENT_FIELDS[kind].items():
        value = raw.get(key)
        if value is None:
            continue
        if rule is int:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                event[key] = int(value)
        elif rule is list:
            if isinstance(value, list):
                event[key] = [str(item)[:160] for item in value[:MAX_FRAMES]]
        elif isinstance(value, str):
            event[key] = value[:rule]
    return event


class AppEvents:
    def __init__(self, state_dir: Path):
        self.folder = Path(state_dir) / "iterate" / "app-events"

    def accept(self, payload: Any, received_at: Optional[float] = None) -> Dict[str, int]:
        """收一批事件。坏的单条跳过，不让整批失败（手机会重发整批）。"""
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise EventError("请求体应该是 {\"events\": [...]}")
        events = payload["events"]
        if len(events) > MAX_EVENTS_PER_POST:
            raise EventError("一次最多 %d 条事件" % MAX_EVENTS_PER_POST)
        received_at = time.time() if received_at is None else received_at
        accepted, rejected = [], 0
        for raw in events:
            try:
                event = clean_event(raw)
            except EventError:
                rejected += 1
                continue
            event["received_at"] = round(received_at, 3)
            accepted.append(event)
        _append(self.folder / ("%s.jsonl" % _day(received_at)), accepted)
        return {"accepted": len(accepted), "rejected": rejected}

    def read_after(self, cursor: Optional[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """从游标往后读；游标是 {"file": 文件名, "line": 读过几行}。"""
        cursor = cursor or {}
        done_file, done_line = cursor.get("file", ""), int(cursor.get("line", 0))
        events: List[Dict[str, Any]] = []
        last = dict(cursor)
        if not self.folder.is_dir():
            return events, last
        for path in sorted(self.folder.glob("*.jsonl")):
            if path.name < done_file:
                continue
            rows = _read_lines(path)
            start = done_line if path.name == done_file else 0
            events.extend(rows[start:])
            last = {"file": path.name, "line": len(rows)}
        return events, last


class SignalStore:
    def __init__(self, state_dir: Path):
        self.folder = Path(state_dir) / "iterate" / "signals"

    def append(self, signals: List[Signal]) -> int:
        by_day: Dict[str, List[Dict[str, Any]]] = {}
        for signal in signals:
            by_day.setdefault(_day(signal.at), []).append(signal.to_dict())
        return sum(_append(self.folder / ("%s.jsonl" % day), rows) for day, rows in by_day.items())

    def since(self, at: float) -> List[Signal]:
        if not self.folder.is_dir():
            return []
        first = _day(at)
        seen, signals = set(), []
        for path in sorted(self.folder.glob("*.jsonl")):
            if path.stem < first:
                continue
            for row in _read_lines(path):
                try:
                    signal = Signal.from_dict(row)
                except (KeyError, TypeError, ValueError):
                    continue
                if signal.at >= at and signal.id not in seen:
                    seen.add(signal.id)
                    signals.append(signal)
        return signals

    def prune(self, keep_days: int, now: Optional[float] = None) -> int:
        cutoff = _day((time.time() if now is None else now) - keep_days * 86400)
        removed = 0
        for folder in (self.folder, self.folder.parent / "app-events"):
            if folder.is_dir():
                for path in folder.glob("*.jsonl"):
                    if path.stem < cutoff:
                        path.unlink()
                        removed += 1
        return removed


class IssueBook:
    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / "iterate" / "issues.json"
        self.issues: Dict[str, Issue] = {}
        for raw in read_json(self.path, {}).values():
            try:
                issue = Issue.from_dict(raw)
            except TypeError:
                continue
            self.issues[issue.fingerprint] = issue

    def merge(self, found: List[Issue]) -> List[Issue]:
        """这一轮检测到的问题并进簿子：次数、证据用新的，状态、尝试次数沿用旧的。"""
        merged = []
        for issue in found:
            old = self.issues.get(issue.fingerprint)
            if old is not None:
                issue.first_seen = min(old.first_seen, issue.first_seen)
                issue.status, issue.attempts, issue.runs = old.status, old.attempts, old.runs
                # 修好关掉的问题又出现了：重新打开。
                if old.status == "closed" and issue.last_seen > old.last_seen:
                    issue.status = "open"
            self.issues[issue.fingerprint] = issue
            merged.append(issue)
        return merged

    def save(self) -> None:
        write_json(self.path, {key: issue.to_dict() for key, issue in self.issues.items()})


class Runs:
    def __init__(self, state_dir: Path):
        self.root = Path(state_dir) / "iterate"
        self.folder = self.root / "runs"

    def create(self, now: float) -> Tuple[str, Path]:
        base = time.strftime("r%Y%m%d-%H%M%S", time.localtime(now))
        run_id, index = base, 1
        while (self.folder / run_id).exists():
            index += 1
            run_id = "%s-%d" % (base, index)
        path = self.folder / run_id
        path.mkdir(parents=True)
        return run_id, path

    def recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        if not self.folder.is_dir():
            return []
        found = []
        for path in sorted(self.folder.iterdir(), reverse=True)[:limit]:
            run = read_json(path / "run.json", None)
            if isinstance(run, dict):
                found.append(run)
        return found

    def state(self) -> Dict[str, Any]:
        return read_json(self.root / "state.json", {})

    def save_state(self, value: Dict[str, Any]) -> None:
        write_json(self.root / "state.json", value)

    def cursors(self) -> Dict[str, Any]:
        return read_json(self.root / "cursors.json", {})

    def save_cursors(self, value: Dict[str, Any]) -> None:
        write_json(self.root / "cursors.json", value)


class Busy(Exception):
    pass


class Lock:
    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / "iterate" / "lock"

    def __enter__(self) -> "Lock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                handle = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    continue
                if age < LOCK_STALE_SECONDS:
                    raise Busy("上一轮还没跑完")
                self.path.unlink()  # 上一轮崩了留下的
                continue
            os.write(handle, str(os.getpid()).encode("ascii"))
            os.close(handle)
            return self
        raise Busy("拿不到锁")

    def __exit__(self, *exc: Any) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass
