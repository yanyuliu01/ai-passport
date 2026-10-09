"""小幽记在这台电脑上的东西。

三样：
  sessions.json    每个对话里（以及每件事里）、每个代理接着聊要用的会话编号
  transcript.json  小幽自己的对话记录：主人说了什么、她答了什么、是谁做的、哪些代理见过
  voice/           还没识别的录音（只在识别前短暂存在）

对话记录属于小幽，不属于任何一个代理。代理各自的会话里只有它参与过的部分；换一个
代理接话时，Runtime 从这里取出它没见过的那几轮先告诉它。手机从别的 Runtime 带过来
的对话也进这里，对所有本机代理来说都是“没见过”。
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_TURNS = 200
MAX_KNOWN_TURNS = 400
# 一个对话里没被任何人见过、等着转告的轮次最多留这么多（保留最近的）。
MAX_UNSEEN_TURNS = 30
ELSEWHERE = "elsewhere"


def _write(path: Path, data: Any, indent: Optional[int] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(path))


class Store:
    def __init__(self, state_dir: Path, legacy_agent: str = "claude"):
        """legacy_agent：0.3 及更早的会话表没有分代理，读到时把那些会话算在它名下。"""
        state_dir = Path(state_dir)
        self._path = state_dir / "sessions.json"
        self._voice_dir = state_dir / "voice"
        self._transcript = Transcript(state_dir / "transcript.json", state_dir / "shared.json")
        # 录音只在识别前短暂落盘；上次没来得及删的（比如中途被关掉）启动时清掉。
        for leftover in self._voice_dir.glob("*.wav"):
            try:
                leftover.unlink()
            except OSError:
                pass
        self._lock = threading.Lock()
        self._sessions: Dict[str, Dict[str, str]] = {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {}
        except (OSError, json.JSONDecodeError) as error:
            # 不悄悄丢掉用户的对话指向：文件坏了就停下来让人看一眼。
            raise RuntimeError("读不了会话记录 %s：%s" % (self._path, error))
        if isinstance(raw, dict):
            for conversation, value in raw.items():
                if not isinstance(conversation, str):
                    continue
                if isinstance(value, str):
                    self._sessions[conversation] = {legacy_agent: value}
                elif isinstance(value, dict):
                    self._sessions[conversation] = {
                        agent: session for agent, session in value.items()
                        if isinstance(agent, str) and isinstance(session, str)
                    }

    def voice_file(self, message_id: str) -> Path:
        """一条语音消息的录音暂存位置。"""
        self._voice_dir.mkdir(parents=True, exist_ok=True)
        return self._voice_dir / (message_id + ".wav")

    @property
    def transcript(self) -> "Transcript":
        return self._transcript

    def session(self, conversation: str, agent: str) -> Optional[str]:
        with self._lock:
            return self._sessions.get(conversation, {}).get(agent)

    def remember(self, conversation: str, agent: str, session_id: Optional[str]) -> None:
        with self._lock:
            sessions = self._sessions.setdefault(conversation, {})
            if session_id:
                if sessions.get(agent) == session_id:
                    return
                sessions[agent] = session_id
            elif sessions.pop(agent, None) is None:
                return
            self._save()

    def forget(self, conversation: str) -> bool:
        """忘掉这个对话里所有代理的会话，包括这个对话里每件事各自的会话；返回之前有没有。"""
        with self._lock:
            # 一件事的会话记在“对话名/卡的编号”下面。
            names = [name for name in self._sessions
                     if name == conversation or name.startswith(conversation + "/")]
            existed = any([self._sessions.pop(name, None) for name in names])
            if existed:
                self._save()
            return existed

    def _save(self) -> None:
        _write(self._path, {name: value for name, value in self._sessions.items() if value}, 2)


class Transcript:
    """小幽的对话记录，以及每一轮有哪些代理见过。

    一轮是 {"id", "text", "reply", "at", "by", "seen"}：by 是做这件事的代理（从别的
    Runtime 带来的是 elsewhere），seen 是已经知道这一轮的本机代理。
    """

    def __init__(self, path: Path, legacy_shared: Optional[Path] = None):
        self._path = path
        self._lock = threading.Lock()
        # 对话名 → {"turns": [...], "known": [...]}；known 是见过的轮次编号，
        # 包括已经被清掉的，用来认出手机重复带来的旧话。
        self._data: Dict[str, Dict[str, List[Any]]] = {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = self._from_legacy(legacy_shared)
        except (OSError, json.JSONDecodeError):
            raw = {}  # 丢了只会少带几句旧话，不值得为它拒绝启动
        if isinstance(raw, dict):
            for conversation, entry in raw.items():
                if not isinstance(conversation, str) or not isinstance(entry, dict):
                    continue
                turns = []
                for turn in entry.get("turns", []):
                    if (isinstance(turn, dict) and isinstance(turn.get("id"), str)
                            and isinstance(turn.get("text"), str)
                            and isinstance(turn.get("reply"), str)):
                        turns.append({
                            "id": turn["id"], "text": turn["text"], "reply": turn["reply"],
                            "at": turn.get("at") if isinstance(turn.get("at"), (int, float)) else 0,
                            "by": turn.get("by") if isinstance(turn.get("by"), str) else ELSEWHERE,
                            "seen": [x for x in turn.get("seen", []) if isinstance(x, str)],
                        })
                self._data[conversation] = {
                    "turns": turns,
                    "known": [x for x in entry.get("known", []) if isinstance(x, str)],
                }

    @staticmethod
    def _from_legacy(path: Optional[Path]) -> Dict[str, Any]:
        """0.3 的 shared.json：known 是见过的编号，pending 是还没转告的轮次。"""
        if path is None:
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        converted: Dict[str, Any] = {}
        if isinstance(raw, dict):
            for conversation, entry in raw.items():
                if isinstance(entry, dict):
                    converted[conversation] = {
                        "known": entry.get("known", []),
                        "turns": [dict(turn, by=ELSEWHERE, seen=[])
                                  for turn in entry.get("pending", []) if isinstance(turn, dict)],
                    }
        return converted

    def _entry(self, conversation: str) -> Dict[str, List[Any]]:
        return self._data.setdefault(conversation, {"turns": [], "known": []})

    def _note(self, entry: Dict[str, List[Any]], turn_id: str) -> None:
        if turn_id not in entry["known"]:
            entry["known"].append(turn_id)
            del entry["known"][:-MAX_KNOWN_TURNS]

    def add(self, conversation: str, turn_id: str, text: str, reply: str, by: str,
            seen: List[str]) -> None:
        """记下这台 Runtime 自己答的一轮；seen 是这一轮里听到了问和答的代理。"""
        with self._lock:
            entry = self._entry(conversation)
            entry["turns"] = [turn for turn in entry["turns"] if turn["id"] != turn_id]
            entry["turns"].append({
                "id": turn_id, "text": text, "reply": reply, "at": time.time(), "by": by,
                "seen": sorted(set(seen)),
            })
            del entry["turns"][:-MAX_TURNS]
            self._note(entry, turn_id)
            self._save()

    def offer(self, conversation: str, turns: List[Dict[str, Any]]) -> int:
        """手机带来的若干轮对话；返回其中这台 Runtime 之前不知道的轮数。"""
        with self._lock:
            entry = self._entry(conversation)
            fresh = [turn for turn in turns if turn["id"] not in entry["known"]]
            # 同一批里重复的编号只留第一条。
            unique: Dict[str, Dict[str, Any]] = {}
            for turn in fresh:
                unique.setdefault(turn["id"], turn)
            for turn in unique.values():
                entry["turns"].append({
                    "id": turn["id"], "text": turn["text"], "reply": turn["reply"],
                    "at": turn.get("at") or 0, "by": ELSEWHERE, "seen": [],
                })
                self._note(entry, turn["id"])
            if unique:
                # 带来的旧话按发生的时间排到该在的位置；没人见过的只留最近的若干轮。
                entry["turns"].sort(key=lambda turn: turn["at"] or 0)
                unseen = [turn for turn in entry["turns"] if not turn["seen"]]
                for stale in unseen[:-MAX_UNSEEN_TURNS]:
                    entry["turns"].remove(stale)
                del entry["turns"][:-MAX_TURNS]
                self._save()
            return len(unique)

    def unseen(self, conversation: str, agent: str) -> List[Dict[str, Any]]:
        """这个代理还没见过的轮次，按时间从早到晚（不改动记录；转告成功后调用 mark）。"""
        with self._lock:
            return [dict(turn) for turn in self._entry(conversation)["turns"]
                    if agent not in turn["seen"]]

    def mark(self, conversation: str, agent: str, turn_ids: List[str]) -> None:
        with self._lock:
            wanted = set(turn_ids)
            changed = False
            for turn in self._entry(conversation)["turns"]:
                if turn["id"] in wanted and agent not in turn["seen"]:
                    turn["seen"] = sorted(turn["seen"] + [agent])
                    changed = True
            if changed:
                self._save()

    def turns(self, conversation: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(turn) for turn in self._entry(conversation)["turns"]]

    def clear(self, conversation: str) -> bool:
        """对话重新开始：记录清掉，但仍然记得见过哪些轮次，手机再带回来也不会重新算进来。

        返回之前有没有记录。
        """
        with self._lock:
            entry = self._entry(conversation)
            if not entry["turns"]:
                return False
            entry["turns"] = []
            self._save()
            return True

    def _save(self) -> None:
        _write(self._path, self._data)
