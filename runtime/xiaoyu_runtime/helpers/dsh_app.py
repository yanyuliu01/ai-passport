"""用 Agent Client Protocol 驱动 DeepSeek Harness（dsh）：`dsh --profile acp`，
标准输入输出上逐行一个 JSON-RPC 消息。

跟 codex_app.py 驱动 Codex 的 app-server 是同一个思路，顺序也差不多：

  → initialize                 ← 结果
  → session/new 或 session/resume  ← 结果里有会话编号、当前的档位（模型、努力程度）
  → session/set_config_option（要换模型或努力程度才发）  ← 结果
  → session/prompt              ← 这一轮结束才回（结果只有 stopReason）
  ← 通知：session/update（agent_message_chunk、agent_thought_chunk、
         tool_call、tool_call_update、usage_update……）
  ← 请求（带 id，要回答）：session/request_permission
  中途要停：发一条 session/cancel 通知（没有 id，不等回答）

dsh 没有 Claude Code 那种“每一步都问主人”的通道：沙箱（spec.sandbox，对应环境变量
DSH_PERMISSION_MODE）内的操作它自己做，出了沙箱才可能来问（session/request_permission）。
usage_update 报的是这一轮占了多少上下文（used/size），不是计费用的 token 数：这条线上
的用量统计只到“用的是哪个模型”，不记 in/out/cached。

协议对的是 dsh 0.2.0-rc.2 的 ACP 实现；握手时协议版本或方法名不对就说清楚，不猜。
"""

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import __version__
from .base import (
    AgentError, Cancelled, Control, Job, Outcome, Watchdog, clip, fields_from_text, kill_tree
)
from ..config import AgentSpec

HANDSHAKE_SECONDS = 20
PROMPT_PROVIDER = "deepseek-official"
# 一直没有任何消息、进程也没退出，等这么久就不等了（比如网络断了，它在后台重试）。
STALL_SECONDS = 120
# 打断之后给它这么久收尾；还不结束就直接停掉进程。
INTERRUPT_SECONDS = 5


def _model_value(model: str) -> str:
    return json.dumps([PROMPT_PROVIDER, model], separators=(",", ":"))


def progress_of(update: Dict[str, Any]) -> Optional[str]:
    """一条 tool_call 开始时的进展行；不算一步操作的返回 None。"""
    kind = update.get("sessionUpdate")
    if kind != "tool_call":
        return None
    title = update.get("title") if isinstance(update.get("title"), str) else ""
    raw_input = update.get("rawInput")
    if isinstance(raw_input, dict):
        path = raw_input.get("file_path") or raw_input.get("path")
        command = raw_input.get("command")
        if isinstance(path, str):
            return "%s %s" % (title, path) if title else path
        if isinstance(command, str):
            return command
    return title or "操作"


