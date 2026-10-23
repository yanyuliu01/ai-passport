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
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from .agents import AgentError, Job
from .voice_tools import Toolbox

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
        self._watching: Dict[str, Dict[str, Any]] = {}
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
        with self._lock:
            entry = self._watching.get(run_id)
            if entry is None:
                entry = self._watching[run_id] = {"card": card, "agent": agent, "started": time.monotonic(),
                                                  "lines": [], "seen": 0, "woke": None, "wakes": 0}
            entry["lines"] = (entry["lines"] + [line])[-6:]
            entry["count"] = entry.get("count", 0) + 1

    def finished(self, run_id: str) -> None:
        with self._lock:
            self._watching.pop(run_id, None)

    # ---- 后台线程 ----

    def _run(self) -> None:
        while not self._stop.wait(TICK):
            try:
                self.tick()
            except Exception as error:  # 后台节拍出错不能拖垮 Runtime：记下来，下一拍再看
                sys.stderr.write("后台节拍出错：%r\n" % (error,))

    def tick(self, now: Optional[float] = None) -> None:
        """看一眼：该叫她的叫她。测试里可以直接调。"""
        mono = time.monotonic() if now is None else now
        for run_id, entry in self._due_watches(mono):
            self._wake_for_task(run_id, entry)
        for item in self._xiaoyou.promises.due(time.time() if now is None else now):
            self._xiaoyou.promises.mark_nudged(item["id"])
            self._wake_for_promise(item)
        self._maybe_upkeep(time.time() if now is None else now)

    def _due_watches(self, mono: float) -> List[Any]:
        due = []
        with self._lock:
            for run_id, entry in list(self._watching.items()):
                card = self._xiaoyou.cards.get(entry["card"])
                if card is None or card.get("run_id") != run_id or card["state"] not in ("working", "waiting"):
                    self._watching.pop(run_id, None)
                    continue
                fresh = entry.get("count", 0) > entry["seen"]
                if (mono - entry["started"] >= WATCH_AFTER and fresh and entry["wakes"] < WATCH_MAX
                        and (entry["woke"] is None or mono - entry["woke"] >= WATCH_EVERY)):
                    entry["woke"], entry["seen"] = mono, entry.get("count", 0)
                    entry["wakes"] += 1
                    due.append((run_id, dict(entry)))
        return due

    # ---- 叫醒她 ----

    def _voice(self) -> Any:
        voice = self._xiaoyou.voice_agent()
        return voice if voice is not None and voice.talks_with_tools() else None

    def _recent_said(self) -> str:
        turns = self._xiaoyou.store.transcript.turns(self._conversation)[-8:]
        return "\n".join("- %s" % turn["reply"][:200].replace("\n", " ") for turn in turns if turn.get("reply"))

    def _ask(self, event: str, only: List[str], rules: str) -> Optional[str]:
        voice = self._voice()
        if voice is None:
            return None
        xiaoyou = self._xiaoyou
        system = "\n\n".join(part for part in (
            xiaoyou.config.persona, xiaoyou.style(), "\n".join(xiaoyou.memory_lines()),
            "\n".join(xiaoyou.promise_lines()), rules) if part)
        box = Toolbox([], [], [], lambda fields: "这一轮不能交办新的事", lambda card: xiaoyou.detail(card, self._conversation),
                      xiaoyou.memory, lambda query: xiaoyou.recall(self._conversation, query), xiaoyou.promises,
                      self._conversation, only=only)
        text = "%s\n\n（你最近跟主人说过的话：）\n%s" % (event, self._recent_said() or "（还没有）")
        messages = None
        began = time.monotonic()
        try:
            if xiaoyou.config.decide_first and voice.talks_with_tools():
                decision = voice.run(Job(text=text, system=system, plain=True,
                                          timeout=xiaoyou.config.voice_timeout_seconds, tools=box,
                                          decision_only=True))
                data = (decision.fields or {}).get("_decision", {})
                system += "\n\n这一轮已落实的事实：" + data.get("facts", "没有执行任何承诺。")
                system += "\n现在只说话或查记录，不能新增任务、记忆或约定。"
                messages = data.get("messages", [])
                box = box.speaking()
            outcome = voice.run(Job(text=text, session_id=None, conversation=self._conversation, system=system,
                                    plain=True, timeout=max(0.001, xiaoyou.config.voice_timeout_seconds - (time.monotonic() - began)),
                                    on_text=lambda raw: None, tools=box, decision_messages=messages))
        except AgentError as error:
            sys.stderr.write("后台叫醒小幽没成：%s\n" % error)
            return None
        return (outcome.text or "").strip()

    def _say(self, card_id: str, text: str, turn_id: str, event: str) -> bool:
        """她决定要说：记成主对话里的一条（kind note），也记进对话记录，下一轮她知道自己说过。"""
        if not text or SILENT.match(text) or text.startswith(("（不说", "(不说")):
            return False
        xiaoyou = self._xiaoyou
        said = xiaoyou.chat_text(text)
        xiaoyou.intake.writer("timer", actor="xiaoyou", cause=turn_id).reply(card_id, say=said, turn_id=turn_id, scope="main", kind="note")
        xiaoyou.store.transcript.add(self._conversation, turn_id, "（没人开口，%s）" % event, said,
                                     xiaoyou.voice_name(), [xiaoyou.voice_name()])
        return True

    def _wake_for_task(self, run_id: str, entry: Dict[str, Any]) -> None:
        card = self._xiaoyou.cards.get(entry["card"])
        if card is None:
            return
        steps = "；".join(entry["lines"][-4:])
        minutes = int((time.monotonic() - entry["started"]) // 60)
        event = "后台那件事 %s「%s」做了 %d 分钟了，最近几步：%s" % (card["id"], card.get("title", ""), minutes, steps)
        text = self._ask(event, ["task_detail", "recall"], GATE_RULES)
        self._say(card["id"], text or "", "%s-gate-%d" % (run_id, entry["wakes"]), "后台的事有新进展")

    def _wake_for_promise(self, item: Dict[str, Any]) -> None:
        event = "你答应主人的事到点了：%s %s%s" % (item["id"], item["title"],
                                             "（%s）" % item["detail"] if item.get("detail") else "")
        text = self._ask(event, ["recall", "resolve", "track"], GATE_RULES)
        if text and not SILENT.match(text) and not text.startswith(("（不说", "(不说")):
            card = self._xiaoyou.intake.writer("timer", actor="xiaoyou").task_open(self._conversation, item["title"])
            self._say(card["id"], text, "%s-due-%d" % (item["id"], int(time.time())), "答应的事到点了")

    # ---- 整理记忆 ----

    def _maybe_upkeep(self, now: float) -> None:
        turns = self._xiaoyou.store.transcript.turns(self._conversation)
        if not turns or now - float(turns[-1].get("at") or now) < UPKEEP_QUIET:
            return
        if self._upkept is None:
            # 第一次：从现在开始算，不把以前所有的对话翻一遍。
            self._upkept = turns[-1]["id"]
            self._save_marker()
            return
        start = 0
        if self._upkept is not None:
            ids = [turn["id"] for turn in turns]
            start = ids.index(self._upkept) + 1 if self._upkept in ids else 0
        fresh = turns[start:][-UPKEEP_MAX_TURNS:]
        if len(fresh) < UPKEEP_MIN_TURNS:
            return
        self._upkept = turns[-1]["id"]
        self._save_marker()
        lines = "\n\n".join("主人：%s\n小幽：%s" % (turn["text"][:400], turn["reply"][:400]) for turn in fresh)
        self._ask("（最近这几轮对话：）\n" + lines, ["remember", "replace", "forget"], UPKEEP_RULES)

    def _load_marker(self) -> Optional[str]:
        try:
            value = json.loads(self._marker.read_text(encoding="utf-8")).get("turn")
        except (OSError, ValueError, AttributeError):
            return None
        return value if isinstance(value, str) else None

    def _save_marker(self) -> None:
        try:
            self._marker.parent.mkdir(parents=True, exist_ok=True)
            self._marker.write_text(json.dumps({"turn": self._upkept}), encoding="utf-8")
        except OSError:
            pass
