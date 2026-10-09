"""给 Claude Code 用的权限询问工具：一个最小的 MCP 服务，把询问转给 Runtime。

Claude Code 以 `--permission-prompt-tool mcp__xiaoyou__approve` 运行时，凡是它要问
“可以吗”的操作，都改成调用这里的 approve 工具。这里把操作交给 Runtime 本机的小门
（approvals.py 的 Gate），一直等到主人在设备或手机上回答，再把答案按 Claude Code
要的格式交回去。

标准输入输出上逐行一个 JSON-RPC 消息。只实现 initialize、tools/list、tools/call；
没有 id 的通知直接忽略。只用标准库，由 Runtime 生成的 --mcp-config 文件启动：

    python3 -m xiaoyou_runtime.permission_mcp

环境变量 XIAOYOU_GATE_URL 是小门的地址，XIAOYOU_GATE_KEY 是这件事的钥匙。
"""

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

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


def handle(message: Any, url: Optional[str], key: Optional[str],
           opener=urllib.request.urlopen) -> Optional[Dict[str, Any]]:
    """处理一条 JSON-RPC 消息；通知和看不懂的东西返回 None（不回答）。"""
    if not isinstance(message, dict) or "id" not in message:
        return None
    method = message.get("method")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    def result(value: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": message["id"], "result": value}

    if method == "initialize":
        version = params.get("protocolVersion")
        return result({
            "protocolVersion": version if isinstance(version, str) else PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "xiaoyou", "version": "1"},
        })
    if method == "ping":
        return result({})
    if method == "tools/list":
        return result({"tools": [TOOL]})
    if method == "tools/call":
        if params.get("name") != "approve":
            return {"jsonrpc": "2.0", "id": message["id"],
                    "error": {"code": -32602, "message": "unknown tool"}}
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        answer = ask(arguments, url, key, opener)
        # Claude Code 要的是一段文字，里面是序列化好的 JSON。
        return result({"content": [{"type": "text", "text": json.dumps(answer, ensure_ascii=False)}]})
    return {"jsonrpc": "2.0", "id": message["id"],
            "error": {"code": -32601, "message": "method not found"}}


def main() -> int:
    url, key = os.environ.get("XIAOYOU_GATE_URL"), os.environ.get("XIAOYOU_GATE_KEY")
    for raw in sys.stdin.buffer:
        try:
            message = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        reply = handle(message, url, key)
        if reply is not None:
            sys.stdout.buffer.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
            sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
