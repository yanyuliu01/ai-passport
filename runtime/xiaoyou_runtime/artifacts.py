"""Verified task outputs: immutable snapshots served by ID, never by local path.

Automatic imports are limited to a task's artifact inbox. Explicit authenticated
registration may also use that agent's configured workspace, with credential and
Runtime state paths excluded. Source files are opened relative to directory file
descriptors without following symlinks and copied in bounded chunks.
"""

import hashlib
import json
import mimetypes
import os
import re
import stat
import struct
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Tuple
from urllib.parse import unquote

from .store import _write

MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_STORE_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_ARTIFACTS = 16
COPY_CHUNK = 64 * 1024
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024
MAX_IMAGE_PIXELS = 16 * 1024 * 1024
ID_RE = re.compile(r"a[0-9a-f]{32}\Z")
CARD_RE = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
_BLOCKED_PARTS = {"credentials", "secrets", "private", "auth", "tokens",
                  "node_modules", "__pycache__"}
_BLOCKED_NAMES = {"config.json", "settings.json", "auth.json", "credentials.json",
                  "id_rsa", "id_ed25519", "manifest.json"}


class ArtifactError(Exception):
    """Safe user-facing failure; errors never disclose a source path."""


def clean_attachments(value: Any) -> List[Dict[str, Any]]:
    """Keep only public, bounded metadata when restoring cards or the index."""
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for raw in value[:256]:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        if not ID_RE.fullmatch(raw["id"]) or raw["id"] in seen:
            continue
        seen.add(raw["id"])
        entry: Dict[str, Any] = {"id": raw["id"]}
        for key, default in (("name", "附件"), ("mime", "application/octet-stream"),
                             ("kind", "file"), ("sha256", ""), ("source", "runtime"),
                             ("status", "error")):
            entry[key] = raw[key][:255] if isinstance(raw.get(key), str) else default
        entry["kind"] = "image" if entry["kind"] == "image" else "file"
        entry["status"] = "ready" if entry["status"] == "ready" else "error"
        if not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", entry["mime"]):
            entry["mime"] = "application/octet-stream"
        entry["name"] = _safe_name(entry["name"])
        size = raw.get("size")
        entry["size"] = size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else 0
        at = raw.get("created_at")
        entry["created_at"] = at if isinstance(at, (int, float)) and not isinstance(at, bool) else 0
        for key in ("width", "height"):
            number = raw.get(key)
            entry[key] = number if isinstance(number, int) and not isinstance(number, bool) and number > 0 else None
        entry["thumbnail"] = raw.get("thumbnail") is True
        entry["error"] = raw["error"][:200] if isinstance(raw.get("error"), str) else None
        result.append(entry)
    return result


def _safe_name(value: Any, fallback: str = "附件") -> str:
    name = value if isinstance(value, str) else fallback
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ord(ch) >= 32 and ord(ch) != 127).strip()
    return name[:180] or fallback


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _image_header(header: bytes) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24 and header[12:16] == b"IHDR":
        width, height = struct.unpack(">II", header[16:24])
        return "image/png", width or None, height or None
    if header[:6] in (b"GIF87a", b"GIF89a") and len(header) >= 10:
        width, height = struct.unpack("<HH", header[6:10])
        return "image/gif", width or None, height or None
    if header.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 4 <= len(header):
            if header[offset] != 255:
                offset += 1
                continue
            marker = header[offset + 1]
            offset += 2
            if marker in (0, 0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            length = int.from_bytes(header[offset:offset + 2], "big")
            if length < 2 or offset + length > len(header):
                break
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF) and length >= 7:
                height, width = struct.unpack(">HH", header[offset + 3:offset + 7])
                return "image/jpeg", width or None, height or None
            offset += length
        return "image/jpeg", None, None
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP" and len(header) >= 30:
        kind = header[12:16]
        if kind == b"VP8X":
            return "image/webp", 1 + int.from_bytes(header[24:27], "little"), 1 + int.from_bytes(header[27:30], "little")
        if kind == b"VP8 " and header[23:26] == b"\x9d\x01\x2a":
            return "image/webp", int.from_bytes(header[26:28], "little") & 0x3FFF, int.from_bytes(header[28:30], "little") & 0x3FFF
        if kind == b"VP8L" and header[20] == 0x2F:
            bits = int.from_bytes(header[21:25], "little")
            return "image/webp", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        return "image/webp", None, None
    return None, None, None


