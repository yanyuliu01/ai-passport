"""Magpie model-service bindings for Runtime-owned helper processes.

Adapter shapes follow yetone/magpie (MIT), snapshot
f3eb92924e56605860f6a85a3449247c26c0bd90, internal/agent/{codex,dsh,agents}.go.
See third_party/magpie.LICENSE. Protocol translation and provider credentials
stay in Magpie; this module never edits a user's native agent configuration.
"""

import ipaddress
import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlsplit

DEFAULT_URL = "http://127.0.0.1:3425"
URL_ENV = "XIAOYOU_MODEL_GATEWAY"
KEY_ENV = "XIAOYOU_MODEL_GATEWAY_KEY"
INFO_ENV = "XIAOYOU_MODEL_INFO"
PROVIDER = "xiaoyou_gateway"
MAX_BYTES = 1024 * 1024
TIMEOUT = 3


class ModelServiceError(ValueError):
    pass


def address(value: Any) -> str:
    """A local gateway only; reject redirects, URL credentials and query keys."""
    if not isinstance(value, str) or len(value) > 512:
        raise ModelServiceError("模型服务地址应为本机 HTTP 地址")
    try:
        parsed = urlsplit(value)
        local = ipaddress.ip_address(parsed.hostname or "").is_loopback
        port = parsed.port
    except ValueError:
        local, port = False, None
    if not local or parsed.scheme != "http" or parsed.username or parsed.password or parsed.query \
            or parsed.fragment or parsed.path not in ("", "/", "/v1", "/v1/") or port == 0:
        raise ModelServiceError("模型服务只连接本机地址，例如 http://127.0.0.1:3425")
    return "%s://%s" % (parsed.scheme, parsed.netloc)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ModelServiceError("模型服务重定向了，请填写它的本机监听地址")


