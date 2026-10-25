"""帮手的能力：能力库、能力网关入口、各 harness 怎么带上（docs/development/helper-capabilities）。"""

import http.server
import json
import subprocess
import sys
import threading
import unittest

from test_runtime import TempDirCase

import xiaoyou_runtime.helpers.agents as agents_module
import xiaoyou_runtime.world.approvals as approvals_module
import xiaoyou_runtime.config as config_module
from xiaoyou_runtime.helpers import capabilities, helper_extras, mcp_client, permission_mcp
from xiaoyou_runtime.helpers.agents import Job

# 一个最小的 stdio MCP 服务：两个工具，echo 原样返回参数，boom 报错。
FAKE_SERVER = r'''
import json, os, sys
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message["method"]
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "Echo back", "inputSchema": {"type": "object"}},
                            {"name": "boom", "description": "Always fails", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        params = message["params"]
        if params["name"] == "boom":
            result = {"content": [{"type": "text", "text": "boom"}], "isError": True}
        else:
            text = json.dumps({"args": params["arguments"], "token": os.environ.get("FAKE_TOKEN")})
            result = {"content": [{"type": "text", "text": text}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "no"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
'''


def settings(grants=None, env=None):
    return capabilities.parse({
        "mcp_servers": {"fake": {"name": "假服务", "command": sys.executable, "args": ["-c", FAKE_SERVER],
                                 "env": env or []}},
        "grants": grants or {},
    }, ["claude", "codex"])


class ConfigTests(TempDirCase):
    def agent(self, raw):
        return config_module._agent("helper", raw, self.folder, 600, {})

    def test_mcp_is_no_longer_configured_per_helper(self):
        with self.assertRaisesRegex(config_module.ConfigError, "capabilities.mcp_servers"):
            self.agent({"type": "claude_code", "mcp_servers": {"x": {"command": "x"}}})

    def test_skills_only_where_the_harness_can_take_them(self):
        spec = self.agent({"type": "dsh", "skills": ["skills/notes"]})
        self.assertEqual(spec.skills, [self.folder / "skills" / "notes"])
        with self.assertRaisesRegex(config_module.ConfigError, "Codex"):
            self.agent({"type": "codex", "skills": ["skills/notes"]})
        with self.assertRaisesRegex(config_module.ConfigError, "同名"):
            self.agent({"type": "pi", "skills": ["a/notes", "b/notes"]})

    def test_pi_is_a_helper_type_with_thinking_levels(self):
        spec = self.agent({"type": "pi", "model": "openai/gpt-x", "efforts": ["off", "high"], "effort": "high"})
        self.assertEqual((spec.command, spec.effort, spec.speaks), (["pi"], "high", False))
        with self.assertRaisesRegex(config_module.ConfigError, "沙箱"):
            self.agent({"type": "pi", "sandbox": "read-only"})

    def test_library_rejects_unknown_helpers_servers_and_secret_values(self):
        with self.assertRaisesRegex(capabilities.CapabilityError, "没有叫"):
            capabilities.parse({"mcp_servers": {}, "grants": {"nobody": {}}}, ["claude"])
        with self.assertRaisesRegex(capabilities.CapabilityError, "没有这个服务"):
            capabilities.parse({"mcp_servers": {}, "grants": {"claude": {"x": False}}}, ["claude"])
        with self.assertRaisesRegex(capabilities.CapabilityError, "只写名字"):
            capabilities.parse({"mcp_servers": {"n": {"url": "https://x", "headers": {"Authorization": "Bearer s"}}}},
                               ["claude"])