class ArtifactStore:
    def __init__(self, config: Any):
        self._config = config
        self._root = Path(config.state_dir).resolve() / "artifacts"
        self._inbox = Path(config.state_dir).resolve() / "artifact-inbox"
        self._blobs = self._root / "blobs"
        self._thumbs = self._root / "thumbnails"
        self._index = self._root / "index.json"
        self._lock = threading.RLock()
        self._items: Dict[str, Dict[str, Any]] = {}
        for directory in (self._root, self._blobs, self._thumbs, self._inbox):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if directory.is_symlink() or directory.resolve() != directory:
                raise ArtifactError("产物存储目录不能经过符号链接")
        try:
            raw = json.loads(self._index.read_text("utf-8"))
        except (OSError, ValueError):
            raw = {}
        saved = raw.get("artifacts") if isinstance(raw, dict) else None
        if isinstance(saved, list):
            # The per-card display limit must not discard newer files from the
            # global download index after a restart.
            for offset in range(0, len(saved), 256):
                for item in clean_attachments(saved[offset:offset + 256]):
                    if re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                        self._items[item["id"]] = item

    def task_dir(self, card_id: str) -> Path:
        if not isinstance(card_id, str) or not CARD_RE.fullmatch(card_id):
            raise ArtifactError("任务编号不合法")
        directory = self._inbox / card_id
        directory.mkdir(exist_ok=True, mode=0o700)
        if directory.is_symlink() or directory.resolve() != directory:
            raise ArtifactError("任务产物目录不能是符号链接")
        return directory

    def _roots(self, card_id: str, agent: Optional[str], automatic: bool) -> List[Path]:
        roots = [self.task_dir(card_id)]
        if not automatic:
            spec = self._config.agent(agent) if agent else self._config.agent(self._config.default_agent)
            if spec is not None and spec.workdir is not None:
                roots.append(Path(spec.workdir).resolve())
        return roots

    def _open_source(self, value: Any, roots: List[Path], *, manifest: bool = False) -> BinaryIO:
        if not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value:
            raise ArtifactError("产物路径不合法")
        asked = Path(value)
        if ".." in asked.parts:
            raise ArtifactError("产物路径不能包含上级目录")
        candidate = asked if asked.is_absolute() else roots[0] / asked
        if not asked.is_absolute() and not candidate.exists() and len(roots) > 1:
            candidate = roots[1] / asked
        allowed = next((root for root in roots if _within(candidate, root)), None)
        if allowed is None and asked.is_absolute():
            # macOS exposes /private/var through /var. Canonicalize only the
            # trusted root prefix; retain child components for O_NOFOLLOW.
            for ancestor in candidate.parents:
                match = next((root for root in roots if ancestor.resolve() == root), None)
                if match is not None:
                    candidate = match / candidate.relative_to(ancestor)
                    allowed = match
                    break
        if allowed is None:
            raise ArtifactError("产物必须位于这件事的输出目录或代理工作区")
        relative = candidate.relative_to(allowed)
        parts = relative.parts
        if not parts:
            raise ArtifactError("产物必须是普通文件")
        names = [part.lower() for part in parts]
        if any(part.startswith(".") or part in _BLOCKED_PARTS for part in names):
            raise ArtifactError("不能公开隐藏文件、凭据或私密目录")
        filename = names[-1]
        if (filename in _BLOCKED_NAMES and not (manifest and filename == "manifest.json")) or filename.endswith((".pem", ".key", ".p12", ".pfx", ".token")) or re.search(r"(^|[._-])(secret|credential|password|token|private[-_]?key|auth)([._-]|$)", filename):
            raise ArtifactError("不能公开配置或凭据文件")
        state = Path(self._config.state_dir).resolve()
        if _within(candidate, state) and not _within(candidate, roots[0]):
            raise ArtifactError("不能公开 Runtime 状态文件")
        for spec in self._config.agents:
            if spec.config_dir is not None and _within(candidate, Path(spec.config_dir).resolve()):
                raise ArtifactError("不能公开代理配置目录")
            if any(candidate == Path(path).resolve() for path in spec.env_files.values()):
                raise ArtifactError("不能公开代理凭据文件")
        if os.name == "nt":
            from .windows_files import open_regular
            try:
                return open_regular(candidate, MAX_MANIFEST_BYTES if manifest else MAX_ARTIFACT_BYTES)
            except OSError:
                raise ArtifactError("产物不存在、不可读或经过重解析路径") from None
        # O_NOFOLLOW on each component closes the parent-directory symlink race.
        directory_fd = None
        try:
            directory_fd = os.open(str(allowed), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for part in parts[:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode):
                os.close(file_fd)
                raise ArtifactError("产物必须是普通文件")
            if info.st_size > (MAX_MANIFEST_BYTES if manifest else MAX_ARTIFACT_BYTES):
                os.close(file_fd)
                raise ArtifactError("产物清单或文件超过大小限制")
            return os.fdopen(file_fd, "rb")
        except OSError:
            raise ArtifactError("产物不存在、不可读或经过符号链接") from None
        finally:
            if directory_fd is not None:
                os.close(directory_fd)

    def _save_index(self) -> None:
        _write(self._index, {"artifacts": list(self._items.values())})
        self._index.chmod(0o600)

    def _thumbnail(self, path: Path, digest: str, meta: Dict[str, Any]) -> None:
        if meta["kind"] != "image":
            return
        width, height = meta["width"], meta["height"]
        if not width or not height or width * height > MAX_IMAGE_PIXELS:
            return
        try:
            from PIL import Image, ImageOps
            with Image.open(path) as opened:
                preview = ImageOps.exif_transpose(opened)
                preview.thumbnail((480, 480))
                preview.convert("RGBA").save(self._thumbs / (digest + ".png"), "PNG")
            meta["thumbnail"] = True
        except Exception:
            # A small original is safe as a preview when Pillow is unavailable.
            meta["thumbnail"] = meta["size"] <= MAX_THUMBNAIL_BYTES and width * height <= 1024 * 1024

    def _snapshot(self, card_id: str, stream: BinaryIO, name: str, source: str) -> Dict[str, Any]:
        with self._lock:
            stored_bytes = sum(p.stat().st_size for p in self._blobs.iterdir() if p.is_file())
            if stored_bytes >= MAX_STORE_BYTES:
                raise ArtifactError("产物存储已满，请清理历史产物后重试")
            fd, temporary = tempfile.mkstemp(prefix="copy-", dir=self._root)
            size = 0
            digest = hashlib.sha256()
            header = bytearray()
            try:
                with os.fdopen(fd, "wb") as output:
                    while True:
                        chunk = stream.read(COPY_CHUNK)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_ARTIFACT_BYTES:
                            raise ArtifactError("单个产物不能超过 64 MB")
                        digest.update(chunk)
                        if len(header) < 128 * 1024:
                            header.extend(chunk[:128 * 1024 - len(header)])
                        output.write(chunk)
                sha = digest.hexdigest()
                identity = hashlib.sha256((card_id + "\0" + name + "\0" + sha).encode()).hexdigest()
                artifact_id = "a" + identity[:32]
                if artifact_id in self._items:
                    return dict(self._items[artifact_id])
                blob = self._blobs / sha
                if blob.exists():
                    os.unlink(temporary)
                else:
                    if stored_bytes + size > MAX_STORE_BYTES:
                        raise ArtifactError("产物存储已满，请清理历史产物后重试")
                    os.replace(temporary, blob)
                image_mime, width, height = _image_header(bytes(header))
                mime = image_mime or mimetypes.guess_type(name)[0] or "application/octet-stream"
                if mime.startswith("image/") and not image_mime:
                    mime = "application/octet-stream"
                meta = {"id": artifact_id, "name": name, "mime": mime, "size": size,
                        "kind": "image" if image_mime else "file", "width": width, "height": height,
                        "sha256": sha, "source": source[:80], "created_at": time.time(),
                        "status": "ready", "thumbnail": False, "error": None}
                self._thumbnail(blob, sha, meta)
                self._items[artifact_id] = meta
                self._save_index()
                return dict(meta)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def register(self, card_id: str, manifest: Any, *, agent: Optional[str] = None,
                 automatic: bool = False) -> List[Dict[str, Any]]:
        if not isinstance(manifest, list) or len(manifest) > MAX_ARTIFACTS:
            raise ArtifactError("artifacts 应该是至多 16 项的产物清单")
        for item in manifest:
            if not isinstance(item, str) and (not isinstance(item, dict) or not isinstance(item.get("path"), str)):
                raise ArtifactError("每项产物都需要 path")
        roots = self._roots(card_id, agent, automatic)
        result = []
        for item in manifest:
            if isinstance(item, str):
                item = {"path": item}
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ArtifactError("每项产物都需要 path")
            name = _safe_name(item.get("name"), _safe_name(item["path"]))
            try:
                with self._open_source(item["path"], roots) as opened:
                    meta = self._snapshot(card_id, opened, name, agent or "runtime")
            except (ArtifactError, OSError) as error:
                failure = str(error) if isinstance(error, ArtifactError) else "产物保存失败"
                identity = hashlib.sha256((card_id + "\0" + item["path"] + "\0" + failure).encode()).hexdigest()
                meta = {"id": "a" + identity[:32], "name": name, "mime": "application/octet-stream",
                        "size": 0, "kind": "file", "width": None, "height": None, "sha256": "",
                        "source": agent or "runtime", "created_at": time.time(), "status": "error",
                        "thumbnail": False, "error": failure}
            result.append(meta)
        return result

    def register_for_card(self, cards: Any, card_id: str, manifest: Any,
                          entry_index: Optional[int] = None, agent: Optional[str] = None) -> Dict[str, Any]:
        card = cards.get(card_id)
        if card is None:
            raise ArtifactError("没有这件事")
        # Validate the binding before copying any files or updating the index.
        if entry_index is not None and (isinstance(entry_index, bool) or not isinstance(entry_index, int)
                                       or not 0 <= entry_index < len(card["entries"])):
            raise ArtifactError("entry_index 应该是现有消息的序号")
        attachments = self.register(card_id, manifest, agent=agent or card.get("agent"))
        from .record import Record  # 在这里引入：cards 要用这个模块，避免循环引入
        return Record(cards).attach(card_id, attachments, entry_index)

    def register_task(self, cards: Any, card_id: str, text: str = "",
                      fields: Optional[Dict[str, Any]] = None, agent: Optional[str] = None) -> List[Dict[str, Any]]:
        """Read an explicit manifest from only this task's inbox, before text cleanup."""
        if cards.get(card_id) is None:
            return []
        manifest = []
        try:
            roots = self._roots(card_id, agent, True)
        except (ArtifactError, OSError):
            return self._task_error(card_id, agent, "任务产物目录不可用")
        path = roots[0] / "manifest.json"
        if path.exists():
            try:
                with self._open_source(str(path), roots, manifest=True) as opened:
                    raw = opened.read(MAX_MANIFEST_BYTES + 1)
                if len(raw) > MAX_MANIFEST_BYTES:
                    raise ArtifactError("产物清单不能超过 64 KB")
                parsed = json.loads(raw.decode("utf-8"))
                manifest = parsed.get("artifacts", []) if isinstance(parsed, dict) else parsed
            except (ArtifactError, ValueError, OSError):
                # An invalid manifest must be visible, but does not fail a completed task.
                return self._task_error(card_id, agent, "产物清单无效或不可读")
        elif isinstance(fields, dict) and isinstance(fields.get("artifacts"), list):
            manifest = fields["artifacts"]
        else:
            for match in re.finditer(r"!?\[[^\]\n]*\]\(([^\)\n]+)\)", text):
                value = unquote(match.group(1).strip().strip("<>"))
                if "://" not in value and (not Path(value).is_absolute() or _within(Path(value), roots[0])):
                    manifest.append({"path": value})
                    if len(manifest) >= MAX_ARTIFACTS:
                        break
        try:
            return self.register(card_id, manifest, agent=agent, automatic=True)
        except (ArtifactError, OSError):
            return self._task_error(card_id, agent, "产物清单无效或保存失败")

    @staticmethod
    def _task_error(card_id: str, agent: Optional[str], error: str) -> List[Dict[str, Any]]:
        return [{"id": "a" + hashlib.sha256((card_id + ":manifest").encode()).hexdigest()[:32],
                 "name": "产物清单", "mime": "application/json", "kind": "file", "size": 0,
                 "sha256": "", "width": None, "height": None, "source": agent or "runtime",
                 "created_at": time.time(), "status": "error", "thumbnail": False, "error": error}]

    def save_text(self, card_id: str, text: str, name: str = "完整回复.txt", source: str = "runtime") -> Dict[str, Any]:
        """Persist full replies that exceed the card's display limit."""
        import io
        try:
            return self._snapshot(card_id, io.BytesIO(text.encode("utf-8")), _safe_name(name), source)
        except OSError:
            raise ArtifactError("完整回复保存失败") from None

    def open_content(self, artifact_id: str, thumbnail: bool = False) -> Tuple[BinaryIO, Dict[str, Any], str, int]:
        if not isinstance(artifact_id, str) or not ID_RE.fullmatch(artifact_id):
            raise ArtifactError("附件编号不合法")
        with self._lock:
            meta = self._items.get(artifact_id)
            if meta is None or meta["status"] != "ready":
                raise ArtifactError("附件不存在或尚未就绪")
            mime = meta["mime"]
            path = self._blobs / meta["sha256"]
            if thumbnail:
                if not meta["thumbnail"]:
                    raise ArtifactError("这个附件没有缩略图，请查看原图")
                preview = self._thumbs / (meta["sha256"] + ".png")
                if preview.exists():
                    path, mime = preview, "image/png"
                elif meta["size"] > MAX_THUMBNAIL_BYTES:
                    raise ArtifactError("缩略图文件不可用，请查看原图")
            try:
                if os.name == "nt":
                    from .windows_files import open_regular
                    stream = open_regular(path, MAX_ARTIFACT_BYTES)
                    return stream, dict(meta), mime, os.fstat(stream.fileno()).st_size
                fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    os.close(fd)
                    raise ArtifactError("附件快照不可读")
                return os.fdopen(fd, "rb"), dict(meta), mime, info.st_size
            except OSError:
                raise ArtifactError("附件快照不可读") from None
