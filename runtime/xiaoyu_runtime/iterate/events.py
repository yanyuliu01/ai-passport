"""统一采集：所有客户端报来的使用事件（见 docs/development/auto-iteration 的"统一采集"）。

  事件格式      {"id", "at"(毫秒), "type", "type_version"?, "client": {"kind","id","version","platform"},
                 "session", "fields": {...}}
                旧版安卓 App 的扁平格式（字段直接放在外层、带 app_version）也认，按 client.kind=android 处理。
  事件类型表    每种事件允许哪些字段、多长、隐私级别、保留几天。只留登记过的字段，别的丢掉。
                内置几种；插件文件里写 EVENT_TYPES = [EventType(...)] 可以加新的（iterate.plugin_dirs）。
  存储          state/iterate/app-events/<收到的日期>.jsonl，只追加。每条记着 origin：最先收到它的那台
                Runtime。别的电脑经 mesh 同步来的也写在这里，按 id 去重，所以每台都有全部事件。
"""

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

VERSION = 1
CLIENT_KINDS = ("android", "desktop", "device", "runtime", "agent")
PRIVACY = ("usage", "diagnostic", "content")
MAX_EVENTS_PER_POST = 200
ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
TYPE_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
MAX_FRAMES = 8


class EventError(ValueError):
    pass


@dataclass
class EventType:
    name: str
    # 字段名 → 规则：int 是整数，list 是字符串列表（最多 MAX_FRAMES 项），数字是字符串最长多少字
    fields: Dict[str, Any]
    privacy: str = "usage"
    retention_days: int = 30
    version: int = 1
    description: str = ""

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "version": self.version, "privacy": self.privacy,
                "retention_days": self.retention_days,
                "fields": {key: ("int" if rule is int else "list" if rule is list else "text:%d" % rule)
                           for key, rule in self.fields.items()},
                "description": self.description}


BUILTIN = [
    EventType("screen", {"name": 40, "ms": int}, "usage", 30, description="离开一个页面：页面名、停留毫秒"),
    EventType("rage_tap", {"screen": 40, "region": 16, "count": int, "control": 48}, "usage", 30,
              description="短时间在同一处连点"),
    EventType("tap", {"screen": 40, "control": 48, "region": 16}, "usage", 30,
              description="点了哪个控件（只记 App 自己起的控件名，不记屏幕上的内容）"),
    EventType("error", {"screen": 40, "message": 120}, "diagnostic", 30, description="提示给主人的错误"),
    EventType("notice", {"screen": 40, "message": 120}, "usage", 30, description="提示给主人的普通消息（不是错误）"),
    EventType("crash", {"exception": 160, "frames": list, "thread": 40}, "diagnostic", 90,
              description="未捕获的异常"),
    EventType("slow", {"metric": 40, "ms": int, "screen": 40}, "usage", 30, description="某个操作的耗时"),
    EventType("update", {"result": 40, "version": 32}, "diagnostic", 90, description="更新下载、安装的结果"),
    EventType("review", {"action": 40, "operation": 16, "result": 16, "reason": 40,
                        "conversation": 64, "mode": 16, "seconds": int, "previous_seconds": int,
                        "expires_at": int, "approval_id": 64, "card": 64, "agent": 64, "by": 32},
              "diagnostic", 90, description="快速审核的模式设置、到期恢复及审核结果，不含操作正文"),
]


class Types:
    """事件类型表。"""

    def __init__(self, extra: Iterable[EventType] = ()):
        self._types: Dict[str, EventType] = {}
        for item in list(BUILTIN) + list(extra):
            self.register(item)

    def register(self, item: EventType) -> None:
        if not isinstance(item, EventType) or not TYPE_NAME.fullmatch(item.name):
            raise EventError("事件类型名只能用小写字母、数字、下划线")
        if item.privacy not in PRIVACY:
            raise EventError("隐私级别只能是 %s" % "、".join(PRIVACY))
        self._types[item.name] = item

    def get(self, name: str) -> Optional[EventType]:
        return self._types.get(name)

    def all(self) -> List[EventType]:
        return sorted(self._types.values(), key=lambda item: item.name)

    def describe(self) -> Dict[str, Any]:
        return {"version": VERSION, "max_per_post": MAX_EVENTS_PER_POST,
                "types": [item.describe() for item in self.all()]}