class LibraryTests(TempDirCase):
    def library(self, **kwargs):
        library = capabilities.Library(settings(**kwargs), self.folder / "secrets")
        self.addCleanup(library.close)
        return library

    def test_every_helper_gets_the_tools_by_default_and_a_grant_narrows_them(self):
        library = self.library(grants={"codex": {"fake": False}, "claude": {"fake": {"tools": ["echo"]}}})
        self.assertFalse(library.offers("codex"))
        self.assertEqual(library.tools("codex")["tools"], [])
        listed = library.tools("claude")["tools"]
        self.assertEqual([tool["name"] for tool in listed], ["fake__echo"])
        self.assertTrue(listed[0]["description"].startswith("[假服务]"))

    def test_a_call_carries_the_versions_it_was_listed_with(self):
        library = self.library()
        tool = library.tools("claude")["tools"][0]
        result = library.call("claude", "fake", "echo", {"a": 1}, tool["config_version"], tool["grant_version"])
        self.assertEqual(json.loads(result["content"][0]["text"])["args"], {"a": 1})
        with self.assertRaisesRegex(capabilities.CapabilityError, "重新列"):
            library.call("claude", "fake", "echo", {}, "old", tool["grant_version"])
        boom = library.call("claude", "fake", "boom", {}, tool["config_version"], tool["grant_version"])
        self.assertTrue(boom["isError"])

    def test_connections_stay_in_the_runtime_and_missing_ones_are_reported(self):
        library = self.library(env=["FAKE_TOKEN"])
        self.assertEqual(library.tools("claude")["tools"], [])
        self.assertIn("还没有连接", library.tools("claude")["unavailable"][0]["reason"])
        (self.folder / "secrets").mkdir()
        (self.folder / "secrets" / "fake.json").write_text(json.dumps({"env": {"FAKE_TOKEN": "t0p"}}), "utf-8")
        tool = library.tools("claude")["tools"][0]
        result = library.call("claude", "fake", "echo", {}, tool["config_version"], tool["grant_version"])
        self.assertEqual(json.loads(result["content"][0]["text"])["token"], "t0p")

    def test_tool_names_are_prefixed_safe_and_unique(self):
        taken = {}
        first = capabilities.runtime_name("docs", "search pages!", taken)
        taken[first] = True
        self.assertEqual(first, "docs__search_pages")
        self.assertNotEqual(capabilities.runtime_name("docs", "search pages?", taken), first)


class GatewayTests(TempDirCase):
    def test_xiaoyou_mcp_lists_and_calls_only_what_the_key_allows(self):
        gate = approvals_module.Gate(approvals_module.Approvals())
        self.addCleanup(gate.close)
        gate.capabilities = capabilities.Library(settings(grants={"codex": {"fake": False}}), self.folder)
        self.addCleanup(gate.capabilities.close)
        claude, codex = gate.open("c1", "default", "claude"), gate.open("c2", "default", "codex")
        state = {}
        listed = permission_mcp.handle({"id": 1, "method": "tools/list"}, claude["XIAOYOU_GATE_URL"],
                                       claude["XIAOYOU_GATE_KEY"], approve=False, state=state)
        self.assertEqual([tool["name"] for tool in listed["result"]["tools"]], ["fake__echo", "fake__boom"])
        called = permission_mcp.handle({"id": 2, "method": "tools/call",
                                        "params": {"name": "fake__echo", "arguments": {"q": "hi"}}},
                                       claude["XIAOYOU_GATE_URL"], claude["XIAOYOU_GATE_KEY"],
                                       approve=False, state=state)
        self.assertEqual(json.loads(called["result"]["content"][0]["text"])["args"], {"q": "hi"})
        # 另一个帮手的钥匙列不出、也调不了这个服务。
        other = {}
        listed = permission_mcp.handle({"id": 3, "method": "tools/list"}, codex["XIAOYOU_GATE_URL"],
                                       codex["XIAOYOU_GATE_KEY"], approve=False, state=other)
        self.assertEqual(listed["result"]["tools"], [])
        gate.shut(claude)
        refused = permission_mcp.handle({"id": 4, "method": "tools/call",
                                         "params": {"name": "fake__echo", "arguments": {}}},
                                        claude["XIAOYOU_GATE_URL"], claude["XIAOYOU_GATE_KEY"],
                                        approve=False, state=state)
        self.assertTrue(refused["result"]["isError"])

    def test_approve_is_listed_only_for_claude_code(self):
        listed = permission_mcp.handle({"id": 1, "method": "tools/list"}, None, None, approve=False)
        self.assertEqual(listed["result"]["tools"], [])
        listed = permission_mcp.handle({"id": 1, "method": "tools/list"}, None, None)
        self.assertEqual([tool["name"] for tool in listed["result"]["tools"]], ["approve"])