class AcpServer:
    """一轮对话用的一个 `dsh --profile acp` 进程。"""

    def __init__(self, spec: AgentSpec, control: Control, env: Optional[Dict[str, str]]):
        self._spec = spec
        self._control = control
        command = list(spec.command) + ["--profile", "acp"]
        # 这个帮手自己的 skill 目录：--patch 是 dsh 自己的参数，写在 --profile 后面。
        from .helper_extras import dsh_skill_patch
        from .helper_models import dsh_patch
        self._patch: Optional[Path] = None
        lines = dsh_skill_patch(spec) + dsh_patch(spec)
        if lines:
            handle, name = tempfile.mkstemp(prefix="dsh-skills-", suffix=".yml")
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write("\n".join(lines) + "\n")
            self._patch = Path(name)
            command += ["--patch", name]
        command += list(spec.extra_args)
        command[0] = shutil.which(command[0]) or command[0]
        extra: Dict[str, Any] = {}
        if env is not None:
            extra["env"] = env
        if spec.workdir is not None:
            spec.workdir.mkdir(parents=True, exist_ok=True)
            extra["cwd"] = str(spec.workdir)
        if os.name != "nt":
            extra["start_new_session"] = True
        try:
            self._process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", **extra)
        except FileNotFoundError:
            raise AgentError("找不到命令 %s。请先安装 DeepSeek Harness，或在配置里改这个代理的 command"
                             % command[0])
        except OSError as error:
            raise AgentError("启动 dsh 失败：%s" % error)
        control.attach(self._process)
        self._write_lock = threading.Lock()
        self._next_id = 1
        self._inbox: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self._stderr: List[str] = []
        threading.Thread(target=self._read, daemon=True).start()
        self._stderr_reader = threading.Thread(
            target=lambda: self._stderr.append(self._process.stderr.read()), daemon=True)
        self._stderr_reader.start()

    def _read(self) -> None:
        for line in self._process.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict):
                self._inbox.put(message)
        self._inbox.put(None)  # 进程的输出结束了

    def send(self, message: Dict[str, Any]) -> None:
        with self._write_lock:
            try:
                self._process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                self._process.stdin.flush()
            except (OSError, ValueError):
                pass  # 进程已经没了；读的那一边会发现

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: Dict[str, Any]) -> int:
        with self._write_lock:
            request_id = self._next_id
            self._next_id += 1
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return request_id

    def reply(self, request_id: Any, result: Optional[Dict[str, Any]] = None,
             error: Optional[Dict[str, Any]] = None) -> None:
        message: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        message["error" if error is not None else "result"] = error if error is not None else (result or {})
        self.send(message)

    def next(self, seconds: float) -> Any:
        """下一条消息。超时返回 False，进程结束返回 None。"""
        try:
            return self._inbox.get(timeout=max(0.01, seconds))
        except queue.Empty:
            return False

    def expect(self, request_id: int, deadline: float, what: str) -> Dict[str, Any]:
        """等某个请求的回答；期间来的别的消息（通知、它发来的请求）先丢掉（握手阶段没有要紧的）。"""
        while True:
            message = self.next(deadline - time.monotonic())
            if message is False:
                raise AgentError("dsh 没有回应（%s）" % what)
            if message is None:
                raise AgentError("dsh 的 acp 进程退出了（%s）：%s" % (what, self.stderr_tail()))
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    error = message["error"]
                    detail = error.get("message") if isinstance(error, dict) else str(error)
                    raise AgentError("dsh 不接受 %s：%s" % (what, clip(str(detail), 200)))
                result = message.get("result")
                return result if isinstance(result, dict) else {}

    def kill(self) -> None:
        kill_tree(self._process)

    def stderr_tail(self) -> str:
        self._stderr_reader.join(timeout=2)
        text = "".join(self._stderr).strip().splitlines()
        return clip(text[-1], 200) if text else "没有输出"

    def close(self) -> None:
        try:
            self._process.stdin.close()
        except OSError:
            pass
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        kill_tree(self._process)
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        self._control.attach(None)
        if self._patch is not None:
            try:
                self._patch.unlink()
            except OSError:
                pass
        self._stderr_reader.join(timeout=2)
        for pipe in (self._process.stdout, self._process.stderr):
            try:
                pipe.close()
            except OSError:
                pass


def handshake(server: AcpServer, deadline: float) -> None:
    hello = server.request("initialize", {
        "protocolVersion": 1,
        "clientInfo": {"name": "xiaoyu-runtime", "title": "Xiaoyu", "version": __version__},
        "clientCapabilities": {},
    })
    server.expect(hello, deadline, "initialize")


def check(spec: AgentSpec, env: Optional[Dict[str, str]]) -> Optional[str]:
    """启动时的检查：只握手，不调用模型。有问题返回说明。"""
    control = Control()
    try:
        server = AcpServer(spec, control, env)
    except AgentError as error:
        return str(error)
    try:
        handshake(server, time.monotonic() + HANDSHAKE_SECONDS)
    except AgentError as error:
        return "%s" % error
    finally:
        server.close()
    return None


def run(spec: AgentSpec, job: Job, control: Control, env: Optional[Dict[str, str]],
        tier: Any = (None, None)) -> Outcome:
    """跑一轮：把这段话交给 dsh，直到它这一轮结束。tier 是这一轮用的（模型，努力程度）。"""
    if control.cancelled:
        raise Cancelled("dsh 被叫停了")
    server = AcpServer(spec, control, env)
    watch = Watchdog(control, "dsh", server.kill, job.timeout or spec.timeout_seconds,
                     spec.idle_seconds, spec.idle_command_seconds)
    try:
        return _turn(server, spec, job, control, watch, tier)
    finally:
        watch.stop()
        control.can_stop(None)
        server.close()


