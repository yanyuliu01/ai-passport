"""后台任务：一件事里花时间的那部分，由一个帮手在自己的会话里做。

每个任务一个线程，任务之间互不等待。同一个帮手可以同时做几件事：帮手的会话是按
“事 + 帮手”记的，后来对同一件事的补充能接上。

做的过程中主人可以：
  补充（after）   这一轮做完，接着在同一个会话里做补充的部分；
  改要求（redo）  帮手支持中途追加就直接告诉它；不支持就停掉这一轮，在同一个会话里
                  按新的要求重做；
  取消            停掉，结果不要了。
"""

import threading
import hashlib
import json
from copy import deepcopy
from collections import deque
from dataclasses import asdict, dataclass, field
import uuid
from typing import Any, Callable, Deque, Dict, List, Optional

from .agents import Agent, AgentError, Control, Job, Outcome
from ..world.store import Store

MAX_PENDING = 8

# 一件事做完（或没做成）时调用：(任务, 结果, 错误)；两个里正好有一个不是 None。
Finish = Callable[["Task", Optional[Outcome], Optional[str]], None]


class StartError(RuntimeError):
    """Admission completed, but no worker thread could be started."""


class Deferred(RuntimeError):
    """The saved input may not begin here now; it stays pending for recovery."""


def fingerprint(agent: Agent) -> str:
    """Bind recovery to the configured helper without storing its credentials."""
    data = json.dumps(asdict(agent.spec), sort_keys=True, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def redo_note(text: str, original: Optional[str]) -> str:
    """改了要求之后交给帮手的话。original 不为空表示它不记得原来的任务，要一起给。"""
    lead = "（原来的任务：%s）\n\n" % original if original else ""
    return "%s（主人改了要求：%s。按新的要求继续，已经做过的不用重复。）" % (lead, text)


def after_note(text: str) -> str:
    return "（主人补充了一句：%s。在刚才的基础上接着做。）" % text


@dataclass
class Task:
    card: str
    conversation: str
    agent: Agent
    text: str
    # 帮手以小鱼的身份回话时的人设和回复结构；只干活的是 None
    system: Optional[str] = None
    schema: Optional[Dict[str, Any]] = None
    hop: int = 0
    control: Control = field(default_factory=Control)
    redo: Optional[str] = None
    after: Deque[str] = field(default_factory=deque)
    cancelled: bool = False
    queued: bool = False
    # 这件事用哪一档努力程度、哪个模型；None 用帮手平时的
    effort: Optional[str] = None
    model: Optional[str] = None
    images: tuple = ()
    artifact_dir: Optional[str] = None
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    # 接着别人的会话做（比如主人在电脑上开的）：在那个会话的目录里、用那个会话编号跑，
    # 但会话不归 Runtime 管，不记进 store：电脑那边照常观察它，主人也能回电脑上接着聊
    cwd: Optional[str] = None
    borrowed_session: Optional[str] = None
    title: str = ""
    relaying_supplement: bool = False
    artifact_error: str = ""
    full_reply_saved: bool = False
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def intent(self, images=()) -> Dict[str, Any]:
        return {"version": 1, "state": "pending", "run_id": self.run_id,
                "agent": self.agent.name, "fingerprint": fingerprint(self.agent),
                "images": list(images),
                "input": deepcopy({**{key: getattr(self, key) for key in (
                    "text", "system", "schema", "hop", "effort", "model",
                    "cwd", "borrowed_session", "title", "redo")},
                    "after": list(self.after)})}

    @classmethod
    def restore(cls, card, agent, intent, images=()):
        data = deepcopy(intent["input"])
        data["after"] = deque(data["after"])
        return cls(card=card["id"], conversation=card["conversation"],
                   agent=agent, run_id=intent["run_id"], images=tuple(images), **data)

    @property
    def session_key(self) -> str:
        # 对话名里不能有斜杠，所以这个键不会和某个对话撞上。
        return "%s/%s" % (self.conversation, self.card)


class Tasks:
    def __init__(self, store: Store, finish: Finish, max_parallel: int = 0,
                 started: Optional[Callable[[Task], None]] = None,
                 prepare: Optional[Callable[[Task, Control], None]] = None,
                 release: Optional[Callable[[Task, Control], None]] = None):
        self._store = store
        self._finish = finish
        self._started = started or (lambda task: None)
        # 每一轮开始前、结束后各调用一次：给遥控器接上进展、授权这些，用完收回。
        self._prepare = prepare or (lambda task, control: None)
        self._release = release or (lambda task, control: None)
        self._lock = threading.Lock()
        self._active: Dict[str, Task] = {}
        self._closed = False
        self._threads: List[threading.Thread] = []
        self._slots = threading.Semaphore(max_parallel) if max_parallel > 0 else None

    def active(self, card: str) -> bool:
        with self._lock:
            return card in self._active

    def count(self) -> int:
        with self._lock:
            return len(self._active)

    def start(self, task: Task, before_start: Optional[Callable[[], None]] = None) -> bool:
        """开始做一件事；重复时返回 False，不调用 before_start。

        before_start 在任务占位后、线程启动前保存本地派发记录；失败时不启动。
        """
        with self._lock:
            if self._closed or task.card in self._active:
                return False
            task.queued = self._slots is not None
            self._active[task.card] = task
            prepared = False
            try:
                # Reserve this card before saving its run identity. A duplicate
                # cannot replace the identity of a worker already executing.
                # This callback persists/enqueues locally; it must not do I/O
                # to a helper or wait for a worker.
                if before_start is not None:
                    before_start()
                prepared = True
                thread = threading.Thread(target=self._run, args=(task,), daemon=True,
                                          name="xiaoyu-task-%s" % task.card)
                thread.start()
            except BaseException as error:
                self._active.pop(task.card, None)
                if prepared and isinstance(error, Exception):
                    raise StartError("Worker thread could not start") from error
                raise
            self._threads = [old for old in self._threads if old.is_alive()] + [thread]
        return True

    def amend(self, card: str, mode: str, text: str, effort: Optional[str] = None,
              model: Optional[str] = None, before_queue: Optional[Callable[[], None]] = None,
              before_change: Optional[Callable[..., None]] = None) -> Optional[str]:
        """对正在做的事补充或改要求。返回实际怎么办的：steer、redo、after；排着的补充已经
        有 MAX_PENDING 条、这条没记上时返回 full；没在做返回 None。

        effort / model 不是 None 时，之后的几轮换成这一档、这个模型。正在跑的那一轮换不了：
        改要求（redo）时为了换档会停掉重做，不走中途追加。
        """
        with self._lock:
            task = self._active.get(card)
            if task is None:
                return None
            if mode == "after" and len(task.after) >= MAX_PENDING:
                return "full"
            if before_change is not None:
                before_change(task, mode, text, effort, model)
            switched = ((effort is not None and effort != task.effort)
                        or (model is not None and model != task.model))
            if effort is not None:
                task.effort = effort
            if model is not None:
                task.model = model
            if mode == "after":
                if before_queue is not None:
                    before_queue()
                task.after.append(text)
                return "after"
            control = task.control
        # 中途追加要和帮手通信，不拿着锁做。
        if not switched and control.steer(text):
            return "steer"
        with self._lock:
            if self._active.get(card) is not task:
                return None
            task.redo = text
            control = task.control
        control.cancel()
        return "redo"

    def cancel(self, card: str) -> bool:
        with self._lock:
            task = self._active.pop(card, None)
            if task is None:
                return False
            task.cancelled = True
            control = task.control
        control.cancel()
        return True

    def close(self) -> None:
        with self._lock:
            self._closed = True
            tasks = list(self._active.values())
            self._active.clear()
            threads = list(self._threads)
        for task in tasks:
            task.cancelled = True
            task.control.cancel()
        for thread in threads:
            thread.join(timeout=5)

    def _run(self, task: Task) -> None:
        if self._slots is not None:
            self._slots.acquire()
        try:
            with self._lock:
                task.queued = False
                gone = task.cancelled
            if not gone:
                try:
                    self._started(task)
                except Deferred:
                    # Nothing was claimed or invoked: release the card without a
                    # terminal result, so recovery can start the same input later.
                    with self._lock:
                        if self._active.get(task.card) is task:
                            self._active.pop(task.card, None)
                    return
                except Exception as failure:
                    with self._lock:
                        if self._active.get(task.card) is task:
                            self._active.pop(task.card, None)
                    self._finish(task, None, "Runtime 内部出错：%s: %s" % (
                        type(failure).__name__, failure))
                    return
                self._work(task)
        finally:
            if self._slots is not None:
                self._slots.release()

    def _work(self, task: Task) -> None:
        text = task.text
        while True:
            with self._lock:
                if task.cancelled:
                    return
                if task.redo is not None:
                    # 还没开始这一轮，要求又改了：直接按最新的来。
                    text = redo_note(task.redo, self._original(task))
                    task.redo = None
                task.control = Control()
                control = task.control
            session = task.borrowed_session or self._store.session(task.session_key, task.agent.name)
            # 一拿到会话编号就记下：这一轮中途被停掉，下一轮也能接着用。借来的会话不记。
            if task.borrowed_session is None:
                control.session = lambda session_id, task=task: self._store.remember(
                    task.session_key, task.agent.name, session_id)
            outcome: Optional[Outcome] = None
            error: Optional[str] = None
            try:
                self._prepare(task, control)
                control.activity("dispatch", text, "dispatch")
                system = task.system
                if task.artifact_dir:
                    instruction = (
                        "\n\n## Task files for the phone\n"
                        "When this task produces an image, design document or downloadable file, "
                        "save that output inside this task's directory: %s. "
                        "Write manifest.json there with {\"artifacts\":[{\"path\":\"preview.png\","
                        "\"name\":\"Preview\"}]} (paths relative to that directory). "
                        "Only list finished task outputs; never credentials, personal data or raw logs. "
                        "Do not create files for a text-only answer. Keep the requested reply format."
                    ) % task.artifact_dir
                    system = (system or "") + instruction
                outcome = task.agent.run(Job(
                    text=text, session_id=session, conversation=task.conversation, title=task.title,
                    system=system, schema=task.schema, hop=task.hop, control=control,
                    effort=task.effort, model=task.model, cwd=task.cwd, card=task.card, images=task.images,
                    require_session=bool(task.borrowed_session),
                ))
            except AgentError as failure:
                error = str(failure)
            except Exception as failure:  # 任务线程不能悄悄死掉：当成这件事没做成
                error = "Runtime 内部出错：%s: %s" % (type(failure).__name__, failure)
            finally:
                phase = "cancelled" if control.cancelled else "failed" if error else "completed"
                try:
                    control.activity(phase, error or ("本轮已停止" if control.cancelled else "本轮执行结束"), "end")
                finally:
                    self._release(task, control)
            if outcome is not None and outcome.session_id and task.borrowed_session is None:
                self._store.remember(task.session_key, task.agent.name, outcome.session_id)
            with self._lock:
                if task.cancelled:
                    return
                if task.redo is not None:
                    text = redo_note(task.redo, self._original(task))
                    task.redo = None
                    continue
                if error is None and task.after:
                    text = after_note(task.after.popleft())
                    task.relaying_supplement = True
                    continue
                # 和“还在不在做”的判断在同一把锁里：之后再来的补充会另起一轮，不会丢。
                self._active.pop(task.card, None)
            self._finish(task, outcome, error)
            # Runs after the task's final round and CLI cleanup, never between
            # queued follow-ups. Opening the app does not change the outcome.
            from .desktop_handoff import completed
            completed(task, outcome, error)
            return

    def _original(self, task: Task) -> Optional[str]:
        """帮手这边没有会话可接（比如第一轮还没跑完就被停掉）时，原来的任务要重新给。"""
        if task.borrowed_session or self._store.session(task.session_key, task.agent.name):
            return None
        return task.text