class HttpClientTests(unittest.TestCase):
    def test_streamable_http_answers_as_an_event_stream(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if "id" not in message:
                    self.send_response(202)
                    self.end_headers()
                    return
                result = ({"protocolVersion": "2025-06-18"} if message["method"] == "initialize" else
                          {"tools": [{"name": "t", "inputSchema": {"type": "object"}}]})
                body = ("event: message\ndata: %s\n\n" % json.dumps(
                    {"jsonrpc": "2.0", "id": message["id"], "result": result})).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Mcp-Session-Id", "s1")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        spec = capabilities.Server("web", "Web", "", url="http://127.0.0.1:%d/mcp" % server.server_address[1])
        session = mcp_client.connect(spec, {"env": {}, "headers": {}})
        self.assertEqual([tool["name"] for tool in session.list_tools()], ["t"])
        self.assertEqual(session._session_id, "s1")


class HarnessTests(TempDirCase):
    gate = {"XIAOYOU_GATE_URL": "http://127.0.0.1:1/approve", "XIAOYOU_GATE_KEY": "k"}

    def test_each_harness_gets_the_same_xiaoyou_mcp(self):
        claude = agents_module.ClaudeCodeAgent.mcp_config(self.gate)["mcpServers"]["xiaoyou"]
        self.assertEqual(claude["env"]["XIAOYOU_GATE_APPROVE"], "1")
        codex = helper_extras.codex_overrides(self.gate)
        self.assertIn('mcp_servers.xiaoyou.args=["-m","xiaoyou_runtime.helpers.permission_mcp"]', codex)
        self.assertEqual(helper_extras.codex_overrides(None), [])
        acp = helper_extras.acp_servers(self.gate)[0]
        self.assertEqual((acp["name"], {"name": "XIAOYOU_GATE_KEY", "value": "k"} in acp["env"]), ("xiaoyou", True))
        self.assertNotIn("XIAOYOU_GATE_APPROVE", [row["name"] for row in acp["env"]])

    def test_codex_puts_the_override_before_the_subcommand_only_with_a_key(self):
        spec = config_module._agent("codex", {"type": "codex", "mode": "exec"}, self.folder, 600, {})
        agent = agents_module.CodexAgent(spec)
        control = agents_module.Control()
        control.gate = self.gate
        args = agent.command(Job("hi", control=control), self.folder / "last.txt")
        self.assertLess(args.index("-c"), args.index("exec"))
        self.assertNotIn("-c", agent.command(Job("hi"), self.folder / "last.txt")[:2])

    def test_pi_runs_once_with_its_own_session_and_skills(self):
        skill = self.folder / "notes"
        skill.mkdir()
        (skill / "SKILL.md").write_text("---\nname: notes\n---\n", "utf-8")
        spec = config_module._agent("pi", {"type": "pi", "model": "openai/gpt-x", "skills": [str(skill)],
                                           "efforts": ["high"], "effort": "high"}, self.folder, 600, {})
        seen = []

        def fake_run(command, **kwargs):
            seen.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "done\n", "")

        agent = agents_module.PiAgent(spec, run=fake_run)
        outcome = agent.run(Job("do it"))
        command, kwargs = seen[-1]
        self.assertEqual(command[:4], ["pi", "-p", "--session-id", outcome.session_id])
        self.assertEqual(command[command.index("--skill") + 1], str(skill))
        self.assertEqual(command[command.index("--thinking") + 1], "high")
        self.assertEqual((outcome.text, kwargs["input"]), ("done", "do it"))
        again = agent.run(Job("more", session_id=outcome.session_id))
        self.assertEqual(again.session_id, outcome.session_id)
        plain = agent.command(Job("hi", plain=True), "s")
        self.assertIn("--no-tools", plain)
        self.assertNotIn("--skill", plain)
        self.assertIsNone(helper_extras.missing_skill(spec))
        (skill / "SKILL.md").unlink()
        self.assertIn("SKILL.md", helper_extras.missing_skill(spec))


if __name__ == "__main__":
    unittest.main()
