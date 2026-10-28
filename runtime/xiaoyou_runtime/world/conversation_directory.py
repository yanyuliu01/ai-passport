"""Shared presentation over existing conversations, tasks and native identities.

Only display metadata is writable here. Tasks, messages, cwd, permissions and
native transcripts remain owned by their existing stores and adapters.
"""
import copy
import hashlib
import json
import math
import os
import threading
import time
from pathlib import Path

from ..events.desktop_content import redact_credentials
from .store import _write
from .task_titles import canonical_title


def uid(kind, *identity):
    value = json.dumps([kind, *identity], ensure_ascii=False, separators=(',', ':'))
    return kind + '-' + hashlib.sha256(value.encode()).hexdigest()[:40]


def native_agent(name):
    name = (name or '').lower().split('@', 1)[0]
    return {'cc': 'claude', 'claude_code': 'claude'}.get(name, name)


def agent_home(spec):
    key = 'CODEX_HOME' if spec.type == 'codex' else 'CLAUDE_CONFIG_DIR'
    value = spec.config_dir or spec.env.get(key)
    if not value and key in spec.env_files:
        path = Path(spec.env_files[key])
        if path.stat().st_size > 4096:
            raise ValueError('Native home setting is oversized')
        value = path.read_text().strip()
    return Path(value or os.environ.get(key) or Path.home()/('.codex' if spec.type == 'codex' else '.claude')).expanduser().resolve()


def home_scope(path):
    return uid('home', str(Path(path).expanduser().resolve()))


