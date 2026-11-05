"""Local-only credentials for a channel host; never published to peers."""
import json
import os
import tempfile
from pathlib import Path

from .weixin import _base


def _validate(data):
    if not isinstance(data, dict) or set(data) != {'token', 'bot', 'owner', 'base'}:
        raise ValueError('invalid channel credentials')
    if not all(isinstance(data[key], str) and data[key] for key in ('token', 'bot', 'owner', 'base')):
        raise ValueError('invalid channel credentials')
    return dict(data, base=_base(data['base']))


class Credentials:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            with self.path.open('r', encoding='utf-8') as stream:
                return _validate(json.load(stream))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError):
            raise ValueError('channel credentials unavailable') from None

    def save(self, data):
        data = _validate(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.channel-', dir=str(self.path.parent))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            try:
                folder = os.open(str(self.path.parent), os.O_RDONLY)
            except OSError:
                return
            try:
                os.fsync(folder)
            except OSError:
                pass
            finally:
                os.close(folder)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def remove(self):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
