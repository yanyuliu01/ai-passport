"""Owner-controlled durable approval settings and snapshot-only batch review."""

import hashlib
import json
import math
import os
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict

from .approvals import ALLOW, TOOL, Approvals

MAX_BATCH = 100
MAX_AUDIT = 200
AUTOMATIC_TOOLS = ("Read", "Glob", "Grep", "LS")
MAX_SECONDS = 365 * 86400


class ReviewConflict(ValueError):
    pass


class QuickReview:
    def __init__(self, approvals: Approvals, state_dir: Path,
                 clock: Callable[[], float] = time.time,
                 event_sink: Callable[[Dict[str, Any]], None] = lambda event: None):
        self.approvals, self.clock = approvals, clock
        self._event_sink = event_sink
        self.epoch = secrets.token_hex(16)
        self._lock = threading.RLock()
        self._policies: Dict[str, Dict[str, Any]] = {}
        self._revision = 0
        self._error = ""
        self._path = state_dir / "approval-review.json"
        self._disabled = state_dir / "approval-review.disabled"
        self._audit = []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            audit = raw.get("audit", [])[-MAX_AUDIT:]
            if not isinstance(audit, list) or any(not isinstance(event, dict) for event in audit):
                raise ValueError("invalid audit")
            self._audit = audit
            policies = raw.get("policies", {})
            if not isinstance(policies, dict) or (policies and raw.get("policy_version") != 1):
                raise ValueError("invalid policies")
            for conversation, policy in policies.items():
                if not isinstance(conversation, str) or not conversation or not isinstance(policy, dict):
                    raise ValueError("invalid policy")
                seconds, expiry = policy.get("seconds"), policy.get("expires_at")
                if (type(seconds) is not int or seconds != 0 and not 60 <= seconds <= MAX_SECONDS
                        or type(expiry) not in (int, float) or not math.isfinite(expiry)
                        or (seconds == 0 and expiry != 0) or (seconds != 0 and expiry <= 0)):
                    raise ValueError("invalid policy duration")
            self._policies = policies
        except FileNotFoundError:
            self._audit = []
        except (OSError, ValueError, TypeError, AttributeError):
            self._policies = {}
            self._error = "审核记录不可读，请检查电脑存储"
        if self._disabled.exists():
            self._policies = {}
            self._error = "上次审核记录写入失败，已恢复手动审核；请重新设置"
        self._expire()
        for conversation, policy in list(self._policies.items()):
            try:
                self._event(conversation, "restored", **policy)
            except OSError:
                break
        approvals.on_request = self._asked

    def _key(self, item: Dict[str, Any]) -> str:
        data = {key: item.get(key) for key in ("id", "card", "conversation", "created_at",
                "agent", "tool", "detail", "source", "request_id", "session_id", "kind", "operation")}
        return hashlib.sha256((self.epoch + json.dumps(data, sort_keys=True,
                              ensure_ascii=False)).encode("utf-8")).hexdigest()

    def _eligible(self, item: Dict[str, Any], conversation: str, automatic: bool = False) -> bool:
        if (item.get("kind", TOOL) != TOOL or item.get("mesh_origin")
                or item.get("decision") is not None):
            return False
        # Only the adapter's structured tool identity qualifies. Never infer shell
        # safety from a command, an LLM description, or a translated display label.
        if automatic and item.get("operation") not in AUTOMATIC_TOOLS:
            return False
        if item.get("source") == "desktop":
            return (not automatic and item.get("remote_actionable") is True
                    and item.get("expires_at", 0) > self.clock())
        return item.get("conversation") == conversation

    def _write(self, audit: list) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            os.chmod(temporary, 0o600)
            json.dump({"audit": audit, "policy_version": 1, "policies": self._policies},
                      output, ensure_ascii=False, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self._path)

    def _emit(self, event: Dict[str, Any]) -> None:
        try:
            self._event_sink(dict(event))
        except Exception as error:
            # The durable approval audit is primary. Diagnostics must never
            # turn an already committed decision into a misleading HTTP failure.
            sys.stderr.write("Quick review event logging failed: %s\n" % type(error).__name__)

    def _revoke(self, conversation: str = "") -> None:
        if not self._policies and self._error.startswith("审核记录写入失败"):
            return
        self._policies.clear()
        self._revision += 1
        self._error = "审核记录写入失败，已恢复手动审核；请刷新查看操作状态"
        # A separate guard prevents an older durable grant from returning after
        # an interrupted/failed atomic state replacement. Explicit setup clears it.
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._disabled, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as output:
                output.write("manual\n")
                output.flush()
                os.fsync(output.fileno())
        except OSError:
            pass
        event = dict(at=self.clock(), epoch=self.epoch, conversation=conversation,
                     action="storage_failed", reason="storage_error")
        self._audit = (self._audit + [event])[-MAX_AUDIT:]
        try:
            self._write(self._audit)
        except OSError:
            pass
        self._emit(event)

    def _event(self, conversation: str, action: str, **fields: Any) -> None:
        # No command bodies, paths, credentials, or private operation text on disk.
        event = dict(at=self.clock(), epoch=self.epoch, conversation=conversation, action=action, **fields)
        audit = (self._audit + [event])[-MAX_AUDIT:]
        try:
            self._write(audit)
        except OSError:
            self._revoke(conversation)
            raise
        self._audit = audit
        self._emit(event)

    def _expire(self) -> None:
        for conversation, policy in list(self._policies.items()):
            if policy["expires_at"] and policy["expires_at"] <= self.clock():
                del self._policies[conversation]
                self._revision += 1
                try:
                    self._event(conversation, "expired", **policy)
                except OSError:
                    break

    def snapshot(self, conversation: str) -> Dict[str, Any]:
        with self._lock:
            self._expire()
            pending = []
            for item in self.approvals.pending(conversation, include_desktop=True):
                # pending() omits mesh_origin; get() retains origin and decision.
                current = self.approvals.get(item["id"]) or {}
                if self._eligible(current, conversation):
                    pending.append(dict(item, key=self._key(current)))
            policy = self._policies.get(conversation)
            return dict(epoch=self.epoch, revision=self._revision, conversation=conversation,
                        mode="auto_allow" if policy is not None else "manual",
                        expires_at=policy["expires_at"] if policy else 0,
                        persistent=policy is not None and policy["seconds"] == 0,
                        duration_seconds=policy["seconds"] if policy else 0,
                        duration_options=dict(persistent=True, min_seconds=60, max_seconds=MAX_SECONDS,
                                              survives_restart=True),
                        pending=pending[:MAX_BATCH], pending_total=len(pending), error=self._error,
                        automatic_tools=list(AUTOMATIC_TOOLS),
                        audit=[event for event in self._audit
                               if event.get("conversation") == conversation][-50:])

    def _check_epoch(self, body: Dict[str, Any]) -> None:
        if body.get("epoch") != self.epoch:
            raise ReviewConflict("电脑已重启，请刷新审核列表")

    def close(self) -> None:
        with self._lock:
            self._policies.clear()
            self.approvals.on_request = None

    def configure(self, conversation: str, body: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._expire()
            self._check_epoch(body)
            if type(body.get("revision")) is not int or body["revision"] != self._revision:
                raise ReviewConflict("审核模式已变化，请刷新后再设置")
            mode = body.get("mode")
            if mode not in ("manual", "auto_allow"):
                raise ValueError("mode 只能是 manual 或 auto_allow")
            seconds = body.get("seconds", 3600)
            if type(seconds) is not int or seconds != 0 and not 60 <= seconds <= MAX_SECONDS:
                raise ValueError("有效时间应为 60 到 31536000 秒，或 0 表示持续开启")
            previous = self._policies.get(conversation)
            if mode == "manual":
                self._policies.pop(conversation, None)
            expiry = self.clock() + seconds if mode == "auto_allow" and seconds else 0
            if mode == "auto_allow":
                self._policies[conversation] = dict(expires_at=expiry, seconds=seconds)
            self._revision += 1
            try:
                self._event(conversation, mode, expires_at=expiry, seconds=seconds,
                            change="disabled" if mode == "manual" else "duration_changed" if previous else "enabled",
                            previous_seconds=previous["seconds"] if previous else 0, mode=mode)
                self._disabled.unlink(missing_ok=True)
            except OSError:
                self._revoke(conversation)
                raise
            self._error = ""
            for item in self.approvals.pending(conversation):
                self._asked(item["id"])
            return self.snapshot(conversation)

    def rejected(self, conversation: str, operation: str, reason: str) -> None:
        with self._lock:
            try:
                self._event(conversation, "rejected", operation=operation, reason=reason)
            except OSError:
                pass

    def _decide(self, item: Dict[str, Any], conversation: str, by: str) -> str:
        self._event(conversation, "requested", id=item["id"], card=item["card"],
                    agent=item["agent"], by=by)
        accepted = self.approvals.answer(item["id"], ALLOW, by=by)
        status = "allowed" if accepted else "stale"
        self._event(conversation, status, id=item["id"], card=item["card"],
                    agent=item["agent"], by=by)
        return status

    def _asked(self, approval_id: str) -> None:
        with self._lock:
            self._expire()
            item = self.approvals.get(approval_id) or {}
            conversation = item.get("conversation", "")
            if conversation not in self._policies or not self._eligible(item, conversation, True):
                return
            try:
                self._decide(item, conversation, "auto_allow")
            except OSError:
                self._revoke(conversation)

    def batch(self, conversation: str, body: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._check_epoch(body)
            items = body.get("items")
            if not isinstance(items, list) or not 1 <= len(items) <= MAX_BATCH:
                raise ValueError("一次应选择 1 到 100 项")
            if any(not isinstance(item, dict) or not isinstance(item.get("id"), str)
                   or not isinstance(item.get("key"), str) for item in items):
                raise ValueError("审核项目缺少 id 或 key")
            results = []
            for reference in items:
                item = self.approvals.get(reference["id"]) or {}
                status = "stale"
                if self._key(item) == reference["key"]:
                    if item.get("decision") is not None:
                        status = item.get("status", "stale")
                    elif self._eligible(item, conversation):
                        status = self._decide(item, conversation, "phone_batch")
                results.append(dict(id=reference["id"], status=status))
            return dict(results=results, **self.snapshot(conversation))
