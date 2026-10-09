"""命令行里的 firmware 子命令：设备固件入库、查看、指定推给设备的那一版。

  python3 -m xiaoyou_runtime firmware list                 仓库里有哪些版本
  python3 -m xiaoyou_runtime firmware status               设备现在跑的是哪一版、在等什么
  python3 -m xiaoyou_runtime firmware add 文件 [--push]     入库一个固件文件
  python3 -m xiaoyou_runtime firmware fetch [标签] [--push] 取回 GitHub 上构建好的固件并入库
  python3 -m xiaoyou_runtime firmware build [--push]       在这台电脑上构建并入库（要先配置）
  python3 -m xiaoyou_runtime firmware push 版本            把这一版推给设备
  python3 -m xiaoyou_runtime firmware restore 版本         同上，说法不同：恢复到这一版
  python3 -m xiaoyou_runtime firmware cancel               不推了
  python3 -m xiaoyou_runtime firmware remove 版本          从仓库里删掉一版

“版本”可以写序号（7 或 #7）、编号的开头几位、latest（最新入库的）、
current（设备现在跑的）、previous（设备另一个槽位里的上一版）。

这些命令只改这台电脑上的清单；真正把固件写进设备的是手机 App：它连着设备、
又连得上这台 Runtime 时，看到清单里有要推的版本就会动手。
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import firmware as firmware_module
from .config import Config
from .firmware import FirmwareError, FirmwareStore

EVENTS = {
    "added": "入库", "requested": "要推给设备", "cancelled": "取消了", "removed": "删掉了",
    "installed": "设备装好了", "failed": "没装成", "gave_up": "不再自动重推",
}


def add_arguments(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "firmware", help="设备固件：入库、查看、指定推给设备的那一版",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = parser.add_subparsers(dest="action", metavar="动作")
    actions.add_parser("list", help="仓库里有哪些版本")
    actions.add_parser("status", help="设备现在跑的是哪一版、在等什么")

    add = actions.add_parser("add", help="入库一个固件文件（应用镜像或合并镜像）")
    add.add_argument("file")
    add.add_argument("--note", default="", help="给这一版写一句备注")
    add.add_argument("--push", action="store_true", help="入库后马上推给设备")

    fetch = actions.add_parser("fetch", help="取回 GitHub 上构建好的固件并入库")
    fetch.add_argument("tag", nargs="?", help="发布的标签；不写就取最新的")
    fetch.add_argument("--repo", help="GitHub 仓库（用户名/仓库名）；不写就用配置里的 firmware.repo")
    fetch.add_argument(
        "--commit", help="只要从这个提交构建出来的；写 HEAD 表示 firmware.source_dir 现在所在的提交")
    fetch.add_argument("--wait", type=int, default=0, metavar="秒",
                       help="还没构建出来就等，最多这么久")
    fetch.add_argument("--note", default="")
    fetch.add_argument("--push", action="store_true", help="入库后马上推给设备")
    fetch.add_argument("--detach", action="store_true",
                       help="放到后台去等和取，这条命令马上返回（日志在固件仓库的 fetch.log）")

    build = actions.add_parser("build", help="在这台电脑上构建固件并入库（要先配置 firmware.build_command）")
    build.add_argument("--note", default="")
    build.add_argument("--push", action="store_true", help="入库后马上推给设备")

    for name, text in (("push", "把这一版推给设备"), ("restore", "恢复到这一版（和 push 一样）")):
        push = actions.add_parser(name, help=text)
        push.add_argument("version", help="序号、编号的开头、latest、current 或 previous")
        push.add_argument("--force", action="store_true",
                          help="这一版装上后不能再经蓝牙换固件也照推")
    actions.add_parser("cancel", help="不推了")
    remove = actions.add_parser("remove", help="从仓库里删掉一版")
    remove.add_argument("version")


def _size(count: int) -> str:
    return "%.2f MB" % (count / 1048576.0)


def _when(stamp: Any) -> str:
    if not isinstance(stamp, (int, float)) or stamp <= 0:
        return "-"
    return time.strftime("%m-%d %H:%M", time.localtime(stamp))


def _line(entry: Dict[str, Any], device: Optional[Dict[str, Any]],
          target: Optional[Dict[str, Any]]) -> str:
    marks = ""
    if device and entry["build"] == device.get("build"):
        marks += "●"
    if target and entry["id"] == target.get("id"):
        marks += "→"
    extra = "" if entry.get("updatable") else "  [装上后只能插线换]"
    return "%-2s %-9s %s  %-10s  %s  %s  %s%s" % (
        marks, "第 %d 版" % entry["seq"], entry["id"], (entry.get("version") or "-")[:10],
        _when(entry.get("added_at")), _size(entry["size"]), entry.get("note") or "", extra)


def _describe(entry: Dict[str, Any]) -> str:
    return "第 %d 版（%s%s）" % (
        entry["seq"], entry["id"], "，" + entry["note"] if entry.get("note") else "")


def _stored(store: FirmwareStore, blob: bytes, source: str, note: str, push: bool,
            say: Callable[[str], None]) -> None:
    entry = store.add(blob, source, note)
    say("%s%s：%s，%s" % ("仓库里已经有这一版了，" if entry["existed"] else "已入库 ",
                        _describe(entry), entry.get("version") or "没有版本号",
                        _size(entry["size"])))
    if push:
        store.set_target(entry["id"], "push")
        say("等手机 App 把它推给设备。")


def _detach(config_path: Path, state_dir: Path, arguments: List[str]) -> Path:
    """把同一条命令（去掉 --detach）放到后台去跑，输出写进日志。"""
    log_path = state_dir / "firmware" / "fetch.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "xiaoyou_runtime", "--config", str(config_path.resolve()),
               "firmware"] + [item for item in arguments if item != "--detach"]
    options: Dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = 0x00000008 | 0x00000200  # 脱离控制台，自成一组
    else:
        options["start_new_session"] = True
    with open(log_path, "ab") as log:
        log.write(("\n[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                    " ".join(command[4:]))).encode("utf-8"))
        log.flush()
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         cwd=str(Path(__file__).resolve().parent.parent),
                         env=dict(os.environ, PYTHONUNBUFFERED="1"), **options)
    return log_path


def run(config: Config, config_path: Path, args: Any, arguments: List[str],
        out: Callable[[str], None] = print,
        opener: Callable[..., Any] = firmware_module.urllib.request.urlopen) -> int:
    """执行一条 firmware 子命令；返回进程退出码。arguments 是 firmware 后面的原始参数。"""
    store = FirmwareStore(config.state_dir)
    try:
        if args.action in (None, "list"):
            snapshot = store.snapshot()
            if not snapshot["versions"]:
                out("仓库里还没有固件。用 firmware fetch 取 GitHub 上构建好的，或者 firmware add 文件。")
                return 0
            for entry in snapshot["versions"]:
                out(_line(entry, snapshot["device"], snapshot["target"]))
            out("● 设备现在跑的    → 等着推给设备的")
            return 0

        if args.action == "status":
            snapshot = store.snapshot()
            device = snapshot["device"]
            target = snapshot["target"]
            if device is None:
                out("设备：还没报过。手机 App 连上设备、又连得上这台 Runtime 时才会报。")
            else:
                known = "第 %d 版" % device["seq"] if device.get("seq") else "不在仓库里的一版"
                out("设备：%s（build %s，%s%s），%s 报的" % (
                    known, device.get("build") or "?", device.get("ver") or "没有版本号",
                    "，新固件还在等认可" if device.get("state") == "pending" else "",
                    _when(device.get("seen_at"))))
                progress = device.get("progress")
                if isinstance(progress, dict) and progress.get("size"):
                    out("      正在传：%d%%（%s / %s）" % (
                        progress["sent"] * 100 // progress["size"], _size(progress["sent"]),
                        _size(progress["size"])))
                if device.get("event") in ("failed", "unsupported") and device.get("detail"):
                    out("      上次没成：%s" % device["detail"])
            if target is None:
                out("要推的：没有")
            else:
                out("要推的：第 %d 版（%s），%s 提出的%s" % (
                    target["seq"], target["id"], _when(target.get("requested_at")),
                    "，已经试过 %d 次" % target["attempts"] if target.get("attempts") else ""))
            for item in snapshot["log"][-8:]:
                out("  %s  %s  %s  %s" % (_when(item.get("at")),
                                          EVENTS.get(item.get("event"), item.get("event")),
                                          item.get("id", ""), item.get("detail", "")))
            return 0

        if args.action == "add":
            try:
                blob = Path(args.file).read_bytes()
            except OSError as error:
                raise FirmwareError("读不了 %s：%s" % (args.file, error))
            _stored(store, blob, "file:" + Path(args.file).name, args.note, args.push, out)
            return 0

        if args.action == "fetch":
            repo = args.repo or config.firmware_repo
            if not repo:
                raise FirmwareError("不知道去哪个 GitHub 仓库取：在配置里设置 firmware.repo，或者加 --repo 用户名/仓库名")
            commit = args.commit
            if commit == "HEAD":
                if config.firmware_source_dir is None:
                    raise FirmwareError("--commit HEAD 要先在配置里设置 firmware.source_dir（固件源码在哪）")
                commit = firmware_module.head_commit(config.firmware_source_dir)
            if args.detach:
                # 先把 HEAD 换成此刻的提交再放到后台：之后源码目录再变也不影响这一次。
                arguments = list(arguments)
                if args.commit == "HEAD":
                    arguments[arguments.index("HEAD")] = commit
                log_path = _detach(config_path, config.state_dir, arguments)
                out("已经放到后台去取了；进展看 %s，或者 firmware status" % log_path)
                return 0
            blob, found = firmware_module.fetch_release(
                repo, config.firmware_asset, config.firmware_tag_prefix, args.tag, commit,
                args.wait, opener=opener, token=os.environ.get("GITHUB_TOKEN"), say=out)
            _stored(store, blob, "github:%s@%s" % (found["tag"], found["commit"][:10]),
                    args.note, args.push, out)
            return 0

        if args.action == "build":
            if config.firmware_source_dir is None:
                raise FirmwareError("配置里没有 firmware.source_dir：不知道固件源码在哪")
            out("构建中（%s）……" % " ".join(config.firmware_build_command or ["?"]))
            blob = firmware_module.build_locally(
                config.firmware_build_command, config.firmware_source_dir,
                config.firmware_source_dir / config.firmware_build_output)
            _stored(store, blob, "build:" + config.name, args.note, args.push, out)
            return 0

        if args.action in ("push", "restore"):
            entry = store.set_target(args.version, args.action, args.force)
            out("%s 等着推给设备：手机 App 连着设备时会自己动手，大约一两分钟。" % _describe(entry))
            return 0

        if args.action == "cancel":
            out("好，不推了。" if store.clear_target() else "本来就没有要推的。")
            return 0

        if args.action == "remove":
            out("已从仓库里删掉 %s。" % _describe(store.remove(args.version)))
            return 0
    except FirmwareError as error:
        print("没成功：%s" % error, file=sys.stderr)
        return 1
    return 2
