"""Intake：写进记录的唯一入口（见 docs/development/runtime-refactor-design 的状态接收层）。

现在它先包住 Record：每个来源（主人、帮手、电脑会话、其他电脑、小幽自己、系统）拿一个
Writer，写法和 Record 一样，只是每次写入都记上“从哪来、谁做的、因为什么”
（provenance：source、actor、cause）。卡上新加的条目、卡的 changed_by、Service 的请求记录
都会带上它，统一消息日志里也看得到。

写入点按来源一类一类搬过来；搬完之后，除了这里和 record.py，没有模块直接调 Record 的
写方法。读取照旧直接读 Record / Cards。
"""

import inspect

from typing import Any, Callable, Optional

from ..world.artifacts import ArtifactError
from .record import Record

SOURCES = ("owner", "helper", "desktop", "mesh", "xiaoyou", "timer", "system")

# Record 里会改动记录的方法。不在这里的（读取、opening 这类上下文）原样转给 Record。
WRITES = frozenset({
    "task_open", "task", "execution_binding", "progress", "progress_checkpoint", "activity", "attach",
    "exchange", "reply", "observed_open", "observed", "replica_task", "replica_message",
    "request_changed", "sequence",
    "conversation_create", "conversation_rename", "conversation_replica", "conversation_touch",
    "directory_edit", "directory_replica", "directory_sources",
    "directory_generated", "directory_generated_replica",
})


class Writer:
    """绑定了来源的写入口。by() 换一个发起方，得到新的 Writer，不改原来的。"""

    def __init__(self, record: Record, source: str, actor: Optional[str] = None,
                 cause: Optional[str] = None):
        if source not in SOURCES:
            raise ValueError("unknown intake source: %s" % source)
        self._record = record
        self.source, self.actor, self.cause = source, actor, cause

    def by(self, actor: Optional[str] = None, cause: Optional[str] = None) -> "Writer":
        return Writer(self._record, self.source, actor or self.actor, cause or self.cause)

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._record, name)
        if name not in WRITES:
            return target
        return self._stamped(target)

    def _stamped(self, write: Callable[..., Any]) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            provenance = {key: value for key, value in
                          {"source": self.source, "actor": self.actor, "cause": self.cause}.items() if value}
            if self.source == "desktop" and write.__name__ not in {
                    "observed_open", "observed", "sequence", "directory_sources", "directory_generated"}:
                raise ValueError("Desktop observation cannot modify owner tasks or messages")
            if write.__name__ in ("observed_open", "observed"):
                bound = inspect.signature(write).bind(*args, **kwargs).arguments
                conversation = bound["conversation"] if write.__name__ == "observed_open" else (
                    self._record.cards.get(bound["card_id"]) or {}).get("conversation")
                if kwargs.get("conversation", conversation) != conversation:
                    raise ValueError("Observed sessions cannot change conversation")
                if conversation is not None and conversation != "__desktop__":
                    raise ValueError("Observed sessions cannot write a shared conversation")
                if write.__name__ == "observed_open":
                    kwargs = dict(kwargs, monitoring_only=True)
                if "entries" in kwargs:
                    kwargs = dict(kwargs, entries=[dict(entry, provenance=entry.get("provenance") or provenance)
                                                  for entry in kwargs["entries"]])
            with self._record.cards.provenance(**provenance):
                return write(*args, **kwargs)
        return call


class Intake:
    def __init__(self, record: Record):
        self.record = record

    @classmethod
    def for_cards(cls, cards) -> "Intake":
        return cls(Record(cards))

    def writer(self, source: str, actor: Optional[str] = None, cause: Optional[str] = None) -> Writer:
        return Writer(self.record, source, actor, cause)


def attach_artifacts(artifacts, cards, card_id: str, manifest: Any, entry_index: Optional[int] = None,
                     agent: Optional[str] = None):
    """Register a task's files with the artifact store, then attach them through Intake."""
    card = cards.get(card_id)
    if card is None:
        raise ArtifactError("没有这件事")
    # Validate the binding before copying any files or updating the index.
    if entry_index is not None and (isinstance(entry_index, bool) or not isinstance(entry_index, int)
                                   or not 0 <= entry_index < len(card["entries"])):
        raise ArtifactError("entry_index 应该是现有消息的序号")
    attachments = artifacts.register(card_id, manifest, agent=agent or card.get("agent"))
    return Intake.for_cards(cards).writer("helper", actor=agent or card.get("agent") or "runtime",
                                           cause=card_id).attach(card_id, attachments, entry_index)
