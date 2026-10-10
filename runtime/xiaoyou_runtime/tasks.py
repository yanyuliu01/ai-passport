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
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional

from .agents import Agent, AgentError, Control, Job, Outcome
from .store import Store

MAX_PENDING = 8

# 一件事做完（或没做成）时调用：(任务, 结果, 错误)；两个里正好有一个不是 None。
Finish = Callable[["Task", Optional[Outcome], Optional[str]], None]


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
    # 帮手以小幽的身份回话时的人设和回复结构；只干活的是 None
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
        self._threads: List[threading.Thread] = []
        self._slots = threading.Semaphore(max_parallel) if max_parallel > 0 else None

    def active(self, card: str) -> bool:
        with self._lock:
            return card in self._active

    def count(self) -> int:
        with self._lock:
            return len(self._active)

    def start(self, task: Task) -> bool:
        """开始做一件事；这张卡上已经有任务在做时返回 False。"""
        with self._lock:
            if task.card in self._active:
                return False
            task.queued = self._slots is not None
            self._active[task.card] = task
            thread = threading.Thread(target=self._run, args=(task,), daemon=True,
                                      name="xiaoyou-task-%s" % task.card)
            self._threads = [old for old in self._threads if old.is_alive()] + [thread]
        thread.start()
        return True

    def amend(self, card: str, mode: str, text: str, effort: Optional[str] = None,
              model: Optional[str] = None) -> Optional[str]:
        """对正在做的事补充或改要求。返回实际怎么办的：steer、redo、after；没在做返回 None。

        effort / model 不是 None 时，之后的几轮换成这一档、这个模型。正在跑的那一轮换不了：
        改要求（redo）时为了换档会停掉重做，不走中途追加。
        """
        with self._lock:
            task = self._active.get(card)
            if task is None:
                return None
            switched = ((effort is not None and effort != task.effort)
                        or (model is not None and model != task.model))
            if effort is not None:
                task.effort = effort
            if model is not None:
                task.model = model
            if mode == "after":
                if len(task.after) < MAX_PENDING:
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
                self._started(task)
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
            session = self._store.session(task.session_key, task.agent.name)
            # 一拿到会话编号就记下：这一轮中途被停掉，下一轮也能接着用。
            control.session = lambda session_id, task=task: self._store.remember(
                task.session_key, task.agent.name, session_id)
            outcome: Optional[Outcome] = None
            error: Optional[str] = None
            try:
                self._prepare(task, control)
                outcome = task.agent.run(Job(
                    text=text, session_id=session, conversation=task.conversation,
                    system=task.system, schema=task.schema, hop=task.hop, control=control,
                    effort=task.effort, model=task.model,
                ))
            except AgentError as failure:
                error = str(failure)
            except Exception as failure:  # 任务线程不能悄悄死掉：当成这件事没做成
                error = "Runtime 内部出错：%s: %s" % (type(failure).__name__, failure)
            finally:
                self._release(task, control)
            if outcome is not None and outcome.session_id:
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
                    continue
                # 和“还在不在做”的判断在同一把锁里：之后再来的补充会另起一轮，不会丢。
                self._active.pop(task.card, None)
            self._finish(task, outcome, error)
            return

    def _original(self, task: Task) -> Optional[str]:
        """帮手这边没有会话可接（比如第一轮还没跑完就被停掉）时，原来的任务要重新给。"""
        if self._store.session(task.session_key, task.agent.name):
            return None
        return task.text
