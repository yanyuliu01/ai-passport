"""Image bridge: durable chunks, authority fencing and delivery without mesh leakage."""
import base64
import dataclasses
import io
import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

from test_ownership import Node, Network, SECRET
from xiaoyu_runtime.delivery.image_relay import CHUNK, ImageRelay, ImageRelayError, STALE_SECONDS
from xiaoyu_runtime.delivery import image_relay
from xiaoyu_runtime.events.image_inputs import upload
from xiaoyu_runtime.loop.xiaoyu import Turn
from xiaoyu_runtime.api.server import make_server
from test_runtime import TempDirCase, until

try:
    from PIL import Image
except ImportError:
    Image = None


@unittest.skipIf(Image is None, "Pillow is needed for image acceptance")
class ImageRelayTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.network = Network()
        self.mac = Node(self, self.network, 'Mac', home=True)
        self.win = Node(self, self.network, 'Windows', peers=[self.mac.url])
        self.assertTrue(until(lambda: self.win.service.owner('default')['owner'] == self.mac.id, 15))
        self.patches = [mock.patch.object(self.win.xiaoyu, 'image_agent', return_value=object()),
                        mock.patch.object(self.mac.xiaoyu, 'image_agent', return_value=object())]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        stream = io.BytesIO()
        Image.new('RGB', (128, 100), 'red').save(stream, 'PNG')
        self.image = stream.getvalue()
        self.attachment = upload(self.win.service.artifacts, self.image)

    def pending_image(self):
        sent = self.win.service.submit('看图', client_id='relay-image-1', image_ids=[self.attachment['id']])
        def pending():
            return self.win.mesh.image_relay.pending(self.mac.id)['transfers']
        self.assertTrue(until(pending, 10))
        return sent, pending()[0]

    def test_roundtrip_resume_duplicate_and_result_ack(self):
        seen = []
        def answer(text, conversation, turn_id, attachments, **kwargs):
            stream, _, _, _ = self.mac.service.artifacts.open_content(attachments[0]['id'])
            with stream:
                seen.append(stream.read())
            return Turn('红色', '红色', 'idle', 'claude')
        with mock.patch.object(self.mac.xiaoyu, 'hear_images', side_effect=answer):
            sent, env = self.pending_image()
            source = self.win.mesh.image_relay
            target = self.mac.mesh.image_relay
            read = source.read({'message_id':sent['id'], 'target_runtime':self.mac.id, 'index':0, 'offset':0})
            body = dict(env, index=0, offset=0, data=read['data'])
            self.assertEqual(target.receive(body)['next'], len(self.image))
            self.assertEqual(target.receive(body)['next'], len(self.image))
            altered = dict(body, data=base64.urlsafe_b64encode(b'x' * len(self.image)).decode().rstrip('='))
            with self.assertRaises(ImageRelayError): target.receive(altered)
            self.assertEqual(target.finish(env)['message_id'], sent['id'])
            self.assertEqual(target.finish(env)['message_id'], sent['id'])
            self.assertTrue(until(lambda: self.mac.get(sent['id']).get('status') == 'done', 10))
            self.assertEqual(seen, [self.image])
            ack = {'message_id':sent['id'], 'target_runtime':self.mac.id, 'status':'done', 'reply':'红色'}
            self.assertEqual(source.ack(ack)['status'], 'recorded')
            self.assertEqual(source.ack(ack)['status'], 'recorded')
            self.assertEqual(self.win.get(sent['id'])['reply'], '红色')
            self.assertFalse(any(item.get('data', {}).get('args', {}).get('message_id') == sent['id'] for item in self.win.mesh._outbox.values()))

    def test_wrong_owner_and_tampered_chunks_are_rejected(self):
        sent, env = self.pending_image()
        read = self.win.mesh.image_relay.read({'message_id':sent['id'], 'target_runtime':self.mac.id, 'index':0, 'offset':0})
        body = dict(env, index=0, offset=0, data=read['data'])
        bad = dict(body, owner_generation=env['owner_generation'] + 1)
        with self.assertRaises(ImageRelayError): self.mac.mesh.image_relay.receive(bad)
        body['data'] = base64.urlsafe_b64encode(b'x' * len(self.image)).decode().rstrip('=')
        self.mac.mesh.image_relay.receive(body)
        with self.assertRaises(ImageRelayError): self.mac.mesh.image_relay.finish(env)
        self.assertIsNone(self.mac.service.get(sent['id']))

    def test_incomplete_offset_and_conflicting_manifest_rejected(self):
        sent, env = self.pending_image()
        read = self.win.mesh.image_relay.read({'message_id':sent['id'], 'target_runtime':self.mac.id, 'index':0, 'offset':0})
        body = dict(env, index=0, offset=0, data=read['data'])
        with self.assertRaises(ImageRelayError): self.mac.mesh.image_relay.receive(dict(body, offset=1))
        self.mac.mesh.image_relay.receive(body)
        with self.assertRaises(ImageRelayError): self.mac.mesh.image_relay.receive(dict(body, text='不同内容'))

    def test_recreated_receiver_resumes_after_chunk_and_lost_confirmation(self):
        sent, env = self.pending_image()
        read = self.win.mesh.image_relay.read({'message_id':sent['id'], 'target_runtime':self.mac.id, 'index':0, 'offset':0})
        body = dict(env, index=0, offset=0, data=read['data'])
        target = self.mac.mesh.image_relay
        target.receive(body)
        target = ImageRelay(self.mac.mesh)  # new process reads only durable filesystem state
        self.assertEqual(target.receive(body)['next'], len(self.image))
        with mock.patch.object(self.mac.xiaoyu, 'hear_images', return_value=Turn('ok', 'ok', 'idle', 'claude')):
            self.assertEqual(target.finish(env)['message_id'], sent['id'])
            target = ImageRelay(self.mac.mesh)
            self.assertEqual(target.finish(env)['message_id'], sent['id'])
            self.assertTrue(until(lambda: self.mac.get(sent['id']).get('status') == 'done', 10))

    def test_receiver_runtime_restart_preserves_incomplete_chunks(self):
        sent, env = self.pending_image()
        read = self.win.mesh.image_relay.read({'message_id':sent['id'], 'target_runtime':self.mac.id, 'index':0, 'offset':0})
        body = dict(env, index=0, offset=0, data=read['data'])
        self.mac.mesh.image_relay.receive(body)
        previous_id = self.mac.id
        self.mac.close()
        self.mac = Node(self, self.network, 'Mac', home=True)
        self.assertEqual(self.mac.id, previous_id)
        self.assertTrue(until(lambda: self.mac.mesh.owner_of('default')['owner'] == self.mac.id, 15))
        with mock.patch.object(self.mac.xiaoyu, 'image_agent', return_value=object()), \
             mock.patch.object(self.mac.xiaoyu, 'hear_images', return_value=Turn('ok', 'ok', 'idle', 'claude')):
            self.assertEqual(self.mac.mesh.image_relay.receive(body)['next'], len(self.image))
            self.assertEqual(self.mac.mesh.image_relay.finish(env)['message_id'], sent['id'])
            self.assertTrue(until(lambda: self.mac.get(sent['id']).get('status') == 'done', 10))

    def test_owner_changes_before_finish_never_accepts_message(self):
        sent, env = self.pending_image()
        read = self.win.mesh.image_relay.read({'message_id':sent['id'], 'target_runtime':self.mac.id, 'index':0, 'offset':0})
        target = self.mac.mesh.image_relay
        target.receive(dict(env, index=0, offset=0, data=read['data']))
        with mock.patch.object(self.mac.mesh, 'owner_of', return_value={'owner':'f'*32, 'generation':env['owner_generation'] + 1}):
            with self.assertRaises(ImageRelayError): target.finish(env)
        self.assertIsNone(self.mac.service.get(sent['id']))

    def test_source_owner_change_marks_uncertain_and_never_retargets(self):
        sent, env = self.pending_image()
        with mock.patch.object(self.win.mesh, 'owner_of', return_value={'owner':'f'*32, 'generation':env['owner_generation'] + 1}):
            self.assertEqual(self.win.mesh.image_relay.pending(self.mac.id)['transfers'], [])
        final = self.win.get(sent['id'])
        self.assertEqual(final['status'], 'failed')
        self.assertEqual(final['operation_state'], 'unknown')
        self.assertIn('核对', final['error'])

    def test_stale_incomplete_transfer_is_reclaimed_and_source_can_retry(self):
        sent, env = self.pending_image()
        read = self.win.mesh.image_relay.read({'message_id':sent['id'], 'target_runtime':self.mac.id, 'index':0, 'offset':0})
        body = dict(env, index=0, offset=0, data=read['data'])
        target = self.mac.mesh.image_relay
        target.receive(body)
        folder = target._folder(env)
        old = time.time() - STALE_SECONDS - 30
        for path in folder.iterdir(): os.utime(path, (old, old))
        os.utime(folder, (old, old))
        target._clean_stale()
        self.assertFalse(folder.exists())
        self.assertEqual(target.receive(body)['next'], len(self.image))

    def test_partial_progress_replaces_snapshot_and_ignores_old_revision(self):
        sent, env = self.pending_image()
        source = self.win.mesh.image_relay
        base = {'message_id':sent['id'], 'target_runtime':self.mac.id}
        source.progress(dict(base, rev=2, partial='看到了'))
        self.assertEqual(self.win.get(sent['id'])['partial'], '看到了')
        source.progress(dict(base, rev=1, partial='过期'))
        self.assertEqual(self.win.get(sent['id'])['partial'], '看到了')
        source.progress(dict(base, rev=3, partial=''))
        self.assertEqual(self.win.get(sent['id'])['partial'], '')

    def test_http_bridge_rejects_normal_client_token_and_advertises_capability(self):
        cfg = dataclasses.replace(self.win.xiaoyu._config, host='127.0.0.1', port=0)
        server = make_server(cfg, self.win.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = 'http://127.0.0.1:%s/v1/mesh/image-relays?target=%s' % (server.server_port, self.mac.id)
        request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + cfg.token})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(error.exception.code, 403)
        request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + SECRET})
        self.assertEqual(json.load(urllib.request.urlopen(request, timeout=3)), {'transfers': []})
        self.assertIn('mesh_image_relay_v1', self.win.service.info()['capabilities'])

    def test_remote_image_handoff_waits_for_helper_final_result(self):
        cards = []
        def handoff(text, conversation, turn_id, attachments, **kwargs):
            card = self.mac.xiaoyu._writer.task_open(conversation, text, 'working', 'claude')
            self.mac.xiaoyu._writer.exchange(card['id'], said=text, say='交给帮手', turn_id=turn_id, kind='handoff')
            cards.append(card['id'])
            return Turn('交给帮手', '交给帮手', 'busy', 'claude', card['id'], True)
        with mock.patch.object(self.mac.xiaoyu, 'hear_images', side_effect=handoff):
            sent, env = self.pending_image()
            read = self.win.mesh.image_relay.read({'message_id':sent['id'], 'target_runtime':self.mac.id, 'index':0, 'offset':0})
            self.mac.mesh.image_relay.receive(dict(env, index=0, offset=0, data=read['data']))
            self.mac.mesh.image_relay.finish(env)
            self.assertTrue(until(lambda: bool(cards), 10))
            self.assertEqual(self.mac.get(sent['id'])['status'], 'running')
            self.mac.xiaoyu._writer.reply(cards[0], '最终答案', turn_id='helper-finished')
            self.mac.xiaoyu._writer.task(cards[0], state='done', brief='最终答案', mood='idle')
            self.assertTrue(until(lambda: self.mac.get(sent['id'])['status'] == 'done', 10))
            self.assertEqual(self.mac.get(sent['id'])['reply'], '最终答案')

    def test_source_without_vision_routes_to_capable_remote_owner(self):
        with mock.patch.object(self.win.xiaoyu, 'image_agent', return_value=None), \
             mock.patch.object(self.mac.xiaoyu, 'hear_images', return_value=Turn('红色', '红色', 'idle', 'claude')):
            sent, env = self.pending_image()
            read = self.win.mesh.image_relay.read({'message_id':sent['id'], 'target_runtime':self.mac.id, 'index':0, 'offset':0})
            self.mac.mesh.image_relay.receive(dict(env, index=0, offset=0, data=read['data']))
            self.mac.mesh.image_relay.finish(env)
            self.assertTrue(until(lambda: self.mac.get(sent['id']).get('status') == 'done', 10))

    def test_target_without_vision_returns_durable_failure_instead_of_retrying_forever(self):
        with mock.patch.object(self.mac.xiaoyu, 'image_agent', return_value=None):
            sent, env = self.pending_image()
            read = self.win.mesh.image_relay.read({'message_id':sent['id'], 'target_runtime':self.mac.id, 'index':0, 'offset':0})
            self.mac.mesh.image_relay.receive(dict(env, index=0, offset=0, data=read['data']))
            self.mac.mesh.image_relay.finish(env)
            self.assertTrue(until(lambda: self.mac.get(sent['id']).get('status') == 'failed', 10))
            failure = self.mac.get(sent['id'])
            self.assertIn('看图', failure['error'])
            self.win.mesh.image_relay.ack({'message_id':sent['id'], 'target_runtime':self.mac.id,
                                          'status':'failed', 'error':failure['error']})
            self.assertEqual(self.win.get(sent['id'])['status'], 'failed')

    def test_old_desktop_wait_is_bounded_without_silent_loss(self):
        sent, env = self.pending_image()
        source = self.win.mesh.image_relay
        source.unavailable({'message_id':sent['id'], 'target_runtime':self.mac.id, 'reason':'unsupported'})
        self.assertIn('不支持', self.win.get(sent['id'])['stage'])
        source.expire_unstarted(seconds=0)
        result = self.win.get(sent['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('尚未发出', result['error'])
        self.assertEqual(result['attachments'][0]['sha256'], self.attachment['sha256'])

    def test_transmission_already_started_is_not_automatically_retried_or_failed(self):
        sent, env = self.pending_image()
        source = self.win.mesh.image_relay
        source.read({'message_id':sent['id'], 'target_runtime':self.mac.id, 'index':0, 'offset':0})
        source.expire_unstarted(seconds=0)
        self.assertEqual(self.win.get(sent['id'])['status'], 'running')

    def test_finished_receipt_cleans_leftover_partial_files(self):
        sent, env = self.pending_image()
        source, target = self.win.mesh.image_relay, self.mac.mesh.image_relay
        read = source.read({'message_id':sent['id'], 'target_runtime':self.mac.id, 'index':0, 'offset':0})
        target.receive(dict(env, index=0, offset=0, data=read['data']))
        with mock.patch.object(self.mac.xiaoyu, 'hear_images', return_value=Turn('ok', 'ok', 'idle', 'claude')):
            target.finish(env)
        left = target._folder(env) / '0.part'
        left.write_bytes(self.image)  # crash after receipt, before unlink
        target._clean_stale()
        self.assertFalse(left.exists())
        self.assertTrue((target._folder(env) / 'manifest.json').exists())

    def test_capacity_limits_reject_without_writing_parts(self):
        sent, env = self.pending_image()
        read = self.win.mesh.image_relay.read({'message_id':sent['id'], 'target_runtime':self.mac.id, 'index':0, 'offset':0})
        target = self.mac.mesh.image_relay
        body = dict(env, index=0, offset=0, data=read['data'])
        with mock.patch.object(image_relay, 'MAX_TRANSFERS', 0):
            with self.assertRaisesRegex(ImageRelayError, '队列已满'): target.receive(body)
        with mock.patch.object(image_relay, 'MAX_RECEIPTS', 0):
            with self.assertRaisesRegex(ImageRelayError, '回执已满'): target.receive(body)
        self.assertFalse(target._folder(env).exists())

    def test_large_picture_transfers_multiple_chunks_with_resume(self):
        stream = io.BytesIO()
        Image.frombytes('RGB', (512, 512), os.urandom(512 * 512 * 3)).save(stream, 'PNG')
        self.image = stream.getvalue()
        self.assertGreater(len(self.image), CHUNK * 2)
        self.attachment = upload(self.win.service.artifacts, self.image)
        with mock.patch.object(self.mac.xiaoyu, 'hear_images', return_value=Turn('ok', 'ok', 'idle', 'claude')):
            sent, env = self.pending_image()
            source, target = self.win.mesh.image_relay, self.mac.mesh.image_relay
            offset, count = 0, 0
            while offset < len(self.image):
                block = source.read({'message_id':sent['id'], 'target_runtime':self.mac.id, 'index':0, 'offset':offset})
                request = dict(env, index=0, offset=offset, data=block['data'])
                self.assertEqual(target.receive(request)['next'], block['next'])
                if count == 1:
                    target = ImageRelay(self.mac.mesh)
                    self.assertEqual(target.receive(request)['next'], block['next'])
                offset = block['next']
                count += 1
            self.assertGreaterEqual(count, 3)
            self.assertEqual(target.finish(env)['message_id'], sent['id'])

    def test_symlink_part_is_rejected(self):
        sent, env = self.pending_image()
        source, target = self.win.mesh.image_relay, self.mac.mesh.image_relay
        read = source.read({'message_id':sent['id'], 'target_runtime':self.mac.id, 'index':0, 'offset':0})
        body = dict(env, index=0, offset=0, data=read['data'])
        target.receive(body)
        part = target._folder(env) / '0.part'
        part.unlink()
        victim = self.folder / 'victim'
        victim.write_bytes(b'unchanged')
        try:
            part.symlink_to(victim)
        except OSError:
            self.skipTest('creating symlinks requires platform privileges')
        with self.assertRaises((OSError, ImageRelayError)):
            target.receive(body)
        self.assertEqual(victim.read_bytes(), b'unchanged')

    def test_windows_branch_uses_verified_handle_not_plain_open(self):
        from xiaoyu_runtime import windows_files
        sentinel = object()
        with mock.patch.object(image_relay.os, 'name', 'nt'), \
             mock.patch.object(windows_files, 'open_rw_regular', return_value=sentinel) as opened:
            self.assertIs(image_relay._open_part('fixture', writable=True), sentinel)
        opened.assert_called_once_with('fixture', 8 * 1024 * 1024)
