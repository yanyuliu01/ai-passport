import http.server
import ssl
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from xiaoyu_runtime.helpers import http_pool


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        with self.server.guard:
            self.server.requests.append((self.path, self.client_address[1], "GET"))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        with self.server.guard:
            self.server.requests.append((self.path, self.client_address[1], "POST"))
        if self.path == "/fail":
            self.send_response(503)
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(b"fail")
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "https://elsewhere.invalid/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/redirect-same":
            self.send_response(302)
            self.send_header("Location", "/after")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path in ("/hang", "/hang-close"):
            self.send_response(200)
            self.send_header("Content-Length", "10000")
            if self.path == "/hang-close":
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"data: hello\n")
            self.wfile.flush()
            self.server.release.wait(2)
            return
        if self.path == "/slow-headers":
            time.sleep(.2)
        body = (b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hello"}}\n\n'
                b'data: {"type":"message_stop"}\n\n') if self.path == "/v1/messages" else b'data: {"type":"message_stop"}\n\n'
        self.send_response(200)
        if self.path in ("/chunked", "/v1/messages"):
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(("%x\r\n" % len(body)).encode() + body + b"\r\n0\r\n\r\n")
            return
        self.send_header("Content-Length", str(len(body)))
        if self.path == "/close":
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


class PoolTest(unittest.TestCase):
    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.guard = threading.Lock()
        self.server.requests = []
        self.server.release = threading.Event()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.pool = http_pool.ConnectionPool()

    def tearDown(self):
        self.server.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        for entries in self.pool._idle.values():
            for _, connection in entries:
                connection.close()

    def request(self, path="/"):
        return urllib.request.Request("http://127.0.0.1:%s%s" % (self.server.server_port, path),
                                      data=b"{}", headers={"X-Api-Key": "test-only"}, method="POST")

    def open(self, path="/"):
        return self.pool.open(self.request(path), timeout=2)

    def test_reuses_complete_stream_and_ignores_late_old_cancel(self):
        first = self.open()
        with first:
            self.assertIn(b"message_stop", next(first))
        second = self.open()
        first.cancel()  # A cancelled timer from turn one must not kill turn two.
        with second:
            self.assertIn(b"message_stop", next(second))
        self.assertEqual(self.server.requests[0][1], self.server.requests[1][1])

    def test_incomplete_body_is_discarded_and_another_request_can_proceed(self):
        first = self.open("/hang")
        with first:
            self.assertEqual(b"data: hello\n", next(first))
        with self.open() as second:
            self.assertIn(b"message_stop", next(second))
        self.assertNotEqual(self.server.requests[0][1], self.server.requests[1][1])

    def test_two_simultaneous_streams_never_share_connection(self):
        first = self.open("/hang")
        self.assertEqual(b"data: hello\n", next(first))
        with self.open() as second:
            self.assertIn(b"message_stop", next(second))
        first.cancel()
        first.close()
        self.assertNotEqual(self.server.requests[0][1], self.server.requests[1][1])

    def test_cancel_wakes_blocked_reader(self):
        for path in ("/hang", "/hang-close"):
            with self.subTest(path=path):
                response = self.open(path)
                self.assertEqual(b"data: hello\n", next(response))
                completed = threading.Event()
                def read():
                    try:
                        next(response)
                    except (StopIteration, OSError, urllib.error.URLError):
                        pass
                    finally:
                        completed.set()
                reader = threading.Thread(target=read, daemon=True)
                reader.start()
                time.sleep(.03)
                response.cancel()
                self.assertTrue(completed.wait(1))
                response.close()

    def test_error_and_redirect_do_not_repeat_post_or_leak_credentials(self):
        for path, code in (("/fail", 503), ("/redirect", 302)):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.open(path)
            self.assertEqual(code, raised.exception.code)
            raised.exception.read()
        self.assertEqual([p for p, _, _ in self.server.requests], ["/fail", "/redirect"])

    def test_same_origin_redirect_uses_urllib_get_without_repeating_post(self):
        with self.open("/redirect-same") as response:
            self.assertEqual(response.read(), b"ok")
        self.assertEqual([(p, m) for p, _, m in self.server.requests],
                         [("/redirect-same", "POST"), ("/after", "GET")])

    def test_chunked_sse_trailer_is_drained_for_reuse(self):
        with self.open("/chunked") as first:
            self.assertIn(b"message_stop", next(first))
        with self.open("/chunked") as second:
            self.assertIn(b"message_stop", next(second))
        self.assertEqual(self.server.requests[0][1], self.server.requests[1][1])

    def test_server_close_is_never_reused(self):
        with self.open("/close") as first:
            self.assertIn(b"message_stop", next(first))
        with self.open() as second:
            self.assertIn(b"message_stop", next(second))
        self.assertNotEqual(self.server.requests[0][1], self.server.requests[1][1])

    def test_custom_hosts_and_proxy_routes_keep_urllib(self):
        request = urllib.request.Request("https://api.deepseek.com/anthropic/v1/messages", data=b"x")
        with patch.object(urllib.request, "getproxies", return_value={}), \
                patch.object(urllib.request, "proxy_bypass", return_value=False):
            self.assertTrue(http_pool.direct_official(request))
            self.assertFalse(http_pool.direct_official(urllib.request.Request("https://example.com/", data=b"x")))
            self.assertFalse(http_pool.direct_official(urllib.request.Request("http://api.deepseek.com/", data=b"x")))
        with patch.object(urllib.request, "getproxies", return_value={"https": "http://proxy.invalid"}), \
                patch.object(urllib.request, "proxy_bypass", return_value=False):
            self.assertFalse(http_pool.direct_official(request))

    def test_runtime_sse_path_reuses_the_same_connection(self):
        from xiaoyu_runtime.helpers import dsh_stream
        from xiaoyu_runtime.helpers.base import Job
        from runtime.tests.test_streaming import dsh_spec
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(dsh_stream, "direct_official", return_value=True), \
                patch.object(dsh_stream, "open_direct", self.pool.open):
            spec = dsh_spec(Path(directory))
            env = {"DEEPSEEK_API_KEY": "test", "DEEPSEEK_BASE_URL":
                   "http://127.0.0.1:%s" % self.server.server_port}
            first = dsh_stream.run(spec, Job(text="one", plain=True), env, "test", lambda _: None)
            second = dsh_stream.run(spec, Job(text="two", plain=True), env, "test", lambda _: None)
        self.assertEqual(first.text, "hello")
        self.assertEqual(second.text, "hello")
        self.assertEqual(self.server.requests[0][1], self.server.requests[1][1])

    def test_new_https_connection_uses_system_ca_and_hostname_validation(self):
        class DummyConnection:
            sock = None
            def connect(self):
                raise OSError("offline fixture")
            def close(self):
                pass
        with patch.object(http_pool.http.client, "HTTPSConnection", return_value=DummyConnection()) as constructor:
            with self.assertRaises(urllib.error.URLError):
                self.pool.open(urllib.request.Request("https://api.deepseek.com/", data=b"x"), 1)
        context = constructor.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_broken_reused_connection_does_not_replay_post(self):
        class BrokenConnection:
            timeout = 0
            class Sock:
                def settimeout(self, value):
                    pass
            sock = Sock()
            attempts = 0
            closed = False
            def request(self, *args, **kwargs):
                self.attempts += 1
                raise BrokenPipeError("peer closed")
            def close(self):
                self.closed = True
        broken = BrokenConnection()
        with patch.object(self.pool, "_take", return_value=broken):
            with self.assertRaises(urllib.error.URLError):
                self.open()
        self.assertEqual(broken.attempts, 1)
        self.assertTrue(broken.closed)
        self.assertEqual(self.server.requests, [])

    def test_uncertain_pooled_post_does_not_fall_back_to_cli(self):
        from xiaoyu_runtime.helpers import dsh_stream
        from xiaoyu_runtime.helpers.agents import DshAgent
        from xiaoyu_runtime.helpers.base import Job
        from runtime.tests.test_streaming import dsh_spec
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(dsh_stream, "direct_official", return_value=True), \
                patch.object(dsh_stream, "open_direct", side_effect=http_pool.AmbiguousSendError("closed")):
            agent = DshAgent(dsh_spec(Path(directory)))
            with patch.object(agent, "_headless") as fallback:
                with self.assertRaises(dsh_stream.NoProviderFallback):
                    agent.run(Job(text="test", plain=True, on_text=lambda _: None))
                fallback.assert_not_called()

    def test_uncertain_send_writes_only_transport_diagnostics(self):
        import json
        from xiaoyu_runtime.helpers import dsh_stream
        from xiaoyu_runtime.helpers.agents import DshAgent
        from xiaoyu_runtime.helpers.base import Job
        from runtime.tests.test_streaming import dsh_spec
        with tempfile.TemporaryDirectory() as directory:
            spec = dsh_spec(Path(directory))
            error = http_pool.AmbiguousSendError(BrokenPipeError('private error text'), stage='read_headers', reused=True)
            with patch.object(dsh_stream, 'direct_official', return_value=True), \
                    patch.object(dsh_stream, 'open_direct', side_effect=error) as send:
                with self.assertRaises(dsh_stream.NoProviderFallback):
                    DshAgent(spec).run(Job(text='private message', plain=True, on_text=lambda _: None))
            self.assertEqual(send.call_count, 1)
            data = (Path(spec.config_dir) / 'xiaoyu-diagnostics' / 'deepseek-transport.jsonl').read_text()
            entry = json.loads(data)
            self.assertEqual((entry['stage'], entry['error_type'], entry['reused_connection']),
                             ('read_headers', 'BrokenPipeError', True))
            self.assertNotIn('private', data)

    def test_connect_and_response_headers_share_one_deadline(self):
        original = http_pool.http.client.HTTPConnection.connect
        def slow_connect(connection):
            time.sleep(.12)
            original(connection)
        start = time.monotonic()
        with patch.object(http_pool.http.client.HTTPConnection, "connect", slow_connect):
            with self.assertRaises(http_pool.AmbiguousSendError):
                self.pool.open(self.request("/slow-headers"), timeout=.2)
        self.assertLess(time.monotonic() - start, .4)
        self.assertEqual(len(self.server.requests), 1)


if __name__ == "__main__":
    unittest.main()
