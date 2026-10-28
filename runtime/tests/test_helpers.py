"""在界面上添加的帮手（helpers.py、Service.helper_*）。"""

import json
import os
import unittest

import urllib.error
import urllib.request

from test_runtime import TOKEN, Scripted, TempDirCase, make_xiaoyou, start_runtime

from xiaoyou_runtime.helpers import helpers
from xiaoyou_runtime.api.service import RequestError, Service


class RegistryTests(TempDirCase):
    def test_display_rename_survives_restart_without_changing_routing(self):
        registry=self.registry()
        registry.rename('claude','我的帮手')
        self.assertEqual('我的帮手',self.registry().label('claude'))
        self.assertEqual([],registry.names())
        self.assertEqual([],registry.disabled())
        with self.assertRaises(helpers.HelperError):registry.rename('missing','名称')
        with self.assertRaises(helpers.HelperError):registry.rename('claude',' ')

    def registry(self, reserved=("claude",)):
        return helpers.Registry(self.folder / "state", self.folder, list(reserved))

    def test_a_key_stays_in_its_own_file_and_never_in_the_list(self):
        registry = self.registry()
        spec = registry.add({"name": "pi-openai", "harness": "pi", "model": "openai/gpt-x",
                             "access": "key", "provider": "openai", "key": " sk-secret "})
        key_file = self.folder / "state" / "helper-secrets" / "pi-openai.key"
        self.assertEqual(spec.env_files, {"OPENAI_API_KEY": key_file})
        self.assertEqual(key_file.read_text("utf-8"), "sk-secret")
        if os.name == "posix":
            self.assertEqual(key_file.stat().st_mode & 0o077, 0)
        listed = (self.folder / "state" / "helpers.json").read_text("utf-8")
        self.assertNotIn("sk-secret", listed)
        self.assertNotIn("key", registry.view("pi-openai"))
        # 重启后照样建得起来。
        again = self.registry().specs()
        self.assertEqual([(item.name, item.type, item.model) for item in again],
                         [("pi-openai", "pi", "openai/gpt-x")])
        registry.remove("pi-openai")
        self.assertFalse(key_file.exists())
        self.assertEqual(self.registry().specs(), [])

    def test_any_harness_takes_any_key_including_a_custom_variable(self):
        registry = self.registry()
        spec = registry.add({"name": "codex-key", "harness": "codex", "access": "key",
                             "provider": "openai", "key": "sk-1"})
        self.assertEqual(list(spec.env_files), ["OPENAI_API_KEY"])
        spec = registry.add({"name": "claude-moon", "harness": "claude_code", "access": "key",
                             "provider": "custom", "env": "ANTHROPIC_AUTH_TOKEN", "key": "sk-2"})
        self.assertEqual(list(spec.env_files), ["ANTHROPIC_AUTH_TOKEN"])
        self.assertEqual(registry.view("claude-moon")["env"], "ANTHROPIC_AUTH_TOKEN")

    def test_a_subscription_helper_records_only_the_way(self):
        spec = self.registry().add({"name": "codex2", "harness": "codex", "access": "login"})
        self.assertEqual((spec.type, spec.env_files), ("codex", {}))

    def test_what_cannot_be_added(self):
        registry = self.registry()
        cases = [
            ({"name": "claude", "harness": "pi", "access": "login"}, "已经有"),
            ({"name": "bad name", "harness": "pi", "access": "login"}, "名字"),
            ({"name": "x", "harness": "gpt", "access": "login"}, "harness"),
            ({"name": "x", "harness": "dsh", "access": "login"}, "不支持订阅登录"),
            ({"name": "x", "harness": "codex", "access": "key", "provider": "acme", "key": "k"}, "服务商"),
            ({"name": "x", "harness": "codex", "access": "key", "provider": "custom", "env": "1BAD", "key": "k"}, "环境变量名"),
            ({"name": "x", "harness": "pi", "access": "key", "provider": "openai"}, "API Key"),
            ({"name": "x", "harness": "pi", "access": "login", "effort": "ultra"}, "effort"),
        ]
        for body, message in cases:
            with self.subTest(body=body), self.assertRaisesRegex(helpers.HelperError, message):
                registry.add(body)
        self.assertEqual(registry.names(), [])
        registry.remove("claude")
        self.assertEqual(self.registry().disabled(), ["claude"])
        registry.enable("claude")
        self.assertEqual(self.registry().disabled(), [])

    def test_options_offer_every_key_to_every_harness_and_login_where_it_exists(self):
        rows = {row["harness"]: row for row in helpers.options()}
        self.assertEqual(rows["dsh"]["ways"], ["magpie", "key"])
        self.assertEqual(rows["codex"]["ways"], ["magpie", "key", "login"])
        self.assertEqual(rows["pi"]["ways"], ["magpie", "key", "login"])
        self.assertEqual(rows["codex"]["providers"], rows["pi"]["providers"])
        self.assertIn("custom", rows["dsh"]["providers"])


class ServiceTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.x, store = make_xiaoyou(self.folder, [Scripted("claude")])
        self.service = Service(self.x, store)
        self.addCleanup(self.service.close)
        self.addCleanup(self.x.close)

    def test_an_added_helper_can_be_used_at_once_and_removed_again(self):
        added = self.service.helper_add({"name": "pi", "harness": "pi", "access": "login",
                                         "description": "写文档"})
        self.assertEqual(added["helper"]["type"], "pi")
        self.assertTrue(self.x.has("pi"))
        self.assertIn("pi", [row["name"] for row in self.service.agents()])
        self.assertIn("pi", self.service.helper_options()["added"])
        self.assertEqual(self.service.helper_remove("pi"), {"removed": "pi"})
        self.assertFalse(self.x.has("pi"))
        with self.assertRaisesRegex(RequestError, "默认帮手"):
            self.service.helper_remove("claude")

    def test_live_membership_updates_usage_and_notifies_other_clients(self):
        before = self.x.usage.rev
        self.service.helper_add({"name": "extra", "harness": "codex", "access": "login"})
        self.assertGreater(self.x.usage.rev, before)
        entry = next(row for row in self.service.usage()["agents"] if row["name"] == "extra")
        self.assertIsNotNone(entry["account"])
        self.assertIn("extra", self.service.helper_options()["removable"])
        self.service.helper_remove("extra")
        self.assertNotIn("extra", [row["name"] for row in self.service.usage()["agents"]])
        self.assertNotIn("extra", [name for row in self.service.usage()["accounts"] for name in row["agents"]])

    def test_config_helper_removal_survives_restart_and_can_be_restored(self):
        from unittest.mock import patch
        main, worker = Scripted("main"), Scripted("worker")
        x, store = make_xiaoyou(self.folder / "base", [main, worker])
        service = Service(x, store)
        self.addCleanup(service.close); self.addCleanup(x.close)
        with patch.object(x.helpers, "_save", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(Exception, "未能移除"):
                service.helper_remove("worker")
        self.assertTrue(x.has("worker")); self.assertEqual(x.helpers.disabled(), [])
        service.helper_remove("worker")
        self.assertFalse(x.has("worker"))
        again, again_store = make_xiaoyou(self.folder / "base", [Scripted("main"), Scripted("worker")])
        self.addCleanup(again.close)
        self.assertFalse(again.has("worker"))
        self.assertNotIn("worker", [row["name"] for row in again.usage.snapshot()["agents"]])
        self.assertEqual(service.helper_options()["restorable"], ["worker"])
        # Restoration uses the original retained config without client-supplied credentials.
        from dataclasses import replace
        x._config = replace(x._config, agents=[main.spec, worker.spec])
        service.helper_add({"restore": "worker"})
        self.assertTrue(x.has("worker")); self.assertEqual(x.helpers.disabled(), [])
        with self.assertRaisesRegex(RequestError, "默认帮手"):
            service.helper_remove("main")

    def test_late_quota_event_cannot_update_a_reused_helper_names_new_account(self):
        from dataclasses import replace
        from xiaoyou_runtime.config import AgentSpec
        meter = self.x.usage
        old = replace(Scripted("quota").spec, type="codex", config_dir=self.folder / "old-login")
        new = replace(old, config_dir=self.folder / "new-login")
        meter.sync_specs([old]); callback = meter.observer("quota")
        meter.sync_specs([new]); callback("codex", {"rateLimits": {"primary": {"usedPercent": 72, "resetsAt": 2000000000}}})
        snapshot = meter.snapshot()
        self.assertEqual(snapshot["accounts"][0]["windows"], [])
        meter.sync_specs([old])
        self.assertEqual(meter.snapshot()["accounts"][0]["windows"][0]["left"], 28)

    def test_codex_unauthorized_quota_refreshes_native_login_once_with_same_deadline(self):
        from unittest.mock import Mock, patch
        from xiaoyou_runtime.helpers.usage import fetch_codex
        from xiaoyou_runtime.helpers.agents import AgentError
        server = Mock()
        server.request.side_effect = [1, 2, 3]
        server.expect.side_effect = [AgentError("401 Unauthorized"), {"account": {}},
            {"rateLimits": {"primary": {"usedPercent": 7, "resetsAt": 2000000000},
                            "secondary": {"usedPercent": 2, "resetsAt": 2000500000}}}]
        with patch("xiaoyou_runtime.helpers.codex_app.AppServer", return_value=server), \
                patch("xiaoyou_runtime.helpers.codex_app.handshake"), \
                patch("xiaoyou_runtime.helpers.agents.CodexAgent._env", return_value={}):
            windows, _, problem = fetch_codex(Scripted("quota").spec)
        self.assertIsNone(problem); self.assertEqual(windows["5h"]["left"], 93)
        self.assertEqual(windows["7d"]["left"], 98)
        self.assertEqual([call.args[0] for call in server.request.call_args_list],
                         ["account/rateLimits/read", "account/read", "account/rateLimits/read"])
        self.assertEqual(server.request.call_args_list[1].args[1], {"refreshToken": True})
        self.assertEqual(len({call.args[1] for call in server.expect.call_args_list}), 1)
        server.close.assert_called_once()

    def test_bad_requests_are_explained(self):
        with self.assertRaisesRegex(RequestError, "harness"):
            self.service.helper_add({"name": "x", "harness": "nope", "access": "login"})
        with self.assertRaisesRegex(RequestError, "对象"):
            self.service.helper_add(["not", "an", "object"])


class HttpTests(TempDirCase):
    def call(self, base, method, path, body=None):
        request = urllib.request.Request(base + path, method=method,
                                         data=json.dumps(body).encode() if body is not None else None)
        request.add_header("Authorization", "Bearer " + TOKEN)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_add_list_and_remove_over_http_and_across_a_restart(self):
        base, _ = start_runtime(self, self.folder)
        status, options = self.call(base, "GET", "/v1/helpers/options")
        self.assertEqual(status, 200)
        self.assertIn("pi", [row["harness"] for row in options["harnesses"]])
        status, added = self.call(base, "POST", "/v1/helpers", {
            "name": "deep", "harness": "dsh", "access": "key", "provider": "deepseek", "key": "sk-1"})
        self.assertEqual((status, added["helper"]["name"]), (201, "deep"))
        self.assertNotIn("sk-1", json.dumps(added))
        status, agents = self.call(base, "GET", "/v1/agents")
        self.assertIn("deep", [row["name"] for row in agents["agents"]])
        status, refused = self.call(base, "POST", "/v1/helpers", {"name": "deep", "harness": "pi", "access": "login"})
        self.assertEqual(status, 400)
        again, _ = start_runtime(self, self.folder)
        status, agents = self.call(again, "GET", "/v1/agents")
        self.assertIn("deep", [row["name"] for row in agents["agents"]])
        status, removed = self.call(again, "POST", "/v1/helpers/deep/remove", {})
        self.assertEqual((status, removed), (200, {"removed": "deep"}))


if __name__ == "__main__":
    unittest.main()
