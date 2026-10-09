"""读取和校验配置。

配置是一个 JSON 文件；少数敏感或随机器变化的值可以用环境变量覆盖，方便以后放到
虚拟机或容器里运行而不改文件。只用标准库，兼容 Python 3.9。

配置里最重要的一块是 agents：小幽可以把话交给哪些代理。小幽自己不是其中任何一个；
她的人设、对话记录和“交给谁”的判断都在 Runtime 里。0.3 及更早的配置（backend、
claude_code、tools）仍然能读，会被换算成只有一个代理的 agents。
"""

import json
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


class ConfigError(Exception):
    """配置有问题；消息里说明是哪一项、应该怎么改。"""


@dataclass(frozen=True)
class AgentSpec:
    """一个代理的配置。各类型用到的字段不同，用不到的保持默认值。"""

    name: str
    type: str
    # 写给小幽和路由器看的一句话：这个代理擅长什么
    description: str
    # 点名时除了 name 之外还认的叫法（例如语音识别常写成的中文名）
    aliases: List[str]
    # 能不能直接以小幽的身份回话。不能的只干活，结果由小幽转述
    speaks: bool
    timeout_seconds: int
    # claude_code / codex / command：要运行的命令
    command: List[str] = field(default_factory=list)
    workdir: Optional[Path] = None
    model: Optional[str] = None
    extra_args: List[str] = field(default_factory=list)
    # claude_code
    config_dir: Optional[Path] = None
    permission_mode: str = "dontAsk"
    allowed_tools: List[str] = field(default_factory=list)
    # codex
    sandbox: Optional[str] = None
    # remote：另一台小幽 Runtime
    url: Optional[str] = None
    token: Optional[str] = None


@dataclass(frozen=True)
class Config:
    # 这台 Runtime 的名字，手机 App 里用它区分几台电脑
    name: str
    host: str
    port: int
    token: str
    state_dir: Path
    persona: str
    brief_max_chars: int
    agents: List[AgentSpec]
    # 没人点名、路由器也没意见时，话交给谁
    default_agent: str
    # 只会干活的代理做完之后，由谁用小幽的口吻转述
    voice_agent: str
    # 一轮里最多转交几次
    max_handoffs: int
    router_type: str = "mention"
    router_command: List[str] = field(default_factory=list)
    router_timeout_seconds: int = 10
    # 语音识别；engine 为 none 时不接受语音消息
    stt_engine: str = "none"
    stt_command: List[str] = field(default_factory=list)
    stt_model_dir: Optional[Path] = None
    stt_language: str = "auto"
    stt_threads: int = 2
    stt_timeout_seconds: int = 60
    # 设备固件：从哪个 GitHub 仓库取构建好的镜像，源码在这台电脑的哪里，会不会自己构建
    firmware_repo: Optional[str] = None
    firmware_asset: str = "FoloToy-AI-Passport-full.bin"
    firmware_tag_prefix: str = "firmware-build-"
    firmware_source_dir: Optional[Path] = None
    firmware_build_command: List[str] = field(default_factory=list)
    firmware_build_output: str = "build/FoloToy-AI-Passport.bin"
    # 配置文件所在的目录；相对路径、stt.command 和 router.command 都以它为准
    base_dir: Path = Path(".")
    # 读配置时发现的、不妨碍启动但值得让人知道的事
    notices: List[str] = field(default_factory=list)

    def agent(self, name: str) -> Optional[AgentSpec]:
        for spec in self.agents:
            if spec.name == name:
                return spec
        return None


AGENT_TYPES = ("claude_code", "codex", "command", "remote", "echo")
# 这些类型默认直接以小幽的身份回话；其余的默认只干活。
SPEAKS_BY_DEFAULT = ("claude_code", "remote", "echo")
ROUTER_TYPES = ("mention", "command")
CODEX_SANDBOXES = ("read-only", "workspace-write", "danger-full-access")
STT_ENGINES = ("none", "sense_voice", "command")
STT_LANGUAGES = ("auto", "zh", "en", "ja", "ko", "yue")
GITHUB_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
MIN_TOKEN_LENGTH = 16
PLACEHOLDER_TOKEN = "change-me-to-a-long-random-string"
AGENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,23}$")
MAX_AGENTS = 12
MAX_ALIAS_CHARS = 24


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


