"""先定再说：串行（S）与推测并行（P）对比实验。

方案见 docs/development/decide-speak-experiment.zh_CN.md。用例见同目录的
decide_speak_cases.jsonl。

    python3 tools/experiments/decide_speak_compare.py run --out ~/xy-exp/results.jsonl
    python3 tools/experiments/decide_speak_compare.py run --dry-run --only ack-01,task-01,chat-02 --out /tmp/x.jsonl
    python3 tools/experiments/decide_speak_compare.py summarize ~/xy-exp/results.jsonl
    python3 tools/experiments/decide_speak_compare.py review ~/xy-exp/results.jsonl --out-dir ~/xy-exp/review
    python3 tools/experiments/decide_speak_compare.py respeak ~/xy-exp/results.jsonl --out ~/xy-exp/respeak.jsonl

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

# 说话规则 v2：按 2026-10-27 主人盲评备注改写，只在实验里试，不是 R7 现行规则。
# 事实写成内部状态，不让模型复述“这轮不能做什么”；示例刻意不取自用例，免得泄题。
FACTS_V2 = {
    NOTHING_DONE: "没有新办理任务、提醒或记忆。主人没问起就不提办理的事。",
    TIMEOUT_FACTS: "这次没来得及办理任何事，不能说已经安排或记下。主人没问起就不提办理的事。",
    SPECULATIVE_FACTS: "还不知道这次要不要办事。不要提任务、提醒、记忆的办理状态，只接话。",
    "决定无效或已被打断，没有执行任何承诺。": "这次没有办成任何事，不能说已经安排或记下。",
}
SPEAK_RULES_V2 = """

## 这次的内部状态（只供你判断，不要原样复述）
{facts}

## 怎么回话
- 现在只说话或查记录，不再启动任务、改记忆或作新约定。
- 办了的事一句话说清：谁接了、现在什么状态。不复述交代给帮手的内容，不预告它会怎么做，主人问了再说。
- 影响真假的状态不能省：排队还是在跑、停止有没有确认、提醒定在几点。
- 没办成就直说没成和现在的真实状态，该道歉就简短道歉，不讲内部过程和原因分析。
- 不提“这轮”“这一阶段”“我不能启动任务”这类内部限制。
- 主人带着情绪（累、烦、开心）时，第一句先接情绪，再说事。
- 意思说一次就够：不重复主人刚说的话，确认之后不再追加限制或保证，不用“不是……而是……”绕着说，不说没来由的“我在”。
- 问什么答什么：问日期就给日期，问来不来得及就一两句。
- 用单独一行的 `---` 分成 1～3 个短气泡，每个气泡一件事；第一个气泡尽量短，十几个字以内。一句话说得完就不分。
- 口语一点，可以偶尔用“～”或一个表情，不要每句都用。
- 不编造自己正在做的事、看过的东西或经历。

## 语气示例（只学长短和语气，不要照抄内容）
主人：明天下午三点提醒我取快递 / 算了改四点
小鱼：好，改成四点了，到时候叫你～

主人：累死了，帮我让 cc 把日志打包一下
小鱼：辛苦啦，先歇会儿
---
交给 cc 了，c7，在排队，好了叫你

主人：列表和元组区别是啥，一句话
小鱼：列表能改，元组不能改～

