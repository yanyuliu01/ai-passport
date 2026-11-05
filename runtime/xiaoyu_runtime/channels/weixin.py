"""Minimal iLink text transport; callers own credentials and delivery policy.

Based on Tencent's openclaw-weixin protocol reference. Server guarantees such
as send idempotency and proactive delivery require separate live validation.
"""
import base64
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from .errors import WeixinError, WeixinTransportError, WeixinPausedError

BASE = 'https://ilinkai.weixin.qq.com'
VERSION = '2.4.8'
CLIENT_VERSION = str((2 << 16) | (4 << 8) | 8)


def _base(url):
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or '').lower()
    if (parsed.scheme != 'https' or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment
            or not (host == 'weixin.qq.com' or host.endswith('.weixin.qq.com'))):
        raise ValueError('untrusted Weixin endpoint')
    return 'https://' + host


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WeixinClient:
    def __init__(self, token='', base=BASE, opener=None):
        self.token = token
        self.base = _base(base)
        self.opener = opener or urllib.request.build_opener(_NoRedirect())

    def _request(self, path, payload=None, timeout=15, authenticated=True):
        headers = {'iLink-App-Id': 'bot', 'iLink-App-ClientVersion': CLIENT_VERSION}
        body = None
        if payload is not None:
            headers.update({'Content-Type': 'application/json', 'AuthorizationType': 'ilink_bot_token',
                            'X-WECHAT-UIN': base64.b64encode(str(secrets.randbits(32)).encode()).decode()})
            if authenticated and self.token:
                headers['Authorization'] = 'Bearer ' + self.token
            body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
        request = urllib.request.Request(self.base + path, data=body, headers=headers)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                data = json.load(response)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            # URLError and HTTPError can include QR values, query parameters,
            # server-provided URLs or other credentials in their repr.
            raise WeixinTransportError('微信连接失败，结果可能尚未确认') from None
        if not isinstance(data, dict):
            raise WeixinError('invalid channel response')
        # Do not include the remote error text, which may contain secrets.
        if data.get('ret') == -14 or data.get('errcode') == -14:
            raise WeixinPausedError('微信暂时要求暂停连接')
        if data.get('ret', 0) not in (0, None) or data.get('errcode', 0) not in (0, None):
            raise WeixinError('channel returned an error')
        return data

    def qr(self):
        return self._request('/ilink/bot/get_bot_qrcode?bot_type=3', {'local_token_list': []}, authenticated=False)

    def qr_status(self, qr, verify_code=None):
        params = {'qrcode': qr}
        if verify_code:
            params['verify_code'] = verify_code
        return self._request('/ilink/bot/get_qrcode_status?' + urllib.parse.urlencode(params), timeout=40)

    def for_base(self, base):
        """A redirect must be explicitly validated; HTTP redirects stay disabled."""
        return WeixinClient(token=self.token, base=base, opener=self.opener)

    def updates(self, cursor):
        if not self.token:
            raise ValueError('channel token required')
        return self._request('/ilink/bot/getupdates', {'get_updates_buf': cursor,
                             'base_info': {'channel_version': VERSION, 'bot_agent': 'Xiaoyou/1.0'}}, timeout=45)

    def send_text(self, user_id, context_token, client_id, text):
        if not all((self.token, user_id, context_token, client_id, text)):
            raise ValueError('token, destination, reply context, identity and text required')
        return self._request('/ilink/bot/sendmessage', {'msg': {
            'from_user_id': '', 'to_user_id': user_id, 'client_id': client_id,
            'message_type': 2, 'message_state': 2, 'context_token': context_token,
            'item_list': [{'type': 1, 'text_item': {'text': text}}]},
            'base_info': {'channel_version': VERSION, 'bot_agent': 'Xiaoyou/1.0'}})

    def send_media(self, user_id, context_token, client_id, data, name, mime):
        if not all((self.token, user_id, context_token, client_id)):
            raise ValueError('token, destination, reply context and identity required')
        from .media import upload
        item = upload(self, user_id, data, name, mime)
        return self._request('/ilink/bot/sendmessage', {'msg': {
            'from_user_id': '', 'to_user_id': user_id, 'client_id': client_id,
            'message_type': 2, 'message_state': 2, 'context_token': context_token,
            'item_list': [item]},
            'base_info': {'channel_version': VERSION, 'bot_agent': 'Xiaoyou/1.0'}})

    def download_media(self, item):
        from .media import download
        return download(item, self.opener)
