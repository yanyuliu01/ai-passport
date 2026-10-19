"""跑一轮：采集 → 检测 → 选题 → 方案 →（以后：实现、门禁、发布、观察）。

每一步出错只停在那一步，写明原因；采集到的信号照样留下。一轮的所有输入输出都写在
state/iterate/runs/<编号>/ 里。dry_run 不落任何持久状态（游标、信号、问题簿都不动），
只在 runs/ 里留一份结果，方便先看看效果。
"""

import json
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import model_gateway
from .objects import FORMAT, Issue, Signal
from .plugins import Context, Plugin, PluginError, Registry, Skip, builtin_registry
from .settings import IterateSettings, parse
from .events import EventStore
from .lease import LeaseClient, capabilities
from .store import IssueBook, Lock, Runs, SignalStore, write_json

# 方案出了却一直没人接着做（还没有 implementer 时就是这样）：过这么多天重新放回待选。
PLANNED_EXPIRE_DAYS = 7


def settings_of(config: Any) -> IterateSettings:
    return parse(getattr(config, "iterate", None) or {}, Path(config.base_dir))


def registry_of(settings: IterateSettings) -> Registry:
    return builtin_registry(settings.plugin_dirs)


class Runner:
    def __init__(self, config: Any, settings: Optional[IterateSettings] = None,
                 registry: Optional[Registry] = None, gateway: Any = None,
                 now: Optional[float] = None, poster: Any = None, leaser: Any = None):
        self.config = config
        self.state_dir = Path(config.state_dir)
        self.settings = settings or settings_of(config)
        self.registry = registry or registry_of(self.settings)
        self.gateway = gateway if gateway is not None else model_gateway.from_config(config)
        self.now = time.time() if now is None else now
        self.poster = poster
        self.leaser = leaser if leaser is not None else LeaseClient(config)
        self.leased = False
        self.log: List[str] = []

    def _ctx(self, plugin: Plugin) -> Context:
        return Context(plugin, self.settings, self.state_dir, self.gateway, self.log, self.now)

    def _plugins(self, stage: str) -> List[Plugin]:
        names = self.settings.pipeline.get(stage)
        if not names:
            return []
        if isinstance(names, str):
            names = [names]
        return [self.registry.get(stage, name) for name in names]

    # ---- 一轮 ----

    def run(self, force: bool = False, dry_run: bool = False) -> Dict[str, Any]:
        with Lock(self.state_dir):
            try:
                return self._run(force, dry_run)
            finally:
                if self.leased:
                    self.leaser("release", "done")
                    self.leased = False

    def _lease(self, run: Dict[str, Any], stage: str) -> bool:
        """申请或续约；别的电脑持有时记下是谁，返回 False。试跑不申请。"""
        if run.get("dry_run"):
            return True
        answer = self.leaser("renew" if self.leased else "acquire", stage)
        if answer.get("granted"):
            self.leased = True
            return True
        run["state"] = "elsewhere"
        run["stages"]["lease"] = {"holder": answer.get("holder_name") or answer.get("holder", ""),
                                  "stage": answer.get("stage", "")}
        return False

    def _run(self, force: bool, dry_run: bool) -> Dict[str, Any]:
        runs = Runs(self.state_dir)
        run_id, folder = runs.create(self.now)
        run: Dict[str, Any] = {"format": FORMAT, "id": run_id, "started_at": self.now, "dry_run": dry_run,
                               "state": "collected", "stages": {}}
        try:
            self._stages(run, folder, runs, force, dry_run)
        except Exception as error:
            run["state"] = "failed"
            run["error"] = "%s: %s" % (type(error).__name__, error)
            self.log.append(traceback.format_exc(limit=6))
        run["finished_at"] = time.time()
        run["log"] = self.log[-50:]
        run["report"] = report(run)
        write_json(folder / "run.json", run)
        (folder / "report.md").write_text(run["report"], encoding="utf-8")
        if not dry_run and run["state"] in ("planned", "failed"):
            self._post(run)
        return run

    def _stages(self, run: Dict[str, Any], folder: Path, runs: Runs, force: bool, dry_run: bool) -> None:
        store = SignalStore(self.state_dir)
        cursors = runs.cursors()
        state = runs.state()
        fresh: List[Signal] = []
        collected: Dict[str, Any] = {}
        for plugin in self._plugins("source"):
            started = time.time()
            try:
                signals, cursor = plugin.run(self._ctx(plugin), cursors.get(plugin.name))
            except Skip as reason:
                collected[plugin.name] = {"skipped": str(reason)}
                continue
            except Exception as error:  # 一个来源坏了不影响别的来源
                collected[plugin.name] = {"error": "%s: %s" % (type(error).__name__, error)}
                continue
            cursors[plugin.name] = cursor
            fresh.extend(signals)
            collected[plugin.name] = {"signals": len(signals), "seconds": round(time.time() - started, 2)}
        run["stages"]["collect"] = collected
        if not dry_run:
            store.append(fresh)
            runs.save_cursors(cursors)
            store.prune(self.settings.retention_days, self.now)
            EventStore(self.state_dir).prune(self.now)
        pending = int(state.get("pending_signals", 0)) + len(fresh)
        last = float(state.get("last_run_at", 0))
        due = pending >= self.settings.min_new_signals or self.now - last >= self.settings.interval_hours * 3600
        if not (force or due):
            run["state"] = "waiting"
            run["stages"]["trigger"] = {"pending_signals": pending, "hours_since_last": round((self.now - last) / 3600, 1)}
            if not dry_run:
                runs.save_state(dict(state, pending_signals=pending))
            return
        if not self._lease(run, "detect"):
            return
        if not dry_run:
            runs.save_state({"last_run_at": self.now, "pending_signals": 0, "last_run": run["id"]})

        # 检测：看最近 window_days 天的全部信号，不只是这次新来的
        window = store.since(self.now - self.settings.window_days * 86400)
        if dry_run:
            known = {signal.id for signal in window}
            window += [signal for signal in fresh if signal.id not in known]
        found: List[Issue] = []
        detected: Dict[str, Any] = {}
        for plugin in self._plugins("detector"):
            mine = [signal for signal in window if plugin.accepts(signal.type)]
            try:
                issues = plugin.run(self._ctx(plugin), mine) if mine else []
            except Exception as error:
                detected[plugin.name] = {"error": "%s: %s" % (type(error).__name__, error)}
                continue
            detected[plugin.name] = {"signals": len(mine), "issues": len(issues)}
            found.extend(issues)
        book = IssueBook(self.state_dir)
        merged = book.merge(found)
        for issue in book.issues.values():
            if issue.status == "planned" and self.now - issue.last_seen > PLANNED_EXPIRE_DAYS * 86400:
                issue.status = "open"
        run["stages"]["detect"] = detected
        run["issues"] = [issue.fingerprint for issue in merged]
        write_json(folder / "issues.json", [issue.to_dict() for issue in
                                            sorted(merged, key=lambda i: (-i.severity, -i.count))])
        run["state"] = "detected"

        chosen: Optional[Issue] = None
        for plugin in self._plugins("triage")[:1]:
            chosen = plugin.run(self._ctx(plugin), list(book.issues.values()))
        if chosen is None:
            run["state"] = "nothing"
            if not dry_run:
                book.save()
            return
        run["issue"] = chosen.to_dict()
        run["state"] = "triaged"

        plan: Optional[Dict[str, Any]] = None
        planners = self._plugins("planner")[:1]
        if planners and "plan" not in capabilities(self.settings, self.gateway)["stages"]:
            run["stages"]["plan"] = {"skipped": "这台电脑没有仓库或可用的模型，留给能写方案的电脑"}
            planners = []
        if planners and not self._lease(run, "plan"):
            return
        for plugin in planners:
            try:
                plan = plugin.run(self._ctx(plugin), chosen)
            except Skip as reason:
                run["stages"]["plan"] = {"skipped": str(reason)}
            except PluginError:
                raise
            except Exception as error:
                run["stages"]["plan"] = {"error": "%s: %s" % (type(error).__name__, error)}
        if plan is not None:
            write_json(folder / "plan.json", plan)
            run["plan"] = plan
            run["state"] = "planned"
            chosen.status = "planned"
        chosen.runs = (chosen.runs + [run["id"]])[-10:]
        if not dry_run:
            book.save()

    # ---- 告诉手机 ----

    def _post(self, run: Dict[str, Any]) -> None:
        """在 App 里显示成一张只读卡（复用外部上报，见 reports.py）。Runtime 没开就算了。"""
        title = "自动迭代：%s" % ((run.get("issue") or {}).get("title") or "这一轮")
        payload = {"origin": "iterate", "session_id": run["id"], "title": title[:100],
                   "state": "failed" if run["state"] == "failed" else "done",
                   "text": run["report"][:7000], "upstream_id": run["id"] + ":report"}
        poster = self.poster or _post_report
        try:
            poster(self.config, payload)
        except Exception as error:
            self.log.append("没能在 App 里显示：%s" % error)


