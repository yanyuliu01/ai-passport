import unittest
import tempfile
from pathlib import Path
from unittest import mock

from xiaoyu_runtime.channels.authority_client import AuthorityUnavailable
from xiaoyu_runtime.channels.host_grant import HostGrant


class HostGrantTest(unittest.TestCase):
    def setUp(self):
        self.service = mock.Mock(runtime_id='local')
        self.client = mock.Mock()
        self.client._authority = mock.Mock(return_value=(None, 'home'))
        self.scope = {'subject': 'c' * 32, 'host_epoch': 'd' * 32, 'authority_epoch': 'e' * 32}
        self.client._scope = mock.Mock(return_value=self.scope)
        self.client.recovery_safe.return_value = True
        self.ref = {'status': 'queued', 'authority': 'home', 'command_id': 'c1', **self.scope}
        self.client.reserve.return_value = self.ref
        self.lock = mock.Mock()
        self.lock.acquire.return_value = True
        self.grant = HostGrant(self.service, self.client, self.lock)

    def response(self, state, generation=1, host='local'):
        return {'status': 'applied', 'authority': 'home', 'channel_authority': {
            'authority': 'home', 'generation': generation, 'state': state, 'host': host}}

    def reserve(self):
        self.grant.reserve()
        self.client.wait.return_value = self.response('reserved')
        self.grant.resolve()

    def test_worker_is_allowed_only_after_confirmed_reservation_and_no_network_check(self):
        self.assertFalse(self.grant.allowed())
        self.grant.reserve()
        self.client.wait.return_value = dict(self.ref)
        self.assertEqual(self.grant.resolve()['status'], 'queued')
        self.assertFalse(self.grant.allowed())
        self.client.wait.return_value = self.response('reserved')
        self.grant.resolve()
        self.client.wait.reset_mock()
        self.assertTrue(self.grant.allowed())
        self.client.wait.assert_not_called()
        self.client._scope.return_value = dict(self.scope, authority_epoch='f' * 32)
        self.assertFalse(self.grant.allowed())
        self.client._scope.return_value = self.scope
        self.client._authority.side_effect = AuthorityUnavailable('conflict')
        self.assertFalse(self.grant.allowed())

    def test_foreign_or_stale_reservation_never_grants_worker_access(self):
        self.grant.reserve()
        self.client.wait.return_value = self.response('reserved', host='another-host')
        with self.assertRaises(AuthorityUnavailable):
            self.grant.resolve()
        self.assertFalse(self.grant.allowed())
        with self.assertRaises(AuthorityUnavailable):
            self.grant.reserve()

    def test_revocation_fences_worker_before_request_and_uncertain_keeps_lock(self):
        self.reserve()
        self.client.begin_release.return_value = self.ref
        self.grant.begin_release()
        self.assertFalse(self.grant.allowed())
        self.client.wait.return_value = self.response('revoking')
        self.grant.resolve()
        controller = mock.Mock()
        self.client.release.return_value = self.ref
        self.grant.release(controller)
        self.client.finish_release.return_value = {'status': 'unknown'}
        self.assertEqual(self.grant.resolve()['status'], 'unknown')
        self.assertFalse(self.grant.allowed())
        with self.assertRaises(AuthorityUnavailable):
            self.grant.reserve()

    def test_completed_release_then_new_grant_can_run(self):
        self.reserve()
        self.client.begin_release.return_value = self.ref
        self.grant.begin_release()
        self.client.wait.return_value = self.response('revoking')
        self.grant.resolve()
        self.client.release.return_value = self.ref
        self.grant.release(mock.Mock())
        self.client.finish_release.return_value = self.response('unbound', host=None)
        self.grant.resolve()
        self.assertFalse(self.grant.allowed())
        self.client.wait.return_value = self.response('reserved', generation=2)
        self.grant.reserve()
        self.grant.resolve()
        self.assertTrue(self.grant.allowed())

    def test_recovery_logic_requires_authority_to_confirm_local_host(self):
        self.client.view.return_value = self.ref
        self.grant.recover()
        self.assertFalse(self.grant.allowed())
        self.client.wait.return_value = self.response('active', generation=3)
        self.grant.resolve()
        self.assertTrue(self.grant.allowed())
        restarted = HostGrant(self.service, self.client, self.lock)
        restarted.recover()
        self.client.wait.return_value = self.response('active', generation=3, host='another-host')
        restarted.resolve()
        self.assertFalse(restarted.allowed())

    def test_confirmed_revocation_recovery_cannot_start_worker(self):
        self.client.view.return_value = self.ref
        self.grant.recover()
        self.client.wait.return_value = self.response('revoking', generation=3)
        self.grant.resolve()
        self.assertFalse(self.grant.allowed())
        self.client.release.return_value = self.ref
        self.grant.release(mock.Mock())
        self.client.finish_release.return_value = self.response('unbound', generation=3, host=None)
        self.assertEqual(self.grant.resolve()['status'], 'applied')
        self.assertFalse(self.grant.allowed())

    def test_recovery_stays_closed_without_durable_command_reconciliation(self):
        self.client.recovery_safe.return_value = False
        with self.assertRaisesRegex(AuthorityUnavailable, '暂不能自动恢复'):
            self.grant.recover()
        self.client.view.assert_not_called()
        self.lock.acquire.assert_not_called()

    def test_recovery_rechecks_pending_operation_after_acquiring_local_lock(self):
        self.client.recovery_safe.side_effect = [True, False]
        with self.assertRaisesRegex(AuthorityUnavailable, '暂不能自动恢复'):
            self.grant.recover()
        self.client.view.assert_not_called()
        self.lock.release.assert_called_once()

    def test_only_one_local_grant_holder_can_reserve(self):
        with tempfile.TemporaryDirectory() as folder:
            service = mock.Mock(runtime_id='local')
            service._store = mock.Mock(state_dir=Path(folder))
            first = HostGrant(service, self.client)
            second = HostGrant(service, self.client)
            first.reserve()
            try:
                with self.assertRaisesRegex(AuthorityUnavailable, '已有微信渠道授权进程'):
                    second.reserve()
                self.client.wait.return_value = {'status': 'refused'}
                self.assertEqual(first.resolve()['status'], 'refused')
                second.reserve()
                self.assertEqual(second.resolve()['status'], 'refused')
            finally:
                first._host_lock.release()
                second._host_lock.release()

    def test_shutdown_fences_old_grant_and_waits_until_controller_stops(self):
        self.reserve()
        controller = mock.Mock()
        controller.close.return_value = False
        self.assertFalse(self.grant.shutdown(controller))
        self.assertFalse(self.grant.allowed())
        self.lock.release.assert_not_called()
        controller.close.return_value = True
        self.assertTrue(self.grant.shutdown(controller))
        self.lock.release.assert_called_once()
        with self.assertRaises(AuthorityUnavailable):
            self.grant.reserve()

    def test_pending_operation_is_reconciled_without_issuing_a_new_mutation(self):
        self.client.reconcile_pending.return_value = {'status': 'queued'}
        self.assertEqual(self.grant.reconcile()['status'], 'queued')
        self.client.reconcile_pending.return_value = {'status': 'applied'}
        self.assertEqual(self.grant.reconcile()['status'], 'applied')
        self.client.reserve.assert_not_called()
