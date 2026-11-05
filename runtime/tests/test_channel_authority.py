import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from xiaoyu_runtime.channels.authority import ChannelAuthority
from xiaoyu_runtime.channels.controller import Controller
from test_channel_controller import Client
from test_runtime import Scripted, make_xiaoyu
from xiaoyu_runtime.api.service import Service, RequestError


class AuthorityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'authority.json'
        self.authority = ChannelAuthority(self.path, 'home', local_runtime='home')
        self.addCleanup(self.authority.close)

    def test_reservation_survives_restart_and_an_offline_host_cannot_be_stolen(self):
        first = self.authority.reserve('mac')
        self.authority.activate('mac', first['generation'])
        self.authority.close()
        reopened = ChannelAuthority(self.path, 'home', local_runtime='home')
        self.addCleanup(reopened.close)
        with self.assertRaisesRegex(ValueError, 'already'):
            reopened.reserve('windows')
        reopened.begin_release('mac', first['generation'])
        with self.assertRaisesRegex(ValueError, 'not stopped'):
            reopened.release('mac', first['generation'], worker_stopped=False)
        with self.assertRaisesRegex(ValueError, 'being revoked'):
            reopened.activate('mac', first['generation'])
        with self.assertRaisesRegex(ValueError, 'changed'):
            reopened.release('windows', first['generation'], worker_stopped=True)
        reopened.release('mac', first['generation'], worker_stopped=True)
        second = reopened.reserve('windows')
        self.assertGreater(second['generation'], first['generation'])
        with self.assertRaisesRegex(ValueError, 'changed'):
            reopened.activate('mac', first['generation'])

    def test_simultaneous_reservations_have_only_one_winner(self):
        gate = threading.Barrier(3)
        results = []
        def attempt(host):
            gate.wait()
            try:
                self.authority.reserve(host)
                results.append(host)
            except ValueError:
                pass
        threads = [threading.Thread(target=attempt, args=(name,)) for name in ('mac', 'windows')]
        for thread in threads:
            thread.start()
        gate.wait()
        for thread in threads:
            thread.join(2)
        self.assertEqual(len(results), 1)
        self.assertEqual(self.authority.view()['host'], results[0])

    def test_revocation_is_durable_and_cannot_be_reactivated_after_restart(self):
        grant = self.authority.reserve('mac')
        self.authority.begin_release('mac', grant['generation'])
        self.authority.close()
        reopened = ChannelAuthority(self.path, 'home', local_runtime='home')
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.view()['state'], 'revoking')
        with self.assertRaises(ValueError):
            reopened.reserve('windows')
        with self.assertRaises(ValueError):
            reopened.activate('mac', grant['generation'])
        reopened.release('mac', grant['generation'], worker_stopped=True)
        self.assertEqual(reopened.reserve('windows')['host'], 'windows')

    def test_only_the_stable_authority_can_open_and_closed_instance_cannot_write(self):
        with self.assertRaises(ValueError):
            ChannelAuthority(self.path, 'home', local_runtime='other')
        with self.assertRaises(ValueError):
            ChannelAuthority(self.path, 'home', local_runtime='home')
        self.authority.close()
        with self.assertRaises(ValueError):
            self.authority.reserve('mac')
        with self.assertRaises(ValueError):
            ChannelAuthority(self.path, 'new-home', local_runtime='new-home')

    def test_failed_durable_write_does_not_publish_reservation(self):
        with mock.patch('xiaoyu_runtime.channels.authority.durable_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.authority.reserve('mac')
        self.assertEqual(self.authority.view()['state'], 'unbound')

    def test_two_hosts_wait_for_actual_worker_shutdown_before_rebinding(self):
        class Writer:
            def conversation_create_weixin(self):
                return {'owner': 'mac'}
        class Service:
            runtime_id = 'mac'
            _owner_writer = Writer()
        grant = self.authority.reserve('mac')
        def mac_is_granted():
            state = self.authority.view()
            return (state['host'] == 'mac' and state['generation'] == grant['generation']
                    and state['state'] in ('reserved', 'active'))
        controller = Controller(Service(), Path(self.tmp.name) / 'mac', host_granted=mac_is_granted,
                                client_factory=lambda **_: Client())
        attempt = controller.begin()['id']
        with mock.patch('xiaoyu_runtime.channels.controller.ChannelWorker') as worker:
            controller.poll(attempt)
            self.authority.activate('mac', grant['generation'])
            self.authority.begin_release('mac', grant['generation'])
            self.assertFalse(mac_is_granted())
            worker.return_value.stop.return_value = False
            self.assertFalse(controller.prepare_release())
            with self.assertRaises(ValueError):
                self.authority.release('mac', grant['generation'], worker_stopped=False)
            with self.assertRaises(ValueError):
                self.authority.reserve('windows')
            worker.return_value.stop.return_value = True
            self.assertTrue(controller.prepare_release())
            self.authority.release('mac', grant['generation'], worker_stopped=True)
            self.assertTrue(controller.close())
            self.assertEqual(self.authority.reserve('windows')['host'], 'windows')
            self.assertFalse(mac_is_granted())

    def test_service_rejects_unknown_or_conflicting_authority_and_removed_host(self):
        xiaoyu, store = make_xiaoyu(Path(self.tmp.name) / 'runtime', [Scripted('voice')])
        service = Service(xiaoyu, store)
        self.addCleanup(service.close)
        mesh = mock.Mock()
        mesh.authority.return_value = service.runtime_id
        mesh.owner_of.return_value = {'conflict': False, 'unresolved': False}
        mesh.peer_id.side_effect = lambda origin: origin
        service.mesh = mesh
        peer = 'a' * 32
        with self.assertRaisesRegex(RequestError, '尚未启用'):
            service.channel_authority_command(peer, {'action': 'reserve'})
        service._channel_authority = ChannelAuthority(Path(self.tmp.name) / 'service-ledger.json',
            service.runtime_id, local_runtime=service.runtime_id)
        ledger = service._channel_authority
        mesh.owner_of.return_value = {'conflict': True}
        with self.assertRaisesRegex(RequestError, '尚未确认'):
            service.channel_authority_command(peer, {'action': 'reserve'})
        mesh.owner_of.return_value = {'conflict': False}
        mesh.authority.return_value = 'b' * 32
        with self.assertRaisesRegex(RequestError, '尚未确认'):
            service.channel_authority_command(peer, {'action': 'reserve'})
        mesh.authority.return_value = service.runtime_id
        with mock.patch.object(service.devices, 'removed', return_value=True):
            with self.assertRaisesRegex(RequestError, '来源不可用'):
                service.channel_authority_command(peer, {'action': 'reserve'})
        with self.assertRaisesRegex(RequestError, '格式无效'):
            service.channel_authority_command(peer, {'action': ['reserve']})
        self.assertEqual(ledger.view()['state'], 'unbound')
        self.assertEqual(service.channel_authority_command(peer, {'action': 'reserve'})
                         ['channel_authority']['host'], peer)
        service.close()
        with self.assertRaises(ValueError):
            ledger.view()