def _optional_string(value: Any, where: str) -> Optional[str]:
    if value is None:
        return None
    _expect(value, str, where)
    if not value.strip():
        raise ConfigError("%s 不能是空字符串；不需要就删掉这一项" % where)
    return value.strip()


def _path(value: str, base: Path) -> Path:
    path = Path(os.path.expanduser(value))
    return path if path.is_absolute() else (base / path).resolve()


def _timeout(value: Any, where: str) -> int:
    _expect(value, int, where)
    if not 10 <= value <= 7200:
        raise ConfigError("%s 应该在 10 到 7200 之间" % where)
    return value


def _agent(name: str, raw: Any, base: Path, default_timeout: int,
           env: Mapping[str, str]) -> Optional[AgentSpec]:
    """读一个代理；enabled 为 false 时返回 None。"""
    where = "agents.%s" % name
    if not AGENT_NAME.match(name):
        raise ConfigError(
            "代理的名字 %r 不行：只能用字母、数字、下划线和连字符，以字母或数字开头，最多 24 个字符"
            % name
        )
    _expect(raw, dict, where)
    if not _expect(raw.get("enabled", True), bool, where + ".enabled"):
        return None
    kind = _expect(raw.get("type", ""), str, where + ".type")
    if kind not in AGENT_TYPES:
        raise ConfigError("%s.type 只能是 %s 之一" % (where, "、".join(AGENT_TYPES)))
    aliases = _strings(raw.get("aliases", []), where + ".aliases")
    for alias in aliases:
        if len(alias) > MAX_ALIAS_CHARS or alias != alias.strip():
            raise ConfigError(
                "%s.aliases 里的叫法不能超过 %d 个字符，前后不能有空白" % (where, MAX_ALIAS_CHARS)
            )
    common = dict(
        name=name,
        type=kind,
        description=_expect(raw.get("description", ""), str, where + ".description").strip(),
        aliases=aliases,
        speaks=_expect(raw.get("speaks", kind in SPEAKS_BY_DEFAULT), bool, where + ".speaks"),
        timeout_seconds=_timeout(
            raw.get("timeout_seconds", default_timeout), where + ".timeout_seconds"
        ),
    )
    if kind == "echo":
        return AgentSpec(**common)
    if kind == "remote":
        url = _optional_string(raw.get("url"), where + ".url")
        token = _optional_string(raw.get("token"), where + ".token")
        if url is None or not url.startswith(("http://", "https://")):
            raise ConfigError("%s.url 要写成 http:// 或 https:// 开头的地址" % where)
        if token is None or len(token) < MIN_TOKEN_LENGTH:
            raise ConfigError("%s.token 要填那台 Runtime 的令牌（至少 %d 位）" % (where, MIN_TOKEN_LENGTH))
        if not common["speaks"]:
            raise ConfigError("%s 是另一台小幽，本来就以小幽的身份回话；不要把 speaks 设成 false" % where)
        return AgentSpec(url=url.rstrip("/"), token=token, **common)

    default_command = {"claude_code": ["claude"], "codex": ["codex"]}.get(kind, [])
    command = _strings(raw.get("command", default_command), where + ".command")
    if not command:
        raise ConfigError("%s.command 不能为空" % where)
    workdir = _path(_expect(raw.get("workdir", "workdir"), str, where + ".workdir"), base)
    shared = dict(
        command=command,
        workdir=workdir,
        model=_optional_string(raw.get("model"), where + ".model"),
        extra_args=_strings(raw.get("extra_args", []), where + ".extra_args"),
    )
    if kind == "command":
        if shared["model"] is not None or shared["extra_args"]:
            raise ConfigError("%s 是 command 类型：参数直接写进 command，不用 model 和 extra_args" % where)
        return AgentSpec(**common, **shared)
    if kind == "codex":
        sandbox = _optional_string(raw.get("sandbox", "read-only"), where + ".sandbox")
        if sandbox is not None and sandbox not in CODEX_SANDBOXES:
            raise ConfigError("%s.sandbox 只能是 %s 之一" % (where, "、".join(CODEX_SANDBOXES)))
        codex_home = _optional_string(raw.get("config_dir"), where + ".config_dir")
        codex_home = env.get("XIAOYOU_CODEX_CONFIG_DIR") or codex_home
        return AgentSpec(sandbox=sandbox,
                         config_dir=_path(codex_home, base) if codex_home else None,
                         **common, **shared)

    config_dir = _optional_string(raw.get("config_dir"), where + ".config_dir")
    config_dir = env.get("XIAOYOU_CLAUDE_CONFIG_DIR") or config_dir
    return AgentSpec(
        config_dir=_path(config_dir, base) if config_dir else None,
        permission_mode=_expect(
            raw.get("permission_mode", "dontAsk"), str, where + ".permission_mode"
        ),
        allowed_tools=_strings(raw.get("allowed_tools", []), where + ".allowed_tools"),
        **common,
        **shared,
    )


