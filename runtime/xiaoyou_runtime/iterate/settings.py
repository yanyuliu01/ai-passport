"""iterate 一节配置。没写的都用默认值；默认不自动跑（enabled 为 false 时只能手动 iterate run）。"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# 每一步默认用哪些插件。implementer 及以后的步骤还没做，默认是空的：跑到方案为止。
DEFAULT_PIPELINE: Dict[str, Any] = {
    "source": ["app_events", "chat_feedback", "card_failures", "reply_latency"],
    "detector": ["crash", "app_errors", "rage_tap", "slow_first_reply", "complaint_cluster",
                 "task_failures"],
    "triage": "score_by_impact",
    "planner": "template_plan",
    "implementer": None,
}
LIST_STAGES = ("source", "detector", "gate")
SINGLE_STAGES = ("triage", "planner", "implementer", "policy", "releaser", "observer")
AREAS = ("app", "runtime", "agent")


class IterateConfigError(ValueError):
    pass


@dataclass
class IterateSettings:
    enabled: bool = False
    pipeline: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_PIPELINE))
    plugin_dirs: List[Path] = field(default_factory=list)
    # 这一阶段只改哪些方面的问题；别的方面的问题照样记、出报告，但不选进来改。
    areas: List[str] = field(default_factory=lambda: ["app"])
    # 方案里允许出现的文件（相对仓库根目录的 glob）
    allow: List[str] = field(default_factory=lambda: ["companion/android/app/src/main/**"])
    deny: List[str] = field(default_factory=lambda: ["**/AndroidManifest.xml", "**/build.gradle"])
    repo_dir: Path = Path("..")
    # 触发：自上一轮以来新信号够这么多条，或者离上一轮够这么多小时
    min_new_signals: int = 50
    interval_hours: float = 24.0
    only_when_idle: bool = True
    # 一个问题至少出现这么多次才会被选来改
    min_issue_count: int = 3
    window_days: int = 7
    retention_days: int = 30
    # 给对话分类时同一句问几次取多数（本地模型便宜，多问几次换稳定）
    votes: int = 3
    # 一轮最多让模型分类多少句话（防止第一次跑时把历史全送去分类）
    max_classify: int = 40


def _int(raw: Dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise IterateConfigError("iterate.%s 应该是 %d 到 %d 之间的整数" % (key, low, high))
    return value


def _strings(raw: Dict[str, Any], key: str, default: List[str]) -> List[str]:
    value = raw.get(key, default)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise IterateConfigError("iterate.%s 应该是字符串列表" % key)
    return list(value)


def parse(raw: Optional[Dict[str, Any]], base: Path) -> IterateSettings:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise IterateConfigError("iterate 应该是一个对象")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise IterateConfigError("iterate.enabled 应该是 true 或 false")
    pipeline = dict(DEFAULT_PIPELINE)
    given = raw.get("pipeline", {})
    if not isinstance(given, dict):
        raise IterateConfigError("iterate.pipeline 应该是一个对象")
    for stage, value in given.items():
        if stage in LIST_STAGES:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise IterateConfigError("iterate.pipeline.%s 应该是插件名的列表" % stage)
        elif stage in SINGLE_STAGES:
            if value is not None and not isinstance(value, str):
                raise IterateConfigError("iterate.pipeline.%s 应该是一个插件名或 null" % stage)
        else:
            raise IterateConfigError("iterate.pipeline 里没有 %s 这一步" % stage)
        pipeline[stage] = value
    areas = _strings(raw, "areas", ["app"])
    for area in areas:
        if area not in AREAS:
            raise IterateConfigError("iterate.areas 只能是 %s" % "、".join(AREAS))
    repo = raw.get("repo_dir", "..")
    if not isinstance(repo, str):
        raise IterateConfigError("iterate.repo_dir 应该是路径")
    trigger = raw.get("trigger", {})
    if not isinstance(trigger, dict):
        raise IterateConfigError("iterate.trigger 应该是一个对象")
    interval = trigger.get("interval_hours", 24)
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 0.1 <= interval <= 24 * 30:
        raise IterateConfigError("iterate.trigger.interval_hours 应该在 0.1 到 720 之间")
    idle = trigger.get("only_when_idle", True)
    if not isinstance(idle, bool):
        raise IterateConfigError("iterate.trigger.only_when_idle 应该是 true 或 false")

    def path(text: str) -> Path:
        result = Path(os.path.expanduser(text))
        return (result if result.is_absolute() else base / result).resolve()

    return IterateSettings(
        enabled=enabled, pipeline=pipeline,
        plugin_dirs=[path(item) for item in _strings(raw, "plugin_dirs", [])],
        areas=areas,
        allow=_strings(raw, "allow", IterateSettings().allow),
        deny=_strings(raw, "deny", IterateSettings().deny),
        repo_dir=path(repo),
        min_new_signals=_int(trigger, "min_new_signals", 50, 1, 100000),
        interval_hours=float(interval), only_when_idle=idle,
        min_issue_count=_int(raw, "min_issue_count", 3, 1, 1000),
        window_days=_int(raw, "window_days", 7, 1, 90),
        retention_days=_int(raw, "retention_days", 30, 1, 365),
        votes=_int(raw, "votes", 3, 1, 7),
        max_classify=_int(raw, "max_classify", 40, 0, 1000),
    )
