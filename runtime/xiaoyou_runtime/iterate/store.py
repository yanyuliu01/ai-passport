"""自动迭代的数据，都在 state/iterate/ 下：

  app-events/<日期>.jsonl   各客户端报来的使用事件，见 events.py
  signals/<日期>.jsonl      各 source 插件产生的信号，只追加；超过保留天数整天删掉
  cursors.json              每个 source 读到哪了
  issues.json               问题簿：fingerprint → Issue，跨轮累计
  runs/<编号>/              每一轮：run.json、issues.json、plan.json、report.md
  state.json                上一轮什么时候跑的、之后新来了多少信号
  lock                      同一时间只跑一轮
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .objects import Issue, Signal

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
        if self.folder.is_dir():
            for path in self.folder.glob("*.jsonl"):
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
