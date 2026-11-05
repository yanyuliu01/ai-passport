"""小鱼网络（mesh.py）：几台 Runtime 互相同步、转交，不经真的网络。

每台 Runtime 是同一个进程里的一套 Xiaoyu + Service + Mesh；HTTP 调用由一个假的 opener 直接交给
对方的 Mesh.exchange（也检查网络口令），所以拉、推、长轮询、转告都走真的代码。
"""

import dataclasses
import io
import json
import threading
import time
import unittest
import urllib.error
from unittest import mock

import urllib.request
from pathlib import Path

from test_runtime import TOKEN, Scripted, TempDirCase, free_port, make_xiaoyu, until, write_config

from xiaoyu_runtime.helpers import agents as agents_module
from xiaoyu_runtime.helpers import factory
from xiaoyu_runtime import config as config_module
from xiaoyu_runtime.api import shared_history
from xiaoyu_runtime.delivery.mesh import Mesh, MeshError, local_id, subject_for
from xiaoyu_runtime.api.server import make_server
from xiaoyu_runtime.api.service import Service, RequestError
from xiaoyu_runtime.world.store import Store
from xiaoyu_runtime.loop.xiaoyu import Xiaoyu

SECRET = "mesh-secret-for-tests-0123456789"


class Network:
    """假的网络：地址 → 那台的 Mesh；down 里的地址连不上。"""

    def __init__(self):
        self.nodes = {}
        self.down = set()
        self.calls = []

    def open(self, request, timeout=None):
        url = request.full_url
        base = url[:url.index("/v1/")]
        self.calls.append(base)
        node = self.nodes.get(base)
        if node is None or base in self.down:
            raise urllib.error.URLError("unreachable %s" % base)
        if not node.authorized(request.get_header("Authorization") or ""):
            raise urllib.error.HTTPError(url, 401, "bad secret", {}, io.BytesIO(b"{}"))
        reply = node.exchange(json.loads(request.data.decode("utf-8")))
        body = json.dumps(reply, ensure_ascii=False).encode("utf-8")

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return Response(body)


class Node:
    def __init__(self, case, network, name, peers=(), advertise=True, agents=None, secret=SECRET, home=False):
        folder = case.folder / name
        self.agent = agents[0] if agents else Scripted("claude")
        self.xiaoyu, self.store = make_xiaoyu(folder, agents or [self.agent])
        self.service = Service(self.xiaoyu, self.store)
        self.url = "http://%s.test" % name.lower()
        config = dataclasses.replace(self.xiaoyu._config, name=name, mesh_secret=secret,
                                     mesh_peers=list(peers),
                                     mesh_advertise=[self.url] if advertise else [], mesh_home=home)
        self.mesh = Mesh(config, self.service, self.xiaoyu, opener=network.open)
        if advertise:
            network.nodes[self.url] = self.mesh
        self.mesh.start()
        case.addCleanup(self.close)

    def close(self):
        self.mesh.close()
        self.service.close()

    def card_from(self, other, card_id):
        """other 那台的一件事在这台的本地卡。"""
        for card in self.xiaoyu.cards.recent(None, 1000, include_desktop=True):
            if card.get("mesh_origin") == other.service.runtime_id and card.get("mesh_id") == card_id:
                return card
        return None

    def done(self, message_id, seconds=10):
        return until(lambda: (self.service.get(message_id) or {}).get("status") in ("done", "failed"), seconds)


