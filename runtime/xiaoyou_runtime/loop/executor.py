"""Durable task selections shared by conversation execution and recovery.

Task actions use explicit ports for admission, helper dispatch, remote control
and presentation. Normal and recovered actions share this boundary and the
existing request journal; no model is called to replay a saved selection.
"""

from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Optional

from ..errors import AgentError, EffectUncertain
from ..helpers.base import clip
from ..world.cards import ACTIVE
from ..helpers.tasks import fingerprint

_TURN_FIELDS = ("text", "conversation", "turn_id", "reply", "brief", "mood", "hop",
                "pinned", "effort", "model", "defer_reply")


QUEUE_FULL_NOTE = "这件事排着的补充已经有好几条了，这句没记上，等它做完这一轮再说吧"


@dataclass(frozen=True)
class ActionPorts:
    """Narrow adapters; late-bound callbacks retain dynamic helpers and Mesh."""
    writer: Any
    brief_limit: Callable[[], int]
    shape: Callable[..., Any]
    turn: Callable[..., Any]
    launch: Callable[..., Any]
    amend: Callable[..., Any]
    cancel: Callable[..., Any]
    continue_desktop: Callable[..., Any]
    remote: Callable[[], Any]
    note: Callable[..., Any]
    delivered: Callable[..., Any]


