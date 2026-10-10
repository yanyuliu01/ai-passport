"""读取和校验配置。

配置是一个 JSON 文件；少数敏感或随机器变化的值可以用环境变量覆盖，方便以后放到
虚拟机或容器里运行而不改文件。只用标准库，兼容 Python 3.9。

配置里最重要的一块是 agents：小幽可以把话交给哪些代理。小幽自己不是其中任何一个；
她的人设、对话记录和“交给谁”的判断都在 Runtime 里。0.3 及更早的配置（backend、
claude_code、tools）仍然能读，会被换算成只有一个代理的 agents。
"""

import ipaddress
import json
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple


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
    # 一轮从头到尾最长多久；0 是不限（看得见事件流的代理靠下面两项判断卡没卡住）
    timeout_seconds: int
    # 连续这么久一行事件都没有，就当它可能卡住了，去问主人还等不等；0 是不管
    idle_seconds: int = 600
    # 最后一步是一条还没跑完的命令（编译、跑测试时事件流本来就是安静的）时，放宽到这么久
    idle_command_seconds: int = 1800
    # claude_code / codex / command：要运行的命令
    command: List[str] = field(default_factory=list)
    workdir: Optional[Path] = None
    model: Optional[str] = None
    extra_args: List[str] = field(default_factory=list)
    # claude_code / codex：派活时除了 model 之外还可以换成哪些模型；空的就是不能按次换
    models: List[str] = field(default_factory=list)
    # claude_code / codex：这个帮手平时用的努力程度；None 表示 Runtime 不传（用工具自己的默认）
    effort: Optional[str] = None
    # 派活时可以选的努力程度；空的就是不能选，也不传
    efforts: List[str] = field(default_factory=list)
    # claude_code
    config_dir: Optional[Path] = None
    # 后台做事时的权限模式。manual：该问的都问，问到主人那里
    permission_mode: str = "manual"
    allowed_tools: List[str] = field(default_factory=list)
    # 除了启动目录之外，还让它碰哪些目录
    add_dirs: List[Path] = field(default_factory=list)
    # claude_code：启动命令时额外设置的环境变量（例如换一个兼容 Anthropic 接口的服务）
    env: Dict[str, str] = field(default_factory=dict)
    # claude_code：值从文件里读的环境变量（密钥放在 config.json 之外），每次启动命令时现读
    env_files: Dict[str, Path] = field(default_factory=dict)
    # codex
    sandbox: Optional[str] = None
    # app_server：能来问主人、能中途追加；exec：一次性跑完，不会来问
    codex_mode: str = "app_server"
    # app_server 模式下什么时候来问：untrusted、on-request、never
    approval_policy: str = "on-request"
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
    # 0.4 的设置，现在不用了（帮手在后台做事，不再在一轮里来回转交）；留着只为旧配置能读
    max_handoffs: int
    # 同时在做的事最多几件；0 是不限
    max_parallel: int = 0
    # 小幽这条线上一次调用最长多久：她只负责听懂、马上答、把活派出去
    voice_timeout_seconds: int = 60
    # 问了主人“还等不等”之后，这么久没人答就停掉；0 是一直等
    idle_answer_seconds: int = 1800
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
    # 共享工作区：小幽和别的 agent 共用的记录（Notion）。none 时不写
    workspace_type: str = "none"
    workspace_token: Optional[str] = None
    workspace_bus_database: Optional[str] = None
    workspace_log_database: Optional[str] = None
    # 小幽在共享工作区里署的名字
    workspace_author: str = "xiaoyou"
    workspace_api_base: str = "https://api.notion.com"
    # 用量：要不要主动去查订阅额度（不查时仍然记下帮手干活时自己报出来的），多久查一次
    usage_enabled: bool = True
    usage_refresh_seconds: int = 300
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
CODEX_MODES = ("app_server", "exec")
CODEX_APPROVAL_POLICIES = ("untrusted", "on-request", "never")
WORKSPACE_TYPES = ("none", "notion")
# 努力程度，从省到费。各工具认的不一样：Claude Code 没有 minimal，Codex 没有 max。
EFFORT_LEVELS = ("minimal", "low", "medium", "high", "xhigh", "max")
EFFORTS_BY_TYPE = {
    "claude_code": ("low", "medium", "high", "xhigh", "max"),
    "codex": ("minimal", "low", "medium", "high", "xhigh"),
}
DEFAULT_EFFORTS = ["low", "medium", "high"]
DEFAULT_EFFORT = "medium"
MAX_MODELS = 8
MAX_MODEL_CHARS = 64
NOTION_ID = re.compile(r"^[0-9a-fA-F]{32}$")
STT_ENGINES = ("none", "sense_voice", "command")
STT_LANGUAGES = ("auto", "zh", "en", "ja", "ko", "yue")
GITHUB_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
MIN_TOKEN_LENGTH = 16
PLACEHOLDER_TOKEN = "change-me-to-a-long-random-string"
AGENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,23}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
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


