"""Bounded anonymous transport shared by optional content adapters."""

import json
import re
import urllib.error
import urllib.request

MAX_RESPONSE = 512 * 1024
TIMEOUT = 8
BV = re.compile(r"(?<![A-Za-z0-9])(BV[0-9A-Za-z]{10})(?![A-Za-z0-9])")


class Unavailable(Exception):
    def __init__(self, message, restricted=False):
        super().__init__(message)
        self.restricted = restricted


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_anonymous(url):
    opener = urllib.request.build_opener(NoRedirect())
    req = urllib.request.Request(url, headers={"User-Agent": "xiaoyu-entertainment/0.1", "Accept": "application/json"})
    return opener.open(req, timeout=TIMEOUT)


def get_json(url, opener=open_anonymous):
    try:
        with opener(url) as response:
            payload = response.read(MAX_RESPONSE + 1)
        if len(payload) > MAX_RESPONSE:
            raise Unavailable("平台返回内容过大")
        result = json.loads(payload.decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code in (403, 412, 429):
            raise Unavailable("平台限制访问（HTTP %s），已停止请求" % error.code, restricted=True) from None
        raise Unavailable("平台访问失败（HTTP %s）" % error.code) from None
    except (urllib.error.URLError, OSError, TimeoutError, UnicodeError, ValueError):
        raise Unavailable("平台暂时不可用或返回格式异常") from None
    if not isinstance(result, dict) or result.get("code") != 0 or not isinstance(result.get("data"), dict):
        code = result.get("code") if isinstance(result, dict) else None
        raise Unavailable("平台未提供可用内容，可能需要登录或已限制访问", restricted=code in (-412, -352, -509))
    return result["data"]
