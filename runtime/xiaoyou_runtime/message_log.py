"""消息日志：一台 Runtime 上所有对话里每一句话的权威记录（见 docs/development/unified-message-model）。

一条消息是一句话：主人说的、小幽答的、帮手的结果、转述、提示（比如“Runtime 重启了”），
以及电脑上观察到的会话里的话。每条有一个在这台 Runtime 里唯一的编号；客户端用
“runtime_id + / + 编号”当全局编号。卡（事）只是把消息按事分组的视图；卡上的 entries 是
这些消息的副本。

存储：state/<卡文件名>.log.jsonl，一行一条消息的最新样子，追加写；读的时候同一个编号以
最后一行为准。行数涨到保留条数的几倍时整份重写（先写临时文件、刷盘、换名）。还在生成的
那段回复（partial）变得太快，不写盘。

保留：最多 MAX_MESSAGES 条，挤掉最早的、而且已经不在任何卡上的。挤掉不是删除：不发墓碑，
客户端自己缓存的旧消息照留；只是“全量快照”里不再有它们。被挤掉的最大序号记成 floor，
客户端游标比它还旧时要重新拉一次快照。

这个类不带锁：由 Cards 在它自己的锁里调用，序号也由 Cards 发。
"""

import json
import os
import sys
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

MAX_MESSAGES = 5000
MAX_ACTIVITIES = 2000
# 文件行数超过保留条数的这么多倍时整份重写。
COMPACT_FACTOR = 3
STATUSES = ("queued", "streaming", "final", "failed", "cancelled")
DESKTOP_CONVERSATION = "__desktop__"
# 卡条目上的旧叫法 → 消息的 kind（卡条目为了旧 App 保持原样）
KINDS = {"worker_result": "result"}

# 记录里由卡决定、请求那边不覆盖的字段
CARD_FIELDS = ("conversation", "task", "scope", "author", "kind", "reply_to", "source",
               "attachments", "created_at")


def entry_message(card: Dict[str, Any], entry: Dict[str, Any]) -> Dict[str, Any]:
    """卡上的一个条目对应的消息（不含 rev、seq、status）。"""
    desktop = card.get("monitoring_only") is True
    you = entry["role"] == "you"
    if desktop and card.get("source") == "external":
        # 别处上报来的会话（reports.py）：只读，标明来源。
        origin = card.get("origin") or "external"
        author = {"kind": "owner", "name": "you"} if you else {"kind": "external", "name": origin}
        source = {"type": "external", "origin": origin, "session_id": card.get("session_id") or ""}
    elif desktop:
        author = {"kind": "owner", "name": "you"} if you else {
            "kind": "desktop", "name": card.get("agent") or "desktop"}
        source = {"type": "desktop", "session_id": card.get("session_id") or ""}
    else:
        name = entry.get("author") or ("you" if you else "xiaoyou")
        if you:
            author = {"kind": "owner", "name": "you"}
        elif name == "xiaoyou":
            author = {"kind": "xiaoyou", "name": "xiaoyou"}
        else:
            author = {"kind": "agent", "name": name}
        source = {"type": "runtime"}
    message_id = entry["id"]
    reply_to = None
    if not you and message_id.endswith(":reply"):
        reply_to = message_id[:-len(":reply")] + ":you"
    return {
        "id": message_id,
        "conversation": card["conversation"],
        "task": card["id"],
        "scope": entry.get("scope") or "main",
        "author": author,
        "kind": KINDS.get(entry.get("kind") or "", entry.get("kind") or ("user" if you else "reply")),
        "reply_to": reply_to,
        "text": entry["text"],
        "source": source,
        "attachments": [dict(item) for item in entry.get("attachments", [])],
        "created_at": entry.get("at") or card.get("created_at") or 0,
    }


