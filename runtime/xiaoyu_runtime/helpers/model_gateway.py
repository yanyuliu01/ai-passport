"""模型网关：Runtime 里所有"直接调一次模型"的地方共用的一层（见 docs/development/auto-iteration）。

Agent gateway 方案（docs/development/agent-gateway）把"提供方/模型"和"harness/CLI"分成两层；
这里是前一层：登记有哪些推理端点（本机的 Ollama、llama.cpp、LM Studio、vLLM，或网上的
DeepSeek、OpenAI 等）、凭据在哪、每个用途按什么顺序选模型、联网花了多少钱。

  - 用途（purpose）选路：配置里 routes 按用途写候选，"iterate.plan" 找不到就退到 "iterate"，
    再退到 "default"。候选按顺序试，前一个连不上或出错就换下一个。
  - 本地优先、省钱：locality 为 local 的端点不花钱；network 的端点只有 network.allowed 打开、
    今天的花费没超过 daily_budget 时才会被选上。
  - 每次调用记一行用量（state/models/usage.jsonl）：用途、端点、模型、token、估算花费、耗时。
    不记提示词和回答原文。
  - harness 也能用同一份登记：binding_env() 给出让 CLI 指向某个端点的环境变量，
    以后 Agent 实例写 provider 名字就行，不必各自再写一份地址和密钥。

只用标准库；协议支持 OpenAI 兼容的 /chat/completions 和 Anthropic 的 /v1/messages。

  python3 -m xiaoyu_runtime models list           有哪些端点、每个用途的候选
  python3 -m xiaoyu_runtime models test [用途]     真的调一次，看走到了哪个端点
  python3 -m xiaoyu_runtime models usage          今天的调用次数和花费
"""

import ipaddress
import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from ..errors import GatewayError

PROTOCOLS = ("openai", "anthropic")
LOCALITIES = ("local", "network")
MAX_PROVIDERS = 16
# 本机端点连不上时，这么久之内不再去试它（直接走下一个候选）。
DOWN_SECONDS = 60.0
ANTHROPIC_VERSION = "2023-06-01"


class GatewayConfigError(ValueError):
    pass


@dataclass
class Provider:
    name: str
    protocol: str
    base_url: str
    locality: str
    key: Optional[str] = None
    models: List[str] = field(default_factory=list)
    # 每百万 token 的价格（输入、输出），单位随主人定；本机端点为 0。
    price_input: float = 0.0
    price_output: float = 0.0
    timeout_seconds: int = 120
    # 原样并进每次请求体的字段，比如 DeepSeek 关掉思考：{"thinking": {"type": "disabled"}}
    extra_body: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Settings:
    providers: Dict[str, Provider] = field(default_factory=dict)
    # 用途 → [(端点, 模型)]
    routes: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)
    network_allowed: bool = False
    daily_budget: float = 0.0

    @property
    def empty(self) -> bool:
        return not self.providers


@dataclass
class Completion:
    text: str
    provider: str
    model: str
    locality: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    milliseconds: int = 0


def infer_locality(base_url: str) -> str:
    """本机、局域网、Tailscale（100.64/10）地址算本地；其余算联网。"""
    host = (urlsplit(base_url).hostname or "").lower()
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".localhost"):
        return "local"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "network"
    if address.is_loopback or address.is_private or address in ipaddress.ip_network("100.64.0.0/10"):
        return "local"
    return "network"


def _key(raw: Dict[str, Any], base: Path, env: Mapping[str, str], where: str) -> Optional[str]:
    key_file, key_env = raw.get("key_file"), raw.get("key_env")
    if key_file is not None and key_env is not None:
        raise GatewayConfigError("%s 的 key_file 和 key_env 只能留一个" % where)
    if key_env is not None:
        if not isinstance(key_env, str) or not key_env:
            raise GatewayConfigError("%s.key_env 要写环境变量名" % where)
        return env.get(key_env) or None
    if key_file is not None:
        if not isinstance(key_file, str) or not key_file:
            raise GatewayConfigError("%s.key_file 要写文件路径" % where)
        path = Path(os.path.expanduser(key_file))
        path = path if path.is_absolute() else base / path
        try:
            return path.read_text(encoding="utf-8").strip() or None
        except OSError as error:
            raise GatewayConfigError("读不到 %s.key_file %s：%s" % (where, path, error))
    return None


