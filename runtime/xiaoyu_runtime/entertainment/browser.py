"""Talk to the local isolated browser without handling its login credentials."""

import base64
import io
import json
import os
import socket
import urllib.error
from pathlib import Path

from .network import Unavailable

SOCKET = Path.home() / '.xiaoyu' / 'browser' / 'bridge.sock'
MAX_REPLY = 6 * 1024 * 1024


def _call(request):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(12)
            connection.connect(str(SOCKET))
            connection.sendall(json.dumps(request, separators=(',', ':')).encode() + b'\n')
            data = bytearray()
            while not data.endswith(b'\n'):
                chunk = connection.recv(min(65536, MAX_REPLY + 1 - len(data)))
                if not chunk or len(data) + len(chunk) > MAX_REPLY:
                    raise ValueError('invalid reply')
                data.extend(chunk)
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError('invalid reply')
            return result
    except (OSError, ValueError, TypeError, AttributeError):
        raise Unavailable('小鱼的独立浏览器未连接，请在桌面端打开小鱼') from None


def logged_in():
    try:
        return _call({'op': 'status'}).get('logged_in') is True
    except Unavailable:
        return False


class Response(io.BytesIO):
    def __init__(self, body, headers):
        super().__init__(body)
        self.headers = headers


def open_session(url):
    response = _call({'op': 'get', 'url': url})
    error = response.get('error')
    if error:
        if error == 'login_required':
            raise Unavailable('请先在小鱼的独立浏览器登录小鱼自己的 B 站账号')
        raise Unavailable('独立浏览器未能读取该内容')
    try:
        status = response['status']
        if not isinstance(status, int) or isinstance(status, bool):
            raise ValueError('invalid status')
        body = base64.b64decode(response.get('body', ''), validate=True)
        location = response.get('location', '')
        if not isinstance(location, str) or len(location) > 4096:
            raise ValueError('invalid location')
        headers = {'Location': location}
        if not 200 <= status < 300:
            raise urllib.error.HTTPError(url, status, 'platform request failed', headers, io.BytesIO(body))
        return Response(body, headers)
    except (KeyError, TypeError, ValueError):
        raise Unavailable('独立浏览器返回的数据无效') from None
