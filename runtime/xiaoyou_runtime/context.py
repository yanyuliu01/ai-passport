"""Model context projected from saved conversation messages.

Only delivery receipts and reset boundaries are stored here. Message bodies,
task state and source identity remain in the existing authoritative stores.
The old transcript is a read/import compatibility source during migration.
"""

import hashlib
import json
import sys
import threading
import time
from copy import deepcopy

from .store import MAX_TURNS
from .cards import ACTIVE
from .world import durable_write


def _version(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class ContextBuilder:
    def __init__(self, cards, legacy, path, memory=None, promises=None):
        self._cards, self._legacy, self._path = cards, legacy, path
        self._memory, self._promises = memory, promises
        self._lock = threading.RLock()
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or any(not isinstance(entry, dict)
                    or not isinstance(entry.get("receipts", {}), dict)
                    or not isinstance(entry.get("hidden", []), list) for entry in state.values()):
                raise ValueError("Invalid context receipt structure")
            self._state = state
        except FileNotFoundError:
            self._state = {}
        except (OSError, ValueError) as error:
            # Losing receipts would repeat quoted context; losing a reset
            # boundary would reintroduce deliberately forgotten history.
            raise RuntimeError("Cannot read model context receipts: %s" % path) from error

    def _entry(self, conversation):
        return self._state.setdefault(conversation, {"receipts": {}, "hidden": []})

    def frame(self, conversation, focus=None):
        """Current task/commitment facts for a decision or speaking phase.

        Re-read after committing an action: the speaker must not be given the
        pre-decision task state as though it were still current.
        """
        cards = self._cards.recent(conversation, limit=10 ** 6)
        listed = [card for card in cards if card["state"] in ACTIVE]
        listed += [card for card in cards if card["state"] not in ACTIVE][-5:]
        listed.sort(key=lambda card: card["created_at"])
        if focus is not None:
            focus = self._cards.get(focus["id"]) or focus
            if all(card["id"] != focus["id"] for card in listed):
                listed.insert(0, focus)
        return {"at": time.time(), "cards": listed, "focus": focus,
                "memory": self._memory.notes() if self._memory is not None else [],
                "promises": [item for item in self._promises.open()
                             if item.get("conversation", "default") == conversation]
                            if self._promises is not None else []}

    def turns(self, conversation, exclude=()):
        # One Cards lock supplies a consistent message/task view. No model or
        # effect runs while projecting it, including after a journal replay.
        tasks, messages = self._cards.context_snapshot(conversation)
        records = {message["id"]: message for message in messages
                   if message["conversation"] == conversation and not message.get("relay_to")}
        canonical = {}
        for message in records.values():
            author = message.get("author") or {}
            if message.get("status") != "final" or not message.get("text") \
                    or author.get("kind") not in ("xiaoyou", "agent") \
                    or message.get("kind") in ("result", "activity") \
                    or (message.get("source") or {}).get("type") == "desktop":
                continue
            card = tasks.get(message.get("task"), {})
            parent = records.get(message.get("reply_to"))
            if parent and parent.get("status") != "final":
                continue
            # Unpaired summaries and notes are assistant events, never a
            # fabricated owner request. The label only attributes the event.
            text = parent["text"] if parent else (
                "（后台的事「%s」%s）" % (card.get("title", ""),
                    "被停了" if message["id"].startswith(card.get("id", "") + "-cancel-") else "有结果了")
                if message.get("kind") == "summary" else "（小幽主动说）")
            mid = message["id"]
            tid = mid[:-6] if mid.endswith(":reply") else mid
            canonical[mid] = {"id": tid, "text": text, "reply": message["text"],
                              "at": message.get("created_at") or 0,
                              "by": author.get("name") or "xiaoyou", "seen": [],
                              "_key": mid, "_version": _version(text, message["text"], author)}
        turns = []
        for turn in self._legacy.context_turns(conversation):
            mid = turn.get("reply_ref") or turn["id"] + ":reply"
            if mid in canonical:
                # Preserve old native-session delivery knowledge without
                # preserving a second copy of the message body.
                if turn["reply"] == canonical[mid]["reply"] and turn["text"] == canonical[mid]["text"]:
                    canonical[mid]["seen"] = list(turn["seen"])
                    canonical[mid]["by"] = turn["by"]
                continue
            # A canonical revision that is now failed/partial must not fall
            # back to an older transcript answer.
            if mid in records:
                continue
            turns.append(dict(turn, _key=mid,
                              _version=_version(turn["text"], turn["reply"], turn["by"])))
        turns.extend(canonical.values())
        with self._lock:
            entry = self._entry(conversation)
            hidden = set(entry.get("hidden", []))
            receipts = entry.get("receipts", {})
            result = []
            for turn in sorted(turns, key=lambda item: (item["at"], item["id"])):
                if turn["_key"] in hidden or turn["id"] in exclude:
                    continue
                receipt = receipts.get(turn["_key"], {})
                if receipt.get("version") == turn["_version"]:
                    turn["seen"] = sorted(set(turn["seen"] + receipt.get("seen", [])))
                    turn["by"] = receipt.get("by") or turn["by"]
                result.append(turn)
            return result[-MAX_TURNS:]

    def unseen(self, conversation, agent):
        return [turn for turn in self.turns(conversation) if agent not in turn["seen"]]

    def offer(self, conversation, turns):
        # The old clients' share/import endpoint remains readable during R4
        # client migration. New Runtime messages never use this write path.
        return self._legacy.offer(conversation, [turn for turn in turns
            if self._cards.message(turn["id"] + ":reply") is None])

    def mark(self, conversation, agent, turns):
        self._mark(conversation, [agent], turns)

    def delivered(self, conversation, turn_id, agents, by=None, *, reply=None, prompt=None):
        if reply is None:
            turns = [turn for turn in self.turns(conversation) if turn["id"] == turn_id]
        else:
            # A model has already produced this saved result. Persist its
            # receipt before publishing the terminal task, so readers cannot
            # observe completion without the corresponding delivery knowledge.
            # If publication fails, the receipt matches no message and does
            # nothing. No message body is stored here.
            turns = [{"_key": turn_id + ":reply", "seen": [], "by": by,
                      "_version": _version(prompt, reply, {"kind": "xiaoyou", "name": "xiaoyou"})}]
        self._mark(conversation, agents, turns, by)

    def _mark(self, conversation, agents, turns, by=None):
        if not turns:
            return
        with self._lock:
            updated = deepcopy(self._state)
            receipts = updated.setdefault(conversation, {}).setdefault("receipts", {})
            for turn in turns:
                previous = receipts.get(turn["_key"], {})
                seen = previous.get("seen", []) if previous.get("version") == turn["_version"] else []
                receipts[turn["_key"]] = {"version": turn["_version"],
                    "seen": sorted(set(seen + turn["seen"] + agents)),
                    "by": by or previous.get("by") or turn["by"]}
            # Bound receipt storage to retained model context. Pruned receipts
            # can only cause a bounded recap, never repeat an external effect.
            if len(receipts) > MAX_TURNS * 2:
                for key in list(receipts)[:-MAX_TURNS * 2]:
                    del receipts[key]
            try:
                durable_write(self._path, updated)
            except OSError:
                # Repeating quoted history is safe. A receipt failure must not
                # turn an already saved reply into a failed owner request.
                sys.stderr.write("Model context receipt unavailable; saved messages remain authoritative.\n")
                return
            self._state = updated

    def clear(self, conversation):
        turns = self.turns(conversation)
        if not turns:
            return False
        # Include every retained message, not just the last MAX_TURNS, so a
        # later revision cannot resurrect a conversation reset by the owner.
        _, messages = self._cards.context_snapshot(conversation)
        with self._lock:
            updated = deepcopy(self._state)
            entry = updated.setdefault(conversation, {})
            entry["hidden"] = sorted(set(entry.get("hidden", [])) |
                {message["id"] for message in messages if message["conversation"] == conversation} |
                {turn.get("reply_ref") or turn["id"] + ":reply"
                 for turn in self._legacy.context_turns(conversation)})
            entry["receipts"] = {}
            durable_write(self._path, updated)
            self._state = updated
        return True
