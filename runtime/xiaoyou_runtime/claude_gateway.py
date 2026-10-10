"""Bind the existing Claude Code harness to DeepSeek's documented API.

Credentials are resolved per child process and passed through a private settings
file, because Claude Code's settings env can override its inherited environment.
The base harness still owns sessions, tool execution, streaming and approvals.
"""

import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

from .agents import AgentError, ClaudeCodeAgent


DEEPSEEK_BASE_URL = "https://api.deepseek.com/anthropic"
MODEL_VARIABLES = (
    "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL",
)
OTHER_AUTH = (
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_REFRESH_TOKEN", "ANTHROPIC_PROFILE",
    "ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID",
    "ANTHROPIC_WORKSPACE_ID", "ANTHROPIC_CUSTOM_HEADERS",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_AWS",
    "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
)


def resolve(value, env, where):
    """Resolve a whole ${NAME} reference, including on older runtime versions."""
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value)
    if match:
        value = env.get(match.group(1), "")
        if not value.strip():
            raise AgentError("%s 缺少环境变量 %s" % (where, match.group(1)))
    return value.strip()


def uses_deepseek(spec):
    # Only explicitly configured agents: never change the owner's other agents.
    return (spec.name == "deepseek" or
            spec.env.get("ANTHROPIC_BASE_URL", "").rstrip("/") == DEEPSEEK_BASE_URL)


