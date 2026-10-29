"""小鱼接话时手边的工具：说的话就是她直接写的文字，要 Runtime 做事就调工具。

以前小鱼每一轮只输出一个 JSON（reply、brief、mood、card、action），Runtime 再从文字里把
reply 抠出来；模型在 JSON 前后多写一句、或者包进代码块，就会把整段 JSON 露给主人。
改成工具调用以后：

- 说的话：她直接写的文字，流式给主人看，空一行就是下一条消息。
- 做事：start_task / amend_task / cancel_task。调用时就按 Xiaoyu._plan 的规则检查一遍，
  办不了的当场把原因告诉她，她在同一轮里改；办得了的先记下，这一轮说完以后由 Runtime
  照旧去办（开卡、启动帮手都用原来的代码）。一轮只能有一件要办的事。
- 查东西：task_detail 看一件事，recall 翻以前的记录；当场查、当场把结果给她，她看完接着说。
- 记事：remember / replace / forget 改她记得的关于主人的事（memory.py）；track / resolve 记下、
  关掉她答应主人的事（promises.py）。这些当场就生效。

only 可以只给一部分工具（后台整理记忆、主动说话那几轮用）。

这里只管“有哪些工具、调用了怎么样”，不碰网络和模型：dsh_stream 负责和模型来回，
Xiaoyu 负责把记下的事真的办掉。哪家模型的工具调用格式都能从 definitions() 改写过去。
"""

import re
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# 她要另起一条消息时，单独写一行 ---。Runtime 和手机都按它分开，几条、多长都由她定。
MESSAGE_BREAK = "---"
JOIN = "\n\n---\n\n"

# 带工具说话的会话编号前缀：跟以前写 JSON 的会话（api-）分开，免得她照着旧的样子只说不做。
TOOL_SESSION_PREFIX = "apt-"

# 嘴上说了要去做的话。一轮里一个工具都没调、却说了这种话，只记一笔数（见 Toolbox.promised），不追问她。
PROMISE = re.compile(
    r"(我这就|这就去|马上去|马上帮你|我先去|我去(查|看|找|问|拢|整理|弄|做|跑|翻|核|试|盯)"
    r"|我来(查|看|找|拢|整理|弄|做|跑|翻|核|安排)|交给\s?\w{1,12}|让\s?\w{1,12}\s?(去|来)(查|看|做|弄|跑|改|整理)"
    r"|已经(让|交给|安排))")
# 嘴上说了要记住、以后照做的话。没调 remember 就说这种话，跟“说了去做却没做”一样，也只记数。
MEMORY_PROMISE = re.compile(
    r"(记下了|记下来|记住了|我记住|记着了|收下这条|收下了|按这个来|照这个来|以后(我|就)|下次(我|就)|往后(我|就)"
    r"|我会注意|长记性)")
# 答应到时候提醒、到时候再做的话。没调 track 就说这种话，到时候没人记得，也只记数。
LATER_PROMISE = re.compile(
    r"(到时候(我|再)|我(会|来|再)提醒|提醒你|晚点(我|再)|回头(我|再)|等.{1,12}(了|后|以后)(我|再)(去|发|做|弄|提醒|告诉))")
MEMORY_TOOLS = ("remember", "replace", "forget")

ACTION_TOOLS = ("start_task", "amend_task", "cancel_task")

# 给模型看的工具结果：办得了的事先记下，提醒她不用再说一遍。
RECORDED = "好，这一轮说完就去办：%s。用一句话告诉主人交给了谁、在做什么；前面已经说过就不用再说，直接结束。"


