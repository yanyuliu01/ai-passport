"""命令行入口：python3 -m xiaoyu_runtime --config config.json

不带子命令就是启动服务。子命令 firmware 管这台电脑上留着的设备固件，
见 python3 -m xiaoyu_runtime firmware --help；子命令 usage 现在去问一次订阅额度。
"""

from xiaoyu_runtime.compat import env_get as _identity_env_get
import argparse
import dataclasses
import ipaddress
import json
import os
import signal
import threading
import socket
import sys
import time
from pathlib import Path

from .events import reports as reports_module
from . import __version__, app_release, firmware_cli
from .helpers.factory import create as create_agent
from .loop import router as router_module, workspace as workspace_module
from .events import stt
from .helpers.agents import AgentError
from .config import ConfigError, load
from .world.identity import IdentityError
from .events.desktop_sync import DesktopSync
from .helpers import desktop_handoff, model_gateway
from .iterate import cli as iterate_cli
from .firmware import FirmwareStore
from .delivery.mesh import Mesh
from .api.server import make_server
from .api.service import Service
from .world.store import Store
from .world.usage import EFFORT_NAMES
from .helpers.usage import create_usage
from .loop.xiaoyu import Turn, Xiaoyu


def _once_turn(config, args, xiaoyu, store):
    """CLI input uses the same durable request/effect path as connected clients."""
    service = Service(xiaoyu, store)
    mesh = Mesh(config, service, xiaoyu) if config.mesh_secret else None
    def close():
        if mesh is not None:
            mesh.close()
        service.close()
    try:
        if mesh is not None:
            mesh.start()
        message = service.submit(args.once, conversation=args.conversation, agent=args.agent)
        deadline = time.monotonic() + config.voice_timeout_seconds + 30
        seen = 0
        while True:
            message = service.get(message["id"], wait=.5)
            for event in message.get("events", [])[seen:]:
                print("（%s %s：%s）" % (event["kind"], event.get("agent") or "-", event["text"]), file=sys.stderr)
            seen = len(message.get("events", []))
            if message["status"] in ("done", "failed"):
                break
            if time.monotonic() >= deadline:
                raise AgentError("消息已保存，仍在等待原对话的处理结果；编号 %s" % message["id"])
        if message["status"] != "done":
            raise AgentError(message.get("error") or "这次操作没有完成")
        card = xiaoyu.cards.get(message.get("card")) or {}
        return Turn(message["reply"], message["brief"], message["mood"], message["agent"],
                    message.get("card") or "", message.get("started", card.get("state") in ("working", "waiting"))), close
    except Exception:
        close()
        raise


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _lan_address() -> str:
    """这台机器在局域网里的地址；查不到时返回 127.0.0.1。不会真的发出数据包。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def main(argv=None) -> int:
    # Windows 的控制台不一定能显示所有字符：显示不了的换成问号，不要因此崩溃。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="xiaoyu_runtime", description="小鱼 Runtime")
    parser.add_argument(
        "--config", default=_identity_env_get("XIAOYU_CONFIG", "config.json"),
        help="配置文件路径（默认 ./config.json，或环境变量 XIAOYU_CONFIG）",
    )
    parser.add_argument("--check", action="store_true", help="只检查配置，然后退出")
    parser.add_argument(
        "--pair", action="store_true",
        help="打印给手机 App 粘贴的连接串（含令牌，别发给别人），然后退出",
    )
    parser.add_argument("--once", metavar="TEXT", help="不启动服务，直接说一句话并打印回复")
    parser.add_argument("--conversation", default="default", help="--once 使用的对话名")
    parser.add_argument("--agent", help="--once 时点名交给这个代理；不写就由小鱼决定")
    parser.add_argument(
        "--stt", metavar="WAV", help="不启动服务，只把一个 WAV 文件识别成文字并打印（检查语音识别配置）",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--desktop-sync", action="store_true",
                        help="同步本机 Codex、Claude Code 会话状态到手机和硬件（只读）")
    parser.add_argument("--desktop-project", action="append", default=[], metavar="PATH",
                        help="只同步这个项目的电脑会话；可重复，不写则同步所有项目")
    parser.add_argument("--desktop-codex-dir", type=Path, metavar="PATH",
                        help="电脑端 Codex 配置目录（默认 ~/.codex）")
    parser.add_argument("--desktop-claude-dir", type=Path, metavar="PATH",
                        help="电脑端 Claude Code 配置目录（默认 ~/.claude）")
    commands = parser.add_subparsers(dest="command", metavar="子命令")
    firmware_cli.add_arguments(commands)
    app_release.add_arguments(commands)
    reports_module.add_arguments(commands)
    desktop_handoff.add_arguments(commands)
    from .api import conversation_cli
    conversation_cli.add_arguments(commands)
    iterate_cli.add_arguments(commands)
    iterate_cli.add_event_arguments(commands)
    model_gateway.add_arguments(commands)
    usage_parser = commands.add_parser(
        "usage", help="现在去问一次订阅额度，打印每个账号还剩多少、每个帮手用的模型和档位",
        description="现在去问一次 Claude Code 和 Codex 的订阅额度并打印。不调用模型，不用先启动服务。")
    usage_parser.add_argument("--detail", action="store_true", help="多打印账号对应的配置目录")
    usage_parser.add_argument("--json", action="store_true", help="打印原始数据（JSON）")
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(arguments)

    try:
        config = load(Path(args.config))
    except ConfigError as error:
        print("配置有问题：%s" % error, file=sys.stderr)
        return 2
    config = dataclasses.replace(config, desktop_sync=args.desktop_sync or config.desktop_sync,
        desktop_codex_dir=args.desktop_codex_dir.expanduser() if args.desktop_codex_dir else config.desktop_codex_dir,
        desktop_claude_dir=args.desktop_claude_dir.expanduser() if args.desktop_claude_dir else config.desktop_claude_dir,
        desktop_projects=[Path(path) for path in args.desktop_project] or config.desktop_projects)
    for notice in config.notices:
        print("提示：%s" % notice, file=sys.stderr)
    if args.command == "desktop":
        return desktop_handoff.run_cli(config, args)
    if args.command == 'conversations':
        return conversation_cli.run_cli(config, args)
    if args.command == "firmware":
        # 只动固件仓库，不需要代理、语音识别这些，所以在它们之前就处理掉。
        return firmware_cli.run(config, Path(args.config), args,
                                arguments[arguments.index("firmware") + 1:])
    if args.command == "report":
        return reports_module.run(config, args)
    if args.command == "app":
        if args.action is None:
            parser.parse_args(["app", "--help"])
        return app_release.run(config, args)
    if args.command == "iterate":
        return iterate_cli.run(config, args)
    if args.command == "events":
        return iterate_cli.run_events(config, args)
    if args.command == "models":
        return model_gateway.run(config, args)
    if args.command == "usage":
        meter = create_usage(config, enabled=True)
        meter.refresh()
        if args.json:
            print(json.dumps(meter.snapshot(), ensure_ascii=False, indent=1))
        else:
            print(meter.report(args.detail))
        return 0
    agents = [create_agent(spec) for spec in config.agents]
    router = router_module.create(
        config, lambda message: print(message, file=sys.stderr))
    roster = "、".join(
        "%s（%s%s%s）" % (
            agent.name, agent.type, "" if agent.speaks else "，只干活",
            "，%s" % EFFORT_NAMES[agent.spec.effort] if agent.spec.effort else "")
        for agent in agents
    )
    if args.check:
        print("配置没问题：监听 %s:%d，代理 %s，默认交给 %s，路由 %s，语音识别 %s" % (
            config.host, config.port, roster, config.default_agent, config.router_type,
            config.stt_engine,
        ))
        failed = False
        for agent in agents:
            problem = agent.check()
            if problem:
                print("代理 %s 还用不了：%s" % (agent.name, problem), file=sys.stderr)
                failed = True
        problem = router.check()
        if problem:
            print("路由器还用不了：%s" % problem, file=sys.stderr)
            failed = True
        problem = stt.check(stt.create(config))
        if problem:
            print("语音识别还用不了：%s" % problem, file=sys.stderr)
            failed = True
        return 2 if failed else 0

    if args.stt is not None:
        try:
            seconds = stt.describe_wav(Path(args.stt))
            started = time.monotonic()
            text = stt.create(config).transcribe(Path(args.stt))
        except stt.SttError as error:
            print("没成功：%s" % error, file=sys.stderr)
            return 1
        print(text)
        print("（录音 %.1f 秒，识别用了 %.1f 秒，含加载模型）" % (
            seconds, time.monotonic() - started), file=sys.stderr)
        return 0

    if args.pair:
        # 监听本机或所有地址时，手机要连的是这台机器在局域网里的地址。
        local_only = _is_loopback(config.host) or config.host in ("0.0.0.0", "::")
        host = _lan_address() if local_only else config.host
        print("http://%s:%d#%s" % (host, config.port, config.token))
        if _is_loopback(config.host):
            print(
                "注意：现在只监听本机（%s），手机连不上。启动时把 server.host 改成 0.0.0.0，"
                "或设置环境变量 XIAOYU_HOST=0.0.0.0。" % config.host,
                file=sys.stderr,
            )
        return 0

    try:
        store = Store(config.state_dir, legacy_agent=config.default_agent)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2
    # 只说一句就走（--once）时不主动去问额度：那要多起一个进程，而这句话多半用不上。
    xiaoyu = Xiaoyu(config, agents, store, router, workspace_module.create(config),
                      create_usage(config, enabled=config.usage_enabled and args.once is None))

    if args.once is not None:
        if args.agent is not None and not xiaoyu.has(args.agent):
            print("没有叫 %s 的代理（现在有：%s）" % (
                args.agent, "、".join(agent.name for agent in agents)), file=sys.stderr)
            return 2
        try:
            turn, close_once = _once_turn(config, args, xiaoyu, store)
        except AgentError as error:
            print("没成功：%s" % error, file=sys.stderr)
            return 1
        if turn.started:
            # 这件事交到后台了：先把她的第一句打出来，再等结果。
            print("（%s：%s）" % (turn.card, turn.reply), file=sys.stderr)
            # 总时长不限（默认）时一直等：卡没卡住由 Runtime 自己看着，到时会来问。
            limit = config.agent(turn.agent).timeout_seconds
            deadline = (time.monotonic() + limit + config.voice_timeout_seconds + 30
                        if limit else float("inf"))
            try:
                while True:
                    card = xiaoyu.settle(turn.card, 0.5)
                    if card["state"] not in ("working", "waiting") or time.monotonic() > deadline:
                        break
                    for approval in xiaoyu.approvals.pending():
                        # 没有手机和设备在场：在终端里问。
                        stalled = approval["kind"] == "stall"
                        print("（%s%s）\n%s" % (
                            "" if stalled else "%s 想做：" % approval["agent"], approval["tool"],
                            approval["detail"]), file=sys.stderr)
                        allowed = False
                        if sys.stdin.isatty():
                            allowed = input("接着等吗？[y/N] " if stalled else "可以吗？[y/N] "
                                            ).strip().lower() in ("y", "yes")
                        else:
                            print("（这里没法问你，当作不行）", file=sys.stderr)
                        xiaoyu.approvals.answer(approval["id"], "allow" if allowed else "deny", by="computer")
            except KeyboardInterrupt:
                xiaoyu.cancel(turn.card, actor="cli", reason="Ctrl+C")
                xiaoyu.close()
                print("取消了", file=sys.stderr)
                return 1
            close_once()
            said = [entry["text"] for entry in card["entries"] if entry["role"] == "xiaoyou"]
            if card["state"] != "done":
                print("没成功：%s" % (said[-1] if said else card["state"]), file=sys.stderr)
                return 1
            print("[%s] %s" % (card["mood"], card["brief"]))
            print()
            print(said[-1])
            return 0
        close_once()
        print("[%s] %s" % (turn.mood, turn.brief))
        print()
        print(turn.reply)
        return 0

    recognizer = stt.create(config)
    problem = stt.check(recognizer)
    if problem:
        print("注意：语音识别还用不了，语音消息会失败：%s" % problem, file=sys.stderr)
    for agent in agents:
        problem = agent.check()
        if problem:
            print("注意：代理 %s 还用不了，交给它的事会失败：%s" % (agent.name, problem),
                  file=sys.stderr)
    try:
        service = Service(xiaoyu, store, recognizer)
    except IdentityError as error:
        print("身份文件有问题：%s" % error, file=sys.stderr)
        return 2
    # 小鱼网络：配置里有 mesh.secret 就加入（同一个主人的几台 Runtime 互相同步，见 mesh.py）。
    mesh = Mesh(config, service, xiaoyu) if config.mesh_secret else None
    try:
        server = make_server(config, service, FirmwareStore(config.state_dir))
    except OSError as error:
        print("没法监听 %s:%d：%s" % (config.host, config.port, error), file=sys.stderr)
        service.close()
        return 1
    if mesh is not None:
        mesh.start()
        print("已加入小鱼网络：先去找 %s；告诉别人从 %s 连这台" % (
            "、".join(config.mesh_peers) or "（没写，等别人来连）",
            "、".join(config.mesh_advertise) or "（不对外，只往外连）"), file=sys.stderr)
    xiaoyu.start_pulse()
    desktop = None
    if args.desktop_sync or config.desktop_sync:
        # 命令行写了的优先，没写的用配置里的。
        codex_dir = args.desktop_codex_dir.expanduser() if args.desktop_codex_dir else config.desktop_codex_dir
        claude_dir = args.desktop_claude_dir.expanduser() if args.desktop_claude_dir else config.desktop_claude_dir
        projects = [Path(path) for path in args.desktop_project] or list(config.desktop_projects)
        desktop = DesktopSync(xiaoyu.cards, config.state_dir, codex_dir=codex_dir,
                              claude_dir=claude_dir, projects=projects,
                              owned=store.session_ids, approvals=xiaoyu.approvals)
        desktop.start()
        print("电脑会话同步已开启；审批 Hook 通过手机和设备回应", file=sys.stderr)
    from .iterate.pipeline import settings_of
    from .iterate.scheduler import Scheduler
    iterate_settings = settings_of(config)
    iterator = None
    if iterate_settings.enabled:
        iterator = Scheduler(Path(args.config).resolve(), iterate_settings, xiaoyu.cards)
        iterator.start()
        print("自动迭代已开启：空闲时检查使用信号（iterate status 看结果）", file=sys.stderr)
    print("小鱼 Runtime %s「%s」已启动：http://%s:%d（代理 %s，默认交给 %s，语音识别 %s）" % (
        __version__, config.name, config.host, config.port, roster, config.default_agent,
        config.stt_engine), file=sys.stderr)
    if not _is_loopback(config.host):
        print(
            "注意：正在监听非本机地址，而这个服务本身只有明文 HTTP。"
            "请只在可信网络里用，或者前面加一层加密通道（见 README）。",
            file=sys.stderr,
        )
    def stop(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    # 被 kill（比如 pkill）时也走下面的收尾：后台的事各有自己的进程组，不收尾会留下来。
    try:
        signal.signal(signal.SIGTERM, stop)
    except (ValueError, OSError):
        pass  # 不在主线程里，或者这个平台没有
    loopback = getattr(server, "loopback", None)
    if loopback is not None:
        threading.Thread(target=loopback.serve_forever, name="loopback-http", daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if loopback is not None:
            loopback.shutdown()
            loopback.server_close()
        if desktop is not None:
            desktop.close()
        if iterator is not None:
            iterator.close()
        if mesh is not None:
            mesh.close()
        service.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
