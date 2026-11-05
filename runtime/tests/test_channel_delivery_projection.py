import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from xiaoyu_runtime.events.message_log import MessageLog
from xiaoyu_runtime.delivery import presentation


class DeliveryProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'messages.jsonl'
        self.seq = 0
        self.log = MessageLog(self.path, self.bump)
        self.log.set_origin('runtime')

    def bump(self):
        self.seq += 1
        return self.seq

    def message(self, **updates):
        record = {'id': 'request:reply', 'conversation': 'weixin', 'scope': 'main',
                  'author': {'kind': 'xiaoyou', 'name': 'xiaoyou'}, 'kind': 'reply',
                  'source': {'type': 'runtime'}, 'status': 'streaming',
                  'text': '', 'partial': '已完成\n---\n后续未完成', 'attachments': [], 'created_at': time.time()}
        record.update(updates)
        self.log._put(record, strict=True)
        return record

    def test_streaming_complete_block_projects_idempotently_and_survives_reload(self):
        self.message()
        block = 'runtime/request:reply#0'
        self.assertTrue(self.log.channel_delivery('request:reply', block, '已完成', 'sent', 3))
        seq = self.seq
        self.assertFalse(self.log.channel_delivery('request:reply', block, '已完成', 'sent', 3))
        with self.assertRaises(ValueError):
            self.log.channel_delivery('request:reply', block, '已完成', 'waiting', 1)
        self.assertEqual(self.seq, seq)
        with self.assertRaises(ValueError):
            self.log.channel_delivery('request:reply', block, '已完成', 'unknown', 3)
        with self.assertRaises(ValueError):
            self.log.channel_delivery('request:reply', 'runtime/request:reply#1', '后续未完成', 'sent', 1)
        shown = presentation.message(self.log.get('request:reply'), 'runtime')
        self.assertEqual(shown['delivery'], {block: 'sent'})
        self.assertNotIn('_delivery_revisions', shown)
        self.assertNotIn('_delivery_hashes', shown)
        changed = self.log.get('request:reply')
        changed['partial'] = '不同内容\n---\n后续未完成'
        self.assertNotIn('delivery', presentation.message(changed, 'runtime'))
        reloaded = MessageLog(self.path, self.bump)
        reloaded.set_origin('runtime')
        self.assertEqual(reloaded.get('request:reply')['delivery'], {block: 'sent'})
        # Reload intentionally discards streaming text, so stale blocks cannot
        # be acknowledged until the canonical source is available again.
        with self.assertRaises(ValueError):
            reloaded.channel_delivery('request:reply', block, '已完成', 'sent', 3)

    def test_cancelled_unsent_block_disappears_but_delivered_text_remains(self):
        self.message(status='final', text='已发出\n\n---\n\n还没发出', partial='')
        first, second = 'runtime/request:reply#0', 'runtime/request:reply#1'
        self.assertTrue(self.log.channel_delivery('request:reply', first, '已发出', 'sent', 2))
        before = presentation.message(self.log.get('request:reply'), 'runtime')['presentation']['input_revision']
        self.assertTrue(self.log.channel_delivery('request:reply', second, '还没发出', 'cancelled', 2))
        view = presentation.message(self.log.get('request:reply'), 'runtime')['presentation']
        self.assertEqual([block['text'] for block in view['blocks']], ['已发出'])
        self.assertEqual(view['plain'], '已发出')
        self.assertNotEqual(view['input_revision'], before)
        # The canonical record and its revisioned delivery evidence remain
        # available even though the unsent bubble is not shown as delivered.
        self.assertIn('还没发出', self.log.get('request:reply')['text'])
        self.assertEqual(self.log.get('request:reply')['delivery'][second], 'cancelled')
        self.assertFalse(self.log.channel_delivery('request:reply', second, '还没发出', 'cancelled', 2))

    def test_channel_tracking_survives_request_to_card_projection(self):
        self.log.request({'id': 'turn', 'conversation': 'weixin', 'client_id': 'channel-abc',
                          'status': 'running', 'text': '你好', 'created_at': 1})
        reply = self.log.get('turn:reply')
        self.assertTrue(presentation.message(reply, 'runtime')['presentation']['channel_delivery_tracked'])
        self.log.observe({'id': 'card', 'conversation': 'weixin', 'entries': [
            {'id': 'turn:reply', 'role': 'xiaoyou', 'text': '你好', 'at': 2}]})
        reply = self.log.get('turn:reply')
        self.assertTrue(presentation.message(reply, 'runtime')['presentation']['channel_delivery_tracked'])

    def test_task_summary_inherits_channel_delivery_tracking(self):
        self.log.request({'id': 'turn', 'conversation': 'weixin', 'client_id': 'channel-abc',
                          'status': 'running', 'text': '帮我查', 'created_at': 1})
        card = {'id': 'card', 'conversation': 'weixin', 'origin_message': 'turn:you',
                'entries': [], 'run_id': 'run', 'state': 'talking', 'summary_partial': '正在整理', 'updated_at': 2}
        self.log.observe(card)
        summary = self.log.get('run-summary:reply')
        self.assertTrue(presentation.message(summary, 'runtime')['presentation']['channel_delivery_tracked'])
        card.update(state='done', summary_partial='', entries=[
            {'id': 'run-summary:reply', 'role': 'xiaoyou', 'kind': 'summary', 'text': '任务结果', 'at': 3}])
        self.log.observe(card)
        summary = self.log.get('run-summary:reply')
        self.assertTrue(presentation.message(summary, 'runtime')['presentation']['channel_delivery_tracked'])

    @mock.patch('xiaoyu_runtime.channels.store.INPUT_PAUSE', 0)
    def test_superseded_bubble_status_reconciles_with_shared_history(self):
        from xiaoyu_runtime.channels.gateway import Gateway
        from xiaoyu_runtime.channels.store import ChannelStore
        class Service:
            runtime_id = 'runtime'
            def _channel_delivery(_, *args):
                return self.log.channel_delivery(*args)
        class Transport:
            def send_text(_, *args): return {'ret': 0}
        store = ChannelStore(Path(self.tmp.name) / 'channel.sqlite')
        self.addCleanup(store.close)
        gateway = Gateway(store, Service(), 'wx', 'bot', 'owner')
        gateway.ingest([{'id': 'one', 'sender': 'owner', 'payload': 'one', 'context_token': 'token'}], 'a')
        store.accepted('wx', 'bot', 'one', 'request')
        gateway.bootstrap_sync('owner', {'runtime_id': 'runtime', 'epoch': 'epoch', 'seq': 1, 'snapshot': {'seq': 1}})
        self.message(status='final', text='已发出\n\n---\n\n还没发出', partial='')
        message = presentation.message(self.log.get('request:reply'), 'runtime')
        gateway.apply_sync('owner', {'runtime_id': 'runtime', 'epoch': 'epoch', 'seq': 2, 'messages': [message]})
        self.assertEqual(gateway.send_next(Transport(), 'owner'), 'sent')
        self.assertEqual(gateway.flush_delivery('owner'), 0)
        gateway.ingest([{'id': 'two', 'sender': 'owner', 'payload': 'two', 'context_token': 'token'}], 'b')
        self.assertEqual(gateway.send_next(Transport(), 'owner'), 'cancelled')
        self.assertEqual(gateway.flush_delivery('owner'), 0)
        message = presentation.message(self.log.get('request:reply'), 'runtime')
        self.assertEqual([block['text'] for block in message['presentation']['blocks']], ['已发出'])
        gateway.apply_sync('owner', {'runtime_id': 'runtime', 'epoch': 'epoch', 'seq': 3, 'messages': [message]})
        self.assertEqual(gateway.send_next(Transport(), 'owner'), 'idle')
        self.assertEqual(store.db.execute("SELECT state FROM outbox ORDER BY rowid").fetchall(),
                         [('sent',), ('cancelled',)])

    def test_forged_foreign_changed_or_non_chat_blocks_fail_closed(self):
        for updates in ({'conversation': 'default'}, {'scope': 'task'},
                        {'author': {'kind': 'owner', 'name': 'you'}},
                        {'mesh_origin': 'other'}, {'relay_to': 'other'}):
            self.message(**updates)
            with self.assertRaises(ValueError):
                self.log.channel_delivery('request:reply', 'runtime/request:reply#0', '已完成', 'sent', 1)
        self.message()
        for block, text in [('other/request:reply#0', '已完成'),
                            ('runtime/request:reply#00', '已完成'),
                            ('runtime/request:reply#0', '已变更')]:
            with self.assertRaises(ValueError):
                self.log.channel_delivery('request:reply', block, text, 'sent', 1)

    def test_media_delivery_is_bound_to_the_exact_final_assistant_attachment(self):
        artifact = 'a' + '1' * 32
        block = 'runtime/request:reply#media:' + artifact
        meta = {'id': artifact, 'status': 'ready', 'source': 'cc'}
        self.message(status='final', text='完成', attachments=[meta])
        self.assertTrue(self.log.channel_delivery('request:reply', block, artifact, 'unknown', 2))
        shown = presentation.message(self.log.get('request:reply'), 'runtime')
        self.assertEqual(shown['delivery'][block], 'unknown')
        self.assertEqual(shown['delivery_revisions'][block], 2)
        self.assertEqual(shown['presentation']['channel_delivery'][block], 'unknown')
        changed = self.log.get('request:reply')
        changed['attachments'] = []
        self.assertNotIn('delivery', presentation.message(changed, 'runtime'))
        self.message(status='final', text='完成', attachments=[dict(meta, source='owner')])
        with self.assertRaises(ValueError):
            self.log.channel_delivery('request:reply', block, artifact, 'sent', 3)

    def test_attachment_delivery_reaches_canonical_history_with_no_duplicate_on_resync(self):
        import hashlib
        import io
        from xiaoyu_runtime.channels.gateway import Gateway
        from xiaoyu_runtime.channels.store import ChannelStore
        data, artifact = b'output file', 'a' + '2' * 32
        meta = {'id': artifact, 'status': 'ready', 'source': 'cc', 'name': 'out.txt',
                'sha256': hashlib.sha256(data).hexdigest()}
        class Artifacts:
            def open_content(self, identity):
                return io.BytesIO(data), meta, 'text/plain', len(data)
        class Service:
            runtime_id = 'runtime'
            artifacts = Artifacts()
            def _channel_delivery(_, *args):
                return self.log.channel_delivery(*args)
        class Transport:
            calls = 0
            def send_media(_, *args):
                _.calls += 1
                return {'ret': 0}
        store = ChannelStore(Path(self.tmp.name) / 'channel.sqlite')
        self.addCleanup(store.close)
        self.message(status='final', text='', attachments=[meta])
        gateway = Gateway(store, Service(), 'wx', 'bot', 'owner')
        gateway.ingest([{'id': 'input', 'sender': 'owner', 'payload': 'hi', 'context_token': 'private'}], 'next')
        store.accepted('wx', 'bot', 'input', 'request')
        gateway.bootstrap_sync('owner', {'runtime_id': 'runtime', 'epoch': 'epoch', 'seq': 1, 'snapshot': {'seq': 1}})
        message = presentation.message(self.log.get('request:reply'), 'runtime')
        gateway.apply_sync('owner', {'runtime_id': 'runtime', 'epoch': 'epoch', 'seq': 2, 'messages': [message]})
        transport = Transport()
        self.assertEqual(gateway.send_next(transport, 'owner'), 'sent')
        self.assertEqual(gateway.flush_delivery('owner'), 0)
        message = presentation.message(self.log.get('request:reply'), 'runtime')
        self.assertEqual(message['delivery']['runtime/request:reply#media:' + artifact], 'sent')
        gateway.apply_sync('owner', {'runtime_id': 'runtime', 'epoch': 'epoch', 'seq': 3, 'messages': [message]})
        self.assertEqual(gateway.send_next(transport, 'owner'), 'idle')
        self.assertEqual(transport.calls, 1)
