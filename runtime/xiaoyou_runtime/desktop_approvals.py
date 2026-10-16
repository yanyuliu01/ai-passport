"""One-shot desktop permission bridge using official synchronous command hooks.

The private spool never edits agent databases or resumes a conversation. A live
PermissionRequest hook owns each request; the runtime may write a single answer
only while that owner and its bounded lease are still present. Notifications
without a decision channel are explicitly non-actionable.
"""

import contextlib
import hashlib
import json
import math
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .approvals import Approvals, MAX_DETAIL_CHARS, describe
from .desktop_content import redact_credentials

try:
    import fcntl
except ImportError:  # No file-locking bridge on unsupported platforms.
    fcntl = None

DEFAULT_TIMEOUT = 300.0
MAX_TIMEOUT = 600.0
LEASE_SECONDS = 10.0
MAX_REQUEST_BYTES = 128 * 1024
MAX_REQUESTS = 128
CONVERSATION = "__desktop__"


def _private_root(state_dir: Path) -> Path:
    root = Path(state_dir) / "desktop-approvals"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or (root.stat().st_mode & 0o022):
        raise ValueError("desktop approval spool is not private")
    return root


def _write(path: Path, request: Dict[str, Any]) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".approval-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(request, handle, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
        os.replace(name, str(path))
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _read(path: Path) -> Optional[Dict[str, Any]]:
    if path.is_symlink() or path.stat().st_size > MAX_REQUEST_BYTES:
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


@contextlib.contextmanager
def _locked(path: Path):
    if fcntl is None:
        raise ValueError("desktop approval file locking is unavailable")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path.with_suffix(".lock")), flags, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _alive(pid: Any) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        # A sandbox may forbid signalling an existing host/CLI parent. The
        # hook's fresh heartbeat still supplies the required bounded lease.
        return True
    except OSError:
        return False


def _number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _identity(request: Dict[str, Any]) -> tuple:
    return tuple(request.get(key) for key in
                 ("request_id", "agent", "session_id", "turn_id", "subagent_id", "tool_id", "fingerprint"))


