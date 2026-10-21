"""记录中心：Runtime 里唯一能改记录的地方（见 docs/development/unified-message-model 的“目标架构”）。

调用方只说发生了什么——主人说了一句、小幽或帮手答了一句、一件事的状态或进展变了、电脑上
观察到了什么、一次请求的状态变了——编号、kind、序号、落盘都由这里和它下面的 Cards /
MessageLog 决定。授权的序号也从这里取。卡、/v1/feed、/v1/sync、给模型的上下文都是读，
不写；tests/test_record.py 检查别的模块没有绕过这里直接写卡或取序号。
"""

import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from .cards import Cards
from .desktop_content import redact_credentials

SCOPES = ("main", "task")
# 一句回复是什么：普通回复、交出去时的那句、帮手原样的结果、小幽的总结、提示（比如出错、重启）。
KINDS = ("reply", "handoff", "worker_result", "summary", "note")
# 只有这些字段能经 task() 改：说的话要走 exchange / reply，观察来的要走 observed。
TASK_FIELDS = {"state", "agent", "brief", "mood", "started_at", "queued", "fresh", "effort", "model",
               "model_asked", "run_id", "result_ready", "summary_partial", "approval", "cost", "edits",
               "title", "title_source", "attachments", "progress_lines", "borrowed_session",
               "borrowed_cwd", "desktop_card", "finished_at", "error", "delegated_to"}


class RecordError(ValueError):
    pass


class Record:
    def __init__(self, cards: Cards):
        self._cards = cards
        self._local = threading.local()

    # ---- 事 ----

    @contextmanager
    def opening(self, **fields: Any) -> Iterator[None]:
        """这段时间里、这个线程上开的卡都带上这些字段（小幽网络：替别的 Runtime 做的事
        开卡时就记着 delegated_from，那台第一次看到这张卡就知道不用另外显示）。"""
        previous = getattr(self._local, "fields", {})
        self._local.fields = dict(previous, **fields)
        try:
            yield
        finally:
            self._local.fields = previous

    def task_open(self, conversation: str, title: str, state: str = "done",
                  agent: Optional[str] = None, **fields: Any) -> Dict[str, Any]:
        """开一件事（一张卡）。"""
        return self._cards.open(conversation, title, state, agent,
                                **dict(getattr(self._local, "fields", {}), **fields))

    def task(self, card_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        """一件事的状态、谁在做、用量、授权这些字段变了。不含说的话。"""
        unknown = set(fields) - TASK_FIELDS
        if unknown:
            raise RecordError("task() 不改这些字段：%s（说的话走 exchange / reply）" % sorted(unknown))
        return self._cards.update(card_id, **fields)

    def progress(self, card_id: str, line: str) -> Optional[Dict[str, Any]]:
        """帮手报了一行进展。"""
        return self._cards.update(card_id, progress=redact_credentials(line))

    def activity(self, card_id: str, author: str, phase: str, text: str,
                 event_id: str) -> None:
        """Persist a public task event separately from conversation messages."""
        if phase not in ("dispatch", "message", "tool_start", "tool_result", "completed", "failed", "cancelled"):
            raise RecordError("Unknown activity phase: %s" % phase)
        text = redact_credentials(text).strip()
        if not text:
            return
        if len(text) > 4000:
            text = text[:4000] + "\n[内容较长，已截取前 4000 字]"
        self._cards.activity(card_id, author, phase, text, event_id)

    def attach(self, card_id: str, attachments: List[Dict[str, Any]],
               entry_index: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """这件事有了产物（文件、图片）。"""
        return self._cards.add_attachments(card_id, attachments, entry_index)

    # ---- 说的话 ----

    def exchange(self, card_id: str, said: Optional[str], say: Optional[str], *, turn_id: Optional[str] = None,
                 scope: str = "main", kind: str = "reply", **fields: Any) -> Optional[Dict[str, Any]]:
        """主人说的一句（said，可以没有）和对它的回答（say）。编号是 turn_id + :you / :reply。"""
        self._check(scope, kind)
        return self._cards.update(card_id, said=said, say=say, turn_id=turn_id, scope=scope, kind=kind,
                                  **self._task_fields(fields))

    def reply(self, card_id: str, say: str, *, turn_id: Optional[str] = None, scope: str = "main",
              kind: str = "reply", author: str = "xiaoyou",
              attachments: Optional[List[Dict[str, Any]]] = None, **fields: Any) -> Optional[Dict[str, Any]]:
        """小幽或帮手单独说的一句（帮手的结果、小幽的总结）。"""
        self._check(scope, kind)
        return self._cards.update(card_id, say=say, turn_id=turn_id, scope=scope, kind=kind, author=author,
                                  attachments=attachments, **self._task_fields(fields))

    # ---- 观察来的（电脑上的会话、外部上报）：只读，内容由来源决定 ----

    def observed_open(self, conversation: str, title: str, state: str, agent: str,
                      **fields: Any) -> Dict[str, Any]:
        return self._cards.open(conversation, title, state, agent, **fields)

    def observed(self, card_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        """来源报来的样子整段替换（含 entries）；只用于只读的观察卡。"""
        card = self._cards.get(card_id)
        if card is not None and not (card.get("monitoring_only") or fields.get("monitoring_only")):
            raise RecordError("observed() 只用于电脑上观察到的会话或外部上报的会话")
        return self._cards.update(card_id, **fields)

    # ---- 别的 Runtime 的记录（小幽网络，mesh.py）：只读副本，内容由那台决定 ----

    def replica_task(self, card_id: Optional[str], fields: Dict[str, Any],
                     entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """别的 Runtime 的一件事在这里的副本；card_id 为 None 时开一张新的本地卡。"""
        if not fields.get("mesh_origin"):
            raise RecordError("replica_task() 只用于别的 Runtime 的事，要带 mesh_origin")
        current = self._cards.get(card_id) if card_id else None
        if current is not None and current.get("mesh_origin") != fields["mesh_origin"]:
            raise RecordError("这张卡不是那台 Runtime 的副本")
        return self._cards.replica(card_id, fields, entries)

    def replica_message(self, record: Dict[str, Any]) -> bool:
        """别的 Runtime 的一条消息在这里的副本；变了返回 True。"""
        if not record.get("mesh_origin"):
            raise RecordError("replica_message() 只用于别的 Runtime 的消息，要带 mesh_origin")
        return self._cards.replicate_message(record)

    # ---- 请求和序号 ----

    def request_changed(self, message: Dict[str, Any], revise: bool = True) -> None:
        """Service 收下的一次请求变了：修订号、序号、写进日志在同一把锁里一起做，不会漏一步。"""
        self._cards.request_changed(message, revise)

    def sequence(self, at_least: int = 0) -> int:
        """取一个新序号（授权变化用；at_least 用在启动时接上请求记录里见过的最大序号）。"""
        return self._cards.bump(at_least)

    # ----

    @staticmethod
    def _check(scope: str, kind: str) -> None:
        if scope not in SCOPES:
            raise RecordError("scope 只能是 main 或 task：%r" % scope)
        if kind not in KINDS:
            raise RecordError("kind 只能是 %s：%r" % ("、".join(KINDS), kind))

    @staticmethod
    def _task_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
        unknown = set(fields) - TASK_FIELDS
        if unknown:
            raise RecordError("这些字段不能和说的话一起改：%s" % sorted(unknown))
        return fields
