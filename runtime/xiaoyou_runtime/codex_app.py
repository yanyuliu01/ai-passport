"""用 app-server 协议驱动 Codex：`codex app-server`，标准输入输出上逐行一个 JSON-RPC 消息。

和 `codex exec` 比，这条路多三样：Codex 要做需要确认的操作时会来问（变成给主人的
授权），做的过程中可以追加一句话（turn/steer），可以打断（turn/interrupt）。

每件事的每一轮起一个进程，这一轮结束就退出。顺序：

  → initialize                      ← 结果          → initialized（通知）
  → thread/start 或 thread/resume   ← 结果里有线程编号（就是下次接着做要用的会话）
  → turn/start                      ← 结果里有这一轮的编号
  ← 通知：item/started、item/completed……直到 turn/completed
  ← 请求（带 id，要回答）：item/commandExecution/requestApproval 等

消息的形状对着 codex-cli 0.162.0 生成的协议定义写的
（`codex app-server generate-json-schema --out <目录>`，用 v2 那一套）。这个模式在
Codex 的帮助里标着“实验性”：对不上时把代理的 mode 改回 exec。
"""

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .agents import (AgentError, Cancelled, Control, Job, Outcome, Watchdog, clip,
                     fields_from_text, kill_tree)
from .config import AgentSpec

MAX_OUTPUT_CHARS = 200000
HANDSHAKE_SECONDS = 20
STEER_SECONDS = 10
# 打断之后给它这么久收尾；还不结束就直接停掉进程。
INTERRUPT_SECONDS = 5
# 一直在“重连”、别的什么都没有，超过这么久就不等了（没登录、断网时它会无限重试）。
STALL_SECONDS = 120
MAX_DIFF_CHARS = 6000


def describe_change(changes: Any) -> str:
    """一次改文件的内容原文：每个文件的路径和改动。"""
    parts: List[str] = []
    for change in changes if isinstance(changes, list) else []:
        if not isinstance(change, dict):
            continue
        kind = change.get("kind")
        kind = kind.get("type") if isinstance(kind, dict) else ""
        path = change.get("path") if isinstance(change.get("path"), str) else ""
        diff = change.get("diff") if isinstance(change.get("diff"), str) else ""
        parts.append(("%s %s\n%s" % (kind, path, diff[:MAX_DIFF_CHARS])).strip())
    return "\n\n".join(parts)


def progress_of(item: Dict[str, Any]) -> Optional[str]:
    """一个条目开始时的进展行；不算一步操作的返回 None。"""
    kind = item.get("type")
    if kind == "commandExecution" and isinstance(item.get("command"), str):
        return item["command"]
    if kind == "fileChange":
        paths = [change.get("path") for change in item.get("changes") or []
                 if isinstance(change, dict) and isinstance(change.get("path"), str)]
        return "改文件 %s" % "、".join(paths) if paths else "改文件"
    if kind == "webSearch":
        return "搜索 %s" % item["query"] if isinstance(item.get("query"), str) else "搜索"
    if kind == "mcpToolCall":
        return "%s.%s" % (item.get("server") or "mcp", item.get("tool") or "")
    return None


class AppServer:
    """一轮对话用的一个 app-server 进程。"""

    def __init__(self, spec: AgentSpec, control: Control, env: Optional[Dict[str, str]]):
        self._spec = spec
        self._control = control
        self._name = spec.name
        command = list(spec.command) + ["app-server"] + list(spec.extra_args)
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
            raise AgentError("找不到命令 %s。请先安装并登录 Codex，或在配置里改这个代理的 command"
                             % command[0])
        except OSError as error:
            raise AgentError("启动 Codex 失败：%s" % error)
        control.attach(self._process)
        self._write_lock = threading.Lock()
        self._next_id = 1
        self._inbox: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        # 别的线程（中途追加）发出的请求在等的回答：编号 → 放回答的地方
        self._waiting: Dict[int, "queue.Queue[Dict[str, Any]]"] = {}
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

    def request(self, method: str, params: Dict[str, Any]) -> int:
        with self._write_lock:
            request_id = self._next_id
            self._next_id += 1
        self.send({"id": request_id, "method": method, "params": params})
        return request_id

    def ask_from_elsewhere(self, method: str, params: Dict[str, Any],
                           seconds: float) -> Optional[Dict[str, Any]]:
        """别的线程发一个请求并等回答（主循环负责把回答转过来）。等不到返回 None。"""
        box: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        with self._write_lock:
            request_id = self._next_id
            self._next_id += 1
            self._waiting[request_id] = box
        self.send({"id": request_id, "method": method, "params": params})
        try:
            return box.get(timeout=seconds)
        except queue.Empty:
            return None
        finally:
            with self._write_lock:
                self._waiting.pop(request_id, None)

    def next(self, seconds: float) -> Any:
        """下一条消息。超时返回 False，进程结束返回 None。"""
        try:
            message = self._inbox.get(timeout=max(0.01, seconds))
        except queue.Empty:
            return False
        if message is not None and "method" not in message:
            with self._write_lock:
                box = self._waiting.get(message.get("id"))
            if box is not None:
                box.put(message)
                return self.next(0.01)
        return message

    def expect(self, request_id: int, deadline: float, what: str) -> Dict[str, Any]:
        """等某个请求的回答；期间来的别的消息先丢掉（握手阶段没有要紧的）。"""
        while True:
            message = self.next(deadline - time.monotonic())
            if message is False:
                raise AgentError("Codex 的 app-server 没有回应（%s）。可以先把这个代理的 mode 改成 exec" % what)
            if message is None:
                raise AgentError("Codex 的 app-server 退出了（%s）：%s" % (what, self.stderr_tail()))
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    error = message["error"]
                    detail = error.get("message") if isinstance(error, dict) else str(error)
                    raise AgentError("Codex 不接受 %s：%s" % (what, clip(str(detail), 200)))
                result = message.get("result")
                return result if isinstance(result, dict) else {}

    def kill(self) -> None:
        kill_tree(self._process)

    def stderr_tail(self) -> str:
        # 进程刚退出时，它最后说的话可能还在路上。
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
        self._stderr_reader.join(timeout=2)
        for pipe in (self._process.stdout, self._process.stderr):
            try:
                pipe.close()
            except OSError:
                pass