def operation_fingerprint(tool: str, tool_input: Any) -> str:
    raw = tool + "\0" + json.dumps(tool_input, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _current(state_dir: Path, request: Dict[str, Any], now: float) -> bool:
    if (request.get("state") != "pending" or not _number(request.get("expires_at"))
            or not _number(request.get("created_at")) or request["created_at"] > now + 10
            or not 0 < request["expires_at"] - request["created_at"] <= MAX_TIMEOUT
            or now >= request["expires_at"]):
        return False
    if request.get("remote_actionable"):
        if (not _alive(request.get("pid")) or not _alive(request.get("parent_pid"))
                or not _number(request.get("heartbeat"))
                or request["heartbeat"] > now + 10
                or now - request["heartbeat"] > LEASE_SECONDS):
            return False
    identity = "%s\0%s" % (request.get("agent"), request.get("session_id"))
    event_path = Path(state_dir) / "desktop-events" / (
        hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".json")
    try:
        event = _read(event_path)
    except FileNotFoundError:
        event = None
    if event and _number(event.get("observed_at")) and event["observed_at"] > request["created_at"]:
        if event.get("turn_id") and request.get("turn_id") and event["turn_id"] != request["turn_id"]:
            return False
        if event.get("status") in ("idle", "interrupted", "failed", "unknown"):
            return False
        if event.get("status") == "running":
            # One tool finishing does not resolve a parallel tool's approval.
            matched = ((request.get("tool_id") and event.get("tool_id") == request["tool_id"])
                       or event.get("tool_fingerprint") == request.get("fingerprint"))
            if matched or request.get("remote_actionable") is not True:
                return False
    return True


def create_request(state_dir: Path, agent: str, payload: Dict[str, Any], *,
                   actionable: bool = False, timeout: float = DEFAULT_TIMEOUT,
                   clock: Callable[[], float] = time.time) -> Path:
    """Capture the exact approval operation, independently of transcript privacy."""
    now = clock()
    bridge_requested = actionable
    if not _number(timeout) or not 0 < timeout <= MAX_TIMEOUT:
        raise ValueError("invalid desktop approval timeout")
    if (agent not in ("codex", "claude") or not isinstance(payload.get("session_id"), str)
            or not 1 <= len(payload["session_id"]) <= 256
            or not isinstance(payload.get("cwd"), str) or not Path(payload["cwd"]).is_absolute()):
        raise ValueError("invalid desktop approval identity")
    tool = payload.get("tool_name")
    data = payload.get("tool_input")
    if actionable and (fcntl is None or not isinstance(tool, str) or not tool.strip()
                       or len(tool) > 80 or any(ord(char) < 32 for char in tool)):
        actionable = False
    if actionable:
        has_context = isinstance(data, dict) and bool(data)
        if tool == "Bash":
            has_context = (isinstance(data, dict) and isinstance(data.get("command"), str)
                           and bool(data["command"].strip()))
        elif tool in ("Read", "Edit", "Write"):
            has_context = (isinstance(data, dict) and isinstance(data.get("file_path"), str)
                           and bool(data["file_path"].strip()))
            if tool == "Write":
                has_context = has_context and isinstance(data.get("content"), str)
            elif tool == "Edit":
                has_context = has_context and all(isinstance(data.get(key), str)
                                                 for key in ("old_string", "new_string"))
        elif tool == "apply_patch":
            has_context = isinstance(data, str) and bool(data.strip())
        if not has_context:
            actionable = False
    if isinstance(tool, str) and tool.strip():
        if tool == "apply_patch" and isinstance(data, str):
            label, detail = agent + " · " + tool, data.strip()
        elif tool == "NotebookEdit":
            label, detail = agent + " · " + tool, json.dumps(data, ensure_ascii=False)
        else:
            label, detail = describe(agent, tool, data)
    else:
        label = agent + " · 电脑端审批"
        message = payload.get("message")
        detail = message if isinstance(message, str) else "电脑端正在等待确认"
    if len(detail) >= MAX_DETAIL_CHARS:
        actionable = False
        detail += "\n操作内容过长，请在电脑端检查完整请求。"
    def metadata(value: Any) -> str:
        return value if isinstance(value, str) and len(value) <= 256 else ""
    request = {
        "request_id": secrets.token_hex(16), "agent": agent,
        "session_id": payload["session_id"], "cwd": payload["cwd"],
        "turn_id": metadata(payload.get("turn_id") or payload.get("prompt_id")),
        "subagent_id": metadata(payload.get("agent_id")), "state": "pending",
        "tool_id": metadata(payload.get("tool_use_id") or payload.get("tool_call_id")),
        "tool": redact_credentials(label),
        "detail": redact_credentials(detail)[:MAX_DETAIL_CHARS],
        "fingerprint": operation_fingerprint(tool if isinstance(tool, str) else "", payload.get("tool_input")),
        "created_at": now, "expires_at": now + timeout, "heartbeat": now,
        "remote_actionable": actionable, "pid": os.getpid(), "parent_pid": os.getppid(),
    }
    if not actionable:
        request["detail"] += "\n请在电脑端确认"
    root = _private_root(state_dir)
    # A permission notification is a second observation of a live hook, not a
    # second operation. Keep distinct actual PermissionRequests independent.
    with (_locked(root / "notification-index.json") if fcntl is not None else contextlib.nullcontext()):
        for existing_path in root.glob("*.json"):
            try:
                existing = _read(existing_path)
                if (not existing or not _current(state_dir, existing, now)
                        or any(existing.get(key) != request.get(key) for key in
                               ("agent", "session_id", "subagent_id"))):
                    continue
                if not bridge_requested and existing.get("remote_actionable") is True:
                    return existing_path
                if (not bridge_requested and existing.get("remote_actionable") is not True
                        and existing.get("detail") == request["detail"]
                        and existing.get("fingerprint") == request["fingerprint"]):
                    existing["expires_at"] = min(existing["created_at"] + MAX_TIMEOUT, now + timeout)
                    _write(existing_path, existing)
                    return existing_path
                if actionable and existing.get("remote_actionable") is not True:
                    existing["state"] = "superseded"
                    _write(existing_path, existing)
            except (OSError, ValueError, TypeError):
                continue
        path = root / (request["request_id"] + ".json")
        _write(path, request)
    return path


def wait_for_decision(state_dir: Path, path: Path, *, clock: Callable[[], float] = time.time,
                      sleep: Callable[[float], None] = time.sleep) -> Optional[str]:
    """Return a decision once, or defer to the original desktop permission flow."""
    original = _read(path)
    if not original or original.get("remote_actionable") is not True:
        return None
    try:
        while True:
            with _locked(path):
                request = _read(path)
                if not request or _identity(request) != _identity(original):
                    return None
                now = clock()
                if request.get("state") == "answered":
                    if now >= request["expires_at"]:
                        return None
                    request["state"] = "consumed"
                    _write(path, request)
                    return request.get("decision") if request.get("decision") in ("allow", "deny") else None
                if not _current(state_dir, request, now):
                    if request.get("state") == "pending":
                        request["state"] = "expired"
                        _write(path, request)
                    return None
                if now - request["heartbeat"] >= 1:
                    request["heartbeat"] = now
                    _write(path, request)
            sleep(0.1)
    finally:
        # Interrupts and ordinary completion both revoke the remote decision
        # channel. A killed process loses its lease and is pruned by the runtime.
        with _locked(path):
            request = _read(path)
            if request and _identity(request) == _identity(original) and request.get("state") == "pending":
                request["state"] = "closed"
                _write(path, request)


def hook_output(decision: Optional[str]) -> Optional[Dict[str, Any]]:
    if decision not in ("allow", "deny"):
        return None
    result = {"behavior": decision}
    if decision == "deny":
        result["message"] = "The owner denied this operation from the companion."
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": result}}


class DesktopApprovals:
    def __init__(self, state_dir: Path, approvals: Approvals,
                 card_for: Callable[[Dict[str, Any]], Optional[str]], *,
                 eligible: Callable[[Dict[str, Any]], bool] = lambda request: True,
                 clock: Callable[[], float] = time.time):
        self.state_dir, self.approvals, self.card_for, self.clock = Path(state_dir), approvals, card_for, clock
        self.eligible = eligible
        self.requests: Dict[Path, tuple] = {}

    def _respond(self, path: Path, original: Dict[str, Any], decision: str) -> bool:
        with _locked(path):
            request = _read(path)
            if (not request or _identity(request) != _identity(original)
                    or not self.eligible(request)
                    or not _current(self.state_dir, request, self.clock())):
                return False
            request.update(state="answered", decision=decision, answered_at=self.clock())
            _write(path, request)
            return True

    def poll(self) -> None:
        root = self.state_dir / "desktop-approvals"
        if not root.exists():
            return
        try:
            paths = sorted(root.glob("*.json"), key=lambda path: path.stat().st_mtime,
                           reverse=True)[:MAX_REQUESTS]
        except OSError:
            return
        for path in paths:
            try:
                request = _read(path)
                if not request or request.get("agent") not in ("codex", "claude"):
                    continue
                if (not isinstance(request.get("request_id"), str)
                        or path.stem != request["request_id"] or len(path.stem) != 32
                        or not isinstance(request.get("session_id"), str)
                        or not isinstance(request.get("cwd"), str) or not Path(request["cwd"]).is_absolute()
                        or not _number(request.get("created_at"))):
                    continue
                existing = self.requests.get(path)
                if not _current(self.state_dir, request, self.clock()):
                    if existing:
                        self.approvals.withdraw(existing[0], "desktop")
                        self.requests.pop(path, None)
                    continue
                if existing:
                    if existing[1] != _identity(request):
                        self.approvals.withdraw(existing[0])
                        self.requests.pop(path, None)
                    elif self.card_for(request) is None:
                        self.approvals.withdraw(existing[0])
                        self.requests.pop(path, None)
                    continue
                card = self.card_for(request)
                if not card:
                    continue
                context = {"source": "desktop", "session_id": request["session_id"],
                           "project": Path(request["cwd"]).name, "request_id": request["request_id"],
                           "remote_actionable": request.get("remote_actionable") is True,
                           "expires_at": request["expires_at"], "turn_id": request.get("turn_id", ""),
                           "subagent_id": request.get("subagent_id", "")}
                approval = self.approvals.ask(card, CONVERSATION, request["agent"],
                    str(request.get("tool", "电脑端审批")), str(request.get("detail", "")),
                    context=context, responder=lambda decision, p=path, r=dict(request):
                        self._respond(p, r, decision))
                self.requests[path] = (approval, _identity(request))
            except (OSError, ValueError, TypeError):
                continue
        for path in set(self.requests) - set(paths):
            approval, _ = self.requests.pop(path)
            self.approvals.withdraw(approval)

    def close(self) -> None:
        for path, (approval, identity) in list(self.requests.items()):
            self.approvals.withdraw(approval)
            try:
                with _locked(path):
                    request = _read(path)
                    if request and _identity(request) == identity and request.get("state") == "pending":
                        request["state"] = "closed"
                        _write(path, request)
            except (OSError, ValueError):
                pass
        self.requests.clear()
