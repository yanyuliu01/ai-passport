"""Startup diagnostics must not delay service availability."""

import contextlib
import io
import json
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from xiaoyu_runtime import __main__ as entry
from xiaoyu_runtime.helpers.agents import EchoAgent


class StartupChecksTest(unittest.TestCase):
    def test_slow_agent_check_does_not_block_health_or_chat(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "persona.txt").write_text("Test persona", encoding="utf-8")
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            token = "test-startup-token-0123456789"
            config = path / "config.json"
            config.write_text(json.dumps({"server": {"host": "127.0.0.1", "port": port,
                                                        "token": token},
                                          "backend": "echo"}))
            check_started, release_check = threading.Event(), threading.Event()
            server_created = threading.Event()
            servers = []
            results = []
            original_make_server = entry.make_server

            def blocked_check(_agent):
                check_started.set()
                release_check.wait(10)
                return None

            def capture_server(*args, **kwargs):
                server = original_make_server(*args, **kwargs)
                servers.append(server)
                server_created.set()
                return server

            def run():
                results.append(entry.main(["--config", str(config)]))

            with mock.patch.object(EchoAgent, "check", blocked_check), \
                    mock.patch.object(entry, "STARTUP_CHECK_GRACE_SECONDS", 0), \
                    mock.patch.object(entry, "make_server", capture_server), \
                    contextlib.redirect_stderr(io.StringIO()):
                thread = threading.Thread(target=run, daemon=True)
                thread.start()
                try:
                    self.assertTrue(check_started.wait(3), "diagnostic never started")
                    self.assertTrue(server_created.wait(2), "listener blocked on diagnostic")
                    base = "http://127.0.0.1:%d" % port
                    with urllib.request.urlopen(base + "/healthz", timeout=2) as response:
                        self.assertTrue(json.load(response)["ok"])
                    body = json.dumps({"text": "hello", "client_id": "startup-1"}).encode()
                    request = urllib.request.Request(base + "/v1/messages", data=body,
                        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
                    with urllib.request.urlopen(request, timeout=2) as response:
                        message = json.load(response)
                    self.assertTrue(message["id"])
                    request = urllib.request.Request(base + "/v1/messages/" + message["id"] + "?wait=1",
                        headers={"Authorization": "Bearer " + token})
                    with urllib.request.urlopen(request, timeout=2) as response:
                        reply = json.load(response)
                    self.assertEqual(reply["status"], "done")
                    self.assertIn("hello", reply["reply"])
                    self.assertFalse(release_check.is_set())
                finally:
                    release_check.set()
                    if servers:
                        servers[0].shutdown()
                    thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(results, [0])

    def test_check_command_still_reports_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            (Path(folder) / "persona.txt").write_text("Test persona", encoding="utf-8")
            config.write_text(json.dumps({"server": {"host": "127.0.0.1", "port": 8765,
                                                      "token": "test-startup-token-0123456789"},
                                          "backend": "echo"}))
            with mock.patch.object(EchoAgent, "check", return_value="not available"), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(entry.main(["--config", str(config), "--check"]), 2)
            self.assertIn("not available", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
