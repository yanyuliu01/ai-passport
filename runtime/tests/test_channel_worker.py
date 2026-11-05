import tempfile
import json
import threading
import unittest
from pathlib import Path
from unittest import mock

from test_runtime import Scripted, make_xiaoyu
from xiaoyu_runtime.api.service import Service
from xiaoyu_runtime.channels.gateway import Gateway
from xiaoyu_runtime.channels.store import ChannelStore
from xiaoyu_runtime.channels.worker import ChannelWorker


class WorkerTest(unittest.TestCase):
    def test_authority_loss_during_long_poll_never_submits_late_message(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            xiaoyu, runtime_store = make_xiaoyu(root, [Scripted('voice')])
            service = Service(xiaoyu, runtime_store)
            self.addCleanup(service.close)
            service._owner_writer.conversation_create_weixin()
            started, release, lost = threading.Event(), threading.Event(), threading.Event()
            granted = [True]
            class Transport:
                def updates(self, cursor):
                    started.set()
                    release.wait(2)
                    return {'get_updates_buf': 'next', 'msgs': [{
                        'message_type': 1, 'from_user_id': 'owner', 'message_id': 1,
                        'context_token': 'private',
                        'item_list': [{'type': 1, 'text_item': {'text': 'late-input'}}]}]}
            class FastSync:
                runtime_id = service.runtime_id
                def sync(self, *args, **kwargs):
                    if kwargs.get('wait'):
                        kwargs['wait'] = 0.1
                    return service.sync(*args, **kwargs)
            path = root / 'channel.sqlite'
            worker = ChannelWorker(lambda: Gateway(ChannelStore(path), service, 'wx', 'bot', 'owner'),
                                   Transport, FastSync(), 'owner', authorized=lambda: granted[0],
                                   status=lambda state: lost.set() if state == 'ownership_unavailable' else None)
            worker.start()
            try:
                self.assertTrue(started.wait(2))
                granted[0] = False
                release.set()
                self.assertTrue(lost.wait(2))
                self.assertTrue(worker.stop(timeout=2))
                store = ChannelStore(path)
                try:
                    self.assertEqual(store.db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0], 0)
                finally:
                    store.close()
                self.assertFalse(any(row.get('text') == 'late-input' for row in
                                     service.sync('weixin', states=['weixin'])['messages']))
            finally:
                release.set()
                worker.stop(timeout=2)

    def test_partial_thread_start_failure_stops_started_thread(self):
        worker = ChannelWorker(lambda: mock.Mock(store=mock.Mock()), lambda: object(), mock.Mock(),
                               'owner', authorized=lambda: True)
        original = threading.Thread.start
        calls = []
        def start(thread):
            calls.append(thread)
            if len(calls) == 2:
                raise RuntimeError('cannot start output')
            return original(thread)
        with mock.patch.object(threading.Thread, 'start', start):
            with self.assertRaisesRegex(RuntimeError, 'cannot start output'):
                worker.start()
        self.assertTrue(worker.stop(timeout=2))

    def test_stop_waits_for_inflight_send_and_never_reports_early_success(self):
        entered, finish = threading.Event(), threading.Event()
        class Store:
            def recover_interrupted(self, destination):
                return 0
            def cursor(self, name):
                return json.dumps(['runtime', 'epoch', 1])
            def close(self):
                pass
        class GatewayStub:
            def __init__(self):
                self.store = Store()
            def poll_once(self, transport, cancelled=None):
                finish.wait(2)
                return 0
            def send_next(self, transport, destination):
                entered.set()
                finish.wait(2)
                return 'sent'
            def flush_delivery(self, destination):
                return 0
        service = mock.Mock(runtime_id='runtime')
        worker = ChannelWorker(GatewayStub, lambda: object(), service, 'owner', authorized=lambda: True)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertFalse(worker.stop(timeout=0.01))
            finish.set()
            self.assertTrue(worker.stop(timeout=2))
        finally:
            finish.set()
            worker.stop(timeout=2)

    def test_unknown_ownership_stops_before_recovery_or_network(self):
        store, transport = mock.Mock(), mock.Mock()
        gateway = mock.Mock(store=store)
        stopped = threading.Event()
        worker = ChannelWorker(lambda: gateway, lambda: transport, mock.Mock(), 'owner',
                               authorized=lambda: False,
                               status=lambda state: stopped.set() if state == 'ownership_unavailable' else None)
        worker.start()
        try:
            self.assertTrue(stopped.wait(2))
        finally:
            self.assertTrue(worker.stop(timeout=2))
        store.recover_interrupted.assert_not_called()
        transport.updates.assert_not_called()
        transport.send_text.assert_not_called()

    def test_reading_pause_can_be_interrupted_without_waiting_for_timer(self):
        entered = threading.Event()
        class Store:
            def recover_interrupted(self, destination): pass
            def cursor(self, name): return json.dumps(['runtime', 'epoch', 1])
            def close(self): pass
        class GatewayStub:
            def __init__(self): self.store = Store()
            def poll_once(self, transport, cancelled=None): return 0
            def send_next(self, transport, destination): entered.set(); return 'paced'
            def pacing_delay(self, destination): return 9
            def flush_delivery(self, destination): return 0
        worker = ChannelWorker(GatewayStub, lambda: object(), mock.Mock(runtime_id='runtime'), 'owner', authorized=lambda: True)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(worker.stop(timeout=1))
        finally:
            worker.stop(timeout=2)

    def test_cancelled_stale_bubble_does_not_stop_new_reply(self):
        delivered = threading.Event()
        statuses = []
        class Store:
            def recover_interrupted(self, destination): pass
            def cursor(self, name): return json.dumps(['runtime', 'epoch', 1])
            def close(self): pass
        class GatewayStub:
            def __init__(self): self.store, self.states = Store(), iter(('cancelled', 'sent', 'idle'))
            def poll_once(self, transport, cancelled=None): return 0
            def send_next(self, transport, destination):
                state = next(self.states, 'idle')
                if state == 'sent': delivered.set()
                return state
            def flush_delivery(self, destination): return 0
            def apply_sync(self, destination, snapshot): pass
        class FastService:
            runtime_id = 'runtime'
            def sync(self, *args, **kwargs): return {'seq': 1}
        worker = ChannelWorker(GatewayStub, lambda: object(), FastService(), 'owner', authorized=lambda: True,
                               status=statuses.append)
        worker.start()
        try:
            self.assertTrue(delivered.wait(2))
            self.assertNotIn('delivery_cancelled', statuses)
        finally:
            self.assertTrue(worker.stop(timeout=2))

    def test_long_inbound_wait_does_not_block_runtime_reply(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            xiaoyu, runtime_store = make_xiaoyu(root, [Scripted('voice')])
            service = Service(xiaoyu, runtime_store)
            self.addCleanup(service.close)
            service._owner_writer.conversation_create_weixin()
            store = ChannelStore(root / 'channel.sqlite')
            with store.db:
                store.db.execute("INSERT INTO outbox(destination, block_id, text, state) VALUES(?,?,?,?)",
                                 ('owner', 'previous-send', 'previous', 'sending'))
            store.close()
            delivered, release, recovered = threading.Event(), threading.Event(), threading.Event()
            calls = []
            class TrackingStore(ChannelStore):
                def recover_interrupted(self, destination):
                    result = super().recover_interrupted(destination)
                    recovered.set()
                    return result
            class Transport:
                def updates(self, cursor):
                    if not cursor:
                        return {'get_updates_buf': 'next', 'msgs': [{
                            'message_type': 1, 'from_user_id': 'owner', 'message_id': 1,
                            'context_token': 'private',
                            'item_list': [{'type': 1, 'text_item': {'text': 'hello'}}]}]}
                    release.wait(2)
                    return {'get_updates_buf': cursor, 'msgs': []}
                def send_text(self, *args):
                    calls.append(args)
                    delivered.set()
                    return {'ret': 0}
            class FastSync:
                runtime_id = service.runtime_id
                def sync(self, *args, **kwargs):
                    if kwargs.get('wait'):
                        kwargs['wait'] = 0.1
                    return service.sync(*args, **kwargs)
            worker = ChannelWorker(lambda: Gateway(TrackingStore(root / 'channel.sqlite'), service,
                                                  'wx', 'bot', 'owner'),
                                   Transport, FastSync(), 'owner', authorized=lambda: True)
            worker.start()
            try:
                self.assertTrue(recovered.wait(3))
                store = ChannelStore(root / 'channel.sqlite')
                try:
                    state = store.db.execute("SELECT state FROM outbox WHERE block_id='previous-send'").fetchone()[0]
                    self.assertEqual(state, 'unknown')
                    self.assertEqual(store.retry('owner', 'previous-send'), 'confirm_required')
                    # Model explicit operator resolution before allowing later
                    # replies through; uncertain sends intentionally block them.
                    with store.db:
                        store.db.execute("UPDATE outbox SET state='cancelled' WHERE block_id='previous-send'")
                finally:
                    store.close()
                self.assertTrue(delivered.wait(3))
                self.assertEqual(calls[0][3], 're:hello')
            finally:
                release.set()
                self.assertTrue(worker.stop(timeout=2))
