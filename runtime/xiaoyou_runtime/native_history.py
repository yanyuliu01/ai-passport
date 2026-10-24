"""Read one known native source, without resuming it or changing its files."""
import base64
import json
import os
import re
from pathlib import Path

from .conversation_directory import uid, agent_home, home_scope
from .desktop_sync import Cursor, DesktopSync, MAX_LINE_BYTES
from .message_log import entry_message

MAX_EXECUTION_BYTES = 16 * 1024 * 1024


def execution(spec, binding):
    """Read a completed, identity-bound Codex turn without resuming a session.

    Observation history is deliberately insufficient evidence: commentary, a
    final-looking message, or another turn's completion cannot settle a run.
    Older/unsupported formats and sources beyond the bounded scan stay unknown.
    """
    if spec.type != 'codex':
        return None
    sid, turn = binding.get('native_session_id'), binding.get('native_turn_id')
    if (not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', sid)
            or not isinstance(turn, str) or not turn):
        return None
    home = agent_home(spec)
    if home_scope(home) != binding.get('native_scope'):
        return None
    root = home / 'sessions'
    paths = [p for p in root.rglob('*' + sid + '.jsonl')
             if 'subagents' not in p.parts and not p.is_symlink()
             and p.resolve().is_relative_to(root.resolve())]
    if len(paths) != 1:
        return None
    path = paths[0]
    with path.open('rb') as stream:
        original = os.fstat(stream.fileno())
        if original.st_size > MAX_EXECUTION_BYTES:
            return None
        used, identity, active, answer, completed = 0, False, None, '', None
        while used < original.st_size:
            line = stream.readline(min(MAX_LINE_BYTES + 1, original.st_size - used))
            used += len(line)
            if not line or len(line) > MAX_LINE_BYTES or not line.endswith(b'\n'):
                return None
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                return None
            if not isinstance(event, dict) or not isinstance(event.get('payload'), dict):
                return None
            payload, kind = event['payload'], event.get('type')
            if kind == 'session_meta':
                source = payload.get('source')
                if (payload.get('id') != sid or payload.get('parent_thread_id')
                        or isinstance(source, dict) and 'subagent' in source):
                    return None
                identity = True
                continue
            if not identity:
                return None
            if kind == 'event_msg':
                event_type, tid = payload.get('type'), payload.get('turn_id')
                if event_type == 'task_started':
                    if tid == turn and (active == turn or completed is not None):
                        return None  # Ambiguous duplicate/reused turn identity.
                    active, answer = tid, ''
                elif event_type in ('turn_aborted', 'task_failed'):
                    if tid == turn or active == turn:
                        return None
                    active, answer = None, ''
                elif event_type == 'task_complete':
                    if active == turn and tid == turn:
                        text = payload.get('last_agent_message')
                        text = text.strip() if isinstance(text, str) else answer
                        if not text:
                            return None
                        if answer and text != answer:
                            return None
                        completed = text
                    elif tid == turn:
                        return None
                    active, answer = None, ''
            elif kind == 'turn_context':
                # A context change with no matching start/end range is a gap.
                tid = payload.get('turn_id')
                if active == turn and tid not in (None, turn):
                    return None
            elif kind == 'response_item' and active == turn:
                if (payload.get('type') == 'message' and payload.get('role') == 'assistant'
                        and payload.get('phase') in ('final', 'final_answer')
                        and payload.get('channel') in (None, '', 'final', 'final_answer')):
                    from .desktop_content import message_text
                    answer = message_text(payload.get('content'))
        latest = os.fstat(stream.fileno())
        # Appends are safe only on a subsequent query of a stable snapshot.
        if (latest.st_size, latest.st_mtime_ns, latest.st_ino) != (
                original.st_size, original.st_mtime_ns, original.st_ino):
            return None
    if completed is None:
        return None
    try:
        completed.encode('utf-8')
    except UnicodeError:
        return None
    from .desktop_content import redact_credentials
    return redact_credentials(completed)


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
