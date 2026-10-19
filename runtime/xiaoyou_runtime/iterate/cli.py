"""python3 -m xiaoyou_runtime iterate …

  iterate plugins                 装了哪些插件、哪些启用、各要什么权限
  iterate run [--force] [--dry-run]   跑一轮（不加 --force 时信号不够、时间没到就只采集）
  iterate status                  最近几轮
  iterate issues [--all]          问题簿
  iterate issue <编号前缀> --open|--ignore   手动改一个问题的状态
"""

import json
import time
from typing import Any

from .pipeline import Runner, registry_of, settings_of
from .plugins import CALLS
from .store import Busy, IssueBook, Runs


def add_arguments(subparsers: Any) -> None:
    parser = subparsers.add_parser("iterate", help="自动迭代：从使用信号里找问题、出方案")
    actions = parser.add_subparsers(dest="action", metavar="动作")
    actions.add_parser("plugins", help="插件、启用情况、权限")
    run = actions.add_parser("run", help="跑一轮")
    run.add_argument("--force", action="store_true", help="不管信号够不够、时间到没到")
    run.add_argument("--dry-run", action="store_true", help="试跑：不保存游标、信号和问题簿")
    run.add_argument("--json", action="store_true", help="打印整轮的 JSON")
    actions.add_parser("status", help="最近几轮")
    issues = actions.add_parser("issues", help="问题簿")
    issues.add_argument("--all", action="store_true", help="包括已关闭、已忽略的")
    issue = actions.add_parser("issue", help="改一个问题的状态")
    issue.add_argument("fingerprint")
    group = issue.add_mutually_exclusive_group(required=True)
    group.add_argument("--open", action="store_true", help="放回待选")
    group.add_argument("--ignore", action="store_true", help="以后不选它")


def _when(at: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(at)) if at else "-"


def run(config: Any, args: Any) -> int:
    settings = settings_of(config)
    if args.action in (None, "plugins"):
        registry = registry_of(settings)
        enabled = set()
        for stage, names in settings.pipeline.items():
            for name in ([names] if isinstance(names, str) else names or []):
                enabled.add((stage, name))
        for plugin in registry.all():
            mark = "✓" if (plugin.kind, plugin.name) in enabled else " "
            print("%s %-11s %-18s %-24s %s" % (mark, plugin.kind, plugin.name,
                                               ",".join(plugin.permissions) or "-",
                                               plugin.description if plugin.origin == "builtin" else plugin.origin))
        for error in registry.errors:
            print("加载失败：%s" % error)
        print("\n插件怎么写：每个 .py 里放 PLUGINS = [{name, kind, permissions, run}]；")
        for kind, call in CALLS.items():
            print("  %-9s %s" % (kind, call))
        return 0
    if args.action == "run":
        try:
            result = Runner(config, settings).run(force=args.force, dry_run=args.dry_run)
        except Busy as error:
            print(str(error))
            return 1
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=1))
        else:
            print(result["report"])
            print("详情：state/iterate/runs/%s/" % result["id"])
        return 0 if result["state"] != "failed" else 1
    if args.action == "status":
        runs = Runs(config.state_dir).recent(10)
        if not runs:
            print("还没跑过。")
        for item in runs:
            issue = (item.get("issue") or {}).get("title", "")
            print("%s  %-9s %s %s" % (item["id"], item["state"], issue, "（试跑）" if item.get("dry_run") else ""))
        return 0
    if args.action == "issues":
        book = IssueBook(config.state_dir)
        shown = [issue for issue in book.issues.values()
                 if args.all or issue.status not in ("closed", "ignored")]
        for issue in sorted(shown, key=lambda i: (-i.severity, -i.count)):
            print("%-34s %-8s %-7s 严重度%d ×%-4d 最近 %s  %s" % (
                issue.fingerprint, issue.status, issue.area, issue.severity, issue.count,
                _when(issue.last_seen), issue.title))
        if not shown:
            print("问题簿是空的。")
        return 0
    if args.action == "issue":
        book = IssueBook(config.state_dir)
        matches = [key for key in book.issues if key.startswith(args.fingerprint)]
        if len(matches) != 1:
            print("找到 %d 个匹配的问题，要正好一个" % len(matches))
            return 1
        book.issues[matches[0]].status = "open" if args.open else "ignored"
        book.save()
        print("%s → %s" % (matches[0], book.issues[matches[0]].status))
        return 0
    return 2
