"""命令行入口：python3 -m xiaoyou_runtime --config config.json"""

import argparse
import ipaddress
import os
import socket
import sys
import time
import uuid
from pathlib import Path

from . import __version__, agents as agent_module, router as router_module, stt
from .agents import AgentError
from .config import ConfigError, load
from .server import make_server
from .service import Service
from .store import Store
from .xiaoyou import Xiaoyou


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
    parser = argparse.ArgumentParser(prog="xiaoyou_runtime", description="小幽 Runtime")
    parser.add_argument(
        "--config", default=os.environ.get("XIAOYOU_CONFIG", "config.json"),
        help="配置文件路径（默认 ./config.json，或环境变量 XIAOYOU_CONFIG）",
    )
    parser.add_argument("--check", action="store_true", help="只检查配置，然后退出")
    parser.add_argument(
        "--pair", action="store_true",
        help="打印给手机 App 粘贴的连接串（含令牌，别发给别人），然后退出",
    )
    parser.add_argument("--once", metavar="TEXT", help="不启动服务，直接说一句话并打印回复")
    parser.add_argument("--conversation", default="default", help="--once 使用的对话名")
    parser.add_argument("--agent", help="--once 时点名交给这个代理；不写就由小幽决定")
    parser.add_argument(
        "--stt", metavar="WAV", help="不启动服务，只把一个 WAV 文件识别成文字并打印（检查语音识别配置）",
    )
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    try:
        config = load(Path(args.config))
    except ConfigError as error:
        print("配置有问题：%s" % error, file=sys.stderr)
        return 2
    for notice in config.notices:
        print("提示：%s" % notice, file=sys.stderr)
    agents = [agent_module.create(spec) for spec in config.agents]
    router = router_module.create(
        config, lambda message: print(message, file=sys.stderr))
    roster = "、".join(
        "%s（%s%s）" % (agent.name, agent.type, "" if agent.speaks else "，只干活")
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
                "或设置环境变量 XIAOYOU_HOST=0.0.0.0。" % config.host,
                file=sys.stderr,
            )
        return 0

    try:
        store = Store(config.state_dir, legacy_agent=config.default_agent)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2
    xiaoyou = Xiaoyou(config, agents, store, router)

    if args.once is not None:
        if args.agent is not None and not xiaoyou.has(args.agent):
            print("没有叫 %s 的代理（现在有：%s）" % (
                args.agent, "、".join(agent.name for agent in agents)), file=sys.stderr)
            return 2
        try:
            turn = xiaoyou.hear(
                args.once, args.conversation, uuid.uuid4().hex, asked=args.agent,
                report=lambda kind, agent, detail: print(
                    "（%s %s：%s）" % (kind, agent or "-", detail), file=sys.stderr),
            )
        except AgentError as error:
            print("没成功：%s" % error, file=sys.stderr)
            return 1
        if turn.started:
            # 这件事交到后台了：先把她的第一句打出来，再等结果。
            print("（%s：%s）" % (turn.card, turn.reply), file=sys.stderr)
            limit = config.agent(turn.agent).timeout_seconds + config.voice_timeout_seconds + 30
            try:
                card = xiaoyou.settle(turn.card, limit)
            except KeyboardInterrupt:
                xiaoyou.cancel(turn.card)
                xiaoyou.close()
                print("取消了", file=sys.stderr)
                return 1
            xiaoyou.close()
            said = [entry["text"] for entry in card["entries"] if entry["role"] == "xiaoyou"]
            if card["state"] != "done":
                print("没成功：%s" % (said[-1] if said else card["state"]), file=sys.stderr)
                return 1
            print("[%s] %s" % (card["mood"], card["brief"]))
            print()
            print(said[-1])
            return 0
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
    service = Service(xiaoyou, store, recognizer)
    try:
        server = make_server(config, service)
    except OSError as error:
        print("没法监听 %s:%d：%s" % (config.host, config.port, error), file=sys.stderr)
        return 1
    print("小幽 Runtime %s「%s」已启动：http://%s:%d（代理 %s，默认交给 %s，语音识别 %s）" % (
        __version__, config.name, config.host, config.port, roster, config.default_agent,
        config.stt_engine), file=sys.stderr)
    if not _is_loopback(config.host):
        print(
            "注意：正在监听非本机地址，而这个服务本身只有明文 HTTP。"
            "请只在可信网络里用，或者前面加一层加密通道（见 README）。",
            file=sys.stderr,
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
