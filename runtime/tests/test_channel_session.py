import unittest
import threading
from types import SimpleNamespace
from unittest import mock

from xiaoyu_runtime.channels.authority_client import AuthorityUnavailable
from xiaoyu_runtime.channels.session import ChannelSession


class SessionTests(unittest.TestCase):
    def session(self):
        grant = mock.Mock()
        grant.resolve.return_value = {'status': 'applied'}
        controller = mock.Mock()
        controller.begin.return_value = {'id': 'attempt', 'qr_content': 'private'}
        controller.poll.return_value = {'state': 'confirmed'}
        controller.close.return_value = True
        service = SimpleNamespace(runtime_id='runtime', _store=SimpleNamespace(state_dir='/unused'))
        return ChannelSession(service, grant=grant, controller=controller, timeout=0), grant, controller

    def test_public_status_never_waits_for_binding_or_controller_locks(self):
        session, _, controller = self.session()
        controller._lock = threading.RLock()
        controller.status.return_value = {'state': 'connected'}
        session._phase = 'active'
        self.assertEqual({'state': 'connected'}, session.public_status())
        for lock in (session._lock, controller._lock):
            held, release = threading.Event(), threading.Event()
            def hold():
                with lock:
                    held.set()
                    release.wait(1)
            worker = threading.Thread(target=hold)
            worker.start()
            self.assertTrue(held.wait(1))
            try:
                self.assertEqual({'state': 'unknown'}, session.public_status())
            finally:
                release.set()
                worker.join(1)

    def test_retry_is_only_allowed_for_the_active_local_host(self):
        session, _, controller = self.session()
        with self.assertRaises(ValueError):
            session.retry_delivery('runtime/reply#0', 'a' * 32, 1)
        session._phase = 'active'
        controller.retry_delivery.return_value = 'queued'
        self.assertEqual('queued', session.retry_delivery('runtime/reply#0', 'a' * 32, 1, confirm_unknown=True))
        controller.retry_delivery.assert_called_once_with('runtime/reply#0', 'a' * 32, 1, confirm_unknown=True)

    def test_bind_reserves_claims_confirms_then_activates(self):
        session, grant, controller = self.session()
        controller.status.return_value = {'state': 'unbound'}
        self.assertEqual(session.status()['state'], 'unbound')
        self.assertEqual(session.begin()['id'], 'attempt')
        self.assertEqual(session.status()['state'], 'binding')
        grant.reserve.assert_called_once()
        grant.prepare_host.assert_called_once()
        self.assertEqual(session.poll('attempt'), {'state': 'confirmed'})
        controller.status.return_value = {'state': 'connected'}
        self.assertEqual(session.status()['state'], 'connected')
        controller.poll.assert_called_once_with('attempt', None)
        grant.activate.assert_called_once()
        with self.assertRaises(ValueError):
            session.poll('attempt')

    def test_delayed_activation_is_reconciled_without_repeating_qr_or_command(self):
        session, grant, controller = self.session()
        session.begin()
        grant.resolve.side_effect = [{'status': 'queued'}, {'status': 'applied'}]
        with self.assertRaises(AuthorityUnavailable):
            session.poll('attempt')
        self.assertEqual(session.poll('attempt'), {'state': 'confirmed'})
        grant.activate.assert_called_once()
        controller.poll.assert_called_once()

    def test_cancel_new_reservation_drains_then_releases(self):
        session, grant, controller = self.session()
        session.begin()
        with mock.patch('xiaoyu_runtime.channels.session.Controller'):
            self.assertTrue(session.cancel('attempt'))
        controller.cancel.assert_called_once_with('attempt')
        grant.begin_release.assert_called_once()
        grant.release.assert_called_once_with(controller)
        self.assertFalse(session.cancel('attempt'))

    def test_cancel_rescan_does_not_release_existing_binding(self):
        session, grant, controller = self.session()
        session.begin()
        session.poll('attempt')
        session.begin()
        self.assertTrue(session.cancel('attempt'))
        grant.begin_release.assert_not_called()
        grant.release.assert_not_called()
        self.assertEqual(grant.reserve.call_count, 1)

    def test_failed_qr_generation_keeps_reserved_host_for_retry(self):
        session, grant, controller = self.session()
        controller.begin.side_effect = [ValueError('network'), {'id': 'attempt'}]
        with self.assertRaises(ValueError):
            session.begin()
        self.assertEqual(session.begin()['id'], 'attempt')
        grant.reserve.assert_called_once()

    def test_recovery_reconfirms_authority_before_loading_saved_binding(self):
        session, grant, controller = self.session()
        grant.resolve.return_value = {'status': 'applied', 'channel_authority':
                                      {'state': 'active', 'host': 'runtime'}}
        controller.resume.return_value = True
        self.assertEqual(session.recover(), {'state': 'active'})
        grant.recover.assert_called_once()
        grant.prepare_host.assert_called_once()
        controller.resume.assert_called_once()
        grant.activate.assert_not_called()

    def test_recovery_resumes_confirmed_qr_after_interrupted_activation(self):
        session, grant, controller = self.session()
        grant.resolve.side_effect = [
            {'status': 'applied', 'channel_authority': {'state': 'reserved', 'host': 'runtime'}},
            {'status': 'queued'}, {'status': 'applied'}]
        controller.resume.return_value = True
        with self.assertRaises(AuthorityUnavailable):
            session.recover()
        self.assertEqual(session.recover(), {'state': 'active'})
        grant.recover.assert_called_once()
        grant.activate.assert_called_once()
        controller.resume.assert_called_once()

    def test_recovery_never_uses_credentials_reserved_by_another_host(self):
        session, grant, controller = self.session()
        grant.resolve.return_value = {'status': 'applied', 'channel_authority':
                                      {'state': 'active', 'host': 'other'}}
        self.assertEqual(session.recover(), {'state': 'external'})
        controller.resume.assert_not_called()

    def test_unresolved_old_command_is_reconciled_before_recovery(self):
        session, grant, controller = self.session()
        grant.client.recovery_safe.return_value = False
        grant.reconcile.side_effect = [{'status': 'queued'}, {'status': 'applied'}]
        grant.resolve.return_value = {'status': 'applied', 'channel_authority': {'state': 'unbound', 'host': None}}
        with self.assertRaises(AuthorityUnavailable):
            session.recover()
        grant.recover.assert_not_called()
        self.assertEqual(session.recover(), {'state': 'unbound'})
        grant.recover.assert_called_once()
        controller.resume.assert_not_called()

    def test_cancel_does_not_wait_for_an_inflight_qr_poll(self):
        session, grant, controller = self.session()
        session.begin()
        entered, release = threading.Event(), threading.Event()
        errors = []
        def poll(*_args):
            entered.set()
            release.wait(2)
            return {'state': 'confirmed'}
        controller.poll.side_effect = poll
        def run():
            try:
                session.poll('attempt')
            except ValueError as error:
                errors.append(error)
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            with mock.patch('xiaoyu_runtime.channels.session.Controller'):
                self.assertTrue(session.cancel('attempt'))
        finally:
            release.set()
            thread.join(2)
        self.assertEqual(len(errors), 1)
        grant.activate.assert_not_called()