def _env(value: Any, where: str) -> Dict[str, str]:
    _expect(value, dict, where)
    for key, item in value.items():
        if not ENV_NAME.match(key):
            raise ConfigError("%s 里的名字 %r 不像环境变量名" % (where, key))
        if not isinstance(item, str):
            raise ConfigError("%s.%s 应该是字符串" % (where, key))
        if key == "CLAUDE_CONFIG_DIR":
            raise ConfigError("%s 里不要写 CLAUDE_CONFIG_DIR；用这个代理的 config_dir" % where)
    return dict(value)


def _env_files(value: Any, env: Dict[str, str], base: Path, where: str) -> Dict[str, Path]:
    _expect(value, dict, where)
    for key, item in value.items():
        if not ENV_NAME.match(key):
            raise ConfigError("%s 里的名字 %r 不像环境变量名" % (where, key))
        if not isinstance(item, str) or not item.strip():
            raise ConfigError("%s.%s 应该是文件路径" % (where, key))
        if key == "CLAUDE_CONFIG_DIR":
            raise ConfigError("%s 里不要写 CLAUDE_CONFIG_DIR；用这个代理的 config_dir" % where)
        if key in env:
            raise ConfigError("%s 和 env 里都有 %s；只留一处" % (where, key))
    return {key: _path(item.strip(), base) for key, item in value.items()}


def _path(value: str, base: Path) -> Path:
    path = Path(os.path.expanduser(value))
    return path if path.is_absolute() else (base / path).resolve()


MAX_TIMEOUT_SECONDS = 86400


def _timeout(value: Any, where: str, least: int = 10) -> int:
    """一个时限：0 是不限，否则在 least 到一天之间。"""
    _expect(value, int, where)
    if value != 0 and not least <= value <= MAX_TIMEOUT_SECONDS:
        raise ConfigError("%s 应该是 0（不限），或者在 %d 到 %d 之间" % (
            where, least, MAX_TIMEOUT_SECONDS))
    return value


def _idle(raw: Dict[str, Any], where: str, default: Tuple[int, int]) -> Tuple[int, int]:
    """没动静多久算卡住：（平时，命令在跑时）。"""
    prefix = where + "." if where else ""
    idle = _timeout(raw.get("idle_timeout_seconds", default[0]),
                    prefix + "idle_timeout_seconds", 30)
    command = _timeout(raw.get("idle_timeout_command_seconds", default[1]),
                       prefix + "idle_timeout_command_seconds", 30)
    if idle and command and command < idle:
        raise ConfigError("%sidle_timeout_command_seconds 不能比 %sidle_timeout_seconds 小：命令在跑时"
                          "事件流本来就安静，应该等得更久" % (prefix, prefix))
    return idle, command


def _agent(name: str, raw: Any, base: Path, default_timeout: int,
           env: Mapping[str, str], default_idle: Tuple[int, int] = (600, 1800)) -> Optional[AgentSpec]:
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
    idle, idle_command = _idle(raw, where, default_idle)
    common = dict(
        idle_seconds=idle,
        idle_command_seconds=idle_command,
        name=name,
        type=kind,
        description=_expect(raw.get("description", ""), str, where + ".description").strip(),
        aliases=aliases,
        speaks=_expect(raw.get("speaks", kind in SPEAKS_BY_DEFAULT), bool, where + ".speaks"),
        timeout_seconds=_timeout(
            raw.get("timeout_seconds", default_timeout), where + ".timeout_seconds"
        ),
    )
    if kind in ("echo", "remote"):
        for key in ("effort", "efforts", "models"):
            if key in raw:
                raise ConfigError("%s 是 %s 类型：没有 %s 这一项" % (where, kind, key))
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
        for key in ("effort", "efforts", "models"):
            if key in raw:
                raise ConfigError("%s 是 command 类型：没有 %s，参数直接写进 command" % (where, key))
        return AgentSpec(**common, **shared)
    if kind == "codex":
        sandbox = _optional_string(raw.get("sandbox", "read-only"), where + ".sandbox")
        if sandbox is not None and sandbox not in CODEX_SANDBOXES:
            raise ConfigError("%s.sandbox 只能是 %s 之一" % (where, "、".join(CODEX_SANDBOXES)))
        mode = _expect(raw.get("mode", "app_server"), str, where + ".mode")
        if mode not in CODEX_MODES:
            raise ConfigError("%s.mode 只能是 %s 之一" % (where, "、".join(CODEX_MODES)))
        policy = _expect(raw.get("approval_policy", "on-request"), str, where + ".approval_policy")
        if policy not in CODEX_APPROVAL_POLICIES:
            raise ConfigError("%s.approval_policy 只能是 %s 之一" % (
                where, "、".join(CODEX_APPROVAL_POLICIES)))
        codex_home = _optional_string(raw.get("config_dir"), where + ".config_dir")
        codex_home = env.get("XIAOYOU_CODEX_CONFIG_DIR") or codex_home
        return AgentSpec(sandbox=sandbox, codex_mode=mode, approval_policy=policy,
                         config_dir=_path(codex_home, base) if codex_home else None,
                         **_tiers(raw, kind, where, shared["model"], shared["extra_args"], False),
                         **common, **shared)

    config_dir = _optional_string(raw.get("config_dir"), where + ".config_dir")
    config_dir = env.get("XIAOYOU_CLAUDE_CONFIG_DIR") or config_dir
    extra_env = _env(raw.get("env", {}), where + ".env")
    env_files = _env_files(raw.get("env_files", {}), extra_env, base, where + ".env_files")
    elsewhere = "ANTHROPIC_BASE_URL" in extra_env or "ANTHROPIC_BASE_URL" in env_files
    return AgentSpec(
        config_dir=_path(config_dir, base) if config_dir else None,
        permission_mode=_expect(
            raw.get("permission_mode", "manual"), str, where + ".permission_mode"
        ),
        add_dirs=[_path(folder, base)
                  for folder in _strings(raw.get("add_dirs", ["~"]), where + ".add_dirs")],
        allowed_tools=_strings(raw.get("allowed_tools", []), where + ".allowed_tools"),
        env=extra_env,
        env_files=env_files,
        **_tiers(raw, kind, where, shared["model"], shared["extra_args"], elsewhere),
        **common,
        **shared,
    )


