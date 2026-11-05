"""Persistent local request budget; this does not imply platform permission."""

import math
import sqlite3
import time
from pathlib import Path

MIN_INTERVAL = 10 * 60
MAX_DAILY = 12
RESTRICTION_COOLDOWN = 24 * 60 * 60


class Limited(Exception):
    pass


class Budget:
    def __init__(self, path):
        self.path = Path(path)

    def status(self, now=None):
        """Read limits without consuming a visit or contacting the platform."""
        now = time.time() if now is None else now
        row = None
        if self.path.exists():
            try:
                with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1) as db:
                    row = db.execute("SELECT last, day, count, blocked FROM budget WHERE id=1").fetchone()
            except (OSError, sqlite3.Error):
                return {"available": False, "reason": "本地访问记录暂不可读取"}
        last, day, count, blocked = row or (0, int(now // 86400), 0, 0)
        if (not isinstance(last, (int, float)) or not isinstance(blocked, (int, float))
                or not math.isfinite(last) or not math.isfinite(blocked)
                or not isinstance(day, int) or not isinstance(count, int) or count < 0):
            return {"available": False, "reason": "本地访问记录无效"}
        today = int(now // 86400)
        used = count if today == day else 0
        next_daily = (today + 1) * 86400 if used >= MAX_DAILY else now
        ready_at = max(now, last + MIN_INTERVAL if last else now, blocked, next_daily)
        return {"available": True, "daily_limit": MAX_DAILY, "used_today": used,
                "remaining_today": max(0, MAX_DAILY - used), "day_basis": "UTC",
                "minimum_interval_seconds": MIN_INTERVAL,
                "wait_seconds": max(0, math.ceil(ready_at - now)),
                "platform_restricted": now < blocked}

    def claim(self, now=None):
        now = time.time() if now is None else now
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with sqlite3.connect(str(self.path), timeout=5, isolation_level=None) as db:
            db.execute("CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY, last REAL NOT NULL, day INTEGER NOT NULL, count INTEGER NOT NULL, blocked REAL NOT NULL)")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT last, day, count, blocked FROM budget WHERE id=1").fetchone()
            last, day, count, blocked = row or (0, int(now // 86400), 0, 0)
            if now < blocked or (last and now - last < MIN_INTERVAL):
                db.execute("ROLLBACK")
                raise Limited("访问处于冷却期，请稍后再试")
            today = int(now // 86400)
            count = count if today == day else 0
            if count >= MAX_DAILY:
                db.execute("ROLLBACK")
                raise Limited("今天的访问额度已用完")
            db.execute("INSERT OR REPLACE INTO budget (id,last,day,count,blocked) VALUES (1,?,?,?,?)",
                       (now, today, count + 1, blocked))
            db.execute("COMMIT")

    def restricted(self, now=None):
        now = time.time() if now is None else now
        with sqlite3.connect(str(self.path), timeout=5) as db:
            db.execute("UPDATE budget SET blocked=MAX(blocked, ?) WHERE id=1", (now + RESTRICTION_COOLDOWN,))
