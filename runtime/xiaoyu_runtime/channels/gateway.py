"""Isolated WeChat ingress for an explicitly bound external account.

The caller is responsible for obtaining and protecting the binding. Nothing in
this module starts a listener or binds a user automatically.
"""
from .store import client_id, public_blocks, public_attachments
from .normalize import text_batch
from .weixin import WeixinError, WeixinPausedError, WeixinTransportError
import json


def _accepted_send(response):
    """A valid success response can omit ret; this means API acceptance only."""
    if not isinstance(response, dict):
        return False
    if 'ret' in response and (type(response['ret']) is not int or response['ret'] != 0):
        return False
    if 'errcode' in response and (type(response['errcode']) is not int or response['errcode'] != 0):
        return False
    if 'ret' not in response and response.get('errmsg') not in (None, ''):
        return False
    return True


class Gateway:
    def __init__(self, store, service, channel, account, owner):
        if not all(isinstance(value, str) and value for value in (channel, account, owner)):
            raise ValueError('explicit channel, account and owner binding required')
        self.store = store
        self.service = service
        self.channel = channel
        self.account = account
        self.owner = owner
        self._projection_after = 0
        self.intake_problem = False

    def flush_delivery(self, destination):
        """Publish a bounded page of statuses, retaining unresolved projections."""
        rows = self.store.dirty_delivery_page(destination, self._projection_after)
        if not rows and self._projection_after:
            self._projection_after = 0
            rows = self.store.dirty_delivery_page(destination, 0)
        errors = 0
        for rowid, block, state, revision in rows:
            self._projection_after = rowid
            prefix = self.service.runtime_id + '/'
            if not block.startswith(prefix) or '#' not in block:
                errors += 1
                continue
            message_id = block[len(prefix):].rsplit('#', 1)[0]
            try:
                text = self.store.delivery_text(destination, block)
                self.service._channel_delivery(message_id, block, text, state, revision)
                self.store.projected_delivery(destination, block, revision)
            except (ValueError, OSError):
                # Keep the durable row for reconciliation; do not copy private
                # block contents or error text into diagnostics.
                errors += 1
        return errors

    def ingest(self, messages, cursor):
        # Do not allow an adapter to route a different sender into the owner's
        # conversation, even if its own filtering was wrong.
        for message in messages:
            if message.get('sender') != self.owner:
                raise ValueError('sender does not match binding')
            if not isinstance(message.get('payload'), str) or (not message['payload'].strip() and not message.get('media')):
                raise ValueError('empty channel message')
        self.store.ingest(self.channel, self.account, messages, cursor)

    def submit_pending(self, transport=None):
        submitted = 0
        self.intake_problem = False
        for channel, account, mid, sender, text in self.store.pending_inbox():
            if (channel, account, sender) != (self.channel, self.account, self.owner):
                continue
            media = self.store.inbox_media(channel, account, mid)
            image_ids = []
            file_ids = []
            if media:
                if transport is None:
                    raise ValueError('channel attachment transport unavailable')
                import io
                from ..events.image_inputs import upload
                from ..world.artifacts import ArtifactError
                from .media import WeixinMediaError
                try:
                    for item in media:
                        data, name = transport.download_media(item)
                        if item.get('type') == 2:
                            image_ids.append(upload(self.service.artifacts, data)['id'])
                        elif item.get('type') == 4:
                            meta = self.service.artifacts._snapshot('owner-weixin', io.BytesIO(data), name, 'owner')
                            file_ids.append(meta['id'])
                        else:
                            raise WeixinMediaError('不支持这种微信附件')
                except (WeixinMediaError, WeixinTransportError, ArtifactError, OSError):
                    # Retain this private input for recovery, while allowing
                    # unrelated later messages to proceed through the inbox.
                    self.intake_problem = True
                    continue
            accepted = self.service.submit(text, conversation='weixin',
                                           client_id=client_id(channel, account, mid),
                                           _channel_authorized=True, **({'image_ids': image_ids} if image_ids else {}),
                                           **({'_channel_file_ids': file_ids} if file_ids else {}))
            # If a crash happens before this write, Runtime deduplicates the
            # identical retry by client_id and returns the original identity.
            self.store.accepted(channel, account, mid, accepted['id'])
            submitted += 1
        return submitted

    def poll_once(self, transport, cancelled=None):
        """Replay durable intake, then accept one long-poll batch atomically."""
        if cancelled and cancelled():
            return 0
        submitted = self.submit_pending(transport)
        cursor = self.store.cursor('in:' + self.channel + ':' + self.account)
        updates = transport.updates(cursor)
        if cancelled and cancelled():
            return submitted
        messages, next_cursor = text_batch(updates, self.owner)
        self.ingest(messages, next_cursor)
        if cancelled and cancelled():
            return submitted
        return submitted + self.submit_pending(transport)

    def bootstrap_sync(self, destination, snapshot):
        """Bind delivery to the current Runtime boundary, never replay history."""
        if (not isinstance(snapshot, dict) or not isinstance(snapshot.get('snapshot'), dict)
                or snapshot['snapshot'].get('seq') != snapshot.get('seq') or snapshot.get('more')):
            raise ValueError('channel bootstrap requires a Runtime snapshot')
        runtime = snapshot.get('runtime_id')
        if not isinstance(runtime, str) or not runtime:
            raise ValueError('channel sync lacks runtime identity')
        self.store.bootstrap_sync(destination, snapshot.get('epoch'), snapshot.get('seq'), runtime_id=runtime)

    def _bind_task(self, destination, task, expected_run=None):
        if not isinstance(task, dict) or task.get('conversation') != 'weixin':
            return
        task_id, run_id, origin = task.get('id'), task.get('run_id'), task.get('origin_message')
        if not all(isinstance(value, str) and value for value in (task_id, run_id, origin)) or not origin.endswith(':you'):
            return
        if expected_run is not None and expected_run != run_id:
            return
        request = origin[:-4]
        address = self.store.reply_address(self.channel, self.account, request)
        if not address or address[0] != self.owner:
            raise ValueError('channel task origin is not yet recorded')
        self.store.remember_task(destination, task_id, run_id, request)

    def send_next(self, transport, destination):
        """Send one stored block; never repeat an uncertain network effect."""
        entry = self.store.next_outgoing(destination)
        if entry is None:
            return 'idle'
        block, text = entry
        request = self.store.outgoing_request(destination, block)
        address = self.store.reply_address(self.channel, self.account, request)
        if not address or address[0] != self.owner:
            raise ValueError('channel reply address unavailable')
        if self.store.send_delay(destination, request, block, channel=self.channel, account=self.account, sender=self.owner) > 0:
            return 'paced'
        claimed = self.store.begin_send(destination, block, channel=self.channel, account=self.account, sender=self.owner)
        if claimed != 'sending':
            return claimed
        try:
            if self.store.outgoing_kind(destination, block) == 'media':
                from .media import MAX_MEDIA_BYTES
                from ..world.artifacts import ArtifactError
                import hashlib
                try:
                    stream, meta, mime, size = self.service.artifacts.open_content(text)
                    with stream:
                        data = stream.read(MAX_MEDIA_BYTES + 1)
                except (ArtifactError, OSError):
                    raise WeixinError('微信附件不可用') from None
                if (meta.get('source') == 'owner' or size != len(data) or size > MAX_MEDIA_BYTES
                        or hashlib.sha256(data).hexdigest() != meta.get('sha256')):
                    raise WeixinError('微信附件不可用或超过大小限制')
                response = transport.send_media(address[0], address[1],
                                                client_id(self.channel, self.account, block),
                                                data, meta['name'], mime)
            else:
                response = transport.send_text(address[0], address[1],
                                               client_id(self.channel, self.account, block), text)
        except (WeixinTransportError, WeixinPausedError):
            self.store.mark(destination, block, 'sending', 'unknown', outcome='transport_uncertain')
            return 'unknown'
        except WeixinError:
            self.store.mark(destination, block, 'sending', 'failed', outcome='upstream_error')
            return 'failed'
        except Exception:
            self.store.mark(destination, block, 'sending', 'unknown', outcome='unexpected_error')
            raise
        state = 'sent' if _accepted_send(response) else 'unknown'
        outcome = ('api_accepted_no_ret' if state == 'sent' and 'ret' not in response else
                   'api_accepted' if state == 'sent' else 'invalid_response')
        server_id = response.get('message_id') if state == 'sent' and isinstance(response, dict) else None
        if (isinstance(server_id, bool) or not isinstance(server_id, (str, int))
                or not 0 < len(str(server_id)) <= 128):
            server_id = None
        self.store.mark(destination, block, 'sending', state, outcome=outcome, server_message_id=server_id)
        return state

    def send_proactive_share(self, transport, destination):
        """Send a separately journaled share; it never blocks ordinary replies."""
        entry = self.store.claim_proactive_share(self.channel, self.account, self.owner, destination)
        if entry is None:
            return 'idle'
        share_id, text, context = entry
        try:
            response = transport.send_text(self.owner, context,
                                           client_id(self.channel, self.account, 'proactive:' + share_id), text)
        except (WeixinTransportError, WeixinPausedError):
            self.store.finish_proactive_share(share_id, 'unknown', 'transport_uncertain')
            return 'unknown'
        except WeixinError:
            self.store.finish_proactive_share(share_id, 'failed', 'upstream_error')
            return 'failed'
        except Exception:
            self.store.finish_proactive_share(share_id, 'unknown', 'unexpected_error')
            raise
        state = 'sent' if _accepted_send(response) else 'unknown'
        self.store.finish_proactive_share(share_id, state, 'api_accepted' if state == 'sent' else 'invalid_response')
        return state

    def send_proactive_link(self, transport, destination):
        """Send one separately journaled link after the recommendation."""
        entry = self.store.claim_proactive_link(self.channel, self.account, self.owner, destination)
        if entry is None:
            return 'idle'
        share_id, url, context = entry
        try:
            response = transport.send_text(self.owner, context,
                client_id(self.channel, self.account, 'proactive-link:' + share_id), url)
        except (WeixinTransportError, WeixinPausedError):
            self.store.finish_proactive_link(share_id, 'unknown', 'transport_uncertain')
            return 'unknown'
        except WeixinError:
            self.store.finish_proactive_link(share_id, 'failed', 'upstream_error')
            return 'failed'
        except Exception:
            self.store.finish_proactive_link(share_id, 'unknown', 'unexpected_error')
            raise
        state = 'sent' if _accepted_send(response) else 'unknown'
        self.store.finish_proactive_link(share_id, state, 'api_accepted' if state == 'sent' else 'invalid_response')
        return state

    def pacing_delay(self, destination):
        entry = self.store.next_outgoing(destination)
        if not entry:
            return 0.0
        return self.store.send_delay(destination, self.store.outgoing_request(destination, entry[0]), entry[0],
                                     channel=self.channel, account=self.account, sender=self.owner)

    def apply_sync(self, destination, snapshot):
        """Queue only replies whose canonical turns map to accepted WeChat input."""
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get('messages'), list):
            raise ValueError('invalid channel sync')
        runtime = snapshot.get('runtime_id')
        if not isinstance(runtime, str) or not runtime:
            raise ValueError('channel sync lacks runtime identity')
        saved = self.store.cursor('sync:' + destination)
        try:
            position = json.loads(saved)
            if (len(position) != 3 or position[0] != runtime or position[1] != snapshot.get('epoch')
                    or type(snapshot.get('seq')) is not int or snapshot['seq'] < position[2] or snapshot.get('reset')):
                raise ValueError()
        except (ValueError, TypeError, IndexError):
            raise ValueError('channel sync requires reconciliation') from None
        mappings = {}
        tasks = snapshot.get('tasks', [])
        if not isinstance(tasks, list):
            raise ValueError('invalid channel tasks projection')
        for task in tasks:
            self._bind_task(destination, task)
        for message in snapshot['messages']:
            if not isinstance(message, dict):
                raise ValueError('invalid channel message projection')
            p = message.get('presentation') or {}
            if p.get('conversation') != 'weixin' or (p.get('author') or {}).get('kind') != 'xiaoyou':
                continue
            turn = p.get('turn_id')
            if not isinstance(turn, str) or not turn.startswith(runtime + '/'):
                if public_blocks(message) or public_attachments(message):
                    raise ValueError('channel message source requires reconciliation')
                continue
            request = turn[len(runtime) + 1:]
            address = self.store.reply_address(self.channel, self.account, request)
            if address and address[0] == self.owner:
                mappings[turn] = request
            elif message.get('kind') in ('summary', 'result'):
                # Summary turn IDs encode the run; require an exact, previously
                # persisted task/run binding to an authorized incoming turn.
                task_id = message.get('task')
                run_id = (request[:-len('-recovery-summary')] if request.endswith('-recovery-summary') else
                          request[:-len('-summary')] if request.endswith('-summary') else '')
                origin_request = self.store.task_request(destination, task_id, run_id) if task_id and run_id else None
                if not origin_request and task_id and run_id:
                    # A sync page can contain the result before the later task
                    # update. The current run is safe to inspect, but a replaced
                    # run must never be guessed from its card's latest origin.
                    cards = getattr(self.service, 'card_store', None)
                    if cards is not None:
                        self._bind_task(destination, cards.get(task_id), expected_run=run_id)
                        origin_request = self.store.task_request(destination, task_id, run_id)
                address = self.store.reply_address(self.channel, self.account, origin_request) if origin_request else None
                if not address or address[0] != self.owner:
                    raise ValueError('channel task result requires origin reconciliation')
                mappings[turn] = origin_request
            elif public_blocks(message) or public_attachments(message):
                # The Runtime can publish before inbox acceptance is persisted.
                # Do not advance past this reply until that association exists.
                raise ValueError('channel turn origin is not yet recorded')
        self.store.apply_sync(destination, snapshot.get('epoch'), snapshot.get('seq'),
                              snapshot['messages'], reset=snapshot.get('reset', False),
                              reply_turns=mappings, runtime_id=runtime)