class MeshTests(TempDirCase):
    def test_fake_weixin_full_round_trip_uses_one_host_and_shares_read_only_history(self):
        from xiaoyu_runtime.channels.controller import Controller
        from xiaoyu_runtime.channels.host_grant import HostGrant
        from xiaoyu_runtime.channels.session import ChannelSession
        home = self.node('RoundTripHome', home=True)
        host = self.node('RoundTripHost', peers=[home.url])
        home.service.enable_channel_hosting(ChannelSession)
        self.assertIsNone(home.service._channel_authority)
        self.assertTrue(until(lambda: host.mesh.authority() == home.service.runtime_id))
        grant = HostGrant(host.service)
        got_reply, release_poll = threading.Event(), threading.Event()
        class FakeWeixin:
            base = 'https://ilinkai.weixin.qq.com'
            def __init__(self):
                self.sent = []
            def qr(self):
                return {'qrcode': 'private-qr', 'qrcode_img_content': 'private-image'}
            def qr_status(self, qr, verify_code=None):
                return {'status': 'confirmed', 'bot_token': 'private-token',
                        'ilink_bot_id': 'private-bot', 'ilink_user_id': 'private-owner'}
            def updates(self, cursor):
                if not cursor:
                    return {'get_updates_buf': 'next', 'msgs': [{
                        'message_type': 1, 'from_user_id': 'private-owner', 'message_id': 37,
                        'context_token': 'private-context',
                        'item_list': [{'type': 1, 'text_item': {'text': 'wechat-round-trip'}}]}]}
                release_poll.wait(3)
                return {'get_updates_buf': cursor, 'msgs': []}
            def send_text(self, *args):
                self.sent.append(args)
                got_reply.set()
                return {'ret': 0}
        transport = FakeWeixin()
        controller = Controller(host.service, host.store.state_dir, host_granted=grant.allowed,
                                client_factory=lambda **_: transport)
        session = ChannelSession(host.service, grant=grant, controller=controller)
        http = make_server(dataclasses.replace(host.xiaoyu.config, host='127.0.0.1', port=0),
                           host.service, channel_session=session)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        base = 'http://127.0.0.1:%d' % http.server_port
        headers = {'Authorization': 'Bearer ' + host.xiaoyu.config.token, 'Content-Type': 'application/json'}
        original_sync = host.service.sync
        def fast_sync(*args, **kwargs):
            if kwargs.get('wait'):
                kwargs['wait'] = 0.1
            return original_sync(*args, **kwargs)
        try:
            with mock.patch.object(host.service, 'sync', side_effect=fast_sync):
                with urllib.request.urlopen(urllib.request.Request(base + '/v1/weixin/bind',
                                            data=b'{}', headers=headers), timeout=6) as response:
                    attempt = json.load(response)['id']
                with urllib.request.urlopen(urllib.request.Request(base + '/v1/weixin/bind/' + attempt,
                                            headers=headers), timeout=6) as response:
                    self.assertEqual(json.load(response)['state'], 'confirmed')
                self.assertTrue(got_reply.wait(5))
                self.assertEqual(transport.sent[0][0:2], ('private-owner', 'private-context'))
                self.assertEqual(transport.sent[0][3], 're:wechat-round-trip')
                self.assertTrue(until(lambda: any('wechat-round-trip' in (message.get('text') or '')
                    for message in home.service.sync('weixin', states=['weixin'])['messages'])))
                def delivered_on(service):
                    return any('sent' in (message.get('delivery') or {}).values()
                               for message in service.sync('weixin', states=['weixin'])['messages'])
                self.assertTrue(until(lambda: delivered_on(host.service)))
                self.assertTrue(until(lambda: delivered_on(home.service)))
                # A reader need not own the channel catalog: route by the
                # source block identity, then let the host verify its grant.
                block = host.service.runtime_id + '/retry:reply#0'
                with mock.patch.object(host.service, '_channel_session', session, create=True), \
                     mock.patch.object(session, 'retry_delivery', return_value='queued') as retry:
                    result = home.service.channel_resend(block, 'e' * 32, 3, True)
                    self.assertEqual('queued', result['status'])
                    self.assertTrue(until(lambda: retry.called))
                    retry.assert_called_once_with(block, 'e' * 32, 3, confirm_unknown=True)
                shared = json.dumps([host.service.sync('weixin', states=['weixin']),
                                     home.service.sync('weixin', states=['weixin']),
                                     list(host.mesh._outbox.values())], ensure_ascii=False)
                for secret in ('private-token', 'private-context', 'private-owner', 'private-qr'):
                    self.assertNotIn(secret, shared)
                self.assertFalse(any(message.get('conversation') == 'weixin' for message in
                    home.service.sync('default', all_conversations=True)['messages']))
                self.assertIsNone(home.mesh.owner_of('weixin')['owner'])
                main = home.service.submit('main-not-for-weixin', client_id='main-isolated')
                side_id = home.service.conversation_create({'id': 'side-not-for-weixin'})['id']
                side = home.service.submit('side-not-for-weixin', conversation=side_id, client_id='side-isolated')
                self.assertTrue(home.done(main['id']))
                self.assertTrue(home.done(side['id']))
                release_poll.set()
                self.assertTrue(session.shutdown(timeout=2))
                grant = HostGrant(host.service)
                controller = Controller(host.service, host.store.state_dir, host_granted=grant.allowed,
                                        client_factory=lambda **_: transport)
                session = ChannelSession(host.service, grant=grant, controller=controller)
                self.assertEqual(session.recover(), {'state': 'active'})
                grant.begin_release()
                self.assertEqual(grant.resolve(timeout=5)['status'], 'applied')
                release_poll.set()
                self.assertTrue(controller.prepare_release(timeout=2))
                grant.release(controller)
                self.assertEqual(grant.resolve(timeout=5)['status'], 'applied')
                self.assertEqual(len(transport.sent), 1)
        finally:
            release_poll.set()
            controller.close(timeout=2)
            grant._host_lock.release()

    def test_interrupted_channel_authority_request_reconciles_without_resending(self):
        from xiaoyu_runtime.channels.authority_client import AuthorityUnavailable
        from xiaoyu_runtime.channels.controller import Controller
        from xiaoyu_runtime.channels.host_grant import HostGrant
        home = self.node('ReconcileHome', home=True)
        host = self.node('ReconcileHost', peers=[home.url])
        home.service._enable_channel_authority()
        self.assertTrue(until(lambda: host.mesh.authority() == home.service.runtime_id))
        self.assertTrue(until(lambda: home.mesh.peer_id(host.service.runtime_id) is not None))
        self.network.down.update({home.url, host.url})
        grant = HostGrant(host.service)
        controller = Controller(host.service, host.store.state_dir, host_granted=grant.allowed)
        recovered = None
        controller2 = None
        third = None
        controller3 = None
        def deliver(sender, receiver, kind, key):
            row = sender.mesh._outbox[(kind, key)]
            receiver.mesh._take({sender.service.runtime_id: {
                'epoch': sender.xiaoyu.cards.epoch, 'upto': row['oseq'], 'records': [dict(row)]}},
                sender.service.runtime_id)
        try:
            request = grant.reserve()
            self.assertEqual(request['status'], 'queued')
            self.assertTrue(grant.shutdown(controller, timeout=1))
            recovered = HostGrant(host.service)
            with self.assertRaises(AuthorityUnavailable):
                recovered.recover()
            self.assertEqual(recovered.reconcile()['status'], 'queued')
            deliver(host, home, 'command', request['command_id'])
            deliver(home, host, 'result', request['command_id'] + ':accepted')
            self.assertEqual(recovered.reconcile()['status'], 'applied')
            view = recovered.recover()
            deliver(host, home, 'command', view['command_id'])
            deliver(home, host, 'result', view['command_id'] + ':accepted')
            self.assertEqual(recovered.resolve()['status'], 'applied')
            self.assertTrue(recovered.allowed())
            self.assertEqual(home.service._channel_authority.view()['generation'], 1)
            controller2 = Controller(host.service, host.store.state_dir, host_granted=recovered.allowed)
            activate = recovered.activate()
            deliver(host, home, 'command', activate['command_id'])
            deliver(home, host, 'result', activate['command_id'] + ':accepted')
            self.assertEqual(recovered.resolve()['status'], 'applied')
            revoke = recovered.begin_release()
            self.assertTrue(recovered.shutdown(controller2, timeout=1))
            third = HostGrant(host.service)
            with self.assertRaises(AuthorityUnavailable):
                third.recover()
            self.assertEqual(third.reconcile()['status'], 'queued')
            deliver(host, home, 'command', revoke['command_id'])
            deliver(home, host, 'result', revoke['command_id'] + ':accepted')
            self.assertEqual(third.reconcile()['status'], 'applied')
            view = third.recover()
            deliver(host, home, 'command', view['command_id'])
            deliver(home, host, 'result', view['command_id'] + ':accepted')
            self.assertEqual(third.resolve()['channel_authority']['state'], 'revoking')
            self.assertFalse(third.allowed())
            controller3 = Controller(host.service, host.store.state_dir, host_granted=third.allowed)
            release = third.release(controller3)
            deliver(host, home, 'command', release['command_id'])
            deliver(home, host, 'result', release['command_id'] + ':accepted')
            self.assertEqual(third.resolve()['channel_authority']['state'], 'unbound')
        finally:
            self.network.down.clear()
            controller.close(timeout=1)
            if controller2 is not None:
                controller2.close(timeout=1)
            if controller3 is not None:
                controller3.close(timeout=1)
            if recovered is not None:
                recovered._host_lock.release()
            if third is not None:
                third._host_lock.release()

    def test_explicit_command_identity_cannot_be_reused_for_different_operation(self):
        node = self.node('CommandIdentity')
        target, command = 'a' * 32, 'b' * 32
        first = node.mesh.command(target, 'weixin_authority', {'action': 'reserve'}, command_id=command)
        sequence = node.mesh._outbox[('command', command)]['oseq']
        self.assertEqual(first, command)
        self.assertEqual(node.mesh.command(target, 'weixin_authority', {'action': 'reserve'}, command_id=command), command)
        self.assertEqual(node.mesh._outbox[('command', command)]['oseq'], sequence)
        with self.assertRaises(MeshError):
            node.mesh.command(target, 'weixin_authority', {'action': 'view'}, command_id=command)
        with self.assertRaises(MeshError):
            node.mesh.command(target, 'weixin_authority', {}, command_id='')

    def test_channel_host_grant_controls_binding_and_handoff_over_mesh(self):
        from xiaoyu_runtime.channels.controller import Controller
        from xiaoyu_runtime.channels.host_grant import HostGrant
        from test_channel_controller import Client
        home = self.node('GrantHome', home=True)
        host = self.node('GrantHost', peers=[home.url])
        home.service._enable_channel_authority()
        self.assertTrue(until(lambda: host.mesh.authority() == home.service.runtime_id))
        host.xiaoyu.intake.writer('mesh', actor='fixture').conversation_observe_weixin()
        self.assertTrue(host.xiaoyu.cards.conversations.get('weixin')['observed_only'])
        grant = HostGrant(host.service)
        controller = Controller(host.service, host.store.state_dir, host_granted=grant.allowed,
                                client_factory=lambda **_: Client())
        self.addCleanup(controller.close)
        self.assertFalse(grant.allowed())
        grant.reserve()
        self.assertEqual(grant.resolve(timeout=5)['status'], 'applied')
        self.assertTrue(grant.allowed())
        claimed = grant.prepare_host()
        self.assertEqual(claimed['owner'], host.service.runtime_id)
        self.assertNotIn('observed_only', claimed)
        with mock.patch('xiaoyu_runtime.channels.controller.ChannelWorker') as worker:
            worker.return_value.stop.return_value = True
            attempt = controller.begin()['id']
            self.assertEqual(controller.poll(attempt)['state'], 'confirmed')
            grant.activate()
            self.assertEqual(grant.resolve(timeout=5)['status'], 'applied')
            self.assertTrue(grant.shutdown(controller, timeout=1))
            self.assertFalse(grant.allowed())
            recovered = HostGrant(host.service)
            recovered.recover()
            self.assertEqual(recovered.resolve(timeout=5)['status'], 'applied')
            self.assertTrue(recovered.allowed())
            self.assertEqual(recovered.prepare_host()['grant_generation'], claimed['grant_generation'])
            controller = Controller(host.service, host.store.state_dir, host_granted=recovered.allowed,
                                    client_factory=lambda **_: Client())
            self.addCleanup(controller.close)
            self.assertTrue(controller.resume())
            recovered.begin_release()
            self.assertFalse(recovered.allowed())
            self.assertEqual(recovered.resolve(timeout=5)['status'], 'applied')
            recovered.release(controller)
            self.assertEqual(recovered.resolve(timeout=5)['status'], 'applied')
            self.assertFalse(recovered.allowed())
        # Channel credentials stay in the host's private file, never in mesh commands.
        self.assertNotIn('private-token', json.dumps(list(host.mesh._outbox.values())))

    def test_channel_authority_client_waits_for_mesh_result_and_drains_before_release(self):
        from xiaoyu_runtime.channels.authority_client import AuthorityClient, AuthorityUnavailable
        home = self.node('AuthorityClientHome', home=True)
        host = self.node('AuthorityClientHost', peers=[home.url])
        home.service._enable_channel_authority()
        self.assertTrue(until(lambda: host.mesh.authority() == home.service.runtime_id))
        client = AuthorityClient(host.service)
        grant = client.wait(client.reserve(), timeout=5)
        self.assertEqual(grant['status'], 'applied')
        self.assertEqual(grant['channel_authority']['host'], host.service.runtime_id)
        generation = grant['channel_authority']['generation']
        self.assertEqual(client.wait(client.activate(generation), timeout=5)['status'], 'applied')
        self.assertEqual(client.wait(client.begin_release(generation), timeout=5)['status'], 'applied')
        controller = mock.Mock()
        controller.prepare_release.return_value = False
        with self.assertRaises(AuthorityUnavailable):
            client.release(controller, generation)
        self.assertEqual(home.service._channel_authority.view()['state'], 'revoking')
        controller.prepare_release.return_value = True
        controller.close.return_value = True
        released = client.finish_release(client.release(controller, generation), controller, generation, timeout=5)
        self.assertEqual(released['status'], 'applied')
        self.assertEqual(released['channel_authority']['state'], 'unbound')
        controller.close.assert_called_once_with(timeout=0)

    def test_channel_authority_commands_use_record_origin_and_fail_closed_on_release(self):
        home = self.node('ChannelAuthority', home=True)
        one = self.node('FirstHost', peers=[home.url])
        two = self.node('SecondHost', peers=[home.url])
        self.assertTrue(until(lambda: one.mesh.authority() == home.service.runtime_id))
        self.assertTrue(until(lambda: two.mesh.authority() == home.service.runtime_id))
        self.assertTrue(until(lambda: home.mesh.peer_id(one.service.runtime_id) is not None))
        self.assertTrue(until(lambda: home.mesh.peer_id(two.service.runtime_id) is not None))

        def command(node, args):
            key = node.mesh.command(home.service.runtime_id, 'weixin_authority', args)
            self.assertTrue(until(lambda: node.mesh.outcome(key, home.service.runtime_id, 'accepted') is not None))
            return node.mesh.outcome(key, home.service.runtime_id, 'accepted')

        self.assertFalse(command(one, {'action': 'reserve'})['ok'])
        home.service._enable_channel_authority()
        self.assertEqual(home.service._enable_channel_authority()['state'], 'unbound')
        with self.assertRaises(RequestError):
            one.service._enable_channel_authority()
        forged_id = 'f' * 32
        home.mesh._command(one.service.runtime_id, {
            'id': forged_id, 'target': home.service.runtime_id, 'from': two.service.runtime_id,
            'kind': 'weixin_authority', 'args': {'action': 'reserve'}, 'expires_at': time.time() + 30})
        self.assertFalse(home.mesh._outbox[('result', forged_id + ':accepted')]['data']['ok'])
        self.assertEqual(home.service._channel_authority.view()['state'], 'unbound')
        reserved = command(one, {'action': 'reserve'})
        self.assertTrue(reserved['ok'], reserved)
        state = reserved['channel_authority']
        self.assertEqual(state['host'], one.service.runtime_id)
        generation = state['generation']
        self.assertEqual(command(two, {'action': 'view'})['channel_authority']['host'], one.service.runtime_id)
        self.assertFalse(command(two, {'action': 'reserve'})['ok'])
        self.assertFalse(command(two, {'action': 'reserve', 'host': one.service.runtime_id})['ok'])
        self.assertTrue(command(one, {'action': 'activate', 'generation': generation})['ok'])
        self.assertFalse(command(one, {'action': 'begin_release', 'generation': generation + 1})['ok'])
        self.assertTrue(command(one, {'action': 'begin_release', 'generation': generation})['ok'])
        self.assertFalse(command(one, {'action': 'release', 'generation': generation, 'worker_stopped': False})['ok'])
        self.assertFalse(command(two, {'action': 'reserve'})['ok'])
        self.assertTrue(command(one, {'action': 'release', 'generation': generation, 'worker_stopped': True})['ok'])
        next_grant = command(two, {'action': 'reserve'})
        self.assertTrue(next_grant['ok'], next_grant)
        self.assertEqual(next_grant['channel_authority']['host'], two.service.runtime_id)
        self.assertGreater(next_grant['channel_authority']['generation'], generation)

    def test_weixin_replica_is_readable_but_never_grants_local_ownership(self):
        a = self.node('ChannelHost')
        b = self.node('ChannelViewer', peers=[a.url])
        a.service._owner_writer.conversation_create_weixin()
        sent = a.service.submit('wechat-fig-message', conversation='weixin', client_id='wx-one',
                                _channel_authorized=True)
        self.assertTrue(a.done(sent['id']))
        self.assertTrue(until(lambda: bool(b.xiaoyu.cards.conversations.get('weixin'))))
        self.assertTrue(until(lambda: any('wechat-fig-message' in (row.get('text') or '')
                              for row in b.service.sync('weixin', states=['weixin'])['messages'])))
        observed = b.xiaoyu.cards.conversations.get('weixin')
        self.assertTrue(observed['observed_only'])
        self.assertNotIn('owner', observed)
        self.assertIsNone(b.mesh.owner_of('weixin')['owner'])
        with self.assertRaisesRegex(RequestError, '未绑定'):
            b.service.submit('must not send', conversation='weixin', _channel_authorized=True)
        self.assertFalse(any(row.get('conversation') == 'weixin'
                             for row in b.service.sync('default', all_conversations=True)['messages']))
        self.assertIn('wechat-fig-message', b.xiaoyu.context.recall('default', 'wechat-fig-message'))
        side = b.service.conversation_create({'id': 'side-channel-recall'})['id']
        self.assertIn('wechat-fig-message', b.xiaoyu.context.recall(side, 'wechat-fig-message'))
        self.assertFalse(any('wechat-fig-message' in turn['text']
                             for turn in b.xiaoyu.context.turns(side)))

    def test_weixin_recall_reads_received_main_and_side_records_without_mixing(self):
        a = self.node('RecallMac')
        b = self.node('RecallWindows', peers=[a.url])
        b.service._owner_writer.conversation_create_weixin()
        side = a.service.conversation_create({'id': 'side-recall-mesh'})['id']
        first = a.service.submit('mesh-main-saffron', client_id='shared-main')
        second = a.service.submit('mesh-side-saffron', conversation=side, client_id='shared-side')
        self.assertTrue(a.done(first['id']))
        self.assertTrue(a.done(second['id']))
        self.assertTrue(until(lambda: b.xiaoyu.cards.conversations.get(side) is not None))
        self.assertTrue(until(lambda: b.card_from(a, a.service.get(first['id'])['card']) is not None))
        self.assertTrue(until(lambda: b.card_from(a, a.service.get(second['id'])['card']) is not None))
        self.assertTrue(until(lambda: 'mesh-side-saffron' in b.xiaoyu.context.recall('weixin', 'saffron')))
        result = b.xiaoyu.context.recall('weixin', 'saffron')
        self.assertIn('mesh-main-saffron', result)
        self.assertIn('mesh-side-saffron', result)
        observed = a.xiaoyu.record.observed_open('__desktop__', '同步排查', 'done', 'codex')
        a.xiaoyu.record.observed(observed['id'], monitoring_only=True, source='desktop',
                                 session_id='native-mesh-history', entries=[
                                     {'id': 'mesh-native:you', 'role': 'you', 'text': 'arbutus desktop request', 'at': 1.0}])
        self.assertTrue(until(lambda: b.card_from(a, observed['id']) is not None))
        self.assertTrue(until(lambda: 'arbutus desktop request' in b.xiaoyu.context.recall('weixin', 'arbutus')))
        self.assertIn('RecallMac', b.xiaoyu.context.recall('weixin', 'arbutus'))
        self.assertEqual([], b.xiaoyu.context.turns('weixin'))

    def test_side_chat_delete_converges_without_restoring_stale_history(self):
        network=Network();a=Node(self,network,'DeleteMac');b=Node(self,network,'DeleteWindows',peers=[a.url])
        chat=a.service.conversation_create({'id':'side-delete-mesh'})
        card=a.xiaoyu.record.task_open(chat['id'],'private task','done','claude')
        a.xiaoyu.record.reply(card['id'],'private deletion body',turn_id='delete-mesh-turn')
        self.assertTrue(until(lambda: b.xiaoyu.cards.conversations.get(chat['id']) is not None))
        self.assertTrue(until(lambda: b.card_from(a,card['id']) is not None))
        b.service.conversation_delete(chat['id'])
        self.assertTrue(until(lambda: bool((a.xiaoyu.cards.conversations.get(chat['id']) or {}).get('deleted'))))
        self.assertEqual([],a.xiaoyu.cards.recent(chat['id'],100))
        self.assertEqual([],b.xiaoyu.cards.recent(chat['id'],100))
        self.assertEqual(0,a.service.share(chat['id'],[{'id':'old','text':'private deletion body','reply':'old answer'}]))


    def setUp(self):
        super().setUp()
        self.network = Network()
        self.addCleanup(lambda: [mesh.close() for mesh in self.network.nodes.values()])

    def node(self, name, **kwargs):
        return Node(self, self.network, name, **kwargs)

    def test_two_runtimes_share_one_subject_and_each_others_records(self):
        a = self.node("Mac")
        b = self.node("Windows", peers=[a.url])
        self.assertEqual(a.service.info()["subject_id"], subject_for(SECRET))
        self.assertEqual(a.service.info()["subject_id"], b.service.info()["subject_id"])
        self.assertIn("mesh", b.service.info()["capabilities"])
        sent = a.service.submit("在 Mac 上说的一句", client_id="c1")
        self.assertTrue(a.done(sent["id"]))
        card = a.service.get(sent["id"])["card"]
        # 卡可能先以开头的样子到，内容随后跟上。
        self.assertTrue(until(lambda: len((b.card_from(a, card) or {}).get("entries", [])) == 2))
        mirrored = b.card_from(a, card)
        self.assertEqual("Mac", mirrored["origin_name"])
        self.assertEqual(["在 Mac 上说的一句", "re:在 Mac 上说的一句"], [entry["text"] for entry in mirrored["entries"]])
        # 同步接口里：消息编号带着产地，事是这里的本地卡。
        page = b.service.sync("default", 0)
        ids = {message["id"] for message in page["messages"]}
        self.assertIn(local_id(a.service.runtime_id, sent["id"] + ":you"), ids)
        self.assertIn(mirrored["id"], {task["id"] for task in page["tasks"]})
        # 那台的小鱼答过的话，这台的小鱼下次接话时会被告知。
        self.assertIn(sent["id"], [turn["id"] for turn in b.store.transcript.turns("default")])
        # 反过来也一样（B 连得上 A，A 也从 B 那里知道 B 的地址并连回去）。
        back = b.service.submit("在 Windows 上说的", client_id="c2")
        self.assertTrue(b.done(back["id"]))
        self.assertTrue(until(lambda: a.card_from(b, b.service.get(back["id"])["card"]) is not None))

    def test_records_reach_runtimes_that_only_know_a_relay(self):
        hub = self.node("Hub")
        left = self.node("Left", peers=[hub.url])
        right = self.node("Right", peers=[hub.url])
        sent = left.service.submit("从左边说", client_id="l1")
        self.assertTrue(left.done(sent["id"]))
        card = left.service.get(sent["id"])["card"]
        self.assertTrue(until(lambda: right.card_from(left, card) is not None, 15))
        # Right 从 Hub 那里知道了 Left 的地址，之后直接连 Left。
        self.assertTrue(until(lambda: any(peer["name"] == "Left" for peer in right.mesh.status()["peers"])))

    def test_a_runtime_nobody_can_reach_pushes_its_records(self):
        home = self.node("Home")
        cloud = self.node("Cloud", peers=[home.url], advertise=False)
        sent = cloud.service.submit("云上说的", client_id="x1")
        self.assertTrue(cloud.done(sent["id"]))
        card = cloud.service.get(sent["id"])["card"]
        self.assertTrue(until(lambda: home.card_from(cloud, card) is not None))
        self.assertNotIn("http://cloud.test", self.network.calls)

    def test_a_sentence_in_another_runtimes_task_is_done_there(self):
        a = self.node("Mac")
        b = self.node("Windows", peers=[a.url])
        sent = a.service.submit("开个头", client_id="s1")
        self.assertTrue(a.done(sent["id"]))
        card = a.service.get(sent["id"])["card"]
        self.assertTrue(until(lambda: b.card_from(a, card) is not None))
        alias = b.card_from(a, card)["id"]
        relayed = b.service.submit("接着说一句", client_id="s2", card=alias, pin=True)
        self.assertTrue(b.done(relayed["id"]))
        finished = b.service.get(relayed["id"])
        self.assertEqual("done", finished["status"], finished.get("error"))
        # 那台在自己的那件事里接住了这句话，用的是同一个编号。
        self.assertTrue(until(lambda: any(entry["id"] == relayed["id"] + ":you"
                                          for entry in a.xiaoyu.cards.get(card)["entries"])))
        self.assertIsNotNone(a.service.get(relayed["id"]))
        # 这台的聊天里这句话只有一条（占位被那台的记录替换，编号不变）。
        self.assertTrue(until(lambda: (b.xiaoyu.cards.message(relayed["id"] + ":you") or {}).get("mesh_origin")
                              == a.service.runtime_id))
        page = b.service.sync("default", 0)
        texts = [message["text"] for message in page["messages"] if message["text"] == "接着说一句"]
        self.assertEqual(1, len(texts))
        # 转交出去的占位不会再转给别人。
        self.assertNotIn(relayed["id"] + ":you", {message["id"] for message in b.xiaoyu.cards.own_since(0)[2]})

    def test_relay_to_an_offline_runtime_fails_with_a_reason(self):
        a = self.node("Mac")
        b = self.node("Windows", peers=[a.url])
        sent = a.service.submit("开个头", client_id="o1")
        self.assertTrue(a.done(sent["id"]))
        card = a.service.get(sent["id"])["card"]
        self.assertTrue(until(lambda: b.card_from(a, card) is not None))
        # Mac 关机了：两边都连不上（正在等的那次交换先收尾），过了一阵子。
        self.network.down.update({a.url, b.url})
        a.mesh.close()
        time.sleep(1.5)
        clock = [10 ** 10]
        b.mesh._clock = lambda: clock[0]
        relayed = b.service.submit("Mac 关机了", client_id="o2", card=b.card_from(a, card)["id"], pin=True)
        self.assertTrue(b.done(relayed["id"]))
        self.assertIn("离线", b.service.get(relayed["id"])["error"])

    def test_relay_never_silently_drops_image_attachments(self):
        a = self.node("Mac")
        b = self.node("Windows", peers=[a.url])
        self.assertTrue(until(lambda: b.mesh._reachable(a.service.runtime_id) is None))
        before = dict(b.mesh._relays)
        problem = b.mesh.relay({"id": "picture-turn", "relay_to": a.service.runtime_id,
                                "conversation": "default", "text": "看看",
                                "attachments": [{"id": "image-1"}]})
        self.assertIsNone(problem)
        self.assertEqual(b.mesh._relays.get("picture-turn"), a.service.runtime_id)
        self.assertNotIn("picture-turn", before)
        self.assertFalse(any(item.get("data", {}).get("args", {}).get("message_id") == "picture-turn"
                             for item in b.mesh._outbox.values()))

    def test_cancel_and_approval_answers_go_to_the_task_owner(self):
        worker = Scripted("claude")
        a = self.node("Mac", agents=[worker])
        b = self.node("Windows", peers=[a.url])
        card = a.xiaoyu.record.task_open("default", "长任务", "working", "claude")
        approval = a.xiaoyu.approvals.ask(card["id"], "default", "claude", "claude · Bash", "rm -rf build")
        self.assertTrue(until(lambda: b.card_from(a, card["id"]) is not None))
        mirrored = local_id(a.service.runtime_id, approval)
        self.assertTrue(until(lambda: any(item["id"] == mirrored for item in b.xiaoyu.approvals.pending())))
        self.assertTrue(b.service.approve(mirrored, "allow"))
        self.assertTrue(until(lambda: (a.xiaoyu.approvals.get(approval) or {}).get("status") == "allowed"))
        self.assertTrue(until(lambda: not any(item["id"] == mirrored for item in b.xiaoyu.approvals.pending())))
        b.service.cancel(b.card_from(a, card["id"])["id"])
        self.assertTrue(until(lambda: a.xiaoyu.cards.get(card["id"])["state"] == "cancelled"))
        self.assertTrue(until(lambda: b.card_from(a, card["id"])["state"] == "cancelled"))
        # The origin records which computer (and which end there) stopped it.
        self.assertEqual(a.xiaoyu.cards.get(card["id"])["cancelled_by"], "mesh:Windows/client")

    def test_xiaoyu_hands_work_to_a_helper_on_another_runtime(self):
        a = self.node("Mac", agents=[Scripted("claude"), Scripted("codex", speaks=False)])
        b = self.node("Windows", peers=[a.url])
        self.assertTrue(until(lambda: b.xiaoyu.has("codex@Mac"), 35) or self._refresh(b, "codex@Mac"))
        sent = b.service.submit("看看日志", client_id="d1", agent="codex@Mac")
        self.assertTrue(b.done(sent["id"]))
        card = b.service.get(sent["id"])["card"]
        self.assertTrue(until(lambda: b.xiaoyu.cards.get(card)["state"] == "done", 15))
        finished = b.xiaoyu.cards.get(card)
        self.assertEqual({"runtime": a.service.runtime_id, "name": "Mac"}, finished["delegated_to"])
        self.assertIn("看看日志", finished["entries"][-1]["text"])
        # Mac 替这件事开的卡记着替谁做；Windows 不再另外显示它。
        worker = [item for item in a.xiaoyu.cards.recent(None, 100) if item.get("delegated_from")]
        self.assertEqual(1, len(worker))
        self.assertEqual(b.service.runtime_id, worker[0]["delegated_from"]["runtime"])
        self.assertIsNone(b.card_from(a, worker[0]["id"]))

    def test_continuing_another_runtimes_desktop_session_happens_on_that_runtime(self):
        heir = Scripted("cc", kind="claude_code")
        home = self.folder / 'mesh-native-home'
        heir.spec = dataclasses.replace(heir.spec, config_dir=home)
        native = home / 'projects' / 'fixture' / 's-mac.jsonl'
        native.parent.mkdir(parents=True)
        native.write_text(json.dumps({'sessionId': 's-mac', 'cwd': str(self.folder),
                                      'type': 'user', 'message': {'content': 'fixture'}}) + '\n')
        a = self.node("Mac", agents=[Scripted("claude"), heir])
        b = self.node("Windows", peers=[a.url])
        desktop = a.xiaoyu.record.observed_open("__desktop__", "改同步方案", "done", "claude")
        a.xiaoyu.record.observed(desktop["id"], monitoring_only=True, source="desktop", session_id="s-mac",
                                  desktop_cwd=str(self.folder), desktop_status="idle", entries=[
                                      {"id": "e1", "role": "you", "text": "先出方案", "at": 1.0}])
        self.assertTrue(until(lambda: b.card_from(a, desktop["id"]) is not None))
        mirrored = b.card_from(a, desktop["id"])
        self.assertTrue(b.mesh.amend(mirrored, "照方案改代码", continue_desktop=True))
        # Mac 自己开一件「接着：…」，交给它能接 Claude 会话的帮手，在那个会话里接着做。
        self.assertTrue(until(lambda: any(card["title"].startswith("接着：") and card["agent"] == "cc"
                                          for card in a.xiaoyu.cards.recent(None, 100)), 10))
        self.assertTrue(until(lambda: heir.jobs and heir.jobs[-1].session_id == "s-mac", 10))
        self.assertFalse(any(card["title"].startswith("接着：") for card in b.xiaoyu.cards.recent(None, 100)
                             if not card.get("mesh_origin")))

    def sharing(self, node, env=(), folders=None):
        shares = node.mesh.shares
        shares.env = list(env)
        shares.folders = dict(folders or {})
        shares.shared = self.folder / ("shared-" + node.mesh.name)
        return shares

    def test_named_environment_values_are_shared_and_the_latest_write_wins(self):
        a = self.node("Mac")
        b = self.node("Windows", peers=[a.url])
        mac, windows = self.sharing(a, ["DEEPSEEK_API_KEY"]), self.sharing(b, ["DEEPSEEK_API_KEY"])
        mac.shared.mkdir()
        (mac.shared / "DEEPSEEK_API_KEY").write_text("sk-one", encoding="utf-8")
        (mac.shared / "NOT_LISTED").write_text("secret", encoding="utf-8")
        mac.scan()
        target = windows.shared / "DEEPSEEK_API_KEY"
        self.assertTrue(until(lambda: target.is_file() and target.read_text(encoding="utf-8") == "sk-one"))
        self.assertFalse((windows.shared / "NOT_LISTED").exists())
        target.write_text("sk-two", encoding="utf-8")
        windows.scan(now=10 ** 10)
        source = mac.shared / "DEEPSEEK_API_KEY"
        self.assertTrue(until(lambda: source.read_text(encoding="utf-8") == "sk-two"))
        # 收到的值不会被当成这台的新值再报回去。
        self.assertEqual(0, mac.scan())

    def test_folders_are_matched_by_name_and_deletions_follow(self):
        a = self.node("Mac")
        b = self.node("Windows", peers=[a.url])
        c = self.node("Other", peers=[a.url])
        mac_memory, windows_memory = self.folder / "mac-memory", self.folder / "C--Users-x" / "memory"
        mac_memory.mkdir()
        windows_memory.mkdir(parents=True)
        (mac_memory / "MEMORY.md").write_text("- [偏好](pref.md)\n", encoding="utf-8")
        (mac_memory / "notes").mkdir()
        (mac_memory / "notes" / "pref.md").write_text("说中文\n", encoding="utf-8")
        mac = self.sharing(a, folders={"claude-memory": mac_memory})
        self.sharing(b, folders={"claude-memory": windows_memory})
        self.sharing(c)  # 没配这个文件夹：只转，不落地
        mac.scan()
        self.assertTrue(until(lambda: (windows_memory / "notes" / "pref.md").is_file()))
        self.assertEqual("- [偏好](pref.md)\n", (windows_memory / "MEMORY.md").read_text(encoding="utf-8"))
        (mac_memory / "notes" / "pref.md").unlink()
        mac.scan()
        self.assertTrue(until(lambda: not (windows_memory / "notes" / "pref.md").exists()))
        self.assertTrue((windows_memory / "MEMORY.md").is_file())

    def test_desktop_histories_are_mirrored_per_computer_and_listed_for_desktop_agents(self):
        a = self.node("Mac")
        b = self.node("Windows", peers=[a.url])
        mac = self.sharing(a)
        mac.history.mkdir(parents=True)
        (mac.history / "claude-s1.md").write_text("# 改同步方案\n\n### 主人 · 2026\n\n先出方案\n", encoding="utf-8")
        mac.scan()
        state = Path(b.xiaoyu._config.state_dir)
        self.assertTrue(until(lambda: shared_history.elsewhere(state)))
        listed = shared_history.elsewhere(state)[0]
        self.assertEqual(("Mac", "改同步方案"), (listed["computer"], listed["title"]))
        self.assertIn("先出方案", Path(listed["path"]).read_text(encoding="utf-8"))
        text, _ = shared_history.context({"tasks": [], "dialog": [], "elsewhere": [listed]})
        self.assertIn(listed["path"], text)

    def test_shared_paths_from_another_runtime_cannot_escape_the_folder(self):
        a = self.node("Mac")
        root = self.folder / "memory"
        root.mkdir()
        shares = self.sharing(a, folders={"claude-memory": root})
        for bad in ("../outside.md", "/etc/passwd", "C:/x.md", "a/../../b.md"):
            shares.apply("f" * 32, "Evil", {"key": "folder:claude-memory/" + bad, "kind": "folder",
                                           "name": "claude-memory", "path": bad, "text": "x", "at": 10 ** 10})
        self.assertFalse((self.folder / "outside.md").exists())
        self.assertEqual([], list(root.rglob("*")))

    def _refresh(self, node, name):
        node.mesh.sweep()
        return until(lambda: node.xiaoyu.has(name))

    def test_wrong_secret_is_refused(self):
        a = self.node("Mac")
        b = self.node("Intruder", peers=[a.url], secret="another-secret-0123456789abc")
        self.assertTrue(until(lambda: any(peer["error"] or peer["runtime_id"] for peer in b.mesh.status()["peers"])
                              or b.mesh.status()["unresolved"], 3))
        self.assertEqual([], a.mesh.status()["peers"])

    def test_replicas_and_local_cards_survive_a_restart(self):
        a = self.node("Mac")
        b = self.node("Windows", peers=[a.url])
        sent = a.service.submit("记住这件事", client_id="r1")
        self.assertTrue(a.done(sent["id"]))
        card = a.service.get(sent["id"])["card"]
        self.assertTrue(until(lambda: b.card_from(a, card) is not None))
        alias = b.card_from(a, card)["id"]
        b.close()
        del self.network.nodes[b.url]
        again = self.node("Windows", peers=[a.url])
        self.assertEqual(alias, again.card_from(a, card)["id"])
        self.assertEqual(b.service.runtime_id, again.service.runtime_id)
        # 重启后不会把别人的事标成“Runtime 重启了，没做完”。
        self.assertNotEqual("failed", again.card_from(a, card)["state"])


