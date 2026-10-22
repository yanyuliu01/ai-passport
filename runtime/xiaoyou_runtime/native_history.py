"""Read one known native source, without resuming it or changing its files."""
import base64
import json
import re
from pathlib import Path

from .conversation_directory import uid, agent_home, home_scope
from .desktop_sync import Cursor, DesktopSync, MAX_LINE_BYTES
from .message_log import entry_message


class _ReadOnly:
    def recent(self, *_):
        return []


def locate(config, row):
    agent, sid = row['agent'], row.get('native_session_id')
    if agent not in ('codex', 'claude') or not sid:
        return None
    homes = [(config.desktop_codex_dir or Path.home()/'.codex') if agent == 'codex'
             else (config.desktop_claude_dir or Path.home()/'.claude')]
    for spec in config.agents:
        if (agent == 'codex' and spec.type == 'codex') or (agent == 'claude' and spec.type == 'claude_code'):
            try:
                homes.append(agent_home(spec))
            except (OSError, ValueError):
                continue
    home = next((Path(p) for p in homes if home_scope(p) == row.get('native_scope')), None)
    if home is None:
        return None
    root = home/('sessions' if agent == 'codex' else 'projects')
    # IDs are taken from an authenticated directory projection, never a caller path.
    if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', sid):
        raise ValueError('Invalid native source identity')
    paths = [p for p in root.rglob('*'+sid+'.jsonl') if 'subagents' not in p.parts and not p.is_symlink()]
    if len(paths) != 1:
        return None
    return home, paths[0]


def recent(config, row):
    location = locate(config,row)
    if location is None:
        return {'messages':[],'coverage':'native-source-unavailable'}
    home,path = location
    agent,sid = row['agent'],row['native_session_id']
    observer=DesktopSync(_ReadOnly(),config.state_dir,codex_dir=home if agent=='codex' else None,
        claude_dir=home if agent=='claude' else None,read_bytes=128*1024,content_entries=20,content_bytes=32768,read_owned=True)
    position=Cursor(path,agent)
    observer._read(position)
    session=position.session
    if session is None or session.skip or session.session_id!=sid:
        return {'messages':[],'coverage':'native-source-unavailable'}
    card=dict(id=row.get('task_id'),conversation='__desktop__',monitoring_only=True,session_id=sid,agent=agent,source='desktop',native_scope=row.get('native_scope'))
    return {'messages':[entry_message(card,e) for e in session.entries], 'coverage':'recent-native-public-text', 'more':True}


def read(config, row, cursor=None, limit=30):
    location = locate(config,row)
    if location is None:
        return None
    home,path = location
    agent,sid = row['agent'],row['native_session_id']
    stat = path.stat()
    offset = 0
    if cursor:
        try:
            if len(cursor) > 1024:
                raise ValueError()
            token = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
            if token['uid'] != row['uid'] or token['inode'] != stat.st_ino or not isinstance(token['offset'], int) or not 0 <= token['offset'] <= stat.st_size:
                raise ValueError()
            offset = token['offset']
        except (ValueError, KeyError, TypeError):
            raise ValueError('Native history changed or cursor scope does not match')
    observer = DesktopSync(_ReadOnly(), config.state_dir, codex_dir=home if agent == 'codex' else None,
                           claude_dir=home if agent == 'claude' else None, content_entries=limit+8,
                           content_bytes=512*1024, read_owned=True)
    position = Cursor(path, agent)
    with path.open('rb') as stream:
        # Re-establish the source identity before following an opaque byte cursor.
        for _ in range(8):
            start = stream.tell()
            line = stream.readline(MAX_LINE_BYTES + 1)
            if not line or len(line) > MAX_LINE_BYTES or not line.endswith(b'\n'):
                break
            observer._line(position, line, stat.st_mtime, start)
            if position.session:
                break
        session = position.session
        if session is None or session.session_id != sid:
            return None
        # Runtime-origin sessions are readable, but remain native protocol inputs;
        # no message is copied into a logical main chat or presented as a new request.
        if session.skip:
            return None
        session.entries.clear()
        stream.seek(offset)
        consumed, dropping, revisions = 0, False, {}
        while consumed < 2*1024*1024 and len(session.entries) < limit:
            start = stream.tell()
            line = stream.readline(MAX_LINE_BYTES + 1)
            if not line:
                break
            consumed += len(line)
            if not line.endswith(b'\n'):
                if len(line) <= MAX_LINE_BYTES and not dropping:
                    stream.seek(start)
                    break
                dropping = True
                continue
            if dropping:
                dropping = False
                continue
            if len(line) <= MAX_LINE_BYTES:
                before = {e['id']: e['text'] for e in session.entries}
                observer._line(position, line, stat.st_mtime, start)
                for entry in session.entries:
                    if before.get(entry['id']) != entry['text']:
                        revisions[entry['id']] = start + 1
            if position.session is not session:
                raise ValueError('Native transcript changed source identity')
        offset = stream.tell()
    card = dict(id=row.get('task_id'), conversation='__desktop__', monitoring_only=True,
                session_id=sid, agent=agent, source='desktop', native_scope=row.get('native_scope'))
    records = []
    for entry in session.entries:
        record = entry_message(card, entry)
        record.update(status='final', seq=revisions.get(entry['id'], 0), rev=revisions.get(entry['id'], 0), native_source_id=row['uid'], input_origin='native_protocol')
        records.append(record)
    more = offset < stat.st_size
    token = {'uid': row['uid'], 'inode': stat.st_ino, 'offset': offset}
    return {'messages': records, 'cursor': base64.urlsafe_b64encode(json.dumps(token).encode()).decode().rstrip('=') if more else None,
            'more': more, 'semantics': 'revision-upserts', 'coverage': 'available-native-public-text',
            'earlier_gaps_possible': session.content_truncated, 'native_inputs_are_not_new_owner_requests': True}
