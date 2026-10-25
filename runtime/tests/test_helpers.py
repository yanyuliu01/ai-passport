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
        with self.assertRaisesRegex(helpers.HelperError, "config.json"):
            registry.remove("claude")

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
        with self.assertRaisesRegex(RequestError, "没有在界面上加过"):
            self.service.helper_remove("claude")

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