class MessageLog:
    def __init__(self, path: Path, bump: Callable[[], int]):
        self._path = Path(path)
        self._bump = bump
        self._messages: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._lines = 0
        self.floor = 0
        # 这台 Runtime 的编号：本机产生的记录都写上它（产地），日志搬家或和别处合并时不会认错。
        self.origin = ""
        self._load()

    def set_origin(self, runtime_id: str) -> None:
        self.origin = runtime_id
        for record in self._messages.values():
            self._stamp(record)

    def _stamp(self, record: Dict[str, Any]) -> None:
        source = record.get("source")
        if self.origin and isinstance(source, dict) and source.get("type") == "runtime" \
                and not source.get("runtime_id"):
            source["runtime_id"] = self.origin

    # ---- 文件 ----

    def _load(self) -> None:
        self._repair_tail()
        try:
            handle = self._path.open("r", encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as error:
            sys.stderr.write("读不了消息日志 %s：%s\n" % (self._path, error))
            return
        with handle:
            for line in handle:
                self._lines += 1
                try:
                    item = json.loads(line)
                except ValueError:
                    continue  # 断电时写了半行：跳过这一行，前后的都还在
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    continue
                if item.get("floor") is not None and isinstance(item.get("floor"), int):
                    self.floor = max(self.floor, item["floor"])
                    continue
                item["partial"] = ""
                if item.get("status") == "streaming":
                    item["status"] = "queued"  # 生成到一半没写完的，重启后由 Service 定夺
                self._messages.pop(item["id"], None)
                self._messages[item["id"]] = item

    def _repair_tail(self) -> None:
        """断电时最后一行可能只写了一半：截掉它，免得下一次追加接在这半行后面一起坏掉。"""
        try:
            with self._path.open("rb+") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                if size == 0:
                    return
                handle.seek(size - 1)
                if handle.read(1) == b"\n":
                    return
                position = size
                while position > 0:
                    step = min(65536, position)
                    handle.seek(position - step)
                    chunk = handle.read(step)
                    index = chunk.rfind(b"\n")
                    if index >= 0:
                        handle.truncate(position - step + index + 1)
                        return
                    position -= step
                handle.truncate(0)
        except FileNotFoundError:
            return
        except OSError as error:
            sys.stderr.write("消息日志末尾修不了：%s\n" % error)

    def max_seq(self) -> int:
        return max([record["seq"] for record in self._messages.values()] + [self.floor, 0])

    def _append(self, records: Iterable[Dict[str, Any]]) -> None:
        lines = []
        for record in records:
            stored = dict(record)
            stored["partial"] = ""
            lines.append(json.dumps(stored, ensure_ascii=False))
        if not lines:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
                handle.flush()
            self._lines += len(lines)
        except OSError as error:
            sys.stderr.write("消息日志写不下去：%s\n" % error)
            return
        if self._lines > MAX_MESSAGES * COMPACT_FACTOR:
            self.compact()

    def compact(self) -> None:
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                if self.floor:
                    handle.write(json.dumps({"id": "", "floor": self.floor}) + "\n")
                for record in self._messages.values():
                    handle.write(json.dumps(dict(record, partial=""), ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary), str(self._path))
            self._lines = len(self._messages) + (1 if self.floor else 0)
        except OSError as error:
            sys.stderr.write("消息日志整理不了：%s\n" % error)

    # ---- 读 ----

    def get(self, message_id: str) -> Optional[Dict[str, Any]]:
        record = self._messages.get(message_id)
        return dict(record) if record is not None else None

    def since(self, conversation: str, after: int, upto: int,
              include_desktop: bool = False) -> List[Dict[str, Any]]:
        return sorted(
            (self._copy(record) for record in self._messages.values()
             if after < record["seq"] <= upto
             and (record["conversation"] == conversation
                  or (include_desktop and record["conversation"] == DESKTOP_CONVERSATION))),
            key=lambda record: record["seq"])

    def own_since(self, after: int, upto: int) -> List[Dict[str, Any]]:
        """这台 Runtime 自己的消息（不含别的 Runtime 的副本），所有对话，按序号排。
        转交给别的 Runtime 的占位（relay_to）也不算：那句话以那台的记录为准。"""
        return sorted(
            (self._copy(record) for record in self._messages.values()
             if after < record["seq"] <= upto and not record.get("mesh_origin")
             and not record.get("relay_to")),
            key=lambda record: record["seq"])

    @staticmethod
    def _copy(record: Dict[str, Any]) -> Dict[str, Any]:
        copy = dict(record)
        copy["author"] = dict(record["author"])
        copy["source"] = dict(record["source"])
        copy["attachments"] = [dict(item) for item in record.get("attachments", [])]
        return copy

    def __len__(self) -> int:
        return len(self._messages)

    # ---- 写 ----

    def _put(self, record: Dict[str, Any], persist: bool = True) -> Dict[str, Any]:
        self._stamp(record)
        # 修订号就用序号：序号跨重启只增不减（Cards 预留号段），没落盘的生成中修订也不会
        # 让重启后的修订号比客户端手里的小。
        record["seq"] = self._bump()
        record["rev"] = record["seq"]
        self._messages.pop(record["id"], None)
        self._messages[record["id"]] = record
        if persist:
            self._append([record])
        return record

    def observe(self, card: Dict[str, Any]) -> None:
        """卡变了：卡上每个条目对应的消息和日志对一遍，变了的记下来。

        卡上的条目是消息的副本：哪边先有都行，同一个编号就是同一条。卡上写着的文字就是
        定稿（status final）；Service 已经标成 failed 的保持 failed。"""
        for entry in card["entries"]:
            if not entry.get("id"):
                # 没带编号的条目（比如重启提示）：配一个，以后它就固定叫这个。
                entry["id"] = "%s-%s" % (card["id"], uuid.uuid4().hex[:12])
            wanted = entry_message(card, entry)
            current = self._messages.get(wanted["id"])
            status = "failed" if current is not None and current["status"] == "failed" else "final"
            if (current is not None and current["status"] == status and not current["partial"]
                    and all(current.get(key) == wanted[key] for key in wanted)):
                continue
            record = dict(current) if current is not None else {"rev": 0}
            record.update(wanted, status=status, partial="")
            self._put(record)
        run_id = card.get("run_id") or ""
        if run_id:
            # 后台结果的转述正在生成：它定稿后会以这个编号上卡。
            live = run_id + "-summary:reply"
            text = card.get("summary_partial") or ""
            current = self._messages.get(live)
            active = card.get("state") in ("talking", "working", "waiting")
            on_card = any(entry.get("id") == live for entry in card["entries"])
            if current is not None and current["status"] in ("queued", "streaming") \
                    and not on_card and not active:
                # 事已经结束，转述却没有上卡（取消了、没成、或者 Runtime 重启了）：收尾。
                ended = "cancelled" if card.get("state") == "cancelled" else "failed"
                self._put(dict(current, status=ended, partial=""))
            elif text and active:
                if current is None or (current["status"] in ("queued", "streaming")
                                       and current["partial"] != text):
                    record = dict(current) if current is not None else {
                        "rev": 0, "id": live, "conversation": card["conversation"],
                        "task": card["id"], "scope": "main",
                        "author": {"kind": "xiaoyou", "name": "xiaoyou"}, "kind": "summary",
                        "reply_to": None, "text": "", "source": {"type": "runtime"},
                        "attachments": [], "created_at": card.get("updated_at") or 0}
                    record.update(status="streaming", partial=text)
                    self._put(record, persist=current is None)

    def activity(self, card: Dict[str, Any], author: str, phase: str, text: str,
                 event_id: str, at: float) -> bool:
        current = self._messages.get(event_id)
        if current is not None:
            if phase != "message" or current.get("text") == text:
                return False
            self._put(dict(current, text=text))
            return True
        self._put({"id": event_id, "conversation": card["conversation"], "task": card["id"],
                   "scope": "task", "author": {"kind": "agent", "name": author},
                   "kind": "activity", "phase": phase, "text": text, "partial": "",
                   "reply_to": None, "source": {"type": "runtime"}, "attachments": [],
                   "created_at": at, "status": "final"})
        return True

    def request(self, message: Dict[str, Any]) -> None:
        """Service 收下的一次请求（见 service.py）变了：主人那句话和小幽的回答这两条消息。

        卡知道得更多（归到哪件事、哪种回答），所以卡已经写过的字段这里不改；这里只管
        “还没上卡”时的样子、生成中的那段、以及失败。"""
        base = message["id"]
        text = message.get("text") or ""
        status = message["status"]
        you = self._messages.get(base + ":you")
        if you is not None and you.get("mesh_origin"):
            you = dict(you, status="final")  # 转交给别的 Runtime 的话：那台的记录为准
        if you is None:
            record = self._request_record(message, ":you")
            record.update(text=text, status="final" if text else (
                "failed" if status == "failed" else "queued"))
            self._put(record)
        elif you["status"] != "final":
            # 语音：识别出文字之前是 queued，识别出来就定稿；没识别出来就失败。
            if text:
                self._put(dict(you, text=text, status="final"))
            elif status == "failed" and you["status"] != "failed":
                self._put(dict(you, status="failed"))
        reply = self._messages.get(base + ":reply")
        if reply is not None and reply.get("mesh_origin"):
            return  # 那台的回复已经到了，这里的占位不再改它
        partial = message.get("partial") or ""
        if status == "failed":
            wanted, words = "failed", message.get("error") or ""
        elif status == "done":
            wanted, words = "final", message.get("reply") or ""
        elif status == "running" and (partial or (reply is not None
                                                  and reply["status"] == "streaming")):
            # 空的 partial 是把已经生成的那段收回：仍然在生成中，只是现在没有字。
            wanted, words = "streaming", ""
        else:
            wanted, words = "queued", ""
        if reply is not None and reply["status"] in ("final", "failed", "cancelled"):
            if wanted == "failed" and reply["status"] == "final" and reply["text"] == words:
                record = dict(reply)
                record["status"] = "failed"
                self._put(record)
            return  # 定稿以后不会退回生成中
        if reply is not None and reply["status"] == wanted and reply["partial"] == partial \
                and (wanted != "final" or reply["text"] == words):
            return
        record = dict(reply) if reply is not None else self._request_record(message, ":reply")
        record.update(status=wanted, partial=partial if wanted == "streaming" else "")
        if wanted in ("final", "failed"):
            record["text"] = words
        # 生成中的那段不落盘；状态变了才写。
        self._put(record, persist=reply is None or reply["status"] != wanted)

    def replicate(self, record: Dict[str, Any]) -> bool:
        """别的 Runtime 的一条消息（小幽网络，mesh.py）：编号、所属的事已经换成这里的，
        mesh_origin 记着它是哪台的。原样记下，序号、修订号用这里的；没变就不记。

        这里转交给那台的话（relay_to 是那台）先有一份占位，那台的那一条来了就替换它，
        编号不变：手机上同一句话只出现一次。"""
        current = self._messages.get(record["id"])
        if current is not None and not current.get("mesh_origin") \
                and current.get("relay_to") != record.get("mesh_origin"):
            return False  # 这台自己的消息，不让别人改
        wanted = dict(record)
        wanted.pop("seq", None)
        wanted.pop("rev", None)
        wanted.setdefault("partial", "")
        if current is not None and all(current.get(key) == value for key, value in wanted.items()) \
                and not (set(current) - set(wanted) - {"seq", "rev", "relay_to"}):
            return False
        persist = current is None or any(current.get(key) != wanted.get(key)
                                         for key in wanted if key != "partial")
        wanted["rev"] = 0
        self._put(wanted, persist=persist)
        return True

    @staticmethod
    def _request_record(message: Dict[str, Any], suffix: str) -> Dict[str, Any]:
        you = suffix == ":you"
        extra = {key: message[key] for key in ("relay_to", "delegated_from") if message.get(key)}
        return {
            **extra,
            "rev": 0, "id": message["id"] + suffix, "conversation": message["conversation"],
            "task": message.get("card") if message.get("pin") else None,
            "scope": "task" if message.get("pin") else "main",
            "author": {"kind": "owner", "name": "you"} if you
            else {"kind": "xiaoyou", "name": "xiaoyou"},
            "kind": "user" if you else "reply",
            "reply_to": None if you else message["id"] + ":you",
            "text": "", "partial": "", "source": {"type": "runtime"},
            "attachments": [dict(item) for item in message.get("attachments", [])] if you else [],
            "created_at": message.get("created_at") or 0,
        }

    def retain(self, keep: Iterable[str]) -> None:
        """超过保留条数时挤掉最早的、不在 keep（现有卡上的消息）里的。"""
        # Tool-heavy work must never consume the conversation's retention budget.
        activities = [key for key, record in self._messages.items() if record.get("kind") == "activity"]
        evicted = False
        if len(activities) > MAX_ACTIVITIES:
            for key in activities[:len(activities) - MAX_ACTIVITIES + MAX_ACTIVITIES // 10]:
                self.floor = max(self.floor, self._messages.pop(key)["seq"])
                evicted = True
        count = sum(record.get("kind") != "activity" for record in self._messages.values())
        if count <= MAX_MESSAGES:
            if evicted:
                self.compact()
            return
        keep = set(keep)
        # 一次多挤掉十分之一：不然之后每来一条都要整份重写一次文件。
        target = MAX_MESSAGES - MAX_MESSAGES // 10
        for message_id in list(self._messages):
            if count <= target:
                break
            record = self._messages[message_id]
            if (record.get("kind") == "activity" or message_id in keep
                    or record["status"] in ("queued", "streaming")):
                continue
            self.floor = max(self.floor, record["seq"])
            del self._messages[message_id]
            count -= 1
            evicted = True
        if evicted:
            self.compact()  # floor 要写下去，文件也顺便变小
