"""操作（operation）：一个要执行的决定，执行之前先有稳定的身份和状态（重构设计 1.5 节）。

现在只有一种操作：处理收下的一句话（Service 的消息）。每句话收下时就有 operation_id，
状态和消息的 status 一起变：

    pending ──→ running ──→ succeeded / failed / cancelled
       │           └──────→ unknown（重启时正在做：外部动作可能已经开始了）
       └──────────────────→ succeeded / failed / cancelled
    unknown ──→ succeeded / failed / cancelled（核对过之后）

结束了（succeeded / failed / cancelled）就不能再变：迟到的进展、重复投递都不能让它重新
运行。同一个操作只能被领取一次：只有 pending 能变成 running（claim）。
"""

from typing import Any, Dict, Optional

STATES = ("pending", "running", "succeeded", "failed", "cancelled", "unknown")
TERMINAL = ("succeeded", "failed", "cancelled")
NEXT = {
    "pending": {"running", "succeeded", "failed", "cancelled"},
    "running": {"succeeded", "failed", "cancelled", "unknown"},
    "unknown": {"succeeded", "failed", "cancelled"},
    "succeeded": set(), "failed": set(), "cancelled": set(),
}
# 消息的 status → 操作状态
FROM_STATUS = {"queued": "pending", "transcribing": "running", "running": "running",
               "done": "succeeded", "failed": "failed", "cancelled": "cancelled"}


def operation_id(message_id: str) -> str:
    """由消息编号定下来：同一句话重试、重放、换台 Runtime 读到的都是同一个操作。"""
    return message_id + ":handle"


def allowed(current: Optional[str], target: str) -> bool:
    return current is None or current == target or target in NEXT.get(current, set())


def migrate(message: Dict[str, Any]) -> None:
    """旧记录没有操作字段：按 status 补上（就地改）。"""
    message.setdefault("operation_id", operation_id(message["id"]))
    if message.get("operation_state") not in STATES:
        message["operation_state"] = FROM_STATUS.get(message.get("status"), "unknown")


def transition(message: Dict[str, Any], fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """这次改动要把操作带到哪个状态；不允许的（比如结束之后又说在做）返回 None，整次改动丢掉。"""
    target = fields.get("operation_state") or FROM_STATUS.get(fields.get("status", ""))
    if target is None:
        return fields
    if not allowed(message.get("operation_state"), target):
        return None
    return dict(fields, operation_state=target)
