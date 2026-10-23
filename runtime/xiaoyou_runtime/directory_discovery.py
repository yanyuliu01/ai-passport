"""Incremental discovery of all available native sources, independent of work cards.

It never executes or resumes an agent, creates a task or imports an owner turn.
Only bounded public metadata is parsed; full text is paged on explicit read.
"""
import threading
import time
from pathlib import Path

from .conversation_directory import uid, agent_home, home_scope
from .desktop_sync import DesktopSync, Cursor, STALE_SECONDS


class _ReadOnly:
    def recent(self, *_):
        return []


class Discovery:
    def __init__(self, config, record, origin, owned=lambda: set(), bound_titles=lambda: {}):
        self.config, self.writer, self.origin = config, record, origin
        self.owned, self.bound_titles = owned, bound_titles
        self.stop = threading.Event()
        self.seen = {}
        self.checked = {}
        self.identities = {}
        self.metadata_seen = {}
        self.status = {'phase': 'discovering', 'indexed_files': 0, 'pending_files': 0}
        self.thread = threading.Thread(target=self.run, name='conversation-directory', daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)

    def scan(self):
        config = self.config
        homes = [('codex', config.desktop_codex_dir or Path.home()/'.codex'),
                 ('claude', config.desktop_claude_dir or Path.home()/'.claude')]
        for spec in config.agents:
            if spec.type in ('codex', 'claude_code'):
                kind = 'codex' if spec.type == 'codex' else 'claude'
                try:
                    homes.append((kind, agent_home(spec)))
                except (OSError, ValueError):
                    continue
        candidates, unchanged, found = [], [], set()
        for agent, home in dict.fromkeys((a, Path(p)) for a, p in homes):
            root = home/('sessions' if agent == 'codex' else 'projects')
            for path in root.rglob('rollout-*.jsonl' if agent == 'codex' else '*.jsonl'):
                if self.stop.is_set():
                    return
                if 'subagents' in path.parts or path.is_symlink():
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                found.add(path)
                signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
                if self.seen.get(path) != signature:
                    candidates.append((stat.st_mtime_ns, agent, home, path, signature))
                elif time.time() - self.checked.get(path, 0) >= 30:
                    unchanged.append((self.checked.get(path, 0), agent, home, path, signature))
        candidates.sort(reverse=True)
        batch = []
        # Metadata-only native renames and stale transitions also propagate.
        # Rotate bounded unchanged sources so a large history cannot monopolize sync.
        selected = candidates[:32] + sorted(unchanged)[:max(0, 32-len(candidates[:32]))]
        owned, titles = self.owned(), self.bound_titles()
        for _, agent, home, path, signature in selected:
            if self.stop.is_set():
                return
            observer = DesktopSync(_ReadOnly(), config.state_dir, codex_dir=home if agent == 'codex' else None,
                claude_dir=home if agent == 'claude' else None, read_bytes=64*1024, content_entries=4,
                content_bytes=8192, read_owned=True)
            cursor = Cursor(path, agent)
            try:
                observer._read(cursor)
            except (OSError, ValueError):
                continue
            self.seen[path] = signature
            self.checked[path] = time.time()
            source = cursor.session
            if source is None or source.skip:
                continue
            if config.desktop_projects and str(Path(source.cwd).expanduser().resolve()) not in {str(p.expanduser().resolve()) for p in config.desktop_projects}:
                continue
            observer.titles.refresh({agent: [source.session_id]})
            title = observer._title(agent, source.session_id, '', source.entries)
            scope = home_scope(home)
            key = uid('native', self.origin, agent, scope, source.session_id)
            self.identities[path] = key
            if source.session_id in owned or source.originator == 'xiaoyou-runtime':
                from .task_titles import select_title
                title = select_title(upstream=observer.titles.get(agent, source.session_id),
                    task=titles.get(key, ''), fallback='小幽内部会话')
            observed = max(source.observed_at, source.content_at)
            stale = source.status in ('running', 'waiting', 'unknown') and time.time()-observed > STALE_SECONDS
            batch.append(dict(uid=key, kind='native', authority=self.origin, source_name=config.name,
                native_scope=scope, native_session_id=source.session_id, agent=agent,
                title=title.title, original_title=title.title, title_source=title.source,
                cwd=source.cwd, project=Path(source.cwd).name if source.cwd else '',
                state='unknown' if stale else {'running':'working','idle':'turn_ended','interrupted':'cancelled'}.get(source.status,source.status),
                stale=stale, observed_at=observed, updated_at=signature[1]/1e9,
                source_revision=signature[1], coverage='native-public-text', metadata_revision=0,
                task_ids=[], native_sources=[], available=True, internal=(source.session_id in owned or source.originator == 'xiaoyou-runtime') and key not in titles))
        previous = self.writer.cards.directory.source_snapshot()
        for path in set(self.identities) - found:
            old = previous.get(self.identities[path])
            if old and old.get('available', True):
                batch.append(dict(old, available=False, stale=True, state='unknown'))
        if batch:
            self.writer.directory_sources(batch, self.origin)
        self.refresh_native_names(dict.fromkeys((a,Path(p)) for a,p in homes))
        self.status = {'phase': 'discovering' if len(candidates)>32 else 'ready',
                       'indexed_files': len(self.seen), 'pending_files': max(0,len(candidates)-32),
                       'last_scan_at': time.time()}

    def refresh_native_names(self, homes):
        from .desktop_titles import DesktopTitles
        from .task_titles import canonical_title
        sources = self.writer.cards.directory.source_snapshot()
        for agent,home in homes:
            if agent != 'codex':
                continue  # Claude renames append to a transcript and are read above.
            home=Path(home)
            paths=[home/'.codex-global-state.json',*home.glob('state_*.sqlite'),*home.glob('state_*.sqlite-wal')]
            signature=[]
            for path in paths:
                try:
                    stat=path.stat();signature.append((str(path),stat.st_ino,stat.st_mtime_ns,stat.st_size))
                except OSError:
                    continue
            signature=tuple(sorted(signature))
            if self.metadata_seen.get(home)==signature:
                continue
            self.metadata_seen[home]=signature
            scope=home_scope(home)
            rows=[r for r in sources.values() if r.get('authority')==self.origin and r.get('agent')==agent and r.get('native_scope')==scope]
            reader=DesktopTitles(home,Path(self.config.desktop_claude_dir or Path.home()/'.claude'))
            changed=[]
            for offset in range(0,len(rows),64):
                if self.stop.is_set():
                    return
                batch=rows[offset:offset+64]
                reader.refresh({agent:[r['native_session_id'] for r in batch]})
                for row in batch:
                    title=canonical_title(reader.get(agent,row['native_session_id']),'upstream')
                    if title.title and (title.title!=row['original_title'] or row.get('title_source')!='upstream'):
                        changed.append(dict(row,title=title.title,original_title=title.title,title_source='upstream'))
            if changed:
                self.writer.directory_sources(changed,self.origin)

    def run(self):
        while not self.stop.is_set():
            try:
                self.scan()
            except Exception:
                self.status = dict(self.status, phase='source_unavailable')
            self.stop.wait(2)
