import io
import json
import unittest

from xiaoyu_runtime.channels.weixin import WeixinClient, WeixinError


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Opener:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        return Response(json.dumps(self.data).encode())


class WeixinTransportTests(unittest.TestCase):
    def test_qr_and_status_do_not_send_credentials(self):
        opener = Opener({'status': 'wait'})
        client = WeixinClient(token='secret', opener=opener)
        client.qr_status('a&b')
        req, timeout = opener.calls[-1]
        self.assertIn('qrcode=a%26b', req.full_url)
        self.assertIsNone(req.get_header('Authorization'))
        self.assertEqual(timeout, 15)
        client.qr()
        self.assertIsNone(opener.calls[-1][0].get_header('Authorization'))

    def test_send_preserves_context_and_id(self):
        opener = Opener({'ret': 0})
        client = WeixinClient(token='secret', opener=opener)
        client.send_text('peer', 'context', 'stable-id', 'hello')
        req, _ = opener.calls[-1]
        body = json.loads(req.data)
        self.assertEqual(body['msg']['context_token'], 'context')
        self.assertEqual(body['msg']['client_id'], 'stable-id')
        self.assertEqual(req.get_header('Authorization'), 'Bearer secret')
        with self.assertRaises(ValueError):
            client.send_text('peer', '', 'id', 'hello')

    def test_bad_endpoint_and_business_error_fail_closed(self):
        for endpoint in ('http://ilinkai.weixin.qq.com', 'https://weixin.qq.com.evil.test',
                         'https://evil.test', 'https://a.weixin.qq.com@evil.test'):
            with self.assertRaises(ValueError):
                WeixinClient(base=endpoint)
        client = WeixinClient(token='secret', opener=Opener({'ret': -14, 'errmsg': 'secret'}))
        with self.assertRaises(WeixinError) as error:
            client.updates('')
        self.assertNotIn('secret', str(error.exception))
