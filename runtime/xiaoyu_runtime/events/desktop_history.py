"""电脑上的 Claude Code、Codex 会话的完整可见记录：导出成文件，经小鱼网络给别的电脑（见 mesh_share.py）。

手机上的卡只放最近 20 条（desktop_sync.py），够看进展；换一台电脑接着干活时要的是前因后果，
所以这里另外导出一份：每个会话一个 Markdown 文件，最多 MAX_ENTRIES 条、MAX_BYTES 字节，
只含主人说的和回答的正文（desktop_content.visible_text 已经去掉工具输出、思考过程和疑似密钥）。
Runtime 自己起的帮手的会话不导出：它们已经在卡上了。
"""

import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from .desktop_sync import Cursor, DesktopSync

MAX_ENTRIES = 500
MAX_BYTES = 512 * 1024
READ_BYTES = 16 * 1024 * 1024
MAX_SESSIONS = 32
RECENT_SECONDS = 7 * 86400


class _NoCards:
    def recent(self, *_: Any) -> List[Any]:
        return []


def _signature(path: Path) -> Tuple[int, int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size


class ExportCache:
    """Process-local optimization; missing or changed files are always re-read."""
    def __init__(self) -> None:
        self.files: Dict[Path, Tuple[tuple, Path, tuple, str]] = {}


def _when(at: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(at)) if at else ""


def render(agent: str, session: Any, title: str, project: str) -> str:
    lines = ["# %s" % title, "",
             "- 来源：%s 会话 `%s`" % (agent, session.session_id),
             "- 项目：%s" % project]
    if session.content_truncated:
        lines.append("- 只含最近的部分（更早的太长没导出）")
    lines.append("")
    for entry in session.entries:
        who = "主人" if entry["role"] == "you" else agent
        lines += ["### %s · %s" % (who, _when(entry["at"])), "", entry["text"].strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


def export(state_dir: Path, out_dir: Path, *, codex_dir: Optional[Path] = None,
           claude_dir: Optional[Path] = None, projects: Iterable[Path] = (),
           owned: Callable[[], Set[str]] = lambda: set(), now: Optional[float] = None,
           cache: Optional[ExportCache] = None) -> List[Path]:
    """把最近的会话写进 out_dir（内容没变的不重写）；返回写了的文件。"""
    now = time.time() if now is None else now
    observer = DesktopSync(_NoCards(), state_dir, codex_dir=codex_dir, claude_dir=claude_dir,
                           projects=list(projects), content_entries=MAX_ENTRIES, content_bytes=MAX_BYTES,
                           read_bytes=READ_BYTES, clock=lambda: now)
    mine = owned()
    found = []
    for agent, folder in observer.roots:
        try:
            paths = [path for path in folder.rglob("rollout-*.jsonl" if agent == "codex" else "*.jsonl")
                     if "subagents" not in path.parts]
            found += [(path.stat().st_mtime, agent, path) for path in paths]
        except OSError:
            continue
    found = [item for item in found if now - item[0] < RECENT_SECONDS]
    found.sort(key=lambda item: item[0], reverse=True)
    written = []
    out_dir.mkdir(parents=True, exist_ok=True)
    if cache is not None:
        selected = {path for _, _, path in found[:MAX_SESSIONS]}
        cache.files = {path: value for path, value in cache.files.items() if path in selected}
    for _, agent, path in found[:MAX_SESSIONS]:
        try:
            signature = _signature(path)
        except OSError:
            continue
        cached = cache.files.get(path) if cache is not None else None
        if cached and cached[0] == signature and cached[1].parent == out_dir and cached[3] not in mine:
            try:
                if _signature(cached[1]) == cached[2]:
                    continue
            except OSError:
                pass  # Deleted export must be reconstructed.
        cursor = Cursor(path, agent)
        try:
            observer._read(cursor)
        except (OSError, ValueError):
            continue
        session = cursor.session
        if session is None or session.skip or session.session_id in mine or not session.entries \
                or not session.cwd:
            continue
        if observer.projects and str(Path(session.cwd).expanduser().resolve()) not in observer.projects:
            continue
        project = Path(session.cwd).name or "project"
        title = observer._title(agent, session.session_id, project, session.entries).title
        text = render(agent, session, title, project)
        target = out_dir / ("%s-%s.md" % (agent, session.session_id))
        try:
            if not target.is_file() or target.read_text(encoding="utf-8") != text:
                temporary = target.with_suffix(".tmp")
                temporary.write_text(text, encoding="utf-8")
                temporary.replace(target)
                written.append(target)
            if cache is not None and _signature(path) == signature:
                cache.files[path] = (signature, target, _signature(target), session.session_id)
        except OSError:
            continue
    return written
