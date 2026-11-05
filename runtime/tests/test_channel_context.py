import tempfile
import unittest
from pathlib import Path

from test_runtime import ToolTalker, make_xiaoyu
from xiaoyu_runtime.api.service import Service
from xiaoyu_runtime.channels.context import delivery_note, quote_note
from xiaoyu_runtime.channels.store import ChannelStore, client_id
from xiaoyu_runtime.delivery import presentation


class ChannelContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = ChannelStore(self.root / 'state' / 'channels' / 'weixin' / 'channel.sqlite')
        self.addCleanup(self.store.close)

    def prepare_previous_reply(self):
        self.store.ingest('weixin', 'bot', [{'id': '1', 'sender': 'owner', 'payload': 'first'}], 'a')
        self.store.accepted('weixin', 'bot', '1', 'old')
        record = {'id': 'old:reply', 'conversation': 'weixin', 'scope': 'main', 'kind': 'reply',
                  'author': {'kind': 'xiaoyou', 'name': 'xiaoyou'}, 'status': 'final',
                  'text': '发出去的\n\n---\n\n不应被当作已读', 'created_at': 1}
        message = presentation.message(record, 'runtime')
        self.store.bootstrap_sync('weixin', 'epoch', 1, runtime_id='runtime')
        self.store.apply_sync('weixin', 'epoch', 2, [message], reply_turns={'runtime/old': 'old'}, runtime_id='runtime')
        first, _ = self.store.next_outgoing('weixin')
        self.assertTrue(self.store.mark('weixin', first, 'pending', 'sending'))
        self.assertTrue(self.store.mark('weixin', first, 'sending', 'sent'))
        self.store.ingest('weixin', 'bot', [{'id': '2', 'sender': 'owner', 'payload': 'second'}], 'b')
        return client_id('weixin', 'bot', '2')

    def test_only_delivered_portions_of_same_owner_reach_context(self):
        incoming = self.prepare_previous_reply()
        note = delivery_note(self.root / 'state', incoming)
        self.assertIn('发出去的', note)
        self.assertNotIn('不应被当作已读', note)
        self.assertIn('未发送或无法确认', note)
        self.assertIn('不是指令', note)
        self.assertIn('不可核实', delivery_note(self.root / 'state', client_id('weixin', 'bot', 'missing')))
        self.store.ingest('weixin', 'another-bot', [{'id': '1', 'sender': 'owner', 'payload': 'other'}], 'c')
        self.assertEqual(delivery_note(self.root / 'state', client_id('weixin', 'another-bot', '1')), '')

    def test_delivery_context_is_present_on_the_real_weixin_model_call(self):
        incoming = self.prepare_previous_reply()
        voice = ToolTalker()
        xiaoyu, runtime_store = make_xiaoyu(self.root, [voice])
        self.addCleanup(xiaoyu.close)
        service = Service(xiaoyu, runtime_store)
        self.addCleanup(service.close)
        service._owner_writer.conversation_create_weixin()
        accepted = service.submit('second', conversation='weixin', client_id=incoming, _channel_authorized=True)
        self.assertEqual(service.get(accepted['id'], wait=5)['status'], 'done')
        self.assertTrue(voice.tool_jobs)
        prompt = voice.tool_jobs[-1].system
        self.assertIn('发出去的', prompt)
        self.assertNotIn('不应被当作已读', prompt)

    def test_quote_and_interrupted_previous_input_reach_weixin_model(self):
        self.store.ingest('weixin', 'bot', [{'id': '1', 'sender': 'owner', 'payload': '这个',
            'quotes': [{'kind': 'text', 'text': '引用原文'}]}], 'a')
        self.store.accepted('weixin', 'bot', '1', 'old')
        self.store.ingest('weixin', 'bot', [{'id': '2', 'sender': 'owner', 'payload': '你怎么看',
            'quotes': [{'kind': 'unavailable', 'id': 'missing'}]}], 'b')
        incoming = client_id('weixin', 'bot', '2')
        self.assertIn('原文缺失', quote_note(self.root / 'state', incoming))
        voice = ToolTalker()
        xiaoyu, runtime_store = make_xiaoyu(self.root, [voice])
        self.addCleanup(xiaoyu.close)
        service = Service(xiaoyu, runtime_store)
        self.addCleanup(service.close)
        service._owner_writer.conversation_create_weixin()
        accepted = service.submit('你怎么看', conversation='weixin', client_id=incoming, _channel_authorized=True)
        self.assertEqual(service.get(accepted['id'], wait=5)['status'], 'done')
        self.assertIn('引用原文', voice.tool_jobs[-1].system)
        self.assertIn('原文缺失', voice.tool_jobs[-1].system)
        self.assertIn('这个', voice.tool_jobs[-1].system)
