"""Read-only local conversation observer; never starts or resumes an agent.

Transcript formats are adapters, not public protocols. Unknown events cannot
prove success or an approval wait. Optional lifecycle hooks supply exact waits.
Only project/session metadata and tool names leave this adapter, not prompts,
replies, arguments, tool output, credentials, or raw transcripts.
"""

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .cards import Cards

POLL_SECONDS = 2.0
DISCOVERY_SECONDS = 10.0
RECENT_SECONDS = 86400
STALE_SECONDS = 300
MAX_SESSIONS = 32
MAX_READ_BYTES = 2 * 1024 * 1024
MAX_LINE_BYTES = 512 * 1024
CONVERSATION = "__desktop__"
STATES = {"running": "working", "waiting": "waiting", "idle": "done",
          "interrupted": "cancelled", "failed": "failed", "unknown": "unknown"}
LABELS = {"running": "正在运行", "waiting": "等待电脑端确认", "idle": "本轮结束",
          "interrupted": "已中断", "failed": "本轮失败", "unknown": "状态未知"}


def _text(value: Any, limit: int = 80) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _at(value: Any, fallback: float) -> float:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        return min(stamp, fallback) if stamp > 0 else fallback
    except (AttributeError, ValueError, TypeError, OverflowError):
        return fallback


@dataclass
class Session:
    agent: str
    session_id: str
    cwd: str = ""
    status: str = "unknown"
    observed_at: float = 0.0
    started_at: Optional[float] = None
    model: str = ""
    tool: str = ""
    turn_id: str = ""
    card: Optional[str] = None
    published: Tuple[Any, ...] = ()
    hook_at: float = 0.0
    skip: bool = False
    unavailable: bool = False

    def transition(self, status: str, at: float, turn: str = "") -> None:
        if at < self.observed_at:
            return
        if status == "running" and (self.status not in ("running", "waiting")
                                    or (turn and turn != self.turn_id)):
            self.started_at = at
            self.tool = ""
        self.status, self.observed_at = status, at
        if turn:
            self.turn_id = turn


@dataclass
class Cursor:
    path: Path
    agent: str
    offset: int = 0
    identity: Tuple[int, int] = (0, 0)
    session: Optional[Session] = None
    dropping: bool = False


