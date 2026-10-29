"""Shared, durable conversation metadata; writes are owned by Record."""

import copy
import json
import math
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict

from .store import _write
from .task_titles import canonical_title


class ConversationCatalog:
    def __init__(self, path: Path):
        self.path = path
        self.origin = ""
        self.lock = threading.RLock()
        try:
            self.items = json.loads(path.read_text('utf-8'))
            if not isinstance(self.items, dict):
                raise ValueError('Invalid conversation catalog')
        except FileNotFoundError:
            self.items = {}
        self.items.setdefault('default', {'id': 'default', 'title': '主对话', 'kind': 'main',
                                         'created_at': 0, 'updated_at': 0, 'revision': 1})

    def list(self, include_deleted=False):
        with self.lock:
            return copy.deepcopy(sorted((v for v in self.items.values() if include_deleted or not v.get('deleted')), key=lambda v: (-v['updated_at'], v['id'])))

    def get(self, name):
        with self.lock:
            return copy.deepcopy(self.items.get(name))

    def create(self, name=None, context=None):
        name = name or 'side-' + uuid.uuid4().hex
        with self.lock:
            if name in self.items:
                if self.items[name].get('deleted'):
                    raise ValueError('旁聊已删除')
                return self.get(name)
            now = time.time()
            item = {'id': name, 'title': '新旁聊', 'kind': 'side', 'created_at': now,
                    'updated_at': now, 'revision': 1, 'title_source': 'pending', 'metadata_at': now, 'origin': self.origin,
                    # 创建它的那台：这段旁聊的第 0 代负责人（ownership.py），改名、同步都不改。
                    **({'owner': self.origin} if self.origin else {})}
            if context:
                item['context'] = copy.deepcopy(context)
            self._save(name, item)
            return self.get(name)

    def _save(self, name, item):
        updated = dict(self.items, **{name: item})
        _write(self.path, updated, 2)  # Publish in memory only after durable write.
        self.items = updated

    def rename(self, name, title, expected=None, source='owner'):
        cleaned = canonical_title(title).title
        if not cleaned or len(cleaned) > 80:
            raise ValueError('标题应为 1 到 80 个字')
        with self.lock:
            item = self.get(name)
            if not item or item.get('deleted') or name == 'default':
                raise ValueError('旁聊不存在')
            if expected is not None and item['revision'] != expected:
                return item  # A late generated title cannot overwrite a newer edit.
            item.update(title=cleaned, title_source=source, revision=item['revision'] + 1, metadata_at=time.time(), origin=self.origin)
            self._save(name, item)
            return self.get(name)

    def touch(self, name, text='', at=None):
        with self.lock:
            if name not in self.items:
                self.create(name)
            item = self.get(name)
            if item.get('deleted'):
                return item
            item['updated_at'] = max(item['updated_at'], at or time.time())
            if name != 'default' and item.get('title_source') == 'pending' and text.strip():
                item.update(title=canonical_title(text, 'request').title or '新旁聊', title_source='request', metadata_at=time.time(), origin=self.origin)
                item['revision'] += 1
            self._save(name, item)
            return self.get(name)

    def delete(self, name):
        with self.lock:
            old = self.items.get(name)
            if not old or old.get('kind') != 'side' or name == 'default':
                raise ValueError('只能删除旁聊')
            if old.get('deleted'):
                return copy.deepcopy(old)
            now = time.time()
            item = {'id': name, 'kind': 'side', 'title': '', 'deleted': True, 'origin': self.origin,
                    'metadata_at': now, 'created_at': now, 'updated_at': now, 'revision': old.get('revision', 0) + 1}
            self._save(name, item)
            return copy.deepcopy(item)

    def merge(self, origin, data):
        """Merge only versioned side-chat metadata from an authenticated mesh peer."""
        import re
        from ..events.desktop_content import redact_credentials
        name = data.get('id')
        if not isinstance(name, str) or not re.fullmatch(r'side-[A-Za-z0-9_-]{1,58}', name):
            return False
        at = data.get('metadata_at', data.get('created_at', 0))
        if not isinstance(at, (int, float)) or isinstance(at, bool) or not math.isfinite(at) or at < 0:
            return False
        with self.lock:
            old = self.get(name)
            if old and old.get('deleted'):
                return False
            if not data.get('deleted') and old and (old.get('metadata_at', 0), old.get('origin', '')) >= (at, origin):
                return False
            if data.get('deleted') is True:
                self._save(name, {'id': name, 'kind': 'side', 'title': '', 'deleted': True, 'origin': origin, 'metadata_at': at, 'created_at': at, 'updated_at': at, 'revision': (old or {}).get('revision', 0) + 1})
                return True
            title = canonical_title(data.get('title')).title[:80] or '新旁聊'
            item = {'id': name, 'kind': 'side', 'title': title, 'origin': origin, 'metadata_at': at,
                    'created_at': at, 'updated_at': at,
                    'revision': (old or {}).get('revision', 0) + 1, 'title_source': data.get('title_source', 'upstream')}
            owner = (old or {}).get('owner') or data.get('owner')
            if isinstance(owner, str) and re.fullmatch(r'[0-9a-f]{32}', owner):
                item['owner'] = owner  # 先记下的为准：后来改名的那台不会变成负责人
            context = data.get('context')
            if isinstance(context, dict):
                item['context'] = {key: context[key] for key in ('id', 'conversation', 'author', 'source') if key in context}
                item['context']['text'] = redact_credentials(str(context.get('text') or ''))[:4000]
            self._save(name, item)
            return True
