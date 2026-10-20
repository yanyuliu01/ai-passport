"""小幽记得的关于主人的事：主人的偏好、定下的规矩、说过要她以后照做的话。

以前她嘴上说“记下了”“以后按这个来”，其实什么都没存，下一轮就忘了。现在存在
state_dir/memory/owner.md 里，一行一条（带记下的日期），每一轮都放进她的系统提示；
她用 remember / forget 两个工具（voice_tools）来记和删。

只记经得起下次检验的事（偏好、规矩、长期的情况），不记流水账；条数和总字数有上限，
满了丢最旧的。文件是普通文字，主人也能直接打开改。
"""

import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Optional

MAX_NOTES = 60
MAX_NOTE_CHARS = 200
MAX_TOTAL_CHARS = 6000
_LINE = re.compile(r"^- (?:\[(\d{4}-\d\d-\d\d)\] )?(.+)$")

HEADER = "# 小幽记得的关于主人的事\n\n一行一条；可以直接改这个文件。\n\n"


class OwnerMemory:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()

    def notes(self) -> List[str]:
        """记着的事，旧的在前；每条是“[日期] 内容”。"""
        with self._lock:
            return self._read()

    def remember(self, note: str, today: Optional[str] = None) -> str:
        """记一条。和已有的一样就不重复记。返回给她看的结果。"""
        text = " ".join((note or "").split())[:MAX_NOTE_CHARS]
        if not text:
            return "没有要记的内容"
        day = today or time.strftime("%Y-%m-%d")
        with self._lock:
            notes = self._read()
            if any(_body(item) == text for item in notes):
                return "这条之前就记着了"
            notes.append("[%s] %s" % (day, text))
            while len(notes) > MAX_NOTES or sum(len(item) for item in notes) > MAX_TOTAL_CHARS:
                notes.pop(0)
            self._write(notes)
        return "记下了：%s" % text

    def forget(self, number: int) -> str:
        """删掉第 number 条（从 1 数，跟提示里的编号一致）。"""
        with self._lock:
            notes = self._read()
            if not 1 <= number <= len(notes):
                return "没有第 %d 条" % number
            gone = notes.pop(number - 1)
            self._write(notes)
        return "删掉了：%s" % _body(gone)

    def _read(self) -> List[str]:
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        notes = []
        for line in lines:
            found = _LINE.match(line.strip())
            if found:
                notes.append("[%s] %s" % (found.group(1), found.group(2).strip()) if found.group(1)
                             else found.group(2).strip())
        return notes

    def _write(self, notes: List[str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        body = HEADER + "".join("- %s\n" % item for item in notes)
        handle, name = tempfile.mkstemp(prefix=".owner-", dir=str(self._path.parent))
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(body)
        if os.name == "posix":
            os.chmod(name, 0o600)
        os.replace(name, self._path)


def _body(item: str) -> str:
    return re.sub(r"^\[\d{4}-\d\d-\d\d\] ", "", item)


def prompt_section(notes: List[str]) -> List[str]:
    """系统提示里的那一段；没记过东西就没有这一段。"""
    if not notes:
        return []
    lines = ["## 你记得的关于主人的事",
             "这是主人说过、要你一直照做的；有冲突以新的为准。主人改主意了就用 forget 删掉旧的、"
             "用 remember 记新的。"]
    lines += ["%d. %s" % (index + 1, item) for index, item in enumerate(notes)]
    return lines
