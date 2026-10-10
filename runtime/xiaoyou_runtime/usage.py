"""Local usage and quota snapshots for coding agents."""

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from .config import AgentSpec, Config

APP_SERVER_TIMEOUT_SECONDS = 20
CLAUDE_QUOTA_KEYS = (
    "quotaLimits", "rate_limits", "five_hour", "seven_day", "resets_at", "out_of_credits",
)


def _host(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    parsed = urlsplit(value)
    return parsed.netloc or parsed.path.split("/")[0] or value


def _sqlite_one(path: Path, query: str) -> Optional[sqlite3.Row]:
    if not path.is_file():
        return None
    connection = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=1)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(query).fetchone()
    finally:
        connection.close()


def _read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _maybe_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _window(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    used = _maybe_int(raw.get("usedPercent"))
    if used is None:
        return None
    used = max(0, min(100, used))
    return {
        "used_percent": used,
        "remaining_percent": 100 - used,
        "resets_at": _maybe_int(raw.get("resetsAt")),
        "window_minutes": _maybe_int(raw.get("windowDurationMins")),
    }


def _rate_limit(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    primary = _window(raw.get("primary"))
    secondary = _window(raw.get("secondary"))
    individual = raw.get("individualLimit") if isinstance(raw.get("individualLimit"), dict) else None
    credits = raw.get("credits") if isinstance(raw.get("credits"), dict) else None
    if primary is None and secondary is None and individual is None and credits is None:
        return None
    return {
        "limit_id": raw.get("limitId") if isinstance(raw.get("limitId"), str) else None,
        "limit_name": raw.get("limitName") if isinstance(raw.get("limitName"), str) else None,
        "plan_type": raw.get("planType") if isinstance(raw.get("planType"), str) else None,
        "primary": primary,
        "secondary": secondary,
        "credits": {
            "balance": credits.get("balance"),
            "has_credits": credits.get("hasCredits"),
            "unlimited": credits.get("unlimited"),
        } if credits is not None else None,
        "individual_limit": {
            "used": individual.get("used"),
            "limit": individual.get("limit"),
            "remaining_percent": individual.get("remainingPercent"),
            "resets_at": individual.get("resetsAt"),
        } if individual is not None else None,
        "spend_control_reached": raw.get("spendControlReached")
        if isinstance(raw.get("spendControlReached"), bool) else None,
        "reached_type": raw.get("rateLimitReachedType")
        if isinstance(raw.get("rateLimitReachedType"), str) else None,
    }


def _rate_limits_response(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {"available": False, "source": "codex_app_server", "error": "unexpected response"}
    buckets: Dict[str, Any] = {}
    by_id = raw.get("rateLimitsByLimitId")
    if isinstance(by_id, dict):
        for key, item in by_id.items():
            parsed = _rate_limit(item)
            if parsed is not None:
                buckets[str(key)] = parsed
    single = _rate_limit(raw.get("rateLimits"))
    if single is not None:
        buckets.setdefault(single.get("limit_id") or "default", single)
    reset = raw.get("rateLimitResetCredits")
    return {
        "available": bool(buckets),
        "source": "codex_app_server",
        "ordinary_usage_allowed": raw.get("ordinaryUsageAllowed")
        if isinstance(raw.get("ordinaryUsageAllowed"), bool) else None,
        "reset_credits": {"available_count": reset.get("availableCount")}
        if isinstance(reset, dict) else None,
        "buckets": buckets,
    }


class UsageMonitor:
    def __init__(self, config: Config):
        self._config = config

    def snapshot(self) -> Dict[str, Any]:
        return {
            "updated_at": time.time(),
            "codex": [self._codex(spec) for spec in self._config.agents if spec.type == "codex"],
            "claude": [self._claude(spec) for spec in self._config.agents
                       if spec.type == "claude_code"],
        }

    def _codex(self, spec: AgentSpec) -> Dict[str, Any]:
        config_dir = spec.config_dir
        result: Dict[str, Any] = {
            "name": spec.name,
            "kind": "codex",
            "config_dir": str(config_dir) if config_dir is not None else None,
            "tokens": self._codex_tokens(config_dir),
            "rate_limits": None,
        }
        if config_dir is None:
            result["rate_limits"] = {"available": False, "source": "config", "error": "no CODEX_HOME"}
        elif spec.codex_mode != "app_server":
            result["rate_limits"] = {
                "available": False, "source": "config", "error": "codex mode is %s" % spec.codex_mode,
            }
        else:
            result["rate_limits"] = self._codex_rate_limits(spec)
        return result

    def _codex_tokens(self, config_dir: Optional[Path]) -> Dict[str, Any]:
        if config_dir is None:
            return {"available": False, "source": "config"}
        try:
            row = _sqlite_one(
                config_dir / "state_5.sqlite",
                "select count(*) as threads, coalesce(sum(tokens_used),0) as tokens, "
                "max(updated_at_ms) as updated_at_ms from threads",
            )
        except sqlite3.Error as error:
            return {"available": False, "source": "state_5.sqlite", "error": str(error)}
        if row is None:
            return {"available": False, "source": "state_5.sqlite", "error": "missing database"}
        return {
            "available": True,
            "source": "state_5.sqlite",
            "threads": int(row["threads"] or 0),
            "total_tokens": int(row["tokens"] or 0),
            "updated_at_ms": int(row["updated_at_ms"] or 0),
        }

    def _codex_rate_limits(self, spec: AgentSpec) -> Dict[str, Any]:
        try:
            from . import codex_app
            from .agents import Control

            control = Control()
            env = dict(os.environ, CODEX_HOME=str(spec.config_dir))
            server = codex_app.AppServer(spec, control, env)
            try:
                deadline = time.monotonic() + APP_SERVER_TIMEOUT_SECONDS
                codex_app.handshake(server, deadline)
                request_id = server.request(
                    "account/rateLimits/read", {"skipResetCreditDetails": True})
                return _rate_limits_response(
                    server.expect(request_id, deadline, "account/rateLimits/read"))
            finally:
                server.close()
        except Exception as error:
            return {
                "available": False,
                "source": "codex_app_server",
                "error": "%s: %s" % (type(error).__name__, str(error)),
            }

    def _claude(self, spec: AgentSpec) -> Dict[str, Any]:
        config_dir = spec.config_dir or (Path.home() / ".claude")
        settings = _read_json(config_dir / "settings.json")
        env = settings.get("env") if isinstance(settings, dict) and isinstance(settings.get("env"), dict) else {}
        base_url = spec.env.get("ANTHROPIC_BASE_URL") or env.get("ANTHROPIC_BASE_URL")
        custom = bool(base_url and _host(base_url) not in ("api.anthropic.com", ""))
        latest = self._latest_claude_quota(config_dir)
        return {
            "name": spec.name,
            "kind": "claude_code",
            "config_dir": str(config_dir),
            "base_url_host": _host(base_url) or "api.anthropic.com",
            "quota": {
                "available": latest is not None and not custom,
                "source": "project_history" if latest is not None else "config",
                "reason": (
                    "custom_base_url" if custom else
                    "no_cached_quota_limits" if latest is None else None
                ),
                "latest_error": latest,
            },
        }

    def _latest_claude_quota(self, config_dir: Path) -> Optional[Dict[str, Any]]:
        root = config_dir / "projects"
        if not root.is_dir():
            return None
        best: Optional[Dict[str, Any]] = None
        for path in root.rglob("*.jsonl"):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                if not any(key in line for key in CLAUDE_QUOTA_KEYS):
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                quota = item.get("quotaLimits")
                if not isinstance(quota, dict):
                    continue
                found = {
                    "timestamp": item.get("timestamp") if isinstance(item.get("timestamp"), str) else None,
                    "status": quota.get("status"),
                    "rate_limit_type": quota.get("rateLimitType"),
                    "resets_at": _maybe_int(quota.get("resetsAt")),
                    "overage_status": quota.get("overageStatus"),
                    "overage_disabled_reason": quota.get("overageDisabledReason"),
                }
                if best is None or str(found["timestamp"]) > str(best.get("timestamp")):
                    best = found
        return best
