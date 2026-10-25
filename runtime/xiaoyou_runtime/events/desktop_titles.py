"""Read-only adapters for the titles shown in desktop conversation sidebars.

A Codex transcript's initial request/``threads.title`` is not its desktop
``thread.name``. Only explicit name metadata is read here. Callers own title
sanitization and their common fallback policy; an unavailable upstream title
is represented by an empty string, never a generated summary.
"""

import json
import re
import sqlite3
from contextlib import closing
from itertools import islice
from pathlib import Path
from typing import Collection, Dict, Mapping, Set, Tuple

MAX_SESSION_IDS = 64
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_TOTAL_READ_BYTES = 8 * 1024 * 1024
MAX_READ_FILES = 256
MAX_LINE_BYTES = 64 * 1024
MAX_TITLE_CHARS = 16 * 1024
MAX_DATABASES = 8
MAX_PROJECTS = 256
MAX_CACHE_FILES = 128
MAX_CACHE_BYTES = 2 * 1024 * 1024
_DATABASE = re.compile(r"state_(\d+)\.sqlite\Z")


def _title(value: object) -> str:
    # Preserve upstream spelling and whitespace. The publishing layer scrubs
    # credentials and applies its own display bound to every agent.
    return value if isinstance(value, str) and len(value) <= MAX_TITLE_CHARS and value.strip() else ""


def _name_record(value: object) -> bool:
    return value is None or isinstance(value, str) and len(value) <= MAX_TITLE_CHARS


def _ids(values: Collection[str]) -> Set[str]:
    result = set()
    if isinstance(values, (str, bytes)):
        return result
    try:
        for value in islice(iter(values), MAX_SESSION_IDS):
            if (isinstance(value, str) and 0 < len(value) <= 128
                    and value not in {".", ".."}
                    and not any(char in value for char in ("/", "\\", "\0"))):
                result.add(value)
    except TypeError:
        pass
    return result


class _Budget:
    def __init__(self):
        self.bytes, self.files = MAX_TOTAL_READ_BYTES, MAX_READ_FILES


def _json_file(path: Path, budget: _Budget) -> object:
    if budget.bytes <= 0 or budget.files <= 0:
        return None
    try:
        with path.open("rb") as stream:
            budget.files -= 1
            limit = min(MAX_METADATA_BYTES + 1, budget.bytes)
            data = stream.read(limit)
            budget.bytes -= len(data)
        # A read that reaches the budget boundary may be an incomplete file.
        if len(data) >= limit or len(data) > MAX_METADATA_BYTES:
            return None
        return json.loads(data)
    except (OSError, ValueError, RecursionError):
        return None


def _records(path: Path, budget: _Budget):
    """Read a bounded JSONL tail, dropping partial/oversized records."""
    if budget.bytes <= 1 or budget.files <= 0:
        return
    try:
        with path.open("rb") as stream:
            budget.files -= 1
            stream.seek(0, 2)
            size = stream.tell()
            remaining = min(MAX_METADATA_BYTES, budget.bytes - 1)
            start = max(0, size - remaining)
            if start:
                stream.seek(start - 1)
                previous = stream.read(1)
                budget.bytes -= 1
                stream.seek(start)
                if previous != b"\n":
                    discarded = stream.readline(remaining)
                    remaining -= len(discarded)
                    budget.bytes -= len(discarded)
            else:
                stream.seek(0)
            dropping = False
            while remaining > 0:
                line = stream.readline(min(MAX_LINE_BYTES + 1, remaining))
                if not line:
                    break
                remaining -= len(line)
                budget.bytes -= len(line)
                complete = line.endswith(b"\n")
                if dropping:
                    dropping = not complete
                    continue
                if len(line) > MAX_LINE_BYTES:
                    dropping = not complete
                    continue
                try:
                    record = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


