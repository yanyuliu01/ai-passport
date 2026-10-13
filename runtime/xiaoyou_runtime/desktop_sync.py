"""Read-only local conversation observer; never starts or resumes an agent.

Transcript formats are adapters, not public protocols. Unknown events cannot
prove success or an approval wait. Optional lifecycle hooks supply exact waits.
Visible user requests and assistant replies join the observed status. System
context, reasoning, tool arguments/results and raw transcripts are excluded;
recognizable credentials are redacted before content is stored or sent.
"""

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .cards import Cards
from .desktop_content import MAX_MESSAGE_CHARS, message_text, visible_text
from .desktop_titles import DesktopTitles
from .task_titles import select_title

POLL_SECONDS = 2.0
DISCOVERY_SECONDS = 10.0
RECENT_SECONDS = 86400
STALE_SECONDS = 300
MAX_SESSIONS = 32
MAX_READ_BYTES = 2 * 1024 * 1024
MAX_LINE_BYTES = 512 * 1024
MAX_CONTENT_ENTRIES = 20
MAX_CONTENT_BYTES = 32 * 1024
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
    entries: List[Dict[str, Any]] = field(default_factory=list)
    content_loaded: bool = False
    content_truncated: bool = False
    progress: List[str] = field(default_factory=list)
    content_at: float = 0.0

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
    record_offset: int = -1


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
        self.titles = DesktopTitles(self.roots[0][1].parent, self.roots[1][1].parent)
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

    def _content(self, session: Session, role: str, content: Any, at: float,
                 identity: str = "", commentary: bool = False) -> None:
        raw = message_text(content)
        text = visible_text(raw)
        if not text:
            return
        session.content_loaded = True
        session.content_at = max(session.content_at, at)
        # Transcript UUIDs are stable across prefix/tail replay. A Claude API
        # message can have several different visible blocks, so UUID/block index
        # identifies a record; a later tool-only record never erases the text.
        identity = identity or "%s:%s" % (at, text)
        key = hashlib.sha256((role + "\0" + identity).encode("utf-8")).hexdigest()
        entry = {"role": role, "text": text, "at": at, "id": key}
        for index, previous in enumerate(session.entries):
            if previous["id"] == key:
                session.entries[index] = entry
                break
        else:
            session.entries.append(entry)
        session.entries.sort(key=lambda item: item["at"])
        if len(session.entries) > MAX_CONTENT_ENTRIES:
            del session.entries[:-MAX_CONTENT_ENTRIES]
            session.content_truncated = True
        # Independent byte bound keeps initial feeds and disk snapshots bounded,
        # including Chinese text. Always retain the latest visible message.
        while len(session.entries) > 1 and sum(
                len(item["text"].encode("utf-8")) for item in session.entries) > MAX_CONTENT_BYTES:
            del session.entries[0]
            session.content_truncated = True
        if len(raw) > MAX_MESSAGE_CHARS:
            session.content_truncated = True
        if commentary:
            line = _text(text, 200)
            if not session.progress or session.progress[-1] != line:
                session.progress.append(line)
                del session.progress[:-4]

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
                # Forked/subagent rollouts can contain a copied parent meta
                # after their own. Keep the file's first identity and skip flag.
                if cursor.session is not None and cursor.session.session_id != sid:
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
            if session is None or session.skip:
                return
            if kind == "response_item" and payload.get("type") == "message":
                role = payload.get("role")
                phase, channel = payload.get("phase"), payload.get("channel")
                visible_channels = (None, "", "commentary", "final", "final_answer")
                if role == "user":
                    self._content(session, "you", payload.get("content"), at,
                                  _text(payload.get("id"), 256) or
                                  "record:%s:user" % cursor.record_offset)
                elif (role == "assistant" and phase in visible_channels
                      and channel in visible_channels):
                    self._content(session, "xiaoyou", payload.get("content"), at,
                                  _text(payload.get("id"), 256) or
                                  "record:%s:assistant" % cursor.record_offset,
                                  commentary=phase == "commentary")
            if at < session.hook_at:
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
            if session is None:
                return
            session.cwd = _text(event.get("cwd"), 2048) or session.cwd
            message = event.get("message")
            kind = event.get("type")
            if (isinstance(message, dict) and not event.get("isMeta")
                    and not event.get("isCompactSummary")):
                content = message.get("content")
                identity = _text(event.get("uuid"), 256)
                block = event.get("apiBlockIndex")
                if (not identity and isinstance(block, int) and not isinstance(block, bool)
                        and 0 <= block <= 65535 and _text(message.get("id"), 256)):
                    identity = "%s:%s" % (_text(message.get("id"), 256), block)
                if not identity:
                    identity = "record:%s:%s" % (cursor.record_offset, kind)
                if kind == "user" and "toolUseResult" not in event:
                    # The interruption marker describes state, not a new request.
                    if message_text(content).strip() != "[Request interrupted by user]":
                        self._content(session, "you", content, at, identity)
                elif kind == "assistant":
                    self._content(session, "xiaoyou", content, at, identity,
                                  commentary=message.get("stop_reason") == "tool_use")
            if at < session.hook_at:
                return
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
                    begin = handle.tell()
                    line = handle.readline(MAX_LINE_BYTES + 1)
                    if len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
                        break
                    self._line(cursor, line, stat.st_mtime, begin)
                if stat.st_size > MAX_READ_BYTES:
                    handle.seek(stat.st_size - MAX_READ_BYTES)
                    handle.readline(MAX_LINE_BYTES + 1)  # discard partial first record
                    if cursor.session:
                        cursor.session.content_truncated = True
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
                self._line(cursor, line, stat.st_mtime, begin)
        if cursor.session:
            cursor.session.unavailable = False

    def _line(self, cursor: Cursor, line: bytes, fallback: float, offset: int = -1) -> None:
        try:
            event = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(event, dict):
            cursor.record_offset = offset
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
                if at < max(session.observed_at, session.content_at):
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
        observed_at = max(session.observed_at, session.content_at)
        stale = session.status in ("running", "waiting", "unknown") and (
            unavailable or session.unavailable or now - observed_at > STALE_SECONDS)
        status = "unknown" if stale else session.status
        project = _text(Path(session.cwd).name, 80) or "project"
        content = tuple((entry["id"], entry["role"], entry["text"], entry["at"])
                        for entry in session.entries)
        existing = self.cards.get(session.card) if session.card else None
        # A hook can restore status before its transcript becomes readable.
        # Reuse the retained visible request for the fallback, without treating
        # a previously imported name as an authoritative upstream rename.
        entries = session.entries if session.content_loaded else (existing or {}).get("entries", [])
        title = self._title(session.agent, session.session_id, project, entries)
        key = (status, project, session.model, session.tool, observed_at,
               session.started_at, stale, content, session.content_truncated, tuple(session.progress),
               title.title, title.source, title.truncated)
        if key == session.published and existing:
            return
        if existing is None:
            card = self.cards.open(CONVERSATION, title.title, "unknown", session.agent,
                                   title_source=title.source)
            session.card = card["id"]
        label = LABELS[status]
        progress = [label] + session.progress
        if session.tool and status == "running":
            progress.append(session.tool)
        latest_reply = ""
        for entry in reversed(session.entries):
            if entry["role"] == "you":
                break
            if entry["role"] == "xiaoyou":
                latest_reply = entry["text"]
                break
        fields: Dict[str, Any] = title.fields()
        if session.content_loaded:
            fields.update(entries=[dict(entry) for entry in session.entries],
                          content_scope="recent", content_truncated=session.content_truncated)
        self.cards.update(session.card, state=STATES[status], source="desktop", project=project,
                          session_id=session.session_id, monitoring_only=True, desktop_status=status,
                          observed_at=observed_at, stale=stale, model=session.model or None,
                          started_at=session.started_at, approval=None, queued=False,
                          brief=_text(latest_reply, 120) or "%s · %s" % (project, label),
                          progress_lines=progress[-5:],
                          mood="busy" if status in ("running", "waiting") else "idle", **fields)
        session.published = key

    def _title(self, agent: str, sid: str, project: str, entries: List[Dict[str, Any]]):
        request = next((entry["text"] for entry in reversed(entries)
                        if entry["role"] == "you"), "")
        return select_title(upstream=self.titles.get(agent, sid), request=request,
                            fallback="%s %s %s" % (agent, project[:10] or "project", sid[-6:]))

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
        # Names can change independently of transcript activity (Codex WAL or
        # sidebar index). Refresh every poll and include the name in the card's
        # change key, so the normal feed wakes for a title-only update.
        owned = self.owned()
        observed = [card for card in self.cards.recent(None, 1000)
                    if card.get("monitoring_only") and card.get("session_id") not in owned]
        self.titles.refresh({agent: list(dict.fromkeys(
            [session.session_id for session in newest
             if session.agent == agent and not session.skip and session.session_id not in owned] +
            [card["session_id"] for card in observed if card.get("agent") == agent]))
            for agent in ("codex", "claude")})
        retained = {(session.agent, session.session_id) for session in newest}
        publishing = {(session.agent, session.session_id) for session in newest
                      if session.cwd and not session.skip and session.session_id not in owned}
        # Retained history may have no recent transcript after a restart. Its
        # sidebar name can still change; refresh only the title, preserving the
        # existing content/status and never inventing a working directory.
        for card in observed:
            identity = (card.get("agent"), card.get("session_id"))
            if identity in publishing or identity[0] not in ("codex", "claude"):
                continue
            fields = self._title(identity[0], identity[1], card.get("project", ""),
                                 card.get("entries", [])).fields()
            if any(card.get(key) != value for key, value in fields.items()):
                self.cards.update(card["id"], **fields)
        for key, session in self.sessions.items():
            if key not in retained and session.card:
                card = self.cards.get(session.card)
                if card and card["state"] in ("working", "waiting"):
                    self._publish(session, now, unavailable=True)
        for session in newest:
            self._publish(session, now)
