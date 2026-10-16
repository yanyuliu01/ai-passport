"""Import selected completed sessions and open them without editing app databases."""

import os
import json
import re
import subprocess
import sys
import time
import tempfile
from pathlib import Path

from .agents import AgentError

PROVIDER_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


def export_session(spec, env, session_id, *, home=None):
    """Import one completed transcript into the native default home.

    Never merge configuration/auth or write an app database. Publish by hard
    link after a complete temporary write, so an existing desktop transcript
    cannot be overwritten, including by concurrent exporters.
    """
    home = Path.home() if home is None else Path(home)
    values = env or os.environ
    codex = spec.type == "codex"
    default = home / (".codex" if codex else ".claude")
    key = "CODEX_HOME" if codex else "CLAUDE_CONFIG_DIR"
    source = Path(values.get(key, str(default))).expanduser().resolve()
    default = default.resolve()
    if source == default:
        return ""
    folder = "sessions" if codex else "projects"
    root = source / folder
    pattern = "*-%s.jsonl" % session_id if codex else session_id + ".jsonl"
    matches = [p for p in root.rglob(pattern) if p.is_file() and not p.is_symlink()]
    if len(matches) != 1:
        return "找不到唯一的原始会话记录，未导入桌面端"
    original = matches[0]
    target = default / folder / original.relative_to(root)
    if not target.resolve().is_relative_to(default):
        return "桌面会话路径超出配置目录，未导入"
    data = original.read_bytes()
    if target.is_symlink():
        return "桌面会话路径是链接，未覆盖"
    if target.exists():
        if target.read_bytes() == data:
            return ""
        return "桌面端已有不同内容的同名会话，未覆盖；请在桌面端继续处理"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".xiaoyou-", suffix=".tmp",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or target.read_bytes() != data:
                return "桌面端会话已变化，未覆盖"
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return ""


def _terminal_launch(command, cwd, env, timeout=15):
    """Claude --desktop refuses redirected I/O; provide a short-lived PTY.

    Drain and discard terminal output so diagnostics cannot expose credentials.
    No prompt is supplied and this command must never start an agent turn.
    """
    import pty
    import select
    master, slave = pty.openpty()
    process = None
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=slave,
                                   stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        slave = None
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(command, timeout)
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    os.read(master, 65536)
                except OSError:
                    pass
        return subprocess.CompletedProcess(command, process.returncode)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
        if slave is not None:
            os.close(slave)


def open_session(agent, session_id, *, run=subprocess.run, platform=None):
    """Return an actionable warning on failure; never turn task success into failure.

    This is a desktop handoff, not a second model invocation. Call only after all
    queued rounds of a background task have finished and its CLI process exited.
    """
    spec = agent.spec
    platform = sys.platform if platform is None else platform
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", session_id):
        return "没有可用的代理会话编号，无法在桌面端打开"
    if spec.type not in ("codex", "claude_code"):
        return "这个代理不支持桌面端会话"
    if platform != "darwin":
        return "当前平台未配置桌面端打开方式"
    try:
        env = agent._env()
        if spec.type == "codex":
            command = ["open", "codex://threads/" + session_id]
        else:
            if any(spec.env.get(key) or key in spec.env_files for key in PROVIDER_ENV):
                return "Claude 桌面会话交接需要订阅登录；当前代理使用 API 或第三方服务"
            # This command only opens a transcript. Use the configured local
            # subscription login, not provider routing inherited by the host
            # process. The worker's environment and authentication are unchanged.
            env = dict(env or os.environ)
            for key in PROVIDER_ENV:
                env.pop(key, None)
            command = list(spec.command) + ["--desktop", "--resume", session_id]
        warning = export_session(spec, env, session_id)
        if warning:
            return warning
        cwd = str(spec.workdir) if spec.workdir else None
        if spec.type == "claude_code" and run is subprocess.run:
            done = _terminal_launch(command, cwd, env)
        else:
            done = run(command, cwd=cwd, env=env,
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, encoding="utf-8", errors="replace", timeout=15)
        if done.returncode:
            # CLI diagnostics may contain provider credentials; never echo them.
            return "桌面端打开失败（退出码 %d）；请检查 App 安装、订阅登录和 CLI 版本" % done.returncode
    except (OSError, subprocess.TimeoutExpired, AgentError):
        return "桌面端打开失败；请检查 App 安装、配置目录和 CLI 版本，任务结果已保留"
    return ""


def completed(task, outcome, error):
    """Best effort, once per successful task, not once per agent turn."""
    if task.cancelled or error is not None or outcome is None or not task.agent.spec.desktop_open:
        return
    warning = open_session(task.agent, outcome.session_id)
    if warning:
        task.control.progress("桌面端：" + warning)
        print("[xiaoyou desktop] " + warning, file=sys.stderr)


def add_arguments(commands):
    parser = commands.add_parser("desktop", help="列出或在原生桌面 App 中打开小幽派发的任务")
    parser.add_argument("action", choices=("list", "open"))
    parser.add_argument("--task", help="卡片编号，如 c149")
    parser.add_argument("--agent", help="配置中的代理名，如 cc 或 codex")
    parser.add_argument("--conversation", help="对话名；同一张卡有多个匹配时指定")


def run_cli(config, args):
    # Read files directly: constructing Store/Cards here would perform startup
    # cleanup and mark an in-flight task interrupted in the running Runtime.
    try:
        sessions = json.loads((config.state_dir / "sessions.json").read_text(encoding="utf-8"))
        cards = json.loads((config.state_dir / "cards.json").read_text(encoding="utf-8"))
        if not isinstance(sessions, dict) or not isinstance(cards, dict):
            raise ValueError("invalid state")
    except (OSError, ValueError):
        print("无法读取任务会话记录", file=sys.stderr)
        return 2
    matches = []
    for card in cards.get("cards", []):
        if not isinstance(card, dict) or card.get("monitoring_only"):
            continue
        if args.task and card.get("id") != args.task:
            continue
        conversation = card.get("conversation", "default")
        if args.conversation and conversation != args.conversation:
            continue
        key = "%s/%s" % (conversation, card.get("id"))
        owned = sessions.get(key, {})
        if not isinstance(owned, dict):
            continue
        for name, session_id in owned.items():
            spec = config.agent(name)
            if spec is None or spec.type not in ("codex", "claude_code"):
                continue
            if args.agent and name != args.agent:
                continue
            matches.append((card, spec, session_id))
    if args.action == "list":
        for card, spec, sid in matches:
            print("%s\t%s\t%s\t%s\t%s" % (
                card["id"], spec.name, card.get("state", "unknown"), sid, card.get("title", "")))
        return 0
    if not args.task or len(matches) != 1:
        print("请用 desktop list 查看，再指定 --task 和 --agent 选择一个会话", file=sys.stderr)
        return 2
    card, spec, sid = matches[0]
    if card.get("state") not in ("done", "failed", "cancelled"):
        print("任务仍在运行或状态未知，先等任务结束再交接到桌面端", file=sys.stderr)
        return 2
    from .agents import create
    warning = open_session(create(spec), sid)
    if warning:
        print(warning, file=sys.stderr)
        return 2
    print("已请求桌面 App 打开原会话；实际显示和接管结果请在 App 内确认")
    return 0
