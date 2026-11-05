"""投递闸和后台整理：小鱼在主人没开口的时候，什么时候被叫醒、醒了说不说由她定。

参考 Muse：主动性分两层——便宜的规则决定“什么时候叫醒她”，她自己决定“说不说、说什么”；
后台的产出不直接对主人说话，都要过她这一关。这里有三件事，都在一个后台线程里每 TICK 秒看一眼：

1. 事情做到一半（progress）：帮手做了 WATCH_AFTER 秒以上、又报了新进展，距上次叫她至少
   WATCH_EVERY 秒，一件事最多叫 WATCH_MAX 次。她看了觉得值得说（有阶段结果、卡住了、比预想
   的久很多……）才说；不值得就回“（不说）”，什么都不发。
2. 答应的事到点了（promises.py）：叫她一次，她决定现在说还是先不说（比如主人说过晚上别打扰）；
   不说的话一小时后再叫。
3. 整理记忆（upkeep）：对话安静了 UPKEEP_QUIET 秒、又有新的几轮时，让她把漏记的偏好、规矩
   补进记忆（只给记忆的工具，不说话）。

叫醒她用的是说话的代理（要能用工具调用）；用不了时这几件都不做，不退回写死的话。
"""

import re
import json
import sqlite3
import sys
import threading
import time
from typing import Any, List, Optional

from ..helpers.agents import AgentError
from ..helpers.deadline import Deadline
from .context_summaries import RULES as SUMMARY_RULES, SegmentSummaries
from .relationship import RULES as RELATIONSHIP_RULES
from .effects import EffectFenceError, EffectUncertain
from .voice_tools import Toolbox
from .prompt_rules import with_runtime_rules
from ..world.world import durable_write
from .progress_wakes import ProgressPorts, ProgressWakes
from .timer_wakes import MemoryUpkeep, ReminderPorts, ReminderWakes, UpkeepPorts

TICK = 20
WATCH_AFTER = 90
WATCH_EVERY = 240
WATCH_MAX = 3
UPKEEP_QUIET = 600
UPKEEP_MIN_TURNS = 2
UPKEEP_MAX_TURNS = 30
SILENT = re.compile(r"^\s*[（(]?\s*不说\s*[）)]?\s*$")

GATE_RULES = (
    "## 这一轮是 Runtime 叫醒你的，不是主人在说话\n"
    "你可以主动跟主人说一句，也可以不说。值得说才说：有阶段结果、需要主人拿主意、卡住了、比预想的"
    "久很多、答应的事到点了。下面“你最近跟主人说过的话”里已经说过的，不要换个说法再说；主人说过"
    "这个时间别打扰的，就先不说。决定不说，就只回一行：（不说）。要说就直接写给主人的话。"
)
UPKEEP_RULES = (
    "## 这一轮是在整理记忆，不跟主人说话\n"
    "看看下面这几轮对话里，主人有没有说过值得长期记住、而你还没记的：偏好、定下的规矩、长期的情况、"
    "长期的约定。有就用 remember 记（带上主人的原话）；主人改了以前的说法，用 replace 改那一条；"
    "主人明确不要了的用 forget。一次性的小事、进度、流水账不记；已经记着的不要重复记；密码、密钥、"
    "验证码绝不记。新说法优先，旧对话与当前记忆冲突时不要把旧说法重新记回来。整理完只回一行：（好了）。"
)


