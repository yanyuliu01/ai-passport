"""授权：帮手要做一件需要确认的操作时，停下来等主人点头。

帮手那边（Claude Code 的权限询问工具 permission_mcp.py，或者 Codex 的 app-server）
把要做的操作交到这里，这里记成一个“待确认的授权”，挂到那件事的卡上（卡变成等你
点头），然后一直等到主人在设备或手机上回答“可以 / 不行”，或者那件事结束。

Claude Code 的权限询问工具是另一个进程，要通过 HTTP 把询问送进来。它用的不是手机
连的那个接口，而是这里单独起的一个只监听本机回环地址的小门（Gate），端口随机，每件
事用一把只对它有效的钥匙：Runtime 的令牌不用写进任何文件，局域网里的机器也碰不到
这个入口。
"""

import hmac
import json
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

MAX_TOOL_CHARS = 80
MAX_DETAIL_CHARS = 20000
MAX_GATE_BODY_BYTES = 1024 * 1024
ALLOW, DENY = "allow", "deny"
TOOL, STALL = "tool", "stall"
DENIED = "主人说不行"
GONE = "这件事已经停了"

# 授权出现或有了答案时调用：(卡的编号, 还在等的授权编号或 None)。
Changed = Callable[[str, Optional[str]], None]
# 授权的每次变化都从和消息、卡同一个计数器取一个序号（见统一消息结构），/v1/sync 按它增量给。
Sequence = Callable[[], int]
# 授权的结局：等着、允许、拒绝、收回（那件事停了、电脑上处理了或过期了、没人答）。
STATUSES = ("pending", "allowed", "denied", "withdrawn")
FIELDS = ("id", "card", "conversation", "agent", "tool", "detail", "created_at", "kind",
          "source", "session_id", "project", "remote_actionable", "expires_at",
          "request_id", "subagent_id", "turn_id", "operation")


def describe(agent: str, tool: str, tool_input: Any) -> Tuple[str, str]:
    """把一次工具调用变成给主人看的两段：谁要用什么，和内容原文。不转述，不总结。"""
    data = tool_input if isinstance(tool_input, dict) else {}

    def text(key: str) -> str:
        value = data.get(key)
        return value if isinstance(value, str) else ""

    if tool == "Bash":
        detail = text("command")
    elif tool == "Write":
        detail = "%s\n%s" % (text("file_path"), text("content"))
    elif tool == "Edit":
        detail = "%s\n- %s\n+ %s" % (text("file_path"), text("old_string"), text("new_string"))
    elif tool in ("Read", "NotebookEdit"):
        detail = text("file_path") or text("notebook_path")
    elif tool == "WebFetch":
        detail = text("url")
    elif tool == "WebSearch":
        detail = text("query")
    else:
        detail = json.dumps(data, ensure_ascii=False) if data else ""
    return ("%s · %s" % (agent, tool))[:MAX_TOOL_CHARS], detail.strip()[:MAX_DETAIL_CHARS]


def progress_line(tool: str, tool_input: Any) -> str:
    """一步操作的进展行：工具名加上最关键的一个参数，原样。"""
    data = tool_input if isinstance(tool_input, dict) else {}
    key = {"Bash": "command", "Read": "file_path", "Edit": "file_path", "Write": "file_path",
           "WebSearch": "query", "WebFetch": "url"}.get(tool)
    value = data.get(key) if key else None
    return "%s %s" % (tool, value) if isinstance(value, str) and value else tool