class DesktopTitles:
    """Refresh requested IDs without changing any agent-owned file.

    SQLite connections use ``mode=ro`` and close after each query. They do not
    use ``immutable=1``: live renames may be in WAL. Claude metadata is cached
    by file identity, timestamps and size; known session paths are read first.
    Every refresh shares an overall file/byte budget. Unknown agents have no
    upstream title adapter and use the caller's common fallback policy.
    """

    def __init__(self, codex_dir: Path, claude_dir: Path):
        self.codex_dir, self.claude_dir = Path(codex_dir), Path(claude_dir)
        self.titles: Dict[Tuple[str, str], str] = {}
        self._claude_paths: Dict[str, Path] = {}
        self._cache = {}

    def get(self, agent: str, session_id: str) -> str:
        return self.titles.get((agent, session_id), "")

    def refresh(self, session_ids: Mapping[str, Collection[str]]) -> None:
        titles, budget = {}, _Budget()
        for agent, reader in (("codex", self._codex), ("claude", self._claude)):
            requested = _ids(session_ids.get(agent, ()))
            if requested:
                titles.update({(agent, sid): title for sid, title in reader(requested, budget).items()
                               if title})
        self.titles = titles

    def _codex(self, requested: Set[str], budget: _Budget) -> Dict[str, str]:
        result, seen = {}, set()
        try:
            databases = []
            for path in islice(self.codex_dir.glob("state_*.sqlite"), MAX_DATABASES * 8):
                match = _DATABASE.fullmatch(path.name)
                if match:
                    databases.append((int(match.group(1)), path))
            databases.sort(key=lambda item: item[0], reverse=True)
        except OSError:
            databases = []
        for _, path in databases[:MAX_DATABASES]:
            missing = requested - seen
            if not missing:
                break
            try:
                with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                                            uri=True, timeout=0.1)) as database:
                    database.execute("PRAGMA query_only=ON")
                    columns = {row[1] for row in database.execute("PRAGMA table_info(threads)")}
                    if not {"id", "name"}.issubset(columns):
                        continue
                    placeholders = ",".join("?" for _ in missing)
                    # Reject malformed/huge values inside SQLite before loading
                    # them into Python. Never select prompts, paths or tokens.
                    rows = database.execute(
                        "SELECT id, CASE WHEN typeof(name)='text' AND length(name)<=? "
                        "THEN name ELSE NULL END FROM threads WHERE id IN (" + placeholders + ") LIMIT ?",
                        (MAX_TITLE_CHARS, *sorted(missing), MAX_SESSION_IDS),
                    ).fetchall()
                    for sid, name in rows:
                        seen.add(sid)
                        value = _title(name)
                        if value:
                            result[sid] = value
            except (OSError, sqlite3.Error, ValueError):
                continue
        # An existing authoritative row with an empty name is a removal, not
        # permission to resurrect an old name from another database/cache.
        missing = requested - seen
        if not missing:
            return result
        indexed = {}
        for record in _records(self.codex_dir / "session_index.jsonl", budget):
            sid = record.get("id")
            if (isinstance(sid, str) and sid in missing and "thread_name" in record
                    and _name_record(record["thread_name"])):
                indexed[sid] = _title(record["thread_name"])
        result.update(indexed)
        missing -= indexed.keys()
        if not missing:
            return result
        # Older desktop builds persisted this specific cache. Do not recurse
        # through arbitrary ``title`` keys, which can describe tools/models.
        state = _json_file(self.codex_dir / ".codex-global-state.json", budget)
        if isinstance(state, dict):
            cache = state.get("thread-titles")
            names = cache.get("titles") if isinstance(cache, dict) else None
            if isinstance(names, dict):
                for sid in missing:
                    name = _title(names.get(sid))
                    if name:
                        result[sid] = name
        return result

    def _cached_claude(self, path: Path, requested: Set[str], budget: _Budget,
                       transcript: bool = False):
        try:
            stat = path.stat()
        except OSError:
            self._cache.pop(path, None)
            return False, {}
        signature = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
        cached = self._cache.get(path)
        if cached and cached[0] == signature and requested.issubset(cached[1]):
            return True, {sid: title for sid, title in cached[2].items() if sid in requested}
        # Do not cache an incomplete read as if an upstream rename disappeared.
        if budget.files <= 0 or budget.bytes <= min(stat.st_size, MAX_METADATA_BYTES) + 1:
            return True, {}
        result = {}
        if transcript:
            for record in _records(path, budget):
                sid = record.get("sessionId")
                if (record.get("type") == "custom-title" and isinstance(sid, str)
                        and sid in requested and "customTitle" in record
                        and _name_record(record["customTitle"])):
                    result[sid] = _title(record["customTitle"])
        else:
            index = _json_file(path, budget)
            entries = index.get("entries") if isinstance(index, dict) else None
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    sid = entry.get("sessionId")
                    if isinstance(sid, str) and sid in requested:
                        name = _title(entry.get("customTitle")) or _title(entry.get("summary"))
                        if name:
                            result[sid] = name
        self._cache.pop(path, None)
        self._cache[path] = (signature, frozenset(requested), result)
        # Bound cache memory as well as its entry count; titles remain raw.
        while self._cache and (len(self._cache) > MAX_CACHE_FILES or sum(
                sum(len(title.encode("utf-8")) for title in value[2].values())
                for value in self._cache.values()) > MAX_CACHE_BYTES):
            self._cache.pop(next(iter(self._cache)))
        return True, result

    def _claude(self, requested: Set[str], budget: _Budget) -> Dict[str, str]:
        result, explicit, known_projects = {}, {}, set()
        self._claude_paths = {sid: path for sid, path in self._claude_paths.items() if sid in requested}
        for sid, path in list(self._claude_paths.items()):
            exists, names = self._cached_claude(path, {sid}, budget, transcript=True)
            if exists:
                known_projects.add(path.parent)
                explicit.update(names)
            else:
                self._claude_paths.pop(sid, None)
        missing = requested - self._claude_paths.keys()
        try:
            projects = (sorted(islice((self.claude_dir / "projects").iterdir(), MAX_PROJECTS),
                               key=lambda path: path.name) if missing else [])
        except OSError:
            projects = []
        projects = sorted(known_projects, key=str) + [p for p in projects if p not in known_projects]
        for project in projects:
            if not missing and project not in known_projects:
                break
            _, names = self._cached_claude(project / "sessions-index.json", requested, budget)
            result.update(names)
            # No recursive sidechain/subagent search; validated IDs are direct
            # child filenames. Remember known paths to avoid repeated scans.
            for sid in sorted(missing):
                path = project / (sid + ".jsonl")
                exists, names = self._cached_claude(path, {sid}, budget, transcript=True)
                if exists:
                    self._claude_paths[sid] = path
                    explicit.update(names)
            missing -= self._claude_paths.keys()
        # A blank explicit marker is a removal and overrides stale index names.
        result.update(explicit)
        return result
