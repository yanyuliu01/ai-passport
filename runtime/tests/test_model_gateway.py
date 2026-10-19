"""Model gateway: local-first routing, network budget, JSON retries, usage ledger, real HTTP."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from test_runtime import TempDirCase, write_config
from xiaoyou_runtime import config as config_module
from xiaoyou_runtime.model_gateway import (
    Gateway, GatewayConfigError, GatewayError, binding_env, infer_locality, parse)


def settings(**network):
    return parse({
        "providers": {
            "local": {"base_url": "http://127.0.0.1:11434/v1", "models": ["small"]},
            "cloud": {"base_url": "https://api.example.com/v1", "key_env": "CLOUD_KEY",
                      "price": {"input": 2, "output": 8}},
        },
        "routes": {"default": ["local/small"], "iterate.plan": ["local/small", "cloud/big"]},
        "network": network,
    }, env={"CLOUD_KEY": "sk-test"})


def openai_reply(text, prompt=10, completion=5):
    return {"choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion}}


class Transport:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(body)))
        result = self.behaviour(url, len(self.calls))
        if isinstance(result, Exception):
            raise result
        return result


class GatewayTest(TempDirCase):
    def test_locality_is_inferred_from_the_address(self):
        self.assertEqual(infer_locality("http://127.0.0.1:11434/v1"), "local")
        self.assertEqual(infer_locality("http://192.168.1.5:8000/v1"), "local")
        self.assertEqual(infer_locality("http://100.88.53.14:1234/v1"), "local")
        self.assertEqual(infer_locality("http://studio.local:1234/v1"), "local")
        self.assertEqual(infer_locality("https://api.deepseek.com/v1"), "network")

    def test_purpose_falls_back_to_its_parent_and_then_default(self):
        gateway = Gateway(settings())
        self.assertEqual(gateway.candidates("iterate.plan"), [("local", "small"), ("cloud", "big")])
        self.assertEqual(gateway.candidates("iterate.classify"), [("local", "small")])

    def test_network_models_are_not_used_unless_allowed(self):
        transport = Transport(lambda url, n: ConnectionError("refused") if "11434" in url else openai_reply("ok"))
        gateway = Gateway(settings(), self.folder, transport)
        with self.assertRaises(GatewayError) as caught:
            gateway.complete("iterate.plan", [{"role": "user", "content": "hi"}])
        self.assertIn("没打开联网模型", str(caught.exception))
        self.assertEqual(len(transport.calls), 1)

    def test_falls_back_to_network_within_budget_and_records_cost(self):
        transport = Transport(lambda url, n: ConnectionError("refused") if "11434" in url
                              else openai_reply("ok", 1_000_000, 0))
        gateway = Gateway(settings(allowed=True, daily_budget=3), self.folder, transport)
        completion = gateway.complete("iterate.plan", [{"role": "user", "content": "hi"}])
        self.assertEqual((completion.provider, completion.locality, completion.cost), ("cloud", "network", 2.0))
        self.assertEqual(transport.calls[-1][1]["Authorization"], "Bearer sk-test")
        self.assertAlmostEqual(gateway.spent_today(), 2.0)
        # The local endpoint is marked down for a while: the next call goes straight to the network.
        gateway.complete("iterate.plan", [{"role": "user", "content": "hi"}])
        self.assertEqual(sum(1 for call in transport.calls if "11434" in call[0]), 1)
        # Budget is now spent (4.0 >= 3): no more network calls.
        with self.assertRaises(GatewayError) as caught:
            gateway.complete("iterate.plan", [{"role": "user", "content": "hi"}])
        self.assertIn("上限", str(caught.exception))
        ledger = (self.folder / "models" / "usage.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("hi", ledger)  # no prompts in the ledger

    def test_json_answers_are_extracted_and_retried_with_the_problem(self):
        answers = ["好的：```json\n{\"category\": \"nope\"}\n```", "{\"category\": \"app_ui\"}"]
        transport = Transport(lambda url, n: openai_reply(answers[n - 1]))
        gateway = Gateway(settings(), self.folder, transport)
        value, _ = gateway.complete_json(
            "default", [{"role": "user", "content": "x"}],
            lambda v: None if v.get("category") == "app_ui" else "category 不对")
        self.assertEqual(value, {"category": "app_ui"})
        retry = transport.calls[1][2]["messages"]
        self.assertIn("category 不对", retry[-1]["content"])
        self.assertEqual(transport.calls[0][2]["response_format"], {"type": "json_object"})

    def test_anthropic_protocol_and_binding_env(self):
        configured = parse({"providers": {"ds": {
            "protocol": "anthropic", "base_url": "https://api.deepseek.com/anthropic", "key_env": "K"}},
            "routes": {"default": "ds/deepseek-v4-flash"}, "network": {"allowed": True, "daily_budget": 1}},
            env={"K": "secret"})
        transport = Transport(lambda url, n: {"content": [{"type": "text", "text": "好"}],
                                              "usage": {"input_tokens": 3, "output_tokens": 1}})
        completion = Gateway(configured, None, transport).complete(
            "default", [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}])
        url, headers, body = transport.calls[0]
        self.assertTrue(url.endswith("/v1/messages"))
        self.assertEqual((headers["x-api-key"], body["system"], completion.text), ("secret", "S", "好"))
        self.assertEqual(binding_env(configured.providers["ds"]),
                         {"ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic", "ANTHROPIC_AUTH_TOKEN": "secret"})

    def test_bad_configuration_is_rejected(self):
        for raw in ({"providers": {"a": {"base_url": "ftp://x"}}},
                    {"providers": {"a": {"base_url": "http://x", "protocol": "grpc"}}},
                    {"providers": {"a": {"base_url": "http://x"}}, "routes": {"default": ["b/m"]}},
                    {"providers": {"a": {"base_url": "http://x", "key_env": "A", "key_file": "f"}}}):
            with self.assertRaises(GatewayConfigError):
                parse(raw, env={})

    def test_config_loads_the_models_section(self):
        path = write_config(self.folder, models={
            "providers": {"local": {"base_url": "http://127.0.0.1:11434/v1"}},
            "routes": {"default": ["local/qwen"]}})
        loaded = config_module.load(path, {})
        self.assertEqual(loaded.models.routes["default"], [("local", "qwen")])
        with self.assertRaises(config_module.ConfigError):
            config_module.load(write_config(self.folder, models={"routes": {"default": ["x/y"]}}), {})

    def test_real_http_against_an_openai_compatible_stub(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                seen.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                data = json.dumps(openai_reply("好")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        configured = parse({"providers": {"local": {"base_url": "http://127.0.0.1:%d/v1" % server.server_address[1]}},
                            "routes": {"default": ["local/qwen"]}})
        completion = Gateway(configured, self.folder).complete("default", [{"role": "user", "content": "hi"}])
        self.assertEqual((completion.text, completion.locality, completion.cost), ("好", "local", 0.0))
        self.assertEqual(seen[0][0], "/v1/chat/completions")
        self.assertEqual(seen[0][1]["model"], "qwen")
