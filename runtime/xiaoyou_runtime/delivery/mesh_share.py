"""小幽网络里共享的设置、文件和电脑会话记录（见 mesh.py 和 Runtime README 的“小幽网络”）。

三样东西，都作为记录随小幽网络同步，不另起通道：

  环境变量  配置里 mesh.share.env 点名的（比如 DEEPSEEK_API_KEY）。值放在
            ~/.config/xiaoyou/shared/<名字>，只有本人可读；代理用 env_files 指向它。没点名的一个都不传。
  文件夹    配置里 mesh.share.folders 给每个名字配一个本机路径（同一个名字在每台电脑上可以
            是不同的路径，比如 Claude Code 的记忆目录）。文件按名字 + 相对路径对上。
  会话记录  电脑上 Claude Code、Codex 会话的完整可见记录（desktop_history.py 导出），别的电脑
            按来源各存一份只读副本，放在 state/mesh/history/<那台>/ 下，Claude Code、Codex 开会话
            时由 shared_history.py 告诉它们在哪。

环境变量和文件夹以最后写的为准（时间相同按 Runtime 编号）；删掉文件也会同步（墓碑）。文件夹的
根目录不在时不同步，免得把别处的文件当成被删了。只传文字文件，单个不超过 MAX_FILE_BYTES。
"""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from ..events import desktop_history

MAX_FILE_BYTES = 512 * 1024
MAX_FOLDER_FILES = 300
HISTORY_SECONDS = 60
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def shared_dir() -> Path:
    return Path(os.environ.get("XIAOYOU_SHARED_DIR") or Path.home() / ".config" / "xiaoyou" / "shared")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe(relative: str) -> Optional[PurePosixPath]:
    """别人给的相对路径：不能是绝对路径、不能往上跳、不能带盘符。"""
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or any(part in ("", ".", "..") for part in path.parts) \
            or ":" in relative or "\\" in relative:
        return None
    return path