def _tiers(raw: Dict[str, Any], kind: str, where: str, model: Optional[str],
           extra_args: List[str], elsewhere: bool) -> Dict[str, Any]:
    """读一个帮手的档位：平时的努力程度、派活时可以选的几档、可以换的模型。

    elsewhere 表示这个 Claude Code 帮手指到了别的服务（比如 DeepSeek）：那边认不认努力程度
    不一定，所以不写就不传；自己写了 efforts 才传。
    """
    allowed = EFFORTS_BY_TYPE[kind]
    if "efforts" in raw:
        efforts = _strings(raw["efforts"], where + ".efforts")
    else:
        efforts = [] if elsewhere else list(DEFAULT_EFFORTS)
    for level in efforts:
        if level not in allowed:
            raise ConfigError("%s.efforts 里的 %s 不行：%s 类型只能是 %s" % (
                where, level, kind, "、".join(allowed)))
    if len(set(efforts)) != len(efforts):
        raise ConfigError("%s.efforts 里有重复的" % where)
    # 按从省到费排好：提示和界面里都是这个顺序。
    efforts = [level for level in EFFORT_LEVELS if level in efforts]
    effort = _optional_string(raw.get("effort"), where + ".effort")
    if not efforts:
        if effort is not None:
            raise ConfigError("%s.efforts 是空的（不传努力程度），就不要再写 effort" % where)
    else:
        if effort is None:
            effort = DEFAULT_EFFORT if DEFAULT_EFFORT in efforts else efforts[0]
        if effort not in efforts:
            raise ConfigError("%s.effort 是 %s，但它不在 efforts（%s）里" % (
                where, effort, "、".join(efforts)))
        for argument in extra_args:
            if argument.split("=", 1)[0] == "--effort" or "model_reasoning_effort" in argument:
                raise ConfigError(
                    "%s.extra_args 里不要再写努力程度（%s）：用这个帮手的 effort；"
                    "不想让 Runtime 传就把 efforts 写成 []" % (where, argument))
    models = _strings(raw.get("models", []), where + ".models")
    if len(models) > MAX_MODELS:
        raise ConfigError("%s.models 最多写 %d 个" % (where, MAX_MODELS))
    for name in models:
        if name != name.strip() or len(name) > MAX_MODEL_CHARS or any(ch.isspace() for ch in name):
            raise ConfigError("%s.models 里的模型名不能有空白，最多 %d 个字符" % (
                where, MAX_MODEL_CHARS))
    if len(set(models)) != len(models):
        raise ConfigError("%s.models 里有重复的" % where)
    return dict(effort=effort, efforts=efforts, models=[name for name in models if name != model])


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


# Tailscale 给每台机器的地址都在这一段里（100.64.0.0/10）。
TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
# Tailscale 自己的服务地址：只用来问系统“去那里走哪个本机地址”，不会真的发包。
TAILSCALE_PROBE = "100.100.100.100"


