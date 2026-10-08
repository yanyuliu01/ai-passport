#!/usr/bin/env python3
"""Tests for the Xiaoyou runtime. Standard library only; no model is called.

The claude_code backend is exercised against a fake `claude` executable that
records its arguments and standard input, so these tests prove how the runtime
drives the command line, not how the real Claude Code behaves.
"""

import ast
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNTIME))

from xiaoyou_runtime import backends, config as config_module  # noqa: E402
from xiaoyou_runtime.__main__ import main  # noqa: E402
from xiaoyou_runtime.server import make_server  # noqa: E402
from xiaoyou_runtime.service import RequestError, Service  # noqa: E402
from xiaoyou_runtime.store import Store  # noqa: E402

TOKEN = "unit-test-token-0123456789"

FAKE_CLAUDE = r'''
import json, os, sys
args = sys.argv[1:]
text = sys.stdin.read()
log = os.environ["FAKE_CLAUDE_LOG"]
with open(log, "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"args": args, "stdin": text, "cwd": os.getcwd()}) + "\n")
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
if mode == "crash":
    sys.stderr.write("not logged in\n")
    sys.exit(1)
if mode == "error":
    print(json.dumps({"is_error": True, "result": "usage limit reached", "session_id": "s-err"}))
    sys.exit(0)
resumed = args[args.index("--resume") + 1] if "--resume" in args else None
out = {"is_error": False, "session_id": resumed or "s-new", "result": "plain " + text}
if mode == "ok":
    out["structured_output"] = {"reply": "full " + text, "brief": "short", "mood": "happy"}
print(json.dumps(out))
'''


def write_config(folder: Path, **overrides):
    (folder / "persona.txt").write_text("PERSONA-MARKER\n", encoding="utf-8")
    raw = {
        "server": {"host": "127.0.0.1", "port": 8765, "token": TOKEN},
        "backend": "echo",
    }
    raw.update(overrides)
    path = folder / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.folder = Path(self._temporary.name)


class ConfigTests(TempDirCase):
    def load(self, env=None, **overrides):
        return config_module.load(write_config(self.folder, **overrides), env or {})

    def test_defaults_and_relative_paths(self):
        loaded = self.load()
        self.assertEqual((loaded.host, loaded.port, loaded.backend), ("127.0.0.1", 8765, "echo"))
        self.assertEqual(loaded.persona, "PERSONA-MARKER")
        self.assertEqual(loaded.state_dir, (self.folder / "state").resolve())
        self.assertEqual(loaded.claude_workdir, (self.folder / "workdir").resolve())
        self.assertEqual(loaded.claude_command, ["claude"])
        self.assertEqual(loaded.claude_permission_mode, "dontAsk")

    def test_environment_overrides_file(self):
        loaded = self.load(env={
            "XIAOYOU_HOST": "0.0.0.0", "XIAOYOU_PORT": "9000",
            "XIAOYOU_TOKEN": "env-token-env-token-env", "XIAOYOU_STATE_DIR": "/var/lib/xiaoyou",
            "XIAOYOU_BACKEND": "claude_code",
        })
        self.assertEqual((loaded.host, loaded.port), ("0.0.0.0", 9000))
        self.assertEqual(loaded.token, "env-token-env-token-env")
        self.assertEqual(loaded.state_dir, Path("/var/lib/xiaoyou"))
        self.assertEqual(loaded.backend, "claude_code")

    def test_placeholder_and_short_tokens_are_rejected(self):
        for token in (config_module.PLACEHOLDER_TOKEN, "short", ""):
            with self.assertRaises(config_module.ConfigError):
                self.load(server={"token": token})

    def test_bad_values_are_rejected(self):
        bad = [
            {"backend": "gpt"},
            {"server": {"token": TOKEN, "port": 70000}},
            {"server": {"token": TOKEN, "port": True}},
            {"brief_max_chars": 5},
            {"turn_timeout_seconds": 1},
            {"claude_code": {"command": []}},
            {"claude_code": {"allowed_tools": "Read"}},
            {"tools": [{"name": ""}]},
            {"tools": [{"name": "A"}, {"name": "A"}]},
            {"persona_file": "missing.md"},
        ]
        for overrides in bad:
            with self.assertRaises(config_module.ConfigError, msg=str(overrides)):
                self.load(**overrides)
        with self.assertRaises(config_module.ConfigError):
            self.load(env={"XIAOYOU_PORT": "abc"})
        with self.assertRaises(config_module.ConfigError):
            config_module.load(self.folder / "nope.json", {})

    def test_disabled_tools_are_dropped_and_rules_merged(self):
        loaded = self.load(
            claude_code={"allowed_tools": ["Read", "Bash(codex exec *)"]},
            tools=[
                {"name": "Codex", "description": "d", "allowed_tools": ["Bash(codex exec *)"]},
                {"name": "Off", "enabled": False, "allowed_tools": ["Bash(rm *)"]},
            ],
        )
        self.assertEqual([tool.name for tool in loaded.tools], ["Codex"])
        self.assertEqual(loaded.allowed_tools(), ["Read", "Bash(codex exec *)"])

    def test_example_config_is_valid_once_the_token_is_set(self):
        with self.assertRaises(config_module.ConfigError):
            config_module.load(RUNTIME / "config.example.json", {})
        loaded = config_module.load(RUNTIME / "config.example.json", {"XIAOYOU_TOKEN": TOKEN})
        self.assertEqual(loaded.backend, "claude_code")
        self.assertEqual(loaded.tools, [])  # the Codex example ships disabled
        self.assertNotIn("Bash(codex exec *)", loaded.allowed_tools())


