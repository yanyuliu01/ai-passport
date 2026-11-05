"""The shared, deterministic display policy. Reads never write or run a model.

Wire records remain canonical. This additive projection is disposable and uses
origin identities rather than a replica's local card IDs or replay sequence.
"""
import hashlib
import json
import re

from ..events.message_parts import parts as _parts

VERSION = 1
TERMINAL = frozenset(('final', 'done', 'failed', 'cancelled'))
LABELS = {
    'accepted': '已接收', 'queued': '排队中', 'running': '执行中',
    'streaming': '正在回复', 'waiting': '等待决定', 'final': '已回复',
    'done': '已完成', 'failed': '未完成', 'cancelled': '已停止',
    'unavailable': '当前不可用', 'unknown': '状态待确认',
    'turn_ended': '本轮已结束',
}
# A complete reply the owner cut into: the words are final, only part was said.
INTERRUPTED_LABEL = '已打断'


FORMATTING = ('bold', 'italic', 'strike', 'code', 'code_block', 'heading', 'link', 'quote', 'list')
EVENTS = ('said', 'assistant_message', 'task_progress', 'session_observed')
# One inline mark at a time, earliest first. Code spans keep their content literal.
INLINE = re.compile(r'(?P<code>`+)(?P<code_text>.+?)(?P=code)'
                    r'|\[(?P<link_text>[^\]\n]+)\]\((?P<href>(?:[^()\s]|\([^()\s]*\))+)\)'
                    r'|\*\*(?P<bold>.+?)\*\*'
                    r'|~~(?P<strike>.+?)~~'
                    r'|(?<![*\w])\*(?![\s*])(?P<italic>.+?)(?<![\s*])\*(?![*\w])')


def policy():
    return {'version': VERSION, 'capability': 'presentation_v1',
            'message_mode': 'complete-blocks',
            # Every run still carries text and bold: older clients drop markers but lose no words.
            'formatting': list(FORMATTING), 'events': list(EVENTS),
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
        'xiaoyu': '小鱼', 'xiaoyou': '小鱼', 'codex': 'Codex', 'claude': 'Claude',
        'cc': 'Claude', 'claude_code': 'Claude', 'assistant': '助手',
    }.get(name, name)
    return {'kind': kind, 'name': name, 'label': label}



def _run(text, style):
    run = {'text': text, 'bold': bool(style.get('bold'))}
    run.update((key, value) for key, value in style.items() if key != 'bold' and value)
    return run


def _inline(text, style):
    """Markers become styles on runs; the markers themselves are never shown."""
    runs, at = [], 0
    for mark in INLINE.finditer(text):
        if mark.start() > at:
            runs.append(_run(text[at:mark.start()], style))
        if mark.group('code'):
            runs.append(_run(mark.group('code_text'), dict(style, code='inline')))
        elif mark.group('href'):
            # Only links a client can open safely; anything else stays plain text.
            href = mark.group('href') if re.match(r'(?i)(https?://|mailto:)', mark.group('href')) else None
            runs.extend(_inline(mark.group('link_text'), dict(style, href=href)))
        else:
            name = next(key for key in ('bold', 'strike', 'italic') if mark.group(key) is not None)
            runs.extend(_inline(mark.group(name), dict(style, **{name: True})))
        at = mark.end()
    if at < len(text):
        runs.append(_run(text[at:], style))
    return runs


def _runs(text, formatted):
    if not formatted:
        return [{'text': text, 'bold': False}]
    lines, fence, language = [], None, ''
    for line in text.split('\n'):
        mark = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if mark:
            token, rest = mark.groups()
            if fence is None:
                fence, language = (token[0], len(token)), rest.strip()
                continue  # The fence line is markup, not content.
            if token[0] == fence[0] and len(token) >= fence[1] and not rest.strip():
                fence = None
                continue
        if fence is not None:
            lines.append([_run(line, {'code': 'block', 'language': language})])
            continue
        style = {}
        quote = re.match(r'^ {0,3}>\s?', line)
        if quote:
            line, style['quote'] = line[quote.end():], True
        heading = re.match(r'^ {0,3}(#{1,6})\s+', line)
        if heading:
            # Bold as well, so a client that only knows bold still sets it apart.
            line = re.sub(r'\s+#+\s*$', '', line[heading.end():])
            style.update(heading=len(heading.group(1)), bold=True)
        line = re.sub(r'^\s*[-*•·]\s+', '•  ', line)
        lines.append(_inline(line, style) or [_run('', style)])
    runs = []
    for index, line in enumerate(lines):
        if index:
            runs.append({'text': '\n', 'bold': False})
        runs.extend(run for run in line if run['text'])
    return runs


def plain(runs):
    return ''.join(run['text'] for run in runs)


