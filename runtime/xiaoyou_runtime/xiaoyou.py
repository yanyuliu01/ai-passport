"""小幽本人。

小幽是一个独立的身份，不是某个模型的别名。属于她的东西都在这里、cards.py 和
store.py 里：人设、她和主人的对话记录、每件事的卡、这句话交给谁的判断、帮手做完
之后怎么转述。Claude、Codex 和以后接进来的本地模型，都只是她能用的代理（agents.py）。

主人说一句话之后（每个对话一条线，一句一句来，只做快的事）：

  1. 路由（router.py）看这句话有没有指定给谁。
  2. 指定了（接口里指定、话的开头点名、路由器选中）：不调用模型，直接开一张卡，
     把活交给那个代理在后台做，这条线立刻空出来。
  3. 没指定：让替小幽说话的代理接这句话。这一次不给它任何工具，只要它听懂：能马上
     答的直接答；要花时间的，在回复里说交给哪个帮手（start）；是对一件正在做的事的
     补充或改要求（amend）、取消（cancel），也在回复里说。Runtime 照着办。
  4. 后台的事做完：帮手能以小幽的身份说话，它的结果就是小幽的话；只会干活的，结果
     交回这条线，由说话的代理转述。说话的代理不在时原样给主人，不让主人空手而归。
  5. 每句话和每个结果都记进小幽的对话记录。说话的代理没亲眼见过的（后台的结果、
     手机从别处带来的），下次轮到它时先告诉它。
"""

import json
import os
import re
import sys
import threading
import uuid
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import router as routing
from .agents import (MOVED, RESUME_HINT, SILENT, STOP, WAIT, Agent, AgentError, Control, Job,
                     Outcome, clip, span)
from .approvals import STALL, Approvals, Gate
from .artifacts import ArtifactError, ArtifactStore
from .record import Record
from .cards import ACTIVE, MAX_ENTRY_CHARS, Cards, title_from
from .config import EFFORT_LEVELS, Config
from .store import Store
from .tasks import Task, Tasks
from .usage import EFFORT_NAMES, Usage
from .workspace import Workspace

MOODS = ("idle", "busy", "ask", "happy", "oops")

# 转告代理没见过的对话时：每轮最多引用这么多字，总共不超过这么多字（保留最近的）。
RECAP_TEXT_CHARS = 500
RECAP_REPLY_CHARS = 1000
RECAP_TOTAL_CHARS = 8000
# 只干活的代理拿到的背景更短：它要的是任务，不是聊天记录。
WORKER_RECAP_TOTAL_CHARS = 3000
# 帮手交回的结果最多把这么多字交给说话的代理去总结。
RESULT_CHARS = 12000
MAX_TASK_CHARS = 8000
# 点名交给帮手时，带上最近这么多轮对话当背景。
BACKGROUND_TURNS = 6

ACTIONS = ("none", "start", "amend", "cancel")
# 小幽接话时提示里列出的事：还在做的全列，另外带上最近结束的几件（主人常会接着问）。
RECENT_FINISHED_CARDS = 5
# 主人看着电脑上的会话说话时，提示里带上它最近的内容：每条最多这么多字，合起来最多这么多字。
DESKTOP_ENTRY_CHARS = 1500
DESKTOP_CONTEXT_CHARS = 6000
# 一件事排着的补充已经满了（见 tasks.MAX_PENDING），这句补充没记上时小幽这样说。
QUEUE_FULL_NOTE = "这件事排着的补充已经有好几条了，这句没记上，等它做完这一轮再说吧"

# 向调用方报告这一轮走到了哪一步：(kind, agent, text)。
Report = Callable[[str, Optional[str], str], None]


@dataclass(frozen=True)
class Turn:
    reply: str  # 小幽这一句的完整回复
    brief: str  # 给小屏幕的一两句话
    mood: str  # 小幽此刻的表情，取值见 MOODS
    agent: str  # 接这句话的代理；交给了帮手时是那个帮手
    card: str = ""  # 这句话归到了哪张卡
    started: bool = False  # 这件事还有帮手在后台做，结果之后出现在卡上


# 小屏和终端显示不出来的东西：反引号代码围栏、表情符号、Markdown 标记。clean_display
# 把它们清理或转写成纯文本，只保留中文、标点和必要的换行。

# 行首的 Markdown 标记：标题 #、引用 >、列表 - * +，以及一整行都是分隔线（--- 之类）。
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_QUOTE = re.compile(r"^\s{0,3}>\s?")
_BULLET = re.compile(r"^\s{0,3}[-*+]\s+")
_RULE = re.compile(r"^\s{0,3}([-*_])\s*(?:\1\s*){2,}$")
# 一行就是 ``` 或 ```语言：整个代码围栏的边框去掉（围栏里的内容留着）。
_FENCE = re.compile(r"^\s*```[^`]*$")

# 行内的 Markdown：链接/图片 [..](..)、加粗 **..**、删除线 ~~..~~。单星号的斜体和
# 下划线不处理，免得把乘法、下划线变量名这些普通文字误伤。
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_BOLD = re.compile(r"\*\*([^*]+?)\*\*")
_STRIKE = re.compile(r"~~([^~]+?)~~")


def clean_display(text: str) -> str:
    """把一段回复整理成小屏和终端能干净显示的样子。

    去掉反引号代码围栏、表情符号和写不出来的符号，把 Markdown 标记转成纯文本，保留中文、
    标点和必要的换行。空字符串原样返回。"""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    lines = []
    for raw in text.split("\n"):
        line = raw.rstrip()
        if _FENCE.match(line):
            lines.append("")
            continue
        line = _HEADING.sub("", line)
        line = _QUOTE.sub("", line)
        if _RULE.match(line):
            line = ""
        else:
            line = _BULLET.sub("· ", line)
        line = _LINK.sub(r"\1", line)
        line = _BOLD.sub(r"\1", line)
        line = _STRIKE.sub(r"\1", line)
        lines.append(line)
    text = "\n".join(lines)
    text = text.replace("`", "")  # 行内代码的反引号直接去掉
    text = "".join(ch for ch in text if _displayable(ch))
    text = re.sub(r" {2,}", " ", text)      # 去掉符号后留下的多余空格
    text = re.sub(r" +\n", "\n", text)      # 行尾多余空格
    text = re.sub(r"\n{3,}", "\n\n", text)  # 连续空行压成一行
    return text.strip()


def _displayable(ch: str) -> bool:
    """能上小屏的字符：字母、数字、标点、空白；符号和隐形标记不要。"""
    category = unicodedata.category(ch)
    if category in ("So", "Sk", "Mn", "Cf"):
        return False
    return category != "Cc" or ch == "\n"


_REPLY_KEY = re.compile(r'"reply"\s*:\s*"')
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def reply_so_far(raw: str) -> str:
    """还没说完的那段 JSON 里，reply 已经写出来的部分。还没写到 reply 时是空串。

    只认到最后一个完整的字符：写了一半的转义（\\、\\u4e）先不算，下一块来了再补上。"""
    found = _REPLY_KEY.search(raw)
    if found is None:
        return ""
    out: List[str] = []
    at, end = found.end(), len(raw)
    while at < end:
        ch = raw[at]
        if ch == '"':
            break
        if ch != "\\":
            out.append(ch)
            at += 1
            continue
        if at + 1 >= end:
            break
        code = raw[at + 1]
        if code == "u":
            digits = raw[at + 2:at + 6]
            if len(digits) < 4:
                break
            try:
                out.append(chr(int(digits, 16)))
            except ValueError:
                break
            at += 6
            continue
        out.append(_ESCAPES.get(code, code))
        at += 2
    text = "".join(out)
    # 代理对：先到的那一半单独显示不出来，等另一半。
    if text and "\ud800" <= text[-1] <= "\udbff":
        text = text[:-1]
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


# 流式回复最快隔这么久往外报一次，免得每个字都让手机多跑一趟。
PARTIAL_SECONDS = 0.12


