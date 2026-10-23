"""给 Claude Code 用的权限询问工具：一个最小的 MCP 服务，把询问转给 Runtime。

Claude Code 以 `--permission-prompt-tool mcp__xiaoyou__approve` 运行时，凡是它要问
“可以吗”的操作，都改成调用这里的 approve 工具。这里把操作交给 Runtime 本机的小门
（approvals.py 的 Gate），一直等到主人在设备或手机上回答，再把答案按 Claude Code
要的格式交回去。

标准输入输出上逐行一个 JSON-RPC 消息。只实现 initialize、tools/list、tools/call；
没有 id 的通知直接忽略。只用标准库，由 Runtime 生成的 --mcp-config 文件启动：

    python3 -m xiaoyou_runtime.permission_mcp

环境变量 XIAOYOU_GATE_URL 是小门的地址，XIAOYOU_GATE_KEY 是这件事的钥匙。

它也是每一轮注入帮手的能力网关入口（见 docs/development/helper-capabilities）：除了 approve
（XIAOYOU_GATE_APPROVE=1 时才列，只有 Claude Code 用），还列出这个帮手被授权的能力库工具，
调用时交给小门的 /mcp/call，由 Runtime 核对授权、替它去调真正的服务。帮手只拿到钥匙。
"""

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = "2024-11-05"
TOOL = {
    "name": "approve",
    "description": "Ask the owner whether a tool call may run.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "input": {"type": "object"},
            "tool_use_id": {"type": "string"},
        },
        "required": ["tool_name", "input"],
    },
}
# 等主人回答可以很久；这个上限只是为了不让一个没人管的进程永远挂着。
WAIT_SECONDS = 7200
# 一次能力库调用：服务自己最多 30 秒，再留一点余量。
CALL_SECONDS = 45


def ask(arguments: Dict[str, Any], url: Optional[str], key: Optional[str],
        opener=urllib.request.urlopen) -> Dict[str, Any]:
    """把一次询问送到小门并等答案；返回 Claude Code 要的那个对象。"""
    tool_input = arguments.get("input") if isinstance(arguments.get("input"), dict) else {}
    if not url or not key:
        return {"behavior": "deny", "message": "没法问主人：这个工具不是由小幽 Runtime 启动的"}
    request = urllib.request.Request(url, method="POST", data=json.dumps({
        "tool_name": arguments.get("tool_name"), "input": tool_input,
        "tool_use_id": arguments.get("tool_use_id"),
    }, ensure_ascii=False).encode("utf-8"))
    request.add_header("Content-Type", "application/json; charset=utf-8")
    request.add_header("X-Xiaoyou-Key", key)
    try:
        with opener(request, timeout=WAIT_SECONDS) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as error:
        return {"behavior": "deny", "message": "没法问主人：连不上小幽 Runtime（%s）" % error}
    if isinstance(answer, dict) and answer.get("decision") == "allow":
        return {"behavior": "allow", "updatedInput": tool_input}
    message = answer.get("message") if isinstance(answer, dict) else None
    return {"behavior": "deny", "message": message if isinstance(message, str) else "主人说不行"}


def _gate(url: Optional[str], key: Optional[str], path: str, body: Optional[Dict[str, Any]],
          opener=urllib.request.urlopen) -> Dict[str, Any]:
    """问小门的能力库接口：GET /mcp/tools 或 POST /mcp/call。"""
    if not url or not key:
        raise OSError("这个工具不是由小幽 Runtime 启动的")
    base = url.rsplit("/", 1)[0]
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(base + path, method="GET" if body is None else "POST", data=data)
    request.add_header("X-Xiaoyou-Key", key)
    if data is not None:
        request.add_header("Content-Type", "application/json; charset=utf-8")
    with opener(request, timeout=CALL_SECONDS) as response:
        answer = json.loads(response.read().decode("utf-8"))
    if not isinstance(answer, dict):
        raise ValueError("小门的回答看不懂")
    return answer


def capability_tools(url: Optional[str], key: Optional[str], state: Dict[str, Any],
                     opener=urllib.request.urlopen) -> List[Dict[str, Any]]:
    """这个帮手被授权的能力库工具；记下每个工具对应哪个服务、哪一版授权。"""
    try:
        answer = _gate(url, key, "/mcp/tools", None, opener)
    except (urllib.error.URLError, OSError, ValueError):
        return []
    tools, state["tools"] = [], {}
    for item in answer.get("tools") or []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        state["tools"][item["name"]] = item
        tools.append({"name": item["name"], "description": item.get("description") or "",
                      "inputSchema": item.get("inputSchema") or {"type": "object"}})
    state["unavailable"] = [row for row in answer.get("unavailable") or [] if isinstance(row, dict)]
    return tools


def handle(message: Any, url: Optional[str], key: Optional[str],
           opener=urllib.request.urlopen, approve: bool = True,
           state: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """处理一条 JSON-RPC 消息；通知和看不懂的东西返回 None（不回答）。"""
    state = state if state is not None else {}
    if not isinstance(message, dict) or "id" not in message:
        return None
    method = message.get("method")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    def result(value: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": message["id"], "result": value}

    if method == "initialize":
        version = params.get("protocolVersion")
        reply = {
            "protocolVersion": version if isinstance(version, str) else PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "xiaoyou", "version": "1"},
        }
        capability_tools(url, key, state, opener)
        if state.get("unavailable"):
            # 用不了的服务也说出来，帮手能如实说“X 需要重新连接”，而不是当它不存在。
            reply["instructions"] = "These capabilities are configured but unavailable now: " + "; ".join(
                "%s (%s)" % (row.get("name"), row.get("reason")) for row in state["unavailable"])[:2000]
        return result(reply)
    if method == "ping":
        return result({})
    if method == "tools/list":
        return result({"tools": ([TOOL] if approve else []) + capability_tools(url, key, state, opener)})
    if method == "tools/call":
        name = params.get("name")
        if name != "approve" or not approve:
            known = state.get("tools", {}).get(name) if isinstance(name, str) else None
            if known is None:
                return {"jsonrpc": "2.0", "id": message["id"],
                        "error": {"code": -32602, "message": "unknown tool"}}
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            try:
                answer = _gate(url, key, "/mcp/call", {
                    "server": known.get("server"), "tool": known.get("tool"), "arguments": arguments,
                    "config_version": known.get("config_version"), "grant_version": known.get("grant_version"),
                }, opener)
            except (urllib.error.URLError, OSError, ValueError) as error:
                answer = {"content": [{"type": "text", "text": "连不上小幽 Runtime：%s" % error}], "isError": True}
            return result(answer)
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        answer = ask(arguments, url, key, opener)
        # Claude Code 要的是一段文字，里面是序列化好的 JSON。
        return result({"content": [{"type": "text", "text": json.dumps(answer, ensure_ascii=False)}]})
    return {"jsonrpc": "2.0", "id": message["id"],
            "error": {"code": -32601, "message": "method not found"}}


def main() -> int:
    url, key = os.environ.get("XIAOYOU_GATE_URL"), os.environ.get("XIAOYOU_GATE_KEY")
    approve = os.environ.get("XIAOYOU_GATE_APPROVE") == "1"
    state: Dict[str, Any] = {}
    for raw in sys.stdin.buffer:
        try:
            message = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        reply = handle(message, url, key, approve=approve, state=state)
        if reply is not None:
            sys.stdout.buffer.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
            sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
