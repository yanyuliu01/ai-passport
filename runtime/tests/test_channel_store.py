import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from xiaoyu_runtime.delivery import presentation
from xiaoyu_runtime.channels.store import ChannelStore, client_id, public_blocks, public_attachments, reading_pause
from xiaoyu_runtime.channels.gateway import Gateway, _accepted_send
from xiaoyu_runtime.api.service import Service as RuntimeService, RequestError
from xiaoyu_runtime.world.conversations import ConversationCatalog
from xiaoyu_runtime.channels.weixin import WeixinError, WeixinTransportError
from test_runtime import Scripted, make_xiaoyu


class ChannelStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'channel.sqlite'
        self.store = ChannelStore(self.path)
        self.addCleanup(self.store.close)

    def message(self, **changes):
        record = dict(id='reply', conversation='weixin', scope='chat',
                      author={'kind': 'xiaoyou', 'name': 'xiaoyou'}, kind='reply',
                      status='done', text='你好', created_at=time.time())
        record.update(changes)
        return presentation.message(record, 'runtime')

    def test_public_weixin_only_and_complete_blocks(self):
        self.assertEqual(public_blocks(self.message()), [('runtime/reply#0', '你好')])
        self.assertFalse(public_blocks(self.message(conversation='side-private')))
        self.assertFalse(public_blocks(self.message(conversation='default')))
        self.assertFalse(public_blocks(self.message(scope='task')))
        self.assertFalse(public_blocks(self.message(author={'kind': 'agent', 'name': 'codex'})))
        self.assertFalse(public_blocks(self.message(status='running', partial='未完成')))
        self.assertFalse(public_blocks({'text': 'no presentation'}))

    def test_weixin_two_bubbles_preserve_order_and_lists_and_code(self):
        from xiaoyu_runtime.loop.xiaoyu import weixin_paragraph_bubbles
        text = weixin_paragraph_bubbles('好呀，这版可以试试\n\n你想先看哪一块？', '试试')
        reply = self.message(text=text)
        self.assertEqual(public_blocks(reply), [
            ('runtime/reply#0', '好呀，这版可以试试'),
            ('runtime/reply#1', '你想先看哪一块？')])
        self.store.bootstrap_sync('owner', 'epoch', 1, runtime_id='runtime')
        partial = self.message(status='streaming', text='', partial='好呀，这版可以试试\n\n你想先看')
        self.store.apply_sync('owner', 'epoch', 2, [partial],
                              reply_turns={'runtime/reply': 'turn'}, runtime_id='runtime')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 0)
        for seq in (3, 4):
            self.store.apply_sync('owner', 'epoch', seq, [reply],
                                  reply_turns={'runtime/reply': 'turn'}, runtime_id='runtime')
        self.assertEqual(self.store.db.execute('SELECT text FROM outbox ORDER BY rowid').fetchall(),
                         [('好呀，这版可以试试',), ('你想先看哪一块？',)])
        listing = '三个点子：\n\n1. 走走\n2. 看书\n3. 休息'
        self.assertEqual(public_blocks(self.message(text=listing)), [('runtime/reply#0', listing)])
        code = '代码：\n```\na\n---\nb\n```'
        self.assertEqual(public_blocks(self.message(text=code)), [('runtime/reply#0', code)])

    def test_interrupted_empty_reply_does_not_enter_outbox(self):
        self.store.bootstrap_sync('owner', 'epoch', 1, runtime_id='runtime')
        empty_cut = self.message(id='cut', text='……', interrupted=True)
        self.store.apply_sync('owner', 'epoch', 2, [empty_cut],
                              reply_turns={'runtime/cut': 'cut'}, runtime_id='runtime')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 0)
        partial = self.message(id='partial', text='先说一半', interrupted=True)
        natural = self.message(id='natural', text='……')
        self.store.apply_sync('owner', 'epoch', 3, [partial, natural],
                              reply_turns={'runtime/partial': 'partial', 'runtime/natural': 'natural'}, runtime_id='runtime')
        self.assertEqual(self.store.db.execute('SELECT text FROM outbox ORDER BY rowid').fetchall(),
                         [('先说一半',), ('……',)])

    def test_only_final_public_assistant_attachments_are_enqueued_once_per_turn(self):
        artifact = {'id': 'a' + '1' * 32, 'source': 'cc', 'status': 'ready'}
        incoming = dict(artifact, id='a' + '2' * 32, source='owner')
        message = self.message(attachments=[artifact, artifact, incoming])
        self.assertEqual(public_attachments(message), [artifact['id']])
        self.assertFalse(public_attachments(self.message(attachments=[artifact], conversation='default')))
        self.assertFalse(public_attachments(self.message(attachments=[artifact], status='running')))
        self.store.bootstrap_sync('owner', 'epoch', 1, runtime_id='runtime')
        self.store.apply_sync('owner', 'epoch', 2, [message],
                              reply_turns={'runtime/reply': 'turn'}, runtime_id='runtime')
        self.store.apply_sync('owner', 'epoch', 3, [message],
                              reply_turns={'runtime/reply': 'turn'}, runtime_id='runtime')
        media = self.store.db.execute("SELECT block_id,text FROM outbox WHERE kind='media'").fetchall()
        self.assertEqual(media, [('runtime/reply#media:' + artifact['id'], artifact['id'])])

    def test_upgrade_never_replays_old_message_attachments_but_new_replies_can_send_them(self):
        artifact = {'id': 'a' + '1' * 32, 'source': 'cc', 'status': 'ready'}
        self.store.bootstrap_sync('owner', 'epoch', 1, runtime_id='runtime')
        prior = self.message(created_at=time.time() - 3600, attachments=[artifact])
        self.store.apply_sync('owner', 'epoch', 2, [prior], reply_turns={'runtime/reply': 'old'}, runtime_id='runtime')
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM outbox WHERE kind='media'").fetchone()[0], 0)
        fresh = self.message(id='new', created_at=time.time() + 1, attachments=[artifact])
        self.store.apply_sync('owner', 'epoch', 3, [fresh], reply_turns={'runtime/new': 'new'}, runtime_id='runtime')
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM outbox WHERE kind='media'").fetchone()[0], 1)

    def test_media_delivery_reads_verified_artifact_and_never_retries_uncertain_send(self):
        import hashlib
        import io
        data = b'contents of a small output file'
        identity = 'a' + '1' * 32
        metadata = {'id': identity, 'source': 'cc', 'status': 'ready', 'name': 'result.txt',
                    'sha256': hashlib.sha256(data).hexdigest()}
        class Artifacts:
            def open_content(self, requested):
                self.requested = requested
                return io.BytesIO(data), metadata, 'text/plain', len(data)
        class Service:
            artifacts = Artifacts()
        class Transport:
            def send_media(self, *args):
                self.args = args
                raise WeixinTransportError('uncertain')
        gateway = Gateway(self.store, Service(), 'wx', 'bot', 'owner')
        gateway.ingest([{'id': '1', 'sender': 'owner', 'payload': 'hi', 'context_token': 'private'}], 'next')
        self.store.accepted('wx', 'bot', '1', 'reply')
        self.store.bootstrap_sync('owner', 'epoch', 1, runtime_id='runtime')
        self.store.apply_sync('owner', 'epoch', 2, [self.message(text='', attachments=[metadata])],
                              reply_turns={'runtime/reply': 'reply'}, runtime_id='runtime')
        transport = Transport()
        self.assertEqual(gateway.send_next(transport, 'owner'), 'unknown')
        self.assertEqual(transport.args[3:], (data, 'result.txt', 'text/plain'))
        self.assertEqual(gateway.send_next(transport, 'owner'), 'idle')
        block = 'runtime/reply#media:' + identity
        revision = self.store.db.execute('SELECT delivery_revision FROM outbox WHERE block_id=?', (block,)).fetchone()[0]
        self.assertEqual(self.store.retry_once('owner', block, '1' * 32, revision), 'confirm_required')
        self.assertEqual(self.store.retry_once('owner', block, '2' * 32, revision, confirm_unknown=True), 'queued')
        self.assertEqual(self.store.retry_once('owner', block, '2' * 32, revision, confirm_unknown=True), 'queued')

    def test_inbox_batch_cursor_is_atomic_and_conflicts_fail(self):
        item = {'id': '1', 'sender': 'owner', 'payload': 'hello'}
        self.store.ingest('wx', 'account', [item], 'a')
        self.store.ingest('wx', 'account', [item], 'a')
        self.assertEqual(len(self.store.pending_inbox()), 1)
        with self.assertRaises(ValueError):
            self.store.ingest('wx', 'account', [{'id': '2', 'sender': 'owner', 'payload': 'ok'},
                                                dict(item, payload='changed')], 'b')
        self.assertEqual(self.store.cursor('in:wx:account'), 'a')
        self.assertEqual(len(self.store.pending_inbox()), 1)
        self.store.accepted('wx', 'account', '1', 'r1')
        self.assertEqual(self.store.pending_inbox(), [])

    def test_incoming_image_is_durable_before_download_and_retry_is_idempotent(self):
        import base64
        # A valid tiny PNG is re-uploaded into the ordinary owner-image store.
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        class Transport:
            def download_media(self, item):
                return png, '微信图片'
        class Service:
            def __init__(self, artifacts):
                self.artifacts = artifacts
                self.calls = []
            def submit(self, text, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    raise TimeoutError('acknowledgement lost')
                return {'id': 'same-turn'}
        artifacts = mock.Mock()
        service = Service(artifacts)
        gateway = Gateway(self.store, service, 'wx', 'bot', 'owner')
        item = {'id': 'image-1', 'sender': 'owner', 'payload': '', 'context_token': 'private',
                'media': [{'type': 2, 'image_item': {'media': {'encrypt_query_param': 'private'}}}]}
        gateway.ingest([item], 'next')
        self.assertEqual(self.store.cursor('in:wx:bot'), 'next')
        with mock.patch('xiaoyu_runtime.events.image_inputs.upload', return_value={'id': 'image-id'}):
            with self.assertRaises(TimeoutError):
                gateway.submit_pending(Transport())
            self.assertEqual(gateway.submit_pending(Transport()), 1)
        self.assertEqual(service.calls[0]['image_ids'], ['image-id'])
        self.assertEqual(service.calls[0]['client_id'], service.calls[1]['client_id'])
        self.assertEqual(self.store.pending_inbox(), [])
        self.assertEqual(self.store.reply_address('wx', 'bot', 'same-turn'), ('owner', 'private'))

    def test_incoming_file_is_snapshotted_and_sent_to_real_runtime_once(self):
        xiaoyu, runtime_store = make_xiaoyu(Path(self.tmp.name), [Scripted('voice')])
        service = RuntimeService(xiaoyu, runtime_store)
        self.addCleanup(service.close)
        service._owner_writer.conversation_create_weixin()
        class Transport:
            def download_media(self, item):
                return b'file from the owner', 'notes.txt'
        gateway = Gateway(self.store, service, 'wx', 'bot', 'owner')
        gateway.ingest([{'id': 'file-1', 'sender': 'owner', 'payload': '', 'context_token': 'private',
                         'media': [{'type': 4, 'file_item': {'media': {'encrypt_query_param': 'private'}}}]}], 'next')
        with mock.patch.object(xiaoyu, 'hear_channel_files', side_effect=Exception('test stops before dispatch')) as receive:
            self.assertEqual(gateway.submit_pending(Transport()), 1)
            turn_id = self.store.reply_address('wx', 'bot', next(iter(service._messages)))
            self.assertEqual(turn_id, ('owner', 'private'))
            message = service.get(next(iter(service._messages)), wait=5)
            self.assertEqual(message['attachments'][0]['name'], 'notes.txt')
            self.assertTrue(message['attachments'][0]['channel_file'])
            stream, _, _, _ = service.artifacts.open_content(message['attachments'][0]['id'])
            with stream:
                self.assertEqual(stream.read(), b'file from the owner')
            self.assertTrue(receive.called)

    def test_unavailable_attachment_does_not_block_later_text(self):
        from xiaoyu_runtime.channels.media import WeixinMediaError
        class Transport:
            def download_media(self, item):
                raise WeixinMediaError('invalid attachment')
        class Service:
            def submit(self, text, **kwargs):
                self.text = text
                return {'id': 'text-turn'}
        service = Service()
        gateway = Gateway(self.store, service, 'wx', 'bot', 'owner')
        gateway.ingest([{'id': 'file-1', 'sender': 'owner', 'payload': '', 'context_token': 'private',
                         'media': [{'type': 4, 'file_item': {'media': {'encrypt_query_param': 'private'}}}]},
                        {'id': 'text-2', 'sender': 'owner', 'payload': 'later', 'context_token': 'private'}], 'next')
        self.assertEqual(gateway.submit_pending(Transport()), 1)
        self.assertTrue(gateway.intake_problem)
        self.assertEqual(service.text, 'later')
        self.assertEqual(self.store.pending_inbox()[0][2], 'file-1')

    def test_incoming_file_reaches_file_capable_agent_with_verified_local_snapshot(self):
        agent = Scripted('helper', speaks=False, kind='codex')
        xiaoyu, runtime_store = make_xiaoyu(Path(self.tmp.name), [agent])
        service = RuntimeService(xiaoyu, runtime_store)
        self.addCleanup(service.close)
        service._owner_writer.conversation_create_weixin()
        class Transport:
            def download_media(self, item):
                return b'actual owner file', 'notes.txt'
        gateway = Gateway(self.store, service, 'wx', 'bot', 'owner')
        gateway.ingest([{'id': 'file-1', 'sender': 'owner', 'payload': '请看文件', 'context_token': 'private',
                         'media': [{'type': 4, 'file_item': {'media': {'encrypt_query_param': 'private'}}}]}], 'next')
        with mock.patch.object(xiaoyu, '_launch') as launch:
            self.assertEqual(gateway.submit_pending(Transport()), 1)
            request_id = self.store.db.execute("SELECT runtime_id FROM inbox WHERE message_id='file-1'").fetchone()[0]
            result = service.get(request_id, wait=5)
            self.assertEqual(result['status'], 'done')
            self.assertEqual(launch.call_count, 1)
            args = launch.call_args.args
            self.assertEqual(args[1].name, 'helper')
            self.assertIn('notes.txt', args[2])
            meta = result['attachments'][0]
            self.assertIn(str(service.artifacts._blobs / meta['sha256']), args[2])
            self.assertEqual(service._xiaoyu.cards.get(result['card'])['conversation'], 'weixin')

    def test_reply_context_is_bound_to_the_accepted_source_turn(self):
        item = {'id': '1', 'sender': 'owner', 'payload': 'hello', 'context_token': 'private-context'}
        self.store.ingest('wx', 'account', [item], 'a')
        self.store.accepted('wx', 'account', '1', 'turn-1')
        self.assertEqual(self.store.reply_address('wx', 'account', 'turn-1'), ('owner', 'private-context'))
        self.store.bootstrap_sync('owner', 'epoch', 1)
        self.store.apply_sync('owner', 'epoch', 2, [self.message()],
                              reply_turns={'runtime/reply': 'turn-1'})
        request = self.store.outgoing_request('owner', 'runtime/reply#0')
        self.assertEqual(self.store.reply_address('wx', 'account', request), ('owner', 'private-context'))
        self.assertIsNone(self.store.reply_address('wx', 'another-account', 'turn-1'))
        with self.assertRaises(ValueError):
            self.store.ingest('wx', 'account', [dict(item, context_token='changed')], 'b')
        self.assertEqual(self.store.cursor('in:wx:account'), 'a')

    def test_outbox_persists_and_does_not_retry_uncertain_send(self):
        self.store.bootstrap_sync('owner', 'epoch', 3)
        self.store.apply_sync('owner', 'epoch', 4, [self.message()], reply_turns={'runtime/reply': 'reply'})
        block, text = self.store.next_outgoing('owner')
        self.assertEqual(text, '你好')
        self.assertTrue(self.store.mark('owner', block, 'pending', 'sending'))
        self.store.close()
        self.store = ChannelStore(self.path)
        self.addCleanup(self.store.close)
        self.addCleanup(self.store.close)
        self.assertIsNone(self.store.next_outgoing('owner'))
        self.assertTrue(self.store.mark('owner', block, 'sending', 'unknown'))
        self.store.apply_sync('owner', 'epoch', 5, [self.message(), self.message(id='second')], reply_turns={'runtime/reply': 'reply', 'runtime/second': 'second'})
        self.assertIsNone(self.store.next_outgoing('owner'))
        self.assertEqual(json.loads(self.store.cursor('sync:owner')), ['epoch', 5])

    def test_revised_published_block_stops_cursor(self):
        self.store.bootstrap_sync('owner', 'epoch', 0)
        self.store.apply_sync('owner', 'epoch', 1, [self.message()], reply_turns={'runtime/reply': 'reply'})
        with self.assertRaises(ValueError):
            self.store.apply_sync('owner', 'epoch', 2, [self.message(text='不同')], reply_turns={'runtime/reply': 'reply'})
        self.assertEqual(json.loads(self.store.cursor('sync:owner')), ['epoch', 1])

    def test_snapshot_reset_or_epoch_change_never_replays_history(self):
        with self.assertRaises(ValueError):
            self.store.apply_sync('owner', 'epoch', 10, [self.message()])
        self.store.bootstrap_sync('owner', 'epoch', 10)
        with self.assertRaises(ValueError):
            self.store.apply_sync('owner', 'epoch', 11, [self.message()], reset=True)
        with self.assertRaises(ValueError):
            self.store.apply_sync('owner', 'other', 11, [self.message()])
        self.assertIsNone(self.store.next_outgoing('owner'))

    def test_ids_are_stable_and_channel_scoped(self):
        self.assertEqual(client_id('wx', 'a', '1'), client_id('wx', 'a', '1'))
        self.assertNotEqual(client_id('wx', 'a', '1'), client_id('wx', 'b', '1'))
        self.assertRegex(client_id('wx', 'a', '1'), r'^channel-[A-Za-z0-9_-]{43}$')

    def test_channel_catalog_is_explicit_durable_and_not_a_side_chat(self):
        path = Path(self.tmp.name) / 'conversations.json'
        catalog = ConversationCatalog(path)
        with self.assertRaises(ValueError):
            catalog.create('weixin')
        catalog.origin = 'a' * 32
        item = catalog.create_weixin()
        self.assertEqual((item['id'], item['kind'], item['owner']), ('weixin', 'weixin', 'a' * 32))
        self.assertEqual(ConversationCatalog(path).get('weixin')['kind'], 'weixin')
        with self.assertRaises(ValueError):
            catalog.rename('weixin', '旁聊')
        with self.assertRaises(ValueError):
            catalog.delete('weixin')
        self.assertFalse(catalog.merge('b' * 32, dict(item, owner='b' * 32)))

    def test_peer_observation_is_read_only_and_does_not_assign_a_host(self):
        path = Path(self.tmp.name) / 'observed-conversations.json'
        catalog = ConversationCatalog(path)
        catalog.origin = 'a' * 32
        observed, created = catalog.observe_weixin()
        self.assertTrue(created)
        self.assertTrue(observed['observed_only'])
        self.assertNotIn('owner', observed)
        self.assertEqual(catalog.create_weixin(), observed)
        self.assertFalse(catalog.observe_weixin()[1])
        self.assertNotIn('owner', ConversationCatalog(path).get('weixin'))

    def test_channel_claim_requires_matching_authority_host_and_monotonic_generation(self):
        catalog = ConversationCatalog(Path(self.tmp.name) / 'claimed-conversations.json')
        catalog.origin = 'a' * 32
        catalog.observe_weixin()
        grant = {'authority': 'b' * 32, 'host': 'a' * 32, 'state': 'reserved', 'generation': 2}
        item, changed = catalog.claim_weixin(grant)
        self.assertTrue(changed)
        self.assertEqual(item['owner'], 'a' * 32)
        self.assertFalse(catalog.claim_weixin(grant)[1])
        for bad in (dict(grant, host='c' * 32), dict(grant, generation=1),
                    dict(grant, authority='c' * 32), dict(grant, state='revoking')):
            with self.assertRaises(ValueError):
                catalog.claim_weixin(bad)
        self.assertEqual(catalog.get('weixin')['grant_generation'], 2)

    def test_existing_channel_database_adds_columns_without_losing_pending_work(self):
        path = Path(self.tmp.name) / 'old.sqlite'
        db = sqlite3.connect(path)
        db.executescript('''
            CREATE TABLE inbox (channel TEXT, account TEXT, message_id TEXT, sender TEXT,
                                payload TEXT, runtime_id TEXT, PRIMARY KEY(channel,account,message_id));
            CREATE TABLE outbox (destination TEXT, block_id TEXT, text TEXT, state TEXT DEFAULT 'pending',
                                 PRIMARY KEY(destination,block_id));
            INSERT INTO inbox VALUES('wx','bot','1','owner','hello',NULL);
            INSERT INTO outbox VALUES('owner','old-block','reply','unknown');
        ''')
        db.close()
        migrated = ChannelStore(path)
        self.addCleanup(migrated.close)
        self.assertEqual(migrated.pending_inbox()[0][-1], 'hello')
        self.assertEqual(migrated.retry('owner', 'old-block'), 'confirm_required')
        self.assertIsNone(migrated.outgoing_request('owner', 'old-block'))
        self.assertEqual(migrated.dirty_delivery('owner'), [('old-block', 'unknown', 1)])

    def test_concurrent_legacy_migration_preserves_pending_work(self):
        path = Path(self.tmp.name) / 'concurrent-old.sqlite'
        db = sqlite3.connect(path)
        db.executescript('''
            CREATE TABLE inbox (channel TEXT, account TEXT, message_id TEXT, sender TEXT,
                                payload TEXT, runtime_id TEXT, PRIMARY KEY(channel,account,message_id));
            CREATE TABLE outbox (destination TEXT, block_id TEXT, text TEXT, state TEXT DEFAULT 'pending',
                                 PRIMARY KEY(destination,block_id));
            INSERT INTO inbox VALUES('wx','bot','1','owner','hello',NULL);
        ''')
        db.close()
        barrier, errors = threading.Barrier(3), []
        def open_store():
            barrier.wait()
            try:
                store = ChannelStore(path)
                try:
                    self.assertEqual(store.pending_inbox()[0][-1], 'hello')
                finally:
                    store.close()
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=open_store) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(3)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])

    def test_outbox_needs_explicit_channel_turn_and_rejects_main(self):
        self.store.bootstrap_sync('owner', 'epoch', 1)
        with self.assertRaises(ValueError):
            self.store.apply_sync('owner', 'epoch', 2, [self.message()])
        self.assertEqual(json.loads(self.store.cursor('sync:owner')), ['epoch', 1])
        self.store.apply_sync('owner', 'epoch', 2,
                              [self.message(id='other'), self.message(conversation='default'),
                               self.message(id='wx')], reply_turns={'runtime/wx': 'wx', 'runtime/reply': 'reply'})
        self.assertEqual(self.store.next_outgoing('owner'), ('runtime/wx#0', '你好'))
        self.assertEqual(self.store.outgoing_request('owner', 'runtime/wx#0'), 'wx')

    def test_uncertain_delivery_needs_explicit_confirmation_to_retry(self):
        self.store.bootstrap_sync('owner', 'epoch', 1)
        self.store.apply_sync('owner', 'epoch', 2, [self.message()], reply_turns={'runtime/reply': 'reply'})
        block, _ = self.store.next_outgoing('owner')
        self.assertTrue(self.store.mark('owner', block, 'pending', 'sending'))
        self.assertTrue(self.store.mark('owner', block, 'sending', 'unknown'))
        self.assertEqual(self.store.retry('owner', block), 'confirm_required')
        self.assertIsNone(self.store.next_outgoing('owner'))
        self.assertEqual(self.store.retry('owner', block, confirm_unknown=True), 'queued')
        self.assertEqual(self.store.next_outgoing('owner')[0], block)
        self.assertTrue(self.store.mark('owner', block, 'pending', 'sending'))
        self.assertTrue(self.store.mark('owner', block, 'sending', 'failed'))
        self.assertEqual(self.store.retry('owner', block), 'queued')

    def test_interrupted_send_becomes_unknown_after_exclusive_recovery(self):
        self.store.bootstrap_sync('owner', 'epoch', 1)
        self.store.apply_sync('owner', 'epoch', 2, [self.message()], reply_turns={'runtime/reply': 'reply'})
        block, _ = self.store.next_outgoing('owner')
        self.assertTrue(self.store.mark('owner', block, 'pending', 'sending'))
        self.assertEqual(self.store.recover_interrupted('owner'), 1)
        self.assertEqual(self.store.recover_interrupted('owner'), 0)
        self.assertEqual(self.store.retry('owner', block), 'confirm_required')

    def test_retry_operation_is_durable_and_never_repeats_after_a_second_uncertain_send(self):
        self.store.bootstrap_sync('owner', 'epoch', 1)
        self.store.apply_sync('owner', 'epoch', 2, [self.message()], reply_turns={'runtime/reply': 'reply'})
        block, _ = self.store.next_outgoing('owner')
        self.assertTrue(self.store.mark('owner', block, 'pending', 'sending'))
        self.assertTrue(self.store.mark('owner', block, 'sending', 'unknown'))
        self.assertEqual('confirm_required', self.store.retry_once('owner', block, 'a' * 32, 3))
        self.assertEqual('queued', self.store.retry_once('owner', block, 'b' * 32, 3, confirm_unknown=True))
        self.assertTrue(self.store.mark('owner', block, 'pending', 'sending'))
        self.assertTrue(self.store.mark('owner', block, 'sending', 'unknown'))
        self.assertEqual('queued', self.store.retry_once('owner', block, 'b' * 32, 3, confirm_unknown=True))
        self.assertEqual('stale', self.store.retry_once('owner', block, 'd' * 32, 3, confirm_unknown=True))
        self.assertIsNone(self.store.next_outgoing('owner'))
        self.store.close()
        self.store = ChannelStore(self.path)
        self.assertEqual('queued', self.store.retry_once('owner', block, 'b' * 32, 3, confirm_unknown=True))
        self.assertIsNone(self.store.next_outgoing('owner'))
        with self.assertRaises(ValueError):
            self.store.retry_once('owner', block, 'b' * 32, 3, confirm_unknown=False)

    def test_concurrent_retry_replays_are_one_atomic_operation(self):
        self.store.bootstrap_sync('owner', 'epoch', 1)
        self.store.apply_sync('owner', 'epoch', 2, [self.message()], reply_turns={'runtime/reply': 'reply'})
        block, _ = self.store.next_outgoing('owner')
        self.assertTrue(self.store.mark('owner', block, 'pending', 'sending'))
        self.assertTrue(self.store.mark('owner', block, 'sending', 'unknown'))
        barrier, results, errors = threading.Barrier(2), [], []
        def retry():
            local = ChannelStore(self.path)
            try:
                barrier.wait(2)
                results.append(local.retry_once('owner', block, 'c' * 32, 3, confirm_unknown=True))
            except Exception as error:
                errors.append(error)
            finally:
                local.close()
        threads = [threading.Thread(target=retry) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(3)
        self.assertFalse(errors)
        self.assertEqual(['queued', 'queued'], sorted(results))
        self.assertTrue(self.store.mark('owner', block, 'pending', 'sending'))
        self.assertTrue(self.store.mark('owner', block, 'sending', 'unknown'))
        self.assertEqual('queued', self.store.retry_once('owner', block, 'c' * 32, 3, confirm_unknown=True))
        self.assertIsNone(self.store.next_outgoing('owner'))

    def test_delivery_projection_keeps_newer_transition_and_survives_restart(self):
        self.store.bootstrap_sync('owner', 'epoch', 1)
        self.store.apply_sync('owner', 'epoch', 2, [self.message()],
                              reply_turns={'runtime/reply': 'reply'})
        block, state, revision = self.store.dirty_delivery('owner')[0]
        self.assertEqual((block, state, revision), ('runtime/reply#0', 'waiting', 1))
        self.assertTrue(self.store.mark('owner', block, 'pending', 'sending'))
        self.assertTrue(self.store.mark('owner', block, 'sending', 'unknown'))
        self.assertFalse(self.store.projected_delivery('owner', block, revision))
        self.store.close()
        self.store = ChannelStore(self.path)
        self.addCleanup(self.store.close)
        self.assertEqual(self.store.dirty_delivery('owner'), [(block, 'unknown', 3)])
        self.assertTrue(self.store.projected_delivery('owner', block, 3))
        self.assertEqual(self.store.dirty_delivery('owner'), [])
        self.assertEqual(self.store.retry('owner', block, confirm_unknown=True), 'queued')
        self.assertEqual(self.store.dirty_delivery('owner'), [(block, 'waiting', 4)])

    def test_delivery_projection_is_bounded_and_omits_private_fields(self):
        self.store.bootstrap_sync('owner', 'epoch', 1)
        self.store.apply_sync('owner', 'epoch', 2,
                              [self.message(), self.message(id='second')],
                              reply_turns={'runtime/reply': 'secret-request',
                                           'runtime/second': 'another-request'})
        result = self.store.dirty_delivery('owner', limit=1)
        self.assertEqual(result, [('runtime/reply#0', 'waiting', 1)])
        self.assertNotIn('secret', repr(result))
        self.assertNotIn('你好', repr(result))
        with self.assertRaises(ValueError):
            self.store.dirty_delivery('owner', limit=0)
        with self.assertRaises(ValueError):
            self.store.projected_delivery('owner', 'runtime/reply#0', True)

    def test_delivery_projection_skips_invalid_records_and_wraps_for_reconciliation(self):
        service = mock.Mock(runtime_id='runtime')
        gateway = Gateway(self.store, service, 'wx', 'account', 'owner')
        with self.store.db:
            self.store.db.execute('INSERT INTO outbox(destination,block_id,text) VALUES(?,?,?)',
                                  ('owner', 'foreign/id#0', 'secret'))
            self.store.db.execute('INSERT INTO outbox(destination,block_id,text) VALUES(?,?,?)',
                                  ('owner', 'runtime/local#0', 'reply'))
        self.assertEqual(gateway.flush_delivery('owner'), 1)
        service._channel_delivery.assert_called_once_with('local', 'runtime/local#0', 'reply', 'waiting', 1)
        self.assertEqual(self.store.dirty_delivery('owner'), [('foreign/id#0', 'waiting', 1)])
        # Seek wraps without losing the unresolved row.
        self.assertEqual(gateway.flush_delivery('owner'), 1)

    def test_concurrent_opener_can_use_existing_database(self):
        # Reproduce a competing creator winning after an existence check.
        with mock.patch.object(Path, 'exists', return_value=False):
            other = ChannelStore(self.path)
        other.close()

    def test_gateway_enforces_binding_and_submits_only_weixin(self):
        calls = []
        class Service:
            def submit(self, text, **kwargs):
                calls.append((text, kwargs))
                return {'id': 'runtime-id'}
        gateway = Gateway(self.store, Service(), 'wx', 'bot', 'owner')
        with self.assertRaises(ValueError):
            gateway.ingest([{'id': '2', 'sender': 'stranger', 'payload': 'attack'}], 'next')
        self.assertEqual(self.store.cursor('in:wx:bot'), '')
        gateway.ingest([{'id': '1', 'sender': 'owner', 'payload': 'hi'}], 'next')
        self.assertEqual(gateway.submit_pending(), 1)
        self.assertEqual(gateway.submit_pending(), 0)
        self.assertEqual(calls[0][1], {'conversation': 'weixin', 'client_id': client_id('wx', 'bot', '1'), '_channel_authorized': True})

    def test_lost_acceptance_reuses_identity_on_retry(self):
        class Service:
            def __init__(self):
                self.ids = []
            def submit(self, text, **kwargs):
                self.ids.append(kwargs['client_id'])
                if len(self.ids) == 1:
                    raise TimeoutError('acceptance unknown')
                return {'id': 'original-runtime-id'}
        service = Service()
        gateway = Gateway(self.store, service, 'wx', 'bot', 'owner')
        gateway.ingest([{'id': '1', 'sender': 'owner', 'payload': 'hi'}], 'next')
        with self.assertRaises(TimeoutError):
            gateway.submit_pending()
        self.assertEqual(gateway.submit_pending(), 1)
        self.assertEqual(service.ids[0], service.ids[1])

    def test_poll_once_preserves_cursor_and_reply_context(self):
        calls = []
        class Service:
            def submit(self, text, **kwargs):
                calls.append(text)
                return {'id': 'turn'}
        class Transport:
            def updates(self, cursor):
                self.cursor = cursor
                return {'get_updates_buf': 'next', 'msgs': [{
                    'message_type': 1, 'from_user_id': 'owner', 'message_id': 99,
                    'context_token': 'private', 'item_list': [{'type': 1, 'text_item': {'text': 'hello'}}]}]}
        gateway = Gateway(self.store, Service(), 'wx', 'bot', 'owner')
        transport = Transport()
        self.assertEqual(gateway.poll_once(transport), 1)
        self.assertEqual(calls, ['hello'])
        self.assertEqual(self.store.cursor('in:wx:bot'), 'next')
        self.assertEqual(self.store.reply_address('wx', 'bot', 'turn'), ('owner', 'private'))
        self.assertEqual(gateway.poll_once(transport), 0)
        self.assertEqual(transport.cursor, 'next')

    def test_cancelled_poll_does_not_advance_cursor_or_accept_late_input(self):
        class Service:
            def submit(self, *_args, **_kwargs):
                self.fail = True
                return {'id': 'unexpected'}
        stopped = []
        class Transport:
            def updates(self, _cursor):
                stopped.append(True)
                return {'get_updates_buf': 'next', 'msgs': [{
                    'message_type': 1, 'from_user_id': 'owner', 'message_id': 99,
                    'context_token': 'private', 'item_list': [{'type': 1, 'text_item': {'text': 'hello'}}]}]}
        service = Service()
        gateway = Gateway(self.store, service, 'wx', 'bot', 'owner')
        self.assertEqual(gateway.poll_once(Transport(), cancelled=lambda: bool(stopped)), 0)
        self.assertEqual(self.store.cursor('in:wx:bot'), '')
        self.assertEqual(self.store.pending_inbox(), [])
        self.assertFalse(hasattr(service, 'fail'))

    def test_send_next_reconciles_context_and_never_retries_unknown(self):
        class Service:
            def submit(self, text, **kwargs):
                return {'id': 'turn-1'}
        class Transport:
            def __init__(self):
                self.calls = []
            def send_text(self, *args):
                self.calls.append(args)
                raise WeixinTransportError('uncertain')
        gateway = Gateway(self.store, Service(), 'wx', 'bot', 'owner')
        gateway.ingest([{'id': '1', 'sender': 'owner', 'payload': 'hi',
                         'context_token': 'private'}], 'next')
        gateway.submit_pending()
        self.store.bootstrap_sync('owner', 'epoch', 1)
        self.store.apply_sync('owner', 'epoch', 2, [self.message()],
                              reply_turns={'runtime/reply': 'turn-1'})
        transport = Transport()
        self.assertEqual(gateway.send_next(transport, 'owner'), 'unknown')
        self.assertEqual(transport.calls[0][:2], ('owner', 'private'))
        self.assertEqual(gateway.send_next(transport, 'owner'), 'idle')
        self.assertEqual(len(transport.calls), 1)

    def test_optional_send_ret_means_service_accepted_not_device_receipt(self):
        for response in ({'ret': 0}, {}, {'message_id': '123'}, {'errcode': 0}):
            self.assertTrue(_accepted_send(response))
        for response in (None, [], {'ret': False}, {'ret': None}, {'ret': 1},
                         {'errcode': -1}, {'errmsg': 'failure'}):
            self.assertFalse(_accepted_send(response))

    def test_success_without_ret_advances_queued_replies(self):
        class Service:
            def submit(self, text, **kwargs):
                return {'id': 'turn-1'}
        class Transport:
            def send_text(self, *args):
                return {'message_id': 'server-assigned'}
        gateway = Gateway(self.store, Service(), 'wx', 'bot', 'owner')
        gateway.ingest([{'id': '1', 'sender': 'owner', 'payload': 'hi', 'context_token': 'private'}], 'next')
        gateway.submit_pending()
        self.store.bootstrap_sync('owner', 'epoch', 1)
        self.store.apply_sync('owner', 'epoch', 2, [self.message(), self.message(id='second')],
                              reply_turns={'runtime/reply': 'turn-1', 'runtime/second': 'turn-1'})
        with mock.patch('xiaoyu_runtime.channels.store.time.time', return_value=100.0) as clock:
            self.assertEqual(gateway.send_next(Transport(), 'owner'), 'sent')
            self.assertEqual(gateway.send_next(Transport(), 'owner'), 'paced')
            self.assertGreaterEqual(gateway.pacing_delay('owner'), 0.6)
            self.assertEqual(self.store.send_delay('owner', 'another-turn'), 0.0)
            # A process restart retains the pause instead of emitting a burst.
            reopened = ChannelStore(self.path)
            try:
                self.assertGreaterEqual(reopened.send_delay('owner', 'turn-1'), 0.6)
            finally:
                reopened.close()
            clock.return_value = 110.0
            self.assertEqual(gateway.send_next(Transport(), 'owner'), 'sent')
        self.assertEqual(gateway.send_next(Transport(), 'owner'), 'idle')
        self.assertEqual(self.store.db.execute('SELECT DISTINCT last_outcome FROM outbox').fetchall(),
                         [('api_accepted_no_ret',)])

    def test_new_owner_message_cancels_old_turn_pacing_without_reordering(self):
        self.store.ingest('wx', 'bot', [{'id': '1', 'sender': 'owner', 'payload': 'first'}], 'a')
        self.store.accepted('wx', 'bot', '1', 'request-1')
        self.store.bootstrap_sync('owner', 'epoch', 1, runtime_id='runtime')
        self.store.apply_sync('owner', 'epoch', 2, [self.message(id='request-1:reply')],
                              reply_turns={'runtime/request-1': 'request-1'}, runtime_id='runtime')
        block, _ = self.store.next_outgoing('owner')
        with mock.patch('xiaoyu_runtime.channels.store.time.time', return_value=100.0):
            self.store.mark('owner', block, 'pending', 'sending')
            self.store.mark('owner', block, 'sending', 'sent')
            self.assertGreater(self.store.send_delay('owner', 'request-1', block, channel='wx', account='bot', sender='owner'), 0)
            self.store.ingest('wx', 'bot', [{'id': '2', 'sender': 'owner', 'payload': 'next'}], 'b')
            self.assertEqual(self.store.send_delay('owner', 'request-1', block, channel='wx', account='bot', sender='owner'), 0)

    def test_first_reply_waits_for_followup_but_cancels_immediately(self):
        with mock.patch('xiaoyu_runtime.channels.store.time.time', return_value=100.0) as clock:
            self.store.ingest('wx', 'bot', [{'id': '1', 'sender': 'owner', 'payload': 'first'}], 'a')
            self.store.accepted('wx', 'bot', '1', 'request-1')
            self.store.bootstrap_sync('owner', 'epoch', 1, runtime_id='runtime')
            self.store.apply_sync('owner', 'epoch', 2, [self.message(id='request-1:reply')],
                                  reply_turns={'runtime/request-1': 'request-1'}, runtime_id='runtime')
            block, _ = self.store.next_outgoing('owner')
            clock.return_value = 101.0
            self.assertAlmostEqual(self.store.send_delay('owner', 'request-1', block, channel='wx', account='bot', sender='owner'), 1.0)
            reopened = ChannelStore(self.path)
            try:
                self.assertAlmostEqual(reopened.send_delay('owner', 'request-1', block, channel='wx', account='bot', sender='owner'), 1.0)
                clock.return_value = 102.0
                self.assertEqual(reopened.send_delay('owner', 'request-1', block, channel='wx', account='bot', sender='owner'), 0.0)
            finally:
                reopened.close()
            clock.return_value = 101.0
            self.store.ingest('wx', 'bot', [{'id': '2', 'sender': 'owner', 'payload': 'followup'}], 'b')
            self.assertEqual(self.store.send_delay('owner', 'request-1', block, channel='wx', account='bot', sender='owner'), 0)
            self.assertEqual(self.store.begin_send('owner', block, channel='wx', account='bot', sender='owner'), 'cancelled')

    def test_quote_id_is_resolved_only_for_same_bound_owner(self):
        self.store.ingest('wx', 'bot', [{'id': 'original', 'sender': 'owner', 'payload': '原文'}], 'a')
        ref = {'kind': 'unavailable', 'id': 'original'}
        self.store.ingest('wx', 'bot', [{'id': 'reply', 'sender': 'owner', 'payload': '这个', 'quotes': [ref]}], 'b')
        saved = json.loads(self.store.db.execute("SELECT quotes_json FROM inbox WHERE message_id='reply'").fetchone()[0])
        self.assertEqual(saved[0]['resolved']['text'], '原文')
        self.store.ingest('wx', 'other', [{'id': 'reply', 'sender': 'owner', 'payload': '这个', 'quotes': [ref]}], 'c')
        other = json.loads(self.store.db.execute("SELECT quotes_json FROM inbox WHERE account='other'").fetchone()[0])
        self.assertEqual(other[0]['resolved']['kind'], 'unavailable')

    def test_quote_can_resolve_unique_sent_bubble_id(self):
        self.store.ingest('wx', 'bot', [{'id': '1', 'sender': 'owner', 'payload': 'hi'}], 'a')
        self.store.accepted('wx', 'bot', '1', 'request-1')
        self.store.bootstrap_sync('owner', 'epoch', 1, runtime_id='runtime')
        self.store.apply_sync('owner', 'epoch', 2, [self.message(id='request-1:reply', text='发出的原文')],
                              reply_turns={'runtime/request-1': 'request-1'}, runtime_id='runtime')
        block, _ = self.store.next_outgoing('owner')
        self.store.mark('owner', block, 'pending', 'sending')
        self.store.mark('owner', block, 'sending', 'sent', server_message_id='upstream-42')
        self.store.ingest('wx', 'bot', [{'id': '2', 'sender': 'owner', 'payload': '这个',
            'quotes': [{'kind': 'unavailable', 'id': 'upstream-42'}]}], 'b')
        saved = json.loads(self.store.db.execute("SELECT quotes_json FROM inbox WHERE message_id='2'").fetchone()[0])
        self.assertEqual(saved[0]['resolved']['text'], '发出的原文')
        self.assertEqual(saved[0]['resolved']['author'], 'xiaoyou')
        self.assertEqual(saved[0]['resolved']['reply_to'], 'hi')
        inline = {'kind': 'text', 'text': '选中的一部分', 'partial': True, 'id': 'upstream-42'}
        self.store.ingest('wx', 'bot', [{'id': '3', 'sender': 'owner', 'payload': '这句', 'quotes': [inline]}], 'c')
        saved = json.loads(self.store.db.execute("SELECT quotes_json FROM inbox WHERE message_id='3'").fetchone()[0])
        self.assertEqual(saved[0]['resolved']['text'], '选中的一部分')
        self.assertEqual(saved[0]['resolved']['reply_to'], 'hi')

    @mock.patch('xiaoyu_runtime.channels.store.INPUT_PAUSE', 0)
    def test_new_owner_input_cancels_unsent_direct_bubbles_but_not_task_results(self):
        class Service: runtime_id = 'runtime'
        class Transport:
            def __init__(self): self.sent = []
            def send_text(self, *args): self.sent.append(args[-1]); return {'ret': 0}
        gateway = Gateway(self.store, Service(), 'wx', 'bot', 'owner')
        gateway.ingest([{'id': '1', 'sender': 'owner', 'payload': 'first', 'context_token': 'private'}], 'a')
        self.store.accepted('wx', 'bot', '1', 'request-1')
        self.store.bootstrap_sync('owner', 'epoch', 1, runtime_id='runtime')
        old = self.message(id='request-1:reply', text='第一句\n\n---\n\n第二句')
        self.store.apply_sync('owner', 'epoch', 2, [old], reply_turns={'runtime/request-1': 'request-1'}, runtime_id='runtime')
        transport = Transport()
        self.assertEqual(gateway.send_next(transport, 'owner'), 'sent')
        self.assertEqual(gateway.send_next(transport, 'owner'), 'paced')
        gateway.ingest([{'id': '2', 'sender': 'owner', 'payload': 'another thought', 'context_token': 'private'}], 'b')
        self.store.accepted('wx', 'bot', '2', 'request-2')
        self.assertEqual(gateway.send_next(transport, 'owner'), 'cancelled')
        self.assertEqual(transport.sent, ['第一句'])
        self.assertEqual(self.store.db.execute("SELECT state FROM outbox WHERE text='第二句'").fetchone()[0], 'cancelled')
        # Replaying the same source message cannot revive the cancelled block.
        self.store.apply_sync('owner', 'epoch', 3, [old], reply_turns={'runtime/request-1': 'request-1'}, runtime_id='runtime')
        self.assertIsNone(self.store.next_outgoing('owner'))
        fresh = self.message(id='request-2:reply', text='回应新的输入')
        self.store.apply_sync('owner', 'epoch', 4, [fresh], reply_turns={'runtime/request-2': 'request-2'}, runtime_id='runtime')
        self.assertEqual(gateway.send_next(transport, 'owner'), 'sent')
        self.assertEqual(transport.sent[-1], '回应新的输入')
        summary = self.message(id='run-summary:reply', kind='summary', text='后台任务结果')
        self.store.apply_sync('owner', 'epoch', 5, [summary], reply_turns={'runtime/run-summary': 'request-1'}, runtime_id='runtime')
        self.assertEqual(gateway.send_next(transport, 'owner'), 'sent')
        self.assertEqual(transport.sent[-1], '后台任务结果')

    def test_cancellation_race_never_retracts_a_send_already_claimed(self):
        self.store.ingest('wx', 'bot', [{'id': '1', 'sender': 'owner', 'payload': 'first'}], 'a')
        self.store.accepted('wx', 'bot', '1', 'request-1')
        self.store.bootstrap_sync('owner', 'epoch', 1, runtime_id='runtime')
        self.store.apply_sync('owner', 'epoch', 2, [self.message(id='request-1:reply')],
                              reply_turns={'runtime/request-1': 'request-1'}, runtime_id='runtime')
        block, _ = self.store.next_outgoing('owner')
        self.assertEqual(self.store.begin_send('owner', block, channel='wx', account='bot', sender='owner'), 'sending')
        self.store.ingest('wx', 'bot', [{'id': '2', 'sender': 'owner', 'payload': 'next'}], 'b')
        self.assertEqual(self.store.begin_send('owner', block, channel='wx', account='bot', sender='owner'), 'idle')
        self.assertTrue(self.store.mark('owner', block, 'sending', 'sent'))

    def test_redelivery_of_same_input_does_not_cancel_its_reply(self):
        item = {'id': '1', 'sender': 'owner', 'payload': 'first'}
        self.store.ingest('wx', 'bot', [item], 'a')
        self.store.accepted('wx', 'bot', '1', 'request-1')
        self.store.bootstrap_sync('owner', 'epoch', 1, runtime_id='runtime')
        self.store.apply_sync('owner', 'epoch', 2, [self.message(id='request-1:reply')],
                              reply_turns={'runtime/request-1': 'request-1'}, runtime_id='runtime')
        self.store.ingest('wx', 'bot', [item], 'b')
        block, _ = self.store.next_outgoing('owner')
        self.assertEqual(self.store.begin_send('owner', block, channel='wx', account='bot', sender='owner'), 'sending')

    def test_new_input_does_not_cancel_or_accelerate_task_result(self):
        class Service: runtime_id = 'runtime'
        class Transport:
            def __init__(self): self.sent = []
            def send_text(self, *args): self.sent.append(args[-1]); return {'ret': 0}
        gateway = Gateway(self.store, Service(), 'wx', 'bot', 'owner')
        gateway.ingest([{'id': '1', 'sender': 'owner', 'payload': 'task', 'context_token': 'token'}], 'a')
        self.store.accepted('wx', 'bot', '1', 'request-1')
        self.store.bootstrap_sync('owner', 'epoch', 1, runtime_id='runtime')
        summary = self.message(id='run-summary:reply', kind='summary', text='任务结果\n\n---\n\n' + '字' * 60)
        self.store.apply_sync('owner', 'epoch', 2, [summary],
                              reply_turns={'runtime/run-summary': 'request-1'}, runtime_id='runtime')
        transport = Transport()
        with mock.patch('xiaoyu_runtime.channels.store.time.time', return_value=100.0) as clock:
            self.assertEqual(gateway.send_next(transport, 'owner'), 'sent')
            gateway.ingest([{'id': '2', 'sender': 'owner', 'payload': 'new', 'context_token': 'token'}], 'b')
            self.assertEqual(gateway.send_next(transport, 'owner'), 'paced')
            clock.return_value = 112.0
            self.assertEqual(gateway.send_next(transport, 'owner'), 'sent')
        self.assertEqual(transport.sent, ['任务结果', '字' * 60])

    def test_backward_clock_change_keeps_pause_bounded(self):
        with self.store.db:
            self.store.db.execute('INSERT INTO delivery_pacing(destination,request_id,next_at) VALUES(?,?,?)', ('owner', 'request-1', 10_000))
        with mock.patch('xiaoyu_runtime.channels.store.time.time', return_value=100.0) as clock:
            self.assertEqual(self.store.send_delay('owner', 'request-1'), 12.0)
            clock.return_value = 112.0
            self.assertEqual(self.store.send_delay('owner', 'request-1'), 0.0)

    def test_reading_pause_grows_with_bubble_length_and_has_bounds(self):
        self.assertAlmostEqual(reading_pause('短'), 0.62)
        self.assertGreater(reading_pause('字' * 60), reading_pause('字' * 20))
        self.assertEqual(reading_pause('字' * 1000), 2.0)
        self.assertEqual(reading_pause('a' * 200, 'media'), 0.6)

    def test_pause_is_based_on_the_delivered_bubble_and_survives_restart(self):
        self.store.bootstrap_sync('owner', 'epoch', 1, runtime_id='runtime')
        message = self.message(id='request:reply', text='短\n\n---\n\n' + '字' * 80)
        self.store.apply_sync('owner', 'epoch', 2, [message],
                              reply_turns={'runtime/request': 'request'}, runtime_id='runtime')
        first, _ = self.store.next_outgoing('owner')
        with mock.patch('xiaoyu_runtime.channels.store.time.time', return_value=100.0) as clock:
            self.assertTrue(self.store.mark('owner', first, 'pending', 'sending'))
            self.assertTrue(self.store.mark('owner', first, 'sending', 'sent'))
            self.assertEqual(self.store.db.execute('SELECT sent_at FROM outbox WHERE block_id=?', (first,)).fetchone()[0], 100.0)
            second, _ = self.store.next_outgoing('owner')
            self.assertAlmostEqual(self.store.send_delay('owner', 'request', second), 0.62)
            reopened = ChannelStore(self.path)
            try:
                clock.return_value = 100.3
                self.assertAlmostEqual(reopened.send_delay('owner', 'request', second), 0.32)
                clock.return_value = 100.62
                self.assertEqual(reopened.send_delay('owner', 'request', second), 0.0)
            finally:
                reopened.close()

    def test_confirm_received_never_resends_and_records_user_evidence(self):
        self.store.bootstrap_sync('owner', 'epoch', 1)
        self.store.apply_sync('owner', 'epoch', 2, [self.message(), self.message(id='second')],
                              reply_turns={'runtime/reply': 'one', 'runtime/second': 'two'})
        block, _ = self.store.next_outgoing('owner')
        self.assertTrue(self.store.mark('owner', block, 'pending', 'sending'))
        self.assertTrue(self.store.mark('owner', block, 'sending', 'unknown'))
        with self.assertRaises(ValueError):
            self.store.confirm_received('owner', block)
        self.assertIsNone(self.store.next_outgoing('owner'))
        self.assertTrue(self.store.confirm_received('owner', block, confirmed=True))
        self.assertFalse(self.store.confirm_received('owner', block, confirmed=True))
        self.assertEqual(self.store.next_outgoing('owner')[0], 'runtime/second#0')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM delivery_confirmation').fetchone()[0], 1)
        self.assertEqual(self.store.dirty_delivery('owner')[0][1], 'sent')

    def test_sync_to_outbox_uses_exact_inbound_turn_and_refuses_unmapped_task(self):
        class Service:
            def submit(self, text, **kwargs):
                return {'id': 'reply'}
        gateway = Gateway(self.store, Service(), 'wx', 'bot', 'owner')
        gateway.ingest([{'id': '1', 'sender': 'owner', 'payload': 'hi',
                         'context_token': 'private'}], 'next')
        gateway.submit_pending()
        with self.assertRaises(ValueError):
            gateway.bootstrap_sync('owner', {'runtime_id': 'runtime', 'epoch': 'epoch', 'seq': 1})
        gateway.bootstrap_sync('owner', {'runtime_id': 'runtime', 'epoch': 'epoch', 'seq': 1,
                                         'snapshot': {'seq': 1}})
        snapshot = {'runtime_id': 'runtime', 'epoch': 'epoch', 'seq': 2,
                    'messages': [self.message(), self.message(id='other', conversation='default')]}
        gateway.apply_sync('owner', snapshot)
        self.assertEqual(self.store.next_outgoing('owner')[0], 'runtime/reply#0')
        summary = self.message(id='run1-summary:reply', kind='summary', task='task1', text='完成了')
        snapshot.update(seq=3, tasks=[{'id': 'task1', 'conversation': 'weixin', 'run_id': 'run1',
                                       'origin_message': 'reply:you'}], messages=[summary])
        gateway.apply_sync('owner', snapshot)
        self.assertEqual(self.store.outgoing_request('owner', 'runtime/run1-summary:reply#0'), 'reply')
        snapshot.update(seq=4, tasks=[], messages=[self.message(id='unlinked', kind='summary')])
        with self.assertRaises(ValueError):
            gateway.apply_sync('owner', snapshot)
        self.assertEqual(json.loads(self.store.cursor('sync:owner')), ['runtime', 'epoch', 3])
        snapshot.update(runtime_id='different', seq=5, messages=[])
        with self.assertRaises(ValueError):
            gateway.apply_sync('owner', snapshot)

    def test_reply_before_inbox_acceptance_does_not_advance_cursor(self):
        class Service:
            pass
        gateway = Gateway(self.store, Service(), 'wx', 'bot', 'owner')
        gateway.ingest([{'id': '1', 'sender': 'owner', 'payload': 'hi',
                         'context_token': 'private'}], 'next')
        gateway.bootstrap_sync('owner', {'runtime_id': 'runtime', 'epoch': 'epoch', 'seq': 1,
                                         'snapshot': {'seq': 1}})
        snapshot = {'runtime_id': 'runtime', 'epoch': 'epoch', 'seq': 2, 'messages': [self.message()]}
        with self.assertRaises(ValueError):
            gateway.apply_sync('owner', snapshot)
        self.assertEqual(json.loads(self.store.cursor('sync:owner')), ['runtime', 'epoch', 1])
        self.store.accepted('wx', 'bot', '1', 'reply')
        gateway.apply_sync('owner', snapshot)
        self.assertEqual(self.store.next_outgoing('owner')[0], 'runtime/reply#0')

    def test_recovery_notice_requires_exact_task_run_and_owner(self):
        gateway = Gateway(self.store, object(), 'wx', 'bot', 'owner')
        gateway.ingest([{'id': '1', 'sender': 'owner', 'payload': 'hi',
                         'context_token': 'private'}], 'next')
        self.store.accepted('wx', 'bot', '1', 'reply')
        gateway.bootstrap_sync('owner', {'runtime_id': 'runtime', 'epoch': 'epoch', 'seq': 1,
                                         'snapshot': {'seq': 1}})
        notice = self.message(id='run1-recovery-summary:reply', kind='summary', task='task1', text='整理中断了')
        gateway.apply_sync('owner', {'runtime_id': 'runtime', 'epoch': 'epoch', 'seq': 2,
            'tasks': [{'id': 'task1', 'conversation': 'weixin', 'run_id': 'run1', 'origin_message': 'reply:you'}],
            'messages': [notice]})
        self.assertEqual(self.store.outgoing_request('owner', 'runtime/run1-recovery-summary:reply#0'), 'reply')
        unbound = self.message(id='run2-recovery-summary:reply', kind='summary', task='task1', text='中断了')
        with self.assertRaises(ValueError):
            gateway.apply_sync('owner', {'runtime_id': 'runtime', 'epoch': 'epoch', 'seq': 3,
                                        'tasks': [], 'messages': [unbound]})

    def test_paged_task_result_can_find_only_its_exact_current_run(self):
        class Cards:
            def get(self, task_id):
                return {'id': task_id, 'conversation': 'weixin', 'run_id': 'run1',
                        'origin_message': 'reply:you'}
        class Service:
            card_store = Cards()
        gateway = Gateway(self.store, Service(), 'wx', 'bot', 'owner')
        gateway.ingest([{'id': '1', 'sender': 'owner', 'payload': 'hi', 'context_token': 'private'}], 'next')
        self.store.accepted('wx', 'bot', '1', 'reply')
        gateway.bootstrap_sync('owner', {'runtime_id': 'runtime', 'epoch': 'epoch', 'seq': 1,
                                         'snapshot': {'seq': 1}})
        summary = self.message(id='run1-summary:reply', kind='summary', task='task1')
        gateway.apply_sync('owner', {'runtime_id': 'runtime', 'epoch': 'epoch', 'seq': 2,
                                     'messages': [summary]})
        self.assertEqual(self.store.outgoing_request('owner', 'runtime/run1-summary:reply#0'), 'reply')
        stale = self.message(id='run2-summary:reply', kind='summary', task='task1')
        with self.assertRaises(ValueError):
            gateway.apply_sync('owner', {'runtime_id': 'runtime', 'epoch': 'epoch', 'seq': 3,
                                         'messages': [stale]})

    def test_gateway_retry_is_accepted_and_deduplicated_by_real_runtime_service(self):
        xiaoyu, runtime_store = make_xiaoyu(Path(self.tmp.name), [Scripted('voice')])
        service = RuntimeService(xiaoyu, runtime_store)
        self.addCleanup(service.close)
        with self.assertRaisesRegex(ValueError, 'restricted'):
            service._owner_writer.conversation_observe_weixin()
        with self.assertRaisesRegex(ValueError, 'internal grant'):
            service._owner_writer.conversation_claim_weixin({})
        with self.assertRaisesRegex(RequestError, '微信对话只能在微信里说'):
            service.submit('not a channel', conversation='weixin')
        with self.assertRaisesRegex(RequestError, '微信渠道未绑定'):
            service.submit('not bound', conversation='weixin', _channel_authorized=True)
        service._owner_writer.conversation_create_weixin()
        accepted = []

        class LostFirstAcknowledgement:
            def submit(self, text, **kwargs):
                result = service.submit(text, **kwargs)
                accepted.append(result['id'])
                if len(accepted) == 1:
                    raise TimeoutError('acceptance unknown')
                return result

        gateway = Gateway(self.store, LostFirstAcknowledgement(), 'wx', 'bot', 'owner')
        gateway.ingest([{'id': '1', 'sender': 'owner', 'payload': 'hi'}], 'next')
        with self.assertRaises(TimeoutError):
            gateway.submit_pending()
        self.assertEqual(gateway.submit_pending(), 1)
        self.assertEqual(accepted[0], accepted[1])
        result = service.get(accepted[0], wait=5)
        self.assertEqual(result['conversation'], 'weixin')
        self.assertEqual(result['client_id'], client_id('wx', 'bot', '1'))
        legacy = service.sync('default', all_conversations=True)
        self.assertFalse(any(item.get('conversation') == 'weixin' for item in legacy['messages']))
        self.assertFalse(any(item.get('id') == 'weixin' for item in legacy['conversations']))
        self.assertFalse(any(item.get('conversation') == 'weixin' for item in legacy['tasks']))
        with self.assertRaises(RequestError):
            service.sync('weixin')
        modern = service.sync('weixin', states=['weixin'])
        self.assertTrue(any(item.get('conversation') == 'weixin' for item in modern['messages']))
        self.assertTrue(any(item.get('id') == 'weixin' and item.get('kind') == 'weixin'
                            for item in modern['conversations']))
        self.assertFalse(any(item.get('conversation_id') == 'weixin'
                             for item in service.directory_page()['entries']))
        self.assertTrue(any(item.get('conversation_id') == 'weixin'
                            for item in service.directory_page(include_weixin=True)['entries']))
        with self.assertRaises(RequestError):
            service.cards('weixin')
        with self.assertRaises(RequestError):
            service.feed('weixin')
        with self.assertRaises(RequestError):
            service.reset('weixin')
        with self.assertRaises(RequestError):
            service.share('weixin', [{'id': 'fake', 'text': 'fake', 'reply': 'fake'}])
        with self.assertRaises(RequestError):
            service.handoff('weixin', 'another-runtime')

    @mock.patch('xiaoyu_runtime.channels.store.INPUT_PAUSE', 0)
    def test_fake_weixin_to_real_runtime_and_back(self):
        xiaoyu, runtime_store = make_xiaoyu(Path(self.tmp.name), [Scripted('voice')])
        service = RuntimeService(xiaoyu, runtime_store)
        self.addCleanup(service.close)
        service._owner_writer.conversation_create_weixin()
        with self.assertRaises(ValueError):
            service._owner_writer.channel_delivery('fake', 'fake#0', 'text', 'sent', 1)
        gateway = Gateway(self.store, service, 'wx', 'bot', 'owner')
        gateway.bootstrap_sync('owner', service.sync('weixin', states=['weixin']))
        class Transport:
            def __init__(self):
                self.sent = []
                self.result = 'failed'
            def updates(self, cursor):
                return {'get_updates_buf': 'next', 'msgs': [{
                    'message_type': 1, 'from_user_id': 'owner', 'message_id': 99,
                    'context_token': 'private', 'item_list': [{'type': 1, 'text_item': {'text': 'hello'}}]}]}
            def send_text(self, *args):
                self.sent.append(args)
                if self.result == 'failed':
                    raise WeixinError('definite failure')
                if self.result == 'unknown':
                    raise WeixinTransportError('delivery uncertain')
                return {'ret': 0}
        transport = Transport()
        self.assertEqual(gateway.poll_once(transport), 1)
        request = self.store.db.execute('SELECT runtime_id FROM inbox').fetchone()[0]
        self.assertEqual(service.get(request, wait=5)['status'], 'done')
        runtime, epoch, seq = json.loads(self.store.cursor('sync:owner'))
        snapshot = service.sync('weixin', after=seq, epoch=epoch, states=['weixin'])
        gateway.apply_sync('owner', snapshot)
        block = self.store.next_outgoing('owner')[0]
        def delivery():
            return next((m.get('delivery', {}).get(block) for m in
                         service.sync('weixin', states=['weixin'])['messages']
                         if block in m.get('delivery', {})), None)
        self.assertEqual(gateway.flush_delivery('owner'), 0)
        self.assertEqual(delivery(), 'waiting')
        self.assertEqual(gateway.send_next(transport, 'owner'), 'failed')
        self.assertEqual(gateway.flush_delivery('owner'), 0)
        self.assertEqual(delivery(), 'failed')
        self.assertEqual(self.store.retry('owner', block), 'queued')
        self.assertEqual(gateway.flush_delivery('owner'), 0)
        self.assertEqual(delivery(), 'waiting')
        transport.result = 'unknown'
        self.assertEqual(gateway.send_next(transport, 'owner'), 'unknown')
        self.assertEqual(gateway.flush_delivery('owner'), 0)
        self.assertEqual(delivery(), 'unknown')
        self.assertEqual(self.store.retry('owner', block), 'confirm_required')
        self.assertEqual(self.store.retry('owner', block, confirm_unknown=True), 'queued')
        transport.result = 'sent'
        self.assertEqual(gateway.send_next(transport, 'owner'), 'sent')
        self.assertEqual(gateway.flush_delivery('owner'), 0)
        self.assertEqual(delivery(), 'sent')
        self.assertEqual(transport.sent[0][0:2], ('owner', 'private'))
        self.assertEqual(transport.sent[0][3], 're:hello')
        self.assertEqual(gateway.send_next(transport, 'owner'), 'idle')