def _set_tier(server: AcpServer, session_id: str, deadline: float, tier: Any) -> None:
    model, effort = tier
    if model:
        from .helper_models import URL_ENV, PROVIDER
        spec = getattr(server, "_spec", None)
        provider = PROVIDER if spec is not None and URL_ENV in spec.env else PROMPT_PROVIDER
        request_id = server.request("session/set_config_option", {
            "sessionId": session_id, "configId": "model", "value": json.dumps([provider, model], separators=(",", ":"))})
        server.expect(request_id, deadline, "session/set_config_option(model)")
    if effort:
        request_id = server.request("session/set_config_option", {
            "sessionId": session_id, "configId": "reasoning_effort", "value": effort})
        server.expect(request_id, deadline, "session/set_config_option(reasoning_effort)")


def _turn(server: AcpServer, spec: AgentSpec, job: Job, control: Control,
         watch: Watchdog, tier: Any = (None, None)) -> Outcome:
    from .helper_extras import acp_servers
    deadline = time.monotonic() + HANDSHAKE_SECONDS
    handshake(server, deadline)
    cwd = str(spec.workdir) if spec.workdir else os.getcwd()
    if job.session_id:
        opened = server.request("session/resume", {"sessionId": job.session_id, "cwd": cwd})
        try:
            began = server.expect(opened, deadline, "session/resume")
        except AgentError:
            # 接不上这个会话编号（比如上一次是别的壳留下的）：当作新对话开始，
            # 不要因为接不上旧对话就让这一轮直接失败。
            opened = server.request("session/new", {"cwd": cwd, "mcpServers": acp_servers(control.gate)})
            began = server.expect(opened, deadline, "session/new")
    else:
        opened = server.request("session/new", {"cwd": cwd, "mcpServers": acp_servers(control.gate)})
        began = server.expect(opened, deadline, "session/new")
    session_id = began.get("sessionId") if isinstance(began.get("sessionId"), str) else job.session_id
    if not isinstance(session_id, str):
        raise AgentError("dsh 没有给出会话编号")
    control.session(session_id)

    model, effort = tier
    _set_tier(server, session_id, time.monotonic() + HANDSHAKE_SECONDS, tier)
    if model:
        control.model(model)

    text = job.text if job.system is None else "%s\n\n%s" % (job.system, job.text)
    if job.schema:
        text += ("\n\n请只输出一个 JSON 对象，不要代码围栏或额外说明。按以下 JSON Schema "
                "填写字段；reply 是对主人说的话。\n" + json.dumps(job.schema, ensure_ascii=False))
    prompt_id = server.request("session/prompt", {
        "sessionId": session_id, "prompt": [{"type": "text", "text": text}]})

    def stop() -> bool:
        stop_at.append(time.monotonic() + INTERRUPT_SECONDS)
        server.notify("session/cancel", {"sessionId": session_id})
        return True

    stop_at: List[float] = []
    control.can_stop(stop)

    said = ""
    public = ""
    public_id = 0
    public_at = 0.0
    problem = ""
    last_alive = time.monotonic()
    watch.start()
    while True:
        now = time.monotonic()
        message = server.next(min(stop_at[0] - now, 1.0) if stop_at else 1.0)
        now = time.monotonic()
        if message is False:
            if stop_at and now >= stop_at[0]:
                raise Cancelled("dsh 被叫停了")
            if problem and now - last_alive > STALL_SECONDS:
                raise AgentError("dsh 一直连不上：%s" % clip(problem, 200))
            continue
        if message is None:
            if control.cancelled:
                raise Cancelled("dsh 被叫停了")
            if watch.reason is not None:
                raise AgentError(watch.reason)
            raise AgentError("dsh 的 acp 进程中途退出了：%s" % (problem or server.stderr_tail()))
        if message.get("id") == prompt_id and "method" not in message:
            if public.strip():
                control.activity("message", public, "message-%s" % public_id)
                public = ""
            if "error" in message:
                error = message["error"]
                detail = error.get("message") if isinstance(error, dict) else str(error)
                raise AgentError("dsh 没有给出结果：%s" % clip(str(detail), 200))
            result = message.get("result") if isinstance(message.get("result"), dict) else {}
            reason = result.get("stopReason")
            last_alive = now
            if control.cancelled or reason == "cancelled":
                if control.cancelled:
                    raise Cancelled("dsh 被叫停了")
                raise AgentError("dsh 这一轮被打断了")
            if reason not in ("end_turn", "max_turn_requests", None):
                raise AgentError("dsh 没有正常结束这一轮（%s）：%s" % (reason, clip(problem, 200)))
            if not said.strip():
                raise AgentError("dsh 没有给出结果：%s" % clip(problem or "这一轮什么都没说", 200))
            return Outcome(said.strip(), session_id, fields_from_text(said) if job.system else None)
        method = message.get("method")
        if method is None:
            continue  # 别的请求的回答（目前没有），这里不用管
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if params.get("sessionId") not in (None, session_id):
            continue
        if "id" in message:
            threading.Thread(target=_answer, args=(server, spec, control, message),
                             daemon=True).start()
            last_alive = now
            control.touch()
            continue
        last_alive = now
        if method != "session/update":
            continue
        update = params.get("update") if isinstance(params.get("update"), dict) else {}
        kind = update.get("sessionUpdate")
        if kind in ("agent_message_chunk", "agent_thought_chunk"):
            content = update.get("content")
            chunk = content.get("text") if isinstance(content, dict) else None
            if isinstance(chunk, str) and kind == "agent_message_chunk":
                said += chunk
                public += chunk
                if now - public_at >= .5 or len(public) >= 4000:
                    control.activity("message", public, "message-%s" % public_id)
                    public_at = now
                if len(public) >= 4000:
                    public = ""
                    public_id += 1
            control.touch()
        elif kind == "tool_call":
            if public.strip():
                control.activity("message", public, "message-%s" % public_id)
                public = ""
                public_id += 1
            call_id = update.get("toolCallId") if isinstance(update.get("toolCallId"), str) else None
            line = progress_of(update)
            control.touch(began=call_id, step=line)
            if line:
                control.progress(line)
                control.activity("tool_start", line, call_id)
        elif kind == "tool_call_update":
            call_id = update.get("toolCallId") if isinstance(update.get("toolCallId"), str) else None
            terminal = update.get("status") in ("completed", "failed", "cancelled")
            control.touch(ended=call_id if terminal else None)
            if terminal:
                from ..events.desktop_content import message_text
                outputs = []
                for item in update.get("content") or []:
                    if isinstance(item, dict) and item.get("type") == "content":
                        outputs.append(message_text(item.get("content")))
                control.activity("tool_result", "%s · %s\n%s" % (
                    update["status"], update.get("title") or call_id or "操作", "\n".join(outputs)), call_id)
        else:
            control.touch()