class DirectoryMetadata:
    """Record-owned display preferences; never an alternative task registry."""
    def __init__(self, path):
        self.path, self.lock = Path(path), threading.RLock()
        self.sources_path = self.path.with_name('conversation-sources.json')
        self.generated_path = self.path.with_name('conversation-generated.json')
        self.errors = set()
        self.sources = self._load(self.sources_path)
        self.items = self._load(self.path)
        self.generated = self._load(self.generated_path)

    def _load(self, path):
        try:
            value = json.loads(path.read_text('utf-8'))
            if not isinstance(value,dict) or any(not isinstance(k,str) or not isinstance(v,dict) for k,v in value.items()):
                raise ValueError('Invalid metadata')
            return value
        except FileNotFoundError:
            return {}
        except (OSError,ValueError):
            self.errors.add(path.name)
            return {}  # Preserve the damaged file; do not overwrite it on an edit.

    def _writable(self, path):
        if path.name in self.errors:
            raise ValueError('Shared metadata is damaged; original file preserved. Repair or restore it before editing.')

    def erase(self, keys):
        with self.lock:
            for attribute, path in (('items',self.path),('generated',self.generated_path)):
                self._writable(path)
                updated = {key: value for key, value in getattr(self,attribute).items() if key not in keys}
                if updated != getattr(self,attribute):
                    _write(path,updated,2)
                    setattr(self,attribute,updated)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.items)

    def source_snapshot(self):
        with self.lock:
            return copy.deepcopy(self.sources)

    def observe_sources(self, values, origin):
        self._writable(self.sources_path)
        with self.lock:
            updated = dict(self.sources)
            for item in values:
                if not isinstance(item, dict) or item.get('authority') != origin or item.get('kind') != 'native':
                    continue
                if any(not isinstance(item.get(k), str) or len(item[k]) > n for k, n in (
                    ('agent', 120), ('native_scope', 80), ('native_session_id', 128), ('title', 200))):
                    continue
                if any(not isinstance(item.get(k), (int, float)) or isinstance(item.get(k), bool) or not math.isfinite(item[k]) or item[k] < 0 for k in ('observed_at', 'updated_at', 'source_revision')):
                    continue
                expected = uid('native', origin, item.get('agent'), item.get('native_scope'), item.get('native_session_id'))
                if item.get('uid') != expected:
                    continue
                old = updated.get(expected)
                if old and old.get('source_revision', 0) > item.get('source_revision', 0):
                    continue
                allowed = {'uid','kind','authority','source_name','native_scope','native_session_id','agent',
                    'title','original_title','title_source','cwd','project','state','stale','observed_at',
                    'updated_at','source_revision','coverage','metadata_revision','internal','available'}
                clean = {k: copy.deepcopy(v) for k, v in item.items() if k in allowed}
                for field in ('title', 'original_title', 'project', 'source_name', 'cwd'):
                    clean[field] = redact_credentials(str(clean.get(field) or ''))[:4096 if field == 'cwd' else 200]
                clean['task_ids'], clean['native_sources'] = [], []
                clean['stale'] = item.get('stale') is True
                clean['internal'] = item.get('internal') is True
                clean['available'] = item.get('available', True) is True
                clean['metadata_revision'] = 0
                if clean.get('state') not in ('working','talking','waiting','unknown','failed','cancelled','turn_ended'):
                    clean['state'] = 'unknown'
                updated[expected] = clean
            if updated == self.sources:
                return False
            _write(self.sources_path, updated, 2)
            self.sources = updated
            return True

    def generated_snapshot(self):
        with self.lock:
            return copy.deepcopy(self.generated)

    def merge_generated(self, origin, key, value):
        self._writable(self.generated_path)
        if not isinstance(key, str) or not isinstance(value, dict) or value.get('origin') != origin:
            return False
        if not isinstance(value.get('created_at'), (int,float)) or not math.isfinite(value['created_at']):
            return False
        if any(not isinstance(value.get(k),str) or len(value[k])>n for k,n in (('title',200),('summary',1200),('input_version',100),('input_hash',64),('source',40))):
            return False
        with self.lock:
            old = self.generated.get(key, {})
            if old and (old.get('origin') != origin or old.get('created_at',0) >= value['created_at']):
                return False
            clean = {k:copy.deepcopy(v) for k,v in value.items() if k in {'title','summary','input_version','input_hash','source','base_revision','created_at','origin','coverage','model','provider'}}
            clean['title'], clean['summary'] = redact_credentials(clean['title']), redact_credentials(clean['summary'])
            updated = dict(self.generated, **{key:clean})
            _write(self.generated_path, updated, 2)
            self.generated = updated
            return True

    def save_generated(self, key, value, expected):
        self._writable(self.generated_path)
        with self.lock:
            if self.items.get(key, {}).get('revision', 0) != expected:
                return False
            updated = dict(self.generated, **{key: copy.deepcopy(value)})
            _write(self.generated_path, updated, 2)
            self.generated = updated
            return True

    def edit(self, key, fields, origin, expected=None):
        self._writable(self.path)
        if not isinstance(fields, dict) or not fields or set(fields) - {'title', 'pinned', 'archived', 'project'}:
            raise ValueError('Only title, pinned, archived and project may be changed')
        cleaned = {}
        for name, value in fields.items():
            if name in ('pinned', 'archived'):
                if not isinstance(value, bool):
                    raise ValueError(name + ' must be boolean')
                cleaned[name] = value
            else:
                if not isinstance(value, str) or len(value) > (200 if name == 'title' else 120):
                    raise ValueError('Invalid ' + name)
                value = canonical_title(value).title
                if name == 'title' and not value:
                    raise ValueError('Title cannot be empty')
                cleaned[name] = value
        with self.lock:
            old = self.items.get(key, {})
            if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int) or expected < 0):
                raise ValueError('Invalid expected metadata revision')
            if all(old.get(k) == v for k, v in cleaned.items()):
                return copy.deepcopy(old)
            if expected is not None and expected != old.get('revision', 0):
                raise ValueError('Metadata changed; refresh before editing')
            item = dict(old, **cleaned, uid=key, origin=origin, revision=old.get('revision', 0) + 1,
                        updated_at=time.time())
            updated = dict(self.items, **{key: item})
            _write(self.path, updated, 2)
            self.items = updated
            return copy.deepcopy(item)

    def merge(self, origin, item):
        self._writable(self.path)
        if not isinstance(item, dict) or item.get('origin') != origin:
            return False
        key, revision, stamp = item.get('uid'), item.get('revision'), item.get('updated_at')
        if not isinstance(key, str) or not 1 <= len(key) <= 64 or not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            return False
        if not isinstance(stamp, (int, float)) or isinstance(stamp, bool) or not math.isfinite(stamp):
            return False
        fields = {k: item[k] for k in ('title', 'pinned', 'archived', 'project') if k in item}
        if not fields or any(not isinstance(v, bool) if k in ('pinned', 'archived') else not isinstance(v, str) or len(v) > 200 for k, v in fields.items()):
            return False
        with self.lock:
            old = self.items.get(key, {})
            if old and (old.get('origin') != origin or old.get('revision', 0) >= revision):
                return False
            value = dict(fields, uid=key, origin=origin, revision=revision, updated_at=stamp)
            for field in ('title', 'project'):
                if field in value:
                    value[field] = redact_credentials(value[field])
            updated = dict(self.items, **{key: value})
            _write(self.path, updated, 2)
            self.items = updated
            return True


