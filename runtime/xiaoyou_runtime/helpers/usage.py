"""Subscription probes; the world projection never starts helper processes."""

import json
import shutil
import subprocess
import time
from typing import Optional, Tuple

from ..config import AgentSpec
from ..world.usage import (
    Usage, Windows, claude_usage_windows, claude_cached_usage_windows, codex_windows,
)

FETCH_SECONDS = 20


def create_usage(config, **kwargs):
    """Compose the usage projection with the existing bounded helper probes."""
    return Usage(config, fetchers={"claude": fetch_claude, "codex": fetch_codex}, **kwargs)


def fetch_claude(spec: AgentSpec) -> Tuple[Windows, Optional[str], Optional[str]]:
    from .agents import AgentError, ClaudeCodeAgent
    # 只问用量：不发提示，所以不调用模型；也不用把登记过的 MCP 服务都起一遍。
    command = list(spec.command) + ["-p", "--input-format", "stream-json",
                                    "--output-format", "stream-json", "--verbose",
                                    "--strict-mcp-config"]
    command[0] = shutil.which(command[0]) or command[0]
    ask = json.dumps({"type": "control_request", "request_id": "xiaoyou-usage",
                      "request": {"subtype": "get_usage", "skip_behaviors": True}})
    try:
        env = ClaudeCodeAgent(spec)._env()
        if spec.workdir is not None:
            spec.workdir.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(
            command, input=ask + "\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=FETCH_SECONDS, env=env,
            cwd=str(spec.workdir) if spec.workdir is not None else None)
    except AgentError as error:
        return {}, None, str(error)
    except FileNotFoundError:
        return {}, None, "找不到命令 %s" % command[0]
    except subprocess.TimeoutExpired:
        return {}, None, "Claude Code %d 秒没有回答用量" % FETCH_SECONDS
    except OSError as error:
        return {}, None, "启动 Claude Code 失败：%s" % error
    for line in done.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = message.get("response") if isinstance(message, dict) else None
        if (message.get("type") if isinstance(message, dict) else None) != "control_response" \
                or not isinstance(reply, dict):
            continue
        if reply.get("subtype") != "success":
            return {}, None, "这个版本的 Claude Code 不能这样查用量：%s" % (
                str(reply.get("error") or "没有说明")[:120])
        windows, plan, problem = claude_usage_windows(reply.get("response"))
        if windows:
            return windows, plan, problem
        cached = claude_cached_usage_windows(spec.config_dir)
        if cached:
            return cached, plan, None
        return windows, plan, problem
    detail = (done.stderr.strip().splitlines() or ["没有输出"])[-1][:120]
    return {}, None, "Claude Code 没有回答用量（退出码 %d）：%s" % (done.returncode, detail)


def fetch_codex(spec: AgentSpec) -> Tuple[Windows, Optional[str], Optional[str]]:
    from . import codex_app
    from .agents import AgentError, CodexAgent, Control
    try:
        server = codex_app.AppServer(spec, Control(), CodexAgent(spec)._env())
    except AgentError as error:
        return {}, None, str(error)
    try:
        deadline = time.monotonic() + FETCH_SECONDS
        codex_app.handshake(server, deadline)
        def read_limits():
            request_id = server.request("account/rateLimits/read", codex_app.LIMITS_PARAMS)
            return server.expect(request_id, deadline, "account/rateLimits/read")
        try:
            response = read_limits()
        except AgentError as error:
            # The native login may still be recoverable even after the usage endpoint rejects its access token.
            detail = str(error).lower()
            if not any(marker in detail for marker in ("401", "unauthorized", "token has expired")):
                raise
            refresh_id = server.request("account/read", {"refreshToken": True})
            server.expect(refresh_id, deadline, "account/read")
            response = read_limits()  # One retry, sharing the original 20-second probe deadline.
        windows, plan = codex_windows(response)
        return windows, plan, None if windows else "Codex 这一次没有给出额度"
    except AgentError as error:
        return {}, None, str(error)
    finally:
        server.close()
