"""Bounded, best-effort turn timing without user content or transport credentials.

Each record uses one process-local monotonic clock and an opaque client UUID.
The Android client records its own elapsed times; these clocks must not be
subtracted across machines. Disk I/O runs on one bounded background queue.
"""

import contextlib
import contextvars
import json
import os
import queue
import re
import stat
import threading
import time
import uuid
from pathlib import Path

_UUID = re.compile(r"^[0-9a-f]{32}$")
_PHASES = frozenset({"runtime_received", "runtime_accepted", "runtime_started",
    "runtime_partial", "runtime_processing_end", "model_start", "model_headers",
    "model_first_event", "model_first_text", "model_end"})
_PROCESS = uuid.uuid4().hex
_LIMIT = 512 * 1024
_QUEUE = queue.Queue(maxsize=1024)
_START_LOCK = threading.Lock()
_STARTED = False
_ACTIVE = contextvars.ContextVar("xiaoyu_turn_timing", default=None)


def _append(path, line):
    """Run only on the diagnostic writer thread, never on the request path."""
    directory = path.parent
    # A removed Runtime state tree must never be recreated by a late log event.
    if not directory.parent.is_dir() or directory.parent.is_symlink():
        return
    directory.mkdir(mode=0o700, exist_ok=True)
    # A directory must not redirect diagnostic writes outside the state tree.
    if directory.is_symlink() or not directory.is_dir():
        return
    try:
        old = path.lstat()
        if not stat.S_ISREG(old.st_mode):
            return
        if old.st_size + len(line) > _LIMIT:
            backup = path.with_suffix(path.suffix + ".1")
            os.replace(path, backup)
    except FileNotFoundError:
        pass
    if os.name == "nt":
        # Windows needs final-handle and reparse-point verification; it does not
        # provide the POSIX O_NOFOLLOW guarantee used below.
        from .windows_files import open_rw_regular
        with open_rw_regular(path, _LIMIT) as stream:
            stream.seek(0, os.SEEK_END)
            stream.write(line)
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return
        os.fchmod(fd, 0o600)
        remaining = memoryview(line)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                break
            remaining = remaining[written:]
    finally:
        os.close(fd)


def _worker():
    while True:
        path, line = _QUEUE.get()
        try:
            _append(path, line)
        except (OSError, ValueError):
            # Diagnostics must never make a conversation fail or block.
            pass
        finally:
            _QUEUE.task_done()


def _submit(path, record):
    global _STARTED
    if not _STARTED:
        with _START_LOCK:
            if not _STARTED:
                threading.Thread(target=_worker, name="xiaoyu-timing", daemon=True).start()
                _STARTED = True
    try:
        _QUEUE.put_nowait((path, (json.dumps(record, separators=(",", ":")) + "\n").encode("ascii")))
    except queue.Full:
        pass


class TurnTiming:
    """A sink for one Runtime state directory; invalid IDs are not recorded."""

    def __init__(self, state_dir):
        self.path = Path(state_dir) / "diagnostics" / "turn-timing.jsonl"

    def mark(self, client_id, request_id, phase, attempt=None):
        if not isinstance(client_id, str) or not _UUID.fullmatch(client_id) or phase not in _PHASES:
            return
        record = {"v": 1, "process": _PROCESS, "client_id": client_id,
                  "phase": phase, "monotonic_ns": time.monotonic_ns()}
        if isinstance(request_id, str) and _UUID.fullmatch(request_id):
            record["request_id"] = request_id
        if isinstance(attempt, int) and not isinstance(attempt, bool) and 0 < attempt <= 128:
            record["attempt"] = attempt
        try:
            _submit(self.path, record)
        except Exception:
            # Thread creation or diagnostic allocation must not break the turn.
            pass


@contextlib.contextmanager
def turn(sink, client_id, request_id):
    token = _ACTIVE.set((sink, client_id, request_id, set()))
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def phase(name, attempt=None, once=False):
    active = _ACTIVE.get()
    if active is None:
        return
    sink, client_id, request_id, seen = active
    if once and name in seen:
        return
    if once:
        seen.add(name)
    sink.mark(client_id, request_id, name, attempt)
