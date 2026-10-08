"""HTTP 接口：手机 App（或任何客户端）通过它和小幽说话。

  GET  /healthz                         不需要令牌，只说明服务活着
  POST /v1/messages                     {"text", "conversation"?, "client_id"?} → 202 + 消息
  GET  /v1/messages/<id>?wait=<秒>      查结果；wait 最多 60 秒，处理完会提前返回
  POST /v1/conversations/<名字>/reset   让这个对话从头开始

除 /healthz 外都要带 Authorization: Bearer <令牌>。
"""

import hmac
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Tuple
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .config import Config
from .service import RequestError, Service

MAX_BODY_BYTES = 64 * 1024
MAX_WAIT_SECONDS = 60.0


def make_server(config: Config, service: Service) -> ThreadingHTTPServer:
    expected = ("Bearer " + config.token).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "xiaoyou-runtime/" + __version__
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

        def _send(self, status: int, body: Dict[str, Any]) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _fail(self, status: int, message: str) -> None:
            # 出错后不再复用这条连接：请求体可能还没读完。
            self.close_connection = True
            self._send(status, {"error": message})

        def _authorized(self) -> bool:
            given = self.headers.get("Authorization", "").encode("utf-8")
            if hmac.compare_digest(given, expected):
                return True
            self._fail(401, "令牌不对或者没带令牌")
            return False

        def _body(self) -> Tuple[bool, Any]:
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._fail(411, "需要 Content-Length")
                return False, None
            if length < 0 or length > MAX_BODY_BYTES:
                self._fail(413, "请求体太大")
                return False, None
            try:
                return True, json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._fail(400, "请求体不是合法的 JSON")
                return False, None

        def do_GET(self) -> None:
            url = urlsplit(self.path)
            parts = [part for part in url.path.split("/") if part]
            if parts == ["healthz"]:
                self._send(200, {"ok": True, "version": __version__, "backend": config.backend})
                return
            if not self._authorized():
                return
            if len(parts) == 3 and parts[:2] == ["v1", "messages"]:
                try:
                    wait = float(parse_qs(url.query).get("wait", ["0"])[0])
                except ValueError:
                    self._fail(400, "wait 应该是秒数")
                    return
                if not 0 <= wait <= MAX_WAIT_SECONDS:  # 同时挡掉 NaN
                    self._fail(400, "wait 应该在 0 到 %d 之间" % MAX_WAIT_SECONDS)
                    return
                message = service.get(parts[2], wait)
                if message is None:
                    self._fail(404, "没有这条消息（Runtime 重启后旧消息查不到）")
                else:
                    self._send(200, message)
                return
            self._fail(404, "没有这个地址")

        def do_POST(self) -> None:
            parts = [part for part in urlsplit(self.path).path.split("/") if part]
            if not self._authorized():
                return
            ok, body = self._body()
            if not ok:
                return
            try:
                if parts == ["v1", "messages"]:
                    if not isinstance(body, dict):
                        raise RequestError("请求体应该是一个对象")
                    message = service.submit(
                        body.get("text"), body.get("conversation", "default"), body.get("client_id")
                    )
                    self._send(202, message)
                    return
                if len(parts) == 4 and parts[:2] == ["v1", "conversations"] and parts[3] == "reset":
                    self._send(200, {"reset": service.reset(parts[2])})
                    return
            except RequestError as error:
                self._fail(400, str(error))
                return
            self._fail(404, "没有这个地址")

    server = ThreadingHTTPServer((config.host, config.port), Handler)
    server.daemon_threads = True
    return server
