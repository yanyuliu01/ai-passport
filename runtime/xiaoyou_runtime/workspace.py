"""共享工作区：小幽和别的 agent（claude.ai 上的 Claude、Codex……）共用的记录。

一式两份，都在 Notion 里：

  总线  AI 之间互通，以它为准。一行一条：任务、结果、留言、交接。内容原样。
  日志  给人看的。一件事结束时写一条大白话摘要，用“关联”对到总线里的行。

小幽这边什么时候写（都是 Runtime 的代码在写，不靠模型记得写）：

  把活交给一个代理（新开、接着再做一轮）    总线：任务
  对正在做的事补充或改要求                  总线：留言
  代理交回结果、或者没做成                  总线：结果
  取消                                      总线：留言（状态是取消）；日志
  一件事结束                                日志

写 Notion 要走网络，所以都排进一条队列由一个线程慢慢写：小幽说话和后台的事不等它。
写不进去的（断网、令牌失效）重试几次后落到 state/workspace-unsent.jsonl，不悄悄丢。

只用标准库。
"""

import json
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config

NOTION_VERSION = "2022-06-28"
# Notion 的限制：一段文字最多 2000 个字符，一次最多带 100 个块。
MAX_PROPERTY_CHARS = 1900
MAX_TITLE_CHARS = 120
BLOCK_CHARS = 1900
MAX_BLOCKS = 40
MAX_QUEUED = 500
ATTEMPTS = 3
REQUEST_TIMEOUT_SECONDS = 20
CUT_NOTE = "（太长，后面的没有写进来。）"

# 卡的状态 → 总线和日志里的写法
RESULTS = {"done": "做完", "failed": "没成", "cancelled": "取消"}


class Workspace:
    """没有配置共享工作区时用的：什么都不写。"""

    enabled = False

    def task(self, card: Dict[str, Any], agent: str, text: str) -> None:
        """小幽把一件事交给了一个代理。text 是交代给它的原话。"""

    def note(self, card: Dict[str, Any], agent: str, text: str, how: str) -> None:
        """主人对一件正在做的事补充或改了要求，已经告诉了那个代理。"""

    def result(self, card: Dict[str, Any], agent: str, text: str, ok: bool) -> None:
        """代理交回了结果（原样），或者没做成（text 是原因）。"""

    def cancelled(self, card: Dict[str, Any]) -> None:
        """一件正在做的事被取消了。"""

    def closed(self, card: Dict[str, Any], state: str, brief: str, reply: str,
               mood: str) -> None:
        """一件事结束了：给人看的那一条。"""

    def close(self, timeout: float = 5.0) -> None:
        """Runtime 要停了：把还排着的写完，最多等 timeout 秒。"""


def _text(value: str, limit: int = MAX_PROPERTY_CHARS) -> List[Dict[str, Any]]:
    value = value[:limit]
    return [{"type": "text", "text": {"content": value}}] if value else []


def _blocks(body: str) -> List[Dict[str, Any]]:
    """正文原样放进代码块：命令、路径、报错不会被 Notion 当成格式改掉。"""
    blocks: List[Dict[str, Any]] = []
    for start in range(0, len(body), BLOCK_CHARS):
        if len(blocks) == MAX_BLOCKS:
            blocks.append({"object": "block", "type": "paragraph",
                           "paragraph": {"rich_text": _text(CUT_NOTE)}})
            break
        blocks.append({"object": "block", "type": "code", "code": {
            "language": "plain text", "rich_text": _text(body[start:start + BLOCK_CHARS])}})
    return blocks


def bus_page(database: str, kind: str, title: str, writer: str, to: str, status: str,
             taker: str, link: str, body: str) -> Dict[str, Any]:
    return {
        "parent": {"database_id": database},
        "properties": {
            "标题": {"title": _text(title, MAX_TITLE_CHARS)},
            "类型": {"select": {"name": kind}},
            "谁写的": {"rich_text": _text(writer)},
            "给谁": {"rich_text": _text(to)},
            "状态": {"select": {"name": status}},
            "谁接了": {"rich_text": _text(taker)},
            "关联": {"rich_text": _text(link)},
        },
        "children": _blocks(body),
    }


def log_page(database: str, title: str, who: str, result: str, conclusion: str, todo: str,
             link: str, body: str) -> Dict[str, Any]:
    return {
        "parent": {"database_id": database},
        "properties": {
            "标题": {"title": _text(title, MAX_TITLE_CHARS)},
            "谁做的": {"rich_text": _text(who)},
            "结果": {"select": {"name": result}},
            "结论": {"rich_text": _text(conclusion)},
            "要你做的": {"rich_text": _text(todo)},
            "关联": {"rich_text": _text(link)},
        },
        # 给人看的：普通段落，不用代码块。
        "children": [
            {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _text(
                body[start:start + BLOCK_CHARS])}}
            for start in range(0, min(len(body), BLOCK_CHARS * MAX_BLOCKS), BLOCK_CHARS)
        ],
    }