主人：你刚才不是说记下了吗
小鱼：抱歉，那条其实没记上
---
现在记好了"""
# v3：v2 盲评后（v2 35 胜、r7 5 胜、平 6），主人嫌“～”太多、答完还补半句；只改这几处，未经实测。
SPEAK_RULES_V3 = (SPEAK_RULES_V2
                  .replace("问来不来得及就一两句。", "问来不来得及就一两句。答完就停，不补一句解释或推算过程。")
                  .replace("- 口语一点，可以偶尔用“～”或一个表情，不要每句都用。",
                           "- 口语一点。“～”和表情很少用，大约十次回话里一两次，不要当成句尾习惯。")
                  .replace("到时候叫你～", "到时候叫你")
                  .replace("元组不能改～", "元组不能改"))
SPEAK_RULESETS = ("r7", "v2", "v3")


def speak_system(system: str, facts: str, rules: str = "r7") -> str:
    if rules in ("v2", "v3"):
        text = SPEAK_RULES_V2 if rules == "v2" else SPEAK_RULES_V3
        return system + text.format(facts=FACTS_V2.get(facts, facts))
    return system + "\n\n这一轮已经发生的事实：" + facts + SPEAK_RULES


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
           recorder: Recorder, began: float, deadline: Optional[float], wait_cap: float) -> Dict[str, Any]:
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
          timeout: float, stop: Optional[threading.Event] = None, max_rounds: int = 3,
          rules: str = "r7") -> Dict[str, Any]:
    """照 dsh_stream：流式说话，只有查看类工具；查看工具的结果由记录器从用例状态里给出。
    时间都相对 began（毫秒）。stop 被设上就停止读取（P 组丢弃缓存时用）。"""
    full_system = speak_system(system, facts, rules)
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
    if not out["error"] and not out["stopped"] and state["stop_reason"] != "end_turn":
        out["error"] = "IncompleteStream"
    out["end_ms"] = round((time.monotonic() - began) * 1000)
    if (out["first_bubble_ms"] is None and out["text"].strip() and not out["stopped"]
            and not out["error"] and state["stop_reason"] == "end_turn"):
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
                   recorder, case, began, args.speak_timeout, rules=args.speak_rules)
    return finish("S", case, decision, speech, None, speech.get("first_bubble_ms"))


def run_parallel(provider: Any, args: argparse.Namespace, case: Dict[str, Any], persona: str) -> Dict[str, Any]:
    world = world_of(case)
    system, messages, recorder = system_prompt(persona, world), messages_of(case), Recorder(world)
    began = time.monotonic()
    stop = threading.Event()
    held: Dict[str, Any] = {}

    def guess() -> None:
        held.update(speak(provider, args.speak_model, system, messages, SPECULATIVE_FACTS, [], recorder, case,
                          began, args.speak_timeout, stop=stop, rules=args.speak_rules))

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
    speech = speak(provider, args.speak_model, system, messages, decision["facts"], decision["messages"],
                   recorder, case, began, args.speak_timeout, rules=args.speak_rules)
    # Cleanup must not delay the replacement request or inflate its release time.
    speculative.join(5)
    discarded = dict(held)
    discarded["usage_complete"] = not speculative.is_alive() and not held.get("stopped", False)
    return finish("P", case, decision, speech, discarded, speech.get("first_bubble_ms"))


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
            "first_bubble_ms": discarded.get("first_bubble_ms"),
            "usage_complete": discarded.get("usage_complete", True)},
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
                        # Late calls still consume tokens even though no commitment executes.
                        record["decision"]["usage"] = ((box or {}).get("answer") or {}).get("usage") or {}
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


def replayed_turn(decision: Dict[str, Any], rules: str) -> Tuple[str, List[Dict[str, Any]]]:
    """把记录下的决定还原成说话阶段看到的事实和工具往返。不再调用决定模型，也不再执行承诺。
    原始 tool_use id 没有记录，统一写成 toolu_replay；两种规则用同一份还原，比较不受影响。"""
    facts = decision["facts"]
    shown = FACTS_V2.get(facts, facts) if rules in ("v2", "v3") else facts
    name = decision.get("name")
    if decision.get("status") not in ("reply", "selected", "refused") or name in (None, "(多个)"):
        return facts, []
    call = {"type": "tool_use", "id": "toolu_replay", "name": name, "input": decision.get("args") or {}}
    result = {"type": "tool_result", "tool_use_id": "toolu_replay", "content": shown,
              "is_error": decision.get("status") == "refused"}
    return facts, [{"role": "assistant", "content": [call]}, {"role": "user", "content": [result]}]


def command_respeak(args: argparse.Namespace) -> int:
    """只重跑说话：复用上一轮某一组记录的决定，用不同的说话规则各说一次。"""
    source = Path(os.path.expanduser(args.results))
    out = Path(os.path.expanduser(args.out))
    if inside_repo(out):
        raise SystemExit("结果文件必须放在仓库外：%s" % out)
    rules = [item for item in args.rules.split(",") if item]
    unknown = set(rules) - set(SPEAK_RULESETS)
    if unknown:
        raise SystemExit("不认识的说话规则：%s" % ", ".join(sorted(unknown)))
    decisions = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        row = json.loads(line) if line.strip() else {}
        if row.get("arm") == args.from_arm and row.get("repeat") == 0 and row.get("decision"):
            decisions[row["case"]] = row["decision"]
    cases = [case for case in load_cases(Path(args.cases), args.only.split(",") if args.only else None)
             if case["id"] in decisions]
    persona = Path(args.persona).read_text(encoding="utf-8")
    current: Dict[str, Any] = {}
    provider: Any = FakeProvider(lambda: current["case"]) if args.dry_run else Provider(args.base_url, read_key(args.key_file))
    meta = {"commit": git_head(), "speak_model": args.speak_model, "dry_run": args.dry_run,
            "persona": str(Path(args.persona)), "replayed_from": "%s:%s" % (source, args.from_arm), "note": args.note}
    finished = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                finished.add((row.get("case"), row.get("arm"), row.get("repeat")))
    out.parent.mkdir(parents=True, exist_ok=True)
    total, done = len(cases) * args.repeats * len(rules), 0
    with out.open("a", encoding="utf-8") as sink:
        for index, case in enumerate(cases):
            current["case"] = case
            decision = decisions[case["id"]]
            for repeat in range(args.repeats):
                for rule in (rules if (index + repeat) % 2 == 0 else list(reversed(rules))):
                    done += 1
                    if (case["id"], rule, repeat) in finished:
                        continue
                    world = world_of(case)
                    facts, turn = replayed_turn(decision, rule)
                    began, started = time.monotonic(), time.time()
                    speech = speak(provider, args.speak_model, system_prompt(persona, world), messages_of(case),
                                   facts, turn, Recorder(world), case, began, args.speak_timeout, rules=rule)
                    record = {
                        "arm": rule, "case": case["id"], "category": case["category"],
                        "decision": {key: decision.get(key) for key in
                                     ("status", "name", "args", "facts", "expected", "acceptable", "match")},
                        "speech": {key: speech.get(key) for key in
                                   ("text", "first_token_ms", "first_bubble_ms", "end_ms", "views", "usage", "error")},
                        "discarded": None,
                        # 从说话开始计时，不含决定耗时
                        "release_ms": speech.get("first_bubble_ms"),
                        "repeat": repeat, "started_at": started, **meta}
                    sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                    sink.flush()
                    print("[%d/%d] %s %s #%d bubble=%s chars=%d" % (
                        done, total, case["id"], rule, repeat, record["release_ms"], len(speech.get("text") or "")),
                        flush=True)
                    time.sleep(args.pause)
    return 0


STYLE_MARKERS = {"这轮": r"这一?轮", "不是而是": r"不是[^。！？\n]{0,30}而是", "我在": r"我在[。！，呢]|^我在$"}


def style_of(text: str) -> Dict[str, Any]:
    bubbles = [part.strip() for part in BUBBLE.split(text or "") if part.strip()]
    return {"chars": len(re.sub(r"\s|---", "", text or "")), "bubbles": len(bubbles),
            "first_chars": len(bubbles[0]) if bubbles else 0,
            **{name: bool(re.search(pattern, text or "", re.MULTILINE)) for name, pattern in STYLE_MARKERS.items()}}


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
            "release_ms": {"mean": statistics.mean(release) if release else None,
                           "p50": statistics.median(release) if release else None,
                           "p95": percentile(release, 0.95), "max": max(release) if release else None,
                           "over_3000": sum(1 for value in release if value > 3000)},
            "decide_timeouts": sum(1 for item in decisions if item["status"] == "timeout"),
            "decide_real_ms_mean": statistics.mean([item["real_ms"] for item in decisions if item.get("real_ms")])
            if any(item.get("real_ms") for item in decisions) else None,
            "decide_real_ms_p50": statistics.median([item["real_ms"] for item in decisions if item.get("real_ms")])
            if any(item.get("real_ms") for item in decisions) else None,
            "decision_match": sum(1 for item in decisions if item.get("match")),
            "discarded": wasted, "cost_estimate": round(cost, 4),
        }
        styles = [style_of((row.get("speech") or {}).get("text") or "") for row in mine if row.get("speech")]
        if styles:
            report[arm]["style"] = {
                "chars_mean": round(statistics.mean(item["chars"] for item in styles), 1),
                "bubbles_mean": round(statistics.mean(item["bubbles"] for item in styles), 2),
                "first_bubble_chars_mean": round(statistics.mean(item["first_chars"] for item in styles), 1),
                **{"with_" + name: sum(1 for item in styles if item[name]) for name in STYLE_MARKERS}}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def review_html(sheet: str) -> str:
    """Offline annotation form; contains only the blinded sheet, never the key."""
    import hashlib
    import html
    def render(content: str) -> str:
        def state(match):
            world = json.loads(match.group(1))
            lines = []
            if world.get("now"):
                lines.append("时间：" + world["now"])
            lines.extend(world.get("memories", []))
            states = {"running": "进行中", "done": "已完成", "queued": "已排队", "waiting": "等待中"}
            for task in world.get("tasks", []):
                lines.append("任务 %s · %s · %s · %s · %s" % (
                    task["card"], task["agent"], task["title"], states.get(task["state"], task["state"]),
                    task.get("progress") or task.get("result") or ""))
            lines.extend("约定：" + item["title"] for item in world.get("promises", []))
            return "\n".join(lines) or "没有已有任务或记忆。"
        content = re.sub(r"```json\n(.*?)\n```", state, content, flags=re.DOTALL)
        rendered = []
        for line in content.splitlines():
            if line in ("**A**", "**B**"):
                rendered.append("<h3>回复 %s</h3>" % line[2])
            elif line.startswith("> "):
                rendered.append('<p class="history">%s</p>' % html.escape(line[2:]))
            elif line.startswith("- "):
                rendered.append('<p class="reply">%s</p>' % html.escape(line[2:]))
            elif line.strip():
                rendered.append('<p>%s</p>' % html.escape(line))
        return "\n".join(rendered)

    sections = re.split(r"^## ", sheet, flags=re.MULTILINE)[1:]
    cards = []
    for section in sections:
        heading, _, content = section.partition("\n")
        case_id = heading.split("（", 1)[0].strip()
        content = content.split("选择（填 A / B / 平）：", 1)[0]
        cards.append('<section data-case="%s"><h2>%s</h2><div>%s</div>' % (
            html.escape(case_id, quote=True), html.escape(heading), render(content)))
        cards.append('<fieldset><legend>哪个回复更好？</legend>' + ''.join(
            '<label><input type="radio" name="%s" value="%s">%s</label>' % (case_id, value, label)
            for value, label in (("A", "A 更好"), ("B", "B 更好"), ("tie", "平"))))
        cards.append('</fieldset><label class="notes">备注（可选）<textarea rows="2"></textarea></label></section>')
    storage = "decide-speak-review-" + hashlib.sha256(sheet.encode()).hexdigest()[:16]
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>小鱼回复盲评</title><meta name="claude:viewport-fit-sides" content="cover"><style>
/* 阅读卡片：一列题目，回复用左侧色条区分；颜色全走变量，跟随明暗主题 */
:root{--bg:#f3f4f1;--card:#ffffff;--fg:#1f2421;--muted:#5b625d;--line:#d5d9d4;--reply:#eef3ef;--accent:#2f6a4f;--on-accent:#ffffff}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#151816;--card:#1e2220;--fg:#e6eae7;--muted:#a3aba6;--line:#353b37;--reply:#233029;--accent:#6fbf96;--on-accent:#0f1a14;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#151816;--card:#1e2220;--fg:#e6eae7;--muted:#a3aba6;--line:#353b37;--reply:#233029;--accent:#6fbf96;--on-accent:#0f1a14;color-scheme:dark}
body{font-family:system-ui,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--fg);margin:0;line-height:1.65;font-size:15px}
body::before{content:"";position:fixed;inset:0 0 auto;height:env(safe-area-inset-top,0px);z-index:10;background:var(--card)}
header{position:sticky;top:env(safe-area-inset-top,0px);background:var(--card);border-bottom:1px solid var(--line);padding-block:12px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));z-index:1}
header div{max-width:960px;margin:auto;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
h1{font-size:19px;margin:0;text-wrap:balance}main{max-width:960px;margin:0 auto;padding-block:16px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));display:grid;gap:20px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:20px;min-width:0}
h2{font-size:17px;margin-top:0}p{overflow-wrap:anywhere}.history{color:var(--muted);margin:6px 0}.reply{background:var(--reply);border-left:3px solid var(--accent);padding:10px 14px}h3{margin-bottom:8px;font-size:15px}
fieldset{border:0;padding:12px 0;display:flex;flex-wrap:wrap;gap:12px 24px}fieldset label{display:inline-flex;align-items:center;gap:6px;cursor:pointer}
input{width:20px;height:20px;accent-color:var(--accent)}textarea{display:block;box-sizing:border-box;width:100%;font:inherit;padding:10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
button{background:var(--accent);color:var(--on-accent);border:0;border-radius:8px;padding:9px 14px;font:inherit;cursor:pointer}button:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
small{display:block;color:var(--muted)}#progress{margin-left:auto;font-variant-numeric:tabular-nums}</style><header><div><h1>小鱼回复盲评</h1><span id="progress"></span><button id="export">导出标注</button></div></header>
<main><p>逐题阅读对话背景和 A／B 回复，选择更好的一个，也可以选“平”。备注可留空。组别对应关系未包含在本表中。</p>
<small>选择会保存在当前浏览器。完成后点“导出标注”，把下载的 JSON 文件交回来。若下载未生效，可在页尾复制导出结果。清理浏览器数据或换浏览器前请先导出。</small>
''' + "\n".join(cards) + '''<details id="export-fallback" hidden><summary>导出结果（未下载时可复制）</summary><button id="copy" type="button">复制结果</button> <span id="copied" role="status"></span><label>把以下 JSON 交给 Claude 或 Codex<textarea id="export-data" rows="10" readonly></textarea></label></details></main><script>
const storageKey = ''' + json.dumps(storage) + ''';
const sections = [...document.querySelectorAll('section[data-case]')];
function values(){return sections.map(s=>({case:s.dataset.case,choice:s.querySelector('input:checked')?.value||null,note:s.querySelector('textarea').value}));}
function progress(){document.getElementById('progress').textContent=values().filter(x=>x.choice).length+' / '+sections.length+' 已评';}
try{const saved=JSON.parse(localStorage.getItem(storageKey)||'[]');for(const x of saved){const s=sections.find(s=>s.dataset.case===x.case);if(!s)continue;for(const r of s.querySelectorAll('input'))r.checked=r.value===x.choice;s.querySelector('textarea').value=x.note||'';}}catch{}
document.addEventListener('input',()=>{try{localStorage.setItem(storageKey,JSON.stringify(values()));}catch{}progress();});
document.getElementById('export').onclick=()=>{const data={review:storageKey,exported_at:new Date().toISOString(),answers:values()};const encoded=JSON.stringify(data,null,2);document.getElementById('export-data').value=encoded;const fallback=document.getElementById('export-fallback');fallback.hidden=false;fallback.open=true;const url=URL.createObjectURL(new Blob([encoded],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='blind-review-answers.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};document.getElementById('copy').onclick=()=>{const box=document.getElementById('export-data');const done=t=>document.getElementById('copied').textContent=t;try{navigator.clipboard.writeText(box.value).then(()=>done('已复制'),()=>{box.select();done('请手动复制选中的文字');});}catch{box.select();done('请手动复制选中的文字');}};progress();
</script></html>'''


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
        arms = sorted(picked[case_id])
        chooser.shuffle(arms)
        key[case_id] = {"A": arms[0], "B": arms[1]}
        sheet += ["## %s（%s）" % (case_id, case["category"]), ""]
        for item in case["history"]:
            sheet.append("> %s：%s" % ("主人" if item["role"] == "owner" else "小鱼", item["text"].replace("\n", " ")))
        if case.get("world"):
            sheet += ["本题已有状态：", "```json",
                      json.dumps(case["world"], ensure_ascii=False, indent=2), "```", ""]
        sheet += ["> 主人：%s" % " / ".join(case["owner"]), ""]
        for label, arm in zip("AB", arms):
            bubbles = [part.strip() for part in BUBBLE.split(picked[case_id][arm]) if part.strip()]
            sheet.append("**%s**" % label)
            sheet += ["- %s" % bubble.replace("\n", " ") for bubble in bubbles] or ["- （没有回复）"]
            sheet.append("")
        sheet += ["选择（填 A / B / 平）：", "", "备注（可选）：", ""]
    blinded = "\n".join(sheet)
    (folder / "blind-review.md").write_text(blinded, encoding="utf-8")
    public = folder / "public"
    public.mkdir(exist_ok=True)
    (public / "index.html").write_text(review_html(blinded), encoding="utf-8")
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
    run.add_argument("--deadline", type=lambda value: None if value.lower() == "none" else float(value),
                     default=DECIDE_DEADLINE, help="决定期限（秒），none 表示不设阶段期限；线上是 1.3")
    run.add_argument("--decide-wait", type=float, default=30.0, help="超时的决定最多再等多久，只为记真实耗时")
    run.add_argument("--speak-timeout", type=float, default=60.0)
    run.add_argument("--pause", type=float, default=1.0, help="两次运行之间停几秒")
    run.add_argument("--note", default="", help="记进每一行的备注，比如版本组合")
    run.add_argument("--speak-rules", choices=SPEAK_RULESETS, default="r7", help="说话规则；r7 与线上一致")
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
    again = sub.add_parser("respeak", help="复用记录下的决定，只用不同说话规则重跑说话")
    again.add_argument("results", help="上一轮 run 的结果文件")
    again.add_argument("--out", required=True, help="结果文件（必须在仓库外，追加写入）")
    again.add_argument("--from-arm", default="S", help="取哪一组第一次运行的决定")
    again.add_argument("--rules", default="r7,v2", help="要比较的说话规则，逗号分隔")
    again.add_argument("--repeats", type=int, default=2)
    again.add_argument("--cases", default=str(CASES))
    again.add_argument("--only", help="只跑这些用例，逗号分隔")
    again.add_argument("--persona", default=str(ROOT / "runtime" / "persona.txt"))
    again.add_argument("--speak-model", default="deepseek-flash")
    again.add_argument("--base-url", default=os.environ.get("DEEPSEEK_BASE_URL") or BASE_URL)
    again.add_argument("--key-file", default=KEY_FILE)
    again.add_argument("--speak-timeout", type=float, default=60.0)
    again.add_argument("--pause", type=float, default=0.5)
    again.add_argument("--note", default="")
    again.add_argument("--dry-run", action="store_true", help="不联网，用假模型检查流程")
    args = parser.parse_args(argv)
    return {"run": command_run, "summarize": command_summarize, "review": command_review,
            "respeak": command_respeak}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
