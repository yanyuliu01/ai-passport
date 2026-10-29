"""内置的 detector 插件：全是规则，不用模型。每个只看自己 consumes 的那几类信号。

规则故意简单、可解释：一个问题为什么被选出来，看证据里的那几条信号就知道。
"""

import re
from typing import Any, Callable, Dict, List

from ..objects import Issue, Signal, evidence, fingerprint

SLOW_MS = 3000           # 跨端可靠性规则：首次有效回复 3 秒内
SLOW_SHARE = 0.2         # 超过 3 秒的比例到这么多才算问题
MIN_SLOW_SAMPLES = 5


def _group(signals: List[Signal], key: Callable[[Signal], Any]) -> Dict[Any, List[Signal]]:
    groups: Dict[Any, List[Signal]] = {}
    for signal in signals:
        groups.setdefault(key(signal), []).append(signal)
    return groups


def _issue(detector: str, key: Any, area: str, title: str, severity: int, items: List[Signal],
           excerpt: Callable[[Signal], str], hints: Dict[str, Any]) -> Issue:
    return Issue(fingerprint=fingerprint(detector, key), detector=detector, area=area, title=title,
                 severity=severity, count=len(items), first_seen=min(s.at for s in items),
                 last_seen=max(s.at for s in items), evidence=evidence(items, excerpt), hints=hints)


def _normalize(text: str) -> str:
    """去掉数字、编号、引号里的东西，让同一类报错归到一起。"""
    text = re.sub(r"[0-9a-f]{8,}", "#", text.lower())
    text = re.sub(r"\d+", "#", text)
    text = re.sub(r"[“\"'‘].*?[”\"'’]", "…", text)
    return text[:60]


def crash(ctx: Any, signals: List[Signal]) -> List[Issue]:
    issues = []
    for key, items in _group(signals, lambda s: (s.data.get("exception", "?"),
                                                 (s.data.get("frames") or ["?"])[0])).items():
        exception, frame = key
        issues.append(_issue("crash", key, "app", "App 崩溃：%s" % exception.split(":")[0], 5, items,
                             lambda s: "%s @ %s（%s）" % (s.data.get("exception", ""), frame,
                                                         s.data.get("app_version", "")),
                             {"exception": exception, "frames": items[-1].data.get("frames", [])}))
    return issues


def app_errors(ctx: Any, signals: List[Signal]) -> List[Issue]:
    issues = []
    groups = _group(signals, lambda s: (s.data.get("screen", ""), _normalize(s.data.get("message", ""))))
    for key, items in groups.items():
        screen, _ = key
        message = items[-1].data.get("message", "")
        issues.append(_issue("app_errors", key, "app", "App 报错：%s" % message[:40], 3, items,
                             lambda s: "%s：%s" % (s.data.get("screen", ""), s.data.get("message", "")),
                             {"screen": screen, "message": message}))
    return issues


def rage_tap(ctx: Any, signals: List[Signal]) -> List[Issue]:
    signals = ordinary(signals)
    issues = []
    for key, items in _group(signals, lambda s: (s.data.get("screen", ""), s.data.get("control") or s.data.get("region", ""))).items():
        screen, where = key
        issues.append(_issue("rage_tap", key, "app", "在「%s」连续点同一处（%s）" % (screen, where), 3,
                             items, lambda s: "%s %s 连点 %s 次" % (
                                 s.data.get("screen"), s.data.get("control") or s.data.get("region"), s.data.get("count")),
                             {"screen": screen, "region": items[-1].data.get("region", ""),
                              "control": items[-1].data.get("control", "")}))
    return issues