class NotionWorkspace(Workspace):
    enabled = True

    def __init__(self, config: Config):
        self._token = config.workspace_token or ""
        self._bus = config.workspace_bus_database or ""
        self._log = config.workspace_log_database
        self._author = config.workspace_author
        self._runtime = config.name
        self._url = config.workspace_api_base.rstrip("/") + "/v1/pages"
        self._unsent = config.state_dir / "workspace-unsent.jsonl"
        self._queue: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue(MAX_QUEUED)
        # 测试里把它调小；正式运行时每次重试前等这么多秒（乘以第几次）。
        self.retry_seconds = 2.0
        self._thread = threading.Thread(target=self._drain, daemon=True, name="xiaoyou-workspace")
        self._thread.start()

    def _link(self, card: Dict[str, Any]) -> str:
        # 卡的编号只在一台 Runtime 里不重复，所以带上 Runtime 的名字。
        return "%s/%s" % (self._runtime, card["id"])

    def _put(self, page: Dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(page)
        except queue.Full:
            self._keep(page, "排着没写的太多了")

    def task(self, card: Dict[str, Any], agent: str, text: str) -> None:
        # 小幽是直接交到那个代理手里的，所以一写下就是“已接”。
        self._put(bus_page(self._bus, "任务", card["title"], self._author, agent, "已接",
                           agent, self._link(card), text))

    def note(self, card: Dict[str, Any], agent: str, text: str, how: str) -> None:
        how = {"steer": "中途追加", "redo": "停下按新的要求重做",
               "after": "这一轮做完接着做"}.get(how, how)
        self._put(bus_page(self._bus, "留言", card["title"], self._author, agent, "已接",
                           agent, self._link(card), "（%s）\n%s" % (how, text)))

    def result(self, card: Dict[str, Any], agent: str, text: str, ok: bool) -> None:
        self._put(bus_page(self._bus, "结果", card["title"], agent, self._author,
                           "做完" if ok else "没成", agent, self._link(card), text))

    def cancelled(self, card: Dict[str, Any]) -> None:
        agent = card.get("agent") or ""
        self._put(bus_page(self._bus, "留言", card["title"], self._author, agent, "取消",
                           agent, self._link(card), "主人取消了这件事。"))
        self.closed(card, "cancelled", "取消了", "", "idle")

    def closed(self, card: Dict[str, Any], state: str, brief: str, reply: str,
               mood: str) -> None:
        if self._log is None:
            return
        self._put(log_page(self._log, card["title"], card.get("agent") or self._author,
                           RESULTS.get(state, state), brief,
                           # 小幽说“要主人拿主意”的，把那句话放进“要你做的”。
                           brief if mood == "ask" else "", self._link(card), reply))

    def close(self, timeout: float = 5.0) -> None:
        self._queue.put(None)
        self._thread.join(timeout)

    def _drain(self) -> None:
        while True:
            page = self._queue.get()
            if page is None:
                return
            try:
                self._send(page)
            except Exception as error:  # 这条线不能死：记下来，接着写下一条
                self._keep(page, "%s: %s" % (type(error).__name__, error))

    def _send(self, page: Dict[str, Any]) -> None:
        data = json.dumps(page, ensure_ascii=False).encode("utf-8")
        problem = ""
        for attempt in range(1, ATTEMPTS + 1):
            request = urllib.request.Request(self._url, data=data, method="POST", headers={
                "Authorization": "Bearer " + self._token,
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            })
            try:
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS):
                    return
            except urllib.error.HTTPError as error:
                detail = error.read(500).decode("utf-8", "replace")
                problem = "Notion 返回 %d：%s" % (error.code, " ".join(detail.split()))
                if error.code != 429 and error.code < 500:
                    break  # 令牌不对、数据库没共享给集成、字段对不上：重试没有用
            except (urllib.error.URLError, OSError) as error:
                problem = "连不上 Notion：%s" % error
            if attempt < ATTEMPTS:
                time.sleep(self.retry_seconds * attempt)
        self._keep(page, problem)

    def _keep(self, page: Dict[str, Any], problem: str) -> None:
        """没写进去的落盘，并在标准错误里说一声。"""
        sys.stderr.write("共享工作区没写进去：%s\n" % problem)
        sys.stderr.flush()
        try:
            self._unsent.parent.mkdir(parents=True, exist_ok=True)
            with self._unsent.open("a", encoding="utf-8") as file:
                file.write(json.dumps({"at": time.time(), "problem": problem, "page": page},
                                      ensure_ascii=False) + "\n")
        except OSError:
            pass


def create(config: Config) -> Workspace:
    return NotionWorkspace(config) if config.workspace_type == "notion" else Workspace()
