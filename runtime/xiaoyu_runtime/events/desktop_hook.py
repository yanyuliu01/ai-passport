"""Observe desktop hooks and optionally relay one-shot permission decisions.

Run ``python3 -m xiaoyu_runtime.desktop_hook --state-dir PATH --agent codex``
as a command hook. With ``--approval-bridge``, an official PermissionRequest
hook waits for the companion's answer. No answer defers to the desktop prompt;
other lifecycle events remain status-only and never return model context.
"""

import argparse
import hashlib
import json
import math
import os
import shlex
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

MAX_INPUT_BYTES = 65536
MAX_SESSION_CHARS = 256
MAX_PATH_CHARS = 4096
MAX_TITLE_CHARS = 120
MAX_MODEL_CHARS = 128

EVENT_STATUS = {
    "SessionStart": "unknown",
    "UserPromptSubmit": "running",
    "PermissionRequest": "waiting",
    "PostToolUse": "running",
    "PostToolUseFailure": "running",
    "Stop": "idle",
    "Interrupt": "interrupted",
    "StopFailure": "failed",
    "SessionEnd": "unknown",
}


def configuration(agent: str, state_dir: Path, runtime_dir: Path,
                  python: str = sys.executable, timeout: float = 300.0) -> Dict[str, Any]:
    """Return mergeable hook groups; never install or replace user settings."""
    if agent not in ("codex", "claude") or not 0 < timeout <= 600:
        raise ValueError("invalid hook configuration")
    command = "env PYTHONPATH=%s %s -m xiaoyu_runtime.desktop_hook --state-dir %s --agent %s" % (
        shlex.quote(str(runtime_dir.resolve())), shlex.quote(python),
        shlex.quote(str(state_dir.resolve())), agent)
    events = ["SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd"]
    events += ["Interrupt"] if agent == "codex" else ["PostToolUseFailure", "StopFailure"]
    groups = {event: [{"hooks": [{"type": "command", "command": command, "timeout": 5}]}]
              for event in events}
    if agent == "codex":
        for event in ("SessionEnd", "Interrupt"):
            groups[event][0]["hooks"][0]["timeout"] = 1
    groups["PermissionRequest"] = [{"hooks": [{"type": "command",
        "command": command + " --approval-bridge --approval-timeout %s" % timeout,
        "timeout": math.ceil(timeout + 10)}]}]
    if agent == "claude":
        groups["Notification"] = [{"matcher": "permission_prompt", "hooks": [{
            "type": "command", "command": command, "timeout": 5}]}]
    return {"hooks": groups}


def _text(value: Any, limit: int, required: bool = False) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        if required:
            raise ValueError("missing hook metadata")
        return None
    if len(value) > limit or any(ord(char) < 32 or ord(char) == 127 for char in value):
        if required:
            raise ValueError("invalid hook metadata")
        return None
    return value


