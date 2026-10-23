"""能力库：这台电脑登记的 MCP 服务、它们的连接，以及哪些帮手能用（见
docs/development/helper-capabilities）。

配置（config.json）：

    "capabilities": {
      "mcp_servers": {
        "notion": {"name": "Notion", "url": "https://mcp.notion.com/mcp", "headers": ["Authorization"]},
        "files": {"name": "本机文件", "command": "npx",
                  "args": ["-y", "@modelcontextprotocol/server-filesystem", "~/notes"]}
      },
      "grants": {"codex": {"notion": false}, "claude": {"files": {"tools": ["read_file"]}}}
    }

连接（请求头、环境变量的值）不写在配置里：放在 state/capability-secrets/<服务编号>.json，
形如 {"headers": {"Authorization": "Bearer …"}, "env": {"TOKEN": "…"}}，只有本机账号能读。
帮手拿不到它们：调用都经过能力网关（入口是 permission_mcp.py，Runtime 这一端是 approvals.Gate 的 /mcp/*）。

授权默认全开：没写 grants 的帮手能用所有启用的服务；写了 false 就收回，写了 tools 就只开放
这几个工具。版本号是配置内容的摘要：改了服务或授权，正在跑的帮手下一次调用就会被拒，
要它重新列工具。
"""

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SERVER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")
HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_SERVERS = 32
MAX_TOOLS_PER_SERVER = 200
MAX_TOOL_NAME = 64
# 工具列表缓存多久；改了配置（版本变了）立刻作废。
CATALOG_SECONDS = 300


class CapabilityError(Exception):
    """能力库配置有问题，或者一次调用不能做；消息直接给人或帮手看。"""


@dataclass(frozen=True)
class Server:
    id: str
    name: str
    description: str
    # 本机命令（stdio）或网络地址（Streamable HTTP）
    command: List[str] = field(default_factory=list)
    url: Optional[str] = None
    # 连接里要有哪些环境变量 / 请求头（值在连接文件里）
    env_names: List[str] = field(default_factory=list)
    header_names: List[str] = field(default_factory=list)
    enabled: bool = True

    @property
    def version(self) -> str:
        return digest({"command": self.command, "url": self.url, "env": self.env_names,
                       "headers": self.header_names, "enabled": self.enabled})


@dataclass(frozen=True)
class Grant:
    enabled: bool = True
    tools: Optional[List[str]] = None  # None：全部工具

    @property
    def version(self) -> str:
        return digest({"enabled": self.enabled, "tools": self.tools})


