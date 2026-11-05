"""Bounded WeChat CDN media transport; credentials never enter public records.

The wire format follows Tencent's openclaw-weixin protocol.  Callers must
separately authorize the conversation, persist delivery intent, and classify
an uncertain send; uploading bytes alone does not send a chat message.
"""
import base64
import binascii
import hashlib
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

from .errors import WeixinError, WeixinTransportError

CDN_BASE = 'https://novac2c.cdn.weixin.qq.com/c2c'
MAX_MEDIA_BYTES = 25 * 1024 * 1024


class WeixinMediaError(WeixinError):
    pass


def _trusted_url(value):
    if not isinstance(value, str) or not value or len(value) > 8192:
        raise WeixinMediaError('微信媒体地址不可用')
    try:
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or '').lower()
        valid = (parsed.scheme == 'https' and not parsed.username and not parsed.password
                 and parsed.port in (None, 443) and not parsed.fragment
                 and host.endswith('.weixin.qq.com') and all(ord(ch) >= 32 for ch in value))
    except ValueError:
        valid = False
    if not valid:
        raise WeixinMediaError('微信媒体地址不可用')
    return value


def _aes(data, key, *, decrypt=False):
    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        raise WeixinMediaError('微信图片和文件需要安装加密组件') from None
    if not isinstance(key, bytes) or len(key) != 16:
        raise WeixinMediaError('微信媒体密钥无效')
    try:
        cipher = Cipher(algorithms.AES(key), modes.ECB())
        if decrypt:
            worker = cipher.decryptor()
            plain = worker.update(data) + worker.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            return unpadder.update(plain) + unpadder.finalize()
        padder = padding.PKCS7(128).padder()
        padded = padder.update(data) + padder.finalize()
        worker = cipher.encryptor()
        return worker.update(padded) + worker.finalize()
    except (TypeError, ValueError):
        raise WeixinMediaError('微信媒体加解密失败') from None


def _key(image, media):
    hexkey = image.get('aeskey') if isinstance(image, dict) else None
    try:
        if isinstance(hexkey, str) and re.fullmatch(r'[0-9a-fA-F]{32}', hexkey):
            return bytes.fromhex(hexkey)
        encoded = media.get('aes_key')
        if encoded is None or encoded == '':
            return None
        if not isinstance(encoded, str) or len(encoded) > 128:
            raise ValueError()
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) == 16:
            return raw
        if len(raw) == 32:
            return bytes.fromhex(raw.decode('ascii'))
    except (ValueError, UnicodeError, binascii.Error):
        pass
    raise WeixinMediaError('微信媒体密钥无效')


def _read_response(response, maximum):
    raw_length = response.headers.get('Content-Length')
    if raw_length:
        try:
            if int(raw_length) < 0 or int(raw_length) > maximum:
                raise WeixinMediaError('微信文件超过大小限制')
        except ValueError:
            raise WeixinMediaError('微信媒体大小无效') from None
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise WeixinMediaError('微信文件超过大小限制')
    return data


def download(item, opener):
    """Download one image or file; return bounded bytes and a safe name."""
    if not isinstance(item, dict) or item.get('type') not in (2, 4):
        raise WeixinMediaError('暂不支持这种微信附件')
    image = item.get('image_item') if item['type'] == 2 else None
    details = image if image is not None else item.get('file_item')
    if not isinstance(details, dict) or not isinstance(details.get('media'), dict):
        raise WeixinMediaError('微信附件信息不完整')
    media = details['media']
    key = _key(image, media)
    if item['type'] == 4 and key is None:
        raise WeixinMediaError('微信文件缺少解密信息')
    full = media.get('full_url')
    param = media.get('encrypt_query_param')
    if not full:
        if not isinstance(param, str) or not param or len(param) > 4096:
            raise WeixinMediaError('微信附件信息不完整')
        full = CDN_BASE + '/download?' + urllib.parse.urlencode({'encrypted_query_param': param})
    request = urllib.request.Request(_trusted_url(full), method='GET')
    try:
        with opener.open(request, timeout=30) as response:
            if getattr(response, 'status', 200) != 200:
                raise WeixinMediaError('微信附件下载失败')
            data = _read_response(response, MAX_MEDIA_BYTES + 16)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise WeixinTransportError('微信附件下载失败') from None
    data = _aes(data, key, decrypt=True) if key is not None else data
    if not data or len(data) > MAX_MEDIA_BYTES:
        raise WeixinMediaError('微信文件为空或超过大小限制')
    length = details.get('len')
    if item['type'] == 4 and length is not None:
        if not isinstance(length, str) or not length.isdecimal() or int(length) != len(data):
            raise WeixinMediaError('微信文件大小不匹配')
    name = details.get('file_name') if item['type'] == 4 else '微信图片'
    if not isinstance(name, str) or not name or len(name) > 255 or any(ch in name for ch in '/\\\x00'):
        raise WeixinMediaError('微信附件名称无效')
    return data, name


def upload(client, recipient, data, name, mime):
    """Upload ciphertext and return a single image/file MessageItem."""
    if (not isinstance(data, bytes) or not data or len(data) > MAX_MEDIA_BYTES
            or not isinstance(name, str) or not name or len(name) > 255
            or any(ch in name for ch in '/\\\x00') or not isinstance(recipient, str) or not recipient):
        raise WeixinMediaError('微信附件无效或超过大小限制')
    is_image = isinstance(mime, str) and mime.startswith('image/')
    key, filekey = secrets.token_bytes(16), secrets.token_hex(16)
    cipher = _aes(data, key)
    response = client._request('/ilink/bot/getuploadurl', {
        'filekey': filekey, 'media_type': 1 if is_image else 3,
        'to_user_id': recipient, 'rawsize': len(data), 'rawfilemd5': hashlib.md5(data).hexdigest(),
        'filesize': len(cipher), 'no_need_thumb': True, 'aeskey': key.hex(),
        'base_info': {'channel_version': '2.4.8', 'bot_agent': 'Xiaoyou/1.0'},
    })
    url = response.get('upload_full_url')
    if not url:
        param = response.get('upload_param')
        if not isinstance(param, str) or not param or len(param) > 4096:
            raise WeixinMediaError('微信附件上传地址不可用')
        url = CDN_BASE + '/upload?' + urllib.parse.urlencode({'encrypted_query_param': param, 'filekey': filekey})
    request = urllib.request.Request(_trusted_url(url), data=cipher, method='POST',
                                     headers={'Content-Type': 'application/octet-stream'})
    try:
        with client.opener.open(request, timeout=60) as result:
            if getattr(result, 'status', 200) != 200:
                raise WeixinMediaError('微信附件上传失败')
            reference = result.headers.get('x-encrypted-param')
    except (urllib.error.URLError, TimeoutError, OSError):
        raise WeixinTransportError('微信附件上传失败') from None
    if not isinstance(reference, str) or not reference or len(reference) > 4096:
        raise WeixinMediaError('微信附件上传结果不可用')
    media = {'encrypt_query_param': reference,
             'aes_key': base64.b64encode(key.hex().encode('ascii')).decode('ascii'), 'encrypt_type': 1}
    return ({'type': 2, 'image_item': {'media': media, 'mid_size': len(cipher)}} if is_image
            else {'type': 4, 'file_item': {'media': media, 'file_name': name, 'len': str(len(data))}})
