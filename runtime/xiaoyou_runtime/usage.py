"""用量和档位：订阅额度还剩多少，每个帮手用的是哪个模型、哪一档。

额度按账号记，不按帮手记：几个帮手用同一个登录（同一个配置目录）时，额度是一份。
只登记订阅登录的账号：Claude Code（没有指到别的服务的）和 Codex。别的帮手没有额度可看，
只记它用的模型。

数从三处来，都不用 Runtime 自己碰任何登录凭据：

  Claude Code 干活时的事件流    rate_limit_event：两个窗口各用了多少、什么时候重置
  Claude Code 的 get_usage      不调用模型的一次询问，和它界面里 /usage 是同一份数
  Codex 的 app-server           account/rateLimits/read，以及干活时的 account/rateLimits/updated

get_usage 在 Claude Code 里标着“实验性”，事件流里的两个窗口标着“内部”：对不上、问不到
都不算错，这个账号就显示成“还不知道”，原因留在 note 里。

记下的东西落在 state/usage.json（里面没有凭据），重启后还在；过了重置时间的数会被清掉。
"""

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import AgentSpec, Config
from .store import _write

WINDOWS = ("5h", "7d")
WINDOW_NAMES = {"5h": "5 小时", "7d": "本周"}
# 剩下不到这么多就算“快用完了”。
WARN_BELOW = 20
# 一件事开始前，手里的数比这新就不再去问。
FRESH_SECONDS = 120
FETCH_SECONDS = 20
EFFORT_NAMES = {"minimal": "最低档", "low": "低档", "medium": "中档", "high": "高档",
                "xhigh": "特高档", "max": "最高档"}
STATE_NAMES = {"ok": "正常", "warn": "快用完了", "out": "用完了", "unknown": "还不知道"}
WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
CLAUDE_KEYS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

Windows = Dict[str, Dict[str, Any]]


def account_of(spec: AgentSpec) -> Optional[Tuple[str, str]]:
    """这个帮手用的订阅账号：（编号，哪一家）。不是订阅登录的返回 None。"""
    if spec.type == "codex":
        return "codex:%s" % (spec.config_dir or "default"), "codex"
    if spec.type != "claude_code":
        return None
    from .claude_gateway import uses_deepseek
    if uses_deepseek(spec) or any(key in spec.env or key in spec.env_files for key in CLAUDE_KEYS):
        return None  # 指到别的服务、或者用密钥：按量付费，没有订阅额度
    return "claude:%s" % (spec.config_dir or "default"), "claude"


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return float(value)


def _left(used_percent: Optional[float]) -> Optional[int]:
    if used_percent is None:
        return None
    return max(0, min(100, 100 - int(round(used_percent))))


def _epoch(value: Any) -> Optional[int]:
    """重置时间 → 秒。认秒、毫秒和 ISO 8601 三种写法。"""
    number = _number(value)
    if number is not None:
        if number > 1e11:
            number /= 1000.0
        return int(number) if number > 0 else None
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return int(parsed.timestamp()) if parsed.tzinfo is not None else None
    return None


def claude_event_windows(info: Any) -> Windows:
    """Claude Code 事件流里 rate_limit_event 的 rate_limit_info → 各个窗口。"""
    if not isinstance(info, dict):
        return {}
    found: Windows = {}
    unified = info.get("unifiedWindows") if isinstance(info.get("unifiedWindows"), dict) else {}
    for key, kind in (("five_hour", "5h"), ("seven_day", "7d")):
        window = unified.get(key)
        if isinstance(window, dict):
            share = _number(window.get("utilization"))
            found[kind] = {"left": _left(share * 100 if share is not None else None),
                           "resets_at": _epoch(window.get("resetsAt"))}
    # 最外面那几项说的是“现在卡着的那个窗口”。
    kind = {"five_hour": "5h", "seven_day": "7d"}.get(info.get("rateLimitType"))
    status = info.get("status")
    if kind is not None:
        window = found.setdefault(kind, {"left": None, "resets_at": None})
        share = _number(info.get("utilization"))
        if window["left"] is None and share is not None:
            window["left"] = _left(share * 100)
        if window["resets_at"] is None:
            window["resets_at"] = _epoch(info.get("resetsAt"))
        if status == "rejected":
            window["left"] = 0
        elif status == "allowed_warning" and window["left"] is None:
            window["flag"] = "warn"
    return found


