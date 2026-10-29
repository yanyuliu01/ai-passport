"""消息记录（state/messages.json）：Runtime 收下的每句话，落盘后才回“收到了”。

以前收到的话只在内存里：Runtime 一重启，排着队的话没了，手机拿着原来的编号来问也
查不到，重发一次还会被当成新的一句再做一遍。现在：

- 每句话收下时先写进这个文件，再回给调用方；状态变了（开始处理、处理完、没成）也写。
  正在生成的那段回复（partial）变得太快，不写，只在内存里给在线的客户端看。
- 去重表单独留（比消息本身留得久）：键是“对话 + client_id”，记着当时那句话内容的
  哈希。同一个对话里同一个 client_id 再来，内容一样就还是那一句；内容不一样是调用方
  弄错了，拒绝（409），不悄悄当成同一句。
- 每句话带着它最近一次变化拿到的序号（和卡共用一条，见 cards.py）。文件里也记着
  见过的最大序号，重启后序号不会往回走。

R3.3 起和任务一样走“快照 + 日志”（world.py）：每次变化只把变了的消息、去重项按差量
追加进 messages.jsonl（追加后 fsync 才算写下），messages.json 每攒 SNAPSHOT_EVERY 条
才整份重写一次，写稳后压缩日志。启动时读快照、重放它之后的记录。别的进程读用 current()。

每句话也是一个操作（operation_id，见 operations.py）：收下时是 pending，开始处理前
先写成 running，结束是 succeeded / failed / cancelled；重启时不知道做到哪一步的是 unknown。

这里只管读写文件。重启后排着的话怎么办（接着做还是标成没做完）由 Service 决定。
"""

import hashlib
import json
import sys
from copy import deepcopy
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..world import world
from .operations import migrate

# 处理完的消息在去重表里留的条数；比消息本身（Service.MAX_KEPT_MESSAGES）多得多，
# 消息被挤掉以后，拿着旧 client_id 重试的也不会被当成新的一句。
MAX_DEDUPE = 2000
FORMAT = 2  # 2：快照记着包含到哪条日志（"journal"）


def content_hash(kind: str, text: str, audio: Optional[bytes]) -> str:
    """一句话内容的指纹：打字的看文字，语音的看录音字节。"""
    digest = hashlib.sha256()
    digest.update(kind.encode("utf-8") + b"\0")
    digest.update(audio if audio is not None else text.encode("utf-8"))
    return digest.hexdigest()


def dedupe_key(conversation: str, client_id: str) -> str:
    return conversation + "\0" + client_id


def _journal(path: Path) -> Path:
    return path.with_name(path.stem + ".jsonl")


