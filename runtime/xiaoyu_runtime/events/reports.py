"""外部上报：不在这台电脑上的会话主动把进展报进来（见 docs/development/unified-message-model 的“来源接入”）。

claude.ai 网页版、云端 Claude Code、别人的电脑上的会话，这台电脑上没有它们的转录文件，
观察器读不到。它们能在这台电脑上跑命令时，就在开始、阶段完成、结束时报一次：

  python3 -m xiaoyu_runtime report --origin claude-web --session <会话> --state working \
      --text "在改同步方案" --upstream-id <这句的编号>

或者直接 POST /v1/reports。每个 (origin, session) 是一张只读的卡，和电脑上观察到的会话放在
一起；报来的话按 upstream_id 去重，同一句重复报只更新不新增。上报的会话不能从手机接着聊：
它不在这台电脑上，Runtime 没法替它跑。超过一段时间没再报的“在做”显示为状态未知。
"""

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from .message_log import DESKTOP_CONVERSATION
from .intake import Intake

ORIGIN = re.compile(r"[a-z][a-z0-9-]{0,31}")
SESSION = re.compile(r"[A-Za-z0-9._:-]{1,128}")
# 上报的状态 -> (卡的状态, desktop_status)。turn_ended 在 /v1/sync 里如实给出（见 cards.task_view）。
STATES = {"working": ("working", "running"), "waiting": ("waiting", "waiting"),
          "turn_ended": ("done", "idle"), "done": ("done", "done"), "failed": ("failed", "failed")}
MAX_TEXT_CHARS = 8000
MAX_TITLE_CHARS = 120
MAX_ENTRIES = 40
# 这么久没再报的“在做 / 等确认”显示为状态未知，不假装还在做。
STALE_SECONDS = 600


class ReportError(Exception):
    pass


def _agent(origin: str) -> str:
    """手机上挂在哪位帮手下面：claude-* 归 Claude，codex-* 归 Codex，其他用来源名自己。"""
    for name in ("claude", "codex"):
        if origin == name or origin.startswith(name + "-"):
            return name
    return origin


class Reports:
    def __init__(self, cards: Any, clock: Any = time.time):
        self._cards, self._clock = cards, clock
        self._writer = Intake.for_cards(cards).writer("desktop", actor="external-report")
        self._lock = threading.Lock()
        self._index: Dict[Tuple[str, str], str] = {}
        for card in cards.recent(None, 1000):
            if card.get("source") == "external" and card.get("origin") and card.get("session_id"):
                self._index[(card["origin"], card["session_id"])] = card["id"]

    def report(self, payload: Any) -> Dict[str, Any]:
        """记下一次上报，返回那张卡的编号和状态。"""
        if not isinstance(payload, dict):
            raise ReportError("请求体应该是一个对象")
        origin, session = payload.get("origin"), payload.get("session_id")
        state = payload.get("state", "working")
        text, upstream = payload.get("text"), payload.get("upstream_id")
        title, role = payload.get("title"), payload.get("role", "agent")
        if not isinstance(origin, str) or not ORIGIN.fullmatch(origin):
            raise ReportError("origin 要用小写字母开头，只含小写字母、数字和 -（比如 claude-web）")
        if not isinstance(session, str) or not SESSION.fullmatch(session):
            raise ReportError("session_id 只能用字母、数字和 . _ : -，最长 128 个字符")
        if state not in STATES:
            raise ReportError("state 只能是 %s 之一" % "、".join(STATES))
        if text is not None and (not isinstance(text, str) or not text.strip()):
            raise ReportError("text 不能是空的")
        if text is not None and (not isinstance(upstream, str) or not 0 < len(upstream) <= 200):
            raise ReportError("带 text 时要给 upstream_id（这句话在上报方那里的编号），重复上报靠它去重")
        if title is not None and (not isinstance(title, str) or not title.strip()):
            raise ReportError("title 不能是空的")
        if role not in ("agent", "owner"):
            raise ReportError("role 只能是 agent 或 owner")
        now = self._clock()
        card_state, status = STATES[state]
        with self._lock:
            card_id = self._index.get((origin, session))
            card = self._cards.get(card_id) if card_id else None
            if card is None:
                name = (title or "%s %s" % (origin, session[-8:])).strip()[:MAX_TITLE_CHARS]
                card = self._writer.observed_open(DESKTOP_CONVERSATION, name, card_state, _agent(origin),
                                        title_source="external")
                card_id = card["id"]
                self._index[(origin, session)] = card_id
            entries = [dict(entry) for entry in card.get("entries", [])]
            if text is not None:
                key = "ext-" + hashlib.sha256(
                    ("%s\0%s\0%s" % (origin, session, upstream)).encode("utf-8")).hexdigest()[:40]
                entry = {"id": key, "role": "you" if role == "owner" else "xiaoyou",
                         "text": text.strip()[:MAX_TEXT_CHARS], "at": now, "scope": "main",
                         "kind": "user" if role == "owner" else "reply",
                         "author": "you" if role == "owner" else origin}
                for index, previous in enumerate(entries):
                    if previous.get("id") == key:
                        entry["at"] = previous.get("at", now)
                        entries[index] = entry
                        break
                else:
                    entries.append(entry)
                del entries[:-MAX_ENTRIES]
            fields: Dict[str, Any] = dict(
                state=card_state, source="external", origin=origin, session_id=session,
                monitoring_only=True, desktop_status=status, observed_at=now, stale=False,
                project=origin, entries=entries, queued=False,
                mood="busy" if card_state in ("working", "waiting") else "idle")
            if title is not None:
                fields.update(title=title.strip()[:MAX_TITLE_CHARS], title_source="external")
            latest = next((item["text"] for item in reversed(entries) if item["role"] == "xiaoyou"), "")
            fields["brief"] = (latest or "%s · %s" % (origin, state))[:120]
            self._writer.observed(card_id, **fields)
        # 回给上报方的是它报的状态（turn_ended 在卡上记成 done，只在 /v1/sync 里如实给出）。
        return {"card": card_id, "state": state}

    def sweep(self) -> None:
        """太久没再报的“在做 / 等确认”标成状态未知。"""
        now = self._clock()
        with self._lock:
            ids = list(self._index.values())
        for card_id in ids:
            card = self._cards.get(card_id)
            if (card and card.get("state") in ("working", "waiting") and not card.get("stale")
                    and now - (card.get("observed_at") or 0) > STALE_SECONDS):
                self._writer.observed(card_id, stale=True)


