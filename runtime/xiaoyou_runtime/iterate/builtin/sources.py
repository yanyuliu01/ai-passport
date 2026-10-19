"""内置的 source 插件：把已有的记录变成信号。都只读，不改卡和消息。

  app_events      手机报来的使用事件（POST /v1/app/events）
  chat_feedback   主人在对话里的抱怨、纠正；有模型时让模型分类，没有时按关键词粗分
  card_failures   失败的事
  reply_latency   每句话从收到到出第一个字用了多久（Runtime 这一侧量的）
"""

import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..objects import Signal
from ..store import AppEvents, read_json

MAX_TEXT = 200
# 像是在抱怨或者纠正：先用这个粗筛，筛出来的才可能送去给模型分类（省钱，也少送原话）。
COMPLAINT = re.compile(
    r"不对|不是这|又|还是没|怎么(还|又|没|不)|没反应|点不|点了没|卡住|卡死|太慢|好慢|慢死|看不到|看不见|"
    r"没显示|不显示|显示不|英文|乱码|闪退|崩了|崩溃|找不到|不行|报错|出错|失败|bug|有问题|难用|麻烦|"
    r"太多了|不方便|反复|重复|偏了|歪了|错位|挡住|跳回|白屏|黑屏", re.I)
APP_WORDS = re.compile(
    r"app|APP|App|手机|界面|页面|首页|按钮|点|显示|弹窗|通知|侧边栏|输入框|头像|图标|字|滑|屏幕|更新|安装", re.I)
CATEGORIES = ("app_bug", "app_ui", "app_slow", "app_confusing", "agent_behavior", "not_feedback")
CATEGORY_HINTS = (
    ("app_slow", re.compile(r"慢|卡住|卡死|半天|等了|转圈")),
    ("app_bug", re.compile(r"闪退|崩|报错|出错|失败|白屏|黑屏|没反应|点不|点了没|bug", re.I)),
    ("app_ui", re.compile(r"偏|歪|错位|挡住|看不到|看不见|显示|英文|乱码|字|头像|图标")),
    ("app_confusing", re.compile(r"找不到|麻烦|太多了|不方便|难用|反复|重复")),
)


def _first_cursor(ctx: Any, cursor: Optional[Dict[str, Any]]) -> float:
    """第一次跑时不回溯全部历史，只看最近 window_days 天。"""
    if cursor and isinstance(cursor.get("at"), (int, float)):
        return float(cursor["at"])
    return ctx.now - ctx.settings.window_days * 86400


# ---- app_events ----

def app_events(ctx: Any, cursor: Optional[Dict[str, Any]]) -> Tuple[List[Signal], Dict[str, Any]]:
    events, new_cursor = AppEvents(ctx.state_dir).read_after(cursor)
    signals = []
    for event in events:
        data = {key: value for key, value in event.items() if key not in ("id", "type", "at")}
        signals.append(Signal(id="app:" + event["id"], type="app." + event["type"], at=event["at"],
                              source="app_events", data=data))
    return signals, new_cursor


# ---- chat_feedback ----

def heuristic_category(text: str) -> str:
    if not APP_WORDS.search(text):
        return "agent_behavior"
    for category, pattern in CATEGORY_HINTS:
        if pattern.search(text):
            return category
    return "app_confusing"


CLASSIFY_PROMPT = """你在帮一个手机 App（Pocket Hub，主人用它和 AI 助手"小幽"聊天、看任务、确认授权、管理随身设备）找产品问题。
下面是主人说的一句话，以及它前面小幽说的一句。判断主人这句话属于哪一类：

- app_bug：App 出错、闪退、点了没反应、功能坏了
- app_ui：App 界面显示问题：位置不对、被挡住、文字不对（比如出英文）、看不清
- app_slow：App 里等得太久、卡
- app_confusing：App 用起来麻烦、步骤太多、找不到入口
- agent_behavior：不是 App 的问题，是对 AI 助手的回答、做事方式不满意
- not_feedback：不是抱怨或纠正

只输出 JSON：{"category": "<上面的一类>", "reason": "<不超过20字>"}"""


def _classify(ctx: Any, text: str, before: str) -> Tuple[str, float, str]:
    gateway = ctx.gateway
    votes = Counter()

    def check(value: Any) -> Optional[str]:
        if not isinstance(value, dict) or value.get("category") not in CATEGORIES:
            return "category 必须是 %s 之一" % "、".join(CATEGORIES)
        return None

    for _ in range(ctx.settings.votes):
        value, _completion = gateway.complete_json("iterate.classify", [
            {"role": "system", "content": CLASSIFY_PROMPT},
            {"role": "user", "content": "小幽：%s\n主人：%s" % (before[:MAX_TEXT], text[:MAX_TEXT])},
        ], check, attempts=2, max_tokens=120)
        votes[value["category"]] += 1
    category, count = votes.most_common(1)[0]
    return category, count / float(ctx.settings.votes), "model"


