"""记住每个对话接着聊要用的会话编号，以及从别的 Runtime 带过来、还没告诉模型的对话。

对话内容本身由后端（Claude Code）保存；这里只存“对话名 → 会话编号”这一张小表，
这样 Runtime 重启、换端口都能接着之前的对话聊。
"""

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional


class Store:
    def __init__(self, state_dir: Path):
        self._path = Path(state_dir) / "sessions.json"
        self._voice_dir = Path(state_dir) / "voice"
        self._shared = SharedHistory(Path(state_dir) / "shared.json")
        # 录音只在识别前短暂落盘；上次没来得及删的（比如中途被关掉）启动时清掉。
        for leftover in self._voice_dir.glob("*.wav"):
            try:
                leftover.unlink()
            except OSError:
                pass
        self._lock = threading.Lock()
        self._sessions: Dict[str, str] = {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {}
        except (OSError, json.JSONDecodeError) as error:
            # 不悄悄丢掉用户的对话指向：文件坏了就停下来让人看一眼。
            raise RuntimeError("读不了会话记录 %s：%s" % (self._path, error))
        if isinstance(raw, dict):
            self._sessions = {
                key: value for key, value in raw.items()
                if isinstance(key, str) and isinstance(value, str)
            }

    def voice_file(self, message_id: str) -> Path:
        """一条语音消息的录音暂存位置。"""
        self._voice_dir.mkdir(parents=True, exist_ok=True)
        return self._voice_dir / (message_id + ".wav")

    @property
    def shared(self) -> "SharedHistory":
        return self._shared

    def session(self, conversation: str) -> Optional[str]:
        with self._lock:
            return self._sessions.get(conversation)

    def remember(self, conversation: str, session_id: Optional[str]) -> None:
        with self._lock:
            if session_id:
                self._sessions[conversation] = session_id
            else:
                self._sessions.pop(conversation, None)
            self._save()

    def forget(self, conversation: str) -> bool:
        with self._lock:
            existed = self._sessions.pop(conversation, None) is not None
            if existed:
                self._save()
            return existed

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self._sessions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(str(temporary), str(self._path))


MAX_KNOWN_TURNS = 400
MAX_PENDING_TURNS = 30


class SharedHistory:
    """几台 Runtime 之间靠手机带话。

    Claude Code 的会话只存在各自的电脑上，搬不走。手机记着最近的对话，换一台 Runtime 时
    把它们交过来；这里记下哪些轮次这台已经知道（自己答的，或者已经转告过模型的），
    没见过的先存着，等这个对话的下一句话到来时一并告诉模型，然后就算知道了。
    """

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, List[Any]]] = {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {}
        except (OSError, json.JSONDecodeError):
            raw = {}  # 丢了只会少带几句旧话，不值得为它拒绝启动
        if isinstance(raw, dict):
            for conversation, entry in raw.items():
                if isinstance(entry, dict):
                    self._data[conversation] = {
                        "known": [x for x in entry.get("known", []) if isinstance(x, str)],
                        "pending": [x for x in entry.get("pending", []) if isinstance(x, dict)],
                    }

    def _entry(self, conversation: str) -> Dict[str, List[Any]]:
        return self._data.setdefault(conversation, {"known": [], "pending": []})

    def know(self, conversation: str, turn_id: str) -> None:
        """这一轮是这台 Runtime 自己答的，以后手机再带回来也不用转告。"""
        with self._lock:
            entry = self._entry(conversation)
            if turn_id not in entry["known"]:
                entry["known"].append(turn_id)
                del entry["known"][:-MAX_KNOWN_TURNS]
                self._save()

    def offer(self, conversation: str, turns: List[Dict[str, Any]]) -> int:
        """手机带来的若干轮对话；返回其中这台 Runtime 之前不知道的轮数。"""
        with self._lock:
            entry = self._entry(conversation)
            seen = set(entry["known"]) | {turn["id"] for turn in entry["pending"]}
            fresh = [turn for turn in turns if turn["id"] not in seen]
            if fresh:
                entry["pending"].extend(fresh)
                entry["pending"].sort(key=lambda turn: turn.get("at") or 0)
                del entry["pending"][:-MAX_PENDING_TURNS]
                self._save()
            return len(fresh)

    def take(self, conversation: str) -> List[Dict[str, Any]]:
        """取出还没转告模型的轮次（不删除；转告成功后调用 told）。"""
        with self._lock:
            return list(self._entry(conversation)["pending"])

    def told(self, conversation: str, turns: List[Dict[str, Any]]) -> None:
        with self._lock:
            entry = self._entry(conversation)
            done = {turn["id"] for turn in turns}
            entry["pending"] = [turn for turn in entry["pending"] if turn["id"] not in done]
            entry["known"].extend(sorted(done - set(entry["known"])))
            del entry["known"][:-MAX_KNOWN_TURNS]
            self._save()

    def clear_pending(self, conversation: str) -> None:
        """对话重新开始：存着没说的旧话不用再说，但仍然记得见过它们。"""
        with self._lock:
            entry = self._entry(conversation)
            if entry["pending"]:
                entry["known"].extend(turn["id"] for turn in entry["pending"])
                del entry["known"][:-MAX_KNOWN_TURNS]
                entry["pending"] = []
                self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self._data, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(str(temporary), str(self._path))