def normalize(agent: str, payload: Any,
              observed_at: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Allow only metadata needed to observe the main conversation's status."""
    if agent not in ("codex", "claude") or not isinstance(payload, dict):
        raise ValueError("invalid hook input")
    source = payload.get("source")
    transcript = payload.get("transcript_path")
    if (payload.get("isSidechain") is True or payload.get("is_sidechain") is True
            or payload.get("agent_id") or payload.get("parent_thread_id")
            or (isinstance(source, dict) and "subagent" in source)
            or (isinstance(transcript, str)
                and "subagents" in Path(transcript).parts)):
        return None
    event = payload.get("hook_event_name")
    if event == "Notification":
        status = "waiting" if payload.get("notification_type") == "permission_prompt" else None
    else:
        status = EVENT_STATUS.get(event) if isinstance(event, str) else None
    if status is None:
        return None
    session = _text(payload.get("session_id"), MAX_SESSION_CHARS, required=True)
    cwd = _text(payload.get("cwd"), MAX_PATH_CHARS, required=True)
    if not Path(cwd).is_absolute():
        raise ValueError("invalid hook working directory")
    at = time.time() if observed_at is None else observed_at
    if (isinstance(at, bool) or not isinstance(at, (int, float))
            or not math.isfinite(at) or at < 0):
        raise ValueError("invalid hook timestamp")
    result = {"agent": agent, "session_id": session, "cwd": cwd,
              "status": status, "observed_at": float(at)}
    for key, value, limit in (
            ("title", payload.get("session_title"), MAX_TITLE_CHARS),
            ("model", payload.get("model"), MAX_MODEL_CHARS),
            ("turn_id", payload.get("turn_id") or payload.get("prompt_id"), MAX_SESSION_CHARS)):
        clean = _text(value, limit)
        if clean is not None:
            result[key] = clean
    if event in ("PostToolUse", "PostToolUseFailure"):
        from .desktop_approvals import operation_fingerprint
        tool = payload.get("tool_name")
        if isinstance(tool, str):
            result["tool_fingerprint"] = operation_fingerprint(tool, payload.get("tool_input"))
        tool_id = _text(payload.get("tool_use_id") or payload.get("tool_call_id"), MAX_SESSION_CHARS)
        if tool_id:
            result["tool_id"] = tool_id
    return result


def _write(path: Path, data: Dict[str, Any]) -> None:
    """Replace a snapshot atomically, using a private unique temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".desktop-hook-", suffix=".tmp",
                                        dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def record(state_dir: Path, event: Dict[str, Any]) -> Path:
    """Each agent/session has one latest snapshot; IDs never become paths."""
    identity = "%s\0%s" % (event["agent"], event["session_id"])
    name = hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".json"
    path = Path(state_dir) / "desktop-events" / name
    _write(path, event)
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Record a desktop conversation status hook")
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--agent", required=True, choices=("codex", "claude"))
    parser.add_argument("--approval-bridge", action="store_true",
                        help="relay one-shot decisions for official PermissionRequest hooks")
    parser.add_argument("--approval-timeout", type=float, default=300.0,
                        help="seconds to wait before restoring desktop approval (maximum 600)")
    parser.add_argument("--print-config", action="store_true", help="print mergeable official hook configuration")
    args = parser.parse_args(argv)
    if args.print_config:
        print(json.dumps(configuration(args.agent, args.state_dir, Path(__file__).resolve().parents[2],
                                       timeout=args.approval_timeout), ensure_ascii=False, indent=2))
        return 0
    try:
        # Reading bytes keeps the limit independent of UTF-8 character width.
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        raw = stream.read(MAX_INPUT_BYTES + 1)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("hook input exceeds size limit")
        payload = json.loads(raw.decode("utf-8"))
        permission = isinstance(payload, dict) and payload.get("hook_event_name") == "PermissionRequest"
        candidate = payload
        if permission:
            # Approval hooks may belong to a subagent. Bind them to its parent
            # session and retain agent_id in the approval rather than losing the
            # request because the transcript observer excludes sidechains.
            candidate = {key: value for key, value in payload.items() if key not in
                         ("agent_id", "parent_thread_id", "isSidechain", "is_sidechain", "source",
                          "transcript_path")}
        event = normalize(args.agent, candidate)
        if event is not None:
            record(args.state_dir, event)
            notification = payload.get("hook_event_name") == "Notification" and event["status"] == "waiting"
            if permission or notification:
                from .desktop_approvals import create_request, hook_output, wait_for_decision
                request = create_request(args.state_dir, args.agent, payload,
                    actionable=args.approval_bridge and permission, timeout=args.approval_timeout)
                if permission and args.approval_bridge:
                    output = hook_output(wait_for_decision(args.state_dir, request))
                    if output is not None:
                        print(json.dumps(output, ensure_ascii=False))
    except (OSError, ValueError, TypeError, RecursionError):
        # Do not echo exceptions: parser errors may contain private hook input.
        print("Desktop status hook could not record this event.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
