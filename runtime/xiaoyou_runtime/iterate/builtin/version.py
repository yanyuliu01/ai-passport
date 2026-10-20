"""外层循环：一批问题 → 按页面归并 → 一份版本方案（见 docs/development/auto-iteration 的"外层循环"）。

grouper（规则，不用模型）：按问题落在的页面归组；没有页面的（对话里的抱怨、回复慢）按类别成组。
version_plan（每批一次模型调用）：harness 先为每组找好相关代码、摘出产品原则，模型按模板写：
  2–3 个主题，每个主题覆盖哪些问题、根因假设、流程层面怎么改、改哪些文件、验收、看哪个指标；
  这一版不做什么；会波及哪些已有功能；依据和冲突的产品原则。
harness 校验：主题覆盖的问题必须是这批里真有的、文件必须出现在给的代码里、原则编号必须存在。
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..objects import Issue
from ..plugins import Skip
from .planner import RISKS, allowed, gather_context, risk_floor

MAX_GROUPS = 6
MAX_THEMES = 3
MAX_CONTEXT_CHARS = 9000
PRINCIPLES = Path("docs/product-principles.zh_CN.md")
MAIN_ACTIVITY = "companion/android/app/src/main/java/com/yanyuliu/pockethub/MainActivity.java"
SCREEN_CASE = re.compile(r'case (TAB_[A-Z_]+): return "([a-z_]+)";')
PRINCIPLE = re.compile(r"^\*\*(P\d+) ([^*]+)\*\*")
NAMES = {"app_bug": "App 功能出错", "app_ui": "App 界面显示", "app_slow": "App 慢",
         "app_confusing": "App 用起来麻烦", "agent_behavior": "小幽做事方式"}


def _key(issue: Issue) -> str:
    screen = issue.hints.get("screen")
    if isinstance(screen, str) and screen:
        return "screen:" + screen
    category = issue.hints.get("category")
    if isinstance(category, str) and category:
        return "category:" + category
    return "detector:" + issue.detector


def by_screen(ctx: Any, issues: List[Issue]) -> List[Dict[str, Any]]:
    """把这一批要处理的问题按页面（或类别）归成组，按严重度和次数排序。"""
    settings = ctx.settings
    groups: Dict[str, Dict[str, Any]] = {}
    for issue in issues:
        if issue.area not in settings.areas or issue.status in ("planned", "fixing", "released", "ignored"):
            continue
        group = groups.setdefault(_key(issue), {"key": _key(issue), "issues": []})
        group["issues"].append(issue)
    ordered = sorted(groups.values(), key=lambda g: -sum(i.severity * 10 + i.count for i in g["issues"]))
    for group in ordered:
        kind, _, value = group["key"].partition(":")
        group["title"] = ("页面「%s」" % value) if kind == "screen" else NAMES.get(value, value)
    return ordered[:MAX_GROUPS]


def _screen_terms(repo: Path) -> Dict[str, str]:
    """页面名 → MainActivity 里的 TAB 常量，让找代码能落到具体的页面上。"""
    try:
        text = (repo / MAIN_ACTIVITY).read_text(encoding="utf-8")
    except OSError:
        return {}
    return {screen: tab for tab, screen in SCREEN_CASE.findall(text)}


def _principles(repo: Path) -> List[Dict[str, str]]:
    try:
        lines = (repo / PRINCIPLES).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        match = PRINCIPLE.match(line)
        if match:
            found.append({"id": match.group(1), "text": match.group(2).strip()})
    return found


def _context(ctx: Any, groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """每组找 1–3 个相关文件的片段；页面名换成 TAB 常量再搜。"""
    repo = ctx.repo
    tabs = _screen_terms(repo)
    found: Dict[str, Dict[str, Any]] = {}
    for group in groups:
        for issue in group["issues"]:
            hints = dict(issue.hints)
            screens = [hints.get("screen")] + list(hints.get("screens") or [])
            hints["texts"] = list(hints.get("texts") or []) + [tabs[s] for s in screens if s in tabs]
            probe = Issue(fingerprint=issue.fingerprint, detector=issue.detector, area=issue.area,
                          title=issue.title, severity=issue.severity, count=issue.count,
                          first_seen=issue.first_seen, last_seen=issue.last_seen, evidence=issue.evidence,
                          hints=hints)
            for item in gather_context(repo, ctx.settings, probe):
                current = found.setdefault(item["file"], {"file": item["file"], "snippets": []})
                for snippet in item["snippets"]:
                    if snippet not in current["snippets"]:
                        current["snippets"].append(snippet)
    total, kept = 0, []
    for item in found.values():
        snippets = []
        for snippet in item["snippets"]:
            if total + len(snippet) > MAX_CONTEXT_CHARS:
                break
            total += len(snippet)
            snippets.append(snippet)
        if snippets:
            kept.append({"file": item["file"], "snippets": snippets})
    return kept


PROMPT = """你是手机 App「Pocket Hub」（Android，Java，界面全部用代码搭）的产品负责人兼工程师。
主人用它和 AI 伙伴"小幽"聊天、看任务、确认授权、管理随身设备。下面是这一批从真实使用里找出的问题（已按页面归组）、
相关代码片段和产品原则。写**一份版本方案**，不是逐个打补丁：
- 先想这些现象背后的共同原因（流程设计？入口位置？反馈缺失？），再决定这一版做哪 1–3 个主题。
- 每个主题说清楚：覆盖哪些问题（用给的编号）、根因假设和依据、流程层面怎么改、要改的文件（只能从"相关代码"里选）、
  验收标准（主人在 App 里做什么、应该看到什么）、发布后看哪个指标。