@dataclass(frozen=True)
class Settings:
    servers: Dict[str, Server] = field(default_factory=dict)
    grants: Dict[str, Dict[str, Grant]] = field(default_factory=dict)

    def grant(self, helper: str, server: str) -> Grant:
        return self.grants.get(helper, {}).get(server, Grant())


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def parse(raw: Any, helpers: List[str], where: str = "capabilities") -> Settings:
    """读配置里的 capabilities；有问题抛 CapabilityError（config.py 换成 ConfigError）。"""
    if raw is None:
        return Settings()
    if not isinstance(raw, dict) or set(raw) - {"mcp_servers", "grants"}:
        raise CapabilityError("%s 只能写 mcp_servers 和 grants" % where)
    servers_raw = raw.get("mcp_servers", {})
    if not isinstance(servers_raw, dict) or len(servers_raw) > MAX_SERVERS:
        raise CapabilityError("%s.mcp_servers 应该是对象，最多 %d 个服务" % (where, MAX_SERVERS))
    servers: Dict[str, Server] = {}
    for key, item in servers_raw.items():
        at = "%s.mcp_servers.%s" % (where, key)
        if not SERVER_ID.match(key):
            raise CapabilityError("%s：编号只能用小写字母、数字、下划线和连字符，最多 24 个字符" % at)
        if not isinstance(item, dict):
            raise CapabilityError("%s 应该是对象" % at)
        allowed = {"name", "description", "enabled", "url", "headers"} if "url" in item else {
            "name", "description", "enabled", "command", "args", "env"}
        if set(item) - allowed:
            raise CapabilityError("%s 里不认识 %s；网络服务写 url、headers，本机命令写 command、args、env"
                                  % (at, "、".join(sorted(set(item) - allowed))))
        name = item.get("name", key)
        description = item.get("description", "")
        enabled = item.get("enabled", True)
        if not isinstance(name, str) or not name.strip() or not isinstance(description, str) \
                or not isinstance(enabled, bool):
            raise CapabilityError("%s 的 name、description 应该是文字，enabled 应该是 true 或 false" % at)
        if "url" in item:
            url = item["url"]
            if not isinstance(url, str) or not url.startswith(("https://", "http://")):
                raise CapabilityError("%s.url 要写成 http:// 或 https:// 开头的地址" % at)
            headers = _names(item.get("headers", []), HEADER_NAME, at + ".headers")
            servers[key] = Server(key, name.strip(), description, url=url, header_names=headers,
                                  enabled=enabled)
        else:
            command = item.get("command")
            args = item.get("args", [])
            if not isinstance(command, str) or not command.strip() or not isinstance(args, list) \
                    or not all(isinstance(arg, str) for arg in args):
                raise CapabilityError("%s 要有 command（文字），args 是文字列表；网络服务写 url" % at)
            env = _names(item.get("env", []), ENV_NAME, at + ".env")
            servers[key] = Server(key, name.strip(), description, command=[command] + args,
                                  env_names=env, enabled=enabled)
    grants_raw = raw.get("grants", {})
    if not isinstance(grants_raw, dict):
        raise CapabilityError("%s.grants 应该是对象：帮手名 → 服务编号 → false 或 {\"tools\": [...]}" % where)
    grants: Dict[str, Dict[str, Grant]] = {}
    for helper, rows in grants_raw.items():
        at = "%s.grants.%s" % (where, helper)
        if helper not in helpers:
            raise CapabilityError("%s：没有叫 %s 的帮手" % (at, helper))
        if not isinstance(rows, dict):
            raise CapabilityError("%s 应该是对象" % at)
        grants[helper] = {}
        for server, value in rows.items():
            if server not in servers:
                raise CapabilityError("%s.%s：能力库里没有这个服务" % (at, server))
            if isinstance(value, bool):
                grants[helper][server] = Grant(enabled=value)
            elif isinstance(value, dict) and set(value) <= {"tools"} and isinstance(value.get("tools"), list) \
                    and all(isinstance(tool, str) and tool for tool in value["tools"]):
                grants[helper][server] = Grant(tools=list(value["tools"]))
            else:
                raise CapabilityError("%s.%s 写 true、false，或者 {\"tools\": [\"工具名\", ...]}" % (at, server))
    return Settings(servers, grants)


def _names(value: Any, pattern: "re.Pattern[str]", where: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and pattern.match(item) for item in value):
        raise CapabilityError("%s 应该是名字列表（只写名字，值放在连接文件里）" % where)
    return list(value)


def runtime_name(server: str, tool: str, taken: Dict[str, Any]) -> str:
    """帮手看到的工具名：<服务>__<工具>，只留安全字符，最长 64；重名时加短哈希。"""
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", tool).strip("_") or "tool"
    name = ("%s__%s" % (server, base))[:MAX_TOOL_NAME]
    if name in taken:
        suffix = "_" + digest([server, tool])[:6]
        name = name[:MAX_TOOL_NAME - len(suffix)] + suffix
    return name