def _post_report(config: Any, payload: Dict[str, Any]) -> None:
    host = config.host if config.host not in ("0.0.0.0", "::", "") else "127.0.0.1"
    request = urllib.request.Request(
        "http://%s:%d/v1/reports" % (host, config.port), data=json.dumps(payload).encode("utf-8"),
        method="POST", headers={"Authorization": "Bearer " + config.token,
                                "Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()


STATE_WORDS = {
    "elsewhere": "别的电脑正在跑这一轮",
    "waiting": "信号还不够，等下一轮", "detected": "找到了问题", "nothing": "没有需要改的问题",
    "triaged": "选了一个问题，但这次没出方案", "planned": "出了方案（还没改代码）", "failed": "这一轮出错了",
}


def report(run: Dict[str, Any]) -> str:
    """给主人看的一页：发现了什么、方案是什么、下一步。"""
    lines = ["# 自动迭代 %s" % run["id"], "", "结果：%s" % STATE_WORDS.get(run["state"], run["state"])]
    if run.get("dry_run"):
        lines.append("（试跑：没有保存游标和问题簿）")
    if run["stages"].get("lease"):
        lease = run["stages"]["lease"]
        lines.append("由 %s 在跑（%s）" % (lease.get("holder") or "另一台电脑", lease.get("stage") or "-"))
    if run.get("error"):
        lines.append("出错：%s" % run["error"])
    collect = run["stages"].get("collect", {})
    if collect:
        lines.append("采集：" + "，".join(
            "%s %s" % (name, value.get("signals", value.get("skipped") or value.get("error")))
            for name, value in collect.items()))
    issue = run.get("issue")
    if issue:
        lines += ["", "## 这次选的问题", "%s（出现 %d 次，严重度 %d/5，方面：%s）" % (
            issue["title"], issue["count"], issue["severity"], issue["area"])]
        lines += ["- %s" % item.get("excerpt", "") for item in issue.get("evidence", [])[:5]]
    plan = run.get("plan")
    if plan:
        lines += ["", "## 方案", plan["summary"], "", plan["problem"], "", "改动："]
        lines += ["- %s：%s" % (change["file"], change["intent"]) for change in plan["changes"]]
        lines += ["", "验收："] + ["- %s" % item for item in plan["acceptance"]]
        if plan.get("out_of_scope"):
            lines += ["", "这次不改："] + ["- %s" % item for item in plan["out_of_scope"]]
        lines += ["", "风险：%s（%s）" % (plan["risk"], plan["risk_reason"]),
                  "方案由 %s 写（%s）" % (plan.get("model", "?"), "本机" if plan.get("locality") == "local" else "联网")]
    elif run["stages"].get("plan"):
        reason = run["stages"]["plan"]
        lines += ["", "没出方案：%s" % (reason.get("skipped") or reason.get("error"))]
    return "\n".join(lines) + "\n"
