import dataclasses
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from test_runtime import Scripted, make_xiaoyu
from xiaoyu_runtime.api.server import make_server
from xiaoyu_runtime.api.service import Service


class BindingHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        xiaoyu, store = make_xiaoyu(Path(self.tmp.name), [Scripted('voice')])
        self.service = Service(xiaoyu, store)
        self.addCleanup(self.service.close)
        self.session = mock.Mock()
        self.session.begin.return_value = {'id': 'private-attempt', 'qr_content': 'private-qr'}
        self.session.poll.return_value = {'state': 'waiting'}
        self.session.cancel.return_value = True
        self.session.status.return_value = {'state': 'unbound', 'local': False, 'reason': None}
        self.config = dataclasses.replace(xiaoyu.config, host='127.0.0.1', port=0)
        self.server = make_server(self.config, self.service, channel_session=self.session)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = 'http://127.0.0.1:%d' % self.server.server_port

    def request(self, path, body=None, token=None, headers=None):
        headers = dict(headers or {})
        if token is not None:
            headers['Authorization'] = 'Bearer ' + token
        if body is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.url + path, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.load(response)

    def test_only_direct_local_authorized_desktop_can_bind(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/v1/weixin/bind', {})
        self.assertEqual(error.exception.code, 401)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/v1/weixin/bind', {}, self.config.token,
                         {'X-Forwarded-For': '192.0.2.1'})
        self.assertEqual(error.exception.code, 403)
        with mock.patch.object(self.service.devices, 'verify', return_value={'id': 'phone', 'kind': 'phone'}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/v1/weixin/bind', {}, 'dev.phone.secret')
            self.assertEqual(error.exception.code, 403)
        self.session.begin.assert_not_called()
        with mock.patch.object(self.service.devices, 'verify', return_value={'id': 'mac', 'kind': 'desktop'}):
            self.assertEqual(self.request('/v1/weixin/bind', {}, 'dev.mac.secret')['id'], 'private-attempt')
        result = self.request('/v1/weixin/bind', {}, self.config.token)
        self.assertEqual(result['id'], 'private-attempt')
        self.assertEqual(self.request('/v1/weixin', token=self.config.token)['state'], 'unbound')
        self.assertIn('weixin_bind_v1', self.request('/v1/info', token=self.config.token)['capabilities'])
        self.assertEqual(self.request('/v1/weixin/bind/private-attempt', token=self.config.token),
                         {'state': 'waiting'})
        self.assertEqual(self.request('/v1/weixin/bind/private-attempt/code', {'code': '123'},
                                      self.config.token), {'state': 'waiting'})
        self.assertEqual(self.request('/v1/weixin/bind/private-attempt/cancel', {},
                                      self.config.token), {'state': 'cancelled'})
        self.session.poll.assert_any_call('private-attempt', '123')

    def test_sensitive_network_error_is_not_returned(self):
        self.session.begin.side_effect = ValueError('private-qr-or-token')
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/v1/weixin/bind', {}, self.config.token)
        self.assertEqual(error.exception.code, 409)
        self.assertNotIn('private-qr-or-token', error.exception.read().decode())

    def test_sync_channel_status_is_explicit_opt_in_and_never_copies_credentials(self):
        self.service._channel_session = self.session
        self.session.public_status.return_value = {'state': 'connected', 'credential': 'must-not-leak'}
        old = self.request('/v1/sync?conversation=default&after=0&wait=0', token=self.config.token)
        self.assertNotIn('weixin', old)
        page = self.request('/v1/sync?conversation=default&after=0&wait=0&states=unavailable,weixin', token=self.config.token)
        self.assertEqual({'state': 'connected'}, page['weixin'])
        self.assertNotIn('must-not-leak', json.dumps(page))
        self.session.public_status.return_value = {'state': 'unknown', 'credential': 'must-not-leak'}
        page = self.request('/v1/sync?conversation=default&after=0&wait=0&states=weixin', token=self.config.token)
        self.assertEqual({'state': 'unknown'}, page['weixin'])

    def test_resend_requires_owner_host_and_explicit_valid_operation(self):
        host = self.service.runtime_id
        mesh = mock.Mock()
        mesh.owner_of.return_value = {'owner': host, 'unresolved': False, 'conflict': False}
        self.service.mesh = mesh
        self.service._channel_session = self.session
        self.session.retry_delivery.return_value = 'confirm_required'
        body = {'block': host + '/reply#0', 'operation': 'a' * 32, 'revision': 3, 'confirm': False}
        with mock.patch.object(self.service, 'permission_problem', return_value=None):
            self.assertEqual('confirm_required', self.request('/v1/weixin/resend', body, self.config.token)['status'])
            self.session.retry_delivery.assert_called_once_with(body['block'], body['operation'], 3, confirm_unknown=False)
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/v1/weixin/resend', dict(body, confirm='yes'), self.config.token)
            self.assertEqual(400, error.exception.code)
            other = 'b' * 32
            mesh.online.return_value = True
            mesh.command.return_value = 'command-id'
            remote = self.request('/v1/weixin/resend', dict(body, block=other + '/reply#0'), self.config.token)
            self.assertEqual('queued', remote['status'])
            mesh.command.assert_called_once_with(other, 'weixin_resend',
                {'block': other + '/reply#0', 'operation': 'a' * 32, 'revision': 3, 'confirm': False})
        self.service.mesh = None