def claude_usage_windows(answer: Any) -> Tuple[Windows, Optional[str], Optional[str]]:
    """get_usage 的回答 →（各个窗口，订阅类型，问不到的原因）。"""
    if not isinstance(answer, dict):
        return {}, None, "Claude Code 的回答看不懂"
    plan = answer.get("subscription_type") if isinstance(answer.get("subscription_type"), str) else None
    limits = answer.get("rate_limits")
    if not isinstance(limits, dict):
        return {}, plan, ("这个登录不是订阅（或者没有读用量的权限），没有额度可看"
                          if answer.get("rate_limits_available") is False
                          else "Claude Code 这一次没有给出额度")
    found: Windows = {}
    for key, kind in (("five_hour", "5h"), ("seven_day", "7d")):
        window = limits.get(key)
        if isinstance(window, dict):
            found[kind] = {"left": _left(_number(window.get("utilization"))),
                           "resets_at": _epoch(window.get("resets_at"))}
    return found, plan, None


def codex_windows(payload: Any) -> Tuple[Windows, Optional[str]]:
    """Codex 的额度（account/rateLimits/read 的结果，或 updated 通知）→（各个窗口，订阅类型）。"""
    if not isinstance(payload, dict):
        return {}, None
    snapshot = payload.get("rateLimits") if isinstance(payload.get("rateLimits"), dict) else payload
    by_id = payload.get("rateLimitsByLimitId")
    if isinstance(by_id, dict) and isinstance(by_id.get("codex"), dict):
        snapshot = by_id["codex"]
    found: Windows = {}
    for key, fallback in (("primary", "5h"), ("secondary", "7d")):
        window = snapshot.get(key)
        if not isinstance(window, dict) or _number(window.get("usedPercent")) is None:
            continue
        minutes = _number(window.get("windowDurationMins"))
        kind = fallback if minutes is None else ("5h" if minutes < 1440 else "7d")
        found[kind] = {"left": _left(_number(window.get("usedPercent"))),
                       "resets_at": _epoch(window.get("resetsAt"))}
    if snapshot.get("rateLimitReachedType") and found:
        # 它说已经撞上限了：哪个窗口剩得最少就是哪个。
        tightest = min(found, key=lambda kind: found[kind]["left"] if found[kind]["left"] is not None else 101)
        found[tightest]["left"] = 0
    plan = snapshot.get("planType") if isinstance(snapshot.get("planType"), str) else None
    return found, plan


def state_of(windows: List[Dict[str, Any]]) -> str:
    known = [window["left"] for window in windows if window["left"] is not None]
    if any(left <= 0 for left in known):
        return "out"
    if any(left < WARN_BELOW for left in known) or any(window.get("flag") == "warn" for window in windows):
        return "warn"
    return "ok" if known else "unknown"


def clock_text(at: Optional[int], now: float) -> str:
    """重置时间给人看的写法：今天之内只写几点，别的带星期。"""
    if not at:
        return ""
    then, today = time.localtime(at), time.localtime(now)
    hour = time.strftime("%H:%M", then)
    if (then.tm_year, then.tm_yday) == (today.tm_year, today.tm_yday):
        return hour
    return "%s %s" % (WEEKDAYS[then.tm_wday], hour)


def describe_account(account: Dict[str, Any], now: float) -> str:
    """一个账号的额度，一句话：给小幽看，也给命令行看。"""
    if account["state"] == "unknown":
        return "用量还不知道"
    parts = []
    for window in account["windows"]:
        reset = clock_text(window["resets_at"], now)
        reset = "（%s 重置）" % reset if reset else ""
        if window["left"] is None:
            if window.get("flag") == "warn":
                parts.append("%s快用完了%s" % (WINDOW_NAMES[window["kind"]], reset))
            continue
        if window["left"] <= 0:
            parts.append("%s用完了%s" % (WINDOW_NAMES[window["kind"]], reset))
        else:
            parts.append("%s剩 %d%%%s" % (WINDOW_NAMES[window["kind"]], window["left"], reset))
    text = "，".join(parts)
    if account["state"] in ("warn", "out"):
        text += " · %s" % STATE_NAMES[account["state"]]
    return text


