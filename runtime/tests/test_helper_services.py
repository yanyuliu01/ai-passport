"""Model-service adapters and MCP lifecycle/failure regressions."""

import concurrent.futures
import http.server
import json
import os
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from test_runtime import TempDirCase, Scripted, make_xiaoyou, write_config, start_runtime, TOKEN
from xiaoyou_runtime.helpers import capabilities, helper_models, helpers, mcp_client, agents
from xiaoyou_runtime import config
from xiaoyou_runtime.api.service import Service, UnavailableError, RequestError


class LocalModels(TempDirCase):
    def setUp(self):
        super().setUp()
        self.requests = []
        requests = self.requests
        fixture = self
        self.catalog_rows = [{"id": "vendor/model-x"}, {"id": "other/model-y"}]

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                data = json.dumps({"data": fixture.catalog_rows}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = "http://127.0.0.1:%d" % self.server.server_port

    def registry(self):
        return helpers.Registry(self.folder / "state", self.folder, ["claude"])

    def body(self, name="added", harness="codex"):
        return {"name": name, "harness": harness, "access": "magpie", "gateway_url": self.url,
                "model": "vendor/model-x", "key": "gateway-only-token"}

    def test_catalog_is_live_bounded_local_and_does_not_return_key(self):
        result = helper_models.catalog(self.url + "/v1", "gateway-only-token")
        self.assertEqual([row["id"] for row in result["models"]], ["vendor/model-x", "other/model-y"])
        self.assertNotIn("gateway-only-token", json.dumps(result))
        self.assertEqual(self.requests, [("/v1/models", "Bearer gateway-only-token")])
        for url in ("https://example.com", "http://127.0.0.1:1/?key=x", "http://x:y@127.0.0.1:1",
                    "http://127.0.0.1:1/other", "http://localhost:1", "http://127.0.0.1:0"):
            with self.subTest(url=url), self.assertRaises(helper_models.ModelServiceError):
                helper_models.address(url)

    def test_same_model_is_bound_to_each_harness_without_native_config_writes(self):
        registry = self.registry()
        claude = registry.add(self.body("cc2", "claude_code"))
        self.assertEqual(claude.env["ANTHROPIC_BASE_URL"], self.url)
        self.assertTrue(str(claude.config_dir).startswith(str(self.folder)))
        codex = registry.add(self.body("codex2"))
        command = agents.CodexAgent(codex).command(agents.Job("hello"), self.folder / "last.txt")
        self.assertLess(command.index("-c"), command.index("exec"))
        self.assertIn('model_provider="xiaoyou_gateway"', command)
        self.assertIn('wire_api="responses"', " ".join(command))
        self.assertNotIn("gateway-only-token", " ".join(command))
        dsh = registry.add(self.body("dsh2", "dsh"))
        self.assertEqual(dsh.env["DEEPSEEK_BASE_URL"], self.url)
        path = agents.DshAgent(dsh)._patch_file(self.folder / "scratch", dsh.model, False)
        data = path.read_text("utf-8")
        self.assertIn("provider: xiaoyou_gateway", data)
        self.assertIn("api: openai-completions", data)
        self.assertNotIn("gateway-only-token", data)
        pi = registry.add(self.body("pi2", "pi"))
        self.assertEqual(pi.model, "xiaoyou_gateway/vendor/model-x")
        env = agents.PiAgent(pi)._env()
        document = json.loads((Path(env["PI_CODING_AGENT_DIR"]) / "models.json").read_text("utf-8"))
        self.assertEqual(document["providers"]["xiaoyou_gateway"]["apiKey"], helper_models.KEY_ENV)
        self.assertEqual(document["providers"]["xiaoyou_gateway"]["models"][0]["id"], "vendor/model-x")
        restored = self.registry().specs()
        self.assertEqual(len(restored), 4)
        saved = (self.folder / "state" / "helpers.json").read_text("utf-8")
        self.assertNotIn("gateway-only-token", saved)

    def test_service_add_checks_current_catalog_and_deletion_failure_preserves_agent(self):
        x, store = make_xiaoyou(self.folder, [Scripted("claude")])
        service = Service(x, store)
        self.addCleanup(x.close)
        self.addCleanup(service.close)
        body = self.body()
        body["model"] = "removed/model"
        with self.assertRaisesRegex(RequestError, "不在模型服务"):
            service.helper_add(body)
        body["model"] = "vendor/model-x"
        service.helper_add(body)
        self.assertTrue(x.has("added"))
        with patch.object(x.helpers, "_save", side_effect=OSError("disk failure")):
            with self.assertRaises(UnavailableError):
                service.helper_remove("added")
        self.assertTrue(x.has("added"))
        self.assertIn("added", x.helpers.names())
        service.helper_remove("added")
        self.assertFalse(x.has("added"))

    def test_http_model_discovery_add_and_grant_edit_preserve_only_public_metadata(self):
        base, _ = start_runtime(self, self.folder, capabilities={"mcp_servers": {"demo": {"command": "unused"}}})
        def post(path, body):
            request = urllib.request.Request(base + path, method="POST", data=json.dumps(body).encode(),
                                             headers={"Authorization": "Bearer " + TOKEN})
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read())
        models = post("/v1/helpers/models", {"gateway_url": self.url, "key": "gateway-only-token"})
        self.assertEqual(models["models"][0]["id"], "vendor/model-x")
        added = post("/v1/helpers", self.body())
        self.assertNotIn("gateway-only-token", json.dumps(added))
        answer = post("/v1/helpers/added/capabilities", {"demo": False})
        self.assertEqual(answer["grants"], {"demo": False})

    def test_native_api_and_reasoning_levels_come_from_the_live_model(self):
        self.catalog_rows = [{"id": "vendor/model-x", "native_endpoints": ["/v1/messages"],
                              "reasoning_levels": ["none", "low", "high"], "reasoning": True}]
        x, store = make_xiaoyou(self.folder, [Scripted("claude")])
        service = Service(x, store)
        self.addCleanup(x.close)
        self.addCleanup(service.close)
        body = dict(self.body(harness="dsh"), effort="high", model_info={"api": "untrusted"})
        service.helper_add(body)
        agent = next(agent for agent in x.agents() if agent.name == "added")
        self.assertEqual(agent.spec.efforts, ["off", "low", "high"])
        path = agent._patch_file(self.folder / "patch", agent.spec.model, False)
        self.assertIn("api: anthropic-messages", path.read_text("utf-8"))
        self.assertIn('"off": "none"', path.read_text("utf-8"))
        # Unknown/non-reasoning models must not inherit the native dsh's off/high.
        spec = self.registry().add(self.body("no-reasoning", "dsh"))
        self.assertEqual(spec.efforts, [])
        path = agents.DshAgent(spec)._patch_file(self.folder / "plain", spec.model, True)
        self.assertNotIn("reasoningEffort:", path.read_text("utf-8"))


