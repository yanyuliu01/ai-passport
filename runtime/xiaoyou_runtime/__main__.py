"""命令行入口：python3 -m xiaoyou_runtime --config config.json"""

import argparse
import ipaddress
import os
import sys
from pathlib import Path

from . import __version__
from .backends import BackendError, create
from .config import ConfigError, load
from .server import make_server
from .service import Service
from .store import Store


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="xiaoyou_runtime", description="小幽 Runtime")
    parser.add_argument(
        "--config", default=os.environ.get("XIAOYOU_CONFIG", "config.json"),
        help="配置文件路径（默认 ./config.json，或环境变量 XIAOYOU_CONFIG）",
    )
    parser.add_argument("--check", action="store_true", help="只检查配置，然后退出")
    parser.add_argument("--once", metavar="TEXT", help="不启动服务，直接说一句话并打印回复")
    parser.add_argument("--conversation", default="default", help="--once 使用的对话名")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    try:
        config = load(Path(args.config))
    except ConfigError as error:
        print("配置有问题：%s" % error, file=sys.stderr)
        return 2
    if args.check:
        print("配置没问题：后端 %s，监听 %s:%d，工具 %s" % (
            config.backend, config.host, config.port,
            "、".join(tool.name for tool in config.tools) or "无",
        ))
        return 0

    try:
        store = Store(config.state_dir)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2
    backend = create(config)

    if args.once is not None:
        try:
            turn = backend.turn(args.once, store.session(args.conversation))
        except BackendError as error:
            print("没成功：%s" % error, file=sys.stderr)
            return 1
        if turn.session_id:
            store.remember(args.conversation, turn.session_id)
        print("[%s] %s" % (turn.mood, turn.brief))
        print()
        print(turn.reply)
        return 0

    service = Service(backend, store)
    try:
        server = make_server(config, service)
    except OSError as error:
        print("没法监听 %s:%d：%s" % (config.host, config.port, error), file=sys.stderr)
        return 1
    print("小幽 Runtime %s 已启动：http://%s:%d（后端 %s）" % (
        __version__, config.host, config.port, config.backend), file=sys.stderr)
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