def message(record, runtime_id=''):
    result = dict(record)
    delivery_revisions = result.pop('_delivery_revisions', {})
    delivery_hashes = result.pop('_delivery_hashes', {})
    key = identity(record, runtime_id)
    author = _author(record)
    source = (record.get('source') or {}).get('type', 'runtime')
    status = record.get('status') or 'final'
    terminal = status in TERMINAL
    text = record.get('text') or ''
    hidden_empty_interrupt = (terminal and record.get('interrupted') is True and source == 'runtime'
                              and author['kind'] == 'xiaoyou' and text.strip() == '……')
    if not terminal:
        text = record.get('partial') or ''
    elif hidden_empty_interrupt:
        # An empty interrupted provider turn uses this internal sentinel; it
        # is not speech and must not become a bubble or an external send.
        text = ''
    placement = ('source' if source in ('desktop', 'external') else
                 'work' if record.get('scope') == 'task' or record.get('kind') == 'activity'
                 else 'chat')
    split = source == 'runtime' and author['kind'] != 'owner' and record.get('kind') != 'activity'
    blocks = [{'id': key + '#%d' % index, 'text': part, 'complete': terminal or closed}
              for index, (part, closed) in enumerate(_parts(text, split))]
    if isinstance(result.get('delivery'), dict):
        delivery_items = [(block['id'], block['text']) for block in blocks if block['complete']]
        if terminal:
            delivery_items.extend((key + '#media:' + item['id'], item['id'])
                                  for item in record.get('attachments', [])
                                  if isinstance(item, dict) and isinstance(item.get('id'), str)
                                  and item.get('status') == 'ready' and item.get('source') != 'owner')
        result['delivery'] = {block_id: result['delivery'][block_id]
                              for block_id, value in delivery_items if block_id in result['delivery']
                              and isinstance(delivery_hashes, dict)
                              and delivery_hashes.get(block_id) == hashlib.sha256(value.encode('utf-8')).hexdigest()}
        if not result['delivery']:
            result.pop('delivery')
        elif isinstance(delivery_revisions, dict):
            result['delivery_revisions'] = {block: delivery_revisions[block] for block in result['delivery']
                                            if type(delivery_revisions.get(block)) is int
                                            and delivery_revisions[block] > 0}
    hidden_channel_blocks = []
    if record.get('conversation') == 'weixin' and author['kind'] == 'xiaoyou':
        delivery = result.get('delivery') or {}
        hidden_channel_blocks = [block['id'] for block in blocks if delivery.get(block['id']) == 'cancelled']
        if hidden_channel_blocks:
            blocks = [block for block in blocks if block['id'] not in hidden_channel_blocks]
    for block in blocks:
        block['runs'] = _runs(block['text'], author['kind'] != 'owner' and record.get('kind') != 'activity')
    # Event kinds of the refactor design (1.2): what this record is, not how it is drawn.
    event = ('session_observed' if placement == 'source' else
             'said' if author['kind'] == 'owner' else
             'assistant_message' if placement == 'chat' else
             'task_progress' if placement == 'work' else 'session_observed')
    # A source-owned revision fingerprint is independent of local mesh seq/rev.
    revision = {name: record.get(name) for name in
                ('conversation', 'scope', 'author', 'kind', 'status', 'text', 'partial', 'attachments', 'input_origin')}
    interrupted = terminal and record.get('interrupted') is True
    if interrupted:
        revision['interrupted'] = True  # Only when set, so existing revisions stay stable.
    if hidden_empty_interrupt:
        revision['hidden_empty_interrupt'] = True
    if hidden_channel_blocks:
        revision['hidden_channel_blocks'] = hidden_channel_blocks
    channel_tracked = record.get('conversation') == 'weixin' and record.get('channel_delivery_tracked') is True
    if channel_tracked:
        revision['channel_delivery_tracked'] = True
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
        'kind': record.get('kind') or 'reply', 'event': event, 'placement': placement,
        'status': status,
        'status_label': INTERRUPTED_LABEL if interrupted else LABELS.get(status, LABELS['unknown']),
        'complete': terminal, 'blocks': blocks,
        **({'interrupted': True} if interrupted else {}),
        **({'channel_delivery_tracked': True} if channel_tracked else {}),
        # Unstyled text for one-line places (home card, device screen, copying).
        'plain': '\n\n'.join(plain(block['runs']) for block in blocks),
        'order': [record.get('turn_at', record.get('created_at')) or 0, turn, 0 if author['kind'] == 'owner' else 1, key],
    }
    if record.get('conversation') == 'weixin' and result.get('delivery'):
        # Older mobile clients retain the presentation contract on both task
        # entries and standalone messages; keep the status with that view.
        result['presentation']['channel_delivery'] = dict(result['delivery'])
        result['presentation']['channel_delivery_revisions'] = dict(result.get('delivery_revisions') or {})
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
    for name in ('brief', 'progress'):
        # Xiaoyu's own words on the card: the home card shows them without markers.
        if isinstance(record.get(name), str) and record[name]:
            result['presentation'][name] = plain(_runs(record[name], True))
    return result


def card(record, runtime_id='', lookup=None):
    from ..events.message_log import entry_message
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
    from ..events.message_log import MessageLog
    reply = MessageLog._request_record(record, ':reply')
    status = record.get('status')
    reply.update(status={'done': 'final', 'failed': 'failed'}.get(status, 'streaming' if status == 'running' else 'queued'),
                 text=record.get('reply') if status == 'done' else record.get('error') or '',
                 partial=record.get('partial') or '')
    if status == 'done' and record.get('interrupted') is True:
        reply['interrupted'] = True
    return message(reply, runtime_id)['presentation']
