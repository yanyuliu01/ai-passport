"""内置的 planner 插件 template_plan：harness 先找好相关代码，模型只按模板填一份方案。

找代码不靠模型：从问题的线索（崩溃栈里的 文件:行号、报错原文、界面名、抱怨原话里的词）
在允许改的文件里搜，挑出最相关的几段给模型。模型回答的 JSON 由这里校验：改动点 1–3 个、
文件存在而且在允许范围内、风险级别合法；不合格带着问题重问。
"""

import fnmatch
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..objects import Skip

MAX_FILES = 3
MAX_SNIPPETS = 3
SNIPPET_LINES = 8
MAX_CONTEXT_CHARS = 7000
SOURCE_SUFFIXES = (".java", ".kt", ".xml")
RISKS = ("L1", "L2", "L3")
# 动到这些文件，风险至少是这一级（模型自评更低也按这个算）
RISK_FLOOR = (
    ("L3", re.compile(r"AppUpdate|UpdateDownload|AppPacks|BleLink|BuddyProtocol|Firmware|LinkService|QuickReview")),
    ("L2", re.compile(r"RuntimeClient|HubStore|SyncCache|ChatHistory")),
)
FRAME = re.compile(r"\(([A-Za-z0-9_]+\.(?:java|kt)):(\d+)\)")
CJK_WORD = re.compile(r"[一-鿿]{2,6}")


def allowed(path: str, settings: Any) -> bool:
    return (any(fnmatch.fnmatch(path, pattern) for pattern in settings.allow)
            and not any(fnmatch.fnmatch(path, pattern) for pattern in settings.deny))


def _files(repo: Path, settings: Any) -> List[Tuple[str, Path]]:
    found = []
    for pattern in settings.allow:
        root = pattern.split("*", 1)[0].rstrip("/")
        base = repo / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix in SOURCE_SUFFIXES and path.is_file():
                relative = path.relative_to(repo).as_posix()
                if allowed(relative, settings):
                    found.append((relative, path))
    return sorted(set(found))


def _terms(issue: Any) -> List[str]:
    hints = issue.hints
    terms: List[str] = []
    for key in ("message", "screen", "exception"):
        value = hints.get(key)
        if isinstance(value, str) and value:
            terms.append(value.split(":")[0].split(".")[-1] if key == "exception" else value[:40])
    for text in hints.get("texts", []) or []:
        terms.extend(CJK_WORD.findall(text)[:4])
    for item in issue.evidence[:5]:
        terms.extend(CJK_WORD.findall(item.get("excerpt", ""))[:2])
    seen, unique = set(), []
    for term in terms:
        if term and term not in seen and len(term) >= 2:
            seen.add(term)
            unique.append(term)
    return unique[:12]


def _snippet(lines: List[str], center: int) -> str:
    start, end = max(0, center - SNIPPET_LINES), min(len(lines), center + SNIPPET_LINES + 1)
    return "\n".join("%5d| %s" % (number + 1, lines[number]) for number in range(start, end))


def gather_context(repo: Path, settings: Any, issue: Any) -> List[Dict[str, Any]]:
    """返回 [{"file", "snippets": [...]}]，最相关的在前。"""
    files = _files(repo, settings)
    by_name = {path.name: (relative, path) for relative, path in files}
    picked: Dict[str, Dict[str, Any]] = {}
    # 崩溃栈里写了文件和行号的，直接取那一行附近。
    for frame in issue.hints.get("frames", []) or []:
        match = FRAME.search(frame)
        if match and match.group(1) in by_name:
            relative, path = by_name[match.group(1)]
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            entry = picked.setdefault(relative, {"file": relative, "score": 100, "snippets": []})
            if len(entry["snippets"]) < MAX_SNIPPETS:
                entry["snippets"].append(_snippet(lines, int(match.group(2)) - 1))
    terms = _terms(issue)
    scored = []
    for relative, path in files:
        if relative in picked or not terms:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = sum(text.count(term) for term in terms)
        if hits:
            scored.append((hits, relative, text))
    for hits, relative, text in sorted(scored, reverse=True)[:MAX_FILES]:
        lines = text.splitlines()
        snippets, used = [], set()
        for number, line in enumerate(lines):
            if any(term in line for term in terms) and not any(abs(number - u) <= SNIPPET_LINES for u in used):
                used.add(number)
                snippets.append(_snippet(lines, number))
                if len(snippets) >= MAX_SNIPPETS:
                    break
        picked[relative] = {"file": relative, "score": hits, "snippets": snippets}
    ordered = sorted(picked.values(), key=lambda item: -item["score"])[:MAX_FILES]
    total = 0
    for item in ordered:
        kept = []
        for snippet in item["snippets"]:
            if total + len(snippet) > MAX_CONTEXT_CHARS:
                break
            total += len(snippet)
            kept.append(snippet)
        item["snippets"] = kept
    return ordered