def handshake(server: AppServer, deadline: float) -> None:
    hello = server.request("initialize", {"clientInfo": {
        "name": "xiaoyou-runtime", "title": "Xiaoyou", "version": __version__}})
    server.expect(hello, deadline, "initialize")
    server.send({"method": "initialized"})


def check(spec: AgentSpec, env: Optional[Dict[str, str]]) -> Optional[str]:
    """启动时的检查：只握手，不调用模型。有问题返回说明。"""
    control = Control()
    try:
        server = AppServer(spec, control, env)
    except AgentError as error:
        return str(error)
    try:
        handshake(server, time.monotonic() + HANDSHAKE_SECONDS)
    except AgentError as error:
        return "%s" % error
    finally:
        server.close()
    return None


def run(spec: AgentSpec, job: Job, control: Control, env: Optional[Dict[str, str]]) -> Outcome:
    """跑一轮：把这段话交给 Codex，直到它这一轮结束。"""
    if control.cancelled:
        raise Cancelled("Codex 被叫停了")
    server = AppServer(spec, control, env)
    # 总时长（0 是不限）和多久没动静都由它看着；到点了它结束进程，主循环读到进程没了。
    watch = Watchdog(control, "Codex", server.kill, job.timeout or spec.timeout_seconds,
                     spec.idle_seconds, spec.idle_command_seconds)
    try:
        return _turn(server, spec, job, control, watch)
    finally:
        watch.stop()
        control.can_steer(None)
        control.can_stop(None)
        server.close()


