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
            if name == 'weixin':
                raise ValueError('微信会话须通过渠道绑定创建')
            now = time.time()
            item = {'id': name, 'title': '新旁聊', 'kind': 'side', 'created_at': now,
                    'updated_at': now, 'revision': 1, 'title_source': 'pending', 'metadata_at': now, 'origin': self.origin,
                    # 创建它的那台：这段旁聊的第 0 代负责人（ownership.py），改名、同步都不改。
                    **({'owner': self.origin} if self.origin else {})}
            if context:
                item['context'] = copy.deepcopy(context)
            self._save(name, item)
            return self.get(name)

    def create_weixin(self):
        """Create the reserved channel conversation, never a side chat."""
        with self.lock:
            old = self.items.get('weixin')
            if old:
                if old.get('kind') != 'weixin' or old.get('deleted'):
                    raise ValueError('微信会话与已有记录冲突，需先核对')
                return copy.deepcopy(old)
            now = time.time()
            item = {'id': 'weixin', 'title': '微信', 'kind': 'weixin',
                    'created_at': now, 'updated_at': now, 'revision': 1,
                    'title_source': 'fixed', 'metadata_at': now, 'origin': self.origin,
                    **({'owner': self.origin} if self.origin else {})}
            self._save('weixin', item)
            return self.get('weixin')

    def observe_weixin(self):
        """Create a read-only catalog entry for a received peer record.

        Observation never grants channel ownership or replaces a binding.
        """
        with self.lock:
            old = self.items.get('weixin')
            if old:
                if old.get('kind') != 'weixin' or old.get('deleted'):
                    raise ValueError('微信会话与已有记录冲突')
                return copy.deepcopy(old), False
            item = {'id': 'weixin', 'title': '微信', 'kind': 'weixin',
                    'created_at': 0, 'updated_at': 0, 'revision': 1,
                    'title_source': 'fixed', 'observed_only': True}
            self._save('weixin', item)
            return copy.deepcopy(item), True

    def claim_weixin(self, grant):
        """Apply an internally verified grant without adopting peer metadata."""
        import re
        if (not isinstance(grant, dict) or grant.get('host') != self.origin
                or not isinstance(self.origin, str) or not re.fullmatch(r'[0-9a-f]{32}', self.origin)
                or not isinstance(grant.get('authority'), str) or not re.fullmatch(r'[0-9a-f]{32}', grant['authority'])
                or type(grant.get('generation')) is not int or grant['generation'] < 1
                or grant.get('state') not in ('reserved', 'active')):
            raise ValueError('微信渠道负责人授权无效')
        with self.lock:
            old = self.items.get('weixin')
            if old and (old.get('kind') != 'weixin' or old.get('deleted')):
                raise ValueError('微信会话与已有记录冲突')
            previous_authority = (old or {}).get('grant_authority')
            previous_generation = (old or {}).get('grant_generation')
            previous_owner = (old or {}).get('owner')
            if ((previous_authority is None) != (previous_generation is None)
                    or (previous_generation is not None and
                        (type(previous_generation) is not int or previous_generation < 1))):
                raise ValueError('微信渠道负责人记录无效，需要核对')
            if previous_authority and previous_authority != grant['authority']:
                raise ValueError('微信渠道权威发生变化，需要核对')
            if previous_owner and previous_owner != self.origin and type(previous_generation) is not int:
                raise ValueError('旧微信渠道负责人未确认，需要核对')
            if type(previous_generation) is int and (previous_generation > grant['generation']
                    or (previous_generation == grant['generation'] and previous_owner != self.origin)):
                raise ValueError('微信渠道负责人版本冲突')
            if (old and previous_owner == self.origin and previous_authority == grant['authority']
                    and previous_generation == grant['generation'] and not old.get('observed_only')):
                return copy.deepcopy(old), False
            now = time.time()
            item = dict(old or {'id': 'weixin', 'title': '微信', 'kind': 'weixin', 'created_at': now,
                                'updated_at': now, 'revision': 0, 'title_source': 'fixed'})
            item.pop('observed_only', None)
            item.update(owner=self.origin, origin=self.origin, grant_authority=grant['authority'],
                        grant_generation=grant['generation'], metadata_at=now,
                        revision=item.get('revision', 0) + 1)
            self._save('weixin', item)
            return copy.deepcopy(item), True

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
            if not item or item.get('deleted') or item.get('kind') != 'side':
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
        if name == 'weixin':
            # A channel binding is explicitly established locally; accepting
            # peer metadata here would bypass ownership and binding authority.
            return False
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