def risk_floor(files: List[str]) -> str:
    for level, pattern in RISK_FLOOR:
        if any(pattern.search(name) for name in files):
            return level
    return "L1"


PLAN_PROMPT = """你是手机 App「Pocket Hub」（Android，Java，界面全部用代码搭，没有 XML 布局）的产品工程师。
根据下面的问题、证据和相关代码片段，写一份**小而具体**的迭代方案。要求：
- 只解决这一个问题；改动点 1 到 3 个，每个只写一个文件；文件必须是"相关代码"里出现过的。
- intent 写清楚要把什么改成什么，具体到方法或界面元素，不要泛泛而谈。
- acceptance 写可以检查的验收标准（主人在 App 里做什么、应该看到什么）。
- risk：L1 只改文案/布局/显示条件/空值保护；L2 改交互流程或新增界面元素或碰网络请求；L3 涉及同步、授权、更新机制。
只输出 JSON，格式：
{"summary": "<一句话，主人能看懂>", "problem": "<问题是什么、为什么会这样>",
 "changes": [{"file": "<相对路径>", "intent": "<要怎么改>"}],
 "acceptance": ["<验收标准>"], "out_of_scope": ["<这次不改什么>"],
 "risk": "L1|L2|L3", "risk_reason": "<一句话>"}"""


def _describe(issue: Any, context: List[Dict[str, Any]]) -> str:
    lines = ["问题：%s（检测器 %s，出现 %d 次，严重度 %d/5）" % (
        issue.title, issue.detector, issue.count, issue.severity), "证据："]
    lines.extend("- %s" % item.get("excerpt", "") for item in issue.evidence[:6])
    hints = {key: value for key, value in issue.hints.items() if key not in ("texts",)}
    if hints:
        lines.append("线索：%s" % hints)
    lines.append("相关代码：")
    for item in context:
        lines.append("=== %s ===" % item["file"])
        lines.extend(item["snippets"])
    return "\n".join(lines)


def check_plan(value: Any, files: List[str], settings: Any) -> Optional[str]:
    if not isinstance(value, dict):
        return "要一个 JSON 对象"
    for key in ("summary", "problem", "risk_reason"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            return "缺少 %s" % key
    changes = value.get("changes")
    if not isinstance(changes, list) or not 1 <= len(changes) <= 3:
        return "changes 要有 1 到 3 项"
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("intent"), str) or not change["intent"].strip():
            return "每个改动点要有 file 和 intent"
        name = change.get("file")
        if name not in files:
            return "文件 %r 不在相关代码里，只能从这些里选：%s" % (name, "、".join(files))
        if not allowed(name, settings):
            return "文件 %s 不允许改" % name
    acceptance = value.get("acceptance")
    if not isinstance(acceptance, list) or not acceptance or not all(isinstance(a, str) and a for a in acceptance):
        return "acceptance 要有至少一条"
    if value.get("risk") not in RISKS:
        return "risk 只能是 L1、L2、L3"
    if not isinstance(value.get("out_of_scope", []), list):
        return "out_of_scope 要是列表"
    return None


def template_plan(ctx: Any, issue: Any) -> Dict[str, Any]:
    if not ctx.model_available("iterate.plan"):
        raise Skip("没有可用的模型（models.routes 里配 iterate.plan，或者打开联网模型）")
    context = gather_context(ctx.repo, ctx.settings, issue)
    if not context:
        raise Skip("在允许改的文件里没找到和这个问题相关的代码")
    files = [item["file"] for item in context]
    value, completion = ctx.gateway.complete_json(
        "iterate.plan",
        [{"role": "system", "content": PLAN_PROMPT}, {"role": "user", "content": _describe(issue, context)}],
        lambda answer: check_plan(answer, files, ctx.settings), attempts=3, max_tokens=1200)
    floor = risk_floor([change["file"] for change in value["changes"]])
    if RISKS.index(floor) > RISKS.index(value["risk"]):
        value["risk_reason"] = "%s（按改动的文件至少是 %s）" % (value["risk_reason"], floor)
        value["risk"] = floor
    value["context_files"] = files
    value["model"] = "%s/%s" % (completion.provider, completion.model)
    value["locality"] = completion.locality
    return value


PLUGINS = [
    {"name": "template_plan", "kind": "planner", "permissions": ["read_repo", "model"],
     "run": template_plan, "description": "harness 找代码，模型按模板填方案"},
]