def shape(reply: str, brief: Any, mood: Any, limit: int, agent: str, card: str = "",
          started: bool = False) -> Turn:
    """把代理给出的原始字段整理成一轮结果：缺的补上，超长的截断，非法的表情改成 idle。"""
    reply = clean_display(reply or "")
    brief_text = clean_display(brief) if isinstance(brief, str) else ""
    return Turn(
        reply=reply,
        brief=clip(brief_text or reply, limit),
        mood=mood if mood in MOODS else "idle",
        agent=agent,
        card=card,
        started=started,
    )


def reply_schema() -> Dict[str, Any]:
    """以小幽的身份说一段话（后台的事做完、转述帮手的结果）时要给出的结构。"""
    return {
        "type": "object",
        "properties": {
            "reply": {"type": "string"},
            "brief": {"type": "string"},
            "mood": {"type": "string", "enum": list(MOODS)},
        },
        "required": ["reply", "brief", "mood"],
        "additionalProperties": False,
    }


def lane_schema(helpers: List[Agent]) -> Dict[str, Any]:
    """小幽接主人一句话时要给出的结构：说什么、归到哪件事、要 Runtime 做什么。"""
    schema = reply_schema()
    action: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": list(ACTIONS if helpers else ("none", "cancel"))},
            "title": {"type": "string"},
            "task": {"type": "string"},
            "mode": {"type": "string", "enum": ["redo", "after"]},
        },
        "required": ["type"],
        "additionalProperties": False,
    }
    if helpers:
        action["properties"]["agent"] = {
            "type": "string", "enum": [agent.name for agent in helpers]}
        # 这一次用哪一档努力程度、换哪个模型：帮手里有人能选才有这两项。
        efforts = [level for level in EFFORT_LEVELS
                   if any(level in agent.spec.efforts for agent in helpers)]
        models = sorted({name for agent in helpers for name in agent.spec.models})
        if efforts:
            action["properties"]["effort"] = {"type": "string", "enum": efforts}
        if models:
            action["properties"]["model"] = {"type": "string", "enum": models}
    schema["properties"]["card"] = {"type": "string"}
    schema["properties"]["action"] = action
    return schema


REPLY_FORMAT = (
    "reply 是给主人看的完整回复；brief 是显示在随身小屏幕上的一两句话，不超过 %d 个字，"
    "要让人不看 reply 也知道结论，只用普通文字和标点，不用表情符号（小屏幕显示不了）；"
    "mood 是你此刻的表情，只能是 idle（平常）、busy（还在忙）、ask（需要主人拿主意）、"
    "happy（顺利完成）、oops（出了问题）之一。"
)