class Approvals:
    def __init__(self, changed: Optional[Changed] = None, sequence: Optional[Sequence] = None):
        self._sequence = sequence
        self._changed = threading.Condition()
        self._items: Dict[str, Dict[str, Any]] = {}
        self._next = 1
        self._notify: Changed = changed or (lambda card, approval: None)
        self._desktop_prefix = "d" + secrets.token_hex(6)
        self._responders: Dict[str, Callable[[str], bool]] = {}
        # 别的 Runtime 的授权在这里有副本（小幽网络，mesh.py）。在这里回答时交给它：
        # (副本, allow/deny) → 送出去了没有。结局等那台报回来。
        self.forward: Optional[Callable[[Dict[str, Any], str], bool]] = None
        self.on_request: Optional[Callable[[str], None]] = None

    def mirror(self, approval_id: str, fields: Dict[str, Any]) -> None:
        """别的 Runtime 的一个授权在这里的副本：整段换成那台报来的样子，取一个这里的序号。"""
        status = fields.get("status", "pending")
        with self._changed:
            item = dict(self._items.get(approval_id) or {})
            item.update({key: fields[key] for key in FIELDS + ("status", "by", "reason", "mesh_origin",
                                                              "mesh_id", "origin_name")
                         if key in fields})
            item["id"] = approval_id
            item.setdefault("created_at", time.time())
            item["decision"] = None if status == "pending" else (ALLOW if status == "allowed" else DENY)
            item["gone"] = status == "withdrawn"
            item["updated_at"] = time.time()
            item["seq"] = self._sequence() if self._sequence is not None else 0
            self._items[approval_id] = item
            card = item["card"]
            waiting = self._waiting(card)
            self._changed.notify_all()
        self._notify(card, waiting)

    def own_since(self, after: int) -> List[Dict[str, Any]]:
        """这台自己的授权里序号比 after 大的变化（所有对话），交给网络里的别的 Runtime。"""
        with self._changed:
            return sorted((dict({key: item[key] for key in FIELDS if key in item},
                                status=item.get("status", "pending"), seq=item.get("seq", 0),
                                updated_at=item.get("updated_at", item["created_at"]),
                                **{key: item[key] for key in ("by", "reason") if key in item})
                           for item in self._items.values()
                           if item.get("seq", 0) > after and not item.get("mesh_origin")),
                          key=lambda item: item["seq"])

    def ask(self, card: str, conversation: str, agent: str, tool: str, detail: str,
            kind: str = TOOL, *, context: Optional[Dict[str, Any]] = None,
            responder: Optional[Callable[[str], bool]] = None) -> str:
        """登记一个授权并返回它的编号。不等。

        kind 是问的什么：tool 是“这一步操作可以吗”，stall 是“它很久没动静了，还等吗”
        （可以 = 接着等，不行 = 停掉）。两种走同一个弹窗。
        """
        with self._changed:
            approval_id = (self._desktop_prefix if context and context.get("source") == "desktop"
                           else "a") + str(self._next)
            self._next += 1
            self._items[approval_id] = {
                "id": approval_id, "card": card, "conversation": conversation, "agent": agent,
                "tool": tool[:MAX_TOOL_CHARS], "detail": detail[:MAX_DETAIL_CHARS],
                "created_at": time.time(), "decision": None, "kind": kind,
            }
            if context:
                for key in ("source", "session_id", "project", "remote_actionable", "expires_at",
                            "request_id", "subagent_id", "turn_id", "operation"):
                    if key in context:
                        self._items[approval_id][key] = context[key]
            if responder is not None:
                self._responders[approval_id] = responder
            self._mark(self._items[approval_id], "pending")
            # 答过的只留最近一些，够回答“这个已经答过了”就行。
            answered = [key for key, item in self._items.items() if item["decision"] is not None]
            for key in answered[:-50]:
                del self._items[key]
                self._responders.pop(key, None)
        self._notify(card, approval_id)
        if self.on_request is not None:
            self.on_request(approval_id)
        return approval_id

    def wait(self, approval_id: str, seconds: Optional[float] = None,
             moot: Optional[Callable[[], bool]] = None) -> Optional[str]:
        """一直等到这个授权有答案（或者那件事停了）；返回 allow 或 deny。

        给了 seconds 就最多等这么久；给了 moot 就在它说“不用再问了”时不等了。这两种
        情况返回 None，授权还挂着，由调用方用 withdraw 收回。
        """
        deadline = None if seconds is None else time.monotonic() + seconds
        with self._changed:
            while True:
                item = self._items.get(approval_id)
                if item is None:
                    return DENY
                if item["decision"] is not None:
                    return item["decision"]
                if moot is not None and moot():
                    return None
                if deadline is not None and time.monotonic() >= deadline:
                    return None
                self._changed.wait(1.0 if deadline is None else max(
                    0.01, min(1.0, deadline - time.monotonic())))

    def withdraw(self, approval_id: str, reason: str = "unanswered") -> None:
        """这个问题不用答了（没人答，或者已经不成问题）：收回弹窗。

        reason 记下为什么收回：unanswered 没人答，desktop 电脑上处理了或过期了，gone 那件事停了。"""
        with self._changed:
            item = self._items.get(approval_id)
            if item is None or item["decision"] is not None:
                return
            item["decision"] = DENY
            item["gone"] = True
            self._mark(item, "withdrawn", reason=reason)
            card = item["card"]
            waiting = self._waiting(card)
            self._changed.notify_all()
        self._notify(card, waiting)

    def waiting(self, card: str) -> bool:
        """这件事是不是正停着等主人回答。"""
        with self._changed:
            return self._waiting(card) is not None

    def answer(self, approval_id: str, decision: str, by: str = "phone") -> Optional[bool]:
        """回答一个授权。没有这个编号返回 None；已经答过了返回 False。

        by 记下在哪答的：phone（手机，设备上的回答也经手机送来）或 computer（电脑上的命令行）。"""
        if decision not in (ALLOW, DENY):
            raise ValueError("invalid approval decision")
        accepted = True
        with self._changed:
            item = self._items.get(approval_id)
            if item is None:
                return None
            if item["decision"] is not None:
                return False
            mirrored = dict(item) if item.get("mesh_origin") else None
        if mirrored is not None:
            # 别的 Runtime 的授权：交给那台去答，结局由它报回来。
            return bool(self.forward is not None and self.forward(mirrored, decision))
        with self._changed:
            item = self._items.get(approval_id)
            if item is None:
                return None
            if item["decision"] is not None:
                return False
            if item.get("source") == "desktop":
                if item.get("remote_actionable") is not True:
                    return False
                responder = self._responders.get(approval_id)
                try:
                    accepted = responder is not None and responder(decision)
                except (OSError, ValueError):
                    accepted = False
                if not accepted:
                    item["decision"], item["gone"] = DENY, True
                    self._mark(item, "withdrawn", reason="desktop")
            if accepted:
                item["decision"] = decision
                self._mark(item, "allowed" if decision == ALLOW else "denied", by=by)
            card = item["card"]
            waiting = self._waiting(card)
            self._changed.notify_all()
        self._notify(card, waiting)
        return accepted

    def drop(self, card: str) -> None:
        """那件事停了（做完、取消、重做）：还在等的授权都不用等了。"""
        with self._changed:
            dropped = False
            for item in self._items.values():
                if item["card"] == card and item["decision"] is None and not item.get("mesh_origin"):
                    item["decision"] = DENY
                    item["gone"] = True
                    self._mark(item, "withdrawn", reason="gone")
                    dropped = True
            self._changed.notify_all()
        if dropped:
            self._notify(card, None)

    def _waiting(self, card: str) -> Optional[str]:
        for item in self._items.values():
            if item["card"] == card and item["decision"] is None:
                return item["id"]
        return None

    def get(self, approval_id: str) -> Optional[Dict[str, Any]]:
        with self._changed:
            item = self._items.get(approval_id)
            return dict(item) if item is not None else None

    def _mark(self, item: Dict[str, Any], status: str, **extra: Any) -> None:
        """记下这次变化：状态、序号、时间，以及谁答的或者为什么收回。调用方持有锁。"""
        item.update(status=status, updated_at=time.time(), **extra)
        item["seq"] = self._sequence() if self._sequence is not None else 0

    def pending(self, conversation: Optional[str] = None, *,
                include_desktop: bool = False) -> List[Dict[str, Any]]:
        """还在等回答的授权，按出现的先后。"""
        with self._changed:
            return [
                {key: item[key] for key in FIELDS if key in item}
                for item in self._items.values()
                if item["decision"] is None
                and (conversation is None or item["conversation"] == conversation
                     or (include_desktop and item.get("source") == "desktop"))
            ]

    def since(self, conversation: Optional[str], after: int, *, include_desktop: bool = True) -> List[Dict[str, Any]]:
        """序号比 after 大的授权变化，含已经有结局的（/v1/sync 增量给）：出现又消失的授权，
        客户端也能看到它的结局。按序号排。"""
        with self._changed:
            changed = [
                dict({key: item[key] for key in FIELDS if key in item},
                     status=item.get("status", "pending"), seq=item.get("seq", 0),
                     updated_at=item.get("updated_at", item["created_at"]),
                     **{key: item[key] for key in ("by", "reason") if key in item})
                for item in self._items.values()
                if item.get("seq", 0) > after
                and (conversation is None or item["conversation"] == conversation
                     or (include_desktop and item.get("source") == "desktop"))
            ]
        return sorted(changed, key=lambda item: item["seq"])


