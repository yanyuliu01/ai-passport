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

from .agents import AgentError
from .voice_tools import Toolbox
from .world import durable_write
from .operations import valid_watch

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
        # 整理到哪一轮（编号），存在记忆旁边：重启后不把整段历史再整理一遍。
        self._marker = xiaoyou.memory.log_dir.parent / "upkeep.json"
        self._upkept: Optional[str] = self._load_marker()
        self._thread = threading.Thread(target=self._run, name="xiaoyou-pulse", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()

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
        for item in self._xiaoyou.promises.due(time.time() if now is None else now):
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

    def _checkpoint_watch(self, card, before, after):
        event_id = after["event"]["id"]
        return self._xiaoyou.intake.writer("timer", actor="xiaoyou", cause=event_id).progress_checkpoint(
            card["id"], card["run_id"], before, after) is not None

    @staticmethod
    def _watch_state(watch, state, **fields):
        result = deepcopy(watch)
        result["event"].update(state=state, **fields)
        if state in ("done", "unknown"):
            result["event"].pop("reply", None)
        return result

    def _watch_reply(self, card, watch):
        message = self._xiaoyou.cards.message(watch["event"]["id"] + ":reply")
        return message if (message and message.get("status") == "final" and message.get("task") == card["id"]
                and message.get("conversation") == card["conversation"] and message.get("kind") == "note") else None

    def _due_watches(self, now: float) -> List[Any]:
        due = []
        for card, watch in self._xiaoyou.cards.watches():
            run_id, owner = card["run_id"], self._owner(card["conversation"])
            if owner is None or self._stop.is_set():
                continue
            if watch is not None:
                if not valid_watch(watch) or watch["run_id"] != run_id:
                    continue  # Preserve malformed claims rather than reset their counters.
                event = watch["event"]
                if event["owner"] != list(owner):
                    continue
                with self._lock:
                    active = (card["conversation"], ("watch", run_id, event["id"])) in self._pending
                if active:
                    continue
                final = self._watch_reply(card, watch)
                if event["state"] != "done" and final:
                    if not any(item.get("id") == final["id"] for item in card["entries"]):
                        if not self._say(card["id"], final["text"], event["id"], "后台的事有新进展",
                                         active_run=run_id, owner=owner):
                            continue
                    done = self._watch_state(watch, "done")
                    if not self._checkpoint_watch(card, watch, done):
                        continue
                    watch, event = done, done["event"]
                elif event["state"] == "running":
                    unknown = self._watch_state(watch, "unknown")
                    if not self._checkpoint_watch(card, watch, unknown):
                        continue
                    watch, event = unknown, unknown["event"]
                if event["state"] in ("pending", "ready"):
                    if event["state"] == "ready" or now >= event.get("retry_at", 0):
                        due.append((run_id, {"card": card["id"], "watch": watch}))
                    continue
            lines = card.get("progress", [])[-6:]
            if not lines or self._voice() is None:
                continue
            signature = hashlib.sha256(json.dumps(lines, ensure_ascii=False).encode()).hexdigest()
            started = card.get("started_at") or card.get("created_at") or now
            wakes = watch["wakes"] if watch else 0
            if (now - started < WATCH_AFTER or wakes >= WATCH_MAX or watch and (
                    watch["attempts"] >= WATCH_MAX or watch["seen"] == signature
                    or now - watch["woke_at"] < WATCH_EVERY)):
                continue
            current = {"version": 1, "run_id": run_id, "seen": signature, "wakes": wakes + 1,
                       "attempts": watch["attempts"] if watch else 0,
                       "woke_at": now, "event": {"id": "%s-gate-%d" % (run_id, wakes + 1),
                       "state": "pending", "progress": signature, "lines": lines[-4:],
                       "owner": list(owner), "created_at": now, "started_at": started}}
            if self._checkpoint_watch(card, watch, current):
                due.append((run_id, {"card": card["id"], "watch": current}))
        return due

    # ---- 叫醒她 ----

    def _voice(self) -> Any:
        voice = self._xiaoyou.voice_agent()
        return voice if voice is not None and voice.talks_with_tools() else None

    def _recent_said(self, conversation) -> str:
        turns = self._xiaoyou.context.turns(conversation)[-8:]
        return "\n".join("- %s" % turn["reply"][:200].replace("\n", " ") for turn in turns if turn.get("reply"))

    def _ask(self, event: str, only: List[str], rules: str, conversation=None, before_effect=None) -> Optional[str]:
        voice = self._voice()
        if voice is None:
            return None
        xiaoyou = self._xiaoyou
        conversation = conversation or self._conversation
        owner = self._owner(conversation)
        if owner is None or self._stop.is_set():
            return None
        system = "\n\n".join(part for part in (
            xiaoyou.config.persona, xiaoyou.style(), "\n".join(xiaoyou.memory_lines()),
            "\n".join(xiaoyou.promise_lines(conversation)), rules) if part)
        box = Toolbox([], [], [], lambda fields: "这一轮不能交办新的事", lambda card: xiaoyou.detail(card, conversation),
                      xiaoyou.memory, lambda query: xiaoyou.recall(conversation, query), xiaoyou.promises,
                      conversation, only=only, before_effect=before_effect)
        text = "%s\n\n（你最近跟主人说过的话：）\n%s" % (event, self._recent_said(conversation) or "（还没有）")
        messages = None
        began = time.monotonic()
        try:
            with xiaoyou.speaking_turn(conversation) as cut:
                if xiaoyou.config.decide_first and voice.talks_with_tools():
                    decision = xiaoyou._speak(voice, text, conversation, system, None, 0, catch_up=True,
                                              tools=box, decision_only=True, interrupt=cut,
                                              ephemeral=rules == UPKEEP_RULES)
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
                    ephemeral=rules == UPKEEP_RULES)
                if cut.is_set() or self._owner(conversation) != owner or self._stop.is_set():
                    return None
        except (AgentError, OSError, RuntimeError) as error:
            sys.stderr.write("后台叫醒小幽没成：%s\n" % error)
            return None
        return (outcome.text or "").strip()

    def _say(self, card_id: str, text: str, turn_id: str, event: str, *, active_run=None, owner=None) -> bool:
        """Save one note in its original conversation; context reads that message."""
        if not text or SILENT.match(text) or text.startswith(("（不说", "(不说")):
            return False
        xiaoyou = self._xiaoyou
        card = xiaoyou.cards.get(card_id)
        if card is None or self._owner(card["conversation"]) is None or self._stop.is_set():
            return False
        if owner is not None and self._owner(card["conversation"]) != owner:
            return False
        said = xiaoyou.chat_text(text)
        saved = xiaoyou.intake.writer("timer", actor="xiaoyou", cause=turn_id).reply(
            card_id, say=said, turn_id=turn_id, scope="main", kind="note", expected_active_run=active_run)
        if saved is None:
            return False
        xiaoyou.context.delivered(card["conversation"], turn_id, [xiaoyou.voice_name()], xiaoyou.voice_name())
        return True

    def _wake_for_task(self, run_id: str, entry: Dict[str, Any]) -> None:
        card = self._xiaoyou.cards.get(entry["card"])
        if card is None or card.get("run_id") != run_id or card["state"] not in ("working", "waiting"):
            return
        watch = entry["watch"]
        owner = tuple(watch["event"]["owner"])
        if self._stop.is_set() or self._owner(card["conversation"]) != owner:
            return
        if watch["event"]["state"] == "pending":
            if self._voice() is None or watch["attempts"] >= WATCH_MAX:
                return
            running = self._watch_state(watch, "running")
            running["attempts"] += 1
            running["woke_at"] = time.time()
            if not self._checkpoint_watch(card, watch, running):
                return
            steps = "；".join(watch["event"]["lines"])
            minutes = max(0, int((time.time() - watch["event"]["started_at"]) // 60))
            event = "后台那件事 %s「%s」做了 %d 分钟了，最近几步：%s" % (card["id"], card.get("title", ""), minutes, steps)
            text = self._ask(event, ["task_detail", "recall"], GATE_RULES, card["conversation"])
            if self._stop.is_set() or self._owner(card["conversation"]) != owner:
                return
            if text is None:
                state = "unknown" if running["attempts"] >= WATCH_MAX else "pending"
                self._checkpoint_watch(card, running, self._watch_state(running, state, retry_at=time.time() + WATCH_EVERY))
                return
            text = self._xiaoyou.chat_text(text)
            text.encode("utf-8")
            ready = self._watch_state(running, "ready", reply=text)
            if not self._checkpoint_watch(card, running, ready):
                return
            watch = ready
        if watch["event"]["state"] != "ready":
            return
        current = self._xiaoyou.cards.get(card["id"])
        if current is None or current.get("run_id") != run_id or current["state"] not in ("working", "waiting"):
            return
        text, turn_id = watch["event"]["reply"], watch["event"]["id"]
        silent = not text or SILENT.match(text) or text.startswith(("（不说", "(不说"))
        if not silent and not self._watch_reply(card, watch):
            if not self._say(card["id"], text, turn_id, "后台的事有新进展", active_run=run_id, owner=owner):
                return
        if not self._stop.is_set() and self._owner(card["conversation"]) == owner:
            self._checkpoint_watch(card, watch, self._watch_state(watch, "done"))

    def _wake_for_promise(self, item: Dict[str, Any]) -> None:
        current = next((value for value in self._xiaoyou.promises.open() if value["id"] == item["id"]), None)
        if current is None or any(current.get(key) != item.get(key) for key in ("due", "nudged", "conversation")):
            return
        conversation = item.get("conversation") or self._conversation
        version = hashlib.sha256(json.dumps([item.get("due"), item.get("nudged")]).encode()).hexdigest()[:16]
        turn_id = "%s-due-%s" % (item["id"], version)
        final = self._xiaoyou.cards.message(turn_id + ":reply")
        if final and final.get("status") == "final" and final.get("conversation") == conversation:
            self._xiaoyou.promises.mark_nudged(item["id"])
            return
        if current.get("wake"):
            return  # A previous tool may have run; never repeat it blindly.
        claimed = []
        owner = self._owner(conversation)
        def before_effect():
            if self._stop.is_set() or self._owner(conversation) != owner:
                raise RuntimeError("Reminder owner changed")
            if not claimed:
                self._xiaoyou.promises.claim_wake(item["id"], turn_id)
                claimed.append(True)
        event = "你答应主人的事到点了：%s %s%s" % (item["id"], item["title"],
                                             "（%s）" % item["detail"] if item.get("detail") else "")
        text = self._ask(event, ["recall", "resolve", "track"], GATE_RULES, conversation, before_effect)
        if text is None:
            return
        if text and not SILENT.match(text) and not text.startswith(("（不说", "(不说")):
            card = self._xiaoyou.intake.writer("timer", actor="xiaoyou").task_open(conversation, item["title"])
            if not self._say(card["id"], text, turn_id, "答应的事到点了"):
                return
        self._xiaoyou.promises.mark_nudged(item["id"])

    # ---- 整理记忆 ----

    def _maybe_upkeep(self, now: float) -> None:
        turns = self._xiaoyou.context.turns(self._conversation)
        if not turns or now - float(turns[-1].get("at") or now) < UPKEEP_QUIET:
            return
        if self._upkept is None:
            # 第一次：从现在开始算，不把以前所有的对话翻一遍。
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
        def before_effect():
            # Do not repeat replace/forget after a lost model response. A
            # failure before any tool still leaves the window retryable.
            self._save_marker(turns[-1]["id"])
        if self._ask("（最近这几轮对话：）\n" + lines, ["remember", "replace", "forget"],
                     UPKEEP_RULES, before_effect=before_effect) is not None:
            self._save_marker(turns[-1]["id"])

    def _load_marker(self) -> Optional[str]:
        try:
            value = json.loads(self._marker.read_text(encoding="utf-8")).get("turn")
        except (OSError, ValueError, AttributeError):
            return None
        return value if isinstance(value, str) else None

    def _save_marker(self, turn) -> None:
        durable_write(self._marker, {"turn": turn})
        self._upkept = turn
