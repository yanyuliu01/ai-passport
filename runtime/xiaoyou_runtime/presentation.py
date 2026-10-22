"""The shared, deterministic display policy. Reads never write or run a model.

Wire records remain canonical. This additive projection is disposable and uses
origin identities rather than a replica's local card IDs or replay sequence.
"""
import hashlib
import json
import re

VERSION = 1
TERMINAL = frozenset(('final', 'done', 'failed', 'cancelled'))
LABELS = {
    'accepted': '已接收', 'queued': '排队中', 'running': '执行中',
    'streaming': '正在回复', 'waiting': '等待决定', 'final': '已回复',
    'done': '已完成', 'failed': '未完成', 'cancelled': '已停止',
    'unavailable': '当前不可用', 'unknown': '状态待确认',
    'turn_ended': '本轮已结束',
}


def policy():
    return {'version': VERSION, 'capability': 'presentation_v1',
            'message_mode': 'complete-blocks',
            'native_surfaces': {
                name: {'status': 'incomplete', 'reason': 'native-rendering-not-verified',
                       'dimensions': ['message-boundaries', 'placement', 'status', 'continuation']}
                for name in ('codex', 'claude')}}


def identity(message, runtime_id=''):
    origin = message.get('mesh_origin') or (message.get('source') or {}).get('runtime_id') or runtime_id
    key = message.get('id') or ''
    prefix = str(origin)[:12] + '.'
    if message.get('mesh_origin') and key.startswith(prefix):
        key = key[len(prefix):]
    return str(origin) + '/' + key


def _author(message):
    author = message.get('author') or {}
    name = author.get('name') or 'assistant'
    kind = author.get('kind') or 'agent'
    native_input = message.get('input_origin') == 'native_protocol' and kind == 'owner'
    label = ('原生会话输入' if native_input else '主人') if kind == 'owner' else {
        'xiaoyou': '小幽', 'codex': 'Codex', 'claude': 'Claude',
        'cc': 'Claude', 'claude_code': 'Claude', 'assistant': '助手',
    }.get(name, name)
    return {'kind': kind, 'name': name, 'label': label}


def _parts(text, split):
    """Legacy Runtime message markers, never separators inside fenced code.

    Native messages and owner input preserve their upstream item boundaries.
    Newlines within an item do not create another message.
    """
    if not split:
        return [(text, False)] if text else []
    parts, lines, fence = [], [], None
    for line in text.splitlines(keepends=True):
        mark = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\r\n'))
        if mark:
            token, rest = mark.groups()
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not rest.strip():
                fence = None
        if fence is None and re.fullmatch(r'[ \t]*-{3,}[ \t]*(?:\r?\n)?', line):
            value = ''.join(lines).strip()
            if value:
                parts.append((value, True))
            lines = []
        else:
            lines.append(line)
    value = ''.join(lines).strip()
    if value:
        parts.append((value, False))
    return parts


def _runs(text, formatted):
    if not formatted:
        return [{'text': text, 'bold': False}]
    runs, fence = [], None
    for index, line in enumerate(text.split('\n')):
        if index:
            runs.append({'text': '\n', 'bold': False})
        mark = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        code = fence is not None or mark is not None
        if mark:
            token, rest = mark.groups()
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not rest.strip():
                fence = None
        if code:
            runs.append({'text': line, 'bold': False})
            continue
        line = re.sub(r'^\s*[-*•·]\s+', '•  ', line)
        at = 0
        for bold in re.finditer(r'\*\*(.+?)\*\*', line):
            if bold.start() > at:
                runs.append({'text': line[at:bold.start()], 'bold': False})
            runs.append({'text': bold.group(1), 'bold': True})
            at = bold.end()
        if at < len(line):
            runs.append({'text': line[at:], 'bold': False})
    return runs