def types_from_dirs(folders: Iterable[Path]) -> Types:
    """内置类型 + 插件目录里登记的类型（文件里的 EVENT_TYPES 列表）。坏文件跳过。"""
    import importlib.util
    extra: List[EventType] = []
    for folder in folders:
        for path in sorted(Path(folder).glob("*.py")) if Path(folder).is_dir() else []:
            try:
                spec = importlib.util.spec_from_file_location("xiaoyu_event_types_%s" % path.stem, path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except Exception:
                continue
            extra.extend(item for item in getattr(module, "EVENT_TYPES", []) if isinstance(item, EventType))
    return Types(extra)


def _text(value: Any, limit: int) -> Optional[str]:
    return value[:limit] if isinstance(value, str) else None


def clean(raw: Any, types: Types, default_kind: str = "android") -> Dict[str, Any]:
    """一条事件 → 存下来的样子；不合格抛 EventError。"""
    if not isinstance(raw, dict):
        raise EventError("事件应该是对象")
    kind = types.get(raw.get("type")) if isinstance(raw.get("type"), str) else None
    if kind is None:
        raise EventError("不认识的事件类型 %r" % raw.get("type"))
    event_id = raw.get("id")
    if not isinstance(event_id, str) or not ID.fullmatch(event_id):
        raise EventError("事件要有 id（字母、数字、_ -，最多 64 个）")
    at = raw.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)) or at <= 0:
        raise EventError("事件要有 at（毫秒时间戳）")
    client_raw = raw.get("client")
    if isinstance(client_raw, dict):
        client_kind = client_raw.get("kind")
        if client_kind not in CLIENT_KINDS:
            raise EventError("client.kind 只能是 %s" % "、".join(CLIENT_KINDS))
        client = {"kind": client_kind}
        for key, limit in (("id", 64), ("version", 32), ("platform", 40)):
            value = _text(client_raw.get(key), limit)
            if value:
                client[key] = value
        fields_raw = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
    else:
        # 旧版安卓 App：字段在外层，版本叫 app_version
        client = {"kind": default_kind}
        version = _text(raw.get("app_version"), 32)
        if version:
            client["version"] = version
        fields_raw = raw
    fields: Dict[str, Any] = {}
    for key, rule in kind.fields.items():
        value = fields_raw.get(key)
        if value is None:
            continue
        if rule is int:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                fields[key] = int(value)
        elif rule is list:
            if isinstance(value, list):
                fields[key] = [str(item)[:160] for item in value[:MAX_FRAMES]]
        elif isinstance(value, str):
            fields[key] = value[:rule]
    event: Dict[str, Any] = {"id": event_id, "type": kind.name, "at": float(at) / 1000.0, "client": client,
                             "fields": fields}
    session = _text(raw.get("session"), 64)
    if session:
        event["session"] = session
    return event


def _day(at: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(at))


def _read(path: Path) -> List[Dict[str, Any]]:
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


