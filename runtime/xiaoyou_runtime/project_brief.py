#!/usr/bin/env python3
"""项目简报：这台电脑上某个项目最近的改动，以及 Claude Code、Codex（不管是 CLI 还是 App）
里聊过的内容。

给小幽的 command 类型帮手用：标准输入里的那段话不解析，只是触发；标准输出打印一段
文字，小幽会把这段话转述给主人。独立于 xiaoyou_runtime 包之外也能跑（只用标准库），
所以配置里可以直接用 ["python3", "这个文件的路径"] 当作 command。

只读这台电脑本地留着的东西：
  git log / git status                           这个仓库本身的改动
  ~/.claude/projects/<转义后的仓库路径>/*.jsonl    Claude Code 的会话（CLI 和 App 共用）
  ~/.codex/sessions/**/rollout-*.jsonl            Codex 的会话（CLI 和 App 共用，按 cwd 过滤）

别的电脑上跑的、或者 claude.ai 普通 Chat 标签页（不带 Code 能力）里聊过的内容，这台
电脑上没有缓存，查不到。
"""

import json
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

# 每个来源最多摘这么多个会话，每个会话摘最后这么多轮。
MAX_SESSIONS = 4
MAX_TURNS = 3
# 一条消息摘这么多字；超过 14 天没动过的会话不算“最近”。
MAX_TEXT_CHARS = 280
RECENT_DAYS = 14
GIT_TIMEOUT_SECONDS = 10


