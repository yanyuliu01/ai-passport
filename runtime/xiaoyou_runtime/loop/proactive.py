"""投递闸和后台整理：小幽在主人没开口的时候，什么时候被叫醒、醒了说不说由她定。

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

import json
import hashlib
import re
import sys
import threading
import time
from copy import deepcopy
from typing import Any, Dict, List, Optional

from ..helpers.agents import AgentError
from .effects import EffectFenceError, EffectUncertain, SavedWake
from ..world.promises import wake_source
from .voice_tools import Toolbox
from ..world.world import durable_write
from .progress_wakes import ProgressPorts, ProgressWakes

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
    "验证码绝不记。整理完只回一行：（好了）。"
)


class Pulse:
    """小幽的后台节拍：投递闸 + 记忆整理。xiaoyou 是 Xiaoyou 本身（用它的代理、记录和记忆）。"""

    def __init__(self, xiaoyou: Any, conversation: str = "default"):
        self._xiaoyou = xiaoyou
        self._conversation = conversation
        self._pending = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._progress = ProgressWakes(
            lambda: self._xiaoyou.cards,
            lambda cause: self._xiaoyou.intake.writer("timer", actor="xiaoyou", cause=cause),
            ProgressPorts(
                owner=lambda conversation: self._owner(conversation),
                stopped=self._stop.is_set,
                pending=self._progress_pending,
                available=lambda: self._voice() is not None,
                policy=lambda: (WATCH_AFTER, WATCH_EVERY, WATCH_MAX),
                ask=lambda event, conversation, owner: self._ask(
                    event, ["task_detail", "recall"], GATE_RULES, conversation, owner=owner),
                say=lambda *args, **kwargs: self._say(*args, **kwargs),
                text=lambda text: self._xiaoyou.chat_text(text),
                silent=lambda text: not text or SILENT.match(text)
                    or text.startswith(("（不说", "(不说"))))
        # 整理到哪一轮（编号），存在记忆旁边：重启后不把整段历史再整理一遍。
        self._marker = xiaoyou.memory.log_dir.parent / "upkeep.json"
        self._marker_lock = threading.RLock()
        self._marker_state = {}
        self._upkept: Optional[str] = self._load_marker()
        self._thread = threading.Thread(target=self._run, name="xiaoyou-pulse", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()

    def unconfirmed(self, conversation):
        return any((item.get("conversation") or self._conversation) == conversation
                   for item in self._xiaoyou.promises.wakes()) or (
            conversation == self._conversation and (
                self._marker_state is None or bool(self._marker_state.get("wake"))))

    def timer_entries(self, include_history=True):
        entries = []
        for item in self._xiaoyou.promises.effect_records():
            records = [(item.get("wake"), False)]
            if include_history and isinstance(item.get("wake_reviews"), list):
                records += [(wake, True) for wake in item["wake_reviews"]]
            for wake, archived in records:
                if wake:
                    entries.append({"kind": "promise", "source_id": item["id"],
                        "conversation": item.get("conversation") or self._conversation,
                        "source": item, "wake": wake, "archived": archived})
        with self._marker_lock:
            state = deepcopy(self._marker_state)
        if state is None:
            entries.append({"kind": "upkeep", "source_id": self._conversation,
                "conversation": self._conversation, "wake": None, "archived": False})
        elif state:
            records = [(state.get("wake"), False)]
            if include_history and isinstance(state.get("reviews"), list):
                records += [(wake, True) for wake in state["reviews"]]
            for wake, archived in records:
                if wake:
                    entries.append({"kind": "upkeep", "source_id": self._conversation,
                        "conversation": self._conversation, "target": state["turn"],
                        "wake": wake, "archived": archived})
        return entries

    def checkpoint_timer(self, entry, wake):
        if entry["archived"]:
            raise EffectUncertain("核对归档不能修改。")
        if entry["kind"] == "promise":
            self._xiaoyou.promises.checkpoint_wake(entry["source"], entry["wake"], wake)
        else:
            self._checkpoint_upkeep(entry["target"], entry["wake"], wake)

    def archive_timer_review(self, entry):
        wake = entry["wake"]
        if entry["kind"] == "promise":
            self._xiaoyou.promises.archive_wake_review(entry["source"], wake)
            return
        with self._marker_lock:
            current = json.loads(self._marker.read_text(encoding="utf-8"))
            if (current != self._marker_state or current.get("wake") != wake
                    or (wake.get("review") or {}).get("published") is not True):
                raise EffectUncertain("整理的核对投递进度已经改变。")
            history = current.setdefault("reviews", [])
            if not isinstance(history, list) or any(value.get("id") == wake["id"] for value in history):
                raise EffectUncertain("整理的核对归档有冲突。")
            history.append(deepcopy(wake))
            current.pop("wake")
            self._write_marker(current)

    # ---- 帮手报进展（在任务线程里调用）：只记下，不在这里叫她 ----

    def progress(self, card: str, run_id: str, agent: str, line: str) -> None:
        self._xiaoyou.intake.writer("helper", actor=agent, cause=run_id).progress(card, line, run_id=run_id)

    # ---- 后台线程 ----

    def _run(self) -> None:
        while not self._stop.wait(TICK):
            try:
                self.tick()
            except Exception as error:  # 后台节拍出错不能拖垮 Runtime：记下来，下一拍再看
                sys.stderr.write("后台节拍出错：%r\n" % (error,))

    def tick(self, now: Optional[float] = None) -> None:
        """看一眼：该叫她的叫她。测试里可以直接调。"""
        self._xiaoyou.retry_effects()
        wall = time.time() if now is None else now
        for run_id, entry in self._due_watches(wall):
            card = self._xiaoyou.cards.get(entry["card"])
            if card is not None:
                self._enqueue(card["conversation"], ("watch", run_id, entry["watch"]["event"]["id"]),
                              lambda rid=run_id, item=entry: self._wake_for_task(rid, item))
        reminders = {item["id"]: item for item in self._xiaoyou.promises.due(wall)}
        reminders.update({item["id"]: item for item in self._xiaoyou.promises.wakes()})
        for item in reminders.values():
            self._enqueue(item.get("conversation") or self._conversation, ("promise", item["id"]),
                          lambda item=item: self._wake_for_promise(item))
        self._enqueue(self._conversation, ("upkeep", self._conversation),
                      lambda: self._maybe_upkeep(time.time() if now is None else now))

    def _owner(self, conversation):
        mesh = self._xiaoyou.mesh
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
            accepted = self._xiaoyou.defer(conversation, run)
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
        voice = self._xiaoyou.voice_agent()
        return voice if voice is not None and voice.talks_with_tools() else None

    def _recent_said(self, conversation) -> str:
        turns = self._xiaoyou.context.turns(conversation)[-8:]
        return "\n".join("- %s" % turn["reply"][:200].replace("\n", " ") for turn in turns if turn.get("reply"))

    def _ask(self, event: str, only: List[str], rules: str, conversation=None, before_effect=None,
             owner=None, effects=None) -> Optional[str]:
        voice = self._voice()
        if voice is None:
            return None
        xiaoyou = self._xiaoyou
        conversation = conversation or self._conversation
        owner = self._owner(conversation) if owner is None else owner
        if owner is None or self._stop.is_set() or self._owner(conversation) != owner:
            return None
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
        system = "\n\n".join(part for part in (
            xiaoyou.config.persona, xiaoyou.style(), "\n".join(xiaoyou.memory_lines()),
            "\n".join(xiaoyou.promise_lines(conversation)), rules) if part)
        box = Toolbox([], [], [], lambda fields: "这一轮不能交办新的事", lambda card: xiaoyou.detail(card, conversation),
                      xiaoyou.memory, lambda query: xiaoyou.recall(conversation, query), xiaoyou.promises,
                      conversation, only=only, before_effect=before_mutation, guard=guard, effects=effects)
        text = "%s\n\n（你最近跟主人说过的话：）\n%s" % (event, self._recent_said(conversation) or "（还没有）")
        messages = None
        began = time.monotonic()
        try:
            with xiaoyou.speaking_turn(conversation) as cut:
                if xiaoyou.config.decide_first and voice.talks_with_tools():
                    decision = xiaoyou._speak(voice, text, conversation, system, None, 0, catch_up=True,
                                              tools=box, decision_only=True, interrupt=cut,
                                              ephemeral=rules == UPKEEP_RULES, guard=guard)
                    effects_open = False
                    data = (decision.fields or {}).get("_decision", {})
                    system += "\n\n这一轮已落实的事实：" + data.get("facts", "没有执行任何承诺。")
                    system += "\n现在只说话或查记录，不能新增任务、记忆或约定。"
                    messages = data.get("messages", [])
                    box = box.speaking()
                if cut.is_set() or self._owner(conversation) != owner or self._stop.is_set():
                    return None
                outcome = xiaoyou._speak(voice, text, conversation, system, None, 0, catch_up=True,
                    timeout_seconds=max(0.001, xiaoyou.config.voice_timeout_seconds - (time.monotonic() - began)),
                    on_text=lambda raw: None, tools=box, decision_messages=messages, interrupt=cut,
                    ephemeral=rules == UPKEEP_RULES, guard=guard)
                if cut.is_set() or self._owner(conversation) != owner or self._stop.is_set():
                    return None
        except (AgentError, OSError, RuntimeError) as error:
            sys.stderr.write("后台叫醒小幽没成：%s\n" % error)
            return None
        finally:
            active = False
        return (outcome.text or "").strip()

    def _say(self, card_id: str, text: str, turn_id: str, event: str, *, active_run=None, owner=None) -> bool:
        """Save one note in its original conversation; context reads that message."""
        if not text or SILENT.match(text) or text.startswith(("（不说", "(不说")):
            return False
        xiaoyou = self._xiaoyou
        card = xiaoyou.cards.get(card_id)
        if card is None:
            return False
        owner = self._owner(card["conversation"]) if owner is None else owner
        if owner is None or self._owner(card["conversation"]) != owner or self._stop.is_set():
            return False
        said = xiaoyou.chat_text(text)
        if self._owner(card["conversation"]) != owner or self._stop.is_set():
            return False
        saved = xiaoyou.intake.writer("timer", actor="xiaoyou", cause=turn_id).reply(
            card_id, say=said, turn_id=turn_id, scope="main", kind="note", expected_active_run=active_run)
        if saved is None or self._owner(card["conversation"]) != owner or self._stop.is_set():
            return False
        xiaoyou.context.delivered(card["conversation"], turn_id, [xiaoyou.voice_name()], xiaoyou.voice_name())
        return True

    def _wake_for_task(self, run_id, entry):
        return self._progress.run(run_id, entry)

    def _wake_for_promise(self, item: Dict[str, Any]) -> None:
        promises = self._xiaoyou.promises
        current = next((value for value in promises.open() + promises.wakes() if value["id"] == item["id"]), None)
        if current is None or wake_source(current) != wake_source(item):
            return
        conversation = item.get("conversation") or self._conversation
        owner = self._owner(conversation)
        if owner is None or self._stop.is_set():
            return
        saved = current.get("wake")
        if isinstance(saved, dict) and "review" in saved:
            return  # A saved human review only resumes publication, never execution.
        version = hashlib.sha256(json.dumps([item.get("due"), item.get("nudged")]).encode()).hexdigest()[:16]
        turn_id = "%s-due-%s" % (item["id"], version)
        final = self._xiaoyou.cards.message(turn_id + ":reply")
        if final and final.get("status") == "final" and final.get("conversation") == conversation:
            if not self._stop.is_set() and self._owner(conversation) == owner:
                promises.mark_nudged(item["id"], expected_wake=current.get("wake"))
            return
        if saved and (not isinstance(saved, dict) or saved.get("version") != 1):
            return  # A legacy claim has no selected inputs/results to resume.
        def guard():
            if self._stop.is_set() or self._owner(conversation) != owner:
                raise EffectFenceError("Reminder owner changed")
        try:
            wake = SavedWake(turn_id, owner, wake_source(current), saved,
                             lambda before, after: promises.checkpoint_wake(current, before, after), guard)
            if any(effect["name"] not in ("track", "resolve") for effect in wake.effects.records):
                raise EffectUncertain("提醒包含无法恢复的操作。")
            if "reply" not in wake.state:
                if saved:
                    box = Toolbox([], [], [], lambda fields: "不能交办", lambda card: None,
                                  promises=promises, conversation=conversation, only=["track", "resolve"], guard=guard)
                    text = wake.resume(box)
                else:
                    event = "你答应主人的事到点了：%s %s%s" % (item["id"], item["title"],
                                                 "（%s）" % item["detail"] if item.get("detail") else "")
                    text = self._ask(event, ["recall", "resolve", "track"], GATE_RULES, conversation,
                                     owner=owner, effects=wake.effects)
                if text is None:
                    return
                wake.finish(text)
            guard()
            text = wake.state["reply"]
            if text and not SILENT.match(text) and not text.startswith(("（不说", "(不说")):
                card = self._reminder_card(conversation, item["title"], turn_id, wake.state.get("card"))
                if wake.state.get("card") != card["id"]:
                    wake.save(card=card["id"])
                if not self._say(card["id"], text, turn_id, "答应的事到点了", owner=owner):
                    return
            guard()
            promises.mark_nudged(item["id"], expected_wake=wake.state)
        except EffectUncertain as error:
            sys.stderr.write("定时操作待核对：%s\n" % error)
            return

    def _reminder_card(self, conversation, title, event_id, saved_id, state="done"):
        cards = self._xiaoyou.cards
        if saved_id:
            card = cards.get(saved_id)
            if not card or card["conversation"] != conversation or card.get("mesh_origin"):
                raise EffectUncertain("提醒的原投递位置不可用。")
            return card
        found = [card for card in cards.recent(conversation, 10 ** 6)
                 if (card.get("changed_by") or {}).get("cause") == event_id and not card.get("mesh_origin")]
        if len(found) > 1:
            raise EffectUncertain("提醒的投递位置有歧义。")
        return found[0] if found else self._xiaoyou.intake.writer(
            "timer", actor="xiaoyou", cause=event_id).task_open(conversation, title, state=state)

    # ---- 整理记忆 ----

    def _maybe_upkeep(self, now: float) -> None:
        owner = self._owner(self._conversation)
        if owner is None or self._stop.is_set() or self._marker_state is None:
            return
        saved = self._marker_state.get("wake")
        if saved:
            if isinstance(saved, dict) and "review" in saved:
                return
            self._upkeep_effects(owner, self._upkept, saved.get("source") if isinstance(saved, dict) else None, saved)
            return
        turns = self._xiaoyou.context.turns(self._conversation)
        if not turns or now - float(turns[-1].get("at") or now) < UPKEEP_QUIET:
            return
        if self._upkept is None:
            # 第一次：从现在开始算，不把以前所有的对话翻一遍。
            if not self._stop.is_set() and self._owner(self._conversation) == owner:
                self._save_marker(turns[-1]["id"])
            return
        start = 0
        if self._upkept is not None:
            ids = [turn["id"] for turn in turns]
            start = ids.index(self._upkept) + 1 if self._upkept in ids else 0
        fresh = turns[start:][-UPKEEP_MAX_TURNS:]
        if len(fresh) < UPKEEP_MIN_TURNS:
            return
        lines = "\n\n".join("主人：%s\n小幽：%s" % (turn["text"][:400], turn["reply"][:400]) for turn in fresh)
        source = [[turn["_key"], turn["_version"]] for turn in fresh]
        self._upkeep_effects(owner, turns[-1]["id"], source, None, lines)

    def _upkeep_effects(self, owner, target, source, saved, lines=None):
        def guard():
            if self._stop.is_set() or self._owner(self._conversation) != owner:
                raise EffectFenceError("Upkeep owner changed")
            history = self._xiaoyou.context.history(self._conversation)
            current = {turn["_key"]: turn["_version"] for turn in history}
            if (not isinstance(source, list) or not source or any(
                    not isinstance(ref, list) or len(ref) != 2 or not all(isinstance(part, str) for part in ref)
                    or current.get(ref[0]) != ref[1] for ref in source)):
                raise EffectUncertain("整理窗口的原记录已改变，需先核对。")
            if not any(turn["id"] == target and turn["_key"] == source[-1][0] for turn in history):
                raise EffectUncertain("整理窗口的结束位置无法核对。")
        try:
            event_id = "upkeep-" + hashlib.sha256(json.dumps([self._conversation, source]).encode()).hexdigest()[:32]
            wake = SavedWake(event_id, owner, source, saved,
                             lambda before, after: self._checkpoint_upkeep(target, before, after), guard)
            if any(effect["name"] not in ("remember", "replace", "forget") for effect in wake.effects.records):
                raise EffectUncertain("整理包含无法恢复的操作。")
            if "reply" not in wake.state:
                if saved:
                    box = Toolbox([], [], [], lambda fields: "不能交办", lambda card: None,
                                  memory=self._xiaoyou.memory, only=["remember", "replace", "forget"], guard=guard)
                    result = wake.resume(box)
                else:
                    result = self._ask("（最近这几轮对话：）\n" + lines, ["remember", "replace", "forget"],
                                       UPKEEP_RULES, owner=owner, effects=wake.effects)
                if result is None:
                    return
                wake.finish(result)
            guard()
            self._save_marker(target, expected_wake=wake.state)
        except EffectUncertain as error:
            sys.stderr.write("记忆整理待核对：%s\n" % error)

    def _load_marker(self) -> Optional[str]:
        try:
            state = json.loads(self._marker.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or not isinstance(state.get("turn"), str):
                raise ValueError("Invalid upkeep marker")
            self._marker_state = state
            return state["turn"]
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            self._marker_state = None  # Never reset a damaged claim into a new model call.
            return None

    def _write_marker(self, state):
        try:
            durable_write(self._marker, state)
        except OSError:
            if json.loads(self._marker.read_text(encoding="utf-8")) != state:
                raise
        self._marker_state = deepcopy(state)
        self._upkept = state["turn"]

    def _checkpoint_upkeep(self, target, before, after):
        with self._marker_lock:
            current = json.loads(self._marker.read_text(encoding="utf-8"))
            if current != self._marker_state or current.get("wake") != before:
                raise EffectUncertain("整理窗口的领取已改变。")
            # The old binary skips this window even if a new effect is pending.
            self._write_marker(dict(current, turn=target, wake=after))

    def _save_marker(self, turn, *, expected_wake=None) -> None:
        with self._marker_lock:
            if self._marker_state is None or self._marker_state.get("wake") != expected_wake:
                raise EffectUncertain("不能覆盖未确认的整理操作。")
            if self._marker.exists() and json.loads(self._marker.read_text(encoding="utf-8")) != self._marker_state:
                raise EffectUncertain("整理进度已经改变。")
            state = dict(self._marker_state, turn=turn)
            state.pop("wake", None)
            self._write_marker(state)