def _elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return "%d 秒" % seconds if seconds < 60 else "%d 分钟" % (seconds // 60)


def describe_card(card: Dict[str, Any], now: float) -> str:
    """现在的事清单里的一行。"""
    who = card["agent"] or "你自己"
    if card["state"] == "working":
        status = "%s 在做，已经 %s" % (who, _elapsed(now - (card["started_at"] or now)))
    elif card["state"] == "waiting":
        status = "%s 在做，等主人点头" % who
    else:
        status = {"done": "做完了", "failed": "没做成", "cancelled": "取消了"}.get(
            card["state"], card["state"])
        status = "%s%s" % (who + " " if card["agent"] else "", status)
    return "- %s「%s」：%s" % (card["id"], card["title"], status)


def tier_rules(helpers: List[Agent], quota: bool) -> List[str]:
    """派活时怎么选档位、怎么看额度。帮手里没人能选档、也没有额度可看（quota）时是空的。"""
    efforts = [level for level in EFFORT_LEVELS
               if any(level in agent.spec.efforts for agent in helpers)]
    models = any(agent.spec.models for agent in helpers)
    if not efforts and not models and not quota:
        return []
    parts = ["", "## 档位和额度",
             "帮手后面〔〕里是它用的模型、努力程度，和它的订阅额度还剩多少。"]
    if efforts:
        parts.append(
            "交给帮手（start、amend）时可以在 action 里写 effort，指定这一次的努力程度：%s。"
            "只能写那个帮手可选的；不写就用它平时的。越高想得越多，也越费额度。" % "、".join(
                "%s 是%s" % (level, EFFORT_NAMES[level]) for level in efforts))
        parts.append(
            "- 主人说了用哪一档就按主人的；说“仔细点”“好好想想”就比平时高一档，"
            "说“随便看看”“快一点”就低一档。")
        parts.append(
            "- 主人没说时：查一下、看一眼、改个小地方用 low；一般的事不写 effort；只有明显难的"
            "（要动很多文件、查了几次都查不出原因）才用 high，并在 reply 里说一句这次用了高档。")
    if models:
        parts.append("- 主人说了换哪个模型，在 action 里写 model（只能写那个帮手可换的）；没说就不写。")
    if quota:
        parts.append("- 几个帮手都能做时，交给额度剩得多的那个：两个窗口里看更紧的那个。")
        if efforts:
            parts.append("- 帮手写着“快用完了”：主人没指定档位的话比平时低一档，并在 reply 里说一句。")
        parts.append(
            "- 帮手写着“用完了”：不要交给它，除非主人点了它的名。能做这件事的帮手都用完了，"
            "就直接告诉主人什么时候重置，action 写 none。")
        parts.append("- 写着“用量还不知道”的不算用完，照常用。")
    return parts


def lane_prompt(persona: str, brief_max_chars: int, helpers: List[Agent],
                cards: List[Dict[str, Any]], focus: Optional[str], now: float,
                pinned: bool = False, notes: Optional[Dict[str, str]] = None,
                quota: bool = False, desktop: Optional[Dict[str, Any]] = None) -> str:
    """小幽接主人一句话时的系统提示：人设、她怎么做事、回复格式、帮手、现在的事。

    notes 是每个帮手的模型、档位和额度（一句话），有就写在它后面；quota 表示帮手里有
    订阅额度可看的。desktop 是主人正看着的电脑上的会话（focus 就是它）。"""
    notes = notes or {}
    parts = [persona, ""]
    parts.append("## 你怎么做事")
    parts.append(
        "这一轮你只负责说话：听懂主人的话，凭你已经知道的就能答的直接答，其余的交给帮手。"
        "你现在没有任何工具，这是特意安排的，不是出了故障：查资料、读文件、写文件、跑命令、"
        "上网，这些动手的事都由帮手在后台做。所以凡是需要动手的事，一律用 start 交给帮手"
        "（对已有的事用 amend），不要自己尝试，不要假装查过，也不要跟主人说“我没有工具”"
        "“我做不了”。帮手在后台做，做完后结果会单独告诉主人：你不用等，也不要替它编结果。"
        "对话里以你的口吻说的“做完了”“没做成”，是帮手在后台做完之后的汇报，不代表你这一轮"
        "自己能动手；主人让你再做一次，就再交给帮手一次。"
        "主人可以同时让几件事一起做，一件事没做完不妨碍开下一件。"
    )
    parts.append(
        "主人的话含糊、没说清楚具体要做什么时，先自己判断一下：能直接问清楚就问（action 写 "
        "none），觉得已经有把握猜到意思，也可以按你的理解派给帮手——但不管选哪条，reply 里都"
        "要写出具体的判断理由，像是你真的想过这件事：这句话含糊在哪、你想到了哪几种可能的"
        "理解、为什么挑了其中一种、或者为什么觉得哪几种理解差太多所以要问。"
        "不要写“收到，你想让我干啥”“在呢，一切正常”“需要确认一下”这类不含具体内容、换成任何"
        "一句话都能用的套话——这种话等于没想、没判断。"
        "比如主人说“测一下”：不能只回“在呢，有事说”，而要说清楚具体是什么让你拿不准，比如"
        "“‘测一下’没说要测什么——是测刚改完的这部分代码、测固件、还是就是想看看我们这条线通不"
        "通？我先按你最近在聊的那件事理解，如果猜错了你告诉我”，然后再决定是问清楚还是按这个"
        "理解派出去。"
        "同一句含糊的话主人又说了一遍，不代表要你换个花样：不要把它当成“顺便验证一下别的帮手"
        "通不通”“这次换一个试试”这种自己加的新任务——没新信息就按上次的理解给个稳定、"
        "一致的答案，或者还是如实说你仍然不确定，不要为了显得有在做事就真的派一件你自己编"
        "出来的任务。真要派给帮手做事，是因为确实听懂了要做什么，不是为了测帮手能不能通。"
    )
    parts.append("")
    parts.append("## 回复格式")
    parts.append("每一轮只输出一个 JSON 对象，按给定的结构：" + REPLY_FORMAT % brief_max_chars)
    parts.append(
        "card 是这句话属于哪件事：新的事写 new；是对下面“现在的事”里某一件的追问、补充、"
        "改要求或取消，写它的编号，连同前面的字母，比如 c3。"
    )
    parts.append("action 是要 Runtime 接着做什么，type 只能是下面几种：")
    parts.append("- none：你已经直接答了，不用做别的。")
    if helpers:
        parts.append(
            "- start：交给一个帮手去做。agent 写帮手的名字；title 是这件事的短标题，不超过 24 个字；"
            "task 是交代给帮手的话——它看不到你和主人的对话，所以把背景和想要的结果写清楚。"
            "reply 写一句你先跟主人说的话（你让谁去做什么），mood 用 busy。"
        )
        parts.append(
            "- amend：主人对一件正在做的事有新的话。card 写那件事的编号，task 写新的内容，"
            "mode 写 redo（要求变了，按新的来）或 after（追加的内容，现在的做完接着做）。"
        )
    parts.append("- cancel：主人不要某件事了。card 写那件事的编号。")
    parts.append("")
    parts.append("## 你的帮手")
    if helpers:
        for agent in helpers:
            note = notes.get(agent.name)
            parts.append("- %s：%s%s" % (agent.name, agent.description or "（没有说明）",
                                        "〔%s〕" % note if note else ""))
        parts.append("自己能答的直接答，不要为了用而用。一句话只能交给一个帮手。")
        parts.extend(tier_rules(helpers, quota))
    else:
        parts.append("现在没有别的帮手，你只能靠自己回答。做不到的事直接说做不到。")
    parts.append("")
    parts.append("## 现在的事")
    if cards:
        parts.extend(describe_card(card, now) for card in cards)
    else:
        parts.append("还没有。")
    if focus is not None and pinned:
        parts.append(
            "主人是打开 %s 这件事、在它里面说的这句话：一般就归到它，card 写 %s。"
            "能直接答的直接答；要动手就接着这件事做（它还在做用 amend，已经结束了用 amend 或 "
            "start，都会接着它原来的会话）。只有这句话明显是跟它无关的另一件新事时，才用 start "
            "并把 card 写 new，另开一件，给它起自己的 title。" % (focus, focus))
    elif focus is not None:
        parts.append(
            "主人说这句话时正看着 %s。没有别的线索时，“这个”“它”“这件事”指的就是它。" % focus)
    if desktop is not None:
        parts.extend(describe_desktop(desktop, helpers))
    return "\n".join(parts)


def describe_desktop(card: Dict[str, Any], helpers: List[Agent]) -> List[str]:
    """主人正看着的电脑上的会话：它是什么、最近说了什么，以及什么时候才让它接着做。"""
    wanted = "codex" if card.get("agent") == "codex" else "claude_code"
    heir = next((agent.name for agent in helpers if agent.type == wanted), None)
    parts = ["", "## 主人正看着的电脑会话",
             "%s「%s」是主人电脑上的 %s 会话，你只是在旁边看着，它不归你管。" % (
                 card["id"], card["title"], card.get("agent") or "代理")]
    if heir is not None:
        parts.append(
            "主人问它的情况、要你解释、翻译、总结它说的内容，你直接答，card 写 new，action 用 none。"
            "只有主人明确要电脑上那个会话接着干活（改代码、继续做、照它说的办）时，才用 amend，"
            "card 写 %s，task 写清要它接着做什么：会交给 %s 在那个会话里接着做。拿不准就先问主人。"
            % (card["id"], heir))
    else:
        parts.append("这台 Runtime 上没有能接这个会话的帮手：你只能回答，不能让它接着做。")
    recent, size = [], 0
    for entry in reversed(card.get("entries") or []):
        text = (entry.get("text") or "").strip()
        if not text:
            continue
        who = "主人" if entry.get("role") == "you" else (card.get("agent") or "代理")
        line = "%s：%s" % (who, clip(text, DESKTOP_ENTRY_CHARS))
        if recent and size + len(line) > DESKTOP_CONTEXT_CHARS:
            break
        recent.insert(0, line)
        size += len(line)
    if recent:
        parts.append("它最近的内容（旧的在前）：")
        parts.extend(recent)
    return parts


def voice_prompt(persona: str, brief_max_chars: int) -> str:
    """转述帮手的结果时的系统提示：人设和回复格式。"""
    return "\n".join([
        persona, "",
        "## 回复格式",
        "只输出一个 JSON 对象，按给定的结构：" + REPLY_FORMAT % brief_max_chars,
    ])


def task_prompt(persona: str, brief_max_chars: int) -> str:
    """帮手以小幽的身份在后台做一件事时的系统提示。"""
    return "\n".join([
        persona, "",
        "## 这一次",
        "你正在后台替主人做一件事。把它做完，然后用你自己的话告诉主人：做了什么、结论是什么、"
        "有没有需要主人决定的事。不要原样贴一大段输出。", "",
        "## 回复格式",
        "做完后只输出一个 JSON 对象，按给定的结构：" + REPLY_FORMAT % brief_max_chars,
    ])


def _recap_blocks(turns: List[Dict[str, Any]], total_chars: int) -> List[str]:
    blocks: List[str] = []
    total = 0
    for turn in reversed(turns):
        block = "主人：%s\n小幽：%s" % (
            turn["text"][:RECAP_TEXT_CHARS], turn["reply"][:RECAP_REPLY_CHARS])
        total += len(block)
        if total > total_chars and blocks:
            break
        blocks.append(block)
    blocks.reverse()
    return blocks


def recap(turns: List[Dict[str, Any]], text: str) -> str:
    """把说话的代理没见过的几轮接在主人这句话前面。"""
    return (
        "（下面是主人和你——小幽——聊过、但你这边还没见过的几轮：那几轮是通过别的途径答的。"
        "这里只是让你接上话，不要复述，也不要提“别的途径”。）\n\n%s\n\n（现在主人说：）\n%s"
        % ("\n\n".join(_recap_blocks(turns, RECAP_TOTAL_CHARS)), text)
    )


def background(turns: List[Dict[str, Any]], text: str) -> str:
    """把最近的对话作为背景交给只干活的代理。"""
    return (
        "（背景：下面是主人和助手小幽最近的几轮对话，只用来帮你理解任务，不用回应。）\n\n%s"
        "\n\n（任务：）\n%s"
        % ("\n\n".join(_recap_blocks(turns, WORKER_RECAP_TOTAL_CHARS)), text)
    )


def named_note(agent: str, text: str) -> str:
    """主人在话里点了名、被点名的代理以小幽的身份做事：告诉它说的就是它自己。主人看不到这段。"""
    return (
        "（主人点名要 %s 来做这件事。你现在用的就是 %s，所以直接做，"
        "不用向主人解释这一点。）\n\n%s" % (agent, agent, text)
    )


def relay(helper: str, title: str, result: str) -> str:
    """只会干活的帮手做完之后，交回给说话的代理的那段话。主人看不到这段。"""
    cut = ""
    if len(result) > RESULT_CHARS:
        head = RESULT_CHARS * 2 // 3
        result = result[:head] + "\n（中间内容省略，完整原文在任务卡中）\n" + result[-(RESULT_CHARS - head):]
        cut = "\n（以上为原文开头和结尾，请勿声称已阅读省略内容。）"
    return (
        "（后台那件事「%s」，帮手 %s 做完了。下面是它交回的原始结果，主人看不到这段。"
        "用你自己的话告诉主人：做了什么、结论是什么、有没有需要主人决定的事。"
        "）\n\n%s%s" % (title, helper, result, cut)
    )


def retry_note(problem: str) -> str:
    """她回复里要办的事办不了：告诉她原因，让她重说一次。主人看不到这段。"""
    return (
        "（你刚才的回复没法照办：%s。主人还没看到那条回复，也看不到这段。重新回复一次："
        "能办就按正确的写，办不了就照实告诉主人，action 写 none。）" % problem
    )


class Xiaoyou:
    def __init__(self, config: Config, agents: List[Agent], store: Store,
                 router: Optional[routing.Router] = None,
                 workspace: Optional[Workspace] = None, usage: Optional[Usage] = None):
        self._config = config
        # 用量和档位。没给就用一个不主动去问的：帮手自己报上来的照记。
        self._usage = usage if usage is not None else Usage(
            config, enabled=False, specs=[agent.spec for agent in agents])
        # 正在做的每一轮在用量那边的记号：遥控器 → 记号
        self._runs: Dict[Control, Dict[str, Any]] = {}
        # 共享工作区：每次把活交给别的代理、每次有结果，都在那里留一条。
        self._workspace = workspace if workspace is not None else Workspace()
        self._agents: Dict[str, Agent] = {agent.name: agent for agent in agents}
        self._order = [agent.name for agent in agents]
        self._agents_lock = threading.Lock()
        self._store = store
        self._router = router if router is not None else routing.Router()
        self._cards = Cards(config.state_dir / "cards.json")
        # 记录中心：这里写卡、说话、改状态都经过它（record.py）；_cards 只用来读。
        self._record = Record(self._cards)
        # 给模型的对话记录：本机产生的每一轮，文字以消息日志为准（store.Transcript）。
        store.transcript.resolve_with(self._final_text)
        self.artifacts = ArtifactStore(config)
        self._approvals = Approvals(self._approval_changed, self._record.sequence)
        self._gate = Gate(self._approvals)
        self._tasks = Tasks(store, self._finished, config.max_parallel, self._begun,
                            self._prepare, self._release)
        # 只会干活的帮手做完后，转述要回到那个对话自己的线上排队；没有人排队（命令行里
        # 说一句就走）时就地做。Service 启动时把它换成“排到这个对话的线上”。
        self.defer: Callable[[str, Callable[[], None]], None] = lambda conversation, work: work()
        # 小幽网络（mesh.py）：别的 Runtime 的事在这里只有副本，补充、取消要交给那台。
        # 没加入网络时是 None。
        self.mesh: Optional[Any] = None

    def set_remote_agents(self, agents: List[Agent]) -> None:
        """换上网络里别的 Runtime 的帮手（mesh.py 按谁在线随时更新）；本机的不动。"""
        with self._agents_lock:
            for name in [name for name, agent in self._agents.items() if agent.type == "mesh"]:
                del self._agents[name]
            self._order = [name for name in self._order if name in self._agents]
            for agent in agents:
                if agent.name not in self._agents:
                    self._agents[agent.name] = agent
                    self._order.append(agent.name)

    @property
    def default_agent(self) -> str:
        return self._config.default_agent

    @property
    def cards(self) -> Cards:
        return self._cards

    @property
    def record(self) -> Record:
        """记录中心：别的模块要写记录走它。"""
        return self._record

    @property
    def approvals(self) -> Approvals:
        return self._approvals

    @property
    def usage(self) -> Usage:
        return self._usage

    def agents(self) -> List[Agent]:
        with self._agents_lock:
            return [self._agents[name] for name in self._order if name in self._agents]

    def has(self, name: str) -> bool:
        return name in self._agents

    def close(self) -> None:
        self._tasks.close()
        self._gate.close()
        self._workspace.close()

    def _available(self, hop: int) -> List[Agent]:
        # 已经是别的 Runtime 转过来的话，不再往外转：两台互相登记时不会来回踢。
        return [agent for agent in self.agents() if hop == 0 or agent.type not in ("remote", "mesh")]

    def _voice(self, available: List[Agent]) -> Optional[Agent]:
        """替小幽转述的代理：配置里指定的那个；它这一轮用不了就找第一个能说话的。
        网络里别的 Runtime 的帮手不替这台说话。"""
        speakers = [agent for agent in available if agent.speaks and agent.type != "mesh"]
        for agent in speakers:
            if agent.name == self._config.voice_agent:
                return agent
        return speakers[0] if speakers else None

    def hear(self, text: str, conversation: str, turn_id: str, asked: Optional[str] = None,
             card: Optional[str] = None, hop: int = 0, report: Optional[Report] = None,
             pin: bool = False, effort: Optional[str] = None,
             model: Optional[str] = None) -> Turn:
        """接主人的一句话。很快返回：要花时间的部分已经交到后台，结果之后出现在卡上。

        card 是主人说这句话时屏幕上的那件事（没有就是 None）。pin 表示主人是打开那件事、
        在它里面说的：这句话一定归到它，不另开卡。effort / model 是调用方给这句话选的努力
        程度和模型：交给帮手时用，那个帮手不认的当作没选。失败时抛 AgentError，消息可以
        直接给主人看。
        """
        say: Report = report if report is not None else (lambda kind, agent, detail: None)
        desktop = self._cards.get(card) if card else None
        if desktop is not None and not desktop.get("monitoring_only"):
            desktop = None
        available = self._available(hop)
        heir = self._desktop_agent(desktop) if desktop is not None and pin else None
        if not available:
            if heir is None:
                raise AgentError("这台 Runtime 上没有能接这句话的代理")
            return self._continue_desktop(desktop, text, conversation, turn_id, hop, say,
                                          effort, model)
        route = routing.decide(text, asked, available, self._config.default_agent, self._router)
        if desktop is not None and pin:
            # 在电脑上的会话里说的话，默认还是先给小幽：解释、翻译、总结她自己答。只有点名让
            # 能接这类会话的帮手做（或者她判断要那边接着干活，见 _converse），才接着那个会话做。
            if heir is not None and route.reason in ("asked", "mention") and route.agent == heir.name:
                return self._continue_desktop(desktop, text, conversation, turn_id, hop, say,
                                              route.effort or effort, model, route.text)
            pin = False  # 会话不归 Runtime：这句话不记到它上面
        # 话里紧跟着名字说的档位，比调用方选的更近。
        effort = route.effort or effort
        first = self._agents[route.agent]
        say("route", first.name, route.reason)
        # 电脑上的会话在自己的对话里；主人看着它说话时它照样是 focus。
        focus = desktop if desktop is not None else self._cards.get(card) if card else None
        if focus is not None and focus["conversation"] != conversation and desktop is None:
            focus = None
        if desktop is not None and route.reason in ("default", "router"):
            # 看着电脑上的会话说的：她来接话，不按默认或路由直接交给帮手。
            lead = first if first.speaks and first.type != "mesh" else self._voice(available)
            if lead is not None:
                return self._converse(lead, text, conversation, turn_id, focus, available, hop, say,
                                      False, effort, model)
        pinned = pin and focus is not None
        if route.reason != "default" or not first.speaks:
            # 指定了由谁做（或者默认代理只会干活）：不用问模型，直接交过去。
            if pinned and (focus["agent"] in (None, first.name)
                           or not self._tasks.active(focus["id"])):
                return self._resume(focus, first, route, text, conversation, turn_id, hop, say,
                                    effort, model)
            return self._assign(first, route, text, conversation, turn_id, hop, say, effort, model)
        return self._converse(first, text, conversation, turn_id, focus, available, hop, say,
                              pinned, effort, model)

    # ---- 指定了由谁做 ----

    def _assign(self, agent: Agent, route: routing.Route, said: str, conversation: str,
                turn_id: str, hop: int, say: Report, effort: Optional[str] = None,
                model: Optional[str] = None) -> Turn:
        reply = "交给 %s 了" % agent.name
        card = self._record.task_open(conversation, route.text, "working", agent.name)
        self._record.exchange(card["id"], said=said, say=reply, turn_id=turn_id, kind="handoff", brief=reply, mood="busy")
        task = route.text
        if route.reason == "mention" and agent.speaks:
            # 话里点的名还留在话里，要告诉它说的就是它自己；接口里指定的不用。
            task = named_note(agent.name, task)
        # 帮手没参与之前的对话：带上最近几轮当背景。
        recent = self._store.transcript.turns(conversation)[-BACKGROUND_TURNS:]
        if recent:
            task = background(recent, task)
        self._store.transcript.add(conversation, turn_id, said, reply, agent.name, [])
        say("handoff", agent.name, reply)
        self._launch(card["id"], agent, task, conversation, hop, effort, model)
        return Turn(reply, reply, "busy", agent.name, card["id"], True)

    def _resume(self, card: Dict[str, Any], agent: Agent, route: routing.Route, said: str,
                conversation: str, turn_id: str, hop: int, say: Report,
                effort: Optional[str] = None, model: Optional[str] = None) -> Turn:
        """主人在一件事里面点名让谁接着做：不另开卡，接在这件事上。"""
        task = route.text
        if route.reason == "mention" and agent.speaks:
            task = named_note(agent.name, task)
        reply = "好，告诉 %s 了" % agent.name
        done = self._amend(card, agent, "after", task, effort, model)
        if done == "full":
            reply = QUEUE_FULL_NOTE
        if done is None:
            # 这件事现在没人在做：让被点名的代理接着做一轮。它没做过这件事时带上背景。
            if not self._store.session("%s/%s" % (conversation, card["id"]), agent.name):
                recent = self._store.transcript.turns(conversation)[-BACKGROUND_TURNS:]
                if recent:
                    task = background(recent, task)
            self._record.exchange(card["id"], said=said, say=reply, turn_id=turn_id, scope="task", kind="handoff", brief=reply, mood="busy",
                               state="working", edits=card["edits"] + 1)
            self._launch(card["id"], agent, task, conversation, hop, effort, model)
        elif done == "full":
            self._record.exchange(card["id"], said=said, say=reply, turn_id=turn_id, scope="task", kind="handoff")
        else:
            self._record.exchange(card["id"], said=said, say=reply, turn_id=turn_id, scope="task", kind="handoff", edits=card["edits"] + 1)
            self._workspace.note(card, agent.name, task, done)
        self._store.transcript.add(conversation, turn_id, said, reply, agent.name, [])
        say("handoff", agent.name, reply)
        return Turn(reply, reply, "busy", agent.name, card["id"], True)

    def _desktop_agent(self, desktop: Dict[str, Any]) -> Optional[Agent]:
        """能接电脑上这类会话的代理：Codex 的会话交给 codex 类型，Claude 的交给 claude_code 类型。"""
        wanted = "codex" if desktop.get("agent") == "codex" else "claude_code"
        return next((agent for agent in self.agents() if agent.type == wanted), None)

    def _continue_desktop(self, desktop: Dict[str, Any], said: str, conversation: str,
                          turn_id: str, hop: int, say: Report, effort: Optional[str] = None,
                          model: Optional[str] = None, task: Optional[str] = None,
                          reply: Optional[str] = None) -> Turn:
        """接着电脑上的会话做一轮：另开一件事，在那个会话的目录里用它的会话编号跑。

        主人点名让帮手接着做时 task 就是主人的原话；小幽判断要接着做时 task 是她交代的话，
        reply 是她先跟主人说的。会话不归 Runtime：不记进会话表，电脑那边照常观察，回复也会
        出现在那个会话里；主人之后还能回到电脑上接着聊。电脑上这一轮还在跑时不接，免得两边
        同时改一个会话。
        """
        agent = self._desktop_agent(desktop)
        if agent is None:
            raise AgentError("这台 Runtime 上没有能接 %s 会话的代理" % (desktop.get("agent") or "这个"))
        if desktop.get("source") == "external":
            raise AgentError("这是别处上报来的会话，只能查看，请在它原来的地方继续")
        session, where = desktop.get("session_id"), desktop.get("desktop_cwd")
        if not session or not where:
            raise AgentError("这个电脑会话还没同步到会话编号和目录，过几秒再试")
        if desktop.get("desktop_status") in ("running", "waiting") and not desktop.get("stale"):
            raise AgentError("电脑上这一轮还在跑，等它停下再发")
        name = desktop.get("title") or "电脑上的会话"
        reply = reply or "好，让 %s 接着电脑上的「%s」做" % (agent.name, name[:30])
        card = self._record.task_open(conversation, "接着：" + name, "working", agent.name)
        self._record.exchange(card["id"], said=said, say=reply, turn_id=turn_id, kind="handoff",
                           brief=clip(reply, self._config.brief_max_chars), mood="busy",
                           borrowed_session=session, borrowed_cwd=where, desktop_card=desktop["id"])
        self._store.transcript.add(conversation, turn_id, said, reply, agent.name, [])
        say("handoff", agent.name, reply)
        self._launch(card["id"], agent, task or said, conversation, hop, effort, model)
        return Turn(reply, clip(reply, self._config.brief_max_chars), "busy", agent.name,
                    card["id"], True)

    # ---- 没指定：她自己接话 ----

    def _converse(self, lead: Agent, text: str, conversation: str, turn_id: str,
                  focus: Optional[Dict[str, Any]], available: List[Agent], hop: int,
                  say: Report, pinned: bool = False, effort: Optional[str] = None,
                  model: Optional[str] = None) -> Turn:
        now = time.time()
        # 太久没问过额度的账号，放到后台问一次；这句话用手头的数，不等它。
        self._usage.poke()
        cards = self._cards.recent(conversation)
        listed = [card for card in cards if card["state"] in ACTIVE]
        listed += [card for card in cards if card["state"] not in ACTIVE][-RECENT_FINISHED_CARDS:]
        listed.sort(key=lambda card: card["created_at"])
        if focus is not None and all(card["id"] != focus["id"] for card in listed):
            listed.insert(0, focus)
        system = lane_prompt(self._config.persona, self._config.brief_max_chars, available,
                             listed, focus["id"] if focus is not None else None, now, pinned,
                             self._usage.notes(),
                             any(self._usage.account(agent.name) for agent in available),
                             focus if focus is not None and focus.get("monitoring_only") else None)
        schema = lane_schema(available)
        stream = self._streamer(lead.name, say)
        outcome = self._speak(lead, text, conversation, system, schema, hop, catch_up=True,
                              on_text=stream)
        plan, problem = self._plan(outcome, conversation, focus, available, pinned)
        self._trace(outcome, problem)
        if problem is not None:
            # 刚才边说边给主人看的那段不算数了：收回来，重说的那一次从头再显示。
            say("partial", lead.name, "")
            stream = self._streamer(lead.name, say)
            outcome = self._speak(lead, retry_note(problem), conversation, system, schema, hop,
                                  catch_up=False, on_text=stream)
            plan, problem = self._plan(outcome, conversation, focus, available, pinned)
            self._trace(outcome, problem)
        fields = outcome.fields or {}
        reply = (self._field(outcome, "reply") or outcome.text).strip()
        mood, brief = fields.get("mood"), fields.get("brief")
        if problem is not None:
            # 说了两次都办不了：不替她编，照实告诉主人。
            plan = {"type": "none", "card": None}
            reply = "这件事我没办成：%s" % problem
            mood, brief = "oops", None
        kind = plan["type"]
        target: Optional[Dict[str, Any]] = plan["card"]
        if target is not None and target.get("mesh_origin"):
            # 别的 Runtime 的事（包括那台电脑上的会话）：这里只有副本。补充、接着做、取消交给那台；
            # 这句话和回答记在这里另开的一张卡上，不写进副本（副本的内容只由那台决定）。
            if kind in ("amend", "desktop") and self.mesh is not None:
                self.mesh.amend(target, plan["task"], continue_desktop=bool(target.get("monitoring_only")))
            elif kind == "cancel" and self.mesh is not None:
                self.mesh.cancel(target["id"])
            if kind in ("amend", "desktop", "cancel"):
                kind = "none"
            target = None
        if kind == "desktop":
            # 她判断要电脑上的那个会话接着干活：照主人点名时一样，另开一件接着那个会话做。
            try:
                return self._continue_desktop(target, text, conversation, turn_id, hop, say,
                                              plan.get("effort") or effort,
                                              plan.get("model") or model, plan["task"], reply)
            except AgentError as error:
                kind, reply, mood, brief = "none", "电脑上的会话这次接不了：%s" % error, "oops", None
        if target is not None and target.get("monitoring_only"):
            # 会话不归 Runtime：她的回答另记一件，不写进它。_plan 只给电脑会话 desktop（上面已经
            # 处理了）或 none，所以这里不会留下一个没有目标的补充。
            target = None
        if not reply:
            if kind == "none":
                raise AgentError("%s 返回了空的回复" % lead.name)
            reply = {"start": "交给 %s 了", "amend": "好，告诉 %s 了",
                     "cancel": "好，这件事不做了%s"}[kind] % (
                         plan.get("agent") or (target or {}).get("agent") or "")
        started = False
        worker = lead.name
        if kind == "none":
            if target is None:
                target = self._record.task_open(conversation, text)
            quiet = target["state"] in ACTIVE
            turn = shape(reply, brief, mood, self._config.brief_max_chars, lead.name, target["id"])
            # 那件事还在做时，卡上的简报和表情留给它的结果，不被一句闲聊盖掉。
            extra = {} if quiet else {"brief": turn.brief, "mood": turn.mood}
            self._record.exchange(target["id"], said=text, say=reply, turn_id=turn_id, scope="task" if pinned else "main", **extra)
        elif kind == "cancel":
            self.cancel(target["id"])
            self._record.exchange(target["id"], said=text, say=reply, turn_id=turn_id, scope="task" if pinned else "main")
            turn = shape(reply, brief, mood, self._config.brief_max_chars, lead.name, target["id"])
        elif kind == "amend":
            worker = target["agent"] or lead.name
            done = self._amend(target, self._agents[worker], plan["mode"], plan["task"],
                               plan.get("effort") or effort, plan.get("model") or model)
            if done == "full":
                reply = QUEUE_FULL_NOTE
                self._record.exchange(target["id"], said=text, say=reply, turn_id=turn_id, scope="task" if pinned else "main")
            elif done is None:
                # 那件事已经不在做了：接着它原来的会话再做一轮。
                self._record.exchange(target["id"], said=text, say=reply, turn_id=turn_id, scope="task" if pinned else "main", brief=clip(
                    reply, self._config.brief_max_chars), mood="busy", state="working",
                    edits=target["edits"] + 1)
                self._launch(target["id"], self._agents[worker], plan["task"], conversation, hop,
                             plan.get("effort") or effort, plan.get("model") or model)
            else:
                self._record.exchange(target["id"], said=text, say=reply, turn_id=turn_id, scope="task" if pinned else "main", edits=target["edits"] + 1)
                self._workspace.note(target, worker, plan["task"], done)
            say("handoff", worker, reply)
            started = True
            turn = shape(reply, brief, "busy", self._config.brief_max_chars, worker,
                         target["id"], True)
        else:
            helper = self._agents[plan["agent"]]
            worker = helper.name
            if target is None:
                target = self._record.task_open(
                    conversation, plan.get("title") or text, "working", helper.name,
                    title_source="task" if plan.get("title") else "request")
            self._record.exchange(target["id"], said=text, say=reply, turn_id=turn_id, scope="task" if pinned else "main", state="working",
                               agent=helper.name, mood="busy",
                               brief=clip(reply, self._config.brief_max_chars))
            say("handoff", helper.name, reply)
            self._launch(target["id"], helper, plan["task"], conversation, hop,
                         plan.get("effort") or effort, plan.get("model") or model)
            started = True
            turn = shape(reply, brief, "busy", self._config.brief_max_chars, helper.name,
                         target["id"], True)
        self._store.transcript.add(conversation, turn_id, text, reply, worker, [lead.name])
        return turn

    def _plan(self, outcome: Outcome, conversation: str, focus: Optional[Dict[str, Any]],
              available: List[Agent], pinned: bool = False) -> Any:
        """读出她要 Runtime 做什么。返回（计划，办不了的原因）；办得了时原因是 None。"""
        fields = outcome.fields or {}
        action = fields.get("action") if isinstance(fields.get("action"), dict) else {}
        kind = action.get("type") if action.get("type") in ACTIONS else "none"
        wanted = fields.get("card")
        wanted = wanted.strip().strip("「」“”\"'#").lower() if isinstance(wanted, str) else ""
        if wanted.isdigit():
            # 她常把 c3 写成 3。
            wanted = "c" + wanted
        target: Optional[Dict[str, Any]] = None
        if wanted and wanted != "new":
            target = self._cards.get(wanted)
            # 主人正看着的电脑上的会话不在这个对话里，也能指它。
            if target is None or (target["conversation"] != conversation and not (
                    focus is not None and target["id"] == focus["id"])):
                return None, "没有编号是 %s 的事" % clip(wanted, 20)
        elif kind in ("amend", "cancel") or (pinned and not (wanted == "new" and kind == "start")):
            # 没说是哪件：主人正看着的那件就是。主人在一件事里面说的话也归到它，
            # 除非她明说这是另一件新事（start 并写了 new）：那就另开一件，不改动它。
            target = focus
            if target is None:
                return None, "%s 要在 card 里写明是哪件事的编号" % kind
        plan: Dict[str, Any] = {"type": kind, "card": target}
        # 她给这一次选的努力程度和模型。那个帮手不认的，交出去时当作没选（见 _tier）。
        for key in ("effort", "model"):
            if isinstance(action.get(key), str) and action[key].strip():
                plan[key] = action[key].strip()
        names = [agent.name for agent in available]
        task = action.get("task") if isinstance(action.get("task"), str) else ""
        task = task.strip()[:MAX_TASK_CHARS]
        if target is not None and target.get("monitoring_only"):
            # 电脑上的会话：只能让它接着做（amend / start 都是），停要在电脑上停。
            if kind == "cancel":
                return None, "%s 是电脑上的会话，只能在电脑上停；要说别的就把 card 写 new" % target["id"]
            if kind in ("amend", "start"):
                if not task:
                    return None, "%s 要在 task 里写清让电脑上的会话接着做什么" % kind
                if self._desktop_agent(target) is None and not target.get("mesh_origin"):
                    # 别的 Runtime 的会话由那台去接，这台有没有能接的帮手无所谓。
                    return None, "这台 Runtime 上没有能接 %s 会话的帮手" % (target.get("agent") or "这个")
                plan.update(type="desktop", task=task)
            return plan, None
        if kind == "cancel":
            if target["state"] not in ACTIVE:
                return None, "%s 这件事已经不在做了，没有可取消的" % target["id"]
        elif kind == "start":
            if action.get("agent") not in names:
                return None, "没有叫 %s 的帮手（现在能找的：%s）" % (
                    clip(str(action.get("agent") or ""), 40), "、".join(names))
            if not task:
                return None, "start 要在 task 里写清交代给帮手的事"
            if target is not None and self._tasks.active(target["id"]):
                # 那件事正在做：再“开始”一次其实是追加。
                plan.update(type="amend", mode="after", task=task)
            else:
                title = action.get("title") if isinstance(action.get("title"), str) else ""
                plan.update(agent=action["agent"], task=task, title=title.strip())
        elif kind == "amend":
            if not task:
                return None, "amend 要在 task 里写清新的内容"
            if not self._tasks.active(target["id"]) and target["agent"] not in names:
                return None, "%s 这件事不是哪个帮手做的，没法接着做；要做就用 start 交给一个帮手" % (
                    target["id"])
            plan.update(task=task, mode="after" if action.get("mode") == "after" else "redo")
        return plan, None

    @staticmethod
    def _trace(outcome: Outcome, problem: Optional[str]) -> None:
        """设了 XIAOYOU_DEBUG 时，把她这一句决定归到哪张卡、要做什么打到标准错误。"""
        if os.environ.get("XIAOYOU_DEBUG"):
            fields = outcome.fields or {}
            sys.stderr.write("小幽的决定：card=%r action=%s%s\n" % (
                fields.get("card"), json.dumps(fields.get("action"), ensure_ascii=False),
                "；办不了：%s" % problem if problem else ""))
            sys.stderr.flush()

    @staticmethod
    def _field(outcome: Outcome, key: str) -> str:
        value = (outcome.fields or {}).get(key)
        return value if isinstance(value, str) else ""

    @staticmethod
    def _streamer(agent: str, say: Report) -> Callable[[str], None]:
        """把代理边生成边交出的原始全文，换成主人能看的 reply 片段报出去：有变化才报，
        而且不报得太勤。原始全文是空串表示这一次重来，已经报过的收回。"""
        state: Dict[str, Any] = {"shown": "", "at": None}

        def on_text(raw: str) -> None:
            if not raw:
                if state["shown"]:
                    state["shown"] = ""
                    say("partial", agent, "")
                return
            now = time.monotonic()
            if state["at"] is not None and now - state["at"] < PARTIAL_SECONDS:
                return
            shown = clean_display(reply_so_far(raw))
            if shown and shown != state["shown"]:
                state["shown"], state["at"] = shown, now
                say("partial", agent, shown)

        return on_text

    def _speak(self, agent: Agent, text: str, conversation: str, system: str,
               schema: Dict[str, Any], hop: int, catch_up: bool,
               on_text: Optional[Callable[[str], None]] = None) -> Outcome:
        """让能说话的代理在这个对话自己的会话里接一段话。只说话，不给工具。"""
        missed = self._store.transcript.unseen(conversation, agent.name) if catch_up else []
        outcome = agent.run(Job(
            text=recap(missed, text) if missed else text,
            session_id=self._store.session(conversation, agent.name),
            conversation=conversation, system=system, schema=schema, hop=hop, plain=True,
            timeout=self._config.voice_timeout_seconds, on_text=on_text,
        ))
        if outcome.session_id:
            self._store.remember(conversation, agent.name, outcome.session_id)
        if missed:
            self._store.transcript.mark(conversation, agent.name, [turn["id"] for turn in missed])
        return outcome

    # ---- 后台的事 ----

    def _tier(self, agent: Agent, card: Optional[Dict[str, Any]], effort: Optional[str],
              model: Optional[str]) -> Any:
        """这一次交给这个帮手用的（努力程度，点名要换的模型）。

        点的档、点的模型它不认时当作没点。没点时：这件事上一次就是它做的，沿用上一次的；
        否则用它平时的。模型是 None 表示用它平时的那个。
        """
        spec = agent.spec
        same = card is not None and card.get("agent") == agent.name
        if effort not in spec.efforts:
            effort = card["effort"] if same and card.get("effort") in spec.efforts else spec.effort
        if model == spec.model:
            model = None  # 点的就是它平时的那个
        elif model not in spec.models:
            model = (card["model_asked"]
                     if same and card.get("model_asked") in spec.models else None)
        return effort, model

    def _amend(self, card: Dict[str, Any], agent: Agent, mode: str, text: str,
               effort: Optional[str], model: Optional[str]) -> Optional[str]:
        """对一件正在做的事补充或改要求；顺带换档、换模型时卡上也跟着改。没在做返回 None。"""
        if effort not in agent.spec.efforts:
            effort = None
        if model not in agent.spec.models:
            model = None
        done = self._tasks.amend(card["id"], mode, text, effort, model)
        if done is not None:
            changes: Dict[str, Any] = {}
            if effort is not None and effort != card.get("effort"):
                changes["effort"] = effort
            if model is not None and model != card.get("model_asked"):
                changes.update(model_asked=model, model=model)
            if changes:
                self._record.task(card["id"], **changes)
        return done

    def _launch(self, card_id: str, agent: Agent, task: str, conversation: str, hop: int,
                effort: Optional[str] = None, model: Optional[str] = None) -> None:
        opened = self._cards.get(card_id) or {}
        borrowed = opened.get("borrowed_session") if opened.get("borrowed_cwd") else None
        # 借来的电脑会话是主人自己的：不给它加小幽的人设和回复格式。
        speaking = agent.speaks and borrowed is None
        effort, model = self._tier(agent, self._cards.get(card_id), effort, model)
        known = next((entry["model"] for entry in self._usage.snapshot()["agents"]
                      if entry["name"] == agent.name), None)
        task_run = Task(
            card=card_id, conversation=conversation, agent=agent, text=task, hop=hop,
            system=task_prompt(self._config.persona, self._config.brief_max_chars) if speaking else None,
            schema=reply_schema() if speaking else None, effort=effort, model=model,
            borrowed_session=borrowed, cwd=opened.get("borrowed_cwd") if borrowed else None,
        )
        # 交给网络里别的 Runtime 的帮手：卡上记着交给了哪台（那台替这件事开的卡会记着替谁做）。
        remote = ({"delegated_to": {"runtime": agent.peer, "name": agent.peer_name}}
                  if agent.type == "mesh" else {})
        card = self._record.task(card_id, state="working", agent=agent.name,
                                  started_at=time.time(), queued=self._config.max_parallel > 0,
                                  fresh=True, effort=effort, model=model or known, model_asked=model,
                                  run_id=task_run.run_id, result_ready=False, summary_partial="",
                                  **remote)
        if card is not None:
            self._workspace.task(card, agent.name, task)
        self._tasks.start(task_run)

    def _begun(self, task: Task) -> None:
        """轮到这件事了（设了并行上限时，前面可能排过队）。"""
        if self._config.max_parallel > 0:
            self._record.task(task.card, queued=False, started_at=time.time())

    def _prepare(self, task: Task, control: Control) -> None:
        """一轮开始前：进展记到卡上；要问“可以吗”的帮手，给它一把通到主人那里的钥匙。"""
        round_id = uuid.uuid4().hex

        def activity(phase: str, text: str, key: Optional[str] = None) -> None:
            event_id = "%s-activity-%s-%s" % (round_id, phase, key or uuid.uuid4().hex)
            self._record.activity(task.card, task.agent.name, phase, text, event_id)

        control.activity = activity
        control.progress = lambda line: self._record.progress(task.card, line)
        control.scratch = self._config.state_dir
        if task.agent.type in ("claude_code", "codex", "dsh"):
            task.artifact_dir = str(self.artifacts.task_dir(task.card))
        name = task.agent.name

        def model(seen: str, task: Task = task) -> None:
            # 它实际用的模型：记到用量那边，卡上写的不一样就改过来。
            self._usage.saw_model(task.agent.name, seen, usual=task.model is None)
            card = self._cards.get(task.card)
            if card is not None and card["model"] != seen:
                self._record.task(task.card, model=seen)

        control.model = model
        control.limits = lambda kind, payload, name=name: self._usage.observe(name, kind, payload)
        # 这一轮开始：记下额度现在剩多少（太久没问过就先问一次），做完好算花了多少。
        self._runs[control] = self._usage.begin(name, task.model)
        if task.agent.type == "claude_code":
            # Claude Code 的询问由另一个进程（权限询问工具）送进来，要一把钥匙。
            control.gate = self._gate.open(task.card, task.conversation, task.agent.name)

        def ask(tool: str, detail: str, task: Task = task) -> bool:
            # 代理自己收到询问的（Codex 的 app-server）直接从这里问。
            approval = self._approvals.ask(
                task.card, task.conversation, task.agent.name, tool, detail)
            return self._approvals.wait(approval) == "allow"

        control.ask = ask
        # 等主人点头的时间不算在时限里。
        control.paused = lambda card=task.card: self._approvals.waiting(card)

        def stalled(idle: float, step: str, moot: Callable[[], bool], task: Task = task) -> str:
            # 很久没动静了：不直接停（误杀一件做了很久的事代价大），先问主人还等不等。
            name = task.agent.name
            detail = "%s 已经 %s没有动静了，接着等吗？\n最后一步：%s\n可以 = 接着等\n不行 = 停掉。%s" % (
                name, span(idle), clip(step, 200) if step else "（还没有做过操作）", RESUME_HINT)
            approval = self._approvals.ask(
                task.card, task.conversation, name, "%s · 没动静" % name, detail, kind=STALL)
            decision = self._approvals.wait(
                approval, self._config.idle_answer_seconds or None, moot)
            if decision is None:
                moved = moot()
                self._approvals.withdraw(approval)
                return MOVED if moved else SILENT
            return WAIT if decision == "allow" else STOP

        control.stalled = stalled

    def _release(self, task: Task, control: Control) -> None:
        """一轮结束（做完、被停掉都算）：钥匙作废，还没答的授权不用等了；记下这一轮花了多少。"""
        self._gate.shut(control.gate)
        self._approvals.drop(task.card)
        run = self._runs.pop(control, None)
        if run is None:
            return
        spent = dict(control.tokens)
        spent.update(self._usage.end(run))
        card = self._cards.get(task.card)
        if card is not None and any(spent.values()):
            cost = dict(card["cost"] or {})
            for key, value in spent.items():
                cost[key] = cost.get(key, 0) + value
            self._record.task(task.card, cost=cost)

    def _approval_changed(self, card_id: str, approval_id: Optional[str]) -> None:
        """一件事在等主人点头，或者不用等了。"""
        card = self._cards.get(card_id)
        if card is None or card.get("mesh_origin"):
            return  # 别的 Runtime 的事：卡上的授权和状态由那台报来
        if card.get("monitoring_only"):
            # The desktop adapter owns its observed state; an approval is a
            # separate response channel, not control of the desktop card.
            self._record.task(card_id, approval=approval_id)
            return
        if card["state"] not in ACTIVE:
            if card["approval"] is not None:
                self._record.task(card_id, approval=None)
            return
        self._record.task(card_id, approval=approval_id,
                           state="waiting" if approval_id is not None else "working")

    def _finished(self, task: Task, outcome: Optional[Outcome], error: Optional[str]) -> None:
        """后台的事做完或没做成。在任务自己的线程里被调用。"""
        name = task.agent.name
        current = self._cards.get(task.card)
        if current is None or current["state"] == "cancelled" or current.get("run_id") != task.run_id:
            return
        if outcome is not None:
            if not outcome.fields:
                try:
                    parsed = json.loads(outcome.text)
                except (ValueError, TypeError):
                    parsed = None
                if isinstance(parsed, dict) and isinstance(parsed.get("reply"), str):
                    outcome = Outcome(outcome.text, outcome.session_id, parsed)
            task.artifacts = self.artifacts.register_task(
                self._cards, task.card, outcome.text, outcome.fields, name)
            full = self._field(outcome, "reply") or outcome.text
            if len(full) > MAX_ENTRY_CHARS:
                try:
                    task.artifacts.append(self.artifacts.save_text(
                        task.card, full, name="完整方案与回复.txt", source=name))
                    task.full_reply_saved = True
                except ArtifactError:
                    # Preserve the reply and make the failed attachment visible.
                    task.artifact_error = "完整回复保存失败，请稍后重试"
            note = "\n\n（内容较长，完整原文见附件。）" if task.full_reply_saved else ""
            original = full if not note else full[:MAX_ENTRY_CHARS - len(note)] + note
            self._record.reply(task.card, say=original, turn_id=task.run_id + "-result",
                               scope="task", kind="worker_result", author=name,
                               result_ready=True, attachments=task.artifacts)
        card = self._cards.get(task.card)
        if card is not None and card["state"] != "cancelled":
            # 代理交回的原样记一条，不管之后小幽怎么转述。
            self._workspace.result(card, name, outcome.text if outcome is not None else (
                error or "没有说明"), outcome is not None)
        if outcome is None:
            self._close(task, "%s 没做成：%s" % (name, error or "没有说明"), None, "oops",
                        "failed", [])
        elif task.system is not None:
            # 帮手是以小幽的身份做的：它的结果就是小幽的话。
            fields = outcome.fields or {}
            reply = fields.get("reply") if isinstance(fields.get("reply"), str) else ""
            self._close(task, reply.strip() or outcome.text, fields.get("brief"),
                        fields.get("mood"), "done", [])
        else:
            self.defer(task.conversation, lambda: self._relay(
                task, self._field(outcome, "reply") or outcome.text))

    def _relay(self, task: Task, result: str) -> None:
        """只会干活的帮手交回了结果：让说话的代理用小幽的话转述。"""
        card = self._cards.get(task.card) or {}
        if card.get("run_id") != task.run_id or card.get("state") == "cancelled":
            return
        def report(kind: str, agent: Optional[str], text: str) -> None:
            current = self._cards.get(task.card)
            if kind == "partial" and current and current.get("run_id") == task.run_id and current["state"] in ACTIVE:
                self._record.task(task.card, summary_partial=text)
        voice = self._voice(self._available(task.hop))
        if voice is not None and voice.name != task.agent.name:
            try:
                outcome = self._speak(
                    voice, relay(task.agent.name, card.get("title", ""), result),
                    task.conversation, voice_prompt(
                        self._config.persona, self._config.brief_max_chars),
                    reply_schema(), task.hop, catch_up=True,
                    on_text=self._streamer(voice.name, report))
                reply = (self._field(outcome, "reply") or outcome.text).strip()
                if reply:
                    fields = outcome.fields or {}
                    self._close(task, reply, fields.get("brief"), fields.get("mood"), "done",
                                [voice.name])
                    return
            except AgentError:
                pass  # 说话的代理不在：结果已经有了，原样给主人，好过让这件事白做
        self._close(task, result, None, "idle", "done", [])

    def _close(self, task: Task, reply: str, brief: Any, mood: Any, state: str,
               seen: List[str]) -> None:
        turn = shape(reply, brief, mood, self._config.brief_max_chars, task.agent.name)
        card = self._cards.get(task.card)
        if card is None or card["state"] == "cancelled" or card.get("run_id") != task.run_id:
            return
        display_reply = turn.reply
        if task.system is None and len(display_reply) > MAX_ENTRY_CHARS:
            try:
                task.artifacts.append(self.artifacts.save_text(task.card, turn.reply,
                                      name="完整结果总结.txt", source="xiaoyou"))
                task.full_reply_saved = True
            except ArtifactError:
                task.artifact_error = "完整总结保存失败，请稍后重试"
        if len(display_reply) > MAX_ENTRY_CHARS:
            note = "\n\n（内容较长，完整内容见附件。）" if task.full_reply_saved else (
                "\n\n（内容较长，当前仅显示部分内容。）")
            display_reply = display_reply[:MAX_ENTRY_CHARS - len(note)] + note
        if task.artifact_error:
            display_reply = display_reply[:MAX_ENTRY_CHARS - len(task.artifact_error) - 2] + (
                "\n\n" + task.artifact_error)
        # 先记进对话记录，再让卡结束：看到这件事结束的人，接着就能在对话记录里看到结果。
        # 说话的代理下次接话时会被告知这个结果。
        self._store.transcript.add(
            task.conversation, "%s-%d" % (task.card, int(time.time() * 1000)),
            "（后台的事「%s」有结果了）" % card["title"], turn.reply, task.agent.name, seen,
            reply_ref=task.run_id + "-summary:reply")
        self._record.reply(task.card, say=display_reply, brief=turn.brief, mood=turn.mood,
                           state=state, approval=None, queued=False, attachments=task.artifacts,
                           turn_id=task.run_id + "-summary", scope="main", kind="summary",
                           result_ready=True, summary_partial="")
        self._workspace.closed(card, state, turn.brief, turn.reply, turn.mood)

    def cancel(self, card_id: str) -> bool:
        """取消一件正在做的事；它不在做时返回 False。"""
        card = self._cards.get(card_id)
        if card is None or card["state"] not in ACTIVE:
            return False
        if card.get("mesh_origin"):
            # 别的 Runtime 的事：叫那台停，状态等它报回来。
            return bool(self.mesh is not None and self.mesh.cancel(card_id))
        self._record.task(card_id, state="cancelled", brief="取消了", mood="idle",
                           approval=None, queued=False)
        self._tasks.cancel(card_id)
        self._approvals.drop(card_id)
        if card["agent"]:
            self._workspace.cancelled(card)
        return True

    def settle(self, card_id: str, timeout: float) -> Optional[Dict[str, Any]]:
        """等一件事结束，最多等 timeout 秒。"""
        return self._cards.settle(card_id, timeout)

    # ---- 对话记录 ----

    def reset(self, conversation: str) -> bool:
        """让这个对话从头开始：所有代理的会话都忘掉，对话记录清空。"""
        had_turns = self._store.transcript.clear(conversation)
        return self._store.forget(conversation) or had_turns

    def _final_text(self, message_id: str) -> Optional[str]:
        """消息日志里这条定稿消息的文字；没有、还没定稿时是 None。"""
        message = self._cards.message(message_id)
        if message is None or message.get("status", "final") != "final":
            return None
        return message.get("text")

    def share(self, conversation: str, turns: List[Dict[str, Any]]) -> int:
        return self._store.transcript.offer(conversation, turns)
