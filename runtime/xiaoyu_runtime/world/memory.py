"""小鱼记得的关于主人的事：主人的偏好、定下的规矩、长期的情况、答应过的事。

分两层（参考 Muse 的做法）：
- 注入层 state_dir/memory/owner.md：现行的几十条，一行一条，每一轮都放进她的提示。
  每条带类型、记下的日期和主人的原话（出处），主人也能直接打开改。
- 日志层 state_dir/memory/log/YYYY-MM-DD.md：每次记、改、删都追加一行，旧说法和纠正都留着。
  现行口径唯一（注入层），历史不丢（日志层）；recall 也会查这里。

她用 remember / replace / forget 三个工具（voice_tools）来改。红线：像密码、密钥、验证码的
内容不记（需要的话只记“它存在哪”）。条数和总字数有上限，满了丢最旧的。
"""

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

from ..errors import EffectUncertain

MAX_NOTES = 60
MAX_NOTE_CHARS = 200
MAX_QUOTE_CHARS = 120
MAX_TOTAL_CHARS = 8000
KINDS = ("偏好", "规矩", "事实", "承诺")

HEADER = "# 小鱼记得的关于主人的事\n\n一行一条：[日期][类型] 内容 —— 原话：「…」。可以直接改这个文件。\n\n"
# - [2026-10-20][偏好] 内容 —— 原话：「…」     （类型和原话可以没有：早先记的就只有日期）
_LINE = re.compile(r"^- (?:\[(\d{4}-\d\d-\d\d)\])?(?:\[([^\]]{1,4})\])? ?(.+?)(?: —— 原话：「(.*)」)?$")

# 像凭据的东西：常见密钥前缀、一长串字母数字混排、或者“密码/口令/验证码……”后面跟着值。
_SECRET = re.compile(
    r"(sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{12,}"
    r"|(?=[A-Za-z0-9_-]{28,})(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{28,}"
    r"|(密码|口令|验证码|密钥|token|api ?key|password|passcode)\s*(是|为|:|：|=)\s*\S{4,})", re.I)


class Note:
    def __init__(self, text: str, day: str = "", kind: str = "", quote: str = ""):
        self.text, self.day, self.kind, self.quote = text, day, kind, quote

    def line(self) -> str:
        head = "".join("[%s]" % part for part in (self.day, self.kind) if part)
        return "- %s%s%s" % (head, " " if head else "", self.text) + (
            " —— 原话：「%s」" % self.quote if self.quote else "")

    def shown(self) -> str:
        """提示里给她看的样子。"""
        head = "".join("[%s]" % part for part in (self.day, self.kind) if part)
        return head + (" " if head else "") + self.text + ("（原话：「%s」）" % self.quote if self.quote else "")