class McpLifecycle(TempDirCase):
    def settings(self):
        return capabilities.parse({"mcp_servers": {"demo": {"command": "unused"}}}, ["claude"])

    def test_concurrent_first_connection_is_shared_and_never_closed_under_its_caller(self):
        entered, release = threading.Event(), threading.Event()
        created = []

        class Fake:
            def __init__(self, server):
                self.version, self.closed = server.version, False
            def alive(self):
                return not self.closed
            def close(self):
                self.closed = True

        def connect(server, connection):
            session = Fake(server)
            created.append(session)
            entered.set()
            release.wait(2)
            return session

        settings = self.settings()
        library = capabilities.Library(settings, self.folder, connect=connect)
        self.addCleanup(library.close)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            first = pool.submit(library._session, settings.servers["demo"])
            self.assertTrue(entered.wait(1))
            second = pool.submit(library._session, settings.servers["demo"])
            release.set()
            self.assertIs(first.result(2), second.result(2))
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].closed)

    def test_invalid_catalogs_are_unavailable_instead_of_offered_to_a_helper(self):
        class Fake:
            def __init__(self, server, connection): self.version = server.version
            def alive(self): return True
            def list_tools(self): return self.rows
            def close(self): pass
        for rows in ([{"name": "same"}, {"name": "same"}], [{"name": "x", "inputSchema": []}],
                     [{"name": "  bad"}], [{"name": "x" * 129}]):
            with self.subTest(rows=rows):
                Fake.rows = rows
                library = capabilities.Library(self.settings(), self.folder, connect=Fake)
                try:
                    listed = library.tools("claude")
                    self.assertEqual(listed["tools"], [])
                    self.assertEqual(len(listed["unavailable"]), 1)
                finally:
                    library.close()

    def test_revoke_regrant_and_restart_cannot_reuse_an_old_grant_version(self):
        x, store = make_xiaoyou(self.folder, [Scripted("claude")], capabilities=self.settings())
        service = Service(x, store)
        self.addCleanup(x.close)
        self.addCleanup(service.close)
        service.helper_add({"name": "added", "harness": "pi", "access": "login"})
        library = x._gate.capabilities
        original = library.grant("added", "demo").version
        service.helper_grants("added", {"demo": False})
        with self.assertRaisesRegex(capabilities.CapabilityError, "不给"):
            library.call("added", "demo", "write", {}, self.settings().servers["demo"].version, original)
        service.helper_grants("added", {"demo": True})
        with self.assertRaisesRegex(capabilities.CapabilityError, "重新列"):
            library.call("added", "demo", "write", {}, self.settings().servers["demo"].version, original)
        registry = helpers.Registry(self.folder / "state", self.folder, ["claude"])
        self.assertEqual(registry.policy("added"), {"grants": {"demo": True}, "revision": 2})
        current = library.grant("added", "demo").version
        with patch.object(x.helpers, "_save", side_effect=OSError("disk failure")):
            with self.assertRaises(UnavailableError):
                service.helper_grants("added", {"demo": False})
        self.assertEqual(library.grant("added", "demo").version, current)

    def test_config_grants_accept_a_persisted_ui_helper(self):
        registry = helpers.Registry(self.folder / "state", self.folder, ["echo"])
        registry.add({"name": "added", "harness": "pi", "access": "login"})
        path = write_config(self.folder, capabilities={"mcp_servers": {"demo": {"command": "unused"}},
                                                       "grants": {"added": {"demo": False}}})
        loaded = config.load(path, {})
        self.assertFalse(loaded.capabilities.grant("added", "demo").enabled)

    def test_rotating_credentials_invalidates_the_catalog_and_reconnects(self):
        settings = capabilities.parse({"mcp_servers": {"demo": {"command": "unused", "env": ["TOKEN"]}}}, ["claude"])
        path = self.folder / "demo.json"
        path.write_text(json.dumps({"env": {"TOKEN": "first"}}), "utf-8")
        if os.name == "posix":
            path.chmod(0o600)
        seen = []
        class Fake:
            closed = False
            def __init__(self, server, connection):
                self.version = server.version
                seen.append(connection["env"]["TOKEN"])
            def alive(self): return not self.closed
            def list_tools(self): return [{"name": "read"}]
            def close(self): self.closed = True
        library = capabilities.Library(settings, self.folder, connect=Fake)
        self.addCleanup(library.close)
        tool = library.tools("claude")["tools"][0]
        old = library._sessions["demo"]
        path.write_text(json.dumps({"env": {"TOKEN": "rotated-longer"}}), "utf-8")
        with self.assertRaisesRegex(capabilities.CapabilityError, "重新列"):
            library.call("claude", "demo", "read", {}, tool["config_version"], tool["grant_version"])
        updated = library.tools("claude")["tools"][0]
        self.assertNotEqual(updated["config_version"], tool["config_version"])
        self.assertTrue(old.closed)
        self.assertEqual(seen, ["first", "rotated-longer"])

    def test_sse_result_returns_before_a_persistent_stream_closes(self):
        release = threading.Event()
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                payload = {"jsonrpc": "2.0", "id": message["id"], "result": {"tools": []}}
                self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode())
                self.wfile.flush()
                release.wait(2)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            spec = capabilities.Server("demo", "Demo", "", url="http://127.0.0.1:%d/mcp" % server.server_port)
            with patch.object(mcp_client, "CALL_SECONDS", 0.2):
                result = mcp_client.HttpSession(spec, {}).request("tools/list", {})
            self.assertEqual(result, {"tools": []})
        finally:
            release.set()
            server.shutdown()
            server.server_close()

    def test_partial_line_heartbeats_cannot_extend_the_total_deadline(self):
        release = threading.Event()
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    while not release.wait(0.01):
                        self.wfile.write(b":heartbeat-without-newline")
                        self.wfile.flush()
                except OSError:
                    pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            spec = capabilities.Server("demo", "Demo", "", url="http://127.0.0.1:%d/mcp" % server.server_port)
            began = time.monotonic()
            with patch.object(mcp_client, "CALL_SECONDS", 0.15), self.assertRaises(mcp_client.McpError):
                mcp_client.HttpSession(spec, {}).request("tools/list", {})
            self.assertLess(time.monotonic() - began, 1)
        finally:
            release.set()
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
