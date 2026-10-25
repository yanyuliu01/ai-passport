"""最小的 MCP 客户端：能力库用它替帮手去调真正的服务（见 capabilities.py）。

两种服务：
- 本机命令（stdio）：每行一个 JSON-RPC 消息。每个服务一个常驻进程，空闲 IDLE_SECONDS 退出，
  下次调用再启动。
- 网络地址（Streamable HTTP）：每个请求一个 POST，回答可以是 JSON，也可以是事件流
  （text/event-stream，取里面对上编号的那一条）。会话编号放在 Mcp-Session-Id 头里。

只做能力库要的三件事：initialize、tools/list（翻页）、tools/call。结果超过 MAX_RESULT_BYTES、
一次超过 CALL_SECONDS 都报错，不让一个服务拖住帮手。只用标准库。
"""

import json
import os
import queue
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "xiaoyou-runtime", "version": "1"}
CALL_SECONDS = 30
IDLE_SECONDS = 600
MAX_RESULT_BYTES = 512 * 1024
MAX_PAGES = 20


class McpError(Exception):
    """服务出错、超时、或者回答看不懂。"""


def connect(server: Any, connection: Dict[str, Dict[str, str]]) -> "Session":
    session = HttpSession(server, connection) if server.url else StdioSession(server, connection)
    try:
        session.initialize()
    except Exception:
        session.close()
        raise
    return session


def _result(message: Dict[str, Any], what: str) -> Dict[str, Any]:
    if "error" in message:
        error = message["error"]
        detail = error.get("message") if isinstance(error, dict) else str(error)
        raise McpError("%s 出错：%s" % (what, str(detail)[:300]))
    result = message.get("result")
    if not isinstance(result, dict):
        raise McpError("%s 的回答看不懂" % what)
    return result


