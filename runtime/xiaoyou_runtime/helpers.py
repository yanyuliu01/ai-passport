"""在界面上添加的帮手（电脑客户端 设置 › 帮手 › 添加帮手）。

config.json 里写的帮手照旧；这里记的是之后加的，放在 state/helpers.json，启动时和配置里的
一起建起来，加了、删了都立刻生效，不用重启。每个帮手用和 config.json 一样的规则校验
（config._agent），所以两处写出来的帮手没有差别。

接入模型三种方式：
- 模型服务：Magpie 管供应商和协议转换，帮手只拿本机网关凭据。
- 填 Key：Key 写进 state/helper-secrets/<帮手名>.key（只有本机账号能读），帮手用 env_files
  按服务商自己的环境变量读，Key 不进 helpers.json，也不回给客户端。
- 订阅登录：用这台电脑上这个 harness 自己的登录（比如 codex login、在 pi 里 /login），这里
  只记下是这种方式。

只能删在这里加的帮手；config.json 里的要改配置。
"""

import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config as config_module
from .config import AgentSpec, ConfigError

FORMAT = 1
# 每种 harness：命令、怎么登录（None 是不支持订阅登录）。填 Key 和 harness 无关：任何 harness 都能
# 填任何服务商的 Key，按服务商自己的环境变量交给它；能不能用上看那个 harness 认不认这个变量。
HARNESSES: Dict[str, Dict[str, Any]] = {
    "claude_code": {"label": "Claude Code", "command": "claude", "login": "在这台电脑上用 claude 登录过就行"},
    "codex": {"label": "Codex", "command": "codex", "login": "在这台电脑上运行过 codex login 就行"},
    "dsh": {"label": "DeepSeek（dsh）", "command": "dsh", "login": None},
    "pi": {"label": "pi", "command": "pi", "login": "在 pi 里用 /login 登录过就行"},
}
# 填 Key 时的服务商 → 环境变量；custom 是自己写变量名（env）。
PROVIDERS: Dict[str, str] = {
    "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
    "google": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY",
}
MAX_KEY_CHARS = 4096
MAX_DESCRIPTION_CHARS = 200


class HelperError(Exception):
    """加不了、删不了：消息直接给人看。"""


def options() -> List[Dict[str, Any]]:
    """添加帮手时能选的 harness：这台电脑装了没有、能怎么接入模型。"""
    rows = []
    for harness, row in HARNESSES.items():
        rows.append({"harness": harness, "label": row["label"],
                     "installed": shutil.which(row["command"]) is not None,
                     "ways": ["magpie", "key"] + (["login"] if row["login"] else []),
                     "providers": sorted(PROVIDERS) + ["custom"], "login_hint": row["login"]})
    return rows


