"""插件：声明、加载、权限。

一个插件是一个字典，放在模块的 PLUGINS 列表里：

    PLUGINS = [{
        "name": "rage_tap", "kind": "detector", "version": 1,
        "consumes": ["app.rage_tap"],      # detector：只收这些类型（以 . 结尾表示前缀）
        "permissions": [],                 # 见 PERMISSIONS
        "run": detect,                     # 各种 kind 的参数见 CALLS
    }]

内置插件在 builtin/；iterate.plugin_dirs 里每个 .py 文件也这样写。同名插件后加载的覆盖先加载的，
所以自己写的可以替换内置的。

权限由 Context 检查：插件只能经 ctx 拿到模型、仓库路径、状态目录。它们和 Runtime 跑在同一个
进程里，这不是安全沙箱，只防"不小心越界"；来路不明的插件不要放进 plugin_dirs。
"""

import importlib.util
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

KINDS = ("source", "detector", "triage", "planner", "implementer", "gate", "policy", "releaser",
         "observer")
PERMISSIONS = ("read_state", "read_repo", "model", "write_worktree", "git_commit", "publish",
               "network")
# 每种插件的 run 怎么调：
CALLS = {
    "source": "run(ctx, cursor) -> (signals, new_cursor)",
    "detector": "run(ctx, signals) -> [Issue]",
    "triage": "run(ctx, issues) -> Issue | None",
    "planner": "run(ctx, issue) -> plan dict",
}


class PluginError(Exception):
    pass


class PermissionDenied(PluginError):
    pass


class Skip(Exception):
    """插件说这一次做不了（比如没有可用的模型），不算出错。"""


@dataclass
class Plugin:
    name: str
    kind: str
    run: Callable[..., Any]
    version: int = 1
    consumes: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    origin: str = "builtin"
    description: str = ""

    def accepts(self, signal_type: str) -> bool:
        if not self.consumes:
            return True
        return any(signal_type.startswith(item) if item.endswith(".") else signal_type == item
                   for item in self.consumes)


def _plugin(raw: Any, origin: str) -> Plugin:
    if not isinstance(raw, dict):
        raise PluginError("%s：PLUGINS 里每一项应该是字典" % origin)
    name, kind, run = raw.get("name"), raw.get("kind"), raw.get("run")
    if not isinstance(name, str) or not name or len(name) > 40:
        raise PluginError("%s：插件要有 name" % origin)
    if kind not in KINDS:
        raise PluginError("%s：%s 的 kind 只能是 %s" % (origin, name, "、".join(KINDS)))
    if not callable(run):
        raise PluginError("%s：%s 没有 run" % (origin, name))
    permissions = list(raw.get("permissions", []))
    for item in permissions:
        if item not in PERMISSIONS:
            raise PluginError("%s：%s 要了不认识的权限 %s" % (origin, name, item))
    return Plugin(name=name, kind=kind, run=run, version=int(raw.get("version", 1)),
                  consumes=list(raw.get("consumes", [])), permissions=permissions, origin=origin,
                  description=str(raw.get("description", "")))


class Registry:
    def __init__(self) -> None:
        self._plugins: Dict[Tuple[str, str], Plugin] = {}
        self.errors: List[str] = []

    def add_module(self, module: Any, origin: str) -> None:
        for raw in getattr(module, "PLUGINS", []):
            plugin = _plugin(raw, origin)
            self._plugins[(plugin.kind, plugin.name)] = plugin

    def load_dir(self, folder: Path) -> None:
        if not folder.is_dir():
            self.errors.append("插件目录不存在：%s" % folder)
            return
        for path in sorted(folder.glob("*.py")):
            name = "xiaoyou_iterate_plugin_%s_%d" % (path.stem, abs(hash(str(path))))
            try:
                spec = importlib.util.spec_from_file_location(name, path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
                self.add_module(module, str(path))
            except Exception as error:  # 坏的插件不影响其余的
                self.errors.append("%s：%s" % (path, error))

    def get(self, kind: str, name: str) -> Plugin:
        plugin = self._plugins.get((kind, name))
        if plugin is None:
            raise PluginError("没有 %s 插件 %s" % (kind, name))
        return plugin

    def all(self) -> List[Plugin]:
        return sorted(self._plugins.values(), key=lambda p: (KINDS.index(p.kind), p.name))


def builtin_registry(extra_dirs: Optional[List[Path]] = None) -> Registry:
    from .builtin import detectors, planner, sources, triage
    registry = Registry()
    for module in (sources, detectors, triage, planner):
        registry.add_module(module, "builtin")
    for folder in extra_dirs or []:
        registry.load_dir(folder)
    return registry


class Context:
    """插件能用的东西。每个插件拿到自己的一份，只开放它声明过的权限。"""

    def __init__(self, plugin: Plugin, settings: Any, state_dir: Path, gateway: Any = None,
                 log: Optional[List[str]] = None, now: Optional[float] = None):
        self.plugin = plugin
        self.settings = settings
        self._state_dir = Path(state_dir)
        self._gateway = gateway
        self._log = log if log is not None else []
        self.now = time.time() if now is None else now

    def _need(self, permission: str) -> None:
        if permission not in self.plugin.permissions:
            raise PermissionDenied("%s 插件 %s 没有声明 %s 权限" % (
                self.plugin.kind, self.plugin.name, permission))

    @property
    def state_dir(self) -> Path:
        self._need("read_state")
        return self._state_dir

    @property
    def repo(self) -> Path:
        self._need("read_repo")
        return self.settings.repo_dir

    def model_available(self, purpose: str) -> bool:
        return ("model" in self.plugin.permissions and self._gateway is not None
                and self._gateway.available(purpose))

    @property
    def gateway(self) -> Any:
        self._need("model")
        if self._gateway is None:
            raise Skip("没有配置模型网关")
        return self._gateway

    def log(self, message: str) -> None:
        self._log.append("[%s/%s] %s" % (self.plugin.kind, self.plugin.name, message))
