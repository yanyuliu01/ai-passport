"""Paged reads of the existing canonical message log and its sealed segments.

There is no second message schema or writable history database. Compaction
seals evicted canonical records before replacing the live retention window.
Pages are revision upserts: consumers retain the highest revision per message.
"""
import base64
import json
import os
from pathlib import Path

SCAN_BYTES = 8 * 1024 * 1024
LINE_BYTES = 1024 * 1024


def seal(path, retained=()):
    path = Path(path)
    if not path.exists() or not path.stat().st_size:
        return
    stamp = path.stat()
    keep, evicted = set(retained), {}
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            key = item.get('id') if isinstance(item, dict) else None
            if key and key not in keep and item.get('kind') != 'activity' and item.get('status') not in ('queued', 'streaming'):
                evicted[key] = item
    if not evicted:
        return
    folder = path.with_suffix('.history')
    folder.mkdir(exist_ok=True, mode=0o700)
    target = folder / ('%020d-%s.jsonl' % (stamp.st_mtime_ns, stamp.st_ino))
    if target.exists():
        return
    temporary = target.with_suffix('.part')
    with temporary.open('w', encoding='utf-8') as stream:
        for item in sorted(evicted.values(), key=lambda item: item.get('seq', 0)):
            stream.write(json.dumps(item, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)


def _token(value):
    return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode().rstrip('=')


def page(path, predicate, scope, cursor=None, limit=50):
    path = Path(path)
    files = sorted(path.with_suffix('.history').glob('*.jsonl'))
    if path.exists():
        files.append(path)
    # Ignore duplicate filesystem identities during concurrent retention changes.
    distinct, inodes = [], set()
    for file in files:
        try:
            identity = file.stat().st_ino
        except OSError:
            continue
        if identity not in inodes:
            distinct.append(file)
            inodes.add(identity)
    files = distinct
    index = len(files) - 1
    offset = files[index].stat().st_size if files else 0
    if cursor:
        try:
            if not isinstance(cursor, str) or len(cursor) > 1024:
                raise ValueError()
            token = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
            if token['scope'] != scope or not isinstance(token['offset'], int) or token['offset'] < 0:
                raise ValueError()
            index = next(i for i, f in enumerate(files) if f.stat().st_ino == token['inode'])
            offset = token['offset']
            if offset > files[index].stat().st_size:
                raise ValueError()
        except (ValueError, KeyError, StopIteration, TypeError, OSError):
            raise ValueError('History cursor expired or belongs to another source; refresh history')
    records, seen, budget = [], set(), SCAN_BYTES
    while index >= 0 and budget > 0 and len(records) < limit:
        with files[index].open('rb') as stream:
            while offset > 0 and budget > 0 and len(records) < limit:
                end = offset
                start = max(0, end - min(LINE_BYTES, budget))
                stream.seek(start)
                chunk = stream.read(end - start)
                budget -= len(chunk)
                if start:
                    boundary = chunk.find(b'\n')
                    if boundary < 0:
                        offset = start
                        continue
                    # Keep the partial prefix for the next read; scan complete
                    # lines once per chunk, rather than rereading 1 MiB per line.
                    start += boundary + 1
                    chunk = chunk[boundary + 1:]
                lines = chunk.splitlines(keepends=True)
                position = end
                offset = start
                for line in reversed(lines):
                    position -= len(line)
                    offset = position
                    try:
                        item = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'] or item.get('floor') is not None:
                        continue
                    if item.get('kind') == 'activity' or item.get('status') in ('queued', 'streaming') or not predicate(item):
                        continue
                    key = (item.get('mesh_origin', ''), item['id'])
                    if key not in seen:
                        seen.add(key)
                        records.append(item)
                        if len(records) >= limit:
                            break
        if offset == 0 and len(records) < limit:
            index -= 1
            offset = files[index].stat().st_size if index >= 0 else 0
    more = index >= 0 and (offset > 0 or index > 0)
    next_cursor = _token({'scope': scope, 'inode': files[index].stat().st_ino, 'offset': offset}) if more else None
    return {'messages': records, 'cursor': next_cursor, 'more': more, 'semantics': 'revision-upserts',
            'coverage': 'retained-canonical-records', 'earlier_gaps_possible': True}
