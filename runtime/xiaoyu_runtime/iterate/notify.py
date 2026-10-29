"""把一轮自动迭代的结果告诉主人：在主对话里开一张普通的事卡，内容是小鱼说的一段话。

不用外部上报（reports.py）：那是旁观别处会话用的，落在电脑会话里，主人的任务列表和聊天里都看不到。
同一轮只开一张卡（按轮次编号去重，记在 state/iterate/posted.json），重发只会更新同一张。
"""

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict

CONVERSATION = "default"
AGENT = "xiaoyou"
MAX_TEXT = 6000
RUN_ID = re.compile(r"r\d{8}-\d{6}(-\d+)?")
_lock = threading.Lock()


def plain(report: str) -> str:
    """报告是 Markdown：标题换成【】，去掉井号，手机和设备上都好读。"""
    lines = []
    for line in report.splitlines():
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            level, text = len(match.group(1)), match.group(2).strip()
            lines.append(text if level == 1 else "【%s】" % text)
        else:
            lines.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n（完整内容在电脑上的 state/iterate/runs/ 里）"


def deliver(writer: Any, state_dir: Path, payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("请求体应该是一个对象")
    run_id, title, text = payload.get("run_id"), payload.get("title"), payload.get("text")
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError("run_id 不对")
    if not isinstance(title, str) or not title.strip() or not isinstance(text, str) or not text.strip():
        raise ValueError("要有 title 和 text")
    failed = payload.get("state") == "failed"
    path = Path(state_dir) / "iterate" / "posted.json"
    with _lock:
        try:
            posted = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            posted = {}
        card_id = posted.get(run_id)
        if card_id is None:
            # agent 写小鱼自己：App 只把有人做过的事放进侧边栏的任务列表（AppPresentation.dispatched）。
            card = writer.task_open(CONVERSATION, title.strip()[:100], "failed" if failed else "done", AGENT)
            card_id = card["id"]
            posted[run_id] = card_id
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(posted, ensure_ascii=False), encoding="utf-8")
        writer.task(card_id, agent=AGENT)
        writer.reply(card_id, plain(text), turn_id="iterate-" + run_id, kind="summary",
                     brief=title.strip()[:120], mood="oops" if failed else "happy")
    return {"card": card_id}
