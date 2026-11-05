import tempfile
import sqlite3
import unittest
from pathlib import Path

from xiaoyu_runtime.channels.gateway import Gateway
from xiaoyu_runtime.channels.store import ChannelStore
from xiaoyu_runtime.channels.weixin import WeixinError, WeixinTransportError
from xiaoyu_runtime.entertainment.schedule import claim_test, record_test_state, request_test
from xiaoyu_runtime.entertainment.share import separate_video_links, split_video_share


class ProactiveShareTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = ChannelStore(Path(self.folder.name) / 'channel.sqlite')
        self.addCleanup(self.store.close)
        self.gateway = Gateway(self.store, None, 'weixin', 'bot-a', 'owner-a')

    def address(self, owner='owner-a', account='bot-a', token='context-a'):
        self.store.ingest('weixin', account, [{'sender': owner, 'id': owner + account,
                          'payload': 'hi', 'context_token': token}], '')
        self.store.accepted('weixin', account, owner + account, 'request-' + owner + account)

    def queue(self, key='share-1', text='A video https://www.bilibili.com/video/BV1Ev4w63EW4'):
        self.store.enqueue_proactive_share(key, text, 'bot-a', 'owner-a')

    def state(self, key='share-1'):
        return self.store.db.execute('SELECT state,outcome FROM proactive_share WHERE share_id=?', (key,)).fetchone()

    def test_bound_owner_latest_context_and_deduplication(self):
        self.address()
        self.address('owner-b', 'bot-b', 'wrong-context')
        self.queue()
        calls = []
        class Transport:
            def send_text(self, *args):
                calls.append(args)
                return {'ret': 0}
        self.assertEqual('sent', self.gateway.send_proactive_share(Transport(), 'weixin'))
        self.assertEqual(('owner-a', 'context-a'), calls[0][:2])
        self.assertEqual('idle', self.gateway.send_proactive_share(Transport(), 'weixin'))
        self.queue()
        self.assertEqual(1, len(calls))
        self.assertEqual(('sent', 'api_accepted'), self.state())

    def test_other_account_and_missing_context_never_send(self):
        self.queue()
        self.address('owner-b', 'bot-b')
        self.assertEqual('idle', self.gateway.send_proactive_share(None, 'weixin'))
        self.assertEqual('pending', self.state()[0])
        other = Gateway(self.store, None, 'weixin', 'bot-b', 'owner-b')
        self.assertEqual('idle', other.send_proactive_share(None, 'weixin'))

    def test_uncertain_and_failed_shares_do_not_block_ordinary_replies(self):
        self.address()
        self.queue()
        class Uncertain:
            def send_text(self, *args):
                raise WeixinTransportError('no result')
        self.assertEqual('unknown', self.gateway.send_proactive_share(Uncertain(), 'weixin'))
        self.assertEqual(('unknown', 'transport_uncertain'), self.state())
        self.queue('share-2', 'another share')
        class Failed:
            def send_text(self, *args):
                raise WeixinError('rejected')
        self.assertEqual('failed', self.gateway.send_proactive_share(Failed(), 'weixin'))
        self.assertEqual(('failed', 'upstream_error'), self.state('share-2'))
        self.assertEqual('idle', self.gateway.send_next(None, 'weixin'))

    def test_interrupted_share_is_never_replayed_and_pending_reply_has_priority(self):
        self.address()
        self.queue()
        self.store.db.execute("INSERT INTO outbox(destination,block_id,text,request_id) VALUES('weixin','block','reply','r')")
        self.store.db.commit()
        self.assertEqual('idle', self.gateway.send_proactive_share(None, 'weixin'))
        self.store.db.execute("UPDATE outbox SET state='sent'")
        self.store.db.commit()
        self.assertIsNotNone(self.store.claim_proactive_share('weixin', 'bot-a', 'owner-a', 'weixin'))
        self.store.recover_proactive_shares()
        self.assertEqual(('unknown', 'interrupted'), self.state())
        self.assertEqual('idle', self.gateway.send_proactive_share(None, 'weixin'))

    def test_old_queued_share_expires(self):
        self.address()
        self.queue()
        self.store.db.execute('UPDATE proactive_share SET created_at=0')
        self.store.db.commit()
        self.assertEqual('idle', self.gateway.send_proactive_share(None, 'weixin'))
        self.assertEqual(('expired', 'expired'), self.state())

    def test_video_share_sends_recommendation_then_link_once(self):
        self.address()
        url = 'https://www.bilibili.com/video/BV1Ev4w63EW4'
        self.store.enqueue_proactive_video('video', '这条挺俏皮。', url, 'bot-a', 'owner-a')
        calls = []
        class Transport:
            def send_text(self, *args):
                calls.append(args)
                return {'ret': 0}
        transport = Transport()
        self.assertEqual('idle', self.gateway.send_proactive_link(transport, 'weixin'))
        self.assertEqual('sent', self.gateway.send_proactive_share(transport, 'weixin'))
        self.assertEqual('sent', self.gateway.send_proactive_link(transport, 'weixin'))
        self.assertEqual(['这条挺俏皮。', url], [call[3] for call in calls])
        self.assertEqual(('owner-a', 'context-a'), calls[1][:2])
        self.assertNotEqual(calls[0][2], calls[1][2])
        self.store.enqueue_proactive_video('video', '这条挺俏皮。', url, 'bot-a', 'owner-a')
        self.assertEqual('idle', self.gateway.send_proactive_share(transport, 'weixin'))
        self.assertEqual('idle', self.gateway.send_proactive_link(transport, 'weixin'))
        self.assertEqual(2, len(calls))

    def test_link_is_cancelled_when_recommendation_delivery_is_uncertain(self):
        self.address()
        url = 'https://www.bilibili.com/video/BV1Ev4w63EW4'
        self.store.enqueue_proactive_video('video', '分享给你。', url, 'bot-a', 'owner-a')
        class Uncertain:
            def send_text(self, *args): raise WeixinTransportError('unknown')
        self.assertEqual('unknown', self.gateway.send_proactive_share(Uncertain(), 'weixin'))
        self.assertEqual('idle', self.gateway.send_proactive_link(None, 'weixin'))
        self.assertEqual(('cancelled', 'text_not_confirmed'), self.store.db.execute(
            'SELECT state,outcome FROM proactive_link WHERE share_id=?', ('video',)).fetchone())

    def test_link_recovery_and_owner_binding_never_duplicate(self):
        self.address()
        self.address('owner-b', 'bot-b', 'other')
        url = 'https://www.bilibili.com/video/BV1Ev4w63EW4'
        self.store.enqueue_proactive_video('video', '分享给你。', url, 'bot-a', 'owner-a')
        other = Gateway(self.store, None, 'weixin', 'bot-b', 'owner-b')
        self.assertEqual('idle', other.send_proactive_link(None, 'weixin'))
        class Text:
            def send_text(self, *args): return {'ret': 0}
        self.assertEqual('sent', self.gateway.send_proactive_share(Text(), 'weixin'))
        self.store.db.execute("INSERT INTO outbox(destination,block_id,text,request_id) VALUES('weixin','reply','hi','r')")
        self.store.db.commit()
        self.assertEqual('idle', self.gateway.send_proactive_link(None, 'weixin'))
        self.store.db.execute("UPDATE outbox SET state='sent'")
        self.store.db.commit()
        self.assertIsNotNone(self.store.claim_proactive_link('weixin', 'bot-a', 'owner-a', 'weixin'))
        self.store.recover_proactive_shares()
        self.assertEqual(('unknown', 'interrupted'), self.store.db.execute(
            'SELECT state,outcome FROM proactive_link WHERE share_id=?', ('video',)).fetchone())
        self.assertEqual('idle', self.gateway.send_proactive_link(None, 'weixin'))

    def test_extracts_one_safe_video_link_without_tracking_parameters(self):
        url = 'https://www.bilibili.com/video/BV1Ev4w63EW4'
        text = '这条挺俏皮——想看的话在这儿：' + url + '?source=share'
        self.assertEqual(('这条挺俏皮', url), split_video_share(text))
        self.assertEqual('这条挺俏皮\n---\n' + url, separate_video_links(text, '\n---\n'))
        self.assertEqual(('这条很可爱。', url), split_video_share('这条很可爱。\n' + url))
        self.assertIsNone(split_video_share('只有一句话'))
        self.assertIsNone(split_video_share('两条 ' + url + ' ' + url))
        self.assertIsNone(split_video_share('奇怪 ' + url.replace('www.bilibili.com', 'www.bilibili.com.evil.test')))
        second = 'https://www.bilibili.com/video/BV1GJ411x7h7'
        separated = separate_video_links('第一条 ' + url + '\n第二条 ' + second, '\n---\n')
        self.assertEqual(['第一条', url, '第二条', second], separated.split('\n---\n'))
        self.assertEqual('视频\n---\n' + url, separate_video_links('[视频](' + url + ')', '\n---\n'))
        original = '其他网站 https://example.com/'
        self.assertEqual(original, separate_video_links(original, '\n---\n'))

    def test_one_test_opportunity_is_consumed_once_and_expires(self):
        schedule = Path(self.folder.name) / 'schedule.sqlite'
        request_test(schedule, 100)
        self.assertTrue(claim_test(schedule, 101))
        record_test_state(schedule, 'no_browse')
        with sqlite3.connect(str(schedule)) as db:
            self.assertEqual('no_browse', db.execute('SELECT state FROM test_result WHERE id=1').fetchone()[0])
        self.assertFalse(claim_test(schedule, 102))
        request_test(schedule, 100)
        self.assertFalse(claim_test(schedule, 3701))
        self.assertFalse(claim_test(schedule, 101))