class DeepSeekClaudeCodeAgent(ClaudeCodeAgent):
    """A provider binding for ClaudeCodeAgent, without replacing its harness."""

    def __init__(self, spec, **kwargs):
        super().__init__(spec, **kwargs)
        self._gateway = threading.local()

    def _env(self):
        running = getattr(self._gateway, "env", None)
        if running is not None:
            return dict(running)
        env = dict(super()._env() or os.environ)
        base = resolve(self.spec.env.get("ANTHROPIC_BASE_URL", ""), env, "DeepSeek 接口")
        if base.rstrip("/") != DEEPSEEK_BASE_URL:
            raise AgentError("DeepSeek 的 ANTHROPIC_BASE_URL 必须是 " + DEEPSEEK_BASE_URL)
        if not self.spec.model or not self.spec.model.startswith("deepseek-"):
            raise AgentError("DeepSeek 的 model 必须明确填写 DeepSeek 模型，如 deepseek-v4-pro")
        # Do not accept a Claude key merely inherited from the parent shell.
        explicit = {name: resolve(self.spec.env.get(name, ""), env, "DeepSeek " + name)
                    for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")}
        keys = {value for value in explicit.values() if value}
        if len(keys) != 1:
            raise AgentError("DeepSeek 的 env / env_files 需要一份明确的密钥；两个认证字段不能不同")
        env.update({name: "" for name in OTHER_AUTH})
        env.update({name: self.spec.model for name in MODEL_VARIABLES})
        env.update(ANTHROPIC_BASE_URL=DEEPSEEK_BASE_URL,
                   ANTHROPIC_AUTH_TOKEN=keys.pop(),
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
        return env

    def _call_env(self, job):
        env = self._env()
        if job.model and job.model != self.spec.model:
            # A model picked for this one job (one of the agent's `models`).
            if not job.model.startswith("deepseek-"):
                raise AgentError("DeepSeek 的 models 里只能写 DeepSeek 的模型，如 deepseek-v4-flash")
            env.update({name: job.model for name in MODEL_VARIABLES})
        if job.plain:
            # DeepSeek ignores thinking.budget_tokens. Explicitly turn thinking
            # off for the short conversation/planning call, not for tool jobs.
            env.update(CLAUDE_CODE_DISABLE_THINKING="1", MAX_THINKING_TOKENS="0",
                       CLAUDE_CODE_EFFORT_LEVEL="low", CLAUDE_CODE_MAX_RETRIES="0",
                       API_TIMEOUT_MS=str(max(1000, ((job.timeout or
                           self.spec.timeout_seconds) - 5) * 1000)))
        return env

    def command(self, job, stream=False, mcp_config=None):
        args = list(self.spec.command[1:]) + list(self.spec.extra_args)
        conflicts = ("--model", "--settings", "--bare", "--system-prompt-snapshot")
        if any(arg.split("=", 1)[0] in conflicts for arg in args):
            raise AgentError("DeepSeek 的 command / extra_args 不要重复填写 " +
                             "、".join(conflicts) + "；这些参数由 Runtime 统一传入")
        command = super().command(job, stream=stream, mcp_config=mcp_config)
        if job.schema:
            # DeepSeek's Anthropic API only implements output_config.effort,
            # not output_config.format. Keep the shape in the system prompt;
            # runtime still checks action/card fields before dispatching work.
            index = command.index("--json-schema")
            del command[index:index + 2]
            instruction = ("\n\n请只输出一个 JSON 对象，不要代码围栏或额外说明。"
                           "按以下 JSON Schema 填写字段；reply 是对主人说的话。\n" +
                           json.dumps(job.schema, ensure_ascii=False))
            if "--append-system-prompt" in command:
                command[command.index("--append-system-prompt") + 1] += instruction
            else:
                command += ["--append-system-prompt", instruction]
        if job.plain:
            # The runtime refreshes card/task state in its prompt on each turn.
            command += ["--system-prompt-snapshot", "off"]
        settings = getattr(self._gateway, "settings", None)
        if settings is not None:
            command += ["--settings", str(settings)]
        return command

    def _settings(self, env, job):
        names = set(MODEL_VARIABLES + OTHER_AUTH + (
            "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        ))
        if job.plain:
            names.update(("CLAUDE_CODE_DISABLE_THINKING", "MAX_THINKING_TOKENS",
                          "CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_MAX_RETRIES", "API_TIMEOUT_MS"))
        return {"model": env["ANTHROPIC_MODEL"], "env": {name: env[name] for name in names}}

    @staticmethod
    def _trace(data):
        if os.environ.get("XIAOYOU_DEBUG", "").lower() not in ("", "0", "false", "no"):
            print("[xiaoyou claude gateway] " + json.dumps(data, ensure_ascii=False),
                  file=sys.stderr, flush=True)

    def run(self, job):
        env = self._call_env(job)
        self.command(job)  # Validate options before creating a credential file.
        fd, name = tempfile.mkstemp(prefix="xiaoyou-deepseek-", suffix=".json")
        path = Path(name)
        started = time.monotonic()
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._settings(env, job), handle, ensure_ascii=False)
            self._gateway.env, self._gateway.settings = env, path
            self._trace({"phase": "start", "host": "api.deepseek.com",
                         "model": self.spec.model, "plain": job.plain,
                         "resume": bool(job.session_id), "settings_bound": True})
            outcome = super().run(job)
            self._trace({"phase": "done", "elapsed_seconds": round(time.monotonic() - started, 2),
                         "has_fields": bool(outcome.fields)})
            return outcome
        except AgentError as error:
            # Do not print raw credential-bearing error content in diagnostics.
            self._trace({"phase": "failed", "elapsed_seconds": round(time.monotonic() - started, 2)})
            raise AgentError("DeepSeek（Claude Code harness）：" + self._redact(str(error), env)) from None
        finally:
            self._gateway.env = self._gateway.settings = None
            path.unlink(missing_ok=True)

    @staticmethod
    def _redact(value, env):
        token = env.get("ANTHROPIC_AUTH_TOKEN", "")
        if token:
            value = value.replace(token, "[redacted]")
        return re.sub(r"sk-[A-Za-z0-9_-]+", "[redacted]", value)

    def parse(self, returncode, payload, detail):
        env = self._env()
        # Sanitize before the base parser clips an error (which could clip a key).
        if payload is not None:
            payload = json.loads(self._redact(json.dumps(payload, ensure_ascii=False), env))
        detail = self._redact(detail, env)
        outcome = super().parse(returncode, payload, detail)
        if outcome.fields is not None:
            # The base parser preserves the JSON text in .text. Only its reply
            # field belongs in the conversation / phone / firmware.
            from .agents import Outcome
            outcome = Outcome(outcome.fields["reply"], outcome.session_id, outcome.fields)
        return outcome


def create_claude_agent(spec):
    return DeepSeekClaudeCodeAgent(spec) if uses_deepseek(spec) else ClaudeCodeAgent(spec)