def project(cards, conversations, preferences, runtime_id, runtime_name='', specs=(), epoch='', native_homes=None, discovered=None):
    """Derive one directory without grouping by title, text or directory name."""
    entries, sources = {}, {}
    types = {spec.name: native_agent(spec.type) for spec in specs}
    scopes = {}
    for spec in specs:
        if spec.type in ('codex', 'claude_code'):
            try:
                scopes[spec.name] = home_scope(agent_home(spec))
            except (OSError, ValueError):
                scopes[spec.name] = 'home_unavailable'
    for item in conversations:
        key = uid('chat', item['id'])
        entries[key] = dict(item, uid=key, kind='conversation', conversation_id=item['id'],
                            authority=item.get('origin') or runtime_id, source_name=runtime_name,
                            metadata_revision=0, native_sources=[], task_ids=[])
    for card in cards:
        if not card.get('agent') and not card.get('worker_agent') and not card.get('monitoring_only'):
            continue  # Ordinary short turns belong to their logical conversation.
        authority = card.get('mesh_origin') or runtime_id
        task_id = card.get('mesh_id') or card['id']
        observed = bool(card.get('monitoring_only'))
        sid = card.get('session_id') if observed else card.get('worker_session')
        agent = native_agent(card.get('agent')) if observed else types.get(card.get('worker_agent') or card.get('agent'), native_agent(card.get('agent')))
        scope = card.get('native_scope') if observed else card.get('worker_scope') or scopes.get(card.get('worker_agent') or card.get('agent'))
        if authority != runtime_id and not card.get('native_scope') and not card.get('worker_scope'):
            scope = ''
        scope = scope or ('legacy-unknown' if authority != runtime_id else (native_homes or {}).get(agent, 'legacy'))
        origin_epoch = card.get('mesh_epoch') or card.get('record_epoch') or epoch
        source_id = uid('native', authority, agent, scope, sid) if sid else None
        key = source_id if observed and source_id else uid('task', authority, origin_epoch, task_id)
        title = card.get('title') or '未命名工作'
        row = dict(uid=key, kind='native' if observed else 'task', title=title,
                   title_source=card.get('title_source', 'task'), original_title=title,
                   authority=authority, source_name=card.get('origin_name') or runtime_name,
                   conversation_id=card.get('conversation', 'default'), task_id=card['id'],
                   origin_task_id=task_id, origin_epoch=origin_epoch, native_scope=scope,
                   agent=card.get('agent') or '', project=card.get('project') or '',
                   cwd=card.get('desktop_cwd') or card.get('worker_cwd') or '', state=card.get('state'),
                   stale=bool(card.get('stale')), observed_at=card.get('observed_at') or 0,
                   updated_at=card.get('updated_at') or 0, native_sources=[source_id] if source_id else [],
                   metadata_revision=0, binding_error=card.get('binding_error',''), retained_only=bool(card.get('retained_only')), execution_attempts=copy.deepcopy(card.get('execution_attempts', [])),
                   legacy_message_ids=card.get('canonical_message_ids') or [e['id'] for e in card.get('entries',[]) if e.get('id')])
        entries[key] = row
        if source_id:
            source = sources.setdefault(source_id, dict(uid=source_id, kind='native', title=title,
                original_title=title, title_source=card.get('title_source', 'task'),
                authority=authority, source_name=row['source_name'], agent=agent, native_session_id=sid, native_scope=scope,
                cwd=row['cwd'], project=row['project'], task_ids=[], state='unknown', stale=True,
                metadata_revision=0, updated_at=row['updated_at'], native_sources=[]))
            if not observed:
                source['task_ids'].append(key)
            else:
                source.update(row, native_session_id=sid, task_ids=source['task_ids'])
                entries[key] = source
    for key, source in sources.items():
        if key not in entries:
            entries[key] = source
    for key, source in (discovered or {}).items():
        existing = entries.get(key)
        if existing is None:
            entries[key] = copy.deepcopy(source)
        elif existing['kind'] == 'native':
            # Discovery supplies newer native facts, never a task's goal/state.
            original = dict(existing)
            existing.update(source)
            existing['task_ids'] = original.get('task_ids', [])
            existing['task_id'] = original.get('task_id')
            existing['native_sources'] = original.get('native_sources', [])
            if original.get('title_source') == 'task' and source.get('title_source') == 'fallback':
                existing.update(title=original['title'], original_title=original['original_title'], title_source=original['title_source'])
    for key, row in entries.items():
        metadata = preferences.get(key, {})
        if metadata.get('origin') == row['authority']:
            row.update({k: metadata[k] for k in ('title', 'pinned', 'archived', 'project') if k in metadata})
            row['metadata_revision'] = metadata.get('revision', 0)
            if 'title' in metadata:
                row['title_source'] = 'owner_alias'
        row.setdefault('pinned', False)
        row.setdefault('archived', False)
        if row['kind'] == 'native':
            row['bound'] = bool(row.get('task_ids'))
    return sorted(entries.values(), key=lambda e: (not e['pinned'], -e.get('updated_at', 0), e['uid']))
