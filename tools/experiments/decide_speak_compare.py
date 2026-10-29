"""先定再说：串行（S）与推测并行（P）对比实验。

方案见 docs/development/decide-speak-experiment.zh_CN.md。用例见同目录的
decide_speak_cases.jsonl。

    python3 tools/experiments/decide_speak_compare.py run --out ~/xy-exp/results.jsonl
    python3 tools/experiments/decide_speak_compare.py run --dry-run --only ack-01,task-01,chat-02 --out /tmp/x.jsonl
    python3 tools/experiments/decide_speak_compare.py summarize ~/xy-exp/results.jsonl
    python3 tools/experiments/decide_speak_compare.py review ~/xy-exp/results.jsonl --out-dir ~/xy-exp/review

不执行任何真实操作：承诺类工具只校验参数，再交给记录器返回“已接收”一类的事实；
不派帮手、不写记忆、不建约定，也不读写 Runtime 的状态目录。密钥只从环境变量或密钥文件读取，
不写进结果。结果文件必须放在仓库外。

请求格式按 R7 的 loop/decision.py（不流式、只给承诺类工具和 just_reply）和
helpers/dsh_stream.py（流式、只给查看类工具）复刻。系统提示只有人格文件加上用例给出的状态，
比线上短；两组使用同一份，所以只影响绝对耗时，不影响两组之间的比较。
"""
import argparse
import json
import os
import random
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
CASES = Path(__file__).with_name("decide_speak_cases.jsonl")
BASE_URL = "https://api.deepseek.com/anthropic"
KEY_FILE = "~/.config/xiaoyu/deepseek.key"
DEFAULT_NOW = "2026-10-27 周二 23:40（上海时区）"
DEFAULT_HELPERS = ["cc", "codex", "project"]
DECIDE_DEADLINE = 1.3

# 和 R7 loop/decision.py 一致。
COMMITMENTS = ("start_task", "amend_task", "cancel_task", "remember", "replace", "forget", "track", "resolve")
DECIDE_RULES = (
    "这一阶段只决定这轮要落实什么，不给主人说话。只选一个工具；不用改变任务、记忆或约定时选 just_reply。"
    "查资料、查看任务详情留到下一阶段。不能仅凭说了要做就当作做了。"
)
NOTHING_DONE = "这轮没有启动任务、改记忆或新增约定。"
TIMEOUT_FACTS = "决定阶段超时。这轮没有执行任何承诺，不能声称已经安排或记下。"
SPECULATIVE_FACTS = "这轮没有执行任何承诺，不能声称已经安排或记下。"
# 和 R7 loop/xiaoyu.py 的 _converse_decided 一致。
SPEAK_RULES = ("\n现在只向主人说话或查记录，不得再启动任务、改记忆或作新的约定。"
               "根据上述真实结果回答一次；没有执行就不能说已经做了，失败就照实说明。")
BUBBLE = re.compile(r"\n\s*---\s*\n")


# ---------- 用例与提示 ----------