def _state(raw: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """快照里的消息和去重项放进一个以键区分的状态：m:编号、d:对话+client_id。"""
    state: Dict[str, Dict[str, Any]] = {}
    for item in raw.get("messages", []) if isinstance(raw.get("messages"), list) else []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            state["m:" + item["id"]] = world._plain(item)
    for item in raw.get("dedupe", []) if isinstance(raw.get("dedupe"), list) else []:
        if (isinstance(item, dict) and isinstance(item.get("conversation"), str)
                and isinstance(item.get("client_id"), str)):
            state["d:" + dedupe_key(item["conversation"], item["client_id"])] = world._plain(item)
    return state


def _replayed(path: Path, raw: Any, records=None) -> Tuple[Dict[str, Dict[str, Any]], int, int]:
    """(快照 + 日志重放出来的状态, 日志编号到了哪, 日志里见过的最大序号)"""
    raw = raw if isinstance(raw, dict) else {}
    records = world.read(_journal(path)) if records is None else records
    state, last, _highest = world.replay_onto(_state(raw), raw.get("journal", 0 if not raw else None), records)
    seq = max([record["seq"] for record in records
               if isinstance(record.get("seq"), int) and not isinstance(record.get("seq"), bool)] + [0])
    return state, last, seq


def current(path: Path) -> Dict[str, Any]:
    """只读：现在的消息记录（快照 + 日志），形状和 messages.json 一样。不改任何文件。"""
    path = Path(path)
    raw, records = world.read_checkpoint(path, _journal(path))
    state, _last, seq = _replayed(path, raw, records)
    saved = raw.get("seq") if isinstance(raw, dict) else 0
    return {"format": FORMAT, "seq": max(seq, saved if isinstance(saved, int) else 0),
            "messages": [item for key, item in state.items() if key.startswith("m:")],
            "dedupe": [item for key, item in state.items() if key.startswith("d:")]}


class Ledger:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._journal: Optional[world.Journal] = None

    @property
    def path(self) -> Path:
        return self._path

    def saved_effects(self, message_id):
        """Effects whose journal append and fsync already returned successfully."""
        if self._journal is None:
            return None
        return deepcopy(self._journal._last.get("m:" + message_id, {}).get("_effects"))

    def saved_effect_review(self, message_id):
        if self._journal is None:
            return None
        owner = self._journal._last.get("m:" + message_id, {}).get("_effect_owner")
        return deepcopy(owner.get("review")) if isinstance(owner, dict) else None

    def load(self) -> Tuple["OrderedDict[str, Dict[str, Any]]", "OrderedDict[str, Dict[str, Any]]", int]:
        """读回（消息, 去重表, 见过的最大序号）。没有文件是第一次启动；文件坏了不拒绝启动，
        但把坏文件留在旁边（.broken），方便以后查。"""
        messages: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        dedupe: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {}
        except (OSError, ValueError):
            try:
                self._path.replace(self._path.with_suffix(self._path.suffix + ".broken"))
            except OSError:
                pass
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        state, last, seq = _replayed(self._path, raw)
        saved = raw.get("seq")
        seq = max(seq, saved if isinstance(saved, int) and not isinstance(saved, bool) and saved > 0 else 0)
        for key, item in state.items():
            if (key.startswith("m:") and isinstance(item.get("conversation"), str)
                    and isinstance(item.get("status"), str)):
                message = dict(item)
                message.setdefault("events", [])
                message["partial"] = ""
                if not isinstance(message.get("rev"), int):
                    message["rev"] = 0
                if not isinstance(message.get("seq"), int):
                    message["seq"] = 0
                migrate(message)  # 旧记录没有 operation_id：按 status 补上
                seq = max(seq, message["seq"])
                messages[message["id"]] = message
            elif key.startswith("d:") and isinstance(item.get("id"), str):
                dedupe[key[2:]] = dict(item)
        # 差量都相对重放出来的状态算；下一次 save 会把这里的清理（partial、补字段）补进日志。
        self._journal = world.Journal(_journal(self._path), state, last, state, seq)
        return messages, dedupe, seq

    def save(self, messages: "OrderedDict[str, Dict[str, Any]]",
             dedupe: "OrderedDict[str, Dict[str, Any]]", seq: int) -> None:
        """把变了的追加进日志（写下才返回）；攒够了写一份快照、压缩日志。"""
        while len(dedupe) > MAX_DEDUPE:
            dedupe.popitem(last=False)
        if self._journal is None:
            self.load()
        state: Dict[str, Dict[str, Any]] = {}
        for message in messages.values():
            item = dict(message)
            item["partial"] = ""  # 生成中的那段不落盘：重启后它已经不会再变了
            item["events"] = [dict(event) for event in message.get("events", [])]
            state["m:" + message["id"]] = item
        for key, item in dedupe.items():
            state["d:" + key] = item
        self._journal.sync(state, seq)  # 写下了：从这里起就算收下
        if self._journal.pending >= world.SNAPSHOT_EVERY or not self._path.exists():
            try:
                self.snapshot(state, seq)
            except OSError as error:
                # 日志已经写下，快照没写成不影响收下；下一次再试。
                sys.stderr.write("消息快照写不下去：%s\n" % error)

    def snapshot(self, state: Dict[str, Dict[str, Any]], seq: int) -> None:
        journal = self._journal.n
        # 写稳再换名、再压缩日志：断电时要么是旧快照 + 完整日志，要么是新快照。
        world.durable_write(self._path, {
            "format": FORMAT, "seq": seq, "journal": journal,
            "messages": [item for key, item in state.items() if key.startswith("m:")],
            "dedupe": [item for key, item in state.items() if key.startswith("d:")]})
        self._journal.compact(journal)