class DesktopSync:
    def __init__(self, cards: Cards, state_dir: Path, *, home: Optional[Path] = None,
                 codex_dir: Optional[Path] = None, claude_dir: Optional[Path] = None,
                 projects: Optional[List[Path]] = None,
                 owned: Callable[[], Set[str]] = lambda: set(),
                 clock: Callable[[], float] = time.time):
        home = home or Path.home()
        self.cards, self.state_dir, self.clock, self.owned = cards, Path(state_dir), clock, owned
        self.roots = [("codex", (codex_dir or home / ".codex") / "sessions"),
                      ("claude", (claude_dir or home / ".claude") / "projects")]
        self.projects = {str(path.expanduser().resolve()) for path in projects or []}
        self.sessions: Dict[Tuple[str, str], Session] = {}
        self.cursors: Dict[Path, Cursor] = {}
        self.hooks: Dict[Path, int] = {}
        self.last_discovery = float("-inf")
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        # Reuse the same cards after a Runtime restart; never bind these sessions
        # to the runtime's conversation/session ownership or model context.
        for card in cards.recent(None, 1000):
            if card.get("monitoring_only") and card.get("session_id"):
                session = Session(card["agent"], card["session_id"], card=card["id"])
                self.sessions[(session.agent, session.session_id)] = session

    def start(self) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, daemon=True, name="desktop-sync")
            self.thread.start()

    def close(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        for session in self.sessions.values():
            card = self.cards.get(session.card) if session.card else None
            if card and card["state"] in ("working", "waiting"):
                self._publish(session, self.clock(), unavailable=True)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.poll()
            except Exception:
                # A changed/unreadable transcript cannot kill synchronization or
                # leave a stale active badge behind. No private exception data.
                for session in list(self.sessions.values()):
                    self._publish(session, self.clock(), unavailable=True)
            self.stop_event.wait(POLL_SECONDS)

    def _session(self, agent: str, session_id: str) -> Session:
        key = (agent, session_id)
        if key not in self.sessions:
            self.sessions[key] = Session(agent, session_id)
        return self.sessions[key]

    def _discover(self, now: float) -> None:
        candidates = []
        for agent, root in self.roots:
            try:
                paths = root.rglob("rollout-*.jsonl" if agent == "codex" else "*.jsonl")
                for path in paths:
                    if "subagents" in path.parts:
                        continue
                    try:
                        mtime = path.stat().st_mtime
                    except OSError:
                        continue
                    if mtime >= now - RECENT_SECONDS:
                        candidates.append((mtime, agent, path))
            except OSError:
                continue
        candidates.sort(key=lambda item: item[0], reverse=True)
        selected = {path for _, _, path in candidates[:MAX_SESSIONS]}
        self.cursors = {path: cursor for path, cursor in self.cursors.items() if path in selected}
        for _, agent, path in candidates[:MAX_SESSIONS]:
            self.cursors.setdefault(path, Cursor(path, agent))
        self.last_discovery = now

    def _event(self, cursor: Cursor, event: Dict[str, Any], fallback: float) -> None:
        at = _at(event.get("timestamp"), fallback)
        if cursor.agent == "codex":
            payload = event.get("payload")
            if not isinstance(payload, dict):
                return
            kind = event.get("type")
            if kind == "session_meta":
                sid = _text(payload.get("id"), 128)
                if not sid:
                    return
                cursor.session = self._session("codex", sid)
                session = cursor.session
                source = payload.get("source")
                session.skip = (isinstance(source, dict) and "subagent" in source
                                or bool(payload.get("parent_thread_id"))
                                or payload.get("originator") == "xiaoyou-runtime")
                session.cwd = _text(payload.get("cwd"), 2048)
                return
            session = cursor.session
            if session is None or at < session.hook_at:
                return
            if kind == "turn_context":
                session.cwd = _text(payload.get("cwd"), 2048) or session.cwd
                session.model = _text(payload.get("model"), 64) or session.model
            elif kind == "event_msg":
                event_type = payload.get("type")
                status = {"task_started": "running", "task_complete": "idle",
                          "turn_aborted": "interrupted"}.get(event_type) \
                    if isinstance(event_type, str) else None
                if status:
                    session.transition(status, at, _text(payload.get("turn_id"), 128))
            elif kind == "response_item" and payload.get("type") in ("function_call", "custom_tool_call"):
                session.tool = _text(payload.get("name"), 48)
                session.transition("running", at)
        else:
            if event.get("isSidechain") is True:
                return
            sid = _text(event.get("sessionId"), 128)
            if sid:
                cursor.session = self._session("claude", sid)
            session = cursor.session
            if session is None or at < session.hook_at:
                return
            session.cwd = _text(event.get("cwd"), 2048) or session.cwd
            message = event.get("message")
            kind = event.get("type")
            if kind == "system" and event.get("subtype") == "turn_duration":
                session.transition("idle", at)
            elif isinstance(message, dict) and kind in ("user", "assistant"):
                content = message.get("content")
                blocks = content if isinstance(content, list) else []
                if kind == "user":
                    interrupted = content == "[Request interrupted by user]" or any(
                        isinstance(block, dict) and block.get("type") == "text"
                        and block.get("text") == "[Request interrupted by user]" for block in blocks)
                    # Ignore synthetic metadata/context user messages, but a tool
                    # result is activity in the already-running turn.
                    if interrupted:
                        session.transition("interrupted", at)
                    elif not event.get("isMeta"):
                        session.transition("running", at)
                else:
                    session.model = _text(message.get("model"), 64) or session.model
                    tool = next((_text(block.get("name"), 48) for block in blocks
                                 if isinstance(block, dict) and block.get("type") == "tool_use"), "")
                    if tool:
                        session.tool = tool
                    reason = message.get("stop_reason")
                    session.transition("idle" if reason in ("end_turn", "stop_sequence")
                                       else "running", at)

    def _read(self, cursor: Cursor) -> None:
        stat = cursor.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        fresh = cursor.identity != identity or stat.st_size < cursor.offset
        with cursor.path.open("rb") as handle:
            if fresh:
                cursor.offset, cursor.session, cursor.dropping = 0, None, False
                cursor.identity = identity
                # Session metadata sits at the beginning even in huge rollouts.
                for _ in range(8):
                    line = handle.readline(MAX_LINE_BYTES + 1)
                    if len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
                        break
                    self._line(cursor, line, stat.st_mtime)
                if stat.st_size > MAX_READ_BYTES:
                    handle.seek(stat.st_size - MAX_READ_BYTES)
                    handle.readline(MAX_LINE_BYTES + 1)  # discard partial first record
                else:
                    handle.seek(0)
                cursor.offset = handle.tell()
            handle.seek(cursor.offset)
            budget = MAX_READ_BYTES
            while budget > 0:
                begin = handle.tell()
                line = handle.readline(min(MAX_LINE_BYTES + 1, budget))
                if not line:
                    break
                budget -= len(line)
                if not line.endswith(b"\n"):
                    if len(line) > MAX_LINE_BYTES or cursor.dropping:
                        cursor.dropping = True
                        cursor.offset = handle.tell()
                        continue
                    handle.seek(begin)  # JSON being appended: retry once complete
                    break
                cursor.offset = handle.tell()
                if cursor.dropping:
                    cursor.dropping = False
                    continue
                if len(line) > MAX_LINE_BYTES:
                    continue
                self._line(cursor, line, stat.st_mtime)
        if cursor.session:
            cursor.session.unavailable = False

    def _line(self, cursor: Cursor, line: bytes, fallback: float) -> None:
        try:
            event = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(event, dict):
            self._event(cursor, event, fallback)

    def _read_hooks(self, now: float) -> None:
        root = self.state_dir / "desktop-events"
        try:
            candidates = []
            for path in root.glob("*.json"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_mtime >= now - RECENT_SECONDS:
                    candidates.append((stat.st_mtime_ns, path))
            candidates.sort(reverse=True)
            paths = [path for _, path in candidates[:MAX_SESSIONS * 4]]
            self.hooks = {path: at for path, at in self.hooks.items() if path in paths}
        except OSError:
            return
        for path in paths:
            try:
                stat = path.stat()
                if self.hooks.get(path) == stat.st_mtime_ns or stat.st_size > MAX_LINE_BYTES:
                    continue
                event = json.loads(path.read_bytes())
                if not isinstance(event, dict):
                    continue
                agent, sid, status = event.get("agent"), event.get("session_id"), event.get("status")
                at = event.get("observed_at")
                if (agent not in ("codex", "claude") or not isinstance(sid, str)
                        or not 1 <= len(sid) <= 128 or status not in STATES
                        or isinstance(at, bool) or not isinstance(at, (float, int))
                        or not 0 < at <= now + 10 or at < now - RECENT_SECONDS):
                    continue
                session = self._session(agent, sid)
                if at < session.observed_at:
                    self.hooks[path] = stat.st_mtime_ns
                    continue
                session.cwd = _text(event.get("cwd"), 2048) or session.cwd
                session.model = _text(event.get("model"), 64) or session.model
                session.tool = _text(event.get("progress"), 48)
                session.transition(status, min(at, now), _text(event.get("turn_id"), 128))
                session.hook_at = max(session.hook_at, min(at, now))
                self.hooks[path] = stat.st_mtime_ns
            except (OSError, ValueError, UnicodeDecodeError, TypeError):
                continue

    def _publish(self, session: Session, now: float, unavailable: bool = False) -> None:
        if session.skip or not session.cwd or session.session_id in self.owned():
            # Runtime-owned work already has a card and must never be duplicated.
            if session.card and session.session_id in self.owned():
                card = self.cards.get(session.card)
                if card and card["state"] in ("working", "waiting", "unknown"):
                    self.cards.update(session.card, state="cancelled", desktop_status="interrupted",
                                      brief="此会话现由 Runtime 管理", stale=False)
            return
        if self.projects and str(Path(session.cwd).expanduser().resolve()) not in self.projects:
            return
        stale = session.status in ("running", "waiting", "unknown") and (
            unavailable or session.unavailable or now - session.observed_at > STALE_SECONDS)
        status = "unknown" if stale else session.status
        project = _text(Path(session.cwd).name, 80) or "project"
        key = (status, project, session.model, session.tool, session.observed_at, session.started_at, stale)
        if key == session.published and session.card and self.cards.get(session.card):
            return
        if session.card is None or self.cards.get(session.card) is None:
            title = "%s %s %s" % (session.agent, project[:10], session.session_id[-6:])
            card = self.cards.open(CONVERSATION, title, "unknown", session.agent)
            session.card = card["id"]
        label = LABELS[status]
        progress = [label]
        if session.tool and status == "running":
            progress.append(session.tool)
        self.cards.update(session.card, state=STATES[status], source="desktop", project=project,
                          session_id=session.session_id, monitoring_only=True, desktop_status=status,
                          observed_at=session.observed_at, stale=stale, model=session.model or None,
                          started_at=session.started_at, approval=None, queued=False,
                          brief="%s · %s" % (project, label), progress_lines=progress,
                          mood="busy" if status in ("running", "waiting") else "idle")
        session.published = key

    def poll(self) -> None:
        now = self.clock()
        if now - self.last_discovery >= DISCOVERY_SECONDS:
            self._discover(now)
        # Read logs before hooks; a precise waiting hook wins over same-time
        # transcript activity, and later transcript records resume normally.
        for cursor in list(self.cursors.values()):
            try:
                self._read(cursor)
            except OSError:
                if cursor.session:
                    cursor.session.unavailable = True
        self._read_hooks(now)
        # Bound adapter memory as sessions rotate, independently of card history.
        protected = {(cursor.session.agent, cursor.session.session_id)
                     for cursor in self.cursors.values() if cursor.session}
        for key, session in sorted(self.sessions.items(), key=lambda item: item[1].observed_at):
            if len(self.sessions) <= MAX_SESSIONS * 2:
                break
            if key not in protected:
                card = self.cards.get(session.card) if session.card else None
                if card and card["state"] in ("working", "waiting"):
                    self._publish(session, now, unavailable=True)
                del self.sessions[key]
        # Publish only the newest observations. Old persisted snapshots remain
        # history, but cannot keep reopening pruned cards on every poll.
        newest = sorted(self.sessions.values(), key=lambda session: session.observed_at,
                        reverse=True)[:MAX_SESSIONS]
        retained = {(session.agent, session.session_id) for session in newest}
        for key, session in self.sessions.items():
            if key not in retained and session.card:
                card = self.cards.get(session.card)
                if card and card["state"] in ("working", "waiting"):
                    self._publish(session, now, unavailable=True)
        for session in newest:
            self._publish(session, now)
