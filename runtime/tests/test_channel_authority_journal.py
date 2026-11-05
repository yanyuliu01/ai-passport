import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from xiaoyu_runtime.channels.authority_client import AuthorityClient
from xiaoyu_runtime.channels.authority_journal import AuthorityJournal
from xiaoyu_runtime.api.service import RequestError


class AuthorityJournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'operation.json'
        self.journal = AuthorityJournal(self.path)
        self.record = {'id': 'a' * 32, 'authority': 'b' * 32,
                       'action': 'activate', 'generation': 2, 'subject': 'c' * 32,
                       'host_epoch': 'd' * 32, 'authority_epoch': 'e' * 32}

    def service(self):
        service = mock.Mock(runtime_id='a' * 32)
        service.subject_id = 'c' * 32
        service.card_store = mock.Mock(epoch='d' * 32)
        service.mesh._replicas = mock.Mock()
        service.mesh._replicas.applied_position.return_value = ('e' * 32, 0)
        return service

    def test_pending_survives_restart_and_requires_matching_completion(self):
        self.journal.begin(self.record)
        reopened = AuthorityJournal(self.path)
        self.assertEqual(reopened.pending(), self.record)
        with self.assertRaises(ValueError):
            reopened.begin(dict(self.record, id='c' * 32))
        with self.assertRaises(ValueError):
            reopened.finish('c' * 32)
        self.assertEqual(reopened.pending(), self.record)
        reopened.finish('a' * 32)
        self.assertIsNone(AuthorityJournal(self.path).pending())

    def test_corruption_and_invalid_metadata_fail_closed(self):
        self.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.journal.pending()
        self.path.unlink()
        with self.assertRaises(ValueError):
            self.journal.begin(dict(self.record, token='must-not-be-stored'))
        self.assertFalse(self.path.exists())

    def test_failed_dispatch_keeps_operation_and_disables_restart_recovery(self):
        service = self.service()
        mesh = service.mesh
        mesh.authority.return_value = 'b' * 32
        mesh.owner_of.return_value = {'conflict': False, 'unresolved': False}
        mesh._reachable = mock.Mock(return_value=None)
        mesh.command.side_effect = OSError('dispatch uncertain')
        client = AuthorityClient(service, self.journal)
        with self.assertRaises(OSError):
            client.reserve()
        pending = self.journal.pending()
        self.assertEqual(pending['action'], 'reserve')
        self.assertFalse(AuthorityClient(service, AuthorityJournal(self.path)).recovery_safe())
        with self.assertRaises(ValueError):
            client.reserve()
        self.assertEqual(mesh.command.call_count, 1)
        self.assertNotIn('token', json.dumps(pending))

    def test_explicit_local_refusal_is_terminal_but_storage_error_stays_uncertain(self):
        service = self.service()
        service.mesh.authority.return_value = service.runtime_id
        service.mesh.owner_of.return_value = {'conflict': False, 'unresolved': False}
        client = AuthorityClient(service, self.journal)
        service.channel_authority_command.side_effect = RequestError('refused')
        with self.assertRaises(RequestError):
            client.reserve()
        self.assertTrue(client.recovery_safe())
        service.channel_authority_command.side_effect = OSError('storage uncertain')
        with self.assertRaises(OSError):
            client.reserve()
        self.assertFalse(client.recovery_safe())

    def test_remote_reconciliation_waits_for_original_applied_result_without_resending(self):
        self.journal.begin(self.record)
        service = self.service()
        mesh = service.mesh
        mesh._changed = threading.Condition()
        mesh._replicas.applied_record.return_value = None
        mesh.authority.return_value = 'b' * 32
        mesh.owner_of.return_value = {'conflict': False}
        client = AuthorityClient(service, self.journal)
        self.assertEqual(client.reconcile_pending()['status'], 'queued')
        self.assertFalse(client.recovery_safe())
        mesh._replicas.applied_record.return_value = {
            'id': self.record['id'], 'from': 'b' * 32, 'to': 'a' * 32, 'phase': 'accepted', 'ok': True,
            'channel_authority': {'authority': 'b' * 32, 'generation': 2,
                                  'state': 'active', 'host': 'a' * 32}}
        self.assertEqual(client.reconcile_pending()['status'], 'applied')
        self.assertTrue(client.recovery_safe())
        mesh.command.assert_not_called()

    def test_unknown_remote_result_uses_one_read_only_view_to_reconcile(self):
        self.journal.begin(self.record)
        service = self.service()
        mesh = service.mesh
        mesh._changed = threading.Condition()
        mesh.authority.return_value = 'b' * 32
        mesh.owner_of.return_value = {'conflict': False}
        mesh._reachable = mock.Mock(return_value=None)
        view_id = 'f' * 32
        mesh.command.return_value = view_id
        responses = {self.record['id'] + ':accepted': {
            'id': self.record['id'], 'from': 'b' * 32, 'to': 'a' * 32,
            'phase': 'accepted', 'ok': False, 'unknown': True}}
        mesh._replicas.applied_record.side_effect = lambda _origin, _kind, key, **_: responses.get(key)
        client = AuthorityClient(service, self.journal)
        self.assertEqual(client.reconcile_pending()['status'], 'unknown')
        self.assertFalse(client.recovery_safe())
        responses[view_id + ':accepted'] = {'id': view_id, 'from': 'b' * 32, 'to': 'a' * 32,
            'phase': 'accepted', 'ok': True, 'channel_authority': {'authority': 'b' * 32,
            'generation': 2, 'state': 'active', 'host': 'a' * 32}}
        self.assertEqual(client.reconcile_pending()['status'], 'applied')
        self.assertTrue(client.recovery_safe())
        mesh.command.assert_called_once_with('b' * 32, 'weixin_authority', {'action': 'view'})

    def test_local_reconciliation_uses_durable_authority_state_without_repeating_mutation(self):
        local_record = dict(self.record, authority='a' * 32, action='release', authority_epoch='d' * 32)
        self.journal.begin(local_record)
        service = self.service()
        service.mesh.authority.return_value = service.runtime_id
        service.mesh.owner_of.return_value = {'conflict': False}
        service.channel_authority_command.return_value = {'channel_authority': {
            'authority': service.runtime_id, 'generation': 2, 'state': 'unbound', 'host': None}}
        client = AuthorityClient(service, self.journal)
        self.assertEqual(client.reconcile_pending()['status'], 'applied')
        self.assertTrue(client.recovery_safe())
        service.channel_authority_command.assert_called_once_with(service.runtime_id, {'action': 'view'})

    def test_reconciliation_rejects_a_changed_subject_or_authority_epoch(self):
        self.journal.begin(self.record)
        service = self.service()
        service.mesh.authority.return_value = 'b' * 32
        service.mesh.owner_of.return_value = {'conflict': False}
        client = AuthorityClient(service, self.journal)
        service.subject_id = 'f' * 32
        with self.assertRaises(ValueError):
            client.reconcile_pending()
        service.subject_id = 'c' * 32
        service.mesh._replicas.applied_position.return_value = ('f' * 32, 0)
        with self.assertRaises(ValueError):
            client.reconcile_pending()
        self.assertEqual(self.journal.pending(), self.record)