def slow_first_reply(ctx: Any, signals: List[Signal]) -> List[Issue]:
    issues = []
    for key, items in _group(signals, lambda s: s.data.get("agent", "") if s.type == "chat.reply_latency"
                             else "app:" + s.data.get("metric", "")).items():
        values = [s.data.get("ms", 0) for s in items]
        slow = [s for s in items if s.data.get("ms", 0) > SLOW_MS]
        if len(items) < MIN_SLOW_SAMPLES or len(slow) < SLOW_SHARE * len(items):
            continue
        ordered = sorted(values)
        p90 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))]
        area = "app" if str(key).startswith("app:") else "runtime"
        issue = _issue("slow_first_reply", key, area,
                       "%s 首次回复超过 3 秒的占 %d%%（P90 %.1f 秒）" % (
                           key or "小鱼", 100 * len(slow) // len(items), p90 / 1000.0),
                       4, slow, lambda s: "%.1f 秒（卡 %s）" % (s.data.get("ms", 0) / 1000.0, s.data.get("card", "")),
                       {"agent": key, "samples": len(items), "p90_ms": p90})
        issues.append(issue)
    return issues


def complaint_cluster(ctx: Any, signals: List[Signal]) -> List[Issue]:
    issues = []
    # 当前这版 App 发布之前说的 App 问题，可能已经在这一版里修了：不算进来，等新版上再出现再说。
    signals = [s for s in signals if not (s.data.get("before_release") and str(s.data.get("category", "")).startswith("app_"))]
    for category, items in _group(signals, lambda s: s.data.get("category", "")).items():
        if not category.startswith("app_") and category != "agent_behavior":
            continue
        area = "agent" if category == "agent_behavior" else "app"
        names = {"app_bug": "App 功能出错", "app_ui": "App 界面显示", "app_slow": "App 慢",
                 "app_confusing": "App 用起来麻烦", "agent_behavior": "对小鱼做事方式不满"}
        # 关键词模式分出来的不太准：严重度低一档。
        sure = sum(1 for s in items if s.data.get("method") == "model")
        severity = 3 if sure * 2 >= len(items) else 2
        issues.append(_issue("complaint_cluster", category, area,
                             "主人说过 %d 次：%s" % (len(items), names.get(category, category)), severity,
                             items, lambda s: s.data.get("text", ""),
                             {"category": category, "texts": [s.data.get("text", "") for s in items[-5:]]}))
    return issues


def task_failures(ctx: Any, signals: List[Signal]) -> List[Issue]:
    issues = []
    owned = [s for s in signals if not s.data.get("monitoring_only")]
    for key, items in _group(owned, lambda s: (s.data.get("agent", ""), _normalize(s.data.get("brief", "")))).items():
        agent, _ = key
        issues.append(_issue("task_failures", key, "agent", "%s 做事失败：%s" % (
            agent, items[-1].data.get("brief", "")[:50]), 3, items,
            lambda s: "%s（%s）" % (s.data.get("brief", ""), s.data.get("model", "")),
            {"agent": agent, "model": items[-1].data.get("model", "")}))
    return issues


QUICK_EXIT_MS = 2000
QUICK_EXIT_SHARE = 0.5
MIN_QUICK_EXIT_VISITS = 5
MIN_LOOPS = 2


def ordinary(signals: List[Signal]) -> List[Signal]:
    """去掉改版前后的行为（新版上手期、等着装更新）：那时候来回点是在试新功能、看更新，不是用不明白。"""
    return [signal for signal in signals if not signal.data.get("phase")]


def quick_exit(ctx: Any, signals: List[Signal]) -> List[Issue]:
    """进了就退：一个页面一半以上的停留不到 2 秒，多半是点错了或者没找到想要的。"""
    signals = ordinary(signals)
    issues = []
    for name, items in _group(signals, lambda s: s.data.get("name", "")).items():
        quick = [s for s in items if s.data.get("ms", 0) < QUICK_EXIT_MS]
        if not name or len(items) < MIN_QUICK_EXIT_VISITS or len(quick) < QUICK_EXIT_SHARE * len(items):
            continue
        issues.append(_issue("quick_exit", name, "app", "「%s」进了就退：%d 次里 %d 次不到 2 秒" % (
            name, len(items), len(quick)), 3, quick,
            lambda s: "%s 停留 %.1f 秒" % (s.data.get("name"), s.data.get("ms", 0) / 1000.0),
            {"screen": name, "visits": len(items), "quick": len(quick)}))
    return issues


def back_and_forth(ctx: Any, signals: List[Signal]) -> List[Issue]:
    """在两个页面之间来回切（A → B → A → B）：多半在找一个不在预期位置的东西。"""
    signals = ordinary(signals)
    counts: Dict[Any, List[Signal]] = {}
    for session, items in _group(signals, lambda s: s.data.get("session", "")).items():
        if not session:
            continue
        path = sorted(items, key=lambda s: s.at)
        for index in range(len(path) - 3):
            a, b, c, d = (path[index + offset].data.get("name") for offset in range(4))
            if a and b and a != b and (a, b) == (c, d):
                counts.setdefault(tuple(sorted((a, b))), []).append(path[index + 3])
    issues = []
    for pair, items in counts.items():
        if len(items) < MIN_LOOPS:
            continue
        issues.append(_issue("back_and_forth", pair, "app", "在「%s」和「%s」之间来回切 %d 次" % (
            pair[0], pair[1], len(items)), 3, items,
            lambda s: "会话 %s：%s" % (str(s.data.get("session", ""))[:6], " ↔ ".join(pair)),
            {"screen": pair[0], "screens": list(pair)}))
    return issues


PLUGINS = [
    {"name": "crash", "kind": "detector", "consumes": ["app.crash"], "run": crash,
     "description": "同一个异常和栈顶归成一个问题"},
    {"name": "app_errors", "kind": "detector", "consumes": ["app.error"], "run": app_errors,
     "description": "界面上提示给主人的错误"},
    {"name": "rage_tap", "kind": "detector", "consumes": ["app.rage_tap"], "run": rage_tap,
     "description": "短时间连续点同一处"},
    {"name": "slow_first_reply", "kind": "detector", "consumes": ["chat.reply_latency", "app.slow"],
     "run": slow_first_reply, "description": "首次回复超过 3 秒"},
    {"name": "complaint_cluster", "kind": "detector", "consumes": ["chat.feedback"],
     "run": complaint_cluster, "description": "对话里的抱怨按类别聚起来"},
    {"name": "task_failures", "kind": "detector", "consumes": ["task.failed"], "run": task_failures,
     "description": "同一个帮手同一类失败"},
    {"name": "quick_exit", "kind": "detector", "consumes": ["app.screen"], "run": quick_exit,
     "description": "页面进了就退"},
    {"name": "back_and_forth", "kind": "detector", "consumes": ["app.screen"], "run": back_and_forth,
     "description": "在两个页面之间来回切"},
]
