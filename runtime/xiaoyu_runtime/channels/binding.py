"""One in-memory QR login attempt; exposing it requires local authorization.

The caller is responsible for a single channel host and durable credential
storage before starting the channel. Neither QR contents nor credentials belong
in sync, logs or status snapshots.
"""
import secrets
import threading
import re

from .weixin import WeixinClient, _base


class Binding:
    def __init__(self, client=None, expected_owner=None):
        self._client = client or WeixinClient()
        if expected_owner is not None and (not isinstance(expected_owner, str) or not expected_owner):
            raise ValueError('invalid expected account')
        self._expected_owner = expected_owner
        self._lock = threading.RLock()
        # A confirmation can be a one-time response. Concurrent polls must
        # not overtake and discard it merely because a later poll returned.
        self._poll_lock = threading.Lock()
        self._attempt = None

    def start(self):
        # Invalidate the old attempt before network I/O: a late poll cannot
        # commit credentials for a replaced login.
        attempt = {'id': secrets.token_urlsafe(24), 'state': 'starting', 'client': self._client}
        with self._lock:
            self._attempt = attempt
        data = self._client.qr()
        qr, image = data.get('qrcode'), data.get('qrcode_img_content')
        if not isinstance(qr, str) or not qr or not isinstance(image, str) or not image:
            raise ValueError('微信没有返回有效二维码')
        with self._lock:
            if self._attempt is not attempt:
                raise ValueError('扫码请求已取消')
            attempt.update(state='waiting', qr=qr, image=image)
            return {'id': attempt['id'], 'qr_content': image}

    def cancel(self, attempt_id):
        with self._lock:
            if self._attempt and self._attempt['id'] == attempt_id:
                self._attempt = None
                return True
            return False

    def poll(self, attempt_id, verify_code=None):
        with self._poll_lock:
            return self._poll(attempt_id, verify_code)

    def _poll(self, attempt_id, verify_code=None):
        if verify_code is not None and (not isinstance(verify_code, str)
                                        or not re.fullmatch(r'[0-9]{1,16}', verify_code)):
            raise ValueError('验证码格式无效')
        with self._lock:
            attempt = self._attempt
            if not attempt or attempt['id'] != attempt_id or 'qr' not in attempt:
                raise ValueError('扫码请求已失效')
            if attempt['state'] in ('confirmed', 'expired', 'failed', 'wrong_account'):
                return {'state': attempt['state']}
            qr = attempt['qr']
            client = attempt['client']
            attempt['poll_generation'] = attempt.get('poll_generation', 0) + 1
            generation = attempt['poll_generation']
        data = client.qr_status(qr, verify_code)
        state = data.get('status')
        states = {'wait': 'waiting', 'scaned': 'scanned', 'need_verifycode': 'need_code',
                  'expired': 'expired', 'verify_code_blocked': 'failed'}
        with self._lock:
            if self._attempt is not attempt:
                raise ValueError('扫码请求已取消')
            if generation != attempt['poll_generation'] or attempt['state'] in ('confirmed', 'expired', 'failed', 'wrong_account'):
                return {'state': attempt['state']}
            if state == 'confirmed':
                token, bot, owner = data.get('bot_token'), data.get('ilink_bot_id'), data.get('ilink_user_id')
                if not all(isinstance(x, str) and x for x in (token, bot, owner)):
                    attempt['state'] = 'failed'
                elif self._expected_owner is not None and owner != self._expected_owner:
                    attempt['state'] = 'wrong_account'
                else:
                    # Reject untrusted base URLs before any token can be used.
                    base = _base(data.get('baseurl') or client.base)
                    attempt.update(state='confirmed', credentials={'token': token, 'bot': bot,
                                                                  'owner': owner, 'base': base})
            elif state in states:
                attempt['state'] = states[state]
            elif state == 'scaned_but_redirect':
                host = data.get('redirect_host')
                if host:
                    if not isinstance(host, str):
                        raise ValueError('invalid channel redirect')
                    attempt['client'] = client.for_base(_base('https://' + host))
                attempt['state'] = 'scanned'
            else:
                # Redirect, already-bound and undocumented server states need
                # explicit support; do not report a successful binding.
                attempt['state'] = 'failed'
            return {'state': attempt['state']}

    def persist_credentials(self, attempt_id, save):
        """Consume credentials only after an authorized durable save succeeds."""
        with self._lock:
            attempt = self._attempt
            if not attempt or attempt['id'] != attempt_id or attempt['state'] != 'confirmed':
                raise ValueError('扫码尚未完成')
            credentials = attempt.get('credentials')
            if credentials is None:
                raise ValueError('扫码凭据已取用')
            save(dict(credentials))
            del attempt['credentials']