def describe_tier(entry: Dict[str, Any]) -> str:
    """一个帮手的模型和档位，一句话。"""
    parts = [entry["model"] or "模型还不知道"]
    if entry["effort"]:
        choices = [EFFORT_NAMES[level][:-1] for level in entry["efforts"]]
        parts.append("平时%s%s" % (
            EFFORT_NAMES[entry["effort"]],
            "，可选 %s" % "/".join(choices) if len(choices) > 1 else ""))
    if entry["models"]:
        parts.append("可换模型 %s" % "/".join(entry["models"]))
    return " · ".join(parts)


class Usage:
    def __init__(self, config: Config, enabled: Optional[bool] = None,
                 clock: Callable[[], float] = time.time,
                 specs: Optional[List[AgentSpec]] = None):
        """enabled 为 False 时不主动去问（不起任何进程），帮手自己报上来的照记。

        specs 是要管的帮手；不给就是配置里启用的那些。"""
        specs = list(config.agents) if specs is None else list(specs)
        self._config = config
        self._enabled = config.usage_enabled if enabled is None else enabled
        self._clock = clock
        self._path = config.state_dir / "usage.json"
        self._changed = threading.Condition()
        self._rev = 0
        self._specs: Dict[str, AgentSpec] = {spec.name: spec for spec in specs}
        self._accounts: Dict[str, Dict[str, Any]] = {}
        self._account_of: Dict[str, str] = {}
        self._models: Dict[str, str] = {}  # 帮手 → 它按平时的配置跑时实际用的模型
        self._running: Dict[str, List[Dict[str, Any]]] = {}  # 帮手 → 正在做的每一轮
        self._fetching: Dict[str, threading.Lock] = {}
        self.fetchers: Dict[str, Callable[[AgentSpec], Tuple[Windows, Optional[str], Optional[str]]]] = {
            "claude": self._fetch_claude, "codex": self._fetch_codex}
        for spec in specs:
            found = account_of(spec)
            if found is None:
                continue
            account = self._accounts.setdefault(found[0], {
                "id": found[0], "kind": found[1], "agents": [], "windows": {}, "source": None,
                "updated_at": 0.0, "checked_at": 0.0, "plan": None, "note": None})
            account["agents"].append(spec.name)
            self._account_of[spec.name] = found[0]
            self._fetching[found[0]] = threading.Lock()
        self._load()

    # ---- 落盘 ----

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return  # 丢了只是要重新问一次
        if not isinstance(raw, dict):
            return
        for saved in raw.get("accounts", []) if isinstance(raw.get("accounts"), list) else []:
            account = self._accounts.get(saved.get("id")) if isinstance(saved, dict) else None
            if account is None or not isinstance(saved.get("windows"), dict):
                continue
            for kind in WINDOWS:
                window = saved["windows"].get(kind)
                if isinstance(window, dict):
                    left, resets = _number(window.get("left")), _number(window.get("resets_at"))
                    account["windows"][kind] = {
                        "left": int(left) if left is not None else None,
                        "resets_at": int(resets) if resets is not None else None}
                    if window.get("flag") == "warn":
                        account["windows"][kind]["flag"] = "warn"
            updated = _number(saved.get("updated_at"))
            account["updated_at"] = updated or 0.0
            for key in ("source", "plan"):
                account[key] = saved.get(key) if isinstance(saved.get(key), str) else None
        models = raw.get("models") if isinstance(raw.get("models"), dict) else {}
        for name, model in models.items():
            if name in self._specs and isinstance(model, str) and model:
                self._models[name] = model

    def _save(self) -> None:
        """调用时已经拿着锁。"""
        try:
            _write(self._path, {
                "accounts": [{key: account[key] for key in (
                    "id", "kind", "windows", "source", "updated_at", "plan")}
                    for account in self._accounts.values()],
                "models": self._models,
            }, indent=1)
        except OSError:
            pass  # 记不下来不妨碍用

    def _touch(self, save: bool = True) -> None:
        """调用时已经拿着锁：有变化了。"""
        self._rev += 1
        if save:
            self._save()
        self._changed.notify_all()

    # ---- 记下看到的 ----

    def _merge(self, account_id: str, windows: Windows, source: str,
               plan: Optional[str] = None) -> None:
        if not windows and plan is None:
            return
        with self._changed:
            account = self._accounts.get(account_id)
            if account is None:
                return
            was = json.dumps([account["windows"], account["source"], account["plan"],
                              account["note"]], sort_keys=True)
            for kind in WINDOWS:
                if kind in windows:
                    new = dict(windows[kind])
                    old = account["windows"].get(kind)
                    # 这一次没带百分比（事件流里只说了状态）时，同一个窗口里原来的数还作数。
                    if (new.get("left") is None and old is not None and old.get("left") is not None
                            and new.get("resets_at") in (None, old.get("resets_at"))):
                        new["left"] = old["left"]
                        new["resets_at"] = old.get("resets_at")
                    account["windows"][kind] = new
            if windows:
                account["source"] = source
                account["updated_at"] = self._clock()
                account["note"] = None
            if plan is not None:
                account["plan"] = plan
            # 同样的数再报一次不算变化：不叫醒在等的客户端，也不重写文件。
            if was != json.dumps([account["windows"], account["source"], account["plan"],
                                  account["note"]], sort_keys=True):
                self._touch()

    def observe(self, agent: str, kind: str, payload: Any) -> None:
        """帮手干活时自己报上来的额度消息。kind：claude_event、codex。"""
        account_id = self._account_of.get(agent)
        if account_id is None:
            return
        if kind == "claude_event":
            self._merge(account_id, claude_event_windows(payload), "events")
        elif kind == "codex":
            windows, plan = codex_windows(payload)
            self._merge(account_id, windows, "app_server", plan)

    def saw_model(self, agent: str, model: str, usual: bool = True) -> None:
        """帮手报出了这一轮实际用的模型。usual：这一轮是按它平时的配置跑的（没有点别的模型）。"""
        if agent not in self._specs or not isinstance(model, str) or not model.strip():
            return
        model = model.strip()[:80]
        with self._changed:
            changed = False
            for run in self._running.get(agent, []):
                if run.get("model") is None:
                    run["model"] = model
                    changed = True
            if usual and self._models.get(agent) != model:
                self._models[agent] = model
                changed = True
            if changed:
                self._touch()

    # ---- 主动去问 ----

    def _fetch_claude(self, spec: AgentSpec) -> Tuple[Windows, Optional[str], Optional[str]]:
        from .agents import AgentError, ClaudeCodeAgent
        # 只问用量：不发提示，所以不调用模型；也不用把登记过的 MCP 服务都起一遍。
        command = list(spec.command) + ["-p", "--input-format", "stream-json",
                                        "--output-format", "stream-json", "--verbose",
                                        "--strict-mcp-config"]
        command[0] = shutil.which(command[0]) or command[0]
        ask = json.dumps({"type": "control_request", "request_id": "xiaoyou-usage",
                          "request": {"subtype": "get_usage", "skip_behaviors": True}})
        try:
            env = ClaudeCodeAgent(spec)._env()
            if spec.workdir is not None:
                spec.workdir.mkdir(parents=True, exist_ok=True)
            done = subprocess.run(
                command, input=ask + "\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", timeout=FETCH_SECONDS, env=env,
                cwd=str(spec.workdir) if spec.workdir is not None else None)
        except AgentError as error:
            return {}, None, str(error)
        except FileNotFoundError:
            return {}, None, "找不到命令 %s" % command[0]
        except subprocess.TimeoutExpired:
            return {}, None, "Claude Code %d 秒没有回答用量" % FETCH_SECONDS
        except OSError as error:
            return {}, None, "启动 Claude Code 失败：%s" % error
        for line in done.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            reply = message.get("response") if isinstance(message, dict) else None
            if (message.get("type") if isinstance(message, dict) else None) != "control_response" \
                    or not isinstance(reply, dict):
                continue
            if reply.get("subtype") != "success":
                return {}, None, "这个版本的 Claude Code 不能这样查用量：%s" % (
                    str(reply.get("error") or "没有说明")[:120])
            return claude_usage_windows(reply.get("response"))
        detail = (done.stderr.strip().splitlines() or ["没有输出"])[-1][:120]
        return {}, None, "Claude Code 没有回答用量（退出码 %d）：%s" % (done.returncode, detail)

    def _fetch_codex(self, spec: AgentSpec) -> Tuple[Windows, Optional[str], Optional[str]]:
        from . import codex_app
        from .agents import AgentError, CodexAgent, Control
        try:
            server = codex_app.AppServer(spec, Control(), CodexAgent(spec)._env())
        except AgentError as error:
            return {}, None, str(error)
        try:
            deadline = time.monotonic() + FETCH_SECONDS
            codex_app.handshake(server, deadline)
            request_id = server.request("account/rateLimits/read", codex_app.LIMITS_PARAMS)
            windows, plan = codex_windows(server.expect(request_id, deadline, "account/rateLimits/read"))
            return windows, plan, None if windows else "Codex 这一次没有给出额度"
        except AgentError as error:
            return {}, None, str(error)
        finally:
            server.close()

    def refresh(self, account_id: Optional[str] = None, wait: bool = True) -> None:
        """去问一次。account_id 不写就是所有账号；wait 为 False 时放到后台。"""
        if not self._enabled:
            return
        targets = [account_id] if account_id is not None else list(self._accounts)
        for target in targets:
            if target not in self._accounts:
                continue
            if wait:
                self._refresh_one(target)
            else:
                threading.Thread(target=self._refresh_one, args=(target,), daemon=True,
                                 name="xiaoyou-usage").start()

    def _refresh_one(self, account_id: str) -> None:
        lock = self._fetching[account_id]
        if not lock.acquire(blocking=False):
            # 已经有人在问了：等它问完，用它的结果。
            with lock:
                return
        try:
            with self._changed:
                account = self._accounts[account_id]
                account["checked_at"] = self._clock()
                spec = self._specs[account["agents"][0]]
                kind = account["kind"]
            try:
                windows, plan, problem = self.fetchers[kind](spec)
            except Exception as error:  # 查用量不能把别的事带倒
                windows, plan, problem = {}, None, "%s: %s" % (type(error).__name__, error)
            if windows:
                self._merge(account_id, windows, "get_usage" if kind == "claude" else "app_server", plan)
            else:
                with self._changed:
                    account = self._accounts[account_id]
                    if plan is not None:
                        account["plan"] = plan
                    if account["note"] != problem:
                        account["note"] = problem
                        self._touch(save=False)
        finally:
            lock.release()

    def poke(self) -> None:
        """有人在看：太久没问过的账号放到后台问一次。很快返回。"""
        if not self._enabled:
            return
        now = self._clock()
        with self._changed:
            stale = [account["id"] for account in self._accounts.values()
                     if now - max(account["checked_at"], account["updated_at"])
                     > self._config.usage_refresh_seconds]
            for account_id in stale:
                self._accounts[account_id]["checked_at"] = now  # 别让下一个来看的人又问一次
        for account_id in stale:
            self.refresh(account_id, wait=False)

    # ---- 一轮的开始和结束 ----

    def begin(self, agent: str, model: Optional[str] = None) -> Dict[str, Any]:
        """一个帮手开始做一轮。返回的东西在这一轮结束时交给 end。

        model 是这一轮点名要用的模型（没点就是 None，等它自己报）。
        """
        account_id = self._account_of.get(agent)
        if account_id is not None and self._enabled:
            with self._changed:
                fresh = self._clock() - self._accounts[account_id]["updated_at"] < FRESH_SECONDS
            if not fresh:
                self._refresh_one(account_id)
        run: Dict[str, Any] = {"agent": agent, "account": account_id, "at": self._clock(),
                               "model": model, "before": {}}
        with self._changed:
            if account_id is not None:
                run["before"] = {window["kind"]: (window["left"], window["resets_at"])
                                 for window in self._windows(self._accounts[account_id])}
            self._running.setdefault(agent, []).append(run)
            self._touch(save=False)
        return run

    def end(self, run: Dict[str, Any]) -> Dict[str, int]:
        """这一轮结束了。返回这一轮里两个窗口各少了多少个百分点（知道的才有）：d5、d7。"""
        account_id = run.get("account")
        with self._changed:
            runs = self._running.get(run["agent"], [])
            if run in runs:
                runs.remove(run)
            self._touch(save=False)
            seen = account_id is not None and self._accounts[account_id]["updated_at"] > run["at"]
        if account_id is None:
            return {}
        if not seen and self._enabled:
            self._refresh_one(account_id)  # 它这一轮自己没报过额度：问一次
        spent: Dict[str, int] = {}
        with self._changed:
            for window in self._windows(self._accounts[account_id]):
                before, reset_before = run["before"].get(window["kind"], (None, None))
                if before is None or window["left"] is None:
                    continue
                if reset_before and window["resets_at"] and abs(reset_before - window["resets_at"]) > 300:
                    continue  # 中间这个窗口重置过，差值没有意义
                spent["d5" if window["kind"] == "5h" else "d7"] = max(0, before - window["left"])
        return spent

    # ---- 给别人看 ----

    def _windows(self, account: Dict[str, Any]) -> List[Dict[str, Any]]:
        """调用时已经拿着锁：这个账号现在作数的窗口。过了重置时间的那个数不作数了。"""
        now = self._clock()
        windows = []
        for kind in WINDOWS:
            window = account["windows"].get(kind)
            if window is None:
                continue
            if window.get("resets_at") and window["resets_at"] <= now:
                window = {"left": None, "resets_at": None}
            windows.append(dict(window, kind=kind))
        return windows

    @property
    def rev(self) -> int:
        with self._changed:
            return self._rev

    def account(self, agent: str) -> Optional[str]:
        return self._account_of.get(agent)

    def snapshot(self) -> Dict[str, Any]:
        """现在知道的全部：每个订阅账号的额度，每个帮手的模型和档位。"""
        with self._changed:
            accounts = []
            for account in self._accounts.values():
                windows = self._windows(account)
                accounts.append({
                    "id": account["id"], "kind": account["kind"], "agents": list(account["agents"]),
                    "state": state_of(windows), "windows": windows, "plan": account["plan"],
                    "source": account["source"], "updated_at": account["updated_at"] or None,
                    "note": account["note"],
                })
            agents = []
            for name, spec in self._specs.items():
                runs = self._running.get(name, [])
                agents.append({
                    "name": name, "type": spec.type,
                    "model": self._models.get(name) or spec.model,
                    "effort": spec.effort, "efforts": list(spec.efforts),
                    "models": list(spec.models), "account": self._account_of.get(name),
                    "running": len(runs),
                    # 正在做的那一轮用的模型（点了别的模型时和平时的不一样）
                    "now": runs[-1]["model"] if runs else None,
                })
            return {"rev": self._rev, "at": self._clock(), "accounts": accounts, "agents": agents}

    def wait(self, rev: int, seconds: float) -> bool:
        """等到和 rev 不一样了（或者超时）。返回变没变。"""
        deadline = time.monotonic() + max(0.0, seconds)
        with self._changed:
            while self._rev == rev:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._changed.wait(remaining)
            return True

    def notes(self) -> Dict[str, str]:
        """每个帮手一句话：模型、档位、额度。给小幽派活时看。没什么可说的帮手不在里面。"""
        view = self.snapshot()
        accounts = {account["id"]: account for account in view["accounts"]}
        notes: Dict[str, str] = {}
        for entry in view["agents"]:
            parts = []
            if entry["model"] or entry["effort"] or entry["models"]:
                parts.append(describe_tier(entry))
            account = accounts.get(entry["account"])
            if account is not None:
                others = [name for name in account["agents"] if name != entry["name"]]
                shared = "和 %s 共用额度，" % "、".join(others) if others else ""
                parts.append(shared + describe_account(account, view["at"]))
            if parts:
                notes[entry["name"]] = "；".join(parts)
        return notes

    def report(self, detail: bool = False) -> str:
        """给命令行看的几行。"""
        view = self.snapshot()
        lines = []
        for account in view["accounts"]:
            age = ""
            if account["updated_at"]:
                age = "，%d 分钟前（来自 %s）" % (
                    max(0, int(view["at"] - account["updated_at"])) // 60, account["source"])
            lines.append("%s%s：%s%s" % (
                "、".join(account["agents"]),
                "（%s）" % account["plan"] if account["plan"] else "",
                describe_account(account, view["at"]), age))
            if account["note"]:
                lines.append("  查不到：%s" % account["note"])
            if detail:
                lines.append("  账号 %s" % account["id"])
        if not view["accounts"]:
            lines.append("没有订阅登录的帮手，没有额度可看")
        for entry in view["agents"]:
            if entry["model"] or entry["effort"]:
                lines.append("%s：%s" % (entry["name"], describe_tier(entry)))
        return "\n".join(lines)