def _legacy_agents(raw: Dict[str, Any], env: Mapping[str, str], notices: List[str]) -> Dict[str, Any]:
    """把 0.3 及更早的 backend / claude_code / tools 换算成 agents。"""
    backend = _expect(raw.get("backend", "claude_code"), str, "backend")
    if backend not in ("claude_code", "echo"):
        raise ConfigError("backend 只能是 claude_code、echo 之一（新写法见 README 的 agents）")
    claude = dict(_expect(raw.get("claude_code", {}), dict, "claude_code"))
    for index, item in enumerate(_expect(raw.get("tools", []), list, "tools")):
        where = "tools[%d]" % index
        _expect(item, dict, where)
        name = _expect(item.get("name", ""), str, where + ".name").strip()
        if not name:
            raise ConfigError(where + ".name 不能为空")
        if _expect(item.get("enabled", True), bool, where + ".enabled"):
            raise ConfigError(
                "tools 这种写法不再使用：把 %s 登记成 agents 里的一个代理（见 README 的“代理”一节），"
                "由小幽把活转交给它" % name
            )
    if "tools" in raw:
        notices.append("配置里的 tools 已经不用了：别的代理现在登记在 agents 里")
    notices.append("这份配置是旧写法（backend / claude_code），已按只有一个代理来读；新写法见 config.example.json")
    if backend == "echo":
        return {"echo": {"type": "echo", "description": "原样复述，用来测试链路"}}
    claude["type"] = "claude_code"
    claude.setdefault("description", "Claude Code")
    return {"claude": claude}


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
    notices: List[str] = []

    server = _expect(raw.get("server", {}), dict, "server")
    host = env.get("XIAOYOU_HOST") or _expect(server.get("host", "127.0.0.1"), str, "server.host")
    port_text = env.get("XIAOYOU_PORT")
    try:
        port = int(port_text) if port_text else _expect(server.get("port", 8765), int, "server.port")
    except ValueError:
        raise ConfigError("环境变量 XIAOYOU_PORT 应该是数字")
    if not 1 <= port <= 65535:
        raise ConfigError("server.port 应该在 1 到 65535 之间")
    name = (env.get("XIAOYOU_NAME") or _expect(server.get("name", ""), str, "server.name")).strip()
    if not name:
        name = socket.gethostname().split(".")[0] or "runtime"
    if len(name) > 40:
        raise ConfigError("server.name 不能超过 40 个字符")
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

    brief = _expect(raw.get("brief_max_chars", 120), int, "brief_max_chars")
    if not 20 <= brief <= 400:
        raise ConfigError("brief_max_chars 应该在 20 到 400 之间")
    timeout = _timeout(raw.get("turn_timeout_seconds", 600), "turn_timeout_seconds")

    if "agents" in raw:
        for old in ("backend", "claude_code", "tools"):
            if old in raw:
                raise ConfigError("配置里同时有 agents 和旧写法的 %s；只留 agents" % old)
        raw_agents = _expect(raw["agents"], dict, "agents")
    else:
        raw_agents = _legacy_agents(raw, env, notices)
    # 不改配置文件就能把整条链路换成回声来测试；XIAOYOU_BACKEND 是旧名字。
    forced = env.get("XIAOYOU_DEFAULT_AGENT")
    if forced == "echo" or env.get("XIAOYOU_BACKEND") == "echo":
        forced = "echo"
        if "echo" not in raw_agents:
            raw_agents = dict(raw_agents, echo={"type": "echo", "description": "原样复述，用来测试链路"})
    if len(raw_agents) > MAX_AGENTS:
        raise ConfigError("agents 最多登记 %d 个" % MAX_AGENTS)
    agents: List[AgentSpec] = []
    for agent_name, agent_raw in raw_agents.items():
        spec = _agent(agent_name, agent_raw, base, timeout, env)
        if spec is not None:
            agents.append(spec)
    if not agents:
        raise ConfigError("agents 里至少要有一个启用的代理")
    taken: Dict[str, str] = {}
    for spec in agents:
        for label in [spec.name] + spec.aliases:
            owner = taken.setdefault(label.lower(), spec.name)
            if owner != spec.name:
                raise ConfigError("%s 和 %s 都叫 %s；名字和叫法不能重复" % (owner, spec.name, label))
    names = [spec.name for spec in agents]

    xiaoyou = _expect(raw.get("xiaoyou", {}), dict, "xiaoyou")
    default_agent = forced or _optional_string(xiaoyou.get("default_agent"), "xiaoyou.default_agent")
    if default_agent is None:
        default_agent = names[0]
    if default_agent not in names:
        raise ConfigError("默认代理 %s 不在启用的代理里（%s）" % (default_agent, "、".join(names)))
    voice_agent = _optional_string(xiaoyou.get("voice_agent"), "xiaoyou.voice_agent")
    by_name = {spec.name: spec for spec in agents}
    if voice_agent is None:
        # 默认代理自己能说话就用它，否则找第一个能说话的。
        speakers = [spec.name for spec in agents if spec.speaks]
        voice_agent = default_agent if by_name[default_agent].speaks else (
            speakers[0] if speakers else default_agent)
    elif voice_agent not in names:
        raise ConfigError("xiaoyou.voice_agent %s 不在启用的代理里" % voice_agent)
    elif not by_name[voice_agent].speaks:
        raise ConfigError("xiaoyou.voice_agent %s 的 speaks 是 false，没法替小幽说话" % voice_agent)
    max_handoffs = _expect(xiaoyou.get("max_handoffs", 2), int, "xiaoyou.max_handoffs")
    if not 0 <= max_handoffs <= 5:
        raise ConfigError("xiaoyou.max_handoffs 应该在 0 到 5 之间")
    router = _expect(xiaoyou.get("router", {}), dict, "xiaoyou.router")
    router_type = _expect(router.get("type", "mention"), str, "xiaoyou.router.type")
    if router_type not in ROUTER_TYPES:
        raise ConfigError("xiaoyou.router.type 只能是 %s 之一" % "、".join(ROUTER_TYPES))
    router_command = _strings(router.get("command", []), "xiaoyou.router.command")
    if router_type == "command" and not router_command:
        raise ConfigError("xiaoyou.router.type 为 command 时要设置 xiaoyou.router.command")
    router_timeout = _expect(router.get("timeout_seconds", 10), int, "xiaoyou.router.timeout_seconds")
    if not 1 <= router_timeout <= 120:
        raise ConfigError("xiaoyou.router.timeout_seconds 应该在 1 到 120 之间")

    stt = _expect(raw.get("stt", {}), dict, "stt")
    stt_engine = _expect(stt.get("engine", "none"), str, "stt.engine")
    if stt_engine not in STT_ENGINES:
        raise ConfigError("stt.engine 只能是 %s 之一" % "、".join(STT_ENGINES))
    stt_command = _strings(stt.get("command", []), "stt.command")
    stt_model_dir = stt.get("model_dir")
    if stt_engine == "command":
        if not stt_command or not any("{audio}" in part for part in stt_command):
            raise ConfigError("stt.engine 为 command 时，stt.command 里要有一个带 {audio} 的参数")
    if stt_engine == "sense_voice":
        if not isinstance(stt_model_dir, str) or not stt_model_dir.strip():
            raise ConfigError("stt.engine 为 sense_voice 时要设置 stt.model_dir（模型所在的目录）")
    elif stt_model_dir is not None:
        _expect(stt_model_dir, str, "stt.model_dir")
    stt_language = _expect(stt.get("language", "auto"), str, "stt.language")
    if stt_language not in STT_LANGUAGES:
        raise ConfigError("stt.language 只能是 %s 之一" % "、".join(STT_LANGUAGES))
    stt_threads = _expect(stt.get("threads", 2), int, "stt.threads")
    if not 1 <= stt_threads <= 16:
        raise ConfigError("stt.threads 应该在 1 到 16 之间")
    stt_timeout = _expect(stt.get("timeout_seconds", 60), int, "stt.timeout_seconds")
    if not 5 <= stt_timeout <= 600:
        raise ConfigError("stt.timeout_seconds 应该在 5 到 600 之间")

    firmware = _expect(raw.get("firmware", {}), dict, "firmware")
    firmware_repo = _optional_string(firmware.get("repo"), "firmware.repo")
    if firmware_repo is not None and not GITHUB_REPO.match(firmware_repo):
        raise ConfigError("firmware.repo 要写成 GitHub 的“用户名/仓库名”，例如 yanyuliu01/ai-passport")
    firmware_asset = _expect(
        firmware.get("asset", "FoloToy-AI-Passport-full.bin"), str, "firmware.asset").strip()
    firmware_tag_prefix = _expect(
        firmware.get("tag_prefix", "firmware-build-"), str, "firmware.tag_prefix").strip()
    if not firmware_asset or not firmware_tag_prefix:
        raise ConfigError("firmware.asset 和 firmware.tag_prefix 不能是空的")
    firmware_source_dir = _optional_string(firmware.get("source_dir"), "firmware.source_dir")
    firmware_build_command = _strings(firmware.get("build_command", []), "firmware.build_command")
    firmware_build_output = _expect(
        firmware.get("build_output", "build/FoloToy-AI-Passport.bin"), str,
        "firmware.build_output").strip()
    if firmware_build_command and firmware_source_dir is None:
        raise ConfigError("设置了 firmware.build_command 就要同时设置 firmware.source_dir（固件源码在哪）")
    if firmware_build_command and not firmware_build_output:
        raise ConfigError("设置了 firmware.build_command 就要同时设置 firmware.build_output（构建出的文件）")

    return Config(
        name=name,
        host=host,
        port=port,
        token=token,
        state_dir=state_dir,
        persona=persona,
        brief_max_chars=brief,
        agents=agents,
        default_agent=default_agent,
        voice_agent=voice_agent,
        max_handoffs=max_handoffs,
        router_type=router_type,
        router_command=router_command,
        router_timeout_seconds=router_timeout,
        stt_engine=stt_engine,
        stt_command=stt_command,
        stt_model_dir=_path(stt_model_dir, base) if stt_model_dir else None,
        stt_language=stt_language,
        stt_threads=stt_threads,
        stt_timeout_seconds=stt_timeout,
        firmware_repo=firmware_repo,
        firmware_asset=firmware_asset,
        firmware_tag_prefix=firmware_tag_prefix,
        firmware_source_dir=_path(firmware_source_dir, base) if firmware_source_dir else None,
        firmware_build_command=firmware_build_command,
        firmware_build_output=firmware_build_output,
        base_dir=base,
        notices=notices,
    )
