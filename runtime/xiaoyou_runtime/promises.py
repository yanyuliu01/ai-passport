"""小幽答应主人、还没办完的事（不是交给帮手的任务）：“晚点提醒我”“等 Mac 上线再发”这类。

参考 Muse 的 tracked_item：有标题、现在的状态、可选的到点时间；状态只由证据推动（她调
resolve 才算办完），活动本身不改状态。存在 state_dir/memory/promises.json，每一轮放进她的
提示；到点的由投递闸（proactive.py）叫醒她，她决定说不说。
"""

import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_OPEN = 30
REPEAT = {"day": 86400, "week": 7 * 86400}


class Promises:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()

    def open(self) -> List[Dict[str, Any]]:
        """没办完的，按到点时间（没有到点时间的排后面）。"""
        with self._lock:
            items = [item for item in self._read() if item["status"] == "active"]
        return sorted(items, key=lambda item: (item.get("due") or float("inf"), item["created"]))

    def track(self, title: str, detail: str = "", due: Optional[float] = None,
              conversation: str = "default", every: str = "") -> Dict[str, Any]:
        title = " ".join((title or "").split())[:60]
        if not title:
            raise ValueError("要写清楚答应了什么")
        now = time.time()
        item = {"id": "p" + uuid.uuid4().hex[:6], "title": title, "detail": " ".join((detail or "").split())[:300],
                "due": due, "status": "active", "created": now, "updated": now,
                "conversation": conversation, "nudged": None,
                "every": every if every in REPEAT else ""}
        with self._lock:
            items = self._read()
            items.append(item)
            active = [entry for entry in items if entry["status"] == "active"]
            while len(active) > MAX_OPEN:
                active[0]["status"] = "dropped"
                active.pop(0)
            self._write(items)
        return item

    def resolve(self, item_id: str, outcome: str = "") -> Optional[Dict[str, Any]]:
        """办完了、或者主人不要了。没有这一条返回 None。"""
        with self._lock:
            items = self._read()
            for item in items:
                if item["id"] == item_id and item["status"] == "active":
                    item.update(status="done", outcome=" ".join((outcome or "").split())[:200],
                                updated=time.time())
                    self._write(items)
                    return item
        return None

    def due(self, now: Optional[float] = None, quiet: float = 3600) -> List[Dict[str, Any]]:
        """到点了、而且最近 quiet 秒内没为它叫醒过她的。"""
        now = now if now is not None else time.time()
        return [item for item in self.open() if item.get("due") and item["due"] <= now
                and (not item.get("nudged") or now - item["nudged"] >= quiet)]

    def mark_nudged(self, item_id: str, now: Optional[float] = None) -> None:
        """为它叫过她了。重复的（每天、每周）把到点时间挪到下一次，不再算到点。"""
        now = now if now is not None else time.time()
        with self._lock:
            items = self._read()
            for item in items:
                if item["id"] == item_id:
                    item["nudged"] = now
                    step = REPEAT.get(item.get("every") or "")
                    if step and item.get("due"):
                        while item["due"] <= now:
                            item["due"] += step
                        item["nudged"] = None
            self._write(items)

    def _read(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [item for item in data if isinstance(item, dict) and "id" in item] if isinstance(data, list) else []

    def _write(self, items: List[Dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix=".promises-", dir=str(self._path.parent))
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(items[-200:], stream, ensure_ascii=False, indent=1)
        if os.name == "posix":
            os.chmod(name, 0o600)
        os.replace(name, self._path)


def when(due: Optional[float]) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(due)) if due else "没定时间"


def prompt_section(items: List[Dict[str, Any]], now: Optional[float] = None) -> List[str]:
    if not items:
        return []
    now = now if now is not None else time.time()
    lines = ["## 你答应主人、还没办完的事",
             "办完了或者主人不要了，用 resolve 关掉；新答应的事（不是交给帮手的那种）用 track 记上。"]
    for item in items:
        late = item.get("due") and item["due"] <= now
        lines.append("- %s %s（%s%s%s）%s" % (item["id"], item["title"], when(item.get("due")),
                                           {"day": "，每天", "week": "，每周"}.get(item.get("every") or "", ""),
                                           "，已经到点" if late else "",
                                           "：" + item["detail"] if item.get("detail") else ""))
    return lines