class ShapeTests(unittest.TestCase):
    def test_clip_and_shape(self):
        self.assertEqual(backends.clip("a  b\n c", 10), "a b c")
        clipped = backends.clip("字" * 50, 20)
        self.assertEqual(len(clipped), 20)
        self.assertTrue(clipped.endswith("…"))
        turn = backends.shape("  hello  ", "", "angry", None, 20)
        self.assertEqual((turn.reply, turn.brief, turn.mood), ("hello", "hello", "idle"))


class ClaudeCodeBackendTests(TempDirCase):
    def setUp(self):
        super().setUp()
        fake = self.folder / "fake_claude.py"
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        self.log = self.folder / "calls.jsonl"
        os.environ["FAKE_CLAUDE_LOG"] = str(self.log)
        os.environ.pop("FAKE_CLAUDE_MODE", None)
        self.addCleanup(os.environ.pop, "FAKE_CLAUDE_LOG", None)
        self.addCleanup(os.environ.pop, "FAKE_CLAUDE_MODE", None)
        self.config = config_module.load(write_config(
            self.folder,
            backend="claude_code",
            claude_code={
                "command": [sys.executable, str(fake)], "model": "some-model",
                "allowed_tools": ["Read"], "extra_args": ["--max-turns", "9"],
            },
            tools=[{"name": "Codex", "description": "second opinion",
                    "allowed_tools": ["Bash(codex exec *)"]}],
        ), {})
        self.backend = backends.create(self.config)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text("utf-8").splitlines()]

    def test_first_turn_arguments_and_structured_output(self):
        turn = self.backend.turn("你好 -- $(x)", None)
        self.assertEqual((turn.reply, turn.brief, turn.mood, turn.session_id),
                         ("full 你好 -- $(x)", "short", "happy", "s-new"))
        call = self.calls()[0]
        args = call["args"]
        # The user's text travels on stdin only, never as an argument.
        self.assertEqual(call["stdin"], "你好 -- $(x)")
        self.assertNotIn("你好 -- $(x)", args)
        self.assertEqual(Path(call["cwd"]).resolve(), self.config.claude_workdir)
        self.assertEqual(args[:3], ["-p", "--output-format", "json"])
        prompt = args[args.index("--append-system-prompt") + 1]
        self.assertIn("PERSONA-MARKER", prompt)
        self.assertIn("Codex：second opinion", prompt)
        self.assertEqual(json.loads(args[args.index("--json-schema") + 1]), backends.REPLY_SCHEMA)
        self.assertEqual(args[args.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(args[args.index("--allowedTools") + 1], "Read,Bash(codex exec *)")
        self.assertEqual(args[args.index("--model") + 1], "some-model")
        self.assertNotIn("--resume", args)
        self.assertEqual(args[-2:], ["--max-turns", "9"])

    def test_resume_passes_the_previous_session(self):
        turn = self.backend.turn("again", "s-prev")
        args = self.calls()[0]["args"]
        self.assertEqual(args[args.index("--resume") + 1], "s-prev")
        self.assertEqual(turn.session_id, "s-prev")

    def test_plain_result_is_used_when_structured_output_is_missing(self):
        os.environ["FAKE_CLAUDE_MODE"] = "plain"
        turn = self.backend.turn("x" * 300, None)
        self.assertEqual(turn.reply, "plain " + "x" * 300)
        self.assertEqual(len(turn.brief), self.config.brief_max_chars)
        self.assertEqual(turn.mood, "idle")

    def test_failures_become_backend_errors(self):
        os.environ["FAKE_CLAUDE_MODE"] = "crash"
        with self.assertRaisesRegex(backends.BackendError, "not logged in"):
            self.backend.turn("x", None)
        os.environ["FAKE_CLAUDE_MODE"] = "error"
        with self.assertRaisesRegex(backends.BackendError, "usage limit reached"):
            self.backend.turn("x", None)

    def test_missing_command_is_explained(self):
        raw = json.loads((self.folder / "config.json").read_text("utf-8"))
        raw["claude_code"]["command"] = [str(self.folder / "no-such-claude")]
        (self.folder / "config.json").write_text(json.dumps(raw), encoding="utf-8")
        backend = backends.create(config_module.load(self.folder / "config.json", {}))
        with self.assertRaisesRegex(backends.BackendError, "no-such-claude"):
            backend.turn("x", None)

    def test_config_dir_is_passed_to_claude_code_only_when_set(self):
        seen = []

        def fake_run(command, **kwargs):
            seen.append(kwargs)
            return subprocess.CompletedProcess(command, 0, '{"result": "ok", "session_id": "s"}', "")

        plain = config_module.load(write_config(self.folder, backend="claude_code"), {})
        self.assertIsNone(plain.claude_config_dir)
        backends.ClaudeCodeBackend(plain, run=fake_run).turn("x", None)
        self.assertNotIn("env", seen[-1])

        separate = config_module.load(
            write_config(self.folder, backend="claude_code",
                         claude_code={"config_dir": "claude-home"}), {})
        self.assertEqual(separate.claude_config_dir, (self.folder / "claude-home").resolve())
        backends.ClaudeCodeBackend(separate, run=fake_run).turn("x", None)
        self.assertEqual(seen[-1]["env"]["CLAUDE_CONFIG_DIR"],
                         str((self.folder / "claude-home").resolve()))
        # 其余环境变量原样保留，否则连 PATH 都没有。
        self.assertEqual(seen[-1]["env"].get("PATH"), os.environ.get("PATH"))

        from_env = config_module.load(
            write_config(self.folder, backend="claude_code"),
            {"XIAOYOU_CLAUDE_CONFIG_DIR": str(self.folder / "other")})
        self.assertEqual(from_env.claude_config_dir, self.folder / "other")
        with self.assertRaisesRegex(config_module.ConfigError, "config_dir"):
            config_module.load(
                write_config(self.folder, backend="claude_code",
                             claude_code={"config_dir": " "}), {})

    def test_no_tools_prompt_and_no_allowed_tools_flag(self):
        loaded = config_module.load(write_config(self.folder, backend="claude_code"), {})
        backend = backends.ClaudeCodeBackend(loaded)
        self.assertNotIn("--allowedTools", backend.command(None))
        self.assertIn("没有配置额外的工具", backends.system_prompt(loaded))


class StoreTests(TempDirCase):
    def test_sessions_survive_a_restart(self):
        store = Store(self.folder / "state")
        self.assertIsNone(store.session("default"))
        store.remember("default", "s1")
        store.remember("work", "s2")
        again = Store(self.folder / "state")
        self.assertEqual((again.session("default"), again.session("work")), ("s1", "s2"))
        self.assertTrue(again.forget("work"))
        self.assertFalse(again.forget("work"))
        self.assertIsNone(Store(self.folder / "state").session("work"))

    def test_a_corrupt_file_is_not_silently_discarded(self):
        (self.folder / "sessions.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            Store(self.folder)


class RecordingBackend(backends.Backend):
    def __init__(self):
        self.calls = []
        self.gate = threading.Event()
        self.gate.set()
        self.fail_on = None

    def turn(self, text, session_id):
        self.gate.wait(5)
        self.calls.append((text, session_id))
        if text == self.fail_on:
            raise backends.BackendError("boom")
        if text == "explode":
            raise ValueError("unexpected")
        number = int(session_id[1:]) + 1 if session_id else 1
        return backends.Turn("re:" + text, "b:" + text, "happy", "s%d" % number)


class ServiceTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.backend = RecordingBackend()
        self.store = Store(self.folder)
        self.service = Service(self.backend, self.store)
        self.addCleanup(self.service.close)

    def finish(self, message):
        done = self.service.get(message["id"], wait=5)
        self.assertIn(done["status"], ("done", "failed"))
        return done

    def test_turns_run_in_order_and_continue_the_session(self):
        self.backend.gate.clear()
        first = self.service.submit("one")
        second = self.service.submit("two")
        self.assertEqual(first["status"], "queued")
        self.assertEqual(self.service.get(second["id"], wait=0.05)["status"], "queued")
        self.backend.gate.set()
        self.assertEqual(self.finish(second)["reply"], "re:two")
        self.assertEqual(self.backend.calls, [("one", None), ("two", "s1")])
        self.assertEqual(self.store.session("default"), "s2")
        done = self.finish(first)
        self.assertEqual((done["brief"], done["mood"], done["error"]), ("b:one", "happy", None))

    def test_conversations_are_independent_and_can_be_reset(self):
        self.finish(self.service.submit("a", "home"))
        self.finish(self.service.submit("b", "work"))
        self.assertTrue(self.service.reset("home"))
        self.finish(self.service.submit("c", "home"))
        self.assertEqual(self.backend.calls, [("a", None), ("b", None), ("c", None)])

    def test_same_client_id_is_not_run_twice(self):
        first = self.service.submit("pay", client_id="cmd-1")
        self.finish(first)
        again = self.service.submit("pay", client_id="cmd-1")
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(again["status"], "done")
        self.assertEqual(len(self.backend.calls), 1)

    def test_failure_is_reported_and_keeps_the_session_and_the_worker(self):
        self.finish(self.service.submit("ok"))
        self.backend.fail_on = "bad"
        failed = self.finish(self.service.submit("bad"))
        self.assertEqual((failed["status"], failed["error"], failed["mood"]), ("failed", "boom", "oops"))
        crashed = self.finish(self.service.submit("explode"))
        self.assertEqual(crashed["status"], "failed")
        self.assertIn("ValueError", crashed["error"])
        self.assertEqual(self.store.session("default"), "s1")
        self.assertEqual(self.finish(self.service.submit("after"))["status"], "done")

    def test_bad_requests(self):
        for args in (("",), ("   ",), (None,), ("x" * 8001,), ("hi", "a/b"), ("hi", ""),
                     ("hi", "default", "has space"), ("hi", 5)):
            with self.assertRaises(RequestError, msg=repr(args)[:60]):
                self.service.submit(*args)
        self.assertIsNone(self.service.get("nope"))


class HttpTests(TempDirCase):
    def setUp(self):
        super().setUp()
        loaded = config_module.load(
            write_config(self.folder), {"XIAOYOU_PORT": str(self._free_port())}
        )
        self.service = Service(backends.create(loaded), Store(loaded.state_dir))
        self.server = make_server(loaded, self.service)
        self.server.RequestHandlerClass.log_message = lambda *args: None
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.service.close)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    @staticmethod
    def _free_port():
        import socket
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    def call(self, method, path, body=None, token=TOKEN, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if token:
            request.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_health_needs_no_token_and_leaks_nothing(self):
        status, body = self.call("GET", "/healthz", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(body["backend"], "echo")
        self.assertNotIn(TOKEN, json.dumps(body))

    def test_everything_else_needs_the_token(self):
        for token in (None, "wrong-token-wrong-token"):
            self.assertEqual(self.call("POST", "/v1/messages", {"text": "hi"}, token=token)[0], 401)
            self.assertEqual(self.call("GET", "/v1/messages/abc", token=token)[0], 401)
            self.assertEqual(self.call("POST", "/v1/conversations/default/reset", {}, token=token)[0], 401)

    def test_send_then_long_poll_and_continue(self):
        status, message = self.call("POST", "/v1/messages", {"text": "你好", "client_id": "c1"})
        self.assertEqual(status, 202)
        status, done = self.call("GET", "/v1/messages/%s?wait=5" % message["id"])
        self.assertEqual((status, done["status"], done["reply"]), (200, "done", "（回声）你好"))
        self.assertEqual(done["mood"], "happy")
        # Same client_id: the same message comes back, already done.
        self.assertEqual(self.call("POST", "/v1/messages", {"text": "你好", "client_id": "c1"})[1]["id"],
                         message["id"])
        self.assertEqual(self.call("POST", "/v1/conversations/default/reset", {})[1], {"reset": True})
        self.assertEqual(self.call("POST", "/v1/conversations/default/reset", {})[1], {"reset": False})

    def test_bad_requests(self):
        self.assertEqual(self.call("POST", "/v1/messages", {"text": ""})[0], 400)
        self.assertEqual(self.call("POST", "/v1/messages", ["x"])[0], 400)
        self.assertEqual(self.call("POST", "/v1/messages", raw=b"{nope")[0], 400)
        self.assertEqual(self.call("POST", "/v1/messages", raw=b"x" * 70000)[0], 413)
        self.assertEqual(self.call("GET", "/v1/messages/unknown")[0], 404)
        self.assertEqual(self.call("GET", "/v1/messages/unknown?wait=999")[0], 400)
        self.assertEqual(self.call("GET", "/v1/messages/unknown?wait=nan")[0], 400)
        self.assertEqual(self.call("GET", "/v1/other")[0], 404)
        self.assertEqual(self.call("POST", "/v1/other", {})[0], 404)


class CommandLineTests(TempDirCase):
    def test_check_and_once(self):
        path = str(write_config(self.folder))
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["--config", path, "--check"]), 0)
            self.assertEqual(main(["--config", path, "--once", "hi"]), 0)
            self.assertEqual(main(["--config", path, "--once", "hi"]), 0)
            self.assertEqual(main(["--config", str(self.folder / "missing.json"), "--check"]), 2)
        self.assertEqual(Store(self.folder / "state").session("default"), "echo-2")
        self.assertIn("[happy]", out.getvalue())
        self.assertIn("missing.json", err.getvalue())
        self.assertNotIn(TOKEN, out.getvalue() + err.getvalue())


class SourceTests(unittest.TestCase):
    def test_sources_parse_as_python_3_9(self):
        for path in sorted((RUNTIME / "xiaoyou_runtime").glob("*.py")):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 9))

    def test_no_real_config_or_state_is_tracked(self):
        ignored = (RUNTIME / ".gitignore").read_text(encoding="utf-8").split()
        for name in ("config.json", "state/", "workdir/"):
            self.assertIn(name, ignored)
        example = json.loads((RUNTIME / "config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(example["server"]["token"], config_module.PLACEHOLDER_TOKEN)


if __name__ == "__main__":
    unittest.main()