def _route_source(target: str) -> str:
    """去 target 时系统会用的本机地址。UDP 的 connect 只查路由，不发任何东西。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((target, 9))
        return probe.getsockname()[0]


def tailscale_address(source: Any = None) -> str:
    """这台机器在 Tailscale 里的地址（100.x.y.z）。server.host 写 tailscale 时用它来监听，
    地址变了也不用改配置。Tailscale 没开时照实报错，不悄悄退回别的地址。"""
    address = ""
    try:
        address = (source or _route_source)(TAILSCALE_PROBE)
        inside = ipaddress.ip_address(address) in TAILSCALE_NET
    except (OSError, ValueError):
        inside = False
    if not inside:
        raise ConfigError(
            "server.host 是 tailscale，但没找到这台机器的 Tailscale 地址（100.x.y.z）："
            "先确认 Tailscale 已经打开并登录；或者把 server.host 直接写成要监听的地址")
    return address


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
    if host.strip().lower() == "tailscale":
        host = tailscale_address()
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
    # 一件后台的事从头到尾最长做多久（各代理可以用自己的 timeout_seconds 改）。默认不限：
    # 长任务按总时长一刀切不合理，卡没卡住看的是多久没动静。
    timeout = _timeout(raw.get("turn_timeout_seconds", 0), "turn_timeout_seconds")
    idle = _idle(raw, "", (600, 1800))
    idle_answer = _timeout(raw.get("idle_answer_seconds", 1800), "idle_answer_seconds", 30)

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
        spec = _agent(agent_name, agent_raw, base, timeout, env, idle)
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
    max_parallel = _expect(xiaoyou.get("max_parallel", 0), int, "xiaoyou.max_parallel")
    if not 0 <= max_parallel <= 64:
        raise ConfigError("xiaoyou.max_parallel 应该在 0 到 64 之间（0 是不限）")
    voice_timeout = _expect(
        xiaoyou.get("voice_timeout_seconds", 60), int, "xiaoyou.voice_timeout_seconds")
    if not 5 <= voice_timeout <= 600:
        raise ConfigError("xiaoyou.voice_timeout_seconds 应该在 5 到 600 之间")
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

    workspace = _expect(raw.get("workspace", {}), dict, "workspace")
    workspace_type = _expect(workspace.get("type", "none"), str, "workspace.type")
    if workspace_type not in WORKSPACE_TYPES:
        raise ConfigError("workspace.type 只能是 %s 之一" % "、".join(WORKSPACE_TYPES))
    workspace_token = env.get("XIAOYOU_NOTION_TOKEN") or _optional_string(
        workspace.get("token"), "workspace.token")
    databases: Dict[str, Optional[str]] = {}
    for key in ("bus_database", "log_database"):
        value = _optional_string(workspace.get(key), "workspace." + key)
        if value is not None:
            value = value.replace("-", "")
            if not NOTION_ID.match(value):
                raise ConfigError(
                    "workspace.%s 要写 Notion 数据库的编号（32 位十六进制，链接里那一串）" % key)
        databases[key] = value
    workspace_author = _expect(workspace.get("author", "xiaoyou"), str, "workspace.author").strip()
    if not AGENT_NAME.match(workspace_author):
        raise ConfigError("workspace.author 只能用字母、数字、下划线和连字符，最多 24 个字符")
    workspace_api_base = _expect(
        workspace.get("api_base", "https://api.notion.com"), str, "workspace.api_base")
    if not workspace_api_base.startswith(("http://", "https://")):
        raise ConfigError("workspace.api_base 要写成 http:// 或 https:// 开头的地址")
    usage = _expect(raw.get("usage", {}), dict, "usage")
    usage_enabled = _expect(usage.get("enabled", True), bool, "usage.enabled")
    usage_refresh = _expect(usage.get("refresh_seconds", 300), int, "usage.refresh_seconds")
    if not 60 <= usage_refresh <= 86400:
        raise ConfigError("usage.refresh_seconds 应该在 60 到 86400 之间")
    if workspace_type == "notion":
        if workspace_token is None:
            raise ConfigError(
                "workspace.type 为 notion 时要有 Notion 集成的令牌：设置环境变量 "
                "XIAOYOU_NOTION_TOKEN，或者写在 workspace.token 里")
        if databases["bus_database"] is None:
            raise ConfigError("workspace.type 为 notion 时要设置 workspace.bus_database（总线）")

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
        max_parallel=max_parallel,
        voice_timeout_seconds=voice_timeout,
        idle_answer_seconds=idle_answer,
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
        workspace_type=workspace_type,
        workspace_token=workspace_token,
        workspace_bus_database=databases["bus_database"],
        workspace_log_database=databases["log_database"],
        workspace_author=workspace_author,
        workspace_api_base=workspace_api_base,
        usage_enabled=usage_enabled,
        usage_refresh_seconds=usage_refresh,
        base_dir=base,
        notices=notices,
    )