class EventStore:
    """事件库。本机收到的、mesh 同步来的都写在这里，按 id 去重。"""

    def __init__(self, state_dir: Path, types: Optional[Types] = None, origin: str = ""):
        self.folder = Path(state_dir) / "iterate" / "app-events"
        self.types = types or Types()
        self.origin = origin
        self._lock = threading.Lock()
        self._seen: Optional[Set[str]] = None

    def _known(self) -> Set[str]:
        if self._seen is None:
            seen: Set[str] = set()
            for path in sorted(self.folder.glob("*.jsonl")) if self.folder.is_dir() else []:
                seen.update(str(row.get("id")) for row in _read(path))
            self._seen = seen
        return self._seen

    def _append(self, rows: List[Dict[str, Any]], now: float) -> None:
        if not rows:
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        with (self.folder / ("%s.jsonl" % _day(now))).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n")

    def accept(self, payload: Any, received_at: Optional[float] = None,
               default_kind: str = "android") -> Dict[str, Any]:
        """客户端报来的一批。坏的单条跳过（整批重发是安全的，重复的按 id 去掉）。
        返回 {"accepted","rejected","duplicates","events"}，events 是新收下的，给 mesh 同步用。"""
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise EventError("请求体应该是 {\"events\": [...]}")
        events = payload["events"]
        if len(events) > MAX_EVENTS_PER_POST:
            raise EventError("一次最多 %d 条事件" % MAX_EVENTS_PER_POST)
        now = time.time() if received_at is None else received_at
        fresh, rejected, duplicates = [], 0, 0
        with self._lock:
            known = self._known()
            for raw in events:
                try:
                    event = clean(raw, self.types, default_kind)
                except EventError:
                    rejected += 1
                    continue
                if event["id"] in known:
                    duplicates += 1
                    continue
                event["received_at"] = round(now, 3)
                if self.origin:
                    event["origin"] = self.origin
                known.add(event["id"])
                fresh.append(event)
            self._append(fresh, now)
        return {"accepted": len(fresh), "rejected": rejected, "duplicates": duplicates, "events": fresh}

    def ingest(self, events: Iterable[Any], origin: str, received_at: Optional[float] = None) -> int:
        """别的电脑同步来的（已经清洗过）。再按类型表过一遍，按 id 去重。"""
        now = time.time() if received_at is None else received_at
        fresh = []
        with self._lock:
            known = self._known()
            for raw in events:
                if not isinstance(raw, dict) or not isinstance(raw.get("client"), dict):
                    continue
                try:
                    event = clean(dict(raw, at=float(raw.get("at", 0)) * 1000.0), self.types)
                except (EventError, TypeError, ValueError):
                    continue
                if event["id"] in known:
                    continue
                event["received_at"] = float(raw.get("received_at") or now)
                event["origin"] = str(raw.get("origin") or origin)[:64]
                known.add(event["id"])
                fresh.append(event)
            self._append(fresh, now)
        return len(fresh)

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
            rows = _read(path)
            start = done_line if path.name == done_file else 0
            events.extend(rows[start:])
            last = {"file": path.name, "line": len(rows)}
        return events, last

    def all(self) -> List[Dict[str, Any]]:
        return self.read_after(None)[0]

    def prune(self, now: Optional[float] = None) -> int:
        """按各类型的保留天数删掉过期的事件；整天都过期的文件直接删。返回删掉几条。"""
        now = time.time() if now is None else now
        removed = 0
        with self._lock:
            for path in sorted(self.folder.glob("*.jsonl")) if self.folder.is_dir() else []:
                rows = _read(path)
                kept = []
                for row in rows:
                    kind = self.types.get(str(row.get("type")))
                    days = kind.retention_days if kind else 30
                    if float(row.get("at") or 0) >= now - days * 86400:
                        kept.append(row)
                if len(kept) == len(rows):
                    continue
                removed += len(rows) - len(kept)
                if kept:
                    temporary = path.with_name(path.name + ".tmp")
                    temporary.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in kept),
                                         encoding="utf-8")
                    os.replace(temporary, path)
                else:
                    path.unlink()
            self._seen = None
        return removed

    def delete(self, kind: Optional[str] = None, before: Optional[float] = None) -> int:
        """主人要删：按类型和/或时间。"""
        removed = 0
        with self._lock:
            for path in sorted(self.folder.glob("*.jsonl")) if self.folder.is_dir() else []:
                rows = _read(path)
                kept = [row for row in rows
                        if not ((kind is None or row.get("type") == kind)
                                and (before is None or float(row.get("at") or 0) < before))]
                if len(kept) == len(rows):
                    continue
                removed += len(rows) - len(kept)
                if kept:
                    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in kept),
                                    encoding="utf-8")
                else:
                    path.unlink()
            self._seen = None
        return removed


def flat(event: Dict[str, Any]) -> Dict[str, Any]:
    """给检测器用的扁平样子：fields 摊开，再带上客户端信息。"""
    if "fields" not in event:  # 0.20 之前的扁平格式
        return {key: value for key, value in event.items() if key not in ("id", "type", "at")}
    data = dict(event.get("fields") or {})
    client = event.get("client") or {}
    data.update(client_kind=client.get("kind", ""), app_version=client.get("version", ""))
    for key in ("session", "origin", "received_at"):
        if key in event:
            data[key] = event[key]
    return data