def _run_git(repo: Path, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              text=True, timeout=GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def git_brief(repo: Path) -> str:
    """这个仓库最近的改动：最近几条提交，和工作区现在的状态。不是仓库就是空字符串。"""
    parts = []
    log = _run_git(repo, "log", "-8", "--date=short", "--pretty=%h %ad %s")
    if log:
        parts.append("最近的提交：\n" + log)
    status = _run_git(repo, "status", "--short", "--branch")
    if status:
        parts.append("工作区状态：\n" + status)
    return "\n\n".join(parts)


# 一条消息里算“文字”的 block 类型：text 是 Claude Code 用的；input_text/output_text
# 是 Codex 用的（用户说的是 input_text，模型答的是 output_text）。
TEXT_BLOCK_TYPES = ("text", "input_text", "output_text")

# 工具自己注入的背景信息，不是主人或模型真的说的话：开头是这些就跳过。
INJECTED_PREFIXES = (
    "<system-reminder", "<environment_context", "<in-app-browser-context",
    "<skills_instructions", "<app-context", "<user_instructions",
    "# agents.md instructions for",
)


def _flatten_text(content: Any) -> str:
    """一条消息的 content（字符串，或者一串 block）→ 读得出来的文字。"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [block.get("text", "") for block in content
                 if isinstance(block, dict) and block.get("type") in TEXT_BLOCK_TYPES]
        return "\n".join(part.strip() for part in parts if part.strip())
    return ""


def _is_injected(text: str) -> bool:
    """这段文字是不是工具自己塞进去的背景信息（AGENTS.md、环境说明……），不是真人说的话。"""
    head = text.lstrip().lower()
    return head.startswith(INJECTED_PREFIXES)


def _clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _claude_project_dir(repo: Path, home: Path) -> Path:
    """Claude Code 按仓库绝对路径转义出来的会话目录（把 / 换成 -）。"""
    return home / ".claude" / "projects" / str(repo).replace("/", "-")


def _claude_turns(path: Path, limit: int) -> List[Dict[str, str]]:
    """一个 Claude Code 会话文件 → 最近几轮真人说的话（谁、说了什么）。

    跳过工具结果（带 toolUseResult 的 user 行）和系统注入的提醒（isMeta），
    那些不是主人或模型自己说的话。
    """
    recent: Deque[Dict[str, str]] = deque(maxlen=limit)
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return []
    with handle:
        for line in handle:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("isMeta"):
                continue
            kind = event.get("type")
            if kind == "user" and "toolUseResult" in event:
                continue  # 这是工具结果，不是主人说的话
            if kind not in ("user", "assistant"):
                continue
            message = event.get("message")
            text = _flatten_text(message.get("content")) if isinstance(message, dict) else ""
            if text and not _is_injected(text):
                recent.append({"role": kind, "text": text})
    return list(recent)


def claude_sessions(repo: Path, now: float, home: Optional[Path] = None) -> List[Dict[str, Any]]:
    """这个仓库最近的 Claude Code 会话（CLI、桌面应用、claude.ai 的 Code 标签页都算，
    只要是在这台电脑上跑的）。查不到目录就是空列表。"""
    folder = _claude_project_dir(repo, home or Path.home())
    if not folder.is_dir():
        return []
    cutoff = now - RECENT_DAYS * 86400
    files = [path for path in folder.glob("*.jsonl") if path.stat().st_mtime >= cutoff]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    sessions = []
    for path in files[:MAX_SESSIONS]:
        turns = _claude_turns(path, MAX_TURNS)
        if turns:
            sessions.append({"when": path.stat().st_mtime, "turns": turns})
    return sessions


# 接话用的，比简报摘得更多：这是要接着往下干活的背景，不是说给人听的一句话。
RECAP_TURNS = 14
RECAP_TEXT_CHARS = 700


def latest_claude_session(repo: Path, now: Optional[float] = None, home: Optional[Path] = None,
                          exclude: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """这个仓库在（通常是主人平时用的）某套 Claude 配置下最近的一个会话。

    跳过 exclude 这个会话编号（用来在自己隔离的配置目录里接话时，不接自己刚留下的那个）。
    没有任何会话就是 None。"""
    folder = _claude_project_dir(repo, home or Path.home())
    if not folder.is_dir():
        return None
    now = time.time() if now is None else now
    cutoff = now - RECENT_DAYS * 86400
    files = [path for path in folder.glob("*.jsonl") if path.stat().st_mtime >= cutoff]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files:
        if exclude is not None and path.stem == exclude:
            continue
        turns = _claude_turns(path, RECAP_TURNS)
        if turns:
            return {"session_id": path.stem, "when": path.stat().st_mtime, "turns": turns}
    return None


def recap_text(session: Dict[str, Any], now: Optional[float] = None) -> str:
    """接话用的一段话：主人最近在别的地方（通常是 App）聊过的内容，接着往下说。"""
    now = time.time() if now is None else now
    lines = [
        "（主人最近（%s）在这个项目上聊过下面这些，当时用的不是这套会话——"
        "接着它的话往下做，不要重新打招呼或者复述这些内容）" % _when(session["when"], now)
    ]
    for turn in session["turns"]:
        who = "主人" if turn["role"] == "user" else "回答"
        lines.append("%s：%s" % (who, _clip(turn["text"], RECAP_TEXT_CHARS)))
    return "\n".join(lines)


def _codex_session(path: Path, repo: str, limit: int) -> Tuple[Optional[str], List[Dict[str, str]]]:
    """一个 Codex 会话文件 →（它是哪个目录下跑的，最近几轮真人说的话）。"""
    cwd: Optional[str] = None
    recent: Deque[Dict[str, str]] = deque(maxlen=limit)
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return None, []
    with handle:
        for line in handle:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            if event.get("type") == "session_meta" and cwd is None:
                cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
                if cwd is not None and cwd != repo:
                    return cwd, []  # 不是这个仓库的会话，不用接着读
            elif event.get("type") == "response_item" and payload.get("type") == "message":
                role = payload.get("role")
                if role in ("user", "assistant"):
                    text = _flatten_text(payload.get("content"))
                    if text and not _is_injected(text):
                        recent.append({"role": role, "text": text})
    return cwd, list(recent)


def codex_sessions(repo: Path, now: float, home: Optional[Path] = None) -> List[Dict[str, Any]]:
    """这个仓库最近的 Codex 会话（CLI、桌面/IDE 应用都算，只要是在这台电脑上跑的）。"""
    root = (home or Path.home()) / ".codex" / "sessions"
    if not root.is_dir():
        return []
    cutoff = now - RECENT_DAYS * 86400
    target = str(repo)
    candidates = [path for path in root.glob("**/rollout-*.jsonl") if path.stat().st_mtime >= cutoff]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    sessions = []
    for path in candidates:
        cwd, turns = _codex_session(path, target, MAX_TURNS)
        if cwd == target and turns:
            sessions.append({"when": path.stat().st_mtime, "turns": turns})
        if len(sessions) >= MAX_SESSIONS:
            break
    return sessions


def _format_sessions(label: str, sessions: List[Dict[str, Any]], now: float) -> str:
    if not sessions:
        return ""
    lines = [label + "："]
    for session in sessions:
        lines.append("- %s：" % _when(session["when"], now))
        for turn in session["turns"]:
            who = "主人" if turn["role"] == "user" else "回答"
            lines.append("  %s：%s" % (who, _clip(turn["text"])))
    return "\n".join(lines)


def _when(at: float, now: float) -> str:
    then = time.localtime(at)
    if time.localtime(now).tm_yday == then.tm_yday:
        return time.strftime("今天 %H:%M", then)
    return time.strftime("%m-%d %H:%M", then)


def brief(repo: Path, now: Optional[float] = None, home: Optional[Path] = None) -> str:
    """拼起来的简报：这个仓库的改动，加上 Claude Code、Codex 里聊过的内容。"""
    now = time.time() if now is None else now
    parts = [
        git_brief(repo),
        _format_sessions("Claude Code 里聊过的（CLI、App 都算）", claude_sessions(repo, now, home), now),
        _format_sessions("Codex 里聊过的（CLI、App 都算）", codex_sessions(repo, now, home), now),
    ]
    text = "\n\n".join(part for part in parts if part)
    return text or "这台电脑上还没有这个项目的改动或会话记录。"


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    sys.stdin.read()  # 这段话的内容不解析，只是触发；读掉免得调用方等着写管道
    repo = Path(argv[0]).expanduser().resolve() if argv else Path.cwd()
    print(brief(repo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
