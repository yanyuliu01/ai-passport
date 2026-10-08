"""后端：真正替小幽“想”的那一层。

Runtime 只认识 Backend 这个接口：给它这一轮的话和上一次的会话编号，它返回回复和
新的会话编号。现在有两个实现：claude_code（驱动本机的 Claude Code 命令行）和
echo（不调用任何模型，用来测试整条链路）。以后接别的代理只需要再加一个实现。
"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .config import Config

MOODS = ("idle", "busy", "ask", "happy", "oops")


class BackendError(Exception):
    """这一轮没有得到回复；消息可以直接给用户看。"""


@dataclass(frozen=True)
class Turn:
    reply: str  # 完整回复，给手机 App 显示
    brief: str  # 给小屏幕的一两句话
    mood: str  # 小幽此刻的表情，取值见 MOODS
    session_id: Optional[str]  # 下一轮接着聊要用的会话编号


def clip(text: str, limit: int) -> str:
    """截到 limit 个字符以内，截断时以省略号结尾。"""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def shape(reply: str, brief: Any, mood: Any, session_id: Optional[str], limit: int) -> Turn:
    """把后端给出的原始字段整理成一轮结果：缺的补上，超长的截断，非法的表情改成 idle。"""
    reply = (reply or "").strip()
    brief_text = brief.strip() if isinstance(brief, str) else ""
    return Turn(
        reply=reply,
        brief=clip(brief_text or reply, limit),
        mood=mood if mood in MOODS else "idle",
        session_id=session_id,
    )


class Backend:
    name = "backend"

    def turn(self, text: str, session_id: Optional[str]) -> Turn:
        raise NotImplementedError


class EchoBackend(Backend):
    """原样复述，用来在没有任何模型的情况下验证手机、Runtime、设备之间的链路。"""

    name = "echo"

    def __init__(self, config: Config):
        self._limit = config.brief_max_chars

    def turn(self, text: str, session_id: Optional[str]) -> Turn:
        count = int(session_id.split("-")[-1]) + 1 if session_id else 1
        return shape("（回声）" + text, None, "happy", "echo-%d" % count, self._limit)


REPLY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "brief": {"type": "string"},
        "mood": {"type": "string", "enum": list(MOODS)},
    },
    "required": ["reply", "brief", "mood"],
    "additionalProperties": False,
}


def system_prompt(config: Config) -> str:
    """人设 + 输出格式 + 当前可用的工具清单。"""
    parts = [config.persona, ""]
    parts.append("## 回复格式")
    parts.append(
        "每一轮都按给定的 JSON 结构回答：reply 是给主人看的完整回复；brief 是显示在随身"
        "小屏幕上的一两句话，不超过 %d 个字，要让人不看 reply 也知道结论，只用普通文字和标点，"
        "不用表情符号（小屏幕显示不了）；mood 是你此刻的"
        "表情，只能是 idle（平常）、busy（还在忙）、ask（需要主人拿主意）、happy（顺利完成）、"
        "oops（出了问题）之一。" % config.brief_max_chars
    )
    parts.append("")
    parts.append("## 你能调用的工具")
    if config.tools:
        for tool in config.tools:
            parts.append("- %s：%s" % (tool.name, tool.description or "（没有说明）"))
        parts.append(
            "自己能答的直接答。自己做不了、而上面某个工具更合适的，就交给它去做，"
            "等结果回来后用你自己的话总结给主人，不要原样转贴一大段输出。"
        )
    else:
        parts.append("目前没有配置额外的工具，你只能靠自己回答。做不到的事直接说做不到。")
    return "\n".join(parts)


class ClaudeCodeBackend(Backend):
    """用非交互模式驱动 Claude Code 命令行。

    每一轮启动一次命令：这一轮的话从标准输入送进去，要求输出 JSON；有上一次的会话
    编号就带上 --resume 接着聊。对话记录由 Claude Code 自己保存。
    """

    name = "claude_code"

    def __init__(self, config: Config, run=subprocess.run):
        self._config = config
        self._run = run
        self._prompt = system_prompt(config)

    def command(self, session_id: Optional[str]) -> List[str]:
        config = self._config
        command = list(config.claude_command)
        command += ["-p", "--output-format", "json"]
        command += ["--append-system-prompt", self._prompt]
        command += ["--json-schema", json.dumps(REPLY_SCHEMA, ensure_ascii=False)]
        command += ["--permission-mode", config.claude_permission_mode]
        allowed = config.allowed_tools()
        if allowed:
            command += ["--allowedTools", ",".join(allowed)]
        if config.claude_model:
            command += ["--model", config.claude_model]
        if session_id:
            command += ["--resume", session_id]
        command += config.claude_extra_args
        return command

    def turn(self, text: str, session_id: Optional[str]) -> Turn:
        config = self._config
        config.claude_workdir.mkdir(parents=True, exist_ok=True)
        extra = {}
        if config.claude_config_dir is not None:
            # 让这台机器上的 Claude Code 用一套单独的登录和设置，
            # 不受（也不影响）使用者平时那套 ~/.claude 配置。
            extra["env"] = dict(os.environ, CLAUDE_CONFIG_DIR=str(config.claude_config_dir))
        command = self.command(session_id)
        # 按 PATH 找到完整路径再启动：Windows 上这样才能找到 claude.exe / claude.cmd。
        command[0] = shutil.which(command[0]) or command[0]
        try:
            done = self._run(
                command,
                input=text,
                **extra,
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=str(config.claude_workdir),
                timeout=config.turn_timeout_seconds,
            )
        except FileNotFoundError:
            raise BackendError(
                "找不到命令 %s。请先安装并登录 Claude Code，或在配置里改 claude_code.command"
                % config.claude_command[0]
            )
        except subprocess.TimeoutExpired:
            raise BackendError("这一轮超过 %d 秒还没结束，已经停止" % config.turn_timeout_seconds)
        except OSError as error:
            raise BackendError("启动 Claude Code 失败：%s" % error)
        return self.parse(done.returncode, done.stdout, done.stderr)

    def parse(self, returncode: int, stdout: str, stderr: str) -> Turn:
        payload: Any = None
        try:
            payload = json.loads(stdout) if stdout.strip() else None
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            detail = clip(stderr or stdout or "没有输出", 200)
            raise BackendError("Claude Code 没有返回可用的结果（退出码 %d）：%s" % (returncode, detail))
        session_id = payload.get("session_id") if isinstance(payload.get("session_id"), str) else None
        result = payload.get("result") if isinstance(payload.get("result"), str) else ""
        if returncode != 0 or payload.get("is_error") is True:
            raise BackendError("Claude Code 报告失败：%s" % clip(result or stderr or "没有说明", 200))
        structured = payload.get("structured_output")
        if isinstance(structured, dict) and isinstance(structured.get("reply"), str):
            return shape(
                structured["reply"],
                structured.get("brief"),
                structured.get("mood"),
                session_id,
                self._config.brief_max_chars,
            )
        if not result.strip():
            raise BackendError("Claude Code 返回了空的回复")
        # 没拿到结构化输出：整段文字当作回复，简报由它截出来。
        return shape(result, None, None, session_id, self._config.brief_max_chars)


def create(config: Config) -> Backend:
    if config.backend == "echo":
        return EchoBackend(config)
    return ClaudeCodeBackend(config)
