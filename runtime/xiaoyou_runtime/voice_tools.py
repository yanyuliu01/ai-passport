"""小幽接话时手边的工具：说的话就是她直接写的文字，要 Runtime 做事就调工具。

以前小幽每一轮只输出一个 JSON（reply、brief、mood、card、action），Runtime 再从文字里把
reply 抠出来；模型在 JSON 前后多写一句、或者包进代码块，就会把整段 JSON 露给主人。
改成工具调用以后：

- 说的话：她直接写的文字，流式给主人看，空一行就是下一条消息。
- 做事：start_task / amend_task / cancel_task。调用时就按 Xiaoyou._plan 的规则检查一遍，
  办不了的当场把原因告诉她，她在同一轮里改；办得了的先记下，这一轮说完以后由 Runtime
  照旧去办（开卡、启动帮手都用原来的代码）。一轮只能有一件要办的事。
- 查东西：task_detail 当场查、当场把结果给她，她看完接着说。

这里只管“有哪些工具、调用了怎么样”，不碰网络和模型：dsh_stream 负责和模型来回，
Xiaoyou 负责把记下的事真的办掉。哪家模型的工具调用格式都能从 definitions() 改写过去。
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

ACTION_TOOLS = ("start_task", "amend_task", "cancel_task")

# 给模型看的工具结果：办得了的事先记下，提醒她不用再说一遍。
RECORDED = "好，记下了，这一轮说完就去办：%s。刚才已经说过的话不用再重复；还有要跟主人说的就接着说，没有就结束。"


class Toolbox:
    """一轮对话用的一套工具。

    helpers：能交办的帮手名字；efforts / models：帮手们能选的努力程度和模型（没有就不出现这一项）。
    check(fields)：拿一份和以前 JSON 一样的字段（card、action）去检查，办不了返回原因，办得了返回 None。
    lookup(card)：一件事的详情（给她看的一段文字），没有这件事返回 None。
    """

    def __init__(self, helpers: List[str], efforts: List[str], models: List[str],
                 check: Callable[[Dict[str, Any]], Optional[str]],
                 lookup: Callable[[str], Optional[str]]):
        self._helpers = helpers
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
        return tools

    def call(self, name: str, args: Any) -> Tuple[str, bool]:
        """执行一次工具调用。返回（给模型看的结果，是不是出错）。"""
        args = args if isinstance(args, dict) else {}
        self.calls.append((name, args))
        if name == "task_detail":
            card = str(args.get("card") or "").strip()
            found = self._lookup(card) if card else None
            if found is None:
                return "没有编号是 %s 的事" % (card or "（空）"), True
            self._looked = card
            return found, False
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

    def fields(self, said: str) -> Dict[str, Any]:
        """这一轮的结果，写成和以前 JSON 一样的字段，交给 Xiaoyou 照原来的路办。

        没调做事的工具时是“直接答了”（action none）；她查过哪件事，这句话就归到那件。
        表情：交出去了是 busy，其余是 happy。brief 不单独要，由 shape 从回复里截。"""
        if self.action is None:
            return {"reply": said, "card": self._looked or "new", "action": {"type": "none"},
                    "mood": "happy"}
        return {"reply": said, "card": self.card or "new", "action": self.action,
                "mood": "busy" if self.action["type"] in ("start", "amend") else "happy"}