class Executor:
    def __init__(self, cards, agents, ports: ActionPorts):
        self._cards, self._agents, self._ports = cards, agents, ports

    def apply(self, scope, lead, plan, say, *, interrupted=False, **turn):
        selection = dict(plan)
        target = selection.pop("card", None)
        selection["card"] = target["id"] if target else None
        names = {lead.name, plan.get("agent") or (target or {}).get("agent")}
        args = dict(turn, lead=lead.name, plan=selection,
                    target_seq=target.get("seq") if target else None,
                    agents={name: fingerprint(self._agents[name]) for name in names if name in self._agents})
        # Interruption controls display only, not the saved selection/result.
        return scope.run("task_action", args,
                         lambda: self._invoke(args, lead, plan, say, interrupted),
                         key=turn["turn_id"] + ":action")

    def resume(self, scope, record, message, say):
        """Reuse the original identity and saved result, never reselect work."""
        args = record["args"]

        def invoke():
            if args["conversation"] != message["conversation"] or args["turn_id"] != message["id"]:
                raise EffectUncertain("原操作不属于这轮请求。")
            for name, expected in args["agents"].items():
                if name not in self._agents or fingerprint(self._agents[name]) != expected:
                    raise EffectUncertain("原帮手缺失或配置已改变，需先核对决定。")
            plan = dict(args["plan"])
            target = self._cards.get(plan["card"]) if plan.get("card") else None
            if plan.get("card") and (target is None or target["seq"] != args.get("target_seq")):
                raise EffectUncertain("原决定对应的任务已经改变，不能沿用旧决定。")
            plan["card"] = target
            lead = self._agents.get(args["lead"])
            if lead is None:
                raise EffectUncertain("原说话代理不可用。")
            return self._invoke(args, lead, plan, say)

        return scope.run(record["name"], args, invoke, key=record["id"])

    def _invoke(self, args, lead, plan, say, interrupted=False):
        # Older adapters ignored additional saved metadata; keep that contract.
        turn = {key: args[key] for key in _TURN_FIELDS}
        result = self.execute(lead=lead, plan=plan, say=say, interrupted=interrupted, **turn)
        target = plan.get("card") or {}
        if target.get("mesh_origin"):
            facts = "已保存发往原负责电脑的操作请求，等待那边确认，尚不能当作远端执行完成。"
        elif plan["type"] == "cancel":
            facts = "已保存任务 %s 的取消请求。" % result.card
        else:
            current = self._cards.get(result.card) or {}
            facts = "已保存任务 %s 的交办；当前记录状态为 %s，执行结果会回到原对话。" % (
                result.card, current.get("state", "unknown"))
        return dict(asdict(result), _facts=facts)

    def execute(self, lead, text, conversation, turn_id, plan, reply, brief, mood,
                hop, say, pinned, effort, model, defer_reply=False, seen=None, interrupted=False):
        """Apply the selected action through the existing bounded task ports."""
        def exchange(*args: Any, **kwargs: Any) -> Any:
            if defer_reply:
                kwargs["say"] = None
            elif interrupted:
                kwargs["interrupted"] = True  # 主人插话，这句只说了一半
            return self._ports.writer.exchange(*args, **kwargs)

        kind = plan["type"]
        target: Optional[Dict[str, Any]] = plan["card"]
        if target is not None and target.get("mesh_origin"):
            mesh = self._ports.remote()
            # 别的 Runtime 的事（包括那台电脑上的会话）：这里只有副本。补充、接着做、取消交给那台；
            # 这句话和回答记在这里另开的一张卡上，不写进副本（副本的内容只由那台决定）。
            if kind in ("amend", "desktop") and mesh is not None:
                mesh.amend(target, plan["task"], continue_desktop=bool(target.get("monitoring_only")))
            elif kind == "cancel" and mesh is not None:
                mesh.cancel(target["id"])
            if kind in ("amend", "desktop", "cancel"):
                kind = "none"
            target = None
        if kind == "desktop":
            # 她判断要电脑上的那个会话接着干活：照主人点名时一样，另开一件接着那个会话做。
            try:
                return self._ports.continue_desktop(target, text, conversation, turn_id, hop, say,
                                              plan.get("effort") or effort,
                                              plan.get("model") or model, plan["task"], reply, defer_reply=defer_reply)
            except AgentError as error:
                kind, reply, mood, brief = "none", "电脑上的会话这次接不了：%s" % error, "oops", None
        if target is not None and target.get("monitoring_only"):
            # 会话不归 Runtime：她的回答另记一件，不写进它。_plan 只给电脑会话 desktop（上面已经
            # 处理了）或 none，所以这里不会留下一个没有目标的补充。
            target = None
        if not reply and not defer_reply:
            if kind == "none":
                raise AgentError("%s 返回了空的回复" % lead.name)
            reply = {"start": "交给 %s 了", "amend": "好，告诉 %s 了",
                     "cancel": "好，这件事不做了%s"}[kind] % (
                         plan.get("agent") or (target or {}).get("agent") or "")
        started = False
        worker = lead.name
        if kind == "none":
            if target is None:
                target = self._ports.writer.task_open(conversation, text)
            quiet = target["state"] in ACTIVE
            turn = self._ports.shape(reply, brief, mood, self._ports.brief_limit(), lead.name, target["id"])
            # 那件事还在做时，卡上的简报和表情留给它的结果，不被一句闲聊盖掉。
            extra = {} if quiet else {"brief": turn.brief, "mood": turn.mood}
            exchange(target["id"], said=text, say=reply, turn_id=turn_id, scope="task" if pinned else "main", **extra)
        elif kind == "cancel":
            self._ports.cancel(target["id"], actor="xiaoyou", reason=text, announce=False)
            exchange(target["id"], said=text, say=reply, turn_id=turn_id, scope="task" if pinned else "main")
            turn = self._ports.shape(reply, brief, mood, self._ports.brief_limit(), lead.name, target["id"])
        elif kind == "amend":
            worker = target["agent"] or lead.name
            done = self._ports.amend(target, self._agents[worker], plan["mode"], plan["task"],
                               plan.get("effort") or effort, plan.get("model") or model)
            if done == "full":
                reply = QUEUE_FULL_NOTE
                exchange(target["id"], said=text, say=reply, turn_id=turn_id, scope="task" if pinned else "main")
            elif done is None:
                # 那件事已经不在做了：接着它原来的会话再做一轮。
                exchange(target["id"], said=text, say=reply, turn_id=turn_id, scope="task" if pinned else "main", brief=clip(
                    reply, self._ports.brief_limit()), mood="busy", state="working",
                    edits=target["edits"] + 1)
                self._ports.launch(target["id"], self._agents[worker], plan["task"], conversation, hop,
                             plan.get("effort") or effort, plan.get("model") or model)
            else:
                exchange(target["id"], said=text, say=reply, turn_id=turn_id, scope="task" if pinned else "main", edits=target["edits"] + 1)
                self._ports.note(target, worker, plan["task"], done)
            say("handoff", worker, reply)
            started = True
            turn = self._ports.shape(reply, brief, "busy", self._ports.brief_limit(), worker,
                         target["id"], True)
        else:
            helper = self._agents[plan["agent"]]
            worker = helper.name
            if target is None:
                target = self._ports.writer.task_open(
                    conversation, plan.get("title") or text, "working", helper.name,
                    title_source="task" if plan.get("title") else "request")
            exchange(target["id"], said=text, say=reply, turn_id=turn_id, scope="task" if pinned else "main", state="working",
                               agent=helper.name, mood="busy",
                               brief=clip(reply, self._ports.brief_limit()))
            say("handoff", helper.name, reply)
            self._ports.launch(target["id"], helper, plan["task"], conversation, hop,
                         plan.get("effort") or effort, plan.get("model") or model)
            started = True
            turn = self._ports.shape(reply, brief, "busy", self._ports.brief_limit(), helper.name,
                         target["id"], True)
        if not defer_reply:
            self._ports.delivered(conversation, turn_id, [lead.name] if seen is None else seen, worker)
        return turn

    def assign(self, agent, title, said, conversation, turn_id, hop, say, prepare,
               effort=None, model=None):
        reply = "交给 %s 了" % agent.name
        card = self._ports.writer.task_open(conversation, title, "working", agent.name)
        self._ports.writer.exchange(card["id"], said=said, say=reply, turn_id=turn_id, kind="handoff", brief=reply, mood="busy")
        task = prepare()
        say("handoff", agent.name, reply)
        self._ports.launch(card["id"], agent, task, conversation, hop, effort, model)
        return self._ports.turn(reply, reply, "busy", agent.name, card["id"], True)

    def resume_named(self, card, agent, task, said, conversation, turn_id, hop, say,
                     prepare_if_idle, effort=None, model=None):
        reply = "好，告诉 %s 了" % agent.name
        done = self._ports.amend(card, agent, "after", task, effort, model)
        if done == "after":
            reply = "补充已记下，等 %s 这一轮结束后转交给同一个会话" % agent.name
        if done == "full":
            reply = QUEUE_FULL_NOTE
        if done is None:
            # 这件事现在没人在做：让被点名的代理接着做一轮。它没做过这件事时带上背景。
            task = prepare_if_idle(task)
            self._ports.writer.exchange(card["id"], said=said, say=reply, turn_id=turn_id, scope="task", kind="handoff", brief=reply, mood="busy",
                               state="working", edits=card["edits"] + 1)
            self._ports.launch(card["id"], agent, task, conversation, hop, effort, model)
        elif done == "full":
            self._ports.writer.exchange(card["id"], said=said, say=reply, turn_id=turn_id, scope="task", kind="handoff")
        else:
            self._ports.writer.exchange(card["id"], said=said, say=reply, turn_id=turn_id, scope="task", kind="handoff", edits=card["edits"] + 1)
            self._ports.note(card, agent.name, task, done)
        say("handoff", agent.name, reply)
        return self._ports.turn(reply, reply, "busy", agent.name, card["id"], True)