class MeshConfigTests(TempDirCase):
    def load(self, env=None, **overrides):
        return config_module.load(write_config(self.folder, **overrides), env or {})

    def test_mesh_is_off_without_a_secret_and_needs_a_long_one(self):
        self.assertIsNone(self.load().mesh_secret)
        with self.assertRaises(config_module.ConfigError):
            self.load(mesh={"secret": "short"})
        with self.assertRaises(config_module.ConfigError):
            self.load(mesh={"peers": ["http://other.test"]})
        with self.assertRaises(config_module.ConfigError):
            self.load(mesh={"secret": SECRET, "peers": ["other.test"]})

    def test_peers_secret_and_advertised_address_come_from_config_env_or_the_listen_address(self):
        (self.folder / "mesh.key").parent.mkdir(parents=True, exist_ok=True)
        (self.folder / "mesh.key").write_text(SECRET + "\n", encoding="utf-8")
        loaded = self.load(mesh={"secret_file": "mesh.key", "peers": ["https://home.test/"]},
                           server={"host": "100.1.2.3", "port": 9000, "token": TOKEN})
        self.assertEqual((SECRET, ["https://home.test"], ["http://100.1.2.3:9000"]),
                         (loaded.mesh_secret, loaded.mesh_peers, loaded.mesh_advertise))
        # 只监听本机：不对外说地址（只往外连）。
        self.assertEqual([], self.load(mesh={"secret": SECRET}).mesh_advertise)
        loaded = self.load({"XIAOYU_MESH_SECRET": SECRET, "XIAOYU_MESH_PEERS": "http://a.test, https://b.test",
                            "XIAOYU_MESH_ADVERTISE": "https://me.test"})
        self.assertEqual((["http://a.test", "https://b.test"], ["https://me.test"]),
                         (loaded.mesh_peers, loaded.mesh_advertise))

    def test_shared_env_names_and_folders_come_from_the_config(self):
        loaded = self.load(mesh={"secret": SECRET, "share": {"env": ["DEEPSEEK_API_KEY"],
                                                             "folders": {"claude-memory": "memory"}}})
        self.assertEqual((["DEEPSEEK_API_KEY"], {"claude-memory": (self.folder / "memory").resolve()}),
                         (loaded.mesh_share_env, loaded.mesh_share_folders))
        self.assertEqual(([], {}), (self.load().mesh_share_env, self.load().mesh_share_folders))
        for bad in ({"env": ["not a name"]}, {"folders": {"../x": "memory"}}, {"folders": {"ok": ""}}):
            with self.subTest(bad=bad), self.assertRaises(config_module.ConfigError):
                self.load(mesh={"secret": SECRET, "share": bad})

    def test_desktop_sync_can_be_turned_on_in_the_config(self):
        self.assertFalse(self.load().desktop_sync)
        self.assertTrue(self.load(desktop_sync=True).desktop_sync)
        loaded = self.load(desktop_sync={"enabled": True, "projects": ["work"]})
        self.assertEqual((True, [(self.folder / "work").resolve()]), (loaded.desktop_sync, loaded.desktop_projects))