def _price(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise GatewayConfigError("%s 应该是不小于 0 的数" % where)
    return float(value)


def parse(raw: Any, base: Path = Path("."), env: Optional[Mapping[str, str]] = None) -> Settings:
    """读配置里的 models 一节；没有这一节时是空的网关（用到模型的步骤会跳过）。"""
    env = os.environ if env is None else env
    if raw is None:
        return Settings()
    if not isinstance(raw, dict):
        raise GatewayConfigError("models 应该是一个对象")
    providers_raw = raw.get("providers", {})
    if not isinstance(providers_raw, dict) or len(providers_raw) > MAX_PROVIDERS:
        raise GatewayConfigError("models.providers 应该是对象，最多 %d 个" % MAX_PROVIDERS)
    providers: Dict[str, Provider] = {}
    for name, item in providers_raw.items():
        where = "models.providers.%s" % name
        if not isinstance(item, dict):
            raise GatewayConfigError("%s 应该是一个对象" % where)
        if not isinstance(name, str) or not name or "/" in name or len(name) > 32:
            raise GatewayConfigError("%s：名字不能带 /，最多 32 个字符" % where)
        protocol = item.get("protocol", "openai")
        if protocol not in PROTOCOLS:
            raise GatewayConfigError("%s.protocol 只能是 %s 之一" % (where, "、".join(PROTOCOLS)))
        base_url = item.get("base_url")
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            raise GatewayConfigError("%s.base_url 要写成 http:// 或 https:// 开头的地址" % where)
        locality = item.get("locality") or infer_locality(base_url)
        if locality not in LOCALITIES:
            raise GatewayConfigError("%s.locality 只能是 local 或 network" % where)
        models = item.get("models", [])
        if not isinstance(models, list) or not all(isinstance(m, str) and m for m in models):
            raise GatewayConfigError("%s.models 应该是模型名的列表" % where)
        price = item.get("price", {})
        if not isinstance(price, dict):
            raise GatewayConfigError("%s.price 应该是 {\"input\", \"output\"}" % where)
        extra_body = item.get("extra_body", {})
        if not isinstance(extra_body, dict):
            raise GatewayConfigError("%s.extra_body 应该是一个对象" % where)
        timeout = item.get("timeout_seconds", 120)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 5 <= timeout <= 1800:
            raise GatewayConfigError("%s.timeout_seconds 应该在 5 到 1800 之间" % where)
        providers[name] = Provider(
            name=name, protocol=protocol, base_url=base_url.rstrip("/"), locality=locality,
            key=_key(item, base, env, where), models=list(models),
            price_input=_price(price.get("input", 0), where + ".price.input"),
            price_output=_price(price.get("output", 0), where + ".price.output"),
            timeout_seconds=timeout, extra_body=dict(extra_body))
    routes_raw = raw.get("routes", {})
    if not isinstance(routes_raw, dict):
        raise GatewayConfigError("models.routes 应该是对象：用途 → [\"端点/模型\", …]")
    routes: Dict[str, List[Tuple[str, str]]] = {}
    for purpose, candidates in routes_raw.items():
        if isinstance(candidates, str):
            candidates = [candidates]
        if not isinstance(candidates, list) or not candidates:
            raise GatewayConfigError("models.routes.%s 应该是 [\"端点/模型\", …]" % purpose)
        parsed = []
        for candidate in candidates:
            if not isinstance(candidate, str) or "/" not in candidate:
                raise GatewayConfigError("models.routes.%s 的候选要写成 端点/模型" % purpose)
            provider, model = candidate.split("/", 1)
            if provider not in providers or not model:
                raise GatewayConfigError("models.routes.%s：没有登记端点 %s" % (purpose, provider))
            parsed.append((provider, model))
        routes[purpose] = parsed
    network = raw.get("network", {})
    if not isinstance(network, dict):
        raise GatewayConfigError("models.network 应该是 {\"allowed\", \"daily_budget\"}")
    allowed = network.get("allowed", False)
    if not isinstance(allowed, bool):
        raise GatewayConfigError("models.network.allowed 应该是 true 或 false")
    return Settings(providers=providers, routes=routes, network_allowed=allowed,
                    daily_budget=_price(network.get("daily_budget", 0), "models.network.daily_budget"))


def binding_env(provider: Provider) -> Dict[str, str]:
    """让一个 harness/CLI 指向这个端点的环境变量（给 Agent 实例用；不含模型名）。"""
    if provider.protocol == "anthropic":
        env = {"ANTHROPIC_BASE_URL": provider.base_url}
        if provider.key:
            env["ANTHROPIC_AUTH_TOKEN"] = provider.key
        return env
    env = {"OPENAI_BASE_URL": provider.base_url}
    env["OPENAI_API_KEY"] = provider.key or "local"
    return env


Transport = Callable[[str, Dict[str, str], bytes, int], Dict[str, Any]]


def _http(url: str, headers: Dict[str, str], body: bytes, timeout: int) -> Dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read(400).decode("utf-8", "replace")
        raise GatewayError("HTTP %d：%s" % (error.code, detail))
    except (urllib.error.URLError, OSError) as error:
        raise ConnectionError(str(getattr(error, "reason", error)))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GatewayError("返回的不是 JSON")


class Gateway:
    def __init__(self, settings: Settings, state_dir: Optional[Path] = None,
                 transport: Transport = _http, clock: Callable[[], float] = time.time):
        self.settings = settings
        self._ledger = Path(state_dir) / "models" / "usage.jsonl" if state_dir else None
        self._transport = transport
        self._clock = clock
        self._down: Dict[str, float] = {}
        self._lock = threading.Lock()

    # ---- 选路 ----

    def candidates(self, purpose: str) -> List[Tuple[str, str]]:
        parts = purpose.split(".")
        for size in range(len(parts), 0, -1):
            found = self.settings.routes.get(".".join(parts[:size]))
            if found:
                return list(found)
        return list(self.settings.routes.get("default", []))

    def available(self, purpose: str) -> bool:
        return any(self._usable(self.settings.providers[name])[0] for name, _ in self.candidates(purpose))

    def _usable(self, provider: Provider) -> Tuple[bool, str]:
        if provider.locality == "network":
            if not self.settings.network_allowed:
                return False, "没打开联网模型（models.network.allowed）"
            if self.spent_today() >= self.settings.daily_budget:
                return False, "今天联网模型的花费到上限了"
        with self._lock:
            until = self._down.get(provider.name, 0.0)
        if until > self._clock():
            return False, "刚才连不上，稍后再试"
        return True, ""

    # ---- 调用 ----

    def complete(self, purpose: str, messages: List[Dict[str, str]], max_tokens: int = 1024,
                 temperature: float = 0.2, json_mode: bool = False) -> Completion:
        reasons = []
        for name, model in self.candidates(purpose):
            provider = self.settings.providers[name]
            usable, why = self._usable(provider)
            if not usable:
                reasons.append("%s/%s：%s" % (name, model, why))
                continue
            started = self._clock()
            try:
                result = self._call(provider, model, messages, max_tokens, temperature, json_mode)
            except ConnectionError as error:
                with self._lock:
                    self._down[name] = self._clock() + DOWN_SECONDS
                reasons.append("%s/%s：连不上（%s）" % (name, model, error))
                self._record(purpose, provider, model, 0, 0, started, False)
                continue
            except GatewayError as error:
                reasons.append("%s/%s：%s" % (name, model, error))
                self._record(purpose, provider, model, 0, 0, started, False)
                continue
            text, tokens_in, tokens_out = result
            completion = Completion(
                text=text, provider=name, model=model, locality=provider.locality,
                input_tokens=tokens_in, output_tokens=tokens_out,
                cost=self._cost(provider, tokens_in, tokens_out),
                milliseconds=int((self._clock() - started) * 1000))
            self._record(purpose, provider, model, tokens_in, tokens_out, started, True)
            return completion
        if not reasons:
            raise GatewayError("用途 %s 没有配置候选模型（models.routes）" % purpose)
        raise GatewayError("没有可用的模型：" + "；".join(reasons))

    def _call(self, provider: Provider, model: str, messages: List[Dict[str, str]],
              max_tokens: int, temperature: float, json_mode: bool) -> Tuple[str, int, int]:
        headers = {"Content-Type": "application/json"}
        if provider.protocol == "anthropic":
            system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
            payload: Dict[str, Any] = {
                "model": model, "max_tokens": max_tokens, "temperature": temperature,
                "messages": [m for m in messages if m["role"] != "system"]}
            if system:
                payload["system"] = system
            payload.update(provider.extra_body)
            headers["anthropic-version"] = ANTHROPIC_VERSION
            if provider.key:
                headers["x-api-key"] = provider.key
            data = self._transport(provider.base_url + "/v1/messages", headers,
                                   json.dumps(payload).encode("utf-8"), provider.timeout_seconds)
            try:
                text = "".join(part.get("text", "") for part in data["content"] if part.get("type") == "text")
            except (KeyError, TypeError, AttributeError):
                raise GatewayError("返回里没有 content")
            usage = data.get("usage") or {}
            return text, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens,
                   "temperature": temperature, "stream": False}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        payload.update(provider.extra_body)
        if provider.key:
            headers["Authorization"] = "Bearer " + provider.key
        data = self._transport(provider.base_url + "/chat/completions", headers,
                               json.dumps(payload).encode("utf-8"), provider.timeout_seconds)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise GatewayError("返回里没有 choices")
        usage = data.get("usage") or {}
        return text, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)

    def complete_json(self, purpose: str, messages: List[Dict[str, str]],
                      check: Callable[[Any], Optional[str]], attempts: int = 3,
                      max_tokens: int = 1024) -> Tuple[Any, Completion]:
        """要一个 JSON 回答；check 返回 None 表示合格，否则是要告诉模型的问题，带着问题重问。"""
        conversation = list(messages)
        problem = "没有尝试"
        for _ in range(max(1, attempts)):
            completion = self.complete(purpose, conversation, max_tokens=max_tokens, json_mode=True)
            value, problem = _parse_json(completion.text)
            if problem is None:
                problem = check(value)
                if problem is None:
                    return value, completion
            conversation = conversation + [
                {"role": "assistant", "content": completion.text[:4000]},
                {"role": "user", "content": "上面的回答不合格：%s。只输出修正后的 JSON，不要别的文字。" % problem}]
        raise GatewayError("模型几次都没给出合格的 JSON：%s" % problem)

    # ---- 用量 ----

    @staticmethod
    def _cost(provider: Provider, tokens_in: int, tokens_out: int) -> float:
        return (tokens_in * provider.price_input + tokens_out * provider.price_output) / 1_000_000

    def _record(self, purpose: str, provider: Provider, model: str, tokens_in: int, tokens_out: int,
                started: float, ok: bool) -> None:
        if self._ledger is None:
            return
        line = {"at": round(started, 3), "purpose": purpose, "provider": provider.name, "model": model,
                "locality": provider.locality, "input_tokens": tokens_in, "output_tokens": tokens_out,
                "cost": round(self._cost(provider, tokens_in, tokens_out), 6), "ok": ok,
                "ms": int((self._clock() - started) * 1000)}
        with self._lock:
            self._ledger.parent.mkdir(parents=True, exist_ok=True)
            with self._ledger.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")

    def usage(self, since: float) -> List[Dict[str, Any]]:
        if self._ledger is None or not self._ledger.exists():
            return []
        rows = []
        for line in self._ledger.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("at", 0) >= since:
                rows.append(row)
        return rows

    def spent_today(self) -> float:
        now = time.localtime(self._clock())
        midnight = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))
        return sum(row.get("cost", 0) for row in self.usage(midnight) if row.get("locality") == "network")