# ---- 命令行：在报告方那台机器上跑，发给这台 Runtime ----

def add_arguments(subparsers: Any) -> None:
    parser = subparsers.add_parser("report", help="把一个不在这台电脑上的会话的进展报给 Runtime（只读显示在手机上）")
    parser.add_argument("--origin", required=True, help="来源名，比如 claude-web")
    parser.add_argument("--session", required=True, help="那边的会话编号")
    parser.add_argument("--state", default="working", choices=sorted(STATES))
    parser.add_argument("--text", help="这次要显示的一句话")
    parser.add_argument("--upstream-id", help="这句话在那边的编号；同一句重复报只更新")
    parser.add_argument("--title", help="会话标题")
    parser.add_argument("--role", default="agent", choices=["agent", "owner"],
                        help="这句话是谁说的：agent（默认）或 owner（主人）")
    parser.add_argument("--url", help="Runtime 地址；不写就用这份配置里的本机地址")
    parser.add_argument("--token", help="令牌；不写就用这份配置里的")


def run(config: Any, args: Any, opener: Any = urllib.request.urlopen) -> int:
    url = (args.url or "http://127.0.0.1:%d" % config.port).rstrip("/")
    payload: Dict[str, Any] = {"origin": args.origin, "session_id": args.session, "state": args.state,
                               "role": args.role}
    for key, value in (("text", args.text), ("upstream_id", args.upstream_id), ("title", args.title)):
        if value is not None:
            payload[key] = value
    request = urllib.request.Request(url + "/v1/reports", data=json.dumps(payload).encode("utf-8"),
                                     method="POST", headers={
                                         "Authorization": "Bearer " + (args.token or config.token),
                                         "Content-Type": "application/json; charset=utf-8"})
    try:
        with opener(request, timeout=15) as response:
            done: Optional[Dict[str, Any]] = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        print("没报上：%s %s" % (error.code, error.read().decode("utf-8", "replace")[:200]))
        return 1
    except (urllib.error.URLError, OSError, ValueError) as error:
        print("没报上：%s" % error)
        return 1
    print("报上了：卡 %s，状态 %s" % ((done or {}).get("card"), (done or {}).get("state")))
    return 0
