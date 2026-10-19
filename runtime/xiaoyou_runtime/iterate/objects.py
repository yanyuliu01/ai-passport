"""插件之间传的数据。都是能原样写成 JSON 的对象，存在每一轮的目录里，方便重跑和回放。"""

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

FORMAT = 1
SEVERITIES = range(1, 6)
ISSUE_STATES = ("open", "planned", "fixing", "released", "closed", "failed", "ignored")
MAX_EVIDENCE = 10


@dataclass
class Signal:
    """一次观察：App 报来的一个事件、对话里的一句抱怨、一件失败的事。"""

    id: str
    type: str          # 例如 app.crash、chat.feedback、task.failed
    at: float          # 秒
    source: str        # 哪个 source 插件产生的
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Signal":
        return cls(id=str(raw["id"]), type=str(raw["type"]), at=float(raw["at"]),
                   source=str(raw.get("source", "")), data=dict(raw.get("data") or {}))


@dataclass
class Issue:
    """检测出来的一个问题。同一个 fingerprint 是同一个问题，跨轮累计。"""

    fingerprint: str
    detector: str
    area: str            # app / runtime / agent
    title: str
    severity: int        # 1 轻 … 5 重
    count: int
    first_seen: float
    last_seen: float
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    # 帮忙定位的线索：screen、exception、frames、message、keywords
    hints: Dict[str, Any] = field(default_factory=dict)
    status: str = "open"
    attempts: int = 0
    runs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Issue":
        known = {name: raw[name] for name in cls.__dataclass_fields__ if name in raw}
        return cls(**known)


def fingerprint(detector: str, *parts: Any) -> str:
    text = "\x1f".join([detector] + [str(part) for part in parts])
    return detector + ":" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def evidence(signals: List[Signal], excerpt: Any) -> List[Dict[str, Any]]:
    """最近的几条信号当证据；excerpt(signal) 给一句不带隐私的摘要。"""
    latest = sorted(signals, key=lambda item: item.at, reverse=True)[:MAX_EVIDENCE]
    return [{"signal": item.id, "at": item.at, "excerpt": str(excerpt(item))[:160]} for item in latest]