class Gate:
    """只在本机回环地址上听的小门：权限询问工具从这里把询问送进来，并等到答案。"""

    def __init__(self, approvals: Approvals):
        self._approvals = approvals
        self._lock = threading.Lock()
        self._keys: Dict[str, Dict[str, str]] = {}  # 钥匙 → 它对哪件事、哪个帮手有效
        self._server: Optional[ThreadingHTTPServer] = None
        # 能力库（capabilities.Library）：有了它，能力网关入口 能经这里列出、调用帮手被授权的工具。
        self.capabilities: Any = None

    def offers(self, agent: str) -> bool:
        """能力库里有没有这个帮手可能用到的服务。"""
        return self.capabilities is not None and self.capabilities.offers(agent)

    def open(self, card: str, conversation: str, agent: str) -> Dict[str, str]:
        """为一件事开一把钥匙；返回能力网关入口（权限询问、能力库工具）需要的环境变量。"""
        with self._lock:
            if self._server is None:
                self._server = self._start()
            key = secrets.token_urlsafe(32)
            self._keys[key] = {"card": card, "conversation": conversation, "agent": agent}
            port = self._server.server_address[1]
        return {"XIAOYOU_GATE_URL": "http://127.0.0.1:%d/approve" % port, "XIAOYOU_GATE_KEY": key}

    def shut(self, env: Optional[Dict[str, str]]) -> None:
        """这一轮结束：钥匙作废。"""
        if env:
            with self._lock:
                self._keys.pop(env.get("XIAOYOU_GATE_KEY", ""), None)

    def close(self) -> None:
        with self._lock:
            server, self._server = self._server, None
            self._keys.clear()
        if server is not None:
            server.shutdown()
            server.server_close()

    def _holder(self, key: str) -> Optional[Dict[str, str]]:
        with self._lock:
            for known, holder in self._keys.items():
                if hmac.compare_digest(known.encode(), key.encode()):
                    return dict(holder)
        return None

    def _start(self) -> ThreadingHTTPServer:
        gate = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:
                pass

            def _send(self, status: int, body: Dict[str, Any]) -> None:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                self.close_connection = True
                holder = gate._holder(self.headers.get("X-Xiaoyou-Key", ""))
                if self.path != "/mcp/tools" or holder is None:
                    self._send(403, {"error": "钥匙不对"})
                    return
                if gate.capabilities is None:
                    self._send(200, {"tools": [], "unavailable": []})
                    return
                self._send(200, gate.capabilities.tools(holder["agent"]))

            def _capability_call(self, holder: Dict[str, str]) -> None:
                from .capabilities import CapabilityError
                try:
                    length = int(self.headers.get("Content-Length", ""))
                    if not 0 < length <= MAX_GATE_BODY_BYTES:
                        raise ValueError
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    fields = ("server", "tool", "config_version", "grant_version")
                    if not isinstance(body, dict) or not all(isinstance(body.get(key), str) for key in fields)                             or not isinstance(body.get("arguments", {}), dict):
                        raise ValueError
                except (ValueError, UnicodeDecodeError):
                    self._send(400, {"error": "请求看不懂"})
                    return
                if gate.capabilities is None:
                    self._send(200, {"content": [{"type": "text", "text": "这台电脑没有能力库"}], "isError": True})
                    return
                try:
                    result = gate.capabilities.call(holder["agent"], body["server"], body["tool"],
                                                    body.get("arguments", {}), body["config_version"],
                                                    body["grant_version"])
                except Exception as error:  # 授权变了、服务出错：如实交给帮手
                    reason = str(error) if isinstance(error, CapabilityError) else "调用失败：%s" % error
                    result = {"content": [{"type": "text", "text": reason[:2000]}], "isError": True}
                try:
                    self._send(200, result)
                except OSError:
                    pass

            def do_POST(self) -> None:
                self.close_connection = True
                holder = gate._holder(self.headers.get("X-Xiaoyou-Key", ""))
                if holder is not None and self.path == "/mcp/call":
                    self._capability_call(holder)
                    return
                if self.path != "/approve" or holder is None:
                    self._send(403, {"error": "钥匙不对"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", ""))
                    if not 0 < length <= MAX_GATE_BODY_BYTES:
                        raise ValueError
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(body, dict) or not isinstance(body.get("tool_name"), str):
                        raise ValueError
                except (ValueError, UnicodeDecodeError):
                    self._send(400, {"error": "请求看不懂"})
                    return
                tool, detail = describe(holder["agent"], body["tool_name"], body.get("input"))
                approval_id = gate._approvals.ask(
                    holder["card"], holder["conversation"], holder["agent"], tool, detail,
                    context={"operation": body["tool_name"]})
                decision = gate._approvals.wait(approval_id)
                item = gate._approvals.get(approval_id) or {}
                try:
                    self._send(200, {
                        "decision": decision,
                        "message": GONE if item.get("gone") else DENIED,
                    })
                except OSError:
                    pass  # 那个进程已经被停掉了

        class Server(ThreadingHTTPServer):
            def handle_error(self, request: Any, client_address: Any) -> None:
                error = sys.exc_info()[1]
                if not isinstance(error, (ConnectionError, TimeoutError)):
                    super().handle_error(request, client_address)

        server = Server(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True, name="xiaoyou-gate").start()
        return server