def _write(path: Path, text: str, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    if private and os.name == "posix":
        os.chmod(temporary, 0o600)
    temporary.replace(path)


class Shares:
    def __init__(self, mesh: Any, config: Any):
        self._mesh, self._config = mesh, config
        self.env = [name for name in config.mesh_share_env if ENV_NAME.match(name)]
        self.folders: Dict[str, Path] = dict(config.mesh_share_folders)
        self.shared = shared_dir()
        self.history = Path(config.state_dir) / "history"
        self.mirror = Path(config.state_dir) / "mesh" / "history"
        self._state = Path(config.state_dir) / "mesh" / "shares.json"
        # 键 → [时间, 产地, 内容指纹, 删了没有]：每个键现在以谁的哪一版为准。
        self._latest: Dict[str, List[Any]] = {}
        self._history_at = 0.0
        try:
            raw = json.loads(self._state.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._latest = {key: value for key, value in raw.items()
                                if isinstance(key, str) and isinstance(value, list) and len(value) == 4}
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        try:
            _write(self._state, json.dumps(self._latest))
        except OSError as error:
            sys.stderr.write("共享设置的记录写不下去：%s\n" % error)

    # ---- 这台的：看看有什么变了，变了的报出去 ----

    def scan(self, now: Optional[float] = None) -> int:
        """返回这次报出去的条数。由 Mesh 每半分钟调一次。"""
        now = time.time() if now is None else now
        items: List[Tuple[str, Dict[str, Any]]] = []
        for name in self.env:
            path = self.shared / name
            if not path.is_file() and os.environ.get(name):
                # 第一次：这台的环境里有，就当成这台的值放进共享目录。
                try:
                    _write(path, os.environ[name], private=True)
                except OSError:
                    continue
            text = self._read(path)
            if text is not None:
                items.append(("env:" + name, {"kind": "env", "name": name, "text": text.strip()}))
        for name, root in self.folders.items():
            if not NAME.match(name) or not root.is_dir():
                continue
            present = set()
            for path in sorted(root.rglob("*"))[:MAX_FOLDER_FILES * 4]:
                if not path.is_file() or path.name.endswith(".tmp") or len(present) >= MAX_FOLDER_FILES:
                    continue
                relative = path.relative_to(root).as_posix()
                text = self._read(path)
                if text is None or _safe(relative) is None:
                    continue
                key = "folder:%s/%s" % (name, relative)
                present.add(key)
                items.append((key, {"kind": "folder", "name": name, "path": relative, "text": text}))
            for key, latest in list(self._latest.items()):
                if key.startswith("folder:%s/" % name) and key not in present and not latest[3]:
                    items.append((key, {"kind": "folder", "name": name, "path": key.split("/", 1)[1],
                                        "deleted": True}))
        if self._config.desktop_sync and now - self._history_at >= HISTORY_SECONDS:
            self._history_at = now
            try:
                desktop_history.export(self._config.state_dir, self.history,
                                       codex_dir=self._config.desktop_codex_dir,
                                       claude_dir=self._config.desktop_claude_dir,
                                       projects=self._config.desktop_projects,
                                       owned=self._mesh.owned_sessions, now=now)
            except Exception as error:
                sys.stderr.write("会话记录导出出错：%s: %s\n" % (type(error).__name__, error))
        if self.history.is_dir():
            for path in sorted(self.history.glob("*.md")):
                text = self._read(path)
                if text is not None:
                    items.append(("history:" + path.name, {"kind": "history", "path": path.name, "text": text}))
        for item in self._mesh._xiaoyou.cards.conversations.list(include_deleted=True):
            if item.get("kind") == "side" and item.get("origin") == self._mesh.runtime_id:
                items.append(("conversation:" + item["id"], {"kind": "conversation", "text": json.dumps(item, ensure_ascii=False)}))
        for key, item in self._mesh._xiaoyou.cards.directory.snapshot().items():
            if item.get('origin') == self._mesh.runtime_id:
                items.append(('directory:' + key, {'kind': 'directory', 'text': json.dumps(item, ensure_ascii=False)}))
        for key, item in self._mesh._xiaoyou.cards.directory.source_snapshot().items():
            if item.get('authority') == self._mesh.runtime_id:
                items.append(('directory-source:' + key, {'kind': 'directory_source', 'text': json.dumps(item, ensure_ascii=False)}))
        for key, item in self._mesh._xiaoyou.cards.directory.generated_snapshot().items():
            if item.get('origin') == self._mesh.runtime_id:
                items.append(('directory-generated:' + key, {'kind':'directory_generated', 'text':json.dumps({'uid':key,'metadata':item},ensure_ascii=False)}))
        sent = 0
        for key, data in items:
            sha = "" if data.get("deleted") else _sha(data["text"])
            latest = self._latest.get(key)
            if latest is not None and latest[2] == sha and bool(latest[3]) == bool(data.get("deleted")):
                continue  # 和现在为准的那一版一样（可能就是刚从别处收到的）：不重复报
            data.update(key=key, sha=sha, at=now)
            self._latest[key] = [now, self._mesh.runtime_id, sha, bool(data.get("deleted"))]
            self._mesh.publish_share(key, data)
            sent += 1
        if sent:
            self._save()
        return sent

    @staticmethod
    def _read(path: Path) -> Optional[str]:
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                return None
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None

    # ---- 别处的：比这里的新就照着改 ----

    def apply(self, origin: str, origin_name: str, data: Dict[str, Any]) -> None:
        key, kind = data.get("key"), data.get("kind")
        at = data.get("at")
        if not isinstance(key, str) or not isinstance(at, (int, float)) or kind not in ("env", "folder", "history", "conversation", "directory", "directory_source", "directory_generated"):
            return
        deleted = data.get("deleted") is True
        text = data.get("text") if isinstance(data.get("text"), str) else ""
        if kind == "conversation":
            try:
                item = json.loads(text)
                if isinstance(item, dict):
                    self._mesh._xiaoyou.intake.writer("mesh", actor="mesh:" + origin, cause=key).conversation_replica(origin, item)
            except (ValueError, OSError):
                pass
            return
        if kind == 'directory':
            try:
                self._mesh._xiaoyou.intake.writer("mesh", actor="mesh:" + origin, cause=key).directory_replica(origin, json.loads(text))
            except (ValueError, OSError):
                pass
            return
        if kind == 'directory_generated':
            try:
                item = json.loads(text)
                self._mesh._xiaoyou.intake.writer("mesh", actor="mesh:" + origin, cause=key).directory_generated_replica(origin, item.get('uid'), item.get('metadata'))
            except (ValueError, OSError, TypeError, AttributeError):
                pass
            return
        if kind == 'directory_source':
            try:
                self._mesh._xiaoyou.intake.writer("mesh", actor="mesh:" + origin, cause=key).directory_sources([json.loads(text)], origin)
            except (ValueError, OSError, TypeError, KeyError):
                pass
            return
        if kind == "history":
            # 别的电脑的会话记录：按那台各存一份，不和这台的混。
            relative = _safe(str(data.get("path") or ""))
            if relative is None or len(relative.parts) != 1:
                return
            label = re.sub(r"[^A-Za-z0-9_.-]+", "-", origin_name or "")[:32].strip("-") or "runtime"
            target = self.mirror / ("%s-%s" % (label, origin[:6])) / relative.name
            try:
                if deleted:
                    target.unlink(missing_ok=True)
                elif self._read(target) != text:
                    _write(target, text)
            except OSError as error:
                sys.stderr.write("别处的会话记录写不下去：%s\n" % error)
            return
        latest = self._latest.get(key)
        if latest is not None and (latest[0], latest[1]) >= (at, origin):
            return  # 这里的更新
        target: Optional[Path] = None
        if kind == "env":
            name = data.get("name")
            if not isinstance(name, str) or not ENV_NAME.match(name) or name not in self.env:
                return  # 这台没点名要这个变量
            target = self.shared / name
        else:
            name, relative = data.get("name"), _safe(str(data.get("path") or ""))
            root = self.folders.get(name) if isinstance(name, str) else None
            if root is None or relative is None:
                return  # 这台没配这个文件夹
            target = root.joinpath(*relative.parts)
        try:
            if deleted:
                if target.is_file():
                    target.unlink()
            else:
                _write(target, text, private=kind == "env")
        except OSError as error:
            sys.stderr.write("共享的 %s 写不下去：%s\n" % (key, error))
            return
        self._latest[key] = [at, origin, "" if deleted else _sha(text), deleted]
        self._save()