- 写明这一版不做什么、为什么；会波及哪些已有功能、要做哪些回归检查。
- 对照产品原则：写依据了哪几条（编号），以及和哪条有冲突（没有就空列表）。
- 行为数据（停留短、来回切、连点）只是现象，不是结论。每个主题都要写出其他可能的解释（比如主人在等更新、在试新功能、
  在测试别的东西），再给出把握程度：high、medium、low。把握是 low 的不要做成主题，放进 not_doing 并写清还缺什么数据。
- 注意"发布时间线"：改版前后的行为已经单独拿出去了，但仍要考虑它的影响。
- 证据不够下结论的，放进 not_doing 并说明还需要什么数据，不要硬编。
只输出 JSON：
{"version_goal": "<一句话>",
 "themes": [{"title": "", "covers": ["<问题编号>"], "root_cause": "", "evidence": "", "alternatives": "<其他可能的解释>",
             "confidence": "high|medium|low", "flow_change": "",
             "changes": [{"file": "", "intent": ""}], "acceptance": [""], "metric": "", "risk": "L1|L2|L3"}],
 "not_doing": [{"item": "", "reason": ""}],
 "regressions": [""],
 "principles": {"relies_on": ["P8"], "conflicts": [{"id": "", "why": ""}]}}"""


def _describe(groups: List[Dict[str, Any]], context: List[Dict[str, Any]], principles: List[Dict[str, str]],
              paths: List[str], releases: Optional[List[str]] = None) -> str:
    lines = []
    if releases:
        lines.append("## 发布时间线（这段时间 App 发过几版）")
        lines.extend("- " + item for item in releases)
    lines.append("## 问题（按组）")
    for group in groups:
        lines.append("### %s" % group["title"])
        for issue in group["issues"]:
            lines.append("- [%s] %s（出现 %d 次，严重度 %d/5）" % (issue.fingerprint, issue.title, issue.count, issue.severity))
            lines.extend("    · %s" % item.get("excerpt", "") for item in issue.evidence[:3])
    if paths:
        lines.append("## 几段真实的使用路径（页面先后）")
        lines.extend("- " + path for path in paths)
    lines.append("## 产品原则")
    lines.extend("- %s %s" % (item["id"], item["text"]) for item in principles)
    lines.append("## 相关代码")
    for item in context:
        lines.append("=== %s ===" % item["file"])
        lines.extend(item["snippets"])
    return "\n".join(lines)


def check_version(value: Any, issues: Dict[str, Issue], files: List[str], principle_ids: List[str],
                  settings: Any) -> Optional[str]:
    if not isinstance(value, dict) or not isinstance(value.get("version_goal"), str) or not value["version_goal"].strip():
        return "要有 version_goal"
    themes = value.get("themes")
    if not isinstance(themes, list) or not 1 <= len(themes) <= MAX_THEMES:
        return "themes 要有 1 到 %d 个" % MAX_THEMES
    for theme in themes:
        if not isinstance(theme, dict):
            return "每个主题是一个对象"
        for key in ("title", "root_cause", "flow_change", "metric", "alternatives"):
            if not isinstance(theme.get(key), str) or not theme[key].strip():
                return "主题缺少 %s" % key
        covers = theme.get("covers")
        if not isinstance(covers, list) or not covers:
            return "主题「%s」要写 covers" % theme.get("title")
        unknown = [item for item in covers if item not in issues]
        if unknown:
            return "covers 里的 %s 不是这批的问题编号，只能用：%s" % ("、".join(map(str, unknown)), "、".join(issues))
        changes = theme.get("changes")
        if not isinstance(changes, list) or not 1 <= len(changes) <= 4:
            return "主题「%s」的 changes 要有 1 到 4 项" % theme.get("title")
        for change in changes:
            if not isinstance(change, dict) or change.get("file") not in files:
                return "文件只能从相关代码里选：%s" % "、".join(files)
            if not allowed(change["file"], settings):
                return "文件 %s 不允许改" % change["file"]
            if not isinstance(change.get("intent"), str) or not change["intent"].strip():
                return "每个改动要写 intent"
        acceptance = theme.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance:
            return "主题「%s」要有验收标准" % theme.get("title")
        if theme.get("risk") not in RISKS:
            return "risk 只能是 L1、L2、L3"
        if theme.get("confidence") not in ("high", "medium", "low"):
            return "confidence 只能是 high、medium、low"
    if not isinstance(value.get("not_doing", []), list) or not isinstance(value.get("regressions", []), list):
        return "not_doing 和 regressions 要是列表"
    principles = value.get("principles")
    if not isinstance(principles, dict) or not isinstance(principles.get("relies_on"), list):
        return "principles 要有 relies_on 列表"
    for item in principles["relies_on"] + [c.get("id") for c in principles.get("conflicts", []) if isinstance(c, dict)]:
        if principle_ids and item not in principle_ids:
            return "原则编号 %s 不存在" % item
    return None


def _paths(signals_by_session: Dict[str, List[str]]) -> List[str]:
    return [" → ".join(names[:24]) for names in list(signals_by_session.values())[:3]]


def version_plan(ctx: Any, groups: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not groups:
        raise Skip("这一批没有要处理的问题")
    if not ctx.model_available("iterate.version_plan"):
        raise Skip("没有可用的模型（models.routes 里配 iterate.version_plan）")
    repo = ctx.repo
    issues = {issue.fingerprint: issue for group in groups for issue in group["issues"]}
    context = _context(ctx, groups)
    files = [item["file"] for item in context]
    principles = _principles(repo)
    principle_ids = [item["id"] for item in principles]
    paths = _paths(getattr(ctx, "journeys", {}) or {})
    releases = list(getattr(ctx, "release_lines", None) or [])
    value, completion = ctx.gateway.complete_json(
        "iterate.version_plan",
        [{"role": "system", "content": PROMPT},
         {"role": "user", "content": _describe(groups, context, principles, paths, releases)}],
        lambda answer: check_version(answer, issues, files, principle_ids, ctx.settings),
        attempts=3, max_tokens=3000)
    # 把握低的不做：harness 自己挪进 not_doing，不靠模型自觉。
    weak = [theme for theme in value["themes"] if theme["confidence"] == "low"]
    value["themes"] = [theme for theme in value["themes"] if theme["confidence"] != "low"]
    value.setdefault("not_doing", [])
    for theme in weak:
        value["not_doing"].append({"item": theme["title"], "reason": "把握低：%s" % theme.get("alternatives", "")})
    for theme in value["themes"]:
        floor = risk_floor([change["file"] for change in theme["changes"]])
        if RISKS.index(floor) > RISKS.index(theme["risk"]):
            theme["risk"] = floor
    value["groups"] = [{"title": g["title"], "issues": [i.fingerprint for i in g["issues"]]} for g in groups]
    value["context_files"] = files
    value["model"] = "%s/%s" % (completion.provider, completion.model)
    value["locality"] = completion.locality
    return value


PLUGINS = [
    {"name": "by_screen", "kind": "grouper", "run": by_screen,
     "description": "按页面（没有页面的按类别）把问题归组"},
    {"name": "version_plan", "kind": "version_planner", "permissions": ["read_repo", "model"],
     "run": version_plan, "description": "一批问题一次写成版本方案，对照产品原则"},
]
