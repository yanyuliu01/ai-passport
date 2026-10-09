"""小幽本人。

小幽是一个独立的身份，不是某个模型的别名。属于她的东西都在这里和 store.py 里：
人设、她和主人的对话记录、这句话交给谁的判断、别的代理做完之后怎么转述。Claude、
Codex 和以后接进来的本地模型，都只是她能用的代理（agents.py）。

一轮是这样走的：

  1. 路由（router.py）决定这句话先交给哪个代理。
  2. 那个代理能以小幽的身份回话：把人设、回复格式、“你还有哪些帮手”一起给它。
     它可以直接回答，也可以在回复里说“这件事交给某某”；Runtime 去找那个帮手干活，
     把结果交回来，由它用小幽的话总结。可以连着转交几次，有上限。
  3. 那个代理只会干活：让它做，结果交给负责说话的代理转述。说话的代理不在时，
     原样把结果给主人，不让主人空手而归。
  主人点了名（“@claude ……”“让 Codex 看看……”）的那个代理，不管它会不会说话，都算
  小幽把这件事交给了它：调用方看得见是谁在做，这一轮也不会再转给别人。
  4. 这一轮记进小幽的对话记录，并记下哪些代理见过。下次轮到没见过的代理接话，
     先把它错过的几轮告诉它。
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import router as routing
from .agents import Agent, AgentError, Job, Outcome, clip
from .config import Config
from .store import Store

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

# 向调用方报告这一轮走到了哪一步：(kind, agent, text)。
Report = Callable[[str, Optional[str], str], None]


@dataclass(frozen=True)
class Turn:
    reply: str  # 完整回复，给手机 App 显示
    brief: str  # 给小屏幕的一两句话
    mood: str  # 小幽此刻的表情，取值见 MOODS
    agent: str  # 这一轮先接话的代理
    helpers: List[str] = field(default_factory=list)  # 被转交过活的代理，按先后


def shape(reply: str, brief: Any, mood: Any, limit: int, agent: str,
          helpers: Optional[List[str]] = None) -> Turn:
    """把代理给出的原始字段整理成一轮结果：缺的补上，超长的截断，非法的表情改成 idle。"""
    reply = (reply or "").strip()
    brief_text = brief.strip() if isinstance(brief, str) else ""
    return Turn(
        reply=reply,
        brief=clip(brief_text or reply, limit),
        mood=mood if mood in MOODS else "idle",
        agent=agent,
        helpers=list(helpers or []),
    )


def reply_schema(helpers: List[Agent]) -> Dict[str, Any]:
    """说话的代理每一轮要给出的结构；有帮手可用时多一个可选的 handoff。"""
    properties: Dict[str, Any] = {
        "reply": {"type": "string"},
        "brief": {"type": "string"},
        "mood": {"type": "string", "enum": list(MOODS)},
    }
    if helpers:
        properties["handoff"] = {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "enum": [agent.name for agent in helpers]},
                "task": {"type": "string"},
            },
            "required": ["agent", "task"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": properties,
        "required": ["reply", "brief", "mood"],
        "additionalProperties": False,
    }


def system_prompt(persona: str, brief_max_chars: int, helpers: List[Agent]) -> str:
    """人设 + 回复格式 + 现在能找的帮手。"""
    parts = [persona, ""]
    parts.append("## 回复格式")
    parts.append(
        "每一轮只输出一个 JSON 对象，按给定的结构：reply 是给主人看的完整回复；brief 是显示在"
        "随身小屏幕上的一两句话，不超过 %d 个字，要让人不看 reply 也知道结论，只用普通文字和标点，"
        "不用表情符号（小屏幕显示不了）；mood 是你此刻的"
        "表情，只能是 idle（平常）、busy（还在忙）、ask（需要主人拿主意）、happy（顺利完成）、"
        "oops（出了问题）之一。" % brief_max_chars
    )
    parts.append("")
    parts.append("## 你的帮手")
    if helpers:
        for agent in helpers:
            parts.append("- %s：%s" % (agent.name, agent.description or "（没有说明）"))
        parts.append(
            "自己能答的直接答，不要为了用而用。自己做不了、而上面某个帮手更合适时，在这一轮的"
            "回复里加上 handoff：agent 写帮手的名字，task 写交代给它的任务——它看不到你和主人的"
            "对话，所以把背景和想要的结果写清楚。这时 reply 写一句你要先跟主人说的话"
            "（比如你去找谁做什么），mood 用 busy。帮手做完后，结果会交回给你，由你用自己的话"
            "总结给主人，不要原样转贴一大段输出。一轮只能交给一个帮手。"
        )
    else:
        parts.append("现在没有别的帮手，你只能靠自己回答。做不到的事直接说做不到。")
    return "\n".join(parts)


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
    """主人点名的代理正好也是替小幽说话的那个：告诉它这件事它自己做。主人看不到这段。"""
    return (
        "（主人点名要 %s 来做这件事。你现在用的就是 %s，所以直接做，不要转交给别的帮手，"
        "也不用向主人解释这一点。）\n\n%s" % (agent, agent, text)
    )


def relay(helper: str, result: Optional[str], error: Optional[str],
          asked: Optional[str] = None) -> str:
    """帮手做完（或没做成）之后，交回给说话的代理的那段话。主人看不到这段。"""
    lead = ""
    if asked is not None:
        lead = "（主人点名让 %s 做这件事，原话是：%s）\n" % (helper, asked)
    if error is not None:
        return (
            "%s（帮手 %s 没做成：%s。主人看不到这段。照实告诉主人没做成和原因，"
            "不要把没做成的事说成做成了。）" % (lead, helper, error)
        )
    body = result or ""
    cut = ""
    if len(body) > RESULT_CHARS:
        body = body[:RESULT_CHARS]
        cut = "\n（结果太长，后面的没有贴出来。）"
    return (
        "%s（帮手 %s 做完了。下面是它交回的原始结果，主人看不到这段。用你自己的话告诉主人："
        "做了什么、结论是什么、有没有需要主人决定的事。）\n\n%s%s" % (lead, helper, body, cut)
    )


class Xiaoyou:
    def __init__(self, config: Config, agents: List[Agent], store: Store,
                 router: Optional[routing.Router] = None):
        self._config = config
        self._agents: Dict[str, Agent] = {agent.name: agent for agent in agents}
        self._order = [agent.name for agent in agents]
        self._store = store
        self._router = router if router is not None else routing.Router()

    @property
    def default_agent(self) -> str:
        return self._config.default_agent

    def agents(self) -> List[Agent]:
        return [self._agents[name] for name in self._order]

    def has(self, name: str) -> bool:
        return name in self._agents

    def _available(self, hop: int) -> List[Agent]:
        # 已经是别的 Runtime 转过来的话，不再往外转：两台互相登记时不会来回踢。
        return [agent for agent in self.agents() if hop == 0 or agent.type != "remote"]

    def answer(self, text: str, conversation: str, turn_id: str, asked: Optional[str] = None,
               hop: int = 0, report: Optional[Report] = None) -> Turn:
        """回答主人的一句话。失败时抛 AgentError，消息可以直接给主人看。"""
        say: Report = report if report is not None else (lambda kind, agent, detail: None)
        available = self._available(hop)
        if not available:
            raise AgentError("这台 Runtime 上没有能接这句话的代理")
        route = routing.decide(text, asked, available, self._config.default_agent, self._router)
        first = self._agents[route.agent]
        say("route", first.name, route.reason)
        witnesses: List[str] = []
        if first.speaks:
            named = route.reason in ("asked", "mention")
            if named:
                # 主人点了名：和点名只干活的代理一样，让调用方看得见这件事交给了谁。
                say("handoff", first.name, "交给 %s 了" % first.name)
            # 话里点的名还留在话里，要告诉它说的就是它自己；接口里指定的不用。
            turn = self._lead(first, route.text, conversation, available, hop, say, witnesses,
                              named=named, told=route.reason == "mention")
            if named:
                say("result", first.name, "%s 做完了" % first.name)
        else:
            turn = self._direct(first, route.text, text, conversation, hop, say, witnesses)
        self._store.transcript.add(conversation, turn_id, text, turn.reply, turn.agent, witnesses)
        return turn

    # ---- 说话的代理接话，必要时转交 ----

    def _lead(self, lead: Agent, text: str, conversation: str, available: List[Agent], hop: int,
              say: Report, witnesses: List[str], named: bool = False,
              told: bool = False) -> Turn:
        helpers = [agent for agent in available if agent.name != lead.name]
        if self._config.max_handoffs == 0 or named:
            # 主人指定了由它来做：这一轮不再转给别人。
            helpers = []
        if told:
            text = named_note(lead.name, text)
        used: List[str] = []
        outcome = self._speak(lead, text, conversation, helpers, hop, catch_up=True)
        witnesses.append(lead.name)
        for step in range(self._config.max_handoffs):
            handoff = self._handoff(outcome)
            if handoff is None:
                break
            name, task = handoff
            helper = self._agents.get(name) if any(a.name == name for a in helpers) else None
            interim = self._field(outcome, "reply").strip()
            result: Optional[str] = None
            error: Optional[str] = None
            if helper is None:
                error = "没有叫 %s 的帮手（现在能找的：%s）" % (
                    clip(name, 40), "、".join(a.name for a in helpers) or "没有")
            else:
                say("handoff", helper.name, interim or "交给 %s 了" % helper.name)
                used.append(helper.name)
                try:
                    result = self._work(helper, task[:MAX_TASK_CHARS], conversation, hop).text
                    say("result", helper.name, "%s 做完了" % helper.name)
                except AgentError as failure:
                    error = str(failure)
                    say("result", helper.name, "%s 没做成" % helper.name)
            # 最后一次机会用完就不再给 handoff 这个选项，逼它给主人一个交代。
            last = step + 1 >= self._config.max_handoffs
            outcome = self._speak(
                lead, relay(helper.name if helper else clip(name, 40), result, error),
                conversation, [] if last else helpers, hop, catch_up=False,
            )
        reply = self._field(outcome, "reply") or outcome.text
        if not reply.strip():
            raise AgentError("%s 返回了空的回复" % lead.name)
        fields = outcome.fields or {}
        return shape(reply, fields.get("brief"), fields.get("mood"),
                     self._config.brief_max_chars, lead.name, used)

    @staticmethod
    def _field(outcome: Outcome, key: str) -> str:
        value = (outcome.fields or {}).get(key)
        return value if isinstance(value, str) else ""

    @staticmethod
    def _handoff(outcome: Outcome) -> Optional[List[str]]:
        handoff = (outcome.fields or {}).get("handoff")
        if not isinstance(handoff, dict):
            return None
        name, task = handoff.get("agent"), handoff.get("task")
        if not isinstance(name, str) or not isinstance(task, str) or not name or not task.strip():
            return None
        return [name, task]

    def _speak(self, agent: Agent, text: str, conversation: str, helpers: List[Agent], hop: int,
               catch_up: bool) -> Outcome:
        """让一个能说话的代理以小幽的身份接一段话。"""
        missed = self._store.transcript.unseen(conversation, agent.name) if catch_up else []
        outcome = agent.run(Job(
            text=recap(missed, text) if missed else text,
            session_id=self._store.session(conversation, agent.name),
            conversation=conversation,
            system=system_prompt(self._config.persona, self._config.brief_max_chars, helpers),
            schema=reply_schema(helpers),
            hop=hop,
        ))
        if outcome.session_id:
            self._store.remember(conversation, agent.name, outcome.session_id)
        if missed:
            self._store.transcript.mark(conversation, agent.name, [turn["id"] for turn in missed])
        return outcome

    def _work(self, agent: Agent, task: str, conversation: str, hop: int,
              with_background: bool = False) -> Outcome:
        """让一个代理只干活：不给人设，结果是原始输出。"""
        missed = self._store.transcript.unseen(conversation, agent.name) if with_background else []
        outcome = agent.run(Job(
            text=background(missed, task) if missed else task,
            session_id=self._store.session(conversation, agent.name),
            conversation=conversation,
            hop=hop,
        ))
        if outcome.session_id:
            self._store.remember(conversation, agent.name, outcome.session_id)
        if missed:
            self._store.transcript.mark(conversation, agent.name, [turn["id"] for turn in missed])
        return outcome

    # ---- 主人点名了一个只干活的代理 ----

    def _direct(self, worker: Agent, task: str, said: str, conversation: str, hop: int,
                say: Report, witnesses: List[str]) -> Turn:
        say("handoff", worker.name, "交给 %s 了" % worker.name)
        result = self._work(worker, task, conversation, hop, with_background=True).text
        say("result", worker.name, "%s 做完了" % worker.name)
        witnesses.append(worker.name)
        voice = self._agents.get(self._config.voice_agent)
        if voice is not None and voice.speaks and voice.name != worker.name and (
                hop == 0 or voice.type != "remote"):
            try:
                outcome = self._speak(voice, relay(worker.name, result, None, asked=said),
                                      conversation, [], hop, catch_up=True)
                reply = self._field(outcome, "reply") or outcome.text
                if reply.strip():
                    witnesses.append(voice.name)
                    fields = outcome.fields or {}
                    return shape(reply, fields.get("brief"), fields.get("mood"),
                                 self._config.brief_max_chars, worker.name)
            except AgentError:
                # 负责说话的代理不在：结果已经有了，原样给主人，好过让这一轮白做。
                say("note", voice.name, "%s 没能转述，原样给出结果" % voice.name)
        return shape(result, None, "idle", self._config.brief_max_chars, worker.name)

    # ---- 对话记录 ----

    def reset(self, conversation: str) -> bool:
        """让这个对话从头开始：所有代理的会话都忘掉，对话记录清空。"""
        had_turns = self._store.transcript.clear(conversation)
        return self._store.forget(conversation) or had_turns

    def share(self, conversation: str, turns: List[Dict[str, Any]]) -> int:
        return self._store.transcript.offer(conversation, turns)
