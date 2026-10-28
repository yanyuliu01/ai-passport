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

from ..helpers.base import clip
from . import recall as recall_search
from . import context_window
from .context_summaries import SEGMENT_TURNS, SegmentSummaries, source_page
from ..world.store import MAX_TURNS
from ..world.cards import ACTIVE
from ..world.world import durable_write

def _version(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def bind(store, cards, memory=None, promises=None):
    """Build the context for this store and make the store's transcript reads use it."""
    def resolve(mid):
        message = cards.message(mid)
        return message.get("text") if message and message.get("status") == "final" else None
    store.legacy_transcript.resolve_with(resolve)
    return store.use_context(ContextBuilder(cards, store.legacy_transcript, store.state_dir / "context.json",
                                            memory, promises))


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
        self._summaries = SegmentSummaries(path.with_name("context-summaries.json"))

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

    def history(self, conversation, exclude=()):
        """All readable retained turns, never a promise of an eternal archive."""
        if (self._cards.conversations.get(conversation) or {}).get('deleted'):
            return []
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
            return result

    def turns(self, conversation, exclude=()):
        """Bounded compatibility view for existing recent-history callers."""
        return self.history(conversation, exclude)[-MAX_TURNS:]

    @staticmethod
    def _segments(conversation, turns):
        segments = []
        for offset in range(0, len(turns), SEGMENT_TURNS):
            page = turns[offset:offset + SEGMENT_TURNS]
            segments.append({"id": _version(conversation, [(turn["_key"], turn["_version"]) for turn in page]),
                             "first": page[0]["id"], "last": page[-1]["id"], "count": len(page),
                             "turns": page})
        return segments

    def segments(self, conversation):
        """Derived version-bound pages; no second history file or model call."""
        return self._segments(conversation, self.history(conversation))

    def segment(self, conversation, segment_id):
        for segment in self.segments(conversation):
            if segment["id"] == segment_id:
                return deepcopy(segment)
        raise ValueError("History segment changed or is no longer readable")

    def summary_candidate(self, conversation, *, now):
        return self._summaries.candidate(conversation, self.segments(conversation), now)

    def summary_activity(self, conversation):
        """Include accepted inputs lacking a final reply, not only history pairs."""
        _, messages = self._cards.context_snapshot(conversation)
        return (max((message.get("seq", 0) for message in messages), default=0),
                max((message.get("created_at", 0) for message in messages), default=0),
                any(message.get("status") in ("queued", "streaming") for message in messages))

    def begin_summary(self, conversation, segment, *, now):
        with self._lock:
            current = self._summaries.candidate(conversation, self.segments(conversation), now)
            if current is None or current["id"] != segment["id"]:
                return False
            return self._summaries.begin(conversation, current, now)

    def save_summary(self, conversation, segment, result):
        with self._lock:
            current = next((page for page in self.segments(conversation) if page["id"] == segment["id"]), None)
            return self._summaries.save(conversation, current, result) if current is not None else False

    def window(self, conversation, agent, *, seed=None, max_chars=8000, text_chars=500, reply_chars=1000, query=None):
        """Render bounded quotes and receipt only complete, actually quoted turns."""
        if type(max_chars) is not int or max_chars < 256:
            raise ValueError("Context window budget must be at least 256 characters")
        if seed is not None and (type(seed) is not int or not 1 <= seed <= MAX_TURNS):
            raise ValueError("Context seed must contain 1 to MAX_TURNS recent turns")
        if any(type(size) is not int or size < 1 for size in (text_chars, reply_chars)):
            raise ValueError("Context excerpt sizes must be positive integers")
        if query is not None and not isinstance(query, str):
            raise ValueError("Context query must be text")
        turns = self.history(conversation)
        segments = self._segments(conversation, turns)
        lookup = context_window.prepare_lookup(query, turns)
        window = context_window.render(turns, segments, agent, seed=seed, max_chars=max_chars,
                                       text_chars=text_chars, reply_chars=reply_chars, query=query, lookup=lookup)
        excluded = {turn["_key"] for turn in window["turns"] + turns[-8:]}
        with self._lock:
            summary, count = self._summaries.render(conversation, segments, excluded, max_chars // 4)
        if summary:
            window = context_window.render(turns, segments, agent, seed=seed,
                max_chars=max_chars - len(summary) - 2, text_chars=text_chars, reply_chars=reply_chars, query=query, lookup=lookup)
            window["quotes_body"], window["summary_body"] = window["body"], summary
            window["body"] = summary + ("\n\n" + window["body"] if window["body"] else "")
        window["coverage"]["summarized"] = count
        return window

    def recall(self, conversation: str, query: str) -> str:
        """Read retained history, global memory logs and scoped task evidence."""
        if query.startswith("source:"):
            return source_page(query, self.segments(conversation))
        records = []
        for segment in self.segments(conversation):
            for turn in segment["turns"]:
                records.append(("聊天", "段 %s / %s" % (segment["id"], turn["id"]), float(turn.get("at") or 0),
                                "主人：%s\n小幽：%s" % (turn["text"], turn["reply"])))
        for path in self._memory.log_files() if self._memory is not None else []:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
                stamp = path.stat().st_mtime
            except OSError:
                continue
            records.extend(("记忆", path.stem, stamp, line) for line in lines if line.strip())
        with self._lock:
            hidden = set(self._entry(conversation).get("hidden", []))
        for card in self._cards.recent(conversation, limit=120):
            # Task entries duplicate canonical messages. A model-context reset
            # must not reintroduce those same hidden messages through recall.
            # The task's current title/state remains available independently.
            said = "\n".join(entry.get("text") or "" for entry in card.get("entries") or []
                             if entry.get("id") not in hidden)
            records.append(("事", "%s %s" % (card["id"], clip(card.get("title") or "", 24)),
                            float(card.get("updated_at") or card.get("created_at") or 0),
                            (card.get("title") or "") + "\n" + said[-6000:]))
        return recall_search.render(query, recall_search.search(query, records))

    def unseen(self, conversation, agent):
        return [turn for turn in self.turns(conversation) if agent not in turn["seen"]]

    def offer(self, conversation, turns):
        # The old clients' share/import endpoint remains readable during R4
        # client migration. New Runtime messages never use this write path.
        return self._legacy.offer(conversation, [turn for turn in turns
            if self._cards.message(turn["id"] + ":reply") is None])

    def mark(self, conversation, agent, turns):
        self._mark(conversation, [agent], [turn for turn in turns if not turn.get("_excerpted")])

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

    def erase(self, conversation):
        with self._lock:
            updated = deepcopy(self._state)
            updated.pop(conversation, None)
            durable_write(self._path, updated)
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
