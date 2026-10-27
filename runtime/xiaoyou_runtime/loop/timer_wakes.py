"""Durable reminder and memory-upkeep lifecycles behind explicit wake ports.

The existing promise records and upkeep marker retain claims, effects and human
reviews. Scheduling and model/publication adapters remain outside these rules.
"""

import hashlib
import json
import sys
import threading
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from .effects import EffectFenceError, EffectUncertain, SavedWake
from .voice_tools import Toolbox
from ..world.promises import wake_source


@dataclass(frozen=True)
class ReminderPorts:
    owner: Callable
    stopped: Callable
    ask: Callable
    say: Callable
    silent: Callable


@dataclass(frozen=True)
class UpkeepPorts:
    owner: Callable
    stopped: Callable
    ask: Callable
    policy: Callable
    write: Callable


class ReminderWakes:
    def __init__(self, promises, cards, writer, ports: ReminderPorts, conversation="default"):
        self._promises, self._cards, self._writer = promises, cards, writer
        self._ports, self._conversation = ports, conversation

    def due(self, now):
        reminders = {item["id"]: item for item in self._promises.due(now)}
        reminders.update({item["id"]: item for item in self._promises.wakes()})
        return list(reminders.values())

    def unconfirmed(self, conversation):
        return any((item.get("conversation") or self._conversation) == conversation
                   for item in self._promises.wakes())

    def entries(self, include_history=True):
        entries = []
        for item in self._promises.effect_records():
            records = [(item.get("wake"), False)]
            if include_history and isinstance(item.get("wake_reviews"), list):
                records += [(wake, True) for wake in item["wake_reviews"]]
            for wake, archived in records:
                if wake:
                    entries.append({"kind": "promise", "source_id": item["id"],
                        "conversation": item.get("conversation") or self._conversation,
                        "source": item, "wake": wake, "archived": archived})
        return entries

    def checkpoint(self, entry, wake):
        self._promises.checkpoint_wake(entry["source"], entry["wake"], wake)

    def archive_review(self, entry):
        self._promises.archive_wake_review(entry["source"], entry["wake"])

    def run(self, item: Dict[str, Any]) -> None:
        promises = self._promises
        current = next((value for value in promises.open() + promises.wakes() if value["id"] == item["id"]), None)
        if current is None or wake_source(current) != wake_source(item):
            return
        conversation = item.get("conversation") or self._conversation
        owner = self._ports.owner(conversation)
        if owner is None or self._ports.stopped():
            return
        saved = current.get("wake")
        if isinstance(saved, dict) and "review" in saved:
            return  # A saved human review only resumes publication, never execution.
        version = hashlib.sha256(json.dumps([item.get("due"), item.get("nudged")]).encode()).hexdigest()[:16]
        turn_id = "%s-due-%s" % (item["id"], version)
        final = self._cards().message(turn_id + ":reply")
        if final and final.get("status") == "final" and final.get("conversation") == conversation:
            if not self._ports.stopped() and self._ports.owner(conversation) == owner:
                promises.mark_nudged(item["id"], expected_wake=current.get("wake"))
            return
        if saved and (not isinstance(saved, dict) or saved.get("version") != 1):
            return  # A legacy claim has no selected inputs/results to resume.
        def guard():
            if self._ports.stopped() or self._ports.owner(conversation) != owner:
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
                    text = self._ports.ask(event, conversation, owner, wake.effects)
                if text is None:
                    return
                wake.finish(text)
            guard()
            text = wake.state["reply"]
            if not self._ports.silent(text):
                card = self.card(conversation, item["title"], turn_id, wake.state.get("card"))
                if wake.state.get("card") != card["id"]:
                    wake.save(card=card["id"])
                if not self._ports.say(card["id"], text, turn_id, "答应的事到点了", owner=owner):
                    return
            guard()
            promises.mark_nudged(item["id"], expected_wake=wake.state)
        except EffectUncertain as error:
            sys.stderr.write("定时操作待核对：%s\n" % error)
            return

    def card(self, conversation, title, event_id, saved_id, state="done"):
        cards = self._cards()
        if saved_id:
            card = cards.get(saved_id)
            if not card or card["conversation"] != conversation or card.get("mesh_origin"):
                raise EffectUncertain("提醒的原投递位置不可用。")
            return card
        found = [card for card in cards.recent(conversation, 10 ** 6)
                 if (card.get("changed_by") or {}).get("cause") == event_id and not card.get("mesh_origin")]
        if len(found) > 1:
            raise EffectUncertain("提醒的投递位置有歧义。")
        return found[0] if found else self._writer(event_id).task_open(conversation, title, state=state)