class Pulse:
    """后台节拍、共享队列准入及模型／投递适配；持久化唤醒规则独立管理。"""

    def __init__(self, xiaoyu: Any, conversation: str = "default"):
        self._xiaoyu = xiaoyu
        self._conversation = conversation
        self._pending = set()
        self._lock = threading.Lock()
        self._browser_planning = False
        self._upkeep_next = 'default'
        self._relationship_next_check = {}
        self._stop = threading.Event()
        self._progress = ProgressWakes(
            lambda: self._xiaoyu.cards,
            lambda cause: self._xiaoyu.intake.writer("timer", actor="xiaoyou", cause=cause),
            ProgressPorts(
                owner=lambda conversation: self._owner(conversation),
                stopped=self._stop.is_set,
                pending=self._progress_pending,
                available=lambda: self._voice() is not None,
                policy=lambda: (WATCH_AFTER, WATCH_EVERY, WATCH_MAX),
                ask=lambda event, conversation, owner: self._ask(
                    event, ["task_detail", "recall"], GATE_RULES, conversation, owner=owner),
                say=lambda *args, **kwargs: self._say(*args, **kwargs),
                text=lambda text: self._xiaoyu.chat_text(text),
                silent=lambda text: not text or SILENT.match(text)
                    or text.startswith(("（不说", "(不说"))))
        self._reminders = ReminderWakes(
            xiaoyu.promises, lambda: self._xiaoyu.cards,
            lambda cause: self._xiaoyu.intake.writer("timer", actor="xiaoyou", cause=cause),
            ReminderPorts(
                owner=lambda conversation: self._owner(conversation),
                stopped=self._stop.is_set,
                ask=lambda event, conversation, owner, effects: self._ask(
                    event, ["recall", "resolve", "track"], GATE_RULES, conversation,
                    owner=owner, effects=effects),
                say=lambda *args, **kwargs: self._say(*args, **kwargs),
                silent=lambda text: not text or SILENT.match(text)
                    or text.startswith(("（不说", "(不说"))), conversation)
        self._upkeep = MemoryUpkeep(
            lambda: self._xiaoyu.context, lambda: self._xiaoyu.memory,
            xiaoyu.memory.log_dir.parent / "upkeep.json",
            UpkeepPorts(
                owner=lambda conversation: self._owner(conversation),
                stopped=self._stop.is_set,
                ask=lambda event, owner, effects, revision: self._ask(
                    event, ["remember", "replace", "forget"], UPKEEP_RULES,
                    owner=owner, effects=effects, expected_memory_revision=revision),
                policy=lambda: (UPKEEP_QUIET, UPKEEP_MIN_TURNS, UPKEEP_MAX_TURNS),
                write=lambda path, state: durable_write(path, state)), conversation)
        # Each conversation owns its durable cursor; both update the same
        # OwnerMemory. Keep the legacy default marker untouched for rollback.
        self._weixin_upkeep = MemoryUpkeep(
            lambda: self._xiaoyu.context, lambda: self._xiaoyu.memory,
            xiaoyu.memory.log_dir.parent / "upkeep-weixin.json",
            UpkeepPorts(
                owner=lambda scope: self._owner(scope),
                stopped=self._stop.is_set,
                ask=lambda event, owner, effects, revision: self._ask(
                    event, ["remember", "replace", "forget"], UPKEEP_RULES,
                    conversation="weixin", owner=owner, effects=effects, expected_memory_revision=revision),
                policy=lambda: (UPKEEP_QUIET, UPKEEP_MIN_TURNS, UPKEEP_MAX_TURNS),
                write=lambda path, state: durable_write(path, state)), "weixin", bootstrap_recent=20)
        self._memory_upkeep_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="xiaoyu-pulse", daemon=True)

    # Compatibility for existing review/fault callers; state is owned by upkeep.
    @property
    def _marker(self):
        return self._upkeep._marker

    @property
    def _marker_state(self):
        return self._upkeep._marker_state

    @_marker_state.setter
    def _marker_state(self, state):
        self._upkeep._marker_state = state

    @property
    def _upkept(self):
        return self._upkeep._upkept

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()

    def unconfirmed(self, conversation):
        return (self._reminders.unconfirmed(conversation) or self._upkeep.unconfirmed(conversation)
                or self._weixin_upkeep.unconfirmed(conversation))

    def timer_entries(self, include_history=True):
        return (self._reminders.entries(include_history) + self._upkeep.entries(include_history)
                + self._weixin_upkeep.entries(include_history))

    def checkpoint_timer(self, entry, wake):
        if entry["archived"]:
            raise EffectUncertain("核对归档不能修改。")
        if entry["kind"] == "promise":
            self._reminders.checkpoint(entry, wake)
        else:
            upkeep = self._weixin_upkeep if entry.get("conversation") == "weixin" else self._upkeep
            upkeep.checkpoint(entry["target"], entry["wake"], wake)

    def archive_timer_review(self, entry):
        if entry["kind"] == "promise":
            self._reminders.archive_review(entry)
        else:
            upkeep = self._weixin_upkeep if entry.get("conversation") == "weixin" else self._upkeep
            upkeep.archive_review(entry)

    # ---- 帮手报进展（在任务线程里调用）：只记下，不在这里叫她 ----

    def progress(self, card: str, run_id: str, agent: str, line: str) -> None:
        self._xiaoyu.intake.writer("helper", actor=agent, cause=run_id).progress(card, line, run_id=run_id)

    # ---- 后台线程 ----

    def _run(self) -> None:
        while not self._stop.wait(TICK):
            try:
                self.tick()
            except Exception as error:  # 后台节拍出错不能拖垮 Runtime：记下来，下一拍再看
                sys.stderr.write("后台节拍出错：%r\n" % (error,))

    def tick(self, now: Optional[float] = None) -> None:
        """看一眼：该叫她的叫她。测试里可以直接调。"""
        self._xiaoyu.retry_effects()
        wall = time.time() if now is None else now
        for run_id, entry in self._due_watches(wall):
            card = self._xiaoyu.cards.get(entry["card"])
            if card is not None:
                self._enqueue(card["conversation"], ("watch", run_id, entry["watch"]["event"]["id"]),
                              lambda rid=run_id, item=entry: self._wake_for_task(rid, item))
        for item in self._reminders.due(wall):
            self._enqueue(item.get("conversation") or self._conversation, ("promise", item["id"]),
                          lambda item=item: self._wake_for_promise(item))
        # Only admit one memory-upkeep lane per tick. Starting both together
        # made default consistently win the non-blocking shared-memory lock,
        # starving WeChat; waiting on that lock would instead block its chat.
        wx_exists = bool(self._xiaoyu.cards.conversations.get('weixin'))
        if not self._memory_upkeep_lock.locked():
            use_weixin = wx_exists and self._upkeep_next == 'weixin'
            self._upkeep_next = 'default' if use_weixin else 'weixin'
            if use_weixin:
                self._enqueue('weixin', ('upkeep', 'weixin'),
                              lambda: self._maybe_weixin_upkeep(time.time() if now is None else now))
            else:
                self._enqueue(self._conversation, ("upkeep", self._conversation),
                              lambda: self._maybe_upkeep(time.time() if now is None else now))
        for conversation in (self._conversation, 'weixin'):
            if (wall >= self._relationship_next_check.get(conversation, 0)
                    and self._xiaoyu.cards.conversations.get(conversation)
                    and self._xiaoyu.relationship.due(conversation, wall)):
                self._relationship_next_check[conversation] = wall + UPKEEP_QUIET
                self._enqueue(conversation, ('relationship', conversation),
                              lambda conversation=conversation: self._maybe_relationship(conversation, time.time() if now is None else now))
        if self._xiaoyu.config.bilibili_autonomous:
            self._start_bilibili(time.time() if now is None else now)

    def _start_bilibili(self, now):
        """Keep model planning off the conversation lane and admit only one planner."""
        with self._lock:
            if self._browser_planning or self._stop.is_set():
                return
            self._browser_planning = True
        def run():
            try:
                self._maybe_bilibili(now)
            except Exception as error:
                sys.stderr.write("浏览探索决策未完成：%s\n" % type(error).__name__)
            finally:
                with self._lock:
                    self._browser_planning = False
        try:
            threading.Thread(target=run, name="xiaoyu-browser-planner", daemon=True).start()
        except RuntimeError:
            with self._lock:
                self._browser_planning = False

    def _maybe_bilibili(self, now):
        """Offer an exploration opportunity; Xiaoyu chooses whether and how to use it."""
        from ..entertainment.schedule import claim, claim_test, defer, record_test_state
        from ..entertainment.browser import logged_in
        xiaoyu = self._xiaoyu
        if self._stop.is_set() or self._owner(self._conversation) is None:
            return
        if xiaoyu.config.mesh_secret and xiaoyu.mesh is None:
            return  # Never explore while this Runtime cannot verify mesh ownership.
        if xiaoyu.mesh is not None and not xiaoyu.config.mesh_home:
            return
        worker = xiaoyu._browser_worker
        if worker is None:
            return
        if not logged_in():
            return
        schedule = xiaoyu.config.state_dir / "entertainment" / "bilibili" / "schedule.sqlite3"
        test_requested = claim_test(schedule, now)
        if not test_requested and not claim(schedule, now):
            return
        def test_state(state):
            if test_requested:
                try:
                    record_test_state(schedule, state)
                except (OSError, sqlite3.Error):
                    pass  # Diagnostics never change the decision or browsing.
        owner = self._owner(self._conversation)
        if owner is None or self._stop.is_set():
            test_state('ownership_changed')
            return
        voice = self._voice()
        if voice is None:
            test_state('voice_unavailable')
            return
        status = xiaoyu._browser_budget(now)
        def guard():
            if self._stop.is_set() or self._owner(self._conversation) != owner:
                raise EffectFenceError("浏览决策已过期")
        def check(fields):
            action = fields.get("action") or {}
            if action.get("type") == "browse_defer":
                schedule_view = status.get("self_schedule") or {}
                later = now + action.get("minutes", 0) * 60
                local_later = time.localtime(later)
                if (not schedule_view.get("available") or not schedule_view.get("deferrals_remaining")
                        or time.strftime("%Y-%m-%d", local_later) != time.strftime("%Y-%m-%d", time.localtime(now))
                        or not 10 <= local_later.tm_hour < 21):
                    return "今天没有可用的延期时机"
                return None
            if not status.get("available") or status.get("wait_seconds", 0) > 0:
                return "当前仍在访问限制或等待期，可以稍后再决定"
            return xiaoyu._plan(Outcome("", None, fields), self._conversation, None,
                                xiaoyu._available(0), False)[1]
        from ..helpers.base import Outcome
        box = Toolbox([], [], [], check, lambda card: None, conversation=self._conversation,
            only=["browse_budget", "browse_content", "defer_browsing"], guard=guard,
            browser_enabled=True, browser_budget=lambda: xiaoyu._browser_budget(now), browser_defer=True)
        system = (with_runtime_rules(xiaoyu.config.persona) + "\n\n" + "\n".join(xiaoyu.memory_lines())
                  + "\n\n最近的交流：\n" + (self._recent_said(self._conversation) or "（暂无）")
                  + "\n\n这是你自己的娱乐机会，不是主人发起的任务。"
                  "你可以不逛、用 defer_browsing 稍后再决定，或者用 browse_content 浏览。"
                  "自主决定候选数量和是否细看一条视频；不要为了用满额度而访问。"
                  "本轮只做自己的决定，不向主人发消息。当前本地状态：" + json.dumps(status, ensure_ascii=False))
        if test_requested:
            system += "\n主人请你试一次自主发现和分享。你仍然自己决定逛不逛、看什么，以及是否值得分享。"
        try:
            xiaoyu._speak(voice, "现在有一个自主探索的机会，你想怎么安排？", self._conversation,
                system, None, 0, catch_up=False, tools=box, ephemeral=True, guard=guard,
                budget=Deadline(xiaoyu.config.voice_timeout_seconds))
            guard()
        except (AgentError, EffectFenceError, OSError, RuntimeError):
            test_state('model_error')
            return
        action = box.action or {}
        if action.get("type") == "browse_defer":
            defer(schedule, action.get("minutes"), now)
            test_state('deferred')
            return
        if action.get("type") != "browse":
            test_state('no_browse')
            return
        plan, problem = xiaoyu._plan(Outcome("", None, {"action": action, "card": "new"}),
                                    self._conversation, None, xiaoyu._available(0), False)
        if problem is not None:
            test_state('plan_rejected')
            return
        writer = xiaoyu.intake.writer("timer", actor="xiaoyou", cause="bilibili-discovery")
        card = writer.task_open(self._conversation, "小鱼的B站探索", "working", worker.name)
        if self._owner(self._conversation) != owner or self._stop.is_set():
            test_state('ownership_changed')
            writer.task(card["id"], state="failed", brief="对话负责人已变化，未开始探索")
            return
        try:
            xiaoyu._autonomous_browser_cards.add(card['id'])
            xiaoyu._launch(card["id"], worker, plan["task"], self._conversation, 0)
            test_state('launched')
        except (OSError, ValueError, AgentError):
            xiaoyu._autonomous_browser_cards.discard(card['id'])
            test_state('launch_failed')
            writer.task(card["id"], state="failed", brief="这次探索没有启动")

    def _owner(self, conversation):
        mesh = self._xiaoyu.mesh
        if mesh is None:
            return ("local", 0)
        view = mesh.owner_of(conversation)
        if view.get("unresolved") or view.get("conflict") or view.get("owner") != mesh.runtime_id:
            return None
        return view.get("owner"), view.get("generation")

    def _enqueue(self, conversation, key, work):
        owner = self._owner(conversation)
        if owner is None or self._stop.is_set():
            return
        key = (conversation, key)
        with self._lock:
            if key in self._pending:
                return
            self._pending.add(key)
        def run():
            try:
                if not self._stop.is_set() and self._owner(conversation) == owner:
                    work()
            finally:
                with self._lock:
                    self._pending.discard(key)
        try:
            accepted = self._xiaoyu.defer(conversation, run)
        except Exception:
            with self._lock:
                self._pending.discard(key)
            raise
        if accepted is False:
            with self._lock:
                self._pending.discard(key)

    def _progress_pending(self, conversation, run_id, event_id):
        with self._lock:
            return (conversation, ("watch", run_id, event_id)) in self._pending

    def _checkpoint_watch(self, card, before, after):
        return self._progress.checkpoint(card, before, after)

    @staticmethod
    def _watch_state(watch, state, **fields):
        return ProgressWakes.state(watch, state, **fields)

    def _due_watches(self, now):
        return self._progress.due(now)

    # ---- 叫醒她 ----

    def _voice(self) -> Any:
        voice = self._xiaoyu.voice_agent()
        return voice if voice is not None and voice.talks_with_tools() else None

    def _recent_said(self, conversation) -> str:
        turns = self._xiaoyu.context.turns(conversation)[-8:]
        return "\n".join("- %s" % turn["reply"][:200].replace("\n", " ") for turn in turns if turn.get("reply"))

    def _ask(self, event: str, only: List[str], rules: str, conversation=None, before_effect=None,
             owner=None, effects=None, expected_memory_revision=None) -> Optional[str]:
        voice = self._voice()
        if voice is None:
            return None
        xiaoyu = self._xiaoyu
        conversation = conversation or self._conversation
        owner = self._owner(conversation) if owner is None else owner
        if owner is None or self._stop.is_set() or self._owner(conversation) != owner:
            return None
        budget = Deadline(xiaoyu.config.voice_timeout_seconds)
        cut, active, effects_open = None, True, True
        def guard():
            if (not active or self._stop.is_set() or self._owner(conversation) != owner
                    or cut is not None and cut.is_set()):
                raise EffectFenceError("后台唤醒已过期，停止旧轮次的工具、会话和回复写入。")
        def before_mutation():
            guard()
            if not effects_open:
                raise EffectFenceError("决定阶段已结束，不能再执行旧工具回调。")
            if before_effect is not None:
                before_effect()
        memory_notes, memory_revision = xiaoyu.memory.snapshot()
        if expected_memory_revision is not None and memory_revision != expected_memory_revision:
            return None
        from ..world.memory import prompt_section
        system = "\n\n".join(part for part in (
            with_runtime_rules(xiaoyu.config.persona), xiaoyu.style(), "\n".join(prompt_section(memory_notes)),
            "\n".join(xiaoyu.promise_lines(conversation)), rules) if part)
        box = Toolbox([], [], [], lambda fields: "这一轮不能交办新的事", lambda card: xiaoyu.detail(card, conversation),
                      xiaoyu.memory, lambda query: xiaoyu.recall(conversation, query), xiaoyu.promises,
                      conversation, only=only, before_effect=before_mutation, guard=guard, effects=effects,
                      expected_memory_revision=memory_revision if rules == UPKEEP_RULES else None)
        text = "%s\n\n（你最近跟主人说过的话：）\n%s" % (event, self._recent_said(conversation) or "（还没有）")
        messages = None
        try:
            with xiaoyu.speaking_turn(conversation) as cut:
                if xiaoyu.config.decide_first and voice.talks_with_tools():
                    decision = xiaoyu._speak(voice, text, conversation, system, None, 0, catch_up=True,
                                              tools=box, decision_only=True, interrupt=cut,
                                              ephemeral=rules == UPKEEP_RULES, guard=guard, budget=budget)
                    effects_open = False
                    data = (decision.fields or {}).get("_decision", {})
                    system += "\n\n这一轮已落实的事实：" + data.get("facts", "没有执行任何承诺。")
                    system += "\n现在只说话或查记录，不能新增任务、记忆或约定。"
                    messages = data.get("messages", [])
                    box = box.speaking()
                if cut.is_set() or self._owner(conversation) != owner or self._stop.is_set():
                    return None
                outcome = xiaoyu._speak(voice, text, conversation, system, None, 0, catch_up=True,
                    on_text=lambda raw: None, tools=box, decision_messages=messages, interrupt=cut,
                    ephemeral=rules == UPKEEP_RULES, guard=guard, budget=budget)
                if cut.is_set() or self._owner(conversation) != owner or self._stop.is_set():
                    return None
        except (AgentError, OSError, RuntimeError) as error:
            sys.stderr.write("后台叫醒小鱼没成：%s\n" % error)
            return None
        finally:
            active = False
        return (outcome.text or "").strip()

    def _say(self, card_id: str, text: str, turn_id: str, event: str, *, active_run=None, owner=None) -> bool:
        """Save one note in its original conversation; context reads that message."""
        if not text or SILENT.match(text) or text.startswith(("（不说", "(不说")):
            return False
        xiaoyu = self._xiaoyu
        card = xiaoyu.cards.get(card_id)
        if card is None:
            return False
        owner = self._owner(card["conversation"]) if owner is None else owner
        if owner is None or self._owner(card["conversation"]) != owner or self._stop.is_set():
            return False
        said = xiaoyu.chat_text(text)
        if self._owner(card["conversation"]) != owner or self._stop.is_set():
            return False
        saved = xiaoyu.intake.writer("timer", actor="xiaoyou", cause=turn_id).reply(
            card_id, say=said, turn_id=turn_id, scope="main", kind="note", expected_active_run=active_run)
        if saved is None or self._owner(card["conversation"]) != owner or self._stop.is_set():
            return False
        xiaoyu.context.delivered(card["conversation"], turn_id, [xiaoyu.voice_name()], xiaoyu.voice_name())
        return True

    def _wake_for_task(self, run_id, entry):
        return self._progress.run(run_id, entry)

    def _wake_for_promise(self, item):
        return self._reminders.run(item)

    def _reminder_card(self, conversation, title, event_id, saved_id, state="done"):
        return self._reminders.card(conversation, title, event_id, saved_id, state)

    # ---- 整理记忆 ----

    def _maybe_upkeep(self, now):
        if self._memory_upkeep_lock.acquire(blocking=False):
            try:
                self._upkeep.maybe(now)
            finally:
                self._memory_upkeep_lock.release()
        else:
            self._upkeep_next = 'default'
        self._maybe_summary(now)

    def _maybe_weixin_upkeep(self, now):
        if self._memory_upkeep_lock.acquire(blocking=False):
            try:
                self._weixin_upkeep.maybe(now)
            finally:
                self._memory_upkeep_lock.release()
        else:
            self._upkeep_next = 'weixin'

    def _maybe_summary(self, now):
        """One optional, interruptible read-only summary on the existing queue."""
        xiaoyu, conversation = self._xiaoyu, self._conversation
        owner, voice = self._owner(conversation), self._voice()
        if owner is None or voice is None or self._stop.is_set() or self.unconfirmed(conversation):
            return
        activity = xiaoyu.context.summary_activity(conversation)
        memory_notes, memory_revision = xiaoyu.memory.snapshot()
        if activity[2] or now - activity[1] < UPKEEP_QUIET:
            return
        turns = xiaoyu.context.history(conversation)
        if not turns or now - float(turns[-1].get("at") or now) < UPKEEP_QUIET:
            return
        candidate = xiaoyu.context.summary_candidate(conversation, now=now)
        if candidate is None:
            return
        with xiaoyu.speaking_turn(conversation) as cut:
            def guard():
                if (self._stop.is_set() or cut.is_set() or self._owner(conversation) != owner
                        or xiaoyu.context.summary_activity(conversation) != activity
                        or xiaoyu.memory.revision() != memory_revision):
                    raise EffectFenceError("历史摘要已过期，停止保存。")
            try:
                guard()
                if not xiaoyu.context.begin_summary(conversation, candidate, now=now):
                    return
                budget = Deadline(min(10, xiaoyu.config.voice_timeout_seconds))
                source = json.dumps({'current_memory': memory_notes,
                                     'historical_turns': json.loads(SegmentSummaries.source(candidate))}, ensure_ascii=False)
                outcome = xiaoyu._speak(voice, source, conversation,
                    SUMMARY_RULES, None, 0, False, ephemeral=True, interrupt=cut,
                    on_text=lambda text: None, guard=guard, budget=budget)
                guard()
                xiaoyu.context.save_summary(conversation, candidate, outcome.text)
            except (AgentError, OSError, RuntimeError):
                # No external effect, message or receipt exists to reconcile.
                # The durable attempt limit/cooldown bounds background retries.
                sys.stderr.write("历史摘要未保存；继续保留原文及检索。\n")

    def _maybe_relationship(self, conversation, now):
        """Quiet, scoped, interruptible synthesis; never sends a message."""
        xiaoyu, owner, voice = self._xiaoyu, self._owner(conversation), self._voice()
        if owner is None or voice is None or self._stop.is_set() or self.unconfirmed(conversation):
            return
        activity = xiaoyu.context.summary_activity(conversation)
        if activity[2] or now - activity[1] < UPKEEP_QUIET:
            return
        turns = xiaoyu.context.history(conversation)
        notes, revision = xiaoyu.memory.snapshot()
        candidate = xiaoyu.relationship.candidate(conversation, turns, revision, now, memory_notes=notes,
                                                  correction_after=xiaoyu.memory.correction_after())
        if candidate is None:
            return
        with xiaoyu.speaking_turn(conversation) as cut:
            def guard():
                if (self._stop.is_set() or cut.is_set() or self._owner(conversation) != owner
                        or xiaoyu.context.summary_activity(conversation) != activity
                        or xiaoyu.memory.revision() != revision):
                    raise EffectFenceError('关系摘要的来源已变化')
            try:
                guard()
                xiaoyu.relationship.begin(conversation, candidate, now)
                outcome = xiaoyu._speak(voice, json.dumps(candidate['source'], ensure_ascii=False), conversation,
                    RELATIONSHIP_RULES, None, 0, False, ephemeral=True, interrupt=cut,
                    on_text=lambda text: None, guard=guard, budget=Deadline(min(10, xiaoyu.config.voice_timeout_seconds)))
                guard()
                xiaoyu.relationship.save(conversation, candidate, outcome.text, now)
            except (AgentError, OSError, RuntimeError):
                sys.stderr.write('关系摘要未保存；原始对话和记忆不受影响。\n')


    def _checkpoint_upkeep(self, target, before, after):
        return self._upkeep.checkpoint(target, before, after)

    def _save_marker(self, turn, *, expected_wake=None):
        return self._upkeep.save_marker(turn, expected_wake=expected_wake)
