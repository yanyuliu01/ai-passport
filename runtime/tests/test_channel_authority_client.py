import threading
import unittest
from unittest import mock

from xiaoyu_runtime.channels.authority_client import AuthorityClient, AuthorityUnavailable


class AuthorityClientTest(unittest.TestCase):
    def setUp(self):
        self.service = mock.Mock(runtime_id='local')
        self.service.subject_id = 'c' * 32
        self.service.card_store = mock.Mock(epoch='d' * 32)
        self.mesh = self.service.mesh
        self.mesh._changed = threading.Condition()
        self.mesh._replicas = mock.Mock()
        self.mesh.authority.return_value = 'home'
        self.mesh.owner_of.return_value = {'conflict': False, 'unresolved': False}
        self.mesh._replicas.applied_record.return_value = None
        self.mesh._replicas.applied_position.return_value = ('e' * 32, 8)
        self.result = {
            'id': 'command', 'from': 'home', 'to': 'local', 'phase': 'accepted', 'ok': True,
            'channel_authority': {'authority': 'home', 'generation': 1, 'state': 'reserved', 'host': 'local'}}
        self.journal = mock.Mock()
        self.journal.pending.return_value = None
        self.client = AuthorityClient(self.service, self.journal)
        self.request = {'status': 'queued', 'authority': 'home', 'command_id': 'command', 'action': 'reserve',
                        'subject': 'c' * 32, 'host_epoch': 'd' * 32, 'authority_epoch': 'e' * 32}

    def test_result_is_not_accepted_until_its_replica_is_applied(self):
        self.assertEqual(self.client.wait(self.request), self.request)
        self.mesh._replicas.applied_record.return_value = self.result
        result = self.client.wait(self.request)
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['channel_authority']['host'], 'local')
        self.mesh.command.assert_not_called()

    def test_wrong_result_identity_and_changed_authority_fail_closed(self):
        self.mesh._replicas.applied_record.return_value = self.result
        self.result['to'] = 'another-client'
        with self.assertRaises(AuthorityUnavailable):
            self.client.wait(self.request)
        self.result['to'] = 'local'
        self.mesh.owner_of.return_value = {'conflict': True}
        with self.assertRaises(AuthorityUnavailable):
            self.client.wait(self.request)

    def test_changed_subject_or_authority_epoch_never_accepts_old_result(self):
        self.mesh._replicas.applied_record.return_value = self.result
        self.service.subject_id = 'f' * 32
        with self.assertRaises(AuthorityUnavailable):
            self.client.wait(self.request)
        self.service.subject_id = 'c' * 32
        self.mesh._replicas.applied_position.return_value = ('f' * 32, 8)
        with self.assertRaises(AuthorityUnavailable):
            self.client.wait(self.request)

    def test_timeout_and_uncertain_result_do_not_submit_another_command(self):
        self.assertEqual(self.client.wait(self.request, timeout=0), self.request)
        self.mesh._replicas.applied_record.return_value = self.result
        self.result.update(ok=False, unknown=True)
        self.assertEqual(self.client.wait(self.request)['status'], 'unknown')
        self.mesh.command.assert_not_called()

    def test_cannot_release_before_local_controller_is_quiescent(self):
        controller = mock.Mock()
        controller.prepare_release.return_value = False
        with self.assertRaises(AuthorityUnavailable):
            self.client.release(controller, 1)
        self.mesh.command.assert_not_called()

    def test_release_keeps_local_lock_until_matching_authority_confirmation(self):
        controller = mock.Mock()
        controller.close.return_value = True
        self.assertEqual(self.client.finish_release(self.request, controller, 1)['status'], 'queued')
        controller.close.assert_not_called()
        self.mesh._replicas.applied_record.return_value = self.result
        self.result.update(ok=False, unknown=True)
        self.assertEqual(self.client.finish_release(self.request, controller, 1)['status'], 'unknown')
        controller.close.assert_not_called()
        self.result.update(ok=True, unknown=False, channel_authority={
            'authority': 'home', 'generation': 2, 'state': 'unbound', 'host': None})
        with self.assertRaises(AuthorityUnavailable):
            self.client.finish_release(self.request, controller, 1)
        controller.close.assert_not_called()
        self.result['channel_authority']['generation'] = 1
        self.assertEqual(self.client.finish_release(self.request, controller, 1)['status'], 'applied')
        controller.close.assert_called_once_with(timeout=0)
