import base64
import io
import importlib.util
import unittest
from unittest import mock

from xiaoyu_runtime.channels.media import WeixinMediaError, _aes, download
from xiaoyu_runtime.channels.weixin import WeixinClient, WeixinTransportError


class Response:
    status = 200
    def __init__(self, data=b'', headers=None):
        self.data, self.headers = data, headers or {}
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self, maximum=-1): return self.data[:maximum] if maximum >= 0 else self.data


class Opener:
    def __init__(self):
        self.requests, self.cipher = [], b''
    def open(self, request, timeout):
        self.requests.append(request)
        if request.get_method() == 'POST':
            self.cipher = request.data
            return Response(headers={'x-encrypted-param': 'private-download-ref'})
        return Response(self.cipher, {'Content-Length': str(len(self.cipher))})


@unittest.skipUnless(importlib.util.find_spec('cryptography'), 'install runtime/requirements-weixin.txt to test encrypted WeChat media')
class MediaTests(unittest.TestCase):
    def roundtrip(self, mime, name):
        opener = Opener()
        client = WeixinClient(token='private-token', opener=opener)
        data = b'example media bytes\x00' * 17
        requests = []
        def api(path, payload=None, **kwargs):
            requests.append((path, payload))
            if path.endswith('/getuploadurl'):
                return {'upload_param': 'private-upload-ref'}
            return {'ret': 0}
        with mock.patch.object(client, '_request', side_effect=api):
            self.assertEqual({'ret': 0}, client.send_media('owner', 'context', 'client', data, name, mime))
        self.assertEqual(['/ilink/bot/getuploadurl', '/ilink/bot/sendmessage'], [x[0] for x in requests])
        upload = requests[0][1]
        self.assertEqual(len(data), upload['rawsize'])
        self.assertEqual(0, upload['filesize'] % 16)
        self.assertNotIn(data, opener.cipher)
        self.assertEqual('POST', opener.requests[0].get_method())
        self.assertIsNone(opener.requests[0].get_header('Authorization'))
        item = requests[1][1]['msg']['item_list'][0]
        result, restored_name = client.download_media(item)
        self.assertEqual(data, result)
        self.assertEqual(name if item['type'] == 4 else '微信图片', restored_name)
        self.assertEqual('GET', opener.requests[1].get_method())
        self.assertIsNone(opener.requests[1].get_header('Authorization'))
        return item, opener

    def test_encrypted_image_roundtrip(self):
        item, _ = self.roundtrip('image/png', 'image.png')
        self.assertEqual(2, item['type'])

    def test_encrypted_file_roundtrip(self):
        item, _ = self.roundtrip('application/pdf', 'report.pdf')
        self.assertEqual(4, item['type'])

    def test_raw_and_hex_key_encodings(self):
        opener = Opener()
        key = bytes(range(16))
        opener.cipher = _aes(b'content', key)
        for raw in (key, key.hex().encode('ascii')):
            item = {'type': 4, 'file_item': {'file_name': 'a.txt', 'len': '7',
                    'media': {'encrypt_query_param': 'ref', 'aes_key': base64.b64encode(raw).decode()}}}
            self.assertEqual((b'content', 'a.txt'), download(item, opener))

    def test_untrusted_urls_bad_names_and_size_mismatch_fail_closed(self):
        opener = Opener()
        item = {'type': 2, 'image_item': {'media': {'full_url': 'https://evil.example/private'}}}
        with self.assertRaises(WeixinMediaError): download(item, opener)
        self.assertEqual([], opener.requests)
        item = {'type': 4, 'file_item': {'file_name': '../secret', 'len': '1',
                'media': {'full_url': 'https://novac2c.cdn.weixin.qq.com/c2c/download',
                          'aes_key': base64.b64encode(b'0' * 16).decode()}}}
        opener.cipher = _aes(b'wrong size', b'0' * 16)
        with self.assertRaises(WeixinMediaError): download(item, opener)

    def test_media_failure_never_exposes_upstream_url_or_secret(self):
        client = WeixinClient(token='secret-token', opener=Opener())
        with mock.patch.object(client, '_request', return_value={'upload_full_url': 'https://evil.example/secret-token'}):
            with self.assertRaises(WeixinMediaError) as caught:
                client.send_media('owner', 'context', 'client', b'file', 'a.txt', 'text/plain')
        self.assertNotIn('secret-token', str(caught.exception))
