"""Durable identity of an unresolved channel-authority mutation.

Contains routing metadata only: never QR contents, account IDs or credentials.
Only a holder of the local channel-grant lock may mutate this file.
"""
import json
import re
from pathlib import Path

from ..world.world import durable_write


def _valid(data):
    return (isinstance(data, dict) and set(data) == {'id', 'authority', 'action', 'generation', 'subject', 'host_epoch', 'authority_epoch'}
            and isinstance(data.get('id'), str) and re.fullmatch(r'[0-9a-f]{32}', data['id']) is not None
            and isinstance(data.get('authority'), str) and re.fullmatch(r'[0-9a-f]{32}', data['authority']) is not None
            and all(isinstance(data.get(key), str) and re.fullmatch(r'[0-9a-f]{32}', data[key]) is not None
                    for key in ('subject', 'host_epoch', 'authority_epoch'))
            and data.get('action') in ('reserve', 'activate', 'begin_release', 'release')
            and ((data['action'] == 'reserve' and data.get('generation') is None)
                 or (data['action'] != 'reserve' and type(data.get('generation')) is int and data['generation'] >= 1)))


class AuthorityJournal:
    def __init__(self, path):
        self.path = Path(path)

    def pending(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeError):
            raise ValueError('微信渠道操作记录不可用') from None
        if data is None:
            return None
        if not _valid(data):
            raise ValueError('微信渠道操作记录不可用')
        return dict(data)

    def begin(self, data):
        if self.pending() is not None:
            raise ValueError('上次微信渠道操作需要先核对')
        # Validate before publishing, using the same constraints as recovery.
        if not _valid(data):
            raise ValueError('微信渠道操作格式无效')
        durable_write(self.path, data)

    def finish(self, command_id):
        pending = self.pending()
        if pending is None or pending['id'] != command_id:
            raise ValueError('微信渠道操作编号不匹配')
        # A durable null is safer than unlinking without a directory barrier.
        durable_write(self.path, None)
