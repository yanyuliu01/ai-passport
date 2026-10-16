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

这里只管读写文件。重启后排着的话怎么办（接着做还是标成没做完）由 Service 决定。
"""

import hashlib
import json
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# 处理完的消息在去重表里留的条数；比消息本身（Service.MAX_KEPT_MESSAGES）多得多，
# 消息被挤掉以后，拿着旧 client_id 重试的也不会被当成新的一句。
MAX_DEDUPE = 2000
FORMAT = 1


def content_hash(kind: str, text: str, audio: Optional[bytes]) -> str:
    """一句话内容的指纹：打字的看文字，语音的看录音字节。"""
    digest = hashlib.sha256()
    digest.update(kind.encode("utf-8") + b"\0")
    digest.update(audio if audio is not None else text.encode("utf-8"))
    return digest.hexdigest()


def dedupe_key(conversation: str, client_id: str) -> str:
    return conversation + "\0" + client_id


class Ledger:
    def __init__(self, path: Path):
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Tuple["OrderedDict[str, Dict[str, Any]]", "OrderedDict[str, Dict[str, Any]]", int]:
        """读回（消息, 去重表, 见过的最大序号）。没有文件是第一次启动；文件坏了不拒绝启动，
        但把坏文件留在旁边（.broken），方便以后查。"""
        messages: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        dedupe: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return messages, dedupe, 0
        except (OSError, ValueError):
            try:
                self._path.replace(self._path.with_suffix(self._path.suffix + ".broken"))
            except OSError:
                pass
            return messages, dedupe, 0
        if not isinstance(raw, dict):
            return messages, dedupe, 0
        seq = raw.get("seq")
        seq = seq if isinstance(seq, int) and not isinstance(seq, bool) and seq > 0 else 0
        for item in raw.get("messages", []):
            if (isinstance(item, dict) and isinstance(item.get("id"), str)
                    and isinstance(item.get("conversation"), str)
                    and isinstance(item.get("status"), str)):
                message = dict(item)
                message.setdefault("events", [])
                message["partial"] = ""
                if not isinstance(message.get("rev"), int):
                    message["rev"] = 0
                if not isinstance(message.get("seq"), int):
                    message["seq"] = 0
                seq = max(seq, message["seq"])
                messages[message["id"]] = message
        for item in raw.get("dedupe", []):
            if (isinstance(item, dict) and isinstance(item.get("conversation"), str)
                    and isinstance(item.get("client_id"), str)
                    and isinstance(item.get("id"), str)):
                dedupe[dedupe_key(item["conversation"], item["client_id"])] = dict(item)
        return messages, dedupe, seq

    def save(self, messages: "OrderedDict[str, Dict[str, Any]]",
             dedupe: "OrderedDict[str, Dict[str, Any]]", seq: int) -> None:
        while len(dedupe) > MAX_DEDUPE:
            dedupe.popitem(last=False)
        stored = []
        for message in messages.values():
            item = dict(message)
            item["partial"] = ""  # 生成中的那段不落盘：重启后它已经不会再变了
            item["events"] = [dict(event) for event in message.get("events", [])]
            stored.append(item)
        data = json.dumps({"format": FORMAT, "seq": seq, "messages": stored,
                           "dedupe": list(dedupe.values())}, ensure_ascii=False) + "\n"
        # 写临时文件、刷到盘上、再换名：断电时要么是旧的一份，要么是新的一份，不会是半份。
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(self._path))
        try:
            directory = os.open(str(self._path.parent), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory)
        except OSError:
            pass
        finally:
            os.close(directory)
