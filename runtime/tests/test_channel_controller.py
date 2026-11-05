import tempfile
import threading
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from xiaoyu_runtime.channels.controller import Controller
from xiaoyu_runtime.channels.store import ChannelStore


class Client:
    base = 'https://ilinkai.weixin.qq.com'
    def qr(self):
        return {'qrcode': 'private-qr', 'qrcode_img_content': 'private-image'}
    def qr_status(self, qr, verify_code=None):
        return {'status': 'confirmed', 'bot_token': 'private-token',
                'ilink_bot_id': 'private-bot', 'ilink_user_id': 'private-owner'}


class Writer:
    def conversation_create_weixin(self):
        return {'id': 'weixin', 'kind': 'weixin', 'owner': 'runtime'}


class Service:
    runtime_id = 'runtime'
    _owner_writer = Writer()


class ControllerTest(unittest.TestCase):
    def test_retry_requires_live_grant_and_reuses_the_same_operation(self):
        with tempfile.TemporaryDirectory() as folder:
            granted = [True]
            controller = Controller(Service(), folder, host_granted=lambda: granted[0])
            controller.root.mkdir(parents=True, exist_ok=True)
            store = ChannelStore(controller.root / 'channel.sqlite')
            store.db.execute("INSERT INTO outbox(destination,block_id,text,state) VALUES(?,?,?,?)",
                             ('weixin', 'runtime/reply#0', 'reply', 'unknown'))
            store.db.commit()
            controller._worker = mock.Mock()
            self.assertEqual('confirm_required', controller.retry_delivery('runtime/reply#0', 'a' * 32, 1))
            self.assertEqual('queued', controller.retry_delivery('runtime/reply#0', 'b' * 32, 1, confirm_unknown=True))
            self.assertTrue(store.mark('weixin', 'runtime/reply#0', 'pending', 'sending'))
            self.assertTrue(store.mark('weixin', 'runtime/reply#0', 'sending', 'unknown'))
            self.assertEqual('queued', controller.retry_delivery('runtime/reply#0', 'b' * 32, 1, confirm_unknown=True))
            self.assertIsNone(store.next_outgoing('weixin'))
            granted[0] = False
            with self.assertRaises(ValueError):
                controller.retry_delivery('runtime/reply#0', 'c' * 32, 1, confirm_unknown=True)
            with self.assertRaises(ValueError):
                controller.retry_delivery('other/reply#0', 'd' * 32, 1, confirm_unknown=True)
            store.close()
    def test_binding_requires_grant_and_public_status_has_no_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            granted = [False]
            controller = Controller(Service(), folder, host_granted=lambda: granted[0],
                                    client_factory=lambda **_: Client())
            with self.assertRaises(ValueError):
                controller.begin()
            granted[0] = True
            attempt = controller.begin()['id']
            with mock.patch('xiaoyu_runtime.channels.controller.ChannelWorker') as worker:
                self.assertEqual(controller.poll(attempt), {'state': 'confirmed'})
                worker.return_value.start.assert_called_once()
            self.assertEqual(controller.credentials.load()['owner'], 'private-owner')
            self.assertNotIn('private', str(controller.status()))
            self.assertNotIn('private', str(list(Path(folder).glob('*.json'))))
            self.assertTrue(controller.close())

    def test_changed_grant_prevents_saving_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            granted = [True]
            controller = Controller(Service(), folder, host_granted=lambda: granted[0],
                                    client_factory=lambda **_: Client())
            attempt = controller.begin()['id']
            granted[0] = False
            with self.assertRaises(ValueError):
                controller.poll(attempt)
            self.assertIsNone(controller.credentials.load())
            controller.close()

    def test_local_process_lock_stays_held_during_worker_and_releases_on_close(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Controller(Service(), folder, host_granted=lambda: True, client_factory=lambda **_: Client())
            second = Controller(Service(), folder, host_granted=lambda: True, client_factory=lambda **_: Client())
            attempt = first.begin()['id']
            with self.assertRaisesRegex(ValueError, '已有微信渠道进程'):
                second.begin()
            with mock.patch('xiaoyu_runtime.channels.controller.ChannelWorker') as worker:
                worker.return_value.stop.return_value = False
                first.poll(attempt)
                self.assertFalse(first.cancel(attempt))
                self.assertFalse(first.close())
                with self.assertRaisesRegex(ValueError, '已有微信渠道进程'):
                    second.begin()
                worker.return_value.stop.return_value = True
                self.assertTrue(first.close())
                another = second.begin()['id']
                self.assertTrue(second.cancel(another))
                self.assertTrue(second.close())

    def test_new_controller_cannot_acknowledge_release_while_another_owns_host(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Controller(Service(), folder, host_granted=lambda: True, client_factory=lambda **_: Client())
            other = Controller(Service(), folder, host_granted=lambda: True, client_factory=lambda **_: Client())
            attempt = first.begin()['id']
            self.assertFalse(other.prepare_release())
            self.assertTrue(first.cancel(attempt))
            self.assertTrue(other.prepare_release())
            self.assertTrue(other.close())
            self.assertTrue(first.close())

    def test_release_marks_a_crashed_prior_send_unknown_before_acknowledging(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'channels' / 'weixin' / 'channel.sqlite'
            store = ChannelStore(path)
            with store.db:
                store.db.execute('INSERT INTO outbox(destination,block_id,text,state) VALUES(?,?,?,?)',
                                 ('weixin', 'prior-block', 'reply', 'sending'))
            store.close()
            controller = Controller(Service(), folder, host_granted=lambda: False, client_factory=lambda **_: Client())
            try:
                self.assertTrue(controller.prepare_release())
                store = ChannelStore(path)
                try:
                    self.assertEqual(store.retry('weixin', 'prior-block'), 'confirm_required')
                finally:
                    store.close()
            finally:
                controller.close()

    def test_release_keeps_host_lock_if_delivery_state_is_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'channels' / 'weixin' / 'channel.sqlite'
            ChannelStore(path).close()
            locked = sqlite3.connect(str(path))
            locked.execute('BEGIN EXCLUSIVE')
            controller = Controller(Service(), folder, host_granted=lambda: False, client_factory=lambda **_: Client())
            other = Controller(Service(), folder, host_granted=lambda: False, client_factory=lambda **_: Client())
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    controller.prepare_release()
                self.assertFalse(other.prepare_release())
                locked.rollback()
                self.assertTrue(controller.prepare_release())
                self.assertTrue(controller.close())
                self.assertTrue(other.close())
            finally:
                locked.close()
                controller.close(timeout=1)
                other.close(timeout=1)

    def test_restart_requires_grant_and_local_lock_before_worker_resumes(self):
        with tempfile.TemporaryDirectory() as folder:
            granted = [True]
            first = Controller(Service(), folder, host_granted=lambda: granted[0], client_factory=lambda **_: Client())
            attempt = first.begin()['id']
            with mock.patch('xiaoyu_runtime.channels.controller.ChannelWorker') as worker:
                worker.return_value.stop.return_value = True
                first.poll(attempt)
                other = Controller(Service(), folder, host_granted=lambda: granted[0], client_factory=lambda **_: Client())
                with self.assertRaisesRegex(ValueError, '已有微信渠道进程'):
                    other.resume()
                self.assertTrue(first.close())
                granted[0] = False
                with self.assertRaisesRegex(ValueError, '负责人尚未确认'):
                    other.resume()
                granted[0] = True
                self.assertTrue(other.resume())
                self.assertEqual(other.status()['state'], 'connecting')
                self.assertTrue(other.close())

    def test_late_qr_does_not_escape_after_new_scan_replaces_it(self):
        with tempfile.TemporaryDirectory() as folder:
            started, release = threading.Event(), threading.Event()
            class SlowClient(Client):
                def qr(self):
                    started.set()
                    release.wait(2)
                    return super().qr()
            clients = iter([SlowClient(), Client()])
            controller = Controller(Service(), folder, host_granted=lambda: True,
                                    client_factory=lambda **_: next(clients))
            outcome = []
            def begin():
                try:
                    outcome.append(controller.begin())
                except ValueError:
                    outcome.append('cancelled')
            thread = threading.Thread(target=begin)
            thread.start()
            try:
                self.assertTrue(started.wait(2))
                current = controller.begin()
                release.set()
                thread.join(2)
                self.assertEqual(outcome, ['cancelled'])
                self.assertTrue(controller.cancel(current['id']))
            finally:
                release.set()
                thread.join(2)
                controller.close()

    def test_release_waits_for_inflight_qr_and_keeps_host_lock_until_acknowledged(self):
        with tempfile.TemporaryDirectory() as folder:
            started, release = threading.Event(), threading.Event()
            class SlowClient(Client):
                def qr(self):
                    started.set()
                    release.wait(2)
                    return super().qr()
            controller = Controller(Service(), folder, host_granted=lambda: True,
                                    client_factory=lambda **_: SlowClient())
            other = Controller(Service(), folder, host_granted=lambda: True,
                               client_factory=lambda **_: Client())
            outcome = []
            def begin():
                try:
                    controller.begin()
                    outcome.append('unexpected-success')
                except ValueError:
                    outcome.append('cancelled')
            thread = threading.Thread(target=begin)
            thread.start()
            try:
                self.assertTrue(started.wait(2))
                self.assertFalse(controller.prepare_release())
                with self.assertRaisesRegex(ValueError, '已有微信渠道进程'):
                    other.begin()
                release.set()
                thread.join(2)
                self.assertEqual(outcome, ['cancelled'])
                self.assertTrue(controller.prepare_release(timeout=1))
                with self.assertRaisesRegex(ValueError, '已有微信渠道进程'):
                    other.begin()
                self.assertTrue(controller.close())
                attempt = other.begin()['id']
                self.assertTrue(other.cancel(attempt))
            finally:
                release.set()
                thread.join(2)
                controller.close(timeout=1)
                other.close(timeout=1)

    def test_release_waits_for_inflight_confirmation_before_acknowledging(self):
        with tempfile.TemporaryDirectory() as folder:
            started, release = threading.Event(), threading.Event()
            class SlowClient(Client):
                def qr_status(self, qr, verify_code=None):
                    started.set()
                    release.wait(2)
                    return super().qr_status(qr, verify_code)
            controller = Controller(Service(), folder, host_granted=lambda: True,
                                    client_factory=lambda **_: SlowClient())
            attempt = controller.begin()['id']
            outcome = []
            def poll():
                try:
                    controller.poll(attempt)
                    outcome.append('unexpected-success')
                except ValueError:
                    outcome.append('cancelled')
            thread = threading.Thread(target=poll)
            thread.start()
            try:
                self.assertTrue(started.wait(2))
                self.assertFalse(controller.prepare_release())
                self.assertIsNone(controller.credentials.load())
                release.set()
                thread.join(2)
                self.assertEqual(outcome, ['cancelled'])
                self.assertTrue(controller.prepare_release(timeout=1))
                self.assertIsNone(controller.credentials.load())
            finally:
                release.set()
                thread.join(2)
                controller.close(timeout=1)

    def test_cancelling_rescan_restores_previous_connection_only_after_poll_returns(self):
        with tempfile.TemporaryDirectory() as folder:
            started, release = threading.Event(), threading.Event()
            class SlowClient(Client):
                def qr_status(self, qr, verify_code=None):
                    started.set()
                    release.wait(2)
                    return super().qr_status(qr, verify_code)
            clients = iter([Client(), SlowClient()])
            controller = Controller(Service(), folder, host_granted=lambda: True,
                                    client_factory=lambda **_: next(clients))
            outcome = []
            with mock.patch('xiaoyu_runtime.channels.controller.ChannelWorker') as worker:
                worker.return_value.stop.return_value = True
                first = controller.begin()['id']
                controller.poll(first)
                second = controller.begin()['id']
                def poll():
                    try:
                        controller.poll(second)
                        outcome.append('unexpected-success')
                    except ValueError:
                        outcome.append('cancelled')
                thread = threading.Thread(target=poll)
                thread.start()
                try:
                    self.assertTrue(started.wait(2))
                    self.assertTrue(controller.cancel(second))
                    self.assertEqual(worker.return_value.start.call_count, 1)
                    release.set()
                    thread.join(2)
                    self.assertEqual(outcome, ['cancelled'])
                    self.assertEqual(worker.return_value.start.call_count, 2)
                    self.assertEqual(controller.status()['state'], 'connecting')
                finally:
                    release.set()
                    thread.join(2)
                    controller.close(timeout=1)