def _parse_json(text: str) -> Tuple[Any, Optional[str]]:
    """小模型常在 JSON 外面包一层 ```json 或说明文字：取第一个 { 到最后一个 }。"""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None, "回答里没有 JSON 对象"
    try:
        return json.loads(text[start:end + 1]), None
    except json.JSONDecodeError as error:
        return None, "JSON 写错了（%s）" % error.msg


def from_config(config: Any) -> Gateway:
    return Gateway(getattr(config, "models", None) or Settings(), getattr(config, "state_dir", None))


# ---- 命令行 ----

def add_arguments(subparsers: Any) -> None:
    parser = subparsers.add_parser("models", help="模型网关：端点、选路、用量")
    actions = parser.add_subparsers(dest="action", metavar="动作")
    actions.add_parser("list", help="登记的端点和每个用途的候选")
    test = actions.add_parser("test", help="真的调一次，看走到了哪个端点")
    test.add_argument("purpose", nargs="?", default="default")
    actions.add_parser("usage", help="今天的调用次数和花费")


def run(config: Any, args: Any) -> int:
    gateway = from_config(config)
    settings = gateway.settings
    if args.action in (None, "list"):
        if settings.empty:
            print("没有配置 models.providers：用到模型的步骤会跳过。")
            return 0
        for provider in settings.providers.values():
            print("%-12s %-9s %-7s %s%s" % (provider.name, provider.protocol, provider.locality,
                                            provider.base_url, "（有密钥）" if provider.key else ""))
        print("联网模型：%s，每天上限 %.2f，今天已花 %.4f" % (
            "允许" if settings.network_allowed else "不允许", settings.daily_budget, gateway.spent_today()))
        for purpose, candidates in sorted(settings.routes.items()):
            print("  %-20s %s" % (purpose, " → ".join("%s/%s" % pair for pair in candidates)))
        return 0
    if args.action == "test":
        try:
            completion = gateway.complete(args.purpose, [{"role": "user", "content": "只回答一个字：好"}],
                                          max_tokens=16)
        except GatewayError as error:
            print("失败：%s" % error)
            return 1
        print("%s/%s（%s）%d ms：%s" % (completion.provider, completion.model, completion.locality,
                                       completion.milliseconds, completion.text.strip()[:80]))
        return 0
    if args.action == "usage":
        now = time.localtime()
        midnight = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))
        rows = gateway.usage(midnight)
        by: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for row in rows:
            by.setdefault((row["provider"], row["model"]), []).append(row)
        for (provider, model), items in sorted(by.items()):
            print("%s/%s：%d 次（失败 %d），输入 %d、输出 %d token，花费 %.4f" % (
                provider, model, len(items), sum(1 for r in items if not r.get("ok")),
                sum(r.get("input_tokens", 0) for r in items), sum(r.get("output_tokens", 0) for r in items),
                sum(r.get("cost", 0) for r in items)))
        if not rows:
            print("今天还没有调用。")
        return 0
    return 2


def conversation_gateway(config):
    """Build the explicitly opted-in, bounded metadata route for the service."""
    settings = config.models or Settings()
    routes = {
        purpose: [(provider, model) for provider, model in candidates if 'astra' not in model.lower()][:2]
        for purpose, candidates in settings.routes.items()
        if purpose == 'conversation' or purpose.startswith('conversation.')
    }
    routes = {key: value for key, value in routes.items() if value}
    providers = {key: replace(value, timeout_seconds=min(value.timeout_seconds, 8))
                 for key, value in settings.providers.items()}
    return Gateway(replace(settings, routes=routes, providers=providers), config.state_dir)
