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
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..errors import EffectUncertain

MAX_OPEN = 30
REPEAT = {"day": 86400, "week": 7 * 86400}


def wake_source(item):
    return {key: item.get(key) for key in (
        "id", "title", "detail", "due", "nudged", "conversation", "every", "created")}


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

    def wakes(self):
        # A resolve tool may have closed the original reminder before its
        # result was saved. Such wakes still need their original receipt.
        with self._lock:
            return [item for item in self._read() if item.get("wake")]

    def effect_records(self):
        with self._lock:
            return [item for item in self._read() if item.get("wake") or item.get("wake_reviews")]

    def archive_wake_review(self, source, wake):
        with self._lock:
            items = self._read()
            item = next((item for item in items if item["id"] == source["id"]), None)
            if (item is None or wake_source(item) != wake_source(source) or item.get("wake") != wake
                    or (wake.get("review") or {}).get("published") is not True):
                raise EffectUncertain("提醒的核对投递进度已经改变。")
            history = item.setdefault("wake_reviews", [])
            if not isinstance(history, list) or any(value.get("id") == wake["id"] for value in history):
                raise EffectUncertain("提醒的核对归档有冲突。")
            history.append(deepcopy(wake))
            item.pop("wake")
            now = time.time()
            item["nudged"] = now
            step = REPEAT.get(item.get("every") or "")
            if step and item.get("due"):
                while item["due"] <= now:
                    item["due"] += step
                item["nudged"] = None
            try:
                self._write(items)
            except OSError:
                if not any(value["id"] == source["id"] and wake in value.get("wake_reviews", [])
                           and not value.get("wake") for value in self._read()):
                    raise

    def checkpoint_wake(self, source, before, after):
        with self._lock:
            items = self._read()
            item = next((item for item in items if item["id"] == source["id"]), None)
            if (item is None or wake_source(item) != wake_source(source)
                    or item.get("wake") != before
                    or before is None and item.get("status") != "active"):
                raise EffectUncertain("原提醒或操作领取已改变，不能继续。")
            item["wake"] = deepcopy(after)
            try:
                self._write(items)
            except OSError:
                if not any(value["id"] == source["id"] and value.get("wake") == after for value in self._read()):
                    raise

    def mark_nudged(self, item_id: str, now: Optional[float] = None, *, expected_wake=None) -> None:
        """为它叫过她了。重复的（每天、每周）把到点时间挪到下一次，不再算到点。"""
        now = now if now is not None else time.time()
        with self._lock:
            items = self._read()
            for item in items:
                if item["id"] == item_id:
                    if expected_wake is not None and item.get("wake") != expected_wake:
                        raise EffectUncertain("提醒的投递进度已经改变。")
                    item.pop("wake", None)
                    item["nudged"] = now
                    step = REPEAT.get(item.get("every") or "")
                    if step and item.get("due"):
                        while item["due"] <= now:
                            item["due"] += step
                        item["nudged"] = None
            self._write(items)

    def claim_wake(self, item_id: str, event_id: str) -> None:
        """Save uncertainty before a timer can mutate memory or commitments.

        A read-only model failure may retry. Once a tool could have changed
        state, an unconfirmed wake must be reconciled instead of replayed.
        """
        with self._lock:
            items = self._read()
            item = next((value for value in items if value["id"] == item_id and value["status"] == "active"), None)
            if item is None or item.get("wake"):
                raise RuntimeError("Reminder wake is no longer available")
            item["wake"] = {"id": event_id, "state": "unknown", "at": time.time()}
            # This boundary must be durable before a tool is called.
            self._write(items, durable=True)

    def _read(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [item for item in data if isinstance(item, dict) and "id" in item] if isinstance(data, list) else []

    def _write(self, items: List[Dict[str, Any]], durable: bool = True) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix=".promises-", dir=str(self._path.parent))
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            recent = {item["id"] for item in items[-200:]}
            json.dump([item for item in items if item["id"] in recent or item.get("wake") or item.get("wake_reviews")],
                      stream, ensure_ascii=False, indent=1)
            if durable:
                stream.flush()
                os.fsync(stream.fileno())
        if os.name == "posix":
            os.chmod(name, 0o600)
        os.replace(name, self._path)
        if durable and os.name == "posix":
            folder = os.open(str(self._path.parent), os.O_RDONLY)
            try:
                os.fsync(folder)
            finally:
                os.close(folder)


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
        if item.get("wake"):
            lines.append("  上次提醒中的工具操作结果尚未确认；先核对记录，不能重新执行。")
    return lines