def _answer(server: AcpServer, spec: AgentSpec, control: Control, message: Dict[str, Any]) -> None:
    """回答 dsh 发来的一个请求。目前只有越过沙箱时的 session/request_permission；其余不答应。"""
    method = message.get("method")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if method == "session/request_permission":
        options = params.get("options") if isinstance(params.get("options"), list) else []
        tool_call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        detail = json.dumps(tool_call.get("rawInput") or tool_call, ensure_ascii=False)
        title = tool_call.get("title") if isinstance(tool_call.get("title"), str) else "操作"
        asker = control.ask
        allowed = False
        if asker is not None:
            try:
                allowed = bool(asker("%s · %s" % (spec.name, title), clip(detail, 500)))
            except Exception:
                allowed = False
        choice = None
        for option in options:
            kind = str(option.get("kind") or "")
            if allowed and kind.startswith("allow"):
                choice = option.get("optionId")
                break
            if not allowed and kind.startswith("reject"):
                choice = option.get("optionId")
                break
        if choice is None and options:
            choice = options[-1 if not allowed else 0].get("optionId")
        if choice is not None:
            server.reply(message["id"], result={"outcome": {"outcome": "selected", "optionId": choice}})
        else:
            server.reply(message["id"], result={"outcome": {"outcome": "cancelled"}})
        return
    server.reply(message["id"], error={
        "code": -32601, "message": "xiaoyu-runtime does not handle %s" % method})