def _cards(ctx: Any) -> List[Dict[str, Any]]:
    data = read_json(Path(ctx.state_dir) / "cards.json", {})
    cards = data.get("cards", []) if isinstance(data, dict) else []
    if isinstance(cards, dict):
        cards = list(cards.values())
    return [card for card in cards if isinstance(card, dict)]


def chat_feedback(ctx: Any, cursor: Optional[Dict[str, Any]]) -> Tuple[List[Signal], Dict[str, Any]]:
    after = _first_cursor(ctx, cursor)
    newest = after
    budget = ctx.settings.max_classify
    use_model = ctx.model_available("iterate.classify")
    signals = []
    for card in _cards(ctx):
        if card.get("monitoring_only"):
            continue  # 电脑上旁观的会话不是在和小幽说话
        entries = card.get("entries") or []
        for index, entry in enumerate(entries):
            at = entry.get("at")
            if entry.get("role") != "you" or not isinstance(at, (int, float)) or at <= after:
                continue
            newest = max(newest, float(at))
            text = str(entry.get("text") or "").strip()
            if not text or not COMPLAINT.search(text):
                continue
            before = ""
            for earlier in reversed(entries[:index]):
                if earlier.get("role") != "you":
                    before = str(earlier.get("text") or "")
                    break
            category, confidence, method = heuristic_category(text), 0.4, "keywords"
            if use_model and budget > 0:
                budget -= 1
                try:
                    category, confidence, method = _classify(ctx, text, before)
                except Exception as error:  # 模型不可用就退回关键词，不让整轮失败
                    ctx.log("分类失败，改用关键词：%s" % error)
            if category == "not_feedback":
                continue
            signals.append(Signal(
                id="chat:%s" % (entry.get("id") or "%s:%d" % (card.get("id"), index)),
                type="chat.feedback", at=float(at), source="chat_feedback",
                data={"text": text[:MAX_TEXT], "card": card.get("id"), "category": category,
                      "confidence": round(confidence, 2), "method": method}))
    return signals, {"at": newest}


# ---- card_failures ----

def card_failures(ctx: Any, cursor: Optional[Dict[str, Any]]) -> Tuple[List[Signal], Dict[str, Any]]:
    after = _first_cursor(ctx, cursor)
    newest = after
    signals = []
    for card in _cards(ctx):
        finished = card.get("finished_at")
        if card.get("state") != "failed" or not isinstance(finished, (int, float)) or finished <= after:
            continue
        newest = max(newest, float(finished))
        signals.append(Signal(
            id="card:%s:%s" % (card.get("id"), card.get("run_id") or int(finished)),
            type="task.failed", at=float(finished), source="card_failures",
            data={"card": card.get("id"), "agent": card.get("agent") or "",
                  "model": card.get("model") or "", "brief": str(card.get("brief") or "")[:MAX_TEXT],
                  "monitoring_only": bool(card.get("monitoring_only"))}))
    return signals, {"at": newest}


# ---- reply_latency ----

def reply_latency(ctx: Any, cursor: Optional[Dict[str, Any]]) -> Tuple[List[Signal], Dict[str, Any]]:
    after = _first_cursor(ctx, cursor)
    newest = after
    data = read_json(Path(ctx.state_dir) / "messages.json", {})
    messages = data.get("messages", []) if isinstance(data, dict) else []
    signals = []
    for message in messages if isinstance(messages, list) else []:
        created, first = message.get("created_at"), message.get("first_partial_at")
        if not isinstance(created, (int, float)) or created <= after:
            continue
        if not isinstance(first, (int, float)):
            if message.get("status") in ("done", "failed", "cancelled"):
                newest = max(newest, float(created))
            continue  # 还在等第一个字：下一轮再看
        newest = max(newest, float(created))
        signals.append(Signal(
            id="reply:%s" % message.get("id"), type="chat.reply_latency", at=float(created),
            source="reply_latency",
            data={"ms": int((first - created) * 1000), "agent": message.get("agent") or "",
                  "card": message.get("card") or "", "kind": message.get("kind") or ""}))
    return signals, {"at": newest}


PLUGINS = [
    {"name": "app_events", "kind": "source", "permissions": ["read_state"], "run": app_events,
     "description": "手机报来的使用事件"},
    {"name": "chat_feedback", "kind": "source", "permissions": ["read_state", "model"],
     "run": chat_feedback, "description": "对话里的抱怨和纠正；有模型时分类"},
    {"name": "card_failures", "kind": "source", "permissions": ["read_state"], "run": card_failures,
     "description": "失败的事"},
    {"name": "reply_latency", "kind": "source", "permissions": ["read_state"], "run": reply_latency,
     "description": "收到一句话到出第一个字的耗时"},
]
