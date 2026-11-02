"""Rebuildable metadata for immutable retained cards; original JSON remains authoritative."""
import hashlib
import json
import os
import tempfile
from pathlib import Path

VERSION = 2
FIELDS = (
    'id', 'record_epoch', 'retained_only', 'mesh_origin', 'mesh_epoch', 'mesh_id',
    'agent', 'worker_agent', 'monitoring_only', 'session_id', 'worker_session',
    'native_scope', 'worker_scope', 'title', 'title_source', 'origin_name',
    'conversation', 'project', 'desktop_cwd', 'worker_cwd', 'state', 'stale',
    'observed_at', 'updated_at', 'binding_error', 'execution_attempts',
    'canonical_message_ids', 'borrowed_session',
)


def summary(card):
    """All fields consumed by directory projection, without retained message bodies."""
    result = {key: card[key] for key in FIELDS if key in card}
    result['entries'] = [{'id': entry['id']} for entry in card.get('entries', [])
                         if isinstance(entry, dict) and entry.get('id')]
    return result


def _signature(path):
    s = path.stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def _encoded(records):
    return json.dumps(records, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _load(path):
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
        records, idsets = raw['data']
        if raw['version'] != VERSION or not isinstance(records, dict) or not isinstance(idsets, list):
            return {}, []
        if hashlib.sha256(_encoded([records, idsets])).hexdigest() != raw['sha256']:
            return {}, []
        return records, idsets
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return {}, []


def _save(path, records, idsets):
    """A cache failure must never prevent reading the original cards."""
    temporary = None
    try:
        encoded = _encoded([records, idsets])
        payload = b'{"version":' + str(VERSION).encode('ascii') + b',"sha256":"' + \
            hashlib.sha256(encoded).hexdigest().encode('ascii') + b'","data":' + encoded + b'}'
        fd, name = tempfile.mkstemp(prefix='.retained-index-', dir=str(path.parent))
        temporary = Path(name)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    except (OSError, ValueError, TypeError):
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def load(folder, index_path):
    """Reconcile every filename and stat with the cache; never delete a snapshot."""
    folder, index_path = Path(folder), Path(index_path)
    cached, cached_sets = _load(index_path)
    records, cards, idsets, interned = {}, {}, [], {}
    changed = False
    for path in folder.glob('*.json'):
        try:
            signature = _signature(path)
            old = cached.get(path.stem)
            ref = old.get('ids') if isinstance(old, dict) else None
            valid_ref = (ref is None or (type(ref) is int and 0 <= ref < len(cached_sets)
                         and isinstance(cached_sets[ref], list)
                         and all(isinstance(value, str) for value in cached_sets[ref])))
            if (isinstance(old, dict) and old.get('signature') == signature
                    and isinstance(old.get('card'), dict)
                    and isinstance(old['card'].get('id'), str)
                    and isinstance(old['card'].get('record_epoch'), str) and valid_ref):
                card = dict(old['card'])
                if ref is not None:
                    card['canonical_message_ids'] = cached_sets[ref]
                    card['entries'] = []
            else:
                changed = True
                item = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(item, dict) or not isinstance(item.get('id'), str) \
                        or not isinstance(item.get('record_epoch'), str):
                    continue
                card = summary(item)
                # A concurrent modification must be checked again next startup.
                if _signature(path) != signature:
                    cards[path.stem] = card
                    continue
            ids = card.get('canonical_message_ids')
            if not ids:
                ids = [entry['id'] for entry in card.get('entries', []) if entry.get('id')]
            saved = dict(card)
            ref = None
            if isinstance(ids, list) and all(isinstance(value, str) for value in ids):
                key = tuple(ids)
                ref = interned.get(key)
                if ref is None:
                    ref = len(idsets)
                    interned[key] = ref
                    idsets.append(ids)
                card['canonical_message_ids'] = idsets[ref]
                card['entries'] = []
                saved.pop('canonical_message_ids', None)
                saved.pop('entries', None)
            records[path.stem] = {'signature': signature, 'card': saved, 'ids': ref}
            cards[path.stem] = card
        except (OSError, ValueError, TypeError, RecursionError):
            changed = True
    if len(cached) != len(records):
        changed = True
    if changed or not index_path.is_file():
        _save(index_path, records, idsets)
    return cards
