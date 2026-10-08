"""记住每个对话接着聊要用的会话编号。

对话内容本身由后端（Claude Code）保存；这里只存“对话名 → 会话编号”这一张小表，
这样 Runtime 重启、换端口都能接着之前的对话聊。
"""

import json
import os
import threading
from pathlib import Path
from typing import Dict, Optional


class Store:
    def __init__(self, state_dir: Path):
        self._path = Path(state_dir) / "sessions.json"
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