class Registry:
    """state/helpers.json：之后加的帮手。线程安全；写文件先写临时文件再换名。"""

    def __init__(self, state_dir: Path, base: Path, reserved: List[str]):
        self._path = Path(state_dir) / "helpers.json"
        self._secrets = Path(state_dir) / "helper-secrets"
        self._base = Path(base)
        self._reserved = set(reserved)  # config.json 里的名字：不能重名，也不能在这里删
        self._lock = threading.Lock()
        self._rows: List[Dict[str, Any]] = self._load()

    def _load(self) -> List[Dict[str, Any]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as error:
            raise HelperError("读不了 %s：%s" % (self._path, error))
        rows = raw.get("helpers") if isinstance(raw, dict) else None
        return [row for row in rows or [] if isinstance(row, dict) and isinstance(row.get("name"), str)]

    def _spec(self, row: Dict[str, Any]) -> AgentSpec:
        raw = {key: value for key, value in row.items()
               if key not in ("name", "access", "provider", "env", "added_at", "gateway_url", "grants", "grant_revision", "model_info")}
        if row.get("access") == "key":
            env = row.get("env") or PROVIDERS.get(row.get("provider", ""), "")
            raw["env_files"] = {env: str(self._secrets / ("%s.key" % row["name"]))}
        elif row.get("access") == "magpie":
            from .helper_models import binding, ModelServiceError
            try:
                raw.update(binding(row, self._path.parent / "helper-config" / row["name"],
                                   self._secrets / ("%s.key" % row["name"])))
            except (ModelServiceError, KeyError) as error:
                raise HelperError("模型服务登记不正确：%s" % error) from None
        try:
            spec = config_module._agent(row["name"], raw, self._base, 0, {})
        except ConfigError as error:
            raise HelperError(str(error))
        if spec is None:
            raise HelperError("这个帮手被停用了")
        return spec

    def specs(self) -> List[AgentSpec]:
        """启动时要建的帮手；和 config.json 重名或者坏掉的跳过（不让一条坏记录挡住启动）。"""
        with self._lock:
            rows = list(self._rows)
        specs = []
        for row in rows:
            if row["name"] in self._reserved:
                continue
            try:
                specs.append(self._spec(row))
            except HelperError:
                continue
        return specs

    def names(self) -> List[str]:
        with self._lock:
            return [row["name"] for row in self._rows]

    def add(self, body: Dict[str, Any]) -> AgentSpec:
        """加一个帮手；返回校验过的配置。Key 只写进本机文件。"""
        name = body.get("name")
        harness = body.get("harness")
        access = body.get("access")
        if not isinstance(name, str) or not config_module.AGENT_NAME.match(name):
            raise HelperError("名字只能用字母、数字、下划线和连字符，以字母或数字开头，最多 24 个字符")
        if harness not in HARNESSES:
            raise HelperError("harness 只能是 %s 之一" % "、".join(HARNESSES))
        row: Dict[str, Any] = {"name": name, "type": harness, "access": access, "added_at": time.time()}
        for key in ("model", "effort"):
            value = body.get(key)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise HelperError("%s 应该是文字" % key)
                row[key] = value.strip()
        description = body.get("description")
        if description is not None:
            if not isinstance(description, str) or len(description) > MAX_DESCRIPTION_CHARS:
                raise HelperError("description 最多 %d 个字" % MAX_DESCRIPTION_CHARS)
            row["description"] = description.strip()
        if "effort" in row:
            row["efforts"] = list(config_module.EFFORTS_BY_TYPE[harness])
        key_value = None
        if access == "magpie":
            from .helper_models import address, ModelServiceError
            try:
                row["gateway_url"] = address(body.get("gateway_url"))
            except ModelServiceError as error:
                raise HelperError(str(error))
            if not row.get("model"):
                raise HelperError("请先从模型服务选择一个模型")
            row["model_info"] = body.get("model_info", {})
            supported = row["model_info"].get("efforts", [])
            row["efforts"] = [level for level in config_module.EFFORTS_BY_TYPE[harness]
                              if level in supported or (level == "off" and "none" in supported)]
            if row.get("effort") and row["effort"] not in row["efforts"]:
                raise HelperError("这个模型没有提供所选的努力程度，请刷新模型列表")
            key_value = body.get("key", "magpie")
            if not isinstance(key_value, str) or not key_value.strip() or len(key_value) > MAX_KEY_CHARS \
                    or any(ch in key_value for ch in "\r\n"):
                raise HelperError("模型服务的访问凭据不正确")
        elif access == "key":
            provider = body.get("provider")
            if provider == "custom":
                env = body.get("env")
                if not isinstance(env, str) or not config_module.ENV_NAME.match(env):
                    raise HelperError("自定义服务商要写环境变量名（字母、数字、下划线，不以数字开头）")
                row["env"] = env
            elif provider not in PROVIDERS:
                raise HelperError("服务商只能是 %s，或者 custom（自己写环境变量名）" % "、".join(PROVIDERS))
            key_value = body.get("key")
            if not isinstance(key_value, str) or not key_value.strip() or len(key_value) > MAX_KEY_CHARS:
                raise HelperError("要填 API Key")
            row["provider"] = provider
        elif access == "login":
            if not HARNESSES[harness]["login"]:
                raise HelperError("%s 不支持订阅登录，要填 Key" % HARNESSES[harness]["label"])
        else:
            raise HelperError("access 只能是 magpie（模型服务）、key（填 Key）或 login（订阅登录）")
        with self._lock:
            if name in self._reserved or any(item["name"] == name for item in self._rows):
                raise HelperError("已经有叫 %s 的帮手了" % name)
            spec = self._spec(row)  # 先校验，再写任何文件
            if key_value is not None:
                self._write_secret(name, key_value.strip())
            self._rows.append(row)
            try:
                self._save()
            except OSError:
                self._rows.pop()
                self._remove_secret(name)
                raise
        return spec

    def remove(self, name: str) -> None:
        with self._lock:
            if name in self._reserved:
                raise HelperError("%s 写在 config.json 里，要改配置才能删" % name)
            rows = [row for row in self._rows if row["name"] != name]
            if len(rows) == len(self._rows):
                raise HelperError("没有叫 %s 的帮手" % name)
            previous, self._rows = self._rows, rows
            try:
                self._save()
            except OSError:
                self._rows = previous
                raise
            self._remove_secret(name)

    def grants(self, name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = next((item for item in self._rows if item["name"] == name), None)
            return dict(row["grants"]) if row is not None and "grants" in row else None

    def set_grants(self, name: str, grants: Dict[str, Any]) -> None:
        with self._lock:
            index = next((i for i, row in enumerate(self._rows) if row["name"] == name), None)
            if index is None:
                raise HelperError("没有这个界面上添加的帮手")
            previous = self._rows[index]
            self._rows[index] = dict(previous, grants=grants, grant_revision=previous.get("grant_revision", 0) + 1)
            try:
                self._save()
            except OSError:
                self._rows[index] = previous
                raise

    def policy(self, name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = next((item for item in self._rows if item["name"] == name), None)
            if row is None:
                return None
            return {"grants": dict(row.get("grants", {})), "revision": row.get("grant_revision", 0)}

    def _write_secret(self, name: str, value: str) -> None:
        self._secrets.mkdir(parents=True, exist_ok=True)
        path = self._secrets / ("%s.key" % name)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            os.chmod(path, 0o600)

    def _remove_secret(self, name: str) -> None:
        try:
            (self._secrets / ("%s.key" % name)).unlink()
        except OSError:
            pass

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix="helpers-", suffix=".json", dir=str(self._path.parent))
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({"format": FORMAT, "helpers": self._rows}, stream, ensure_ascii=False, indent=1)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self._path)

    def view(self, name: str) -> Optional[Dict[str, Any]]:
        """客户端显示用：不含 Key。"""
        with self._lock:
            row = next((item for item in self._rows if item["name"] == name), None)
        if row is None:
            return None
        return {key: row[key] for key in ("name", "type", "access", "provider", "env", "model", "effort", "gateway_url", "grants",
                                          "description", "added_at") if key in row}