class MemoryUpkeep:
    def __init__(self, context, memory, marker, ports: UpkeepPorts, conversation="default"):
        self._context, self._memory, self._marker = context, memory, marker
        self._ports, self._conversation = ports, conversation
        self._marker_lock = threading.RLock()
        self._marker_state = {}
        self._upkept: Optional[str] = self._load_marker()

    def unconfirmed(self, conversation):
        return conversation == self._conversation and (
            self._marker_state is None or bool(self._marker_state.get("wake")))

    def entries(self, include_history=True):
        entries = []
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

    def archive_review(self, entry):
        wake = entry["wake"]
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

    def maybe(self, now: float) -> None:
        quiet, minimum, maximum = self._ports.policy()
        owner = self._ports.owner(self._conversation)
        if owner is None or self._ports.stopped() or self._marker_state is None:
            return
        saved = self._marker_state.get("wake")
        if saved:
            if isinstance(saved, dict) and "review" in saved:
                return
            self.apply(owner, self._upkept, saved.get("source") if isinstance(saved, dict) else None, saved)
            return
        turns = self._context().turns(self._conversation)
        if not turns or now - float(turns[-1].get("at") or now) < quiet:
            return
        if self._upkept is None:
            # 第一次：从现在开始算，不把以前所有的对话翻一遍。
            if not self._ports.stopped() and self._ports.owner(self._conversation) == owner:
                self.save_marker(turns[-1]["id"])
            return
        start = 0
        if self._upkept is not None:
            ids = [turn["id"] for turn in turns]
            start = ids.index(self._upkept) + 1 if self._upkept in ids else 0
        fresh = turns[start:][-maximum:]
        if len(fresh) < minimum:
            return
        lines = "\n\n".join("主人：%s\n小幽：%s" % (turn["text"][:400], turn["reply"][:400]) for turn in fresh)
        source = [[turn["_key"], turn["_version"]] for turn in fresh]
        self.apply(owner, turns[-1]["id"], source, None, lines)

    def apply(self, owner, target, source, saved, lines=None):
        def guard():
            if self._ports.stopped() or self._ports.owner(self._conversation) != owner:
                raise EffectFenceError("Upkeep owner changed")
            history = self._context().history(self._conversation)
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
                             lambda before, after: self.checkpoint(target, before, after), guard)
            if any(effect["name"] not in ("remember", "replace", "forget") for effect in wake.effects.records):
                raise EffectUncertain("整理包含无法恢复的操作。")
            if "reply" not in wake.state:
                if saved:
                    box = Toolbox([], [], [], lambda fields: "不能交办", lambda card: None,
                                  memory=self._memory(), only=["remember", "replace", "forget"], guard=guard)
                    result = wake.resume(box)
                else:
                    result = self._ports.ask("（最近这几轮对话：）\n" + lines, owner, wake.effects)
                if result is None:
                    return
                wake.finish(result)
            guard()
            self.save_marker(target, expected_wake=wake.state)
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
            self._ports.write(self._marker, state)
        except OSError:
            if json.loads(self._marker.read_text(encoding="utf-8")) != state:
                raise
        self._marker_state = deepcopy(state)
        self._upkept = state["turn"]

    def checkpoint(self, target, before, after):
        with self._marker_lock:
            current = json.loads(self._marker.read_text(encoding="utf-8"))
            if current != self._marker_state or current.get("wake") != before:
                raise EffectUncertain("整理窗口的领取已改变。")
            # The old binary skips this window even if a new effect is pending.
            self._write_marker(dict(current, turn=target, wake=after))

    def save_marker(self, turn, *, expected_wake=None) -> None:
        with self._marker_lock:
            if self._marker_state is None or self._marker_state.get("wake") != expected_wake:
                raise EffectUncertain("不能覆盖未确认的整理操作。")
            if self._marker.exists() and json.loads(self._marker.read_text(encoding="utf-8")) != self._marker_state:
                raise EffectUncertain("整理进度已经改变。")
            state = dict(self._marker_state, turn=turn)
            state.pop("wake", None)
            self._write_marker(state)
