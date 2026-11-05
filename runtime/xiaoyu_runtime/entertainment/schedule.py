"""A once-per-day local claim for optional, designated-host exploration."""

import sqlite3
import time
from pathlib import Path


def request_test(path, now=None):
    """Request one local, user-initiated exploration opportunity."""
    now = time.time() if now is None else now
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sqlite3.connect(str(path), timeout=1) as db:
        db.execute('CREATE TABLE IF NOT EXISTS test_request (id INTEGER PRIMARY KEY CHECK(id=1), requested REAL NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS test_result (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL)')
        db.execute('INSERT OR REPLACE INTO test_result(id,state) VALUES(1,?)', ('requested',))
        db.execute('INSERT OR REPLACE INTO test_request(id,requested) VALUES(1,?)', (now,))


def claim_test(path, now=None):
    now = time.time() if now is None else now
    path = Path(path)
    if not path.exists():
        return False
    with sqlite3.connect(str(path), timeout=1, isolation_level=None) as db:
        db.execute('CREATE TABLE IF NOT EXISTS test_request (id INTEGER PRIMARY KEY CHECK(id=1), requested REAL NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS test_result (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT requested FROM test_request WHERE id=1').fetchone()
        if row:
            db.execute('DELETE FROM test_request WHERE id=1')
            db.execute('INSERT OR REPLACE INTO test_result(id,state) VALUES(1,?)',
                       ('claimed' if 0 <= now - row[0] <= 3600 else 'expired',))
        db.execute('COMMIT')
        return bool(row and 0 <= now - row[0] <= 3600)


def record_test_state(path, state):
    """Persist a non-sensitive diagnostic for the explicit local test only."""
    if state not in ('voice_unavailable', 'model_error', 'ownership_changed', 'no_browse',
                     'deferred', 'plan_rejected', 'launch_failed', 'launched'):
        raise ValueError('invalid test state')
    with sqlite3.connect(str(path), timeout=1) as db:
        db.execute('UPDATE test_result SET state=? WHERE id=1', (state,))


def status(path, now=None):
    """Describe today's autonomous opportunities without changing the schedule."""
    now = time.time() if now is None else now
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    path = Path(path)
    row = None
    if path.exists():
        try:
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1) as db:
                table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deferred'").fetchone()
                if table:
                    row = db.execute("SELECT due, count FROM deferred WHERE day=?", (day,)).fetchone()
        except (OSError, sqlite3.Error):
            return {"available": False, "reason": "自主探索安排暂不可读取"}
    due, count = row or (0, 0)
    if not isinstance(due, (int, float)) or not isinstance(count, int) or count < 0:
        return {"available": False, "reason": "自主探索安排无效"}
    return {"available": True, "window_local": "10:00-21:00", "defer_limit": 3,
            "deferrals_used": count, "deferrals_remaining": max(0, 3 - count),
            "defer_minutes_range": [10, 180],
            "scheduled_in_seconds": max(0, int(due - now)) if due else None}


def claim(path, now=None):
    now = time.time() if now is None else now
    local = time.localtime(now)
    if not 10 <= local.tm_hour < 21:
        return False
    day = time.strftime("%Y-%m-%d", local)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sqlite3.connect(str(path), timeout=1, isolation_level=None) as db:
        db.execute("CREATE TABLE IF NOT EXISTS discovery (day TEXT PRIMARY KEY)")
        db.execute("CREATE TABLE IF NOT EXISTS deferred (day TEXT PRIMARY KEY, due REAL NOT NULL, count INTEGER NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        pending = db.execute("SELECT due FROM deferred WHERE day=?", (day,)).fetchone()
        if pending and pending[0] > 0:
            if now < pending[0]:
                db.execute("ROLLBACK")
                return False
            db.execute("UPDATE deferred SET due=0 WHERE day=?", (day,))
            db.execute("COMMIT")
            return True
        cursor = db.execute("INSERT OR IGNORE INTO discovery(day) VALUES (?)", (day,))
        db.execute("COMMIT")
        return cursor.rowcount == 1


def defer(path, minutes, now=None):
    """Persist a model-selected later opportunity, at most three times per day."""
    now = time.time() if now is None else now
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 10 <= minutes <= 180:
        return False
    due = now + minutes * 60
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    local_due = time.localtime(due)
    if time.strftime("%Y-%m-%d", local_due) != day or not 10 <= local_due.tm_hour < 21:
        return False
    path = Path(path)
    with sqlite3.connect(str(path), timeout=1, isolation_level=None) as db:
        db.execute("CREATE TABLE IF NOT EXISTS deferred (day TEXT PRIMARY KEY, due REAL NOT NULL, count INTEGER NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute("SELECT count FROM deferred WHERE day=?", (day,)).fetchone()
        count = prior[0] if prior else 0
        if count >= 3:
            db.execute("ROLLBACK")
            return False
        db.execute("INSERT OR REPLACE INTO deferred(day,due,count) VALUES (?,?,?)", (day, due, count + 1))
        db.execute("COMMIT")
        return True
