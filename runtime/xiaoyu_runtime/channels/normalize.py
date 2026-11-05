"""Convert a WeChat update batch into authorized, durable text inputs."""


def _reference(item):
    """Keep only bounded quote metadata, never media keys or nested commands."""
    if 'ref_msg' not in item:
        return None
    ref = item.get('ref_msg')
    if not isinstance(ref, dict):
        return {'kind': 'unavailable'}
    nested = ref.get('message_item') if isinstance(ref.get('message_item'), dict) else {}
    refid = ref.get('svr_id') or nested.get('msg_id')
    if isinstance(refid, bool) or not isinstance(refid, (str, int)) or len(str(refid)) > 128:
        refid = None
    text_item = nested.get('text_item') if isinstance(nested.get('text_item'), dict) else {}
    text = text_item.get('text')
    title = ref.get('title')
    if isinstance(text, str) and text.strip():
        result = {'kind': 'text', 'text': text[:3000], 'truncated': len(text) > 3000}
    elif isinstance(title, str) and title.strip():
        result = {'kind': 'summary', 'text': title[:3000], 'truncated': len(title) > 3000}
    elif nested.get('type') in (2, 3, 4, 5):
        result = {'kind': 'media'}
    else:
        result = {'kind': 'unavailable'}
    if refid is not None and str(refid):
        result['id'] = str(refid)
    if isinstance(ref.get('partial_text'), dict):
        # Without validating the selection against the original, never claim
        # that the whole referenced message is the selected substring.
        result['partial'] = True
    return result


def text_batch(response, owner):
    """Return (messages, cursor); reject unknown formats without advancing.

    An unsupported owner message must remain visible to recovery instead of
    being silently skipped while the channel cursor is advanced.
    """
    if not isinstance(response, dict) or not isinstance(owner, str) or not owner:
        raise ValueError('invalid channel update')
    messages = response.get('msgs', [])
    cursor = response.get('get_updates_buf', '')
    if not isinstance(messages, list) or not isinstance(cursor, str):
        raise ValueError('invalid channel update')
    output = []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError('invalid channel message')
        # Only an authorized owner in a direct chat may trigger a turn. Ignore
        # bot echoes, other users, and group messages without recording them.
        if message.get('message_type') == 2:
            continue
        sender = message.get('from_user_id')
        if not isinstance(sender, str) or not sender:
            raise ValueError('channel sender is unavailable')
        if sender != owner or message.get('group_id'):
            continue
        if message.get('message_type') != 1:
            raise ValueError('unsupported channel message type')
        identity, context = message.get('message_id'), message.get('context_token')
        if (isinstance(identity, bool) or not isinstance(identity, (str, int))
                or not str(identity) or not isinstance(context, str) or not context):
            raise ValueError('channel message lacks identity or reply context')
        items = message.get('item_list')
        if not isinstance(items, list) or not items:
            raise ValueError('unsupported channel message')
        texts = []
        media = []
        quotes = []
        for item in items:
            if isinstance(item, dict) and 'ref_msg' in item:
                if len(quotes) >= 4:
                    raise ValueError('too many referenced messages')
                quotes.append(_reference(item))
            if isinstance(item, dict) and item.get('type') in (2, 4):
                details = item.get('image_item') if item['type'] == 2 else item.get('file_item')
                if not isinstance(details, dict) or not isinstance(details.get('media'), dict) or len(media) >= 4:
                    raise ValueError('unsupported channel message')
                media.append(item)
                continue
            if not isinstance(item, dict) or item.get('type') != 1 or not isinstance(item.get('text_item'), dict):
                raise ValueError('unsupported channel message')
            text = item['text_item'].get('text')
            if not isinstance(text, str) or not text.strip():
                raise ValueError('unsupported channel message')
            texts.append(text)
        normalized = {'id': str(identity), 'sender': owner, 'payload': '\n'.join(texts),
                      'context_token': context}
        if media:
            normalized['media'] = media
        if quotes:
            normalized['quotes'] = quotes
        output.append(normalized)
    return output, cursor