def message(record, runtime_id=''):
    result = dict(record)
    key = identity(record, runtime_id)
    author = _author(record)
    source = (record.get('source') or {}).get('type', 'runtime')
    status = record.get('status') or 'final'
    terminal = status in TERMINAL
    text = record.get('text') or ''
    if not terminal:
        text = record.get('partial') or ''
    placement = ('source' if source in ('desktop', 'external') else
                 'work' if record.get('scope') == 'task' or record.get('kind') == 'activity'
                 else 'chat')
    split = source == 'runtime' and author['kind'] != 'owner' and record.get('kind') != 'activity'
    blocks = [{'id': key + '#%d' % index, 'text': part, 'complete': terminal or closed}
              for index, (part, closed) in enumerate(_parts(text, split))]
    for block in blocks:
        block['runs'] = _runs(block['text'], author['kind'] != 'owner' and record.get('kind') != 'activity')
    # A source-owned revision fingerprint is independent of local mesh seq/rev.
    revision = {name: record.get(name) for name in
                ('conversation', 'scope', 'author', 'kind', 'status', 'text', 'partial', 'attachments', 'input_origin')}
    digest = hashlib.sha256(json.dumps(revision, sort_keys=True, ensure_ascii=False,
                                      separators=(',', ':')).encode()).hexdigest()[:20]
    reply_to = record.get('reply_to')
    turn = identity(dict(record, id=reply_to), runtime_id) if reply_to else key
    if turn.endswith(':you'):
        turn = turn[:-4]
    elif not reply_to and turn.endswith(':reply'):
        turn = turn[:-6]
    result['presentation'] = {
        'version': VERSION, 'input_revision': digest, 'id': key, 'turn_id': turn,
        'conversation': record.get('conversation') or 'default', 'author': author,
        'kind': record.get('kind') or 'reply', 'placement': placement,
        'status': status, 'status_label': LABELS.get(status, LABELS['unknown']),
        'complete': terminal, 'blocks': blocks,
        'order': [record.get('turn_at', record.get('created_at')) or 0, turn, 0 if author['kind'] == 'owner' else 1, key],
    }
    return result


def task(record):
    result = dict(record)
    state = ('unavailable' if record.get('stale') and record.get('state') in ('working', 'talking', 'waiting', 'unknown')
             else 'queued' if record.get('queued') and record.get('state') in ('working','talking')
             else record.get('state') or 'unknown')
    state = {'working': 'running', 'talking': 'streaming'}.get(state, state)
    if state == 'done' and (record.get('monitoring_only') or record.get('source') in ('desktop', 'external')):
        state = 'turn_ended'
    result['presentation'] = {'version': VERSION, 'status': state,
                              'status_label': LABELS.get(state, LABELS['unknown']),
                              'group': 'waiting' if state == 'waiting' else 'running' if state in ('running','streaming','queued','accepted')
                              else 'uncertain' if state in ('unavailable','unknown') else 'history'}
    return result


def card(record, runtime_id='', lookup=None):
    from .message_log import entry_message
    result = task(record)
    result['entries'] = []
    for entry in record.get('entries', []):
        shown = dict(entry)
        if entry.get('id'):
            canonical = (lookup(entry['id']) if lookup else None) or entry_message(record, entry)
            shown['presentation'] = message(canonical, runtime_id)['presentation']
        # Unnumbered legacy entries keep their position; reads do not invent IDs.
        result['entries'].append(shown)
    return result


def history(page, runtime_id=''):
    return dict(page, presentation_policy=policy(),
                messages=[message(item, runtime_id) for item in page.get('messages', [])])


def response(record, runtime_id=''):
    """Project the same reply on SSE/polling and sync, including empty retractions."""
    from .message_log import MessageLog
    reply = MessageLog._request_record(record, ':reply')
    status = record.get('status')
    reply.update(status={'done': 'final', 'failed': 'failed'}.get(status, 'streaming' if status == 'running' else 'queued'),
                 text=record.get('reply') if status == 'done' else record.get('error') or '',
                 partial=record.get('partial') or '')
    return message(reply, runtime_id)['presentation']
