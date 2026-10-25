"""帮手这一轮带上的东西：能力网关入口 和 skill（见 docs/development/helper-capabilities）。

能力网关的入口：每一轮注入帮手的唯一一个 MCP 服务（permission_mcp.py，stdio）。它列出这个帮手
被授权的能力库工具，调用时交回网关在 Runtime 的一端核对授权、替帮手去调真正的服务；Claude Code
还多一个权限询问工具 approve。帮手手里只有这一轮的钥匙，拿不到服务的账号和 Key。

| harness     | 能力网关入口                          | skill                                  |
| ----------- | ------------------------------------- | -------------------------------------- |
| claude_code | 写进这一次的 --mcp-config 文件         | 临时拼一个插件目录，--plugin-dir 载入   |
| codex       | -c mcp_servers.xiaoyou.… 覆盖配置      | 还没有按次指定的办法：配置里就拒绝      |
| dsh         | session/new 的 mcpServers（ACP 格式）  | --patch 给 skill-filesystem 加目录      |
| pi          | 只有全局的 pi mcp add：暂不注入        | --skill                                |

只说话的那一次（job.plain）不带这些：那一次不给工具。
"""

import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SERVER = "xiaoyou"
MAX_SKILLS = 32
# 哪些类型能带 skill。不在这里的写了就是配置错误，不悄悄丢掉。
SKILL_TYPES = ("claude_code", "dsh", "pi")
PLUGIN_NAME = "xiaoyou-helper-skills"


def toml(value: Any) -> str:
    """-c 覆盖用的 TOML 值：字符串、字符串数组、字符串表。JSON 的字符串写法 TOML 也认。"""
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ",".join(json.dumps(item) for item in value) + "]"
    return "{" + ",".join("%s=%s" % (key, json.dumps(item)) for key, item in value.items()) + "}"


def xiaoyou_server(gate: Dict[str, str], approve: bool = False) -> Dict[str, Any]:
    """能力网关入口 怎么启动：命令、参数和环境变量（钥匙在环境变量里）。"""
    package_parent = str(Path(__file__).resolve().parents[2])
    env = dict(gate, PYTHONPATH=package_parent)
    if approve:
        env["XIAOYOU_GATE_APPROVE"] = "1"  # 只有 Claude Code 用 approve 当权限询问工具
    return {"command": sys.executable, "args": ["-m", "xiaoyou_runtime.helpers.permission_mcp"], "env": env}


def codex_overrides(gate: Optional[Dict[str, str]]) -> List[str]:
    """Codex 的 -c 覆盖：mcp_servers.xiaoyou.command / args / env。-c 写在子命令前面。"""
    if gate is None:
        return []
    server = xiaoyou_server(gate)
    prefix = "mcp_servers.%s." % SERVER
    return ["-c", prefix + "command=" + toml(server["command"]),
            "-c", prefix + "args=" + toml(server["args"]),
            "-c", prefix + "env=" + toml(server["env"])]


def acp_servers(gate: Optional[Dict[str, str]]) -> List[Dict[str, Any]]:
    """ACP session/new 的 mcpServers：环境变量是 name / value 列表。"""
    if gate is None:
        return []
    server = xiaoyou_server(gate)
    return [{"name": SERVER, "command": server["command"], "args": server["args"],
             "env": [{"name": key, "value": value} for key, value in server["env"].items()]}]


def dsh_skill_patch(spec) -> List[str]:
    """dsh --patch 里给 skill-filesystem 插件加上这个帮手的 skill 目录。"""
    if not spec.skills:
        return []
    return ["- id: skill-filesystem", "  config:", "    customSkillDirs:"] + [
        "      - %s" % json.dumps(str(folder)) for folder in spec.skills]


def missing_skill(spec) -> Optional[str]:
    """启动时的检查：配置里的 skill 目录不在、或者里面没有 SKILL.md。"""
    for folder in spec.skills:
        if not (folder / "SKILL.md").is_file():
            return "%s 的 skill 目录 %s 里没有 SKILL.md" % (spec.name, folder)
    return None


def claude_plugin(spec, folder: Path) -> Optional[Path]:
    """临时拼一个只装着这些 skill 的 Claude Code 插件目录（--plugin-dir 载入）。

    复制而不是链接：Windows 上建链接要管理员权限；skill 一般只有几个小文件。"""
    if not spec.skills:
        return None
    root = folder / PLUGIN_NAME
    if root.exists():
        shutil.rmtree(root)
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": PLUGIN_NAME, "description": "Skills configured for this helper"}),
        encoding="utf-8")
    for skill in spec.skills:
        shutil.copytree(skill, root / "skills" / skill.name)
    return root