class MeshHttpTests(TempDirCase):
    """两台真的在本机端口上跑的 Runtime，经真的 HTTP 交换。"""

    def runtime(self, name, peers=()):
        port = free_port()
        url = "http://127.0.0.1:%d" % port
        loaded = config_module.load(write_config(self.folder / name), {
            "XIAOYU_PORT": str(port), "XIAOYU_NAME": name, "XIAOYU_MESH_SECRET": SECRET,
            "XIAOYU_MESH_PEERS": ",".join(peers), "XIAOYU_MESH_ADVERTISE": url})
        store = Store(loaded.state_dir, legacy_agent=loaded.default_agent)
        xiaoyu = Xiaoyu(loaded, [factory.create(spec) for spec in loaded.agents], store)
        service = Service(xiaoyu, store)
        mesh = Mesh(loaded, service, xiaoyu)
        server = make_server(loaded, service)
        server.RequestHandlerClass.log_message = lambda *args: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        mesh.start()
        self.addCleanup(service.close)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(mesh.close)
        return url, service, xiaoyu

    def call(self, method, url, body=None, token=TOKEN):
        request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                         method=method)
        if token:
            request.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read() or b"{}")

    def test_two_runtimes_sync_over_http_and_the_exchange_needs_the_mesh_secret(self):
        home, home_service, _ = self.runtime("Home")
        laptop, _, laptop_xiaoyu = self.runtime("Laptop", peers=[home])
        self.assertEqual(401, self.call("POST", home + "/v1/mesh/exchange", {}, token=None)[0])
        self.assertEqual(401, self.call("POST", home + "/v1/mesh/exchange", {}, token=TOKEN)[0])
        self.assertEqual(401, self.call("GET", home + "/v1/mesh", token=SECRET)[0])
        sent = home_service.submit("家里说的", client_id="h1")
        self.assertTrue(until(lambda: home_service.get(sent["id"])["status"] == "done"))
        card = home_service.get(sent["id"])["card"]
        self.assertTrue(until(lambda: any(item.get("mesh_id") == card and len(item["entries"]) == 2
                                          for item in laptop_xiaoyu.cards.recent(None, 100)), 10))
        status, mesh = self.call("GET", laptop + "/v1/mesh")
        self.assertEqual(200, status)
        self.assertEqual(["Home"], [peer["name"] for peer in mesh["peers"]])
        self.assertTrue(mesh["peers"][0]["online"])
        status, info = self.call("GET", laptop + "/v1/info")
        self.assertIn("mesh", info["capabilities"])
        self.assertEqual(subject_for(SECRET), info["subject_id"])


if __name__ == "__main__":
    unittest.main()