def _turn(server: AppServer, spec: AgentSpec, job: Job, control: Control,
          watch: Watchdog) -> Outcome:
    handshake(server, time.monotonic() + HANDSHAKE_SECONDS)
    settings: Dict[str, Any] = {"cwd": str(spec.workdir) if spec.workdir else os.getcwd(),
                                "approvalPolicy": spec.approval_policy}
    if spec.sandbox:
        settings["sandbox"] = spec.sandbox
    if spec.model:
        settings["model"] = spec.model
    if job.session_id:
        opened = server.request("thread/resume", dict(settings, threadId=job.session_id))
    else:
        opened = server.request("thread/start", settings)
    thread = server.expect(opened, time.monotonic() + HANDSHAKE_SECONDS,
                           "thread/resume" if job.session_id else "thread/start").get("thread")
    thread_id = thread.get("id") if isinstance(thread, dict) else None
    if not isinstance(thread_id, str):
        raise AgentError("Codex 没有给出线程编号。可以先把这个代理的 mode 改成 exec")
    control.session(thread_id)

    text = job.text if job.system is None else "%s\n\n%s" % (job.system, job.text)
    start: Dict[str, Any] = {"threadId": thread_id, "input": [{"type": "text", "text": text}]}
    if job.schema:
        start["outputSchema"] = job.schema
    begun = server.request("turn/start", start)
    turn = server.expect(begun, time.monotonic() + HANDSHAKE_SECONDS, "turn/start").get("turn")
    turn_id = turn.get("id") if isinstance(turn, dict) else None
    if not isinstance(turn_id, str):
        raise AgentError("Codex 没有给出这一轮的编号。可以先把这个代理的 mode 改成 exec")

    def steer(more: str) -> bool:
        answer = server.ask_from_elsewhere("turn/steer", {
            "threadId": thread_id, "expectedTurnId": turn_id,
            "input": [{"type": "text", "text": more}]}, STEER_SECONDS)
        return answer is not None and "result" in answer

    stop_at: List[float] = []

    def stop() -> bool:
        # 先客气地打断，让它把这一轮收好尾；主循环会在限期到了还没结束时直接停进程。
        stop_at.append(time.monotonic() + INTERRUPT_SECONDS)
        server.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
        return True

    control.can_steer(steer)
    control.can_stop(stop)

    said = ""
    problem = ""
    changes: Dict[str, Any] = {}  # 改文件的条目编号 → 改了什么（来问的时候只给编号）
    last_alive = time.monotonic()
    watch.start()
    while True:
        now = time.monotonic()
        message = server.next(min(stop_at[0] - now, 1.0) if stop_at else 1.0)
        now = time.monotonic()
        if message is False:
            if stop_at and now >= stop_at[0]:
                raise Cancelled("Codex 被叫停了")
            if problem and now - last_alive > STALL_SECONDS:
                raise AgentError("Codex 一直连不上：%s" % clip(problem, 200))
            continue
        if message is None:
            if control.cancelled:
                raise Cancelled("Codex 被叫停了")
            if watch.reason is not None:
                raise AgentError(watch.reason)
            raise AgentError("Codex 的 app-server 中途退出了：%s" % (problem or server.stderr_tail()))
        method = message.get("method")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method is None:
            continue  # 我们发的某个请求的回答，这里不用管
        if "id" in message:
            # 它来问我们了：另起线程去等主人，主循环接着读。
            threading.Thread(target=_answer, args=(server, spec, control, message, changes),
                             daemon=True).start()
            last_alive = now
            control.touch()
            continue
        if method == "error":
            error = params.get("error") if isinstance(params.get("error"), dict) else {}
            details = error.get("additionalDetails")
            problem = "%s%s" % (error.get("message") or "出错了",
                                "（%s）" % details if isinstance(details, str) and details else "")
            continue
        last_alive = now
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        item_id = item.get("id") if isinstance(item.get("id"), str) else None
        if method == "item/started":
            if item.get("type") == "fileChange" and item_id is not None:
                changes[item_id] = item.get("changes")
            line = progress_of(item)
            # 一步操作从开始到结束，中间可能很久没有消息：按“有操作在跑”的时限等。
            control.touch(began=item_id if line else None, step=line)
            if line:
                control.progress(line)
        elif method == "item/completed":
            control.touch(ended=item_id)
            if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                said = item["text"]
        else:
            control.touch()
        if method == "turn/completed":
            done = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            if done.get("id") not in (None, turn_id):
                continue
            status = done.get("status")
            if control.cancelled or status == "interrupted":
                if control.cancelled:
                    raise Cancelled("Codex 被叫停了")
                raise AgentError("Codex 这一轮被打断了")
            if status == "failed":
                error = done.get("error") if isinstance(done.get("error"), dict) else {}
                raise AgentError("Codex 没有给出结果：%s" % clip(
                    str(error.get("message") or problem or "没有说明"), 200))
            if not said.strip():
                raise AgentError("Codex 没有给出结果：%s" % clip(problem or "这一轮什么都没说", 200))
            said = said.strip()[:MAX_OUTPUT_CHARS]
            return Outcome(said, thread_id, fields_from_text(said) if job.system else None)


def _answer(server: AppServer, spec: AgentSpec, control: Control, message: Dict[str, Any],
            changes: Dict[str, Any]) -> None:
    """回答 Codex 发来的一个请求。需要确认的变成给主人的授权，其余的一律不答应。"""
    method = message.get("method")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    reason = params.get("reason") if isinstance(params.get("reason"), str) else ""

    def ask(tool: str, detail: str) -> bool:
        if reason:
            detail = "%s\n（Codex 说明：%s）" % (detail, reason)
        asker = control.ask
        try:
            return bool(asker("%s · %s" % (spec.name, tool), detail.strip())) if asker else False
        except Exception:
            return False

    result: Optional[Dict[str, Any]] = None
    if method == "item/commandExecution/requestApproval":
        command = params.get("command") if isinstance(params.get("command"), str) else ""
        where = params.get("cwd") if isinstance(params.get("cwd"), str) else ""
        allowed = ask("命令", "%s%s" % (command, "\n（在 %s）" % where if where else ""))
        result = {"decision": "accept" if allowed else "decline"}
    elif method == "item/fileChange/requestApproval":
        detail = describe_change(changes.get(params.get("itemId")))
        root = params.get("grantRoot")
        if isinstance(root, str) and root:
            detail = "%s\n（并允许之后写 %s）" % (detail, root)
        allowed = ask("改文件", detail or "（没有给出改动内容）")
        result = {"decision": "accept" if allowed else "decline"}
    elif method == "item/permissions/requestApproval":
        wanted = params.get("permissions") if isinstance(params.get("permissions"), dict) else {}
        allowed = ask("权限", json.dumps(wanted, ensure_ascii=False))
        result = {"permissions": wanted if allowed else {}, "scope": "turn"}
    elif method in ("execCommandApproval", "applyPatchApproval"):
        result = {"decision": "denied"}  # 旧版协议的问法：这里不该出现，出现了就不答应
    if result is not None:
        server.send({"id": message["id"], "result": result})
    else:
        server.send({"id": message["id"], "error": {
            "code": -32601, "message": "xiaoyou-runtime does not handle %s" % method}})