def catalog(url: str, key: str = "magpie") -> Dict[str, Any]:
    """Read Magpie's live catalog; no model names or reasoning levels compiled in."""
    base = address(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(base + "/v1/models", headers={"Authorization": "Bearer " + key})
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ModelServiceError("模型服务的列表过大")
        body = json.loads(data.decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise ModelServiceError("模型服务返回 HTTP %d，请检查网关访问凭据" % error.code) from None
    except (OSError, ValueError) as error:
        if isinstance(error, ModelServiceError):
            raise
        raise ModelServiceError("连不上模型服务，请先启动 Magpie 并添加供应商") from None
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ModelServiceError("模型服务返回的列表格式不正确")
    models = []
    seen = set()
    for row in body["data"]:
        name = row.get("id") if isinstance(row, dict) else None
        if not isinstance(name, str) or not name or len(name) > 64 or any(ch.isspace() for ch in name):
            continue
        if name not in seen:
            levels = row.get("reasoning_levels", [])
            if not isinstance(levels, list):
                levels = []
            levels = [level for level in levels if level in ("none", "off", "minimal", "low", "medium", "high", "xhigh", "max")]
            native = row.get("native_endpoints", [])
            if not isinstance(native, list):
                native = []
            modalities = row.get("modalities")
            modalities = modalities if isinstance(modalities, dict) else {}
            api = ("openai-responses" if "/v1/responses" in native else
                   "anthropic-messages" if "/v1/messages" in native else "openai-completions")
            info = {"id": name, "name": row.get("display_name") or row.get("name") or name,
                    "efforts": levels, "api": api, "reasoning": row.get("reasoning") is True,
                    "input": ["text", "image"] if isinstance(modalities.get("input"), list) and "image" in modalities["input"] else ["text"]}
            if not isinstance(info["name"], str):
                info["name"] = name
            for source, target in (("context_window", "contextWindow"), ("max_output_tokens", "maxTokens")):
                value = row.get(source)
                if isinstance(value, int) and 0 < value <= 2000000:
                    info[target] = value
            models.append(info)
            seen.add(name)
    return {"url": base, "models": models}


def binding(row: Dict[str, Any], folder: Path, secret: Path) -> Dict[str, Any]:
    """Build an AgentSpec input; only a gateway token reaches the harness."""
    url = address(row["gateway_url"])
    env = {URL_ENV: url, INFO_ENV: json.dumps(row.get("model_info", {}))}
    files = {KEY_ENV: str(secret)}
    model = row["model"]
    kind = row["type"]
    if kind == "claude_code":
        env.update(ANTHROPIC_BASE_URL=url, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
        files["ANTHROPIC_API_KEY"] = str(secret)
        # A Runtime-owned home also keeps native login and settings separate.
        return {"env": env, "env_files": files, "config_dir": str(folder / "claude")}
    if kind == "pi":
        env["PI_CODING_AGENT_DIR"] = str(folder / "pi")
        return {"env": env, "env_files": files, "model": PROVIDER + "/" + model}
    if kind == "dsh":
        env["DEEPSEEK_BASE_URL"] = url
        files["DEEPSEEK_API_KEY"] = str(secret)
        return {"env": env, "env_files": files, "config_dir": str(folder / "dsh")}
    if kind == "codex":
        return {"env": env, "env_files": files, "config_dir": str(folder / "codex")}
    return {"env": env, "env_files": files}


def codex_overrides(spec: Any) -> List[str]:
    url = spec.env.get(URL_ENV)
    if not url:
        return []
    values = {"model_provider": PROVIDER,
              "model_providers." + PROVIDER: {"name": "Xiaoyou model service", "base_url": url + "/v1",
                                             "wire_api": "responses", "env_key": KEY_ENV,
                                             "requires_openai_auth": False}}
    args = []
    for name, value in values.items():
        if isinstance(value, dict):
            encoded = "{" + ",".join(k + "=" + json.dumps(v) for k, v in value.items()) + "}"
        else:
            encoded = json.dumps(value)
        args.extend(["-c", name + "=" + encoded])
    return args


def prepare_codex(spec: Any) -> None:
    if URL_ENV in spec.env and spec.config_dir is not None:
        spec.config_dir.mkdir(parents=True, exist_ok=True)


def dsh_patch(spec: Any) -> List[str]:
    url = spec.env.get(URL_ENV)
    if not url:
        return []
    info = json.loads(spec.env.get(INFO_ENV, "{}"))
    api = info.get("api", "openai-completions")
    base = url if api == "anthropic-messages" else url + "/v1"
    model = {"id": spec.model, "name": info.get("name", spec.model)}
    model.update({key: info[key] for key in ("contextWindow", "maxTokens", "input") if key in info})
    levels = {("off" if level == "none" else level): level for level in info.get("efforts", [])}
    if len(levels) > 1 or (levels and "off" not in levels):
        model["reasoningEfforts"] = levels
    # dsh >= 0.1.5: llm-pi-ai custom provider, the same shape its Models page writes.
    return ["- id: llm-pi-ai", "  name: '@deepseek-ai/dsh-llm-pi-ai'", "  config:",
            "    providers:", "      " + PROVIDER + ":", "        displayName: Xiaoyou model service",
            "        api: " + api, "        apiKeyEnv: " + KEY_ENV,
            "        baseURL: " + json.dumps(base), "        models: " + json.dumps([model])]


def prepare_pi(spec: Any) -> None:
    if spec.type != "pi" or URL_ENV not in spec.env:
        return
    folder = Path(spec.env["PI_CODING_AGENT_DIR"])
    model = spec.model.split("/", 1)[1]
    info = json.loads(spec.env.get(INFO_ENV, "{}"))
    api = info.get("api", "openai-completions")
    base = spec.env[URL_ENV] + ("" if api == "anthropic-messages" else "/v1")
    entry = {"id": model, "name": info.get("name", model), "reasoning": info.get("reasoning", False)}
    entry.update({key: info[key] for key in ("contextWindow", "maxTokens", "input") if key in info})
    data = {"providers": {PROVIDER: {"baseUrl": base, "api": api,
                                    "apiKey": KEY_ENV, "models": [entry]}}}
    folder.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="models-", suffix=".json", dir=str(folder))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, folder / "models.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
