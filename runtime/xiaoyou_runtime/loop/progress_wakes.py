"""Durable progress-wake rules, independent of the clock and model controller.

The task journal owns admission, claims and saved replies. The shared conversation
lane owns scheduling; these rules neither add a queue nor replay an external effect.
"""

import hashlib
import json
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from ..events.operations import valid_watch


@dataclass(frozen=True)
class ProgressPorts:
    owner: Callable
    stopped: Callable
    pending: Callable
    available: Callable
    policy: Callable
    ask: Callable
    say: Callable
    text: Callable
    silent: Callable


class ProgressWakes:
    def __init__(self, cards, writer, ports: ProgressPorts):
        # Late-bound access preserves startup order and live controller bindings.
        self._cards, self._writer, self._ports = cards, writer, ports

    def checkpoint(self, card, before, after):
        event_id = after["event"]["id"]
        return self._writer(event_id).progress_checkpoint(
            card["id"], card["run_id"], before, after) is not None

    @staticmethod
    def state(watch, state, **fields):
        result = deepcopy(watch)
        result["event"].update(state=state, **fields)
        if state in ("done", "unknown"):
            result["event"].pop("reply", None)
        return result

    def reply(self, card, watch):
        message = self._cards().message(watch["event"]["id"] + ":reply")
        return message if (message and message.get("status") == "final" and message.get("task") == card["id"]
                and message.get("conversation") == card["conversation"] and message.get("kind") == "note") else None

    def due(self, now: float) -> List[Any]:
        after, every, maximum = self._ports.policy()
        due = []
        for card, watch in self._cards().watches():
            run_id, owner = card["run_id"], self._ports.owner(card["conversation"])
            if owner is None or self._ports.stopped():
                continue
            if watch is not None:
                if not valid_watch(watch) or watch["run_id"] != run_id:
                    continue  # Preserve malformed claims rather than reset their counters.
                event = watch["event"]
                if event["owner"] != list(owner):
                    continue
                active = self._ports.pending(card["conversation"], run_id, event["id"])
                if active:
                    continue
                final = self.reply(card, watch)
                if event["state"] != "done" and final:
                    if not any(item.get("id") == final["id"] for item in card["entries"]):
                        if not self._ports.say(card["id"], final["text"], event["id"], "后台的事有新进展",
                                         active_run=run_id, owner=owner):
                            continue
                    done = self.state(watch, "done")
                    if not self.checkpoint(card, watch, done):
                        continue
                    watch, event = done, done["event"]
                elif event["state"] == "running":
                    unknown = self.state(watch, "unknown")
                    if not self.checkpoint(card, watch, unknown):
                        continue
                    watch, event = unknown, unknown["event"]
                if event["state"] in ("pending", "ready"):
                    if event["state"] == "ready" or now >= event.get("retry_at", 0):
                        due.append((run_id, {"card": card["id"], "watch": watch}))
                    continue
            lines = card.get("progress", [])[-6:]
            if not lines or not self._ports.available():
                continue
            signature = hashlib.sha256(json.dumps(lines, ensure_ascii=False).encode()).hexdigest()
            started = card.get("started_at") or card.get("created_at") or now
            wakes = watch["wakes"] if watch else 0
            if (now - started < after or wakes >= maximum or watch and (
                    watch["attempts"] >= maximum or watch["seen"] == signature
                    or now - watch["woke_at"] < every)):
                continue
            current = {"version": 1, "run_id": run_id, "seen": signature, "wakes": wakes + 1,
                       "attempts": watch["attempts"] if watch else 0,
                       "woke_at": now, "event": {"id": "%s-gate-%d" % (run_id, wakes + 1),
                       "state": "pending", "progress": signature, "lines": lines[-4:],
                       "owner": list(owner), "created_at": now, "started_at": started}}
            if self.checkpoint(card, watch, current):
                due.append((run_id, {"card": card["id"], "watch": current}))
        return due

    def run(self, run_id: str, entry: Dict[str, Any]) -> None:
        _, every, maximum = self._ports.policy()
        card = self._cards().get(entry["card"])
        if card is None or card.get("run_id") != run_id or card["state"] not in ("working", "waiting"):
            return
        watch = entry["watch"]
        owner = tuple(watch["event"]["owner"])
        if self._ports.stopped() or self._ports.owner(card["conversation"]) != owner:
            return
        if watch["event"]["state"] == "pending":
            if not self._ports.available() or watch["attempts"] >= maximum:
                return
            running = self.state(watch, "running")
            running["attempts"] += 1
            running["woke_at"] = time.time()
            if not self.checkpoint(card, watch, running):
                return
            steps = "；".join(watch["event"]["lines"])
            minutes = max(0, int((time.time() - watch["event"]["started_at"]) // 60))
            event = "后台那件事 %s「%s」做了 %d 分钟了，最近几步：%s" % (card["id"], card.get("title", ""), minutes, steps)
            text = self._ports.ask(event, card["conversation"], owner)
            if self._ports.stopped() or self._ports.owner(card["conversation"]) != owner:
                return
            if text is None:
                state = "unknown" if running["attempts"] >= maximum else "pending"
                self.checkpoint(card, running, self.state(running, state, retry_at=time.time() + every))
                return
            text = self._ports.text(text)
            text.encode("utf-8")
            ready = self.state(running, "ready", reply=text)
            if not self.checkpoint(card, running, ready):
                return
            watch = ready
        if watch["event"]["state"] != "ready":
            return
        current = self._cards().get(card["id"])
        if current is None or current.get("run_id") != run_id or current["state"] not in ("working", "waiting"):
            return
        text, turn_id = watch["event"]["reply"], watch["event"]["id"]
        silent = self._ports.silent(text)
        if not silent and not self.reply(card, watch):
            if not self._ports.say(card["id"], text, turn_id, "后台的事有新进展", active_run=run_id, owner=owner):
                return
        if not self._ports.stopped() and self._ports.owner(card["conversation"]) == owner:
            self.checkpoint(card, watch, self.state(watch, "done"))
