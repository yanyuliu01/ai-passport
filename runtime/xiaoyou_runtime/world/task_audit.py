"""Append-only public execution evidence, independent of the sync retention window."""
import hashlib
import json
import os
from collections import deque
from pathlib import Path


class TaskAudit:
    def __init__(self, directory: Path):
        self.directory = directory

    def _path(self, task):
        return self.directory / (hashlib.sha256(task.encode()).hexdigest() + '.jsonl')

    def append(self, event):
        self.directory.mkdir(parents=True, exist_ok=True)
        with self._path(event['task']).open('a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')
            f.flush()
            os.fsync(f.fileno())

    def page(self, task, before=None, limit=100):
        events, count = deque(maxlen=limit), 0
        try:
            with self._path(task).open(encoding='utf-8') as f:
                for line in f:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue  # An interrupted final line is not an event.
                    if before is None or item.get('seq', 0) < before:
                        count += 1
                        events.append(item)
        except FileNotFoundError:
            pass
        rows = list(events)
        return {'events': rows, 'before': rows[0]['seq'] if rows else None,
                'more': count > len(rows), 'coverage': 'retained-public-events'}