def load_cases(path: Path, only: Optional[List[str]]) -> List[Dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if only:
        wanted = set(only)
        missing = wanted - {case["id"] for case in cases}
        if missing:
            raise SystemExit("没有这些用例：%s" % ", ".join(sorted(missing)))
        cases = [case for case in cases if case["id"] in wanted]
    return cases


def world_of(case: Dict[str, Any]) -> Dict[str, Any]:
    world = dict(case.get("world") or {})
    world.setdefault("now", DEFAULT_NOW)
    world.setdefault("helpers", list(DEFAULT_HELPERS))
    world.setdefault("memories", [])
    world.setdefault("tasks", [])
    world.setdefault("promises", [])
    return world


def system_prompt(persona: str, world: Dict[str, Any]) -> str:
    lines = [persona.strip(), "", "## 现在", "现在是 %s。" % world["now"],
             "能交办的帮手：%s。" % "、".join(world["helpers"])]
    lines += ["", "## 你记得的关于主人的事"]
    lines += world["memories"] or ["（还没有）"]
    lines += ["", "## 进行中和最近的事"]
    if world["tasks"]:
        for task in world["tasks"]:
            extra = task.get("progress") or task.get("result") or ""
            lines.append("- %s｜%s｜%s｜%s%s" % (task["card"], task["agent"], task["title"], task["state"],
                                                 ("｜" + extra) if extra else ""))
    else:
        lines.append("（没有）")
    lines += ["", "## 答应过还没办的事"]
    lines += ["- %s｜%s" % (item["id"], item["title"]) for item in world["promises"]] or ["（没有）"]
    return "\n".join(lines)


def messages_of(case: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把前文和这一批消息转成交替的 user/assistant 消息；相邻同一方的合并。"""
    turns = [("user" if item["role"] == "owner" else "assistant", item["text"]) for item in case["history"]]
    turns.append(("user", "\n".join(case["owner"])))
    merged: List[Dict[str, Any]] = []
    for role, text in turns:
        if merged and merged[-1]["role"] == role:
            merged[-1]["content"] += "\n" + text
        else:
            merged.append({"role": role, "content": text})
    if merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": "（接着之前的对话）"})
    return merged


def commitment_tools(world: Dict[str, Any]) -> List[Dict[str, Any]]:
    """R7 loop/voice_tools.py 里承诺类工具的定义（只取 decision 用到的）。"""
    card = {"type": "string", "description": "那件事的编号，连同前面的字母，比如 c3"}
    quote = {"type": "string", "description": "主人的原话（出处），一句就够"}
    return [
        {"name": "start_task",
         "description": "把一件需要动手的事（查资料、读写文件、跑命令、上网……）交给一个帮手在后台做。做完会另外告诉主人。",
         "input_schema": {"type": "object", "properties": {
             "agent": {"type": "string", "enum": list(world["helpers"]), "description": "交给哪个帮手"},
             "title": {"type": "string", "description": "这件事的短标题，不超过 24 个字"},
             "task": {"type": "string", "description": "交代给帮手的话。它看不到你和主人的对话，把背景和想要的结果写清楚"},
             "card": dict(card, description="要接着一件已有的事做时写它的编号；新的事不写")},
             "required": ["agent", "title", "task"]}},
        {"name": "amend_task", "description": "主人对一件已有的事有新的话：要求变了（redo）或者追加内容（after）。",
         "input_schema": {"type": "object", "properties": {
             "card": dict(card, description="哪件事；主人正看着的那件可以不写"),
             "task": {"type": "string", "description": "新的内容"},
             "mode": {"type": "string", "enum": ["redo", "after"]}}, "required": ["task", "mode"]}},
        {"name": "cancel_task", "description": "主人不要某件正在做的事了。",
         "input_schema": {"type": "object", "properties": {"card": card}, "required": []}},
        {"name": "remember", "description": "记住一条关于主人的事：偏好、定下的规矩、长期的情况、答应过的长期约定。",
         "input_schema": {"type": "object", "properties": {
             "note": {"type": "string", "description": "要记的一句话，写清楚以后该怎么做"},
             "kind": {"type": "string", "enum": ["偏好", "规矩", "事实", "承诺"]},
             "quote": quote}, "required": ["note", "kind"]}},
        {"name": "replace", "description": "主人改了说法：把记着的第 number 条换成新的（旧的会留在修改记录里）。",
         "input_schema": {"type": "object", "properties": {
             "number": {"type": "integer"}, "note": {"type": "string"}, "quote": quote},
             "required": ["number", "note"]}},
        {"name": "forget",
         "description": "删掉一条记着的事（主人不要了、或者记错了）。number 是“你记得的关于主人的事”里的编号。",
         "input_schema": {"type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]}},
        {"name": "track",
         "description": "记下你答应主人、要以后办的事（不是交给帮手的任务），比如“晚上提醒我…”“等 Mac 上线再发”。"
                        "提醒类一定写 due（第一次到点的时间），到时候会叫你决定要不要说；每天、每周重复的再写 every。",
         "input_schema": {"type": "object", "properties": {
             "title": {"type": "string", "description": "答应了什么，一句话"},
             "detail": {"type": "string"},
             "due": {"type": "string", "description": "什么时候办，写成 YYYY-MM-DD HH:MM（主人所在时区）；没有就不写"},
             "every": {"type": "string", "enum": ["day", "week"], "description": "每天或每周重复；不重复就不写"}},
             "required": ["title"]}},
        {"name": "resolve", "description": "答应的事办完了、或者主人不要了：按编号（p 开头）关掉。",
         "input_schema": {"type": "object", "properties": {
             "id": {"type": "string"}, "outcome": {"type": "string", "description": "怎么了结的，一句话"}},
             "required": ["id"]}},
        {"name": "just_reply", "description": "这轮不作承诺，只回答或询问。",
         "input_schema": {"type": "object", "properties": {}}},
    ]


VIEW_TOOLS = [
    {"name": "task_detail", "description": "查一件事现在怎么样了：状态、谁在做、最近几步进展、做完的结果。只是看，不改它。",
     "input_schema": {"type": "object", "properties": {
         "card": {"type": "string", "description": "那件事的编号，连同前面的字母，比如 c3"}}, "required": ["card"]}},
    {"name": "recall", "description": "翻以前的记录：跟主人聊过的话、记忆的修改记录、最近的事和帮手交回的结果。",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "要找的关键词，几个词用空格分开"}}, "required": ["query"]}},
]


# ---------- 记录器：只校验，不执行 ----------

class Recorder:
    """承诺类工具的替身。校验规则照线上的意思写；通过就返回“已接收”的事实，什么都不执行。"""

    def __init__(self, world: Dict[str, Any]):
        self.world = world
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def _card(self, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        tasks = self.world["tasks"]
        if args.get("card"):
            return next((task for task in tasks if task["card"] == args["card"]), None)
        running = [task for task in tasks if task["state"] in ("queued", "running", "waiting")]
        return running[0] if len(running) == 1 else None

    def call(self, name: str, args: Dict[str, Any]) -> Tuple[str, bool]:
        self.calls.append((name, args))
        memories = self.world["memories"]
        if name == "start_task":
            if args.get("agent") not in self.world["helpers"]:
                return "操作未完成：没有叫 %s 的帮手。" % args.get("agent"), True
            if not args.get("title") or not args.get("task"):
                return "操作未完成：缺少标题或交代的内容。", True
            return "决定已落实：类型 start_task；任务 c100；当前状态 queued。", False
        if name in ("amend_task", "cancel_task"):
            task = self._card(args)
            if task is None or task["state"] not in ("queued", "running", "waiting"):
                return "操作未完成：没有找到这件正在做的事。", True
            if name == "amend_task":
                if args.get("mode") not in ("redo", "after") or not args.get("task"):
                    return "操作未完成：补充的内容或方式不对。", True
                return "决定已落实：类型 amend_task；任务 %s；当前状态 %s。" % (task["card"], task["state"]), False
            return "决定已落实：类型 cancel_task；任务 %s；当前状态 stopping（已请求停止，尚未确认）。" % task["card"], False
        if name == "remember":
            if not args.get("note") or args.get("kind") not in ("偏好", "规矩", "事实", "承诺"):
                return "操作未完成：要记的内容或类别不对。", True
            return "已记下第 %d 条：%s" % (len(memories) + 1, args["note"]), False
        if name in ("replace", "forget"):
            number = args.get("number")
            if not isinstance(number, int) or not 1 <= number <= len(memories):
                return "操作未完成：没有第 %s 条。" % number, True
            if name == "replace":
                if not args.get("note"):
                    return "操作未完成：缺少新的内容。", True
                return "已把第 %d 条改成：%s" % (number, args["note"]), False
            return "已删掉第 %d 条。" % number, False
        if name == "track":
            due = args.get("due")
            if not args.get("title"):
                return "操作未完成：缺少要答应的事。", True
            if due and not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", due):
                return "操作未完成：时间格式应为 YYYY-MM-DD HH:MM。", True
            return "已记下约定 p1：%s%s" % (args["title"], ("，%s" % due) if due else ""), False
        if name == "resolve":
            if args.get("id") not in {item["id"] for item in self.world["promises"]}:
                return "操作未完成：没有这个约定。", True
            return "已关掉约定 %s。" % args["id"], False
        return "操作未完成：不认识的工具 %s。" % name, True

    def view(self, name: str, args: Dict[str, Any], case: Dict[str, Any]) -> str:
        if name == "task_detail":
            task = next((task for task in self.world["tasks"] if task["card"] == args.get("card")), None)
            return json.dumps(task, ensure_ascii=False) if task else "没有这件事。"
        if name == "recall":
            words = [word for word in str(args.get("query", "")).split() if word]
            pool = [item["text"] for item in case["history"]] + self.world["memories"] + [
                json.dumps(task, ensure_ascii=False) for task in self.world["tasks"]]
            hits = [text for text in pool if any(word in text for word in words)]
            return "\n".join(hits[:5]) or "没有找到。"
        return "不认识的工具。"


# ---------- 模型接口 ----------

class Provider:
    """DeepSeek 的 Anthropic 兼容接口。"""

    def __init__(self, base: str, key: str):
        base = base.rstrip("/")
        self.url = (base if base.endswith("/v1") else base + "/v1") + "/messages"
        self.key = key

    def _request(self, body: Dict[str, Any], stream: bool) -> urllib.request.Request:
        request = urllib.request.Request(self.url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                         method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "text/event-stream" if stream else "application/json")
        request.add_header("x-api-key", self.key)
        request.add_header("anthropic-version", "2023-06-01")
        return request

    def complete(self, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        with urllib.request.urlopen(self._request(body, False), timeout=timeout) as response:
            return json.loads(response.read(262145))

    def stream(self, body: Dict[str, Any], timeout: float, on_event: Callable[[Dict[str, Any]], bool]) -> None:
        """逐个事件交给 on_event；它返回 False 就停止读取并断开（模拟线上丢弃）。"""
        with urllib.request.urlopen(self._request(body, True), timeout=timeout) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                if not on_event(json.loads(payload)):
                    sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                    if isinstance(sock, socket.socket):
                        try:
                            sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    return


class FakeProvider:
    """--dry-run 用：不联网。决定直接选用例的标准答案，说话返回一段固定文字。"""

    def __init__(self, case_lookup: Callable[[], Dict[str, Any]], seed: int = 7):
        self.case = case_lookup
        self.random = random.Random(seed)

    def complete(self, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        time.sleep(self.random.uniform(0.6, 1.8))
        case = self.case()
        name = case["expected"]["decision"]
        world = world_of(case)
        hint = case["expected"].get("args_hint", "")
        owner = "\n".join(case["owner"])
        args: Dict[str, Any] = {}
        if name == "start_task":
            match = re.search(r"agent=(\w+)", hint)
            args = {"agent": match.group(1) if match else world["helpers"][0], "title": owner[:24], "task": owner}
        elif name == "amend_task":
            args = {"task": owner, "mode": "redo" if "redo" in hint else "after"}
        elif name == "remember":
            args = {"note": owner, "kind": "规矩" if "规矩" in hint else "偏好"}
        elif name in ("replace", "forget"):
            match = re.search(r"number=(\d+)", hint)
            args = {"number": int(match.group(1)) if match else 1}
            if name == "replace":
                args["note"] = owner
        elif name == "track":
            match = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", hint)
            args = {"title": owner[:24]}
            if match:
                args["due"] = match.group(0)
        call = {"type": "tool_use", "id": "toolu_dry", "name": name, "input": args}
        return {"content": [call], "usage": {"input_tokens": 3000, "output_tokens": 40}}

    def stream(self, body: Dict[str, Any], timeout: float, on_event: Callable[[Dict[str, Any]], bool]) -> None:
        events = [{"type": "message_start", "message": {"usage": {"input_tokens": 3000}}}]
        for piece in ["（模拟）", "好的。", "\n---\n", "第二条。"]:
            events.append({"type": "content_block_delta", "delta": {"type": "text_delta", "text": piece}})
        events.append({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 20}})
        time.sleep(self.random.uniform(0.3, 0.6))
        for event in events:
            time.sleep(0.05)
            if not on_event(event):
                return


# ---------- 一次调用 ----------

def decide(provider: Any, model: str, system: str, messages: List[Dict[str, Any]], world: Dict[str, Any],
           recorder: Recorder, began: float, deadline: float, wait_cap: float) -> Dict[str, Any]:
    """照 loop/decision.py：限时内拿到答案就校验并交给记录器；超时就当没定。
    超时的请求继续在后台跑完，只为记下真实耗时（最多再等 wait_cap 秒）。"""
    body = {"model": model, "stream": False, "max_tokens": 512, "thinking": {"type": "disabled"},
            "tools": commitment_tools(world), "system": system + "\n\n" + DECIDE_RULES, "messages": messages}
    box: Dict[str, Any] = {}
    done = threading.Event()

    def fetch() -> None:
        try:
            box["answer"] = provider.complete(body, timeout=wait_cap)
        except Exception as error:  # 记下类型，不记内容（可能带请求信息）
            box["error"] = type(error).__name__
        box["real_ms"] = round((time.monotonic() - began) * 1000)
        done.set()

    threading.Thread(target=fetch, daemon=True).start()
    arrived = done.wait(deadline)
    result: Dict[str, Any] = {"status": "reply", "name": "just_reply", "args": {}, "facts": NOTHING_DONE,
                              "messages": [], "usage": {}, "thread": done, "box": box}
    if not arrived:
        result.update(status="timeout", facts=TIMEOUT_FACTS)
        result["end_ms"] = round(deadline * 1000)
        return result
    result["end_ms"] = box["real_ms"]
    answer = box.get("answer")
    if answer is None:
        result.update(status="unavailable", facts="决定无效或已被打断，没有执行任何承诺。")
        return result
    result["usage"] = answer.get("usage") or {}
    calls = [item for item in answer.get("content", []) if isinstance(item, dict) and item.get("type") == "tool_use"]
    if len(calls) > 1:
        result.update(status="refused", facts="决定无效或已被打断，没有执行任何承诺。", name="(多个)")
        return result
    if not calls:
        return result
    call = calls[0]
    name, args = call.get("name"), call.get("input")
    allowed = {tool["name"] for tool in commitment_tools(world)}
    if name not in allowed or not isinstance(args, dict):
        result.update(status="refused", facts="决定无效或已被打断，没有执行任何承诺。", name=str(name))
        return result
    result.update(name=name, args=args)
    if name != "just_reply":
        facts, failed = recorder.call(name, args)
        result.update(status="refused" if failed else "selected", facts=facts)
    else:
        facts, failed = NOTHING_DONE, False
    result["messages"] = [{"role": "assistant", "content": [call]},
                          {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call.get("id", "toolu"),
                                                        "content": facts, "is_error": failed}]}]
    return result


def speak(provider: Any, model: str, system: str, messages: List[Dict[str, Any]], facts: str,
          decision_messages: List[Dict[str, Any]], recorder: Recorder, case: Dict[str, Any], began: float,
          timeout: float, stop: Optional[threading.Event] = None, max_rounds: int = 3) -> Dict[str, Any]:
    """照 dsh_stream：流式说话，只有查看类工具；查看工具的结果由记录器从用例状态里给出。
    时间都相对 began（毫秒）。stop 被设上就停止读取（P 组丢弃缓存时用）。"""
    full_system = system + "\n\n这一轮已经发生的事实：" + facts + SPEAK_RULES
    convo = list(messages) + list(decision_messages)
    out: Dict[str, Any] = {"text": "", "first_token_ms": None, "first_bubble_ms": None, "end_ms": None,
                           "views": [], "usage": {"input_tokens": 0, "output_tokens": 0}, "error": None,
                           "stopped": False}
    for _ in range(max_rounds):
        calls: List[Dict[str, Any]] = []
        current: Dict[str, Any] = {}
        state = {"stop_reason": None}

        def on_event(event: Dict[str, Any]) -> bool:
            if stop is not None and stop.is_set():
                out["stopped"] = True
                return False
            kind = event.get("type")
            now = round((time.monotonic() - began) * 1000)
            if kind == "message_start":
                usage = (event.get("message") or {}).get("usage") or {}
                out["usage"]["input_tokens"] += usage.get("input_tokens", 0) or 0
            elif kind == "content_block_start":
                block = event.get("content_block") or {}
                if block.get("type") == "tool_use":
                    current.clear()
                    current.update(id=block.get("id"), name=block.get("name"), json="")
                    calls.append(current.copy())
            elif kind == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    piece = delta.get("text", "")
                    if piece and out["first_token_ms"] is None:
                        out["first_token_ms"] = now
                    out["text"] += piece
                    if out["first_bubble_ms"] is None and BUBBLE.search(out["text"]) and \
                            BUBBLE.split(out["text"])[0].strip():
                        out["first_bubble_ms"] = now
                elif delta.get("type") == "input_json_delta" and calls:
                    calls[-1]["json"] += delta.get("partial_json", "")
            elif kind == "message_delta":
                state["stop_reason"] = (event.get("delta") or {}).get("stop_reason")
                usage = event.get("usage") or {}
                out["usage"]["output_tokens"] += usage.get("output_tokens", 0) or 0
            elif kind == "error":
                raise RuntimeError("流式接口返回错误")
            return True

        body = {"model": model, "stream": True, "max_tokens": 2048, "thinking": {"type": "disabled"},
                "system": full_system, "messages": convo, "tools": VIEW_TOOLS}
        try:
            provider.stream(body, timeout, on_event)
        except Exception as error:
            out["error"] = type(error).__name__
            break
        if out["stopped"] or state["stop_reason"] != "tool_use" or not calls:
            break
        assistant: List[Dict[str, Any]] = []
        results: List[Dict[str, Any]] = []
        for call in calls:
            try:
                args = json.loads(call["json"] or "{}")
            except ValueError:
                args = {}
            answer = recorder.view(call["name"], args, case)
            out["views"].append({"name": call["name"], "args": args})
            assistant.append({"type": "tool_use", "id": call["id"], "name": call["name"], "input": args})
            results.append({"type": "tool_result", "tool_use_id": call["id"], "content": answer})
        convo += [{"role": "assistant", "content": assistant}, {"role": "user", "content": results}]
    out["end_ms"] = round((time.monotonic() - began) * 1000)
    if out["first_bubble_ms"] is None and out["text"].strip() and not out["stopped"]:
        out["first_bubble_ms"] = out["end_ms"]  # 只有一条气泡：说完才算完整
    return out


# ---------- 两个实验组 ----------

RELEASE = ("reply", "timeout", "unavailable")


def run_serial(provider: Any, args: argparse.Namespace, case: Dict[str, Any], persona: str) -> Dict[str, Any]:
    world = world_of(case)
    system, messages, recorder = system_prompt(persona, world), messages_of(case), Recorder(world)
    began = time.monotonic()
    decision = decide(provider, args.decide_model, system, messages, world, recorder, began,
                      args.deadline, args.decide_wait)
    speech = speak(provider, args.speak_model, system, messages, decision["facts"], decision["messages"],
                   recorder, case, began, args.speak_timeout)
    return finish("S", case, decision, speech, None, speech.get("first_bubble_ms"))


def run_parallel(provider: Any, args: argparse.Namespace, case: Dict[str, Any], persona: str) -> Dict[str, Any]:
    world = world_of(case)
    system, messages, recorder = system_prompt(persona, world), messages_of(case), Recorder(world)
    began = time.monotonic()
    stop = threading.Event()
    held: Dict[str, Any] = {}

    def guess() -> None:
        held.update(speak(provider, args.speak_model, system, messages, SPECULATIVE_FACTS, [], recorder, case,
                          began, args.speak_timeout, stop=stop))

    speculative = threading.Thread(target=guess, daemon=True)
    speculative.start()
    decision = decide(provider, args.decide_model, system, messages, world, recorder, began,
                      args.deadline, args.decide_wait)
    if decision["status"] in RELEASE:
        speculative.join(args.speak_timeout + 5)
        first = held.get("first_bubble_ms")
        release = max(decision["end_ms"], first) if first is not None else None
        return finish("P", case, decision, held, None, release)
    stop.set()  # 承诺被执行或被拒：丢弃缓存，带着实际结果重说
    speculative.join(5)
    speech = speak(provider, args.speak_model, system, messages, decision["facts"], decision["messages"],
                   recorder, case, began, args.speak_timeout)
    return finish("P", case, decision, speech, dict(held), speech.get("first_bubble_ms"))


def finish(arm: str, case: Dict[str, Any], decision: Dict[str, Any], speech: Dict[str, Any],
           discarded: Optional[Dict[str, Any]], release: Optional[int]) -> Dict[str, Any]:
    expected = case["expected"]
    acceptable = {expected["decision"]} | {item.split("：")[0].split(":")[0].strip()
                                           for item in expected.get("also_ok", [])}
    return {
        "arm": arm, "case": case["id"], "category": case["category"],
        "decision": {"status": decision["status"], "name": decision["name"], "args": decision["args"],
                     "facts": decision["facts"], "end_ms": decision["end_ms"], "usage": decision["usage"],
                     "expected": expected["decision"], "acceptable": sorted(acceptable),
                     "match": decision["name"] in acceptable},
        "speech": {key: speech.get(key) for key in
                   ("text", "first_token_ms", "first_bubble_ms", "end_ms", "views", "usage", "error")},
        "discarded": None if discarded is None else {
            "chars": len(discarded.get("text") or ""), "usage": discarded.get("usage"),
            "first_bubble_ms": discarded.get("first_bubble_ms")},
        "release_ms": release,
        "_decision_thread": decision["thread"], "_decision_box": decision["box"],
    }


# ---------- 命令 ----------

def inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT)
        return True
    except ValueError:
        return False


def read_key(path: str) -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        file = Path(os.path.expanduser(path))
        if file.is_file():
            key = file.read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit("没有密钥：设 DEEPSEEK_API_KEY，或用 --key-file 指向密钥文件。")
    return key


def git_head() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def command_run(args: argparse.Namespace) -> int:
    out = Path(os.path.expanduser(args.out))
    if inside_repo(out):
        raise SystemExit("结果文件必须放在仓库外：%s" % out)
    cases = load_cases(Path(args.cases), args.only.split(",") if args.only else None)
    persona = Path(args.persona).read_text(encoding="utf-8")
    current: Dict[str, Any] = {}
    provider: Any = FakeProvider(lambda: current["case"]) if args.dry_run else Provider(args.base_url, read_key(args.key_file))
    arms = {"S": run_serial, "P": run_parallel}
    chosen = [arm for arm in args.arms.split(",") if arm]
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = {"commit": git_head(), "decide_model": args.decide_model, "speak_model": args.speak_model,
            "deadline_s": args.deadline, "dry_run": args.dry_run, "persona": str(Path(args.persona)),
            "note": args.note}
    total = len(cases) * args.repeats * len(chosen)
    done = 0
    finished = set()
    if out.exists():
        # 接着上次中断的地方跑：已经写过的（用例、组、第几次）不再重跑，失败和慢样本也不重跑
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                finished.add((row.get("case"), row.get("arm"), row.get("repeat")))
    with out.open("a", encoding="utf-8") as sink:
        for index, case in enumerate(cases):
            current["case"] = case
            for repeat in range(args.repeats):
                order = chosen if (index + repeat) % 2 == 0 else list(reversed(chosen))
                for arm in order:
                    if (case["id"], arm, repeat) in finished:
                        done += 1
                        continue
                    started = time.time()
                    try:
                        record = arms[arm](provider, args, case, persona)
                    except Exception as error:
                        record = {"arm": arm, "case": case["id"], "category": case["category"],
                                  "error": type(error).__name__, "release_ms": None}
                    thread = record.pop("_decision_thread", None)
                    box = record.pop("_decision_box", None)
                    if thread is not None:
                        thread.wait(args.decide_wait)  # 超时的决定也等它跑完，只为记下真实耗时
                        record["decision"]["real_ms"] = (box or {}).get("real_ms")
                        record["decision"]["fetch_error"] = (box or {}).get("error")
                        if record["decision"]["status"] == "timeout":
                            # 超时的决定没有被执行；正确率按它最后给出的答案算，另记一项
                            late = [item.get("name") for item in ((box or {}).get("answer") or {}).get("content", [])
                                    if isinstance(item, dict) and item.get("type") == "tool_use"]
                            record["decision"]["late_name"] = late[0] if len(late) == 1 else (
                                "just_reply" if not late and (box or {}).get("answer") else None)
                            record["decision"]["match"] = record["decision"]["late_name"] in \
                                record["decision"]["acceptable"]
                    record.update(repeat=repeat, started_at=started, **meta)
                    sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                    sink.flush()
                    done += 1
                    print("[%d/%d] %s %s #%d release=%s decide=%s/%s" % (
                        done, total, case["id"], arm, repeat, record.get("release_ms"),
                        (record.get("decision") or {}).get("status"), (record.get("decision") or {}).get("name")),
                        flush=True)
                    time.sleep(args.pause)
    return 0


def percentile(values: List[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(round(fraction * len(ordered) + 0.5)) - 1))]


def command_summarize(args: argparse.Namespace) -> int:
    rows = [json.loads(line) for line in Path(os.path.expanduser(args.results)).read_text(encoding="utf-8").splitlines()
            if line.strip()]
    price_in, price_out = args.price_in / 1e6, args.price_out / 1e6
    report: Dict[str, Any] = {}
    for arm in sorted({row["arm"] for row in rows}):
        mine = [row for row in rows if row["arm"] == arm]
        release = [row["release_ms"] for row in mine if row.get("release_ms") is not None]
        decisions = [row["decision"] for row in mine if row.get("decision")]
        cost = 0.0
        wasted = 0
        for row in mine:
            for usage in ((row.get("decision") or {}).get("usage") or {}, (row.get("speech") or {}).get("usage") or {}):
                cost += usage.get("input_tokens", 0) * price_in + usage.get("output_tokens", 0) * price_out
            if row.get("discarded"):
                wasted += 1
                usage = row["discarded"].get("usage") or {}
                cost += usage.get("input_tokens", 0) * price_in + usage.get("output_tokens", 0) * price_out
        report[arm] = {
            "runs": len(mine), "errors": sum(1 for row in mine if row.get("error") or (row.get("speech") or {}).get("error")),
            "no_bubble": len(mine) - len(release),
            "release_ms": {"p50": statistics.median(release) if release else None,
                           "p95": percentile(release, 0.95), "max": max(release) if release else None,
                           "over_3000": sum(1 for value in release if value > 3000)},
            "decide_timeouts": sum(1 for item in decisions if item["status"] == "timeout"),
            "decide_real_ms_p50": statistics.median([item["real_ms"] for item in decisions if item.get("real_ms")])
            if any(item.get("real_ms") for item in decisions) else None,
            "decision_match": sum(1 for item in decisions if item.get("match")),
            "discarded": wasted, "cost_estimate": round(cost, 4),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def command_review(args: argparse.Namespace) -> int:
    """给主人盲评：每个用例取两组第一次运行的回复，随机排成 A/B；对应关系另存一个文件。"""
    rows = [json.loads(line) for line in Path(os.path.expanduser(args.results)).read_text(encoding="utf-8").splitlines()
            if line.strip()]
    cases = {case["id"]: case for case in load_cases(Path(args.cases), None)}
    folder = Path(os.path.expanduser(args.out_dir))
    if inside_repo(folder):
        raise SystemExit("评审文件必须放在仓库外：%s" % folder)
    folder.mkdir(parents=True, exist_ok=True)
    picked: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if row.get("repeat") == 0 and row.get("speech"):
            picked.setdefault(row["case"], {})[row["arm"]] = row["speech"]["text"]
    chooser = random.Random(args.seed)
    sheet = ["# 盲评：每个用例选更好的一个，或写“平”", ""]
    key: Dict[str, Dict[str, str]] = {}
    for case_id in [case_id for case_id in cases if case_id in picked and len(picked[case_id]) == 2]:
        case = cases[case_id]
        arms = ["S", "P"]
        chooser.shuffle(arms)
        key[case_id] = {"A": arms[0], "B": arms[1]}
        sheet += ["## %s（%s）" % (case_id, case["category"]), ""]
        for item in case["history"][-4:]:
            sheet.append("> %s：%s" % ("主人" if item["role"] == "owner" else "小鱼", item["text"].replace("\n", " ")))
        sheet += ["> 主人：%s" % " / ".join(case["owner"]), ""]
        for label, arm in zip("AB", arms):
            bubbles = [part.strip() for part in BUBBLE.split(picked[case_id][arm]) if part.strip()]
            sheet.append("**%s**" % label)
            sheet += ["- %s" % bubble.replace("\n", " ") for bubble in bubbles] or ["- （没有回复）"]
            sheet.append("")
        sheet += ["选择：", ""]
    (folder / "blind-review.md").write_text("\n".join(sheet), encoding="utf-8")
    (folder / "blind-review-key.json").write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")
    print("写好了：%s（评完再看 %s）" % (folder / "blind-review.md", folder / "blind-review-key.json"))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="先定再说：串行与推测并行对比实验")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="跑实验，每次运行写一行结果")
    run.add_argument("--out", required=True, help="结果文件（必须在仓库外，追加写入）")
    run.add_argument("--cases", default=str(CASES))
    run.add_argument("--only", help="只跑这些用例，逗号分隔")
    run.add_argument("--arms", default="S,P")
    run.add_argument("--repeats", type=int, default=5)
    run.add_argument("--persona", default=str(ROOT / "runtime" / "persona.txt"))
    run.add_argument("--decide-model", default="deepseek-flash")
    run.add_argument("--speak-model", default="deepseek-flash")
    run.add_argument("--base-url", default=os.environ.get("DEEPSEEK_BASE_URL") or BASE_URL)
    run.add_argument("--key-file", default=KEY_FILE)
    run.add_argument("--deadline", type=float, default=DECIDE_DEADLINE, help="决定期限（秒），线上是 1.3")
    run.add_argument("--decide-wait", type=float, default=30.0, help="超时的决定最多再等多久，只为记真实耗时")
    run.add_argument("--speak-timeout", type=float, default=60.0)
    run.add_argument("--pause", type=float, default=1.0, help="两次运行之间停几秒")
    run.add_argument("--note", default="", help="记进每一行的备注，比如版本组合")
    run.add_argument("--dry-run", action="store_true", help="不联网，用假模型检查流程和记录器")
    summary = sub.add_parser("summarize", help="按组汇总时延、决定、丢弃和费用")
    summary.add_argument("results")
    summary.add_argument("--price-in", type=float, default=2.0, help="每百万输入 token 的单价")
    summary.add_argument("--price-out", type=float, default=8.0, help="每百万输出 token 的单价")
    review = sub.add_parser("review", help="生成主人盲评用的对照表")
    review.add_argument("results")
    review.add_argument("--out-dir", required=True)
    review.add_argument("--cases", default=str(CASES))
    review.add_argument("--seed", type=int, default=20261027)
    args = parser.parse_args(argv)
    return {"run": command_run, "summarize": command_summarize, "review": command_review}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