class Toolbox:
    """一轮对话用的一套工具。

    helpers：能交办的帮手名字；efforts / models：帮手们能选的努力程度和模型（没有就不出现这一项）。
    check(fields)：拿一份和以前 JSON 一样的字段（card、action）去检查，办不了返回原因，办得了返回 None。
    lookup(card)：一件事的详情（给她看的一段文字），没有这件事返回 None。
    """

    def __init__(self, helpers: List[str], efforts: List[str], models: List[str],
                 check: Callable[[Dict[str, Any]], Optional[str]],
                 lookup: Callable[[str], Optional[str]], memory: Any = None,
                 recall: Optional[Callable[[str], str]] = None, promises: Any = None,
                 conversation: str = "default", only: Optional[Iterable[str]] = None,
                 before_effect: Optional[Callable[[], None]] = None, effects=None,
                 guard: Optional[Callable[[], None]] = None):
        self._helpers = helpers
        self._memory = memory  # memory.OwnerMemory；None 就没有 remember / replace / forget
        self._recall = recall  # recall(query) → 给她看的结果；None 就没有 recall
        self._promises = promises  # promises.Promises；None 就没有 track / resolve
        self._conversation = conversation
        self._only = set(only) if only is not None else None
        self._before_effect = before_effect
        self._effects = effects
        self._guard = guard or (lambda: None)
        self._efforts = efforts
        self._models = models
        self._check = check
        self._lookup = lookup
        self.action: Optional[Dict[str, Any]] = None
        self.card: Optional[str] = None
        self._looked: Optional[str] = None
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def definitions(self) -> List[Dict[str, Any]]:
        """Anthropic Messages 接口格式的工具清单（name、description、input_schema）。"""
        card = {"type": "string", "description": "那件事的编号，连同前面的字母，比如 c3"}
        tools: List[Dict[str, Any]] = []
        if self._helpers:
            start: Dict[str, Any] = {
                "agent": {"type": "string", "enum": list(self._helpers), "description": "交给哪个帮手"},
                "title": {"type": "string", "description": "这件事的短标题，不超过 24 个字"},
                "task": {"type": "string", "description": "交代给帮手的话。它看不到你和主人的对话，把背景和想要的结果写清楚"},
                "card": dict(card, description="要接着一件已有的事做时写它的编号；新的事不写"),
            }
            if self._efforts:
                start["effort"] = {"type": "string", "enum": list(self._efforts)}
            if self._models:
                start["model"] = {"type": "string", "enum": list(self._models)}
            tools.append({
                "name": "start_task",
                "description": "把一件需要动手的事（查资料、读写文件、跑命令、上网……）交给一个帮手在后台做。做完会另外告诉主人。",
                "input_schema": {"type": "object", "properties": start,
                                 "required": ["agent", "title", "task"]},
            })
            tools.append({
                "name": "amend_task",
                "description": "主人对一件已有的事有新的话：要求变了（redo）或者追加内容（after）。",
                "input_schema": {"type": "object", "properties": {
                    "card": dict(card, description="哪件事；主人正看着的那件可以不写"),
                    "task": {"type": "string", "description": "新的内容"},
                    "mode": {"type": "string", "enum": ["redo", "after"]},
                }, "required": ["task", "mode"]},
            })
        tools.append({
            "name": "cancel_task",
            "description": "主人不要某件正在做的事了。",
            "input_schema": {"type": "object", "properties": {"card": card}, "required": []},
        })
        tools.append({
            "name": "task_detail",
            "description": "查一件事现在怎么样了：状态、谁在做、最近几步进展、做完的结果。只是看，不改它。",
            "input_schema": {"type": "object", "properties": {"card": card}, "required": ["card"]},
        })
        if self._recall is not None:
            tools.append({
                "name": "recall",
                "description": "翻以前的记录：跟主人聊过的话、记忆的修改记录、最近的事和帮手交回的结果。"
                               "主人提到“上次”“之前说的”、或者你记不清的时候用。",
                "input_schema": {"type": "object", "properties": {
                    "query": {"type": "string", "description": "要找的关键词，几个词用空格分开"},
                }, "required": ["query"]},
            })
        if self._memory is not None:
            quote = {"type": "string", "description": "主人的原话（出处），一句就够"}
            tools.append({
                "name": "remember",
                "description": "记住一条关于主人的事：偏好、定下的规矩、长期的情况、答应过的长期约定。"
                               "每一轮你都会看到记着的事。跟已经记着的某条冲突或者是改口，用 replace 改那一条，"
                               "不要再记一条。一次性的小事、流水账不用记；密码、密钥、验证码绝不记。",
                "input_schema": {"type": "object", "properties": {
                    "note": {"type": "string", "description": "要记的一句话，写清楚以后该怎么做"},
                    "kind": {"type": "string", "enum": ["偏好", "规矩", "事实", "承诺"]},
                    "quote": quote,
                }, "required": ["note", "kind"]},
            })
            tools.append({
                "name": "replace",
                "description": "主人改了说法：把记着的第 number 条换成新的（旧的会留在修改记录里）。",
                "input_schema": {"type": "object", "properties": {
                    "number": {"type": "integer"},
                    "note": {"type": "string"},
                    "quote": quote,
                }, "required": ["number", "note"]},
            })
            tools.append({
                "name": "forget",
                "description": "删掉一条记着的事（主人不要了、或者记错了）。number 是“你记得的关于主人的事”里的编号。",
                "input_schema": {"type": "object", "properties": {
                    "number": {"type": "integer"},
                }, "required": ["number"]},
            })
        if self._promises is not None:
            tools.append({
                "name": "track",
                "description": "记下你答应主人、要以后办的事（不是交给帮手的任务），比如“晚上提醒我…”"
                               "“等 Mac 上线再发”。提醒类一定写 due（第一次到点的时间），到时候会叫你决定要不要说；"
                               "每天、每周重复的再写 every。",
                "input_schema": {"type": "object", "properties": {
                    "title": {"type": "string", "description": "答应了什么，一句话"},
                    "detail": {"type": "string"},
                    "due": {"type": "string", "description": "什么时候办，写成 YYYY-MM-DD HH:MM（主人所在时区）；没有就不写"},
                    "every": {"type": "string", "enum": ["day", "week"], "description": "每天或每周重复；不重复就不写"},
                }, "required": ["title"]},
            })
            tools.append({
                "name": "resolve",
                "description": "答应的事办完了、或者主人不要了：按编号（p 开头）关掉。",
                "input_schema": {"type": "object", "properties": {
                    "id": {"type": "string"},
                    "outcome": {"type": "string", "description": "怎么了结的，一句话"},
                }, "required": ["id"]},
            })
        if self._only is not None:
            tools = [tool for tool in tools if tool["name"] in self._only]
        return tools

    def call(self, name: str, args: Any) -> Tuple[str, bool]:
        """执行一次工具调用。返回（给模型看的结果，是不是出错）。"""
        return self.guarded_call(name, args, lambda: None)

    def guarded_call(self, name: str, args: Any, guard: Callable[[], None]) -> Tuple[str, bool]:
        def check():
            self._guard()
            guard()
        check()
        args = args if isinstance(args, dict) else {}
        self.calls.append((name, args))
        if self._only is not None and name not in self._only:
            return "这一轮没有 %s 这个工具" % name, True
        if name in ("remember", "replace") and self._memory is not None:
            from ..world.memory import _checked
            _, problem = _checked(str(args.get("note") or ""), str(args.get("quote") or ""))
            if problem:
                return problem, True
        if name in ("remember", "replace", "forget", "track", "resolve"):
            if name in ("replace", "forget") and self._memory is not None and self._effects is not None:
                args = dict(args, _memory_revision=self._memory.revision())
            def invoke():
                if self._before_effect is not None:
                    self._before_effect()
                check()
                result = self._call(name, args)
                check()
                return result
            if self._effects is not None:
                return self._effects.run(name, args, invoke)
            return invoke()
        result = self._call(name, args)
        check()
        return result

    def _call(self, name: str, args: Any) -> Tuple[str, bool]:
        if name == "recall" and self._recall is not None:
            query = str(args.get("query") or "").strip()
            return (self._recall(query), False) if query else ("要写想找什么", True)
        if name == "remember" and self._memory is not None:
            note = str(args.get("note") or "").strip()
            said = self._memory.remember(note, kind=str(args.get("kind") or ""),
                                         quote=str(args.get("quote") or ""))
            return said, not said.startswith(("记下了", "这条之前"))
        if name == "replace" and self._memory is not None:
            try:
                said = self._memory.replace(int(args.get("number")), str(args.get("note") or ""),
                                            quote=str(args.get("quote") or ""),
                                            **({"expected": args["_memory_revision"]} if "_memory_revision" in args else {}))
            except (TypeError, ValueError):
                return "number 要写记着的那条的编号", True
            return said, not said.startswith("改好了")
        if name == "track" and self._promises is not None:
            due, problem = _due(args.get("due"))
            if problem:
                return problem, True
            try:
                item = self._promises.track(str(args.get("title") or ""), str(args.get("detail") or ""),
                                            due, self._conversation, every=str(args.get("every") or ""))
            except ValueError as error:
                return str(error), True
            return ("记上了：%s %s（%s%s）。前面已经跟主人说过的话不用再说一遍，没有别的就直接结束。" % (
                item["id"], item["title"], time.strftime("%m-%d %H:%M", time.localtime(due)) if due else "没定时间",
                {"day": "，每天", "week": "，每周"}.get(item.get("every") or "", ""))), False
        if name == "resolve" and self._promises is not None:
            item = self._promises.resolve(str(args.get("id") or "").strip(), str(args.get("outcome") or ""))
            return ("关掉了：%s" % item["title"], False) if item else ("没有这个编号的事（或者已经关掉了）", True)
        if name == "task_detail":
            card = str(args.get("card") or "").strip()
            found = self._lookup(card) if card else None
            if found is None:
                return "没有编号是 %s 的事" % (card or "（空）"), True
            self._looked = card
            return found, False
        if name == "remember" and self._memory is not None:
            note = str(args.get("note") or "").strip()
            return self._memory.remember(note), not note
        if name == "forget" and self._memory is not None:
            try:
                return self._memory.forget(int(args.get("number")),
                    **({"expected": args["_memory_revision"]} if "_memory_revision" in args else {})), False
            except (TypeError, ValueError):
                return "number 要写记着的那条的编号", True
        if name not in ACTION_TOOLS:
            return "没有叫 %s 的工具" % name, True
        if self.action is not None:
            return "这一轮已经有一件要办的事了（一句话只办一件）。别的等主人下一句再说。", True
        action: Dict[str, Any] = {"type": name.split("_")[0]}
        for key in ("agent", "title", "task", "mode", "effort", "model"):
            if isinstance(args.get(key), str) and args[key].strip():
                action[key] = args[key].strip()
        card = str(args.get("card") or "").strip() or None
        fields = {"card": card or "new", "action": action}
        problem = self._check(fields)
        if problem is not None:
            return "办不了：%s。改好再调一次，或者照实告诉主人。" % problem, True
        self.action, self.card = action, card
        what = {"start": "交给 %s 做「%s」" % (action.get("agent"), action.get("title") or "这件事"),
                "amend": "告诉 %s 新的要求" % (card or "正看着的那件事"),
                "cancel": "停掉 %s" % (card or "正看着的那件事")}[action["type"]]
        return RECORDED % what, False

    def promised(self, said: str) -> bool:
        """嘴上说了却没做：说要去做、却没调做事或查看的工具；说记下了、却没调记忆的工具；
        说到时候提醒、却没调 track。最后是在问主人（问号结尾）的不算：那是在征求意见，不是答应。"""
        names = {name for name, _ in self.calls}
        said = said or ""
        if said.rstrip().endswith(("？", "?")):
            return False
        if PROMISE.search(said) and not names & set(ACTION_TOOLS + ("task_detail", "recall")):
            return True
        if self._memory is not None and MEMORY_PROMISE.search(said) and not names & set(MEMORY_TOOLS + ("track",)):
            return True
        return self._promises is not None and LATER_PROMISE.search(said) is not None \
            and not names & {"track", "start_task"}

    def speaking(self) -> "SpeakingTools":
        return SpeakingTools(self)

    def fields(self, said: str) -> Dict[str, Any]:
        """这一轮的结果，写成和以前 JSON 一样的字段，交给 Xiaoyu 照原来的路办。

        没调做事的工具时是“直接答了”（action none）；她查过哪件事，这句话就归到那件。
        表情：交出去了是 busy，其余是 happy。brief 不单独要，由 shape 从回复里截。"""
        if self.action is None:
            return {"reply": said, "card": self._looked or "new", "action": {"type": "none"},
                    "mood": "happy"}
        return {"reply": said, "card": self.card or "new", "action": self.action,
                "mood": "busy" if self.action["type"] in ("start", "amend") else "happy"}


