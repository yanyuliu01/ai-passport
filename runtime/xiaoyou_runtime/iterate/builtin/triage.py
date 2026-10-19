"""内置的 triage 插件：一轮只选一个问题来改。"""

import math
from typing import Any, List, Optional

from ..objects import Issue

# 这些状态的问题已经有人在管，不再选
BUSY = ("planned", "fixing", "released", "ignored")
# 一个问题修过这么多次还没好：不再自动选，留给主人
MAX_ATTEMPTS = 2


def score(issue: Issue, now: float) -> float:
    days = max(0.0, (now - issue.last_seen) / 86400.0)
    return (issue.severity * 10 + math.log2(issue.count + 1) * 5
            + max(0.0, 7 - days) - issue.attempts * 15)


def score_by_impact(ctx: Any, issues: List[Issue]) -> Optional[Issue]:
    settings = ctx.settings
    candidates = [issue for issue in issues
                  if issue.area in settings.areas and issue.status not in BUSY
                  and issue.attempts < MAX_ATTEMPTS
                  # 崩溃一次就值得修；别的要出现够次数
                  and issue.count >= (1 if issue.severity >= 5 else settings.min_issue_count)]
    if not candidates:
        return None
    return max(candidates, key=lambda issue: (score(issue, ctx.now), issue.last_seen))


PLUGINS = [
    {"name": "score_by_impact", "kind": "triage", "run": score_by_impact,
     "description": "严重度、次数、新近程度打分，修过没好的降分"},
]