class Session:
    def __init__(self, server: Any, connection: Dict[str, Dict[str, str]]):
        self.server = server
        self.version = server.version
        self._connection = connection
        self._next_id = 0
        self._id_lock = threading.Lock()

    def _id(self) -> int:
        with self._id_lock:
            self._next_id += 1
            return self._next_id

    def request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        raise NotImplementedError

    def initialize(self) -> None:
        self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                    "clientInfo": CLIENT_INFO})
        self.notify("notifications/initialized")

    def list_tools(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        cursor = None
        for _ in range(MAX_PAGES):
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            for tool in result.get("tools") or []:
                if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                    tools.append({"name": tool["name"], "description": tool.get("description") or "",
                                  "inputSchema": tool.get("inputSchema") or {"type": "object"}})
            cursor = result.get("nextCursor")
            if not cursor:
                break
        else:
            raise McpError("%s 的工具列表超过翻页上限" % self.server.name)
        return tools

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_RESULT_BYTES:
            raise McpError("%s 的结果超过 %d KB，没有交给帮手" % (self.server.name, MAX_RESULT_BYTES // 1024))
        return {"content": result.get("content") or [], "isError": bool(result.get("isError")),
                **({"structuredContent": result["structuredContent"]}
                   if isinstance(result.get("structuredContent"), dict) else {})}

    def alive(self) -> bool:
        return True

    def close(self) -> None:
        pass


class StdioSession(Session):
    def __init__(self, server: Any, connection: Dict[str, Dict[str, str]]):
        super().__init__(server, connection)
        command = list(server.command)
        command[0] = shutil.which(command[0]) or command[0]
        env = dict(os.environ)
        env.update(connection.get("env", {}))
        try:
            self._process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", env=env)
        except OSError as error:
            raise McpError("启动 %s 失败：%s" % (server.name, error))
        self._pending: Dict[int, "queue.Queue[Dict[str, Any]]"] = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._used = time.monotonic()
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._reap, daemon=True).start()

    def _read(self) -> None:
        for line in self._process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict) or not isinstance(message.get("id"), int):
                continue  # 通知和服务发来的请求不管
            with self._pending_lock:
                waiter = self._pending.get(message["id"])
            if waiter is not None:
                waiter.put(message)

    def _reap(self) -> None:
        while self.alive():
            time.sleep(30)
            if time.monotonic() - self._used > IDLE_SECONDS:
                self.close()

    def _send(self, message: Dict[str, Any]) -> None:
        with self._write_lock:
            try:
                self._process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                self._process.stdin.flush()
            except (OSError, ValueError) as error:
                raise McpError("%s 已经退出了：%s" % (self.server.name, error))

    def request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._used = time.monotonic()
        request_id = self._id()
        waiter: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        with self._pending_lock:
            self._pending[request_id] = waiter
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            try:
                message = waiter.get(timeout=CALL_SECONDS)
            except queue.Empty:
                raise McpError("%s 超过 %d 秒没有回答（%s）" % (self.server.name, CALL_SECONDS, method))
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        return _result(message, "%s 的 %s" % (self.server.name, method))

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def alive(self) -> bool:
        return self._process.poll() is None

    def close(self) -> None:
        if self._process.poll() is None:
            try:
                self._process.stdin.close()
            except OSError:
                pass
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()


class HttpSession(Session):
    def __init__(self, server: Any, connection: Dict[str, Dict[str, str]], opener=urllib.request.urlopen):
        super().__init__(server, connection)
        self._open = opener
        self._session_id: Optional[str] = None
        self._negotiated: Optional[str] = None

    def _post(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        deadline = time.monotonic() + CALL_SECONDS
        request = urllib.request.Request(self.server.url, method="POST",
                                         data=json.dumps(message, ensure_ascii=False).encode("utf-8"))
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json, text/event-stream")
        for key, value in self._connection.get("headers", {}).items():
            request.add_header(key, value)
        if self._session_id:
            request.add_header("Mcp-Session-Id", self._session_id)
        if self._negotiated:
            request.add_header("MCP-Protocol-Version", self._negotiated)
        try:
            with self._open(request, timeout=CALL_SECONDS) as response:
                self._session_id = response.headers.get("Mcp-Session-Id") or self._session_id
                if "id" not in message:
                    return None  # 通知：服务回 202，没有内容
                kind = response.headers.get("Content-Type", "")
                sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                expired = threading.Event()
                def cancel_read() -> None:
                    expired.set()
                    if sock is not None:
                        try:
                            sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                timer = threading.Timer(max(0.001, deadline - time.monotonic()), cancel_read)
                timer.daemon = True
                timer.start()
                try:
                    if "text/event-stream" in kind:
                        result = self._event_result(response, message["id"], deadline)
                        if expired.is_set():
                            raise McpError("%s 的事件流已超时" % self.server.name)
                        return result
                    body = response.read(MAX_RESULT_BYTES + 1)
                    if expired.is_set():
                        raise McpError("%s 的回答已超时" % self.server.name)
                finally:
                    timer.cancel()
        except urllib.error.HTTPError as error:
            raise McpError("%s 回答 HTTP %d" % (self.server.name, error.code))
        except (urllib.error.URLError, OSError) as error:
            raise McpError("连不上 %s：%s" % (self.server.name, error))
        if len(body) > MAX_RESULT_BYTES:
            raise McpError("%s 的结果超过 %d KB，没有交给帮手" % (self.server.name, MAX_RESULT_BYTES // 1024))
        text = body.decode("utf-8", errors="replace")
        candidates = [text]
        if "text/event-stream" in kind:
            # 事件流：每个事件的 data 行拼起来是一条 JSON-RPC 消息。
            candidates, data = [], []
            for line in text.splitlines() + [""]:
                if line.startswith("data:"):
                    data.append(line[5:].lstrip())
                elif not line.strip() and data:
                    candidates.append("\n".join(data))
                    data = []
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except ValueError:
                continue
            if isinstance(parsed, dict) and parsed.get("id") == message["id"]:
                return parsed
        raise McpError("%s 的回答看不懂" % self.server.name)

    def _event_result(self, response: Any, request_id: int, deadline: float) -> Dict[str, Any]:
        count, data = 0, []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpError("%s 的事件流已超时" % self.server.name)
            # Bound both idle reads and the whole stream, including heartbeat-only streams.
            sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
            if sock is not None:
                sock.settimeout(remaining)
            line = response.readline(MAX_RESULT_BYTES + 1)
            count += len(line)
            if count > MAX_RESULT_BYTES:
                raise McpError("%s 的结果超过 %d KB" % (self.server.name, MAX_RESULT_BYTES // 1024))
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if text.startswith("data:"):
                data.append(text[5:].lstrip())
            elif not text and data:
                try:
                    parsed = json.loads("\n".join(data))
                except ValueError:
                    parsed = None
                data = []
                if isinstance(parsed, dict) and parsed.get("id") == request_id:
                    return parsed
            if not line:
                raise McpError("%s 的事件流结束了，没有返回本次结果" % self.server.name)

    def request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        message = self._post({"jsonrpc": "2.0", "id": self._id(), "method": method, "params": params})
        result = _result(message or {}, "%s 的 %s" % (self.server.name, method))
        if method == "initialize" and isinstance(result.get("protocolVersion"), str):
            self._negotiated = result["protocolVersion"]
        return result

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        self._post({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})