class SpeakingTools:
    """The speaking phase can look things up, but cannot make another commitment."""

    def __init__(self, parent: Toolbox):
        self.parent = parent
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self.action = None
        self.allowed = {tool["name"] for tool in parent.definitions()} & {"task_detail", "recall"}

    def definitions(self) -> List[Dict[str, Any]]:
        return [tool for tool in self.parent.definitions() if tool["name"] in self.allowed]

    def call(self, name: str, args: Any) -> Tuple[str, bool]:
        return self.guarded_call(name, args, lambda: None)

    def guarded_call(self, name: str, args: Any, guard: Callable[[], None]) -> Tuple[str, bool]:
        guard()
        self.calls.append((name, args))
        if name not in self.allowed:
            return "说话阶段不能改变任务、记忆或约定", True
        return self.parent.guarded_call(name, args, guard)

    def promised(self, said: str) -> bool:
        return False

    def fields(self, said: str) -> Dict[str, Any]:
        return {"reply": said, "card": self.parent._looked or "new",
                "action": {"type": "none"}, "mood": "happy"}


def _due(value: Any) -> Tuple[Optional[float], Optional[str]]:
    """track 的 due：YYYY-MM-DD HH:MM（本地时间）。没写是 None；写错了给出原因。"""
    text = str(value or "").strip()
    if not text:
        return None, None
    for form in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(text, form)), None
        except ValueError:
            continue
    return None, "due 要写成 YYYY-MM-DD HH:MM，比如 2026-10-21 09:00"