class OwnerMemory:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._log_dir = self._path.parent / "log"
        self._lock = threading.Lock()

    @property
    def log_dir(self) -> Path:
        return self._log_dir

    def notes(self) -> List[str]:
        """现行的记忆，旧的在前；给提示用的样子（带日期、类型、原话）。"""
        with self._lock:
            return [note.shown() for note in self._read()]

    @staticmethod
    def _revision(notes):
        return hashlib.sha256(json.dumps([note.shown() for note in notes], ensure_ascii=False).encode()).hexdigest()

    def revision(self):
        with self._lock:
            return self._revision(self._read())

    def texts(self) -> List[str]:
        """现行的记忆，只有内容（后台整理去重用）。"""
        with self._lock:
            return [note.text for note in self._read()]

    def remember(self, note: str, today: Optional[str] = None, kind: str = "",
                 quote: str = "") -> str:
        """记一条。和已有的一样就不重复记。返回给她看的结果。"""
        cleaned, problem = _checked(note, quote)
        if problem:
            return problem
        text, said = cleaned
        day = today or time.strftime("%Y-%m-%d")
        kind = kind if kind in KINDS else ""
        with self._lock:
            notes = self._read()
            if any(item.text == text for item in notes):
                return "这条之前就记着了"
            notes.append(Note(text, day, kind, said))
            dropped = self._fit(notes)
            self._write(notes)
            self._log("记", text, said, day)
            for gone in dropped:
                self._log("满了丢掉", gone.text, "", day)
        return "记下了：%s" % text

    def replace(self, number: int, note: str, today: Optional[str] = None, quote: str = "", *, expected=None) -> str:
        """第 number 条（从 1 数）改成新的说法：旧的从注入层拿掉，日志里记下“取代”。"""
        cleaned, problem = _checked(note, quote)
        if problem:
            return problem
        text, said = cleaned
        day = today or time.strftime("%Y-%m-%d")
        with self._lock:
            notes = self._read()
            if expected is not None and expected != self._revision(notes):
                raise EffectUncertain("记忆正文已改变，不能沿用旧编号修改。")
            if not 1 <= number <= len(notes):
                return "没有第 %d 条" % number
            old = notes[number - 1]
            notes[number - 1] = Note(text, day, old.kind, said)
            self._write(notes)
            self._log("改", "%s（取代：%s）" % (text, old.text), said, day)
        return "改好了：%s（原来是：%s）" % (text, old.text)

    def forget(self, number: int, today: Optional[str] = None, *, expected=None) -> str:
        """删掉第 number 条（从 1 数，跟提示里的编号一致）。日志里留着记录。"""
        day = today or time.strftime("%Y-%m-%d")
        with self._lock:
            notes = self._read()
            if expected is not None and expected != self._revision(notes):
                raise EffectUncertain("记忆正文已改变，不能沿用旧编号删除。")
            if not 1 <= number <= len(notes):
                return "没有第 %d 条" % number
            gone = notes.pop(number - 1)
            self._write(notes)
            self._log("删", gone.text, "", day)
        return "删掉了：%s" % gone.text

    def log_files(self) -> List[Path]:
        try:
            return sorted(self._log_dir.glob("*.md"))
        except OSError:
            return []

    def _fit(self, notes: List[Note]) -> List[Note]:
        dropped = []
        while len(notes) > MAX_NOTES or sum(len(item.line()) for item in notes) > MAX_TOTAL_CHARS:
            dropped.append(notes.pop(0))
        return dropped

    def _read(self) -> List[Note]:
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        notes = []
        for line in lines:
            found = _LINE.match(line.strip())
            if found and found.group(3).strip():
                notes.append(Note(found.group(3).strip(), found.group(1) or "", found.group(2) or "",
                                  (found.group(4) or "").strip()))
        return notes

    def _write(self, notes: List[Note]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        _atomic(self._path, HEADER + "".join(note.line() + "\n" for note in notes))

    def _log(self, what: str, text: str, quote: str, day: str) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        line = "- %s %s：%s%s\n" % (time.strftime("%H:%M"), what, text,
                                    " —— 原话：「%s」" % quote if quote else "")
        target = self._log_dir / ("%s.md" % day)
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            os.chmod(target, 0o600)
        _sync_directory(target.parent)


def _checked(note: str, quote: str) -> Tuple[Tuple[str, str], Optional[str]]:
    text = " ".join((note or "").split())[:MAX_NOTE_CHARS]
    said = " ".join((quote or "").split())[:MAX_QUOTE_CHARS].replace("」", "”")
    if not text:
        return ("", ""), "没有要记的内容"
    if _SECRET.search(text) or _SECRET.search(said):
        return ("", ""), ("这像是密码、密钥或验证码，不能记。需要的话只记它存在哪（比如“GitHub 令牌在电脑的钥匙串里”），"
                          "不记值本身。")
    return (text, said), None


def _atomic(path: Path, body: str) -> None:
    handle, name = tempfile.mkstemp(prefix=".owner-", dir=str(path.parent))
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name == "posix":
        os.chmod(name, 0o600)
    os.replace(name, path)
    _sync_directory(path.parent)


def _sync_directory(path):
    if os.name != "posix":
        return
    folder = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


def prompt_section(notes: List[str]) -> List[str]:
    """系统提示里的那一段；没记过东西就没有这一段。"""
    if not notes:
        return []
    lines = ["## 你记得的关于主人的事",
             "主人说过、要你一直照做的；有冲突以新的为准。说法变了用 replace 改，不要了用 forget 删，"
             "新学到值得长期记的用 remember。记忆是以前的情况，不是现在的事实：会变的事（进度、价格、"
             "状态）说之前先核实；记得主人同意过某件事，不等于这次可以不问就做。"]
    lines += ["%d. %s" % (index + 1, item) for index, item in enumerate(notes)]
    return lines
