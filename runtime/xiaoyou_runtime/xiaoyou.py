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
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import router as routing
from .agents import Agent, AgentError, Control, Job, Outcome, clip
from .approvals import Approvals, Gate
from .cards import ACTIVE, Cards, title_from
from .config import Config
from .store import Store
from .tasks import Task, Tasks
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


def shape(reply: str, brief: Any, mood: Any, limit: int, agent: str, card: str = "",
          started: bool = False) -> Turn:
    """把代理给出的原始字段整理成一轮结果：缺的补上，超长的截断，非法的表情改成 idle。"""
    reply = (reply or "").strip()
    brief_text = brief.strip() if isinstance(brief, str) else ""
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


def lane_prompt(persona: str, brief_max_chars: int, helpers: List[Agent],
                cards: List[Dict[str, Any]], focus: Optional[str], now: float,
                pinned: bool = False) -> str:
    """小幽接主人一句话时的系统提示：人设、她怎么做事、回复格式、帮手、现在的事。"""
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
            parts.append("- %s：%s" % (agent.name, agent.description or "（没有说明）"))
        parts.append("自己能答的直接答，不要为了用而用。一句话只能交给一个帮手。")
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
            "主人是打开 %s 这件事、在它里面说的这句话：这句话就归到它，card 写 %s，不要写 new。"
            "能直接答的直接答；要动手就接着这件事做（它还在做用 amend，已经结束了用 amend 或 "
            "start，都会接着它原来的会话），不要另开一件事。" % (focus, focus))
    elif focus is not None:
        parts.append(
            "主人说这句话时正看着 %s。没有别的线索时，“这个”“它”“这件事”指的就是它。" % focus)
    return "\n".join(parts)


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
        result = result[:RESULT_CHARS]
        cut = "\n（结果太长，后面的没有贴出来。）"
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
                 workspace: Optional[Workspace] = None):
        self._config = config
        # 共享工作区：每次把活交给别的代理、每次有结果，都在那里留一条。
        self._workspace = workspace if workspace is not None else Workspace()
        self._agents: Dict[str, Agent] = {agent.name: agent for agent in agents}
        self._order = [agent.name for agent in agents]
        self._store = store
        self._router = router if router is not None else routing.Router()
        self._cards = Cards(config.state_dir / "cards.json")
        self._approvals = Approvals(self._approval_changed)
        self._gate = Gate(self._approvals)
        self._tasks = Tasks(store, self._finished, config.max_parallel, self._begun,
                            self._prepare, self._release)
        # 只会干活的帮手做完后，转述要回到那个对话自己的线上排队；没有人排队（命令行里
        # 说一句就走）时就地做。Service 启动时把它换成“排到这个对话的线上”。
        self.defer: Callable[[str, Callable[[], None]], None] = lambda conversation, work: work()

    @property
    def default_agent(self) -> str:
        return self._config.default_agent

    @property
    def cards(self) -> Cards:
        return self._cards

    @property
    def approvals(self) -> Approvals:
        return self._approvals

    def agents(self) -> List[Agent]:
        return [self._agents[name] for name in self._order]

    def has(self, name: str) -> bool:
        return name in self._agents

    def close(self) -> None:
        self._tasks.close()
        self._gate.close()
        self._workspace.close()

    def _available(self, hop: int) -> List[Agent]:
        # 已经是别的 Runtime 转过来的话，不再往外转：两台互相登记时不会来回踢。
        return [agent for agent in self.agents() if hop == 0 or agent.type != "remote"]

    def _voice(self, available: List[Agent]) -> Optional[Agent]:
        """替小幽转述的代理：配置里指定的那个；它这一轮用不了就找第一个能说话的。"""
        speakers = [agent for agent in available if agent.speaks]
        for agent in speakers:
            if agent.name == self._config.voice_agent:
                return agent
        return speakers[0] if speakers else None

    def hear(self, text: str, conversation: str, turn_id: str, asked: Optional[str] = None,
             card: Optional[str] = None, hop: int = 0, report: Optional[Report] = None,
             pin: bool = False) -> Turn:
        """接主人的一句话。很快返回：要花时间的部分已经交到后台，结果之后出现在卡上。

        card 是主人说这句话时屏幕上的那件事（没有就是 None）。pin 表示主人是打开那件事、
        在它里面说的：这句话一定归到它，不另开卡。失败时抛 AgentError，消息可以直接给主人看。
        """
        say: Report = report if report is not None else (lambda kind, agent, detail: None)
        available = self._available(hop)
        if not available:
            raise AgentError("这台 Runtime 上没有能接这句话的代理")
        route = routing.decide(text, asked, available, self._config.default_agent, self._router)
        first = self._agents[route.agent]
        say("route", first.name, route.reason)
        focus = self._cards.get(card) if card else None
        if focus is not None and focus["conversation"] != conversation:
            focus = None
        pinned = pin and focus is not None
        if route.reason != "default" or not first.speaks:
            # 指定了由谁做（或者默认代理只会干活）：不用问模型，直接交过去。
            if pinned and (focus["agent"] in (None, first.name)
                           or not self._tasks.active(focus["id"])):
                return self._resume(focus, first, route, text, conversation, turn_id, hop, say)
            return self._assign(first, route, text, conversation, turn_id, hop, say)
        return self._converse(first, text, conversation, turn_id, focus, available, hop, say,
                              pinned)

    # ---- 指定了由谁做 ----

    def _assign(self, agent: Agent, route: routing.Route, said: str, conversation: str,
                turn_id: str, hop: int, say: Report) -> Turn:
        reply = "交给 %s 了" % agent.name
        card = self._cards.open(conversation, route.text, "working", agent.name)
        self._cards.update(card["id"], said=said, say=reply, brief=reply, mood="busy")
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
        self._launch(card["id"], agent, task, conversation, hop)
        return Turn(reply, reply, "busy", agent.name, card["id"], True)

    def _resume(self, card: Dict[str, Any], agent: Agent, route: routing.Route, said: str,
                conversation: str, turn_id: str, hop: int, say: Report) -> Turn:
        """主人在一件事里面点名让谁接着做：不另开卡，接在这件事上。"""
        task = route.text
        if route.reason == "mention" and agent.speaks:
            task = named_note(agent.name, task)
        reply = "好，告诉 %s 了" % agent.name
        done = self._tasks.amend(card["id"], "after", task)
        if done is None:
            # 这件事现在没人在做：让被点名的代理接着做一轮。它没做过这件事时带上背景。
            if not self._store.session("%s/%s" % (conversation, card["id"]), agent.name):
                recent = self._store.transcript.turns(conversation)[-BACKGROUND_TURNS:]
                if recent:
                    task = background(recent, task)
            self._cards.update(card["id"], said=said, say=reply, brief=reply, mood="busy",
                               state="working", edits=card["edits"] + 1)
            self._launch(card["id"], agent, task, conversation, hop)
        else:
            self._cards.update(card["id"], said=said, say=reply, edits=card["edits"] + 1)
            self._workspace.note(card, agent.name, task, done)
        self._store.transcript.add(conversation, turn_id, said, reply, agent.name, [])
        say("handoff", agent.name, reply)
        return Turn(reply, reply, "busy", agent.name, card["id"], True)

    # ---- 没指定：她自己接话 ----

    def _converse(self, lead: Agent, text: str, conversation: str, turn_id: str,
                  focus: Optional[Dict[str, Any]], available: List[Agent], hop: int,
                  say: Report, pinned: bool = False) -> Turn:
        now = time.time()
        cards = self._cards.recent(conversation)
        listed = [card for card in cards if card["state"] in ACTIVE]
        listed += [card for card in cards if card["state"] not in ACTIVE][-RECENT_FINISHED_CARDS:]
        listed.sort(key=lambda card: card["created_at"])
        if focus is not None and all(card["id"] != focus["id"] for card in listed):
            listed.insert(0, focus)
        system = lane_prompt(self._config.persona, self._config.brief_max_chars, available,
                             listed, focus["id"] if focus is not None else None, now, pinned)
        schema = lane_schema(available)
        outcome = self._speak(lead, text, conversation, system, schema, hop, catch_up=True)
        plan, problem = self._plan(outcome, conversation, focus, available, pinned)
        self._trace(outcome, problem)
        if problem is not None:
            outcome = self._speak(lead, retry_note(problem), conversation, system, schema, hop,
                                  catch_up=False)
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
                target = self._cards.open(conversation, text)
            quiet = target["state"] in ACTIVE
            turn = shape(reply, brief, mood, self._config.brief_max_chars, lead.name, target["id"])
            # 那件事还在做时，卡上的简报和表情留给它的结果，不被一句闲聊盖掉。
            extra = {} if quiet else {"brief": turn.brief, "mood": turn.mood}
            self._cards.update(target["id"], said=text, say=reply, **extra)
        elif kind == "cancel":
            self.cancel(target["id"])
            self._cards.update(target["id"], said=text, say=reply)
            turn = shape(reply, brief, mood, self._config.brief_max_chars, lead.name, target["id"])
        elif kind == "amend":
            done = self._tasks.amend(target["id"], plan["mode"], plan["task"])
            worker = target["agent"] or lead.name
            if done is None:
                # 那件事已经不在做了：接着它原来的会话再做一轮。
                self._cards.update(target["id"], said=text, say=reply, brief=clip(
                    reply, self._config.brief_max_chars), mood="busy", state="working",
                    edits=target["edits"] + 1)
                self._launch(target["id"], self._agents[worker], plan["task"], conversation, hop)
            else:
                self._cards.update(target["id"], said=text, say=reply, edits=target["edits"] + 1)
                self._workspace.note(target, worker, plan["task"], done)
            say("handoff", worker, reply)
            started = True
            turn = shape(reply, brief, "busy", self._config.brief_max_chars, worker,
                         target["id"], True)
        else:
            helper = self._agents[plan["agent"]]
            worker = helper.name
            if target is None:
                target = self._cards.open(
                    conversation, plan.get("title") or text, "working", helper.name)
            self._cards.update(target["id"], said=text, say=reply, state="working",
                               agent=helper.name, mood="busy",
                               brief=clip(reply, self._config.brief_max_chars))
            say("handoff", helper.name, reply)
            self._launch(target["id"], helper, plan["task"], conversation, hop)
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
            if target is None or target["conversation"] != conversation:
                return None, "没有编号是 %s 的事" % clip(wanted, 20)
        elif pinned or kind in ("amend", "cancel"):
            # 没说是哪件：主人正看着的那件就是。主人在一件事里面说的话，写了 new 也归到它。
            target = focus
            if target is None:
                return None, "%s 要在 card 里写明是哪件事的编号" % kind
        plan: Dict[str, Any] = {"type": kind, "card": target}
        names = [agent.name for agent in available]
        task = action.get("task") if isinstance(action.get("task"), str) else ""
        task = task.strip()[:MAX_TASK_CHARS]
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

    def _speak(self, agent: Agent, text: str, conversation: str, system: str,
               schema: Dict[str, Any], hop: int, catch_up: bool) -> Outcome:
        """让能说话的代理在这个对话自己的会话里接一段话。只说话，不给工具。"""
        missed = self._store.transcript.unseen(conversation, agent.name) if catch_up else []
        outcome = agent.run(Job(
            text=recap(missed, text) if missed else text,
            session_id=self._store.session(conversation, agent.name),
            conversation=conversation, system=system, schema=schema, hop=hop, plain=True,
            timeout=self._config.voice_timeout_seconds,
        ))
        if outcome.session_id:
            self._store.remember(conversation, agent.name, outcome.session_id)
        if missed:
            self._store.transcript.mark(conversation, agent.name, [turn["id"] for turn in missed])
        return outcome

    # ---- 后台的事 ----

    def _launch(self, card_id: str, agent: Agent, task: str, conversation: str, hop: int) -> None:
        speaking = agent.speaks
        card = self._cards.update(card_id, state="working", agent=agent.name,
                                  started_at=time.time(),
                                  queued=self._config.max_parallel > 0, fresh=True)
        if card is not None:
            self._workspace.task(card, agent.name, task)
        self._tasks.start(Task(
            card=card_id, conversation=conversation, agent=agent, text=task, hop=hop,
            system=task_prompt(self._config.persona, self._config.brief_max_chars)
            if speaking else None,
            schema=reply_schema() if speaking else None,
        ))

    def _begun(self, task: Task) -> None:
        """轮到这件事了（设了并行上限时，前面可能排过队）。"""
        if self._config.max_parallel > 0:
            self._cards.update(task.card, queued=False, started_at=time.time())

    def _prepare(self, task: Task, control: Control) -> None:
        """一轮开始前：进展记到卡上；要问“可以吗”的帮手，给它一把通到主人那里的钥匙。"""
        control.progress = lambda line, card=task.card: self._cards.update(card, progress=line)
        control.scratch = self._config.state_dir
        if task.agent.type == "claude_code":
            # Claude Code 的询问由另一个进程（权限询问工具）送进来，要一把钥匙。
            control.gate = self._gate.open(task.card, task.conversation, task.agent.name)

        def ask(tool: str, detail: str, task: Task = task) -> bool:
            # 代理自己收到询问的（Codex 的 app-server）直接从这里问。
            approval = self._approvals.ask(
                task.card, task.conversation, task.agent.name, tool, detail)
            return self._approvals.wait(approval) == "allow"

        control.ask = ask

    def _release(self, task: Task, control: Control) -> None:
        """一轮结束（做完、被停掉都算）：钥匙作废，还没答的授权不用等了。"""
        self._gate.shut(control.gate)
        self._approvals.drop(task.card)

    def _approval_changed(self, card_id: str, approval_id: Optional[str]) -> None:
        """一件事在等主人点头，或者不用等了。"""
        card = self._cards.get(card_id)
        if card is None:
            return
        if card["state"] not in ACTIVE:
            if card["approval"] is not None:
                self._cards.update(card_id, approval=None)
            return
        self._cards.update(card_id, approval=approval_id,
                           state="waiting" if approval_id is not None else "working")

    def _finished(self, task: Task, outcome: Optional[Outcome], error: Optional[str]) -> None:
        """后台的事做完或没做成。在任务自己的线程里被调用。"""
        name = task.agent.name
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
            self.defer(task.conversation, lambda: self._relay(task, outcome.text))

    def _relay(self, task: Task, result: str) -> None:
        """只会干活的帮手交回了结果：让说话的代理用小幽的话转述。"""
        card = self._cards.get(task.card) or {}
        voice = self._voice(self._available(task.hop))
        if voice is not None and voice.name != task.agent.name:
            try:
                outcome = self._speak(
                    voice, relay(task.agent.name, card.get("title", ""), result),
                    task.conversation, voice_prompt(
                        self._config.persona, self._config.brief_max_chars),
                    reply_schema(), task.hop, catch_up=True)
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
        if card is None or card["state"] == "cancelled":
            return
        self._cards.update(task.card, say=turn.reply, brief=turn.brief, mood=turn.mood,
                           state=state, approval=None, queued=False)
        # 记进对话记录：说话的代理下次接话时会被告知这个结果。
        self._workspace.closed(card, state, turn.brief, turn.reply, turn.mood)
        self._store.transcript.add(
            task.conversation, "%s-%d" % (task.card, int(time.time() * 1000)),
            "（后台的事「%s」有结果了）" % card["title"], turn.reply, task.agent.name, seen)

    def cancel(self, card_id: str) -> bool:
        """取消一件正在做的事；它不在做时返回 False。"""
        card = self._cards.get(card_id)
        if card is None or card["state"] not in ACTIVE:
            return False
        self._cards.update(card_id, state="cancelled", brief="取消了", mood="idle",
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

    def share(self, conversation: str, turns: List[Dict[str, Any]]) -> int:
        return self._store.transcript.offer(conversation, turns)
