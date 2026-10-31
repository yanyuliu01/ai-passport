import json
import tempfile
import unittest
from pathlib import Path

from xiaoyu_runtime.delivery import presentation
from xiaoyu_runtime.channels.store import ChannelStore, client_id, public_blocks
from xiaoyu_runtime.channels.gateway import Gateway
from xiaoyu_runtime.api.service import Service as RuntimeService
from test_runtime import Scripted, make_xiaoyu


class ChannelStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'channel.sqlite'
        self.store = ChannelStore(self.path)
        self.addCleanup(self.store.close)

    def message(self, **changes):
        record = dict(id='reply', conversation='default', scope='chat',
                      author={'kind': 'xiaoyou', 'name': 'xiaoyou'}, kind='reply',
                      status='done', text='你好')
        record.update(changes)
        return presentation.message(record, 'runtime')

    def test_public_main_only_and_complete_blocks(self):
        self.assertEqual(public_blocks(self.message()), [('runtime/reply#0', '你好')])
        self.assertFalse(public_blocks(self.message(conversation='side-private')))
        self.assertFalse(public_blocks(self.message(scope='task')))
        self.assertFalse(public_blocks(self.message(author={'kind': 'agent', 'name': 'codex'})))
        self.assertFalse(public_blocks(self.message(status='running', partial='未完成')))
        self.assertFalse(public_blocks({'text': 'no presentation'}))

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

    def test_outbox_persists_and_does_not_retry_uncertain_send(self):
        self.store.bootstrap_sync('owner', 'epoch', 3)
        self.store.apply_sync('owner', 'epoch', 4, [self.message()])
        block, text = self.store.next_outgoing('owner')
        self.assertEqual(text, '你好')
        self.assertTrue(self.store.mark('owner', block, 'pending', 'sending'))
        self.store.close()
        self.store = ChannelStore(self.path)
        self.addCleanup(self.store.close)
        self.assertIsNone(self.store.next_outgoing('owner'))
        self.assertTrue(self.store.mark('owner', block, 'sending', 'unknown'))
        self.store.apply_sync('owner', 'epoch', 5, [self.message(), self.message(id='second')])
        self.assertIsNone(self.store.next_outgoing('owner'))
        self.assertEqual(json.loads(self.store.cursor('sync:owner')), ['epoch', 5])

    def test_revised_published_block_stops_cursor(self):
        self.store.bootstrap_sync('owner', 'epoch', 0)
        self.store.apply_sync('owner', 'epoch', 1, [self.message()])
        with self.assertRaises(ValueError):
            self.store.apply_sync('owner', 'epoch', 2, [self.message(text='不同')])
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

    def test_gateway_enforces_binding_and_submits_only_main(self):
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
        self.assertEqual(calls[0][1], {'conversation': 'default', 'client_id': client_id('wx', 'bot', '1')})

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

    def test_gateway_retry_is_accepted_and_deduplicated_by_real_runtime_service(self):
        xiaoyu, runtime_store = make_xiaoyu(Path(self.tmp.name), [Scripted('voice')])
        service = RuntimeService(xiaoyu, runtime_store)
        self.addCleanup(service.close)
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
        self.assertEqual(result['conversation'], 'default')
        self.assertEqual(result['client_id'], client_id('wx', 'bot', '1'))
