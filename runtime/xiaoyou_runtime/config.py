"""读取和校验配置。

配置是一个 JSON 文件；少数敏感或随机器变化的值可以用环境变量覆盖，方便以后放到
虚拟机或容器里运行而不改文件。只用标准库，兼容 Python 3.9。
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


class ConfigError(Exception):
    """配置有问题；消息里说明是哪一项、应该怎么改。"""


@dataclass(frozen=True)
class Tool:
    """小幽可以调用的一个外部工具（例如 Codex）。"""

    name: str
    description: str
    allowed_tools: List[str]


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    token: str
    state_dir: Path
    persona: str
    backend: str
    brief_max_chars: int
    turn_timeout_seconds: int
    # claude_code 后端
    claude_command: List[str]
    claude_workdir: Path
    # Claude Code 自己的配置目录；None 表示用它的默认位置（~/.claude）
    claude_config_dir: Optional[Path]
    claude_model: Optional[str]
    claude_permission_mode: str
    claude_allowed_tools: List[str]
    claude_extra_args: List[str]
    tools: List[Tool] = field(default_factory=list)

    def allowed_tools(self) -> List[str]:
        merged = list(self.claude_allowed_tools)
        for tool in self.tools:
            for rule in tool.allowed_tools:
                if rule not in merged:
                    merged.append(rule)
        return merged


BACKENDS = ("claude_code", "echo")
MIN_TOKEN_LENGTH = 16
PLACEHOLDER_TOKEN = "change-me-to-a-long-random-string"


def _expect(value: Any, kind: type, where: str) -> Any:
    # bool 是 int 的子类；端口写成 true 不应该被当成 1。
    if isinstance(value, bool) and kind is not bool:
        raise ConfigError("%s 应该是 %s" % (where, kind.__name__))
    if not isinstance(value, kind):
        raise ConfigError("%s 应该是 %s" % (where, kind.__name__))
    return value


def _strings(value: Any, where: str) -> List[str]:
    _expect(value, list, where)
    for item in value:
        if not isinstance(item, str) or not item:
            raise ConfigError("%s 里只能是非空字符串" % where)
    return list(value)


def _path(value: str, base: Path) -> Path:
    path = Path(os.path.expanduser(value))
    return path if path.is_absolute() else (base / path).resolve()


def load(path: Path, env: Optional[Mapping[str, str]] = None) -> Config:
    """读取配置文件。相对路径都相对于配置文件所在的目录。"""
    env = os.environ if env is None else env
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError("找不到配置文件 %s；可以从 config.example.json 复制一份" % path)
    except json.JSONDecodeError as error:
        raise ConfigError("配置文件 %s 不是合法的 JSON：%s" % (path, error))
    if not isinstance(raw, dict):
        raise ConfigError("配置文件最外层应该是一个对象")
    base = path.resolve().parent

    server = _expect(raw.get("server", {}), dict, "server")
    host = env.get("XIAOYOU_HOST") or _expect(server.get("host", "127.0.0.1"), str, "server.host")
    port_text = env.get("XIAOYOU_PORT")
    try:
        port = int(port_text) if port_text else _expect(server.get("port", 8765), int, "server.port")
    except ValueError:
        raise ConfigError("环境变量 XIAOYOU_PORT 应该是数字")
    if not 1 <= port <= 65535:
        raise ConfigError("server.port 应该在 1 到 65535 之间")
    token = env.get("XIAOYOU_TOKEN") or _expect(server.get("token", ""), str, "server.token")
    if token == PLACEHOLDER_TOKEN or len(token) < MIN_TOKEN_LENGTH:
        raise ConfigError(
            "server.token 需要换成至少 %d 位的随机字符串（或设置环境变量 XIAOYOU_TOKEN）。"
            "可以用 python3 -c \"import secrets; print(secrets.token_urlsafe(32))\" 生成"
            % MIN_TOKEN_LENGTH
        )

    state_dir = _path(
        env.get("XIAOYOU_STATE_DIR") or _expect(raw.get("state_dir", "state"), str, "state_dir"),
        base,
    )

    persona_file = _path(_expect(raw.get("persona_file", "persona.txt"), str, "persona_file"), base)
    try:
        persona = persona_file.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ConfigError("读不到人设文件 %s：%s" % (persona_file, error))
    if not persona:
        raise ConfigError("人设文件 %s 是空的" % persona_file)

    backend = env.get("XIAOYOU_BACKEND") or _expect(raw.get("backend", "claude_code"), str, "backend")
    if backend not in BACKENDS:
        raise ConfigError("backend 只能是 %s 之一" % "、".join(BACKENDS))

    brief = _expect(raw.get("brief_max_chars", 120), int, "brief_max_chars")
    if not 20 <= brief <= 400:
        raise ConfigError("brief_max_chars 应该在 20 到 400 之间")
    timeout = _expect(raw.get("turn_timeout_seconds", 600), int, "turn_timeout_seconds")
    if not 10 <= timeout <= 7200:
        raise ConfigError("turn_timeout_seconds 应该在 10 到 7200 之间")

    claude = _expect(raw.get("claude_code", {}), dict, "claude_code")
    command = _strings(claude.get("command", ["claude"]), "claude_code.command")
    if not command:
        raise ConfigError("claude_code.command 不能为空")
    model = claude.get("model")
    if model is not None:
        _expect(model, str, "claude_code.model")
    permission_mode = _expect(
        claude.get("permission_mode", "dontAsk"), str, "claude_code.permission_mode"
    )

    config_dir = claude.get("config_dir")
    if config_dir is not None:
        _expect(config_dir, str, "claude_code.config_dir")
        if not config_dir.strip():
            raise ConfigError("claude_code.config_dir 不能是空字符串；不需要就删掉这一项")
    config_dir = env.get("XIAOYOU_CLAUDE_CONFIG_DIR") or config_dir

    tools: List[Tool] = []
    seen: Dict[str, bool] = {}
    for index, item in enumerate(_expect(raw.get("tools", []), list, "tools")):
        where = "tools[%d]" % index
        _expect(item, dict, where)
        if not _expect(item.get("enabled", True), bool, where + ".enabled"):
            continue
        name = _expect(item.get("name", ""), str, where + ".name").strip()
        if not name:
            raise ConfigError(where + ".name 不能为空")
        if name in seen:
            raise ConfigError("工具名 %s 重复了" % name)
        seen[name] = True
        tools.append(
            Tool(
                name=name,
                description=_expect(item.get("description", ""), str, where + ".description").strip(),
                allowed_tools=_strings(item.get("allowed_tools", []), where + ".allowed_tools"),
            )
        )

    return Config(
        host=host,
        port=port,
        token=token,
        state_dir=state_dir,
        persona=persona,
        backend=backend,
        brief_max_chars=brief,
        turn_timeout_seconds=timeout,
        claude_command=command,
        claude_workdir=_path(_expect(claude.get("workdir", "workdir"), str, "claude_code.workdir"), base),
        claude_config_dir=_path(config_dir, base) if config_dir else None,
        claude_model=model,
        claude_permission_mode=permission_mode,
        claude_allowed_tools=_strings(claude.get("allowed_tools", []), "claude_code.allowed_tools"),
        claude_extra_args=_strings(claude.get("extra_args", []), "claude_code.extra_args"),
        tools=tools,
    )
