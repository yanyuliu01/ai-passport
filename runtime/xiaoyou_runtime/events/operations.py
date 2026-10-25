"""操作（operation）：一个要执行的决定，执行之前先有稳定的身份和状态（重构设计 1.5 节）。

Service 消息收下时就有 operation_id，状态和消息的 status 一起变；帮手意图则保存在
任务日志的私有 dispatch 字段里，在调用帮手前和执行绑定一起领取：

    pending ──→ running ──→ succeeded / failed / cancelled
       │           └──────→ unknown（重启时正在做：外部动作可能已经开始了）
       └──────────────────→ succeeded / failed / cancelled
    unknown ──→ succeeded / failed / cancelled（核对过之后）

结束了（succeeded / failed / cancelled）就不能再变：迟到的进展、重复投递都不能让它重新
运行。同一个操作只能被领取一次：只有 pending 能变成 running（claim）。
"""

from typing import Any, Dict, Optional
import math

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


def valid_dispatch(value: Any) -> bool:
    """A private, versioned helper intent in the existing task journal."""
    if not isinstance(value, dict) or value.get("version") != 1:
        return False
    if (value.get("state") not in STATES or not isinstance(value.get("run_id"), str)
            or not value["run_id"] or not isinstance(value.get("agent"), str)
            or not isinstance(value.get("fingerprint"), str)):
        return False
    data = value.get("input")
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        return False
    strings = ("system", "effort", "model", "cwd", "borrowed_session", "redo")
    return (all(data.get(key) is None or isinstance(data[key], str) for key in strings)
            and (data.get("schema") is None or isinstance(data["schema"], dict))
            and isinstance(data.get("hop"), int) and not isinstance(data["hop"], bool)
            and data["hop"] >= 0 and isinstance(data.get("title"), str)
            and isinstance(data.get("after"), list)
            and all(isinstance(item, str) for item in data["after"])
            and isinstance(value.get("images"), list))


def valid_watch(value):
    """One bounded progress trigger in its task journal, without a second store."""
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        return False
    event = value.get("event")
    if (not isinstance(value.get("run_id"), str) or not isinstance(value.get("seen"), str)
            or type(value.get("wakes")) is not int or not 1 <= value["wakes"] <= 32
            or type(value.get("attempts")) is not int or not 0 <= value["attempts"] <= 32
            or not isinstance(event, dict)):
        return False
    owner = event.get("owner")
    return (event.get("id") == "%s-gate-%d" % (value["run_id"], value["wakes"])
            and event.get("state") in ("pending", "running", "ready", "done", "unknown")
            and event.get("progress") == value["seen"] and bool(value["seen"])
            and isinstance(event.get("lines"), list) and len(event["lines"]) <= 4
            and all(isinstance(line, str) and len(line) <= 2000 for line in event["lines"])
            and isinstance(owner, list) and len(owner) == 2 and isinstance(owner[0], str)
            and type(owner[1]) is int and owner[1] >= 0
            and all(type(item) in (int, float) and 0 <= item <= 1e12 and math.isfinite(item)
                    for item in (value.get("woke_at"), event.get("created_at"),
                                 event.get("started_at"), event.get("retry_at", 0)))
            and (event["state"] != "ready" or isinstance(event.get("reply"), str)))


def watch_transition(previous, current, run_id):
    if not valid_watch(current) or current["run_id"] != run_id:
        return False
    event = current["event"]
    if previous is None:
        return current["wakes"] == 1 and current["attempts"] == 0 and event["state"] == "pending"
    if not valid_watch(previous) or previous["run_id"] != run_id:
        return False
    old = previous["event"]
    if event["id"] != old["id"]:
        return (old["state"] in ("done", "unknown") and event["state"] == "pending"
                and current["wakes"] == previous["wakes"] + 1 and current["seen"] != previous["seen"]
                and current["attempts"] == previous["attempts"])
    fields = ("id", "progress", "lines", "owner", "created_at", "started_at")
    return (current["wakes"] == previous["wakes"] and current["seen"] == previous["seen"]
            and current["attempts"] == previous["attempts"] + int(old["state"] == "pending" and event["state"] == "running")
            and all(event.get(key) == old.get(key) for key in fields)
            and event["state"] in {"pending": {"running"}, "running": {"pending", "ready", "unknown", "done"},
                                   "ready": {"done"}, "unknown": {"done"}}.get(old["state"], set()))