class Library:
    """这台电脑的能力库：列出帮手能用的工具，替帮手调用。线程安全。"""

    def __init__(self, settings: Settings, secrets_dir: Path,
                 connect: Optional[Callable[..., Any]] = None):
        from . import mcp_client
        self.settings = settings
        self._secrets_dir = Path(secrets_dir)
        self._connect = connect or mcp_client.connect
        self._lock = threading.Lock()
        self._sessions: Dict[str, Any] = {}
        self._catalogs: Dict[str, Tuple[str, float, List[Dict[str, Any]]]] = {}

    def offers(self, helper: str) -> bool:
        """这个帮手有没有可能用到的服务（决定要不要给它注入能力网关入口）。"""
        return any(server.enabled and self.settings.grant(helper, server.id).enabled
                   for server in self.settings.servers.values())

    def connection(self, server: Server) -> Dict[str, Dict[str, str]]:
        """连接文件里的值；缺了要的就报错（帮手看到的是“需要重新连接”）。"""
        path = self._secrets_dir / ("%s.json" % server.id)
        values: Dict[str, Any] = {}
        if server.env_names or server.header_names:
            try:
                values = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise CapabilityError("%s 还没有连接（%s）" % (server.name, path))
            if os.name == "posix" and path.stat().st_mode & 0o077:
                raise CapabilityError("%s 的连接文件别人也能读：chmod 600 %s" % (server.name, path))
        env = values.get("env") if isinstance(values.get("env"), dict) else {}
        headers = values.get("headers") if isinstance(values.get("headers"), dict) else {}
        missing = [name for name in server.env_names if not isinstance(env.get(name), str) or not env[name]]
        missing += [name for name in server.header_names
                    if not isinstance(headers.get(name), str) or not headers[name]]
        if missing:
            raise CapabilityError("%s 的连接缺少 %s" % (server.name, "、".join(missing)))
        return {"env": {key: env[key] for key in server.env_names},
                "headers": {key: headers[key] for key in server.header_names}}

    def _session(self, server: Server) -> Any:
        with self._lock:
            session = self._sessions.get(server.id)
            if session is not None and session.version == server.version and session.alive():
                return session
        connection = self.connection(server)
        session = self._connect(server, connection)
        with self._lock:
            old = self._sessions.pop(server.id, None)
            self._sessions[server.id] = session
        if old is not None:
            old.close()
        return session

    def _catalog(self, server: Server) -> List[Dict[str, Any]]:
        with self._lock:
            cached = self._catalogs.get(server.id)
        if cached and cached[0] == server.version and time.monotonic() - cached[1] < CATALOG_SECONDS:
            return cached[2]
        tools = self._session(server).list_tools()
        if len(tools) > MAX_TOOLS_PER_SERVER:
            raise CapabilityError("%s 有 %d 个工具，超过 %d 个，不登记" % (
                server.name, len(tools), MAX_TOOLS_PER_SERVER))
        with self._lock:
            self._catalogs[server.id] = (server.version, time.monotonic(), tools)
        return tools

    def tools(self, helper: str) -> Dict[str, Any]:
        """这个帮手现在能用的工具，加上用不了的服务和原因。"""
        tools: List[Dict[str, Any]] = []
        unavailable: List[Dict[str, str]] = []
        taken: Dict[str, Any] = {}
        for server in self.settings.servers.values():
            grant = self.settings.grant(helper, server.id)
            if not server.enabled or not grant.enabled:
                continue
            try:
                catalog = self._catalog(server)
            except Exception as error:  # 连不上、缺连接：列出来，让帮手如实说
                unavailable.append({"server": server.id, "name": server.name, "reason": str(error)[:300]})
                continue
            for tool in catalog:
                if grant.tools is not None and tool["name"] not in grant.tools:
                    continue
                name = runtime_name(server.id, tool["name"], taken)
                taken[name] = True
                tools.append({"name": name, "server": server.id, "tool": tool["name"],
                              "description": "[%s] %s" % (server.name, tool.get("description") or ""),
                              "inputSchema": tool.get("inputSchema") or {"type": "object"},
                              "config_version": server.version, "grant_version": grant.version})
        return {"tools": tools, "unavailable": unavailable}

    def call(self, helper: str, server_id: str, tool: str, arguments: Dict[str, Any],
             config_version: str, grant_version: str) -> Dict[str, Any]:
        """替帮手调用一个工具。授权或配置变了就拒绝，要它重新列工具。"""
        server = self.settings.servers.get(server_id)
        grant = self.settings.grant(helper, server_id)
        if server is None or not server.enabled or not grant.enabled \
                or (grant.tools is not None and tool not in grant.tools):
            raise CapabilityError("这个工具已经不给 %s 用了" % helper)
        if server.version != config_version or grant.version != grant_version:
            raise CapabilityError("%s 的配置或授权变了：请重新列一次工具再用" % server.name)
        return self._session(server).call_tool(tool, arguments)

    def close(self) -> None:
        with self._lock:
            sessions, self._sessions = list(self._sessions.values()), {}
        for session in sessions:
            session.close()
