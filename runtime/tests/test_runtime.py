#!/usr/bin/env python3
"""Tests for the Xiaoyou runtime. Standard library only; no model is called.

The claude_code and codex agents are exercised against fake executables that
record their arguments and standard input, so these tests prove how the runtime
drives the command lines, not how the real Claude Code or Codex behave.
"""

import ast
import contextlib
import dataclasses
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNTIME))

from xiaoyou_runtime import agents as agents_module  # noqa: E402
from xiaoyou_runtime import config as config_module  # noqa: E402
from xiaoyou_runtime import router as router_module  # noqa: E402
from xiaoyou_runtime import stt as stt_module  # noqa: E402
from xiaoyou_runtime import xiaoyou as xiaoyou_module  # noqa: E402
from xiaoyou_runtime.__main__ import main  # noqa: E402
from xiaoyou_runtime.agents import AgentError, Job, Outcome  # noqa: E402
from xiaoyou_runtime.server import make_server  # noqa: E402
from xiaoyou_runtime.service import RequestError, Service  # noqa: E402
from xiaoyou_runtime.store import Store  # noqa: E402
from xiaoyou_runtime.xiaoyou import Xiaoyou  # noqa: E402

TOKEN = "unit-test-token-0123456789"
OTHER_TOKEN = "other-runtime-token-0123456789"

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
if mode == "ok" and "--json-schema" in args:
    out["structured_output"] = {"reply": "full " + text, "brief": "short", "mood": "happy"}
print(json.dumps(out))
'''

FAKE_CODEX = r'''
import json, os, sys
args = sys.argv[1:]
text = sys.stdin.read()
with open(os.environ["FAKE_CODEX_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"args": args, "stdin": text, "cwd": os.getcwd()}) + "\n")
mode = os.environ.get("FAKE_CODEX_MODE", "ok")
resumed = args[args.index("resume") + 1] if "resume" in args else None
print("some log line that is not JSON")
print(json.dumps({"type": "thread.started", "thread_id": resumed or "thread-1"}))
print(json.dumps({"type": "error", "message": "Reconnecting... 2/5"}))
if mode == "failed":
    print(json.dumps({"type": "turn.failed", "error": {"message": "quota exceeded"}}))
    sys.exit(1)
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "draft"}}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "final " + text}}))
if mode != "no-file":
    with open(args[args.index("-o") + 1], "w", encoding="utf-8") as handle:
        handle.write("written " + text + "\n")
'''


def write_config(folder: Path, **overrides):
    """A legacy-style config whose only agent is echo, unless overridden."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "persona.txt").write_text("PERSONA-MARKER\n", encoding="utf-8")
    raw = {
        "server": {"host": "127.0.0.1", "port": 8765, "token": TOKEN},
        "backend": "echo",
    }
    raw.update(overrides)
    if "agents" in raw:
        raw.pop("backend", None)
    path = folder / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def free_port():
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.folder = Path(self._temporary.name)


class Scripted(agents_module.Agent):
    """A stand-in agent: records every job; answers from a script, or by echoing."""

    def __init__(self, name, speaks=True, kind="echo", aliases=()):
        super().__init__(config_module.AgentSpec(
            name=name, type=kind, description=name + " helper", aliases=list(aliases),
            speaks=speaks, timeout_seconds=60,
        ))
        self.jobs = []
        self.gate = threading.Event()
        self.gate.set()
        self.fail_on = None
        self.script = []

    @property
    def texts(self):
        return [job.text for job in self.jobs]

    def run(self, job):
        if job.text.endswith("slow"):
            self.gate.wait(5)
        self.jobs.append(job)
        if self.fail_on is not None and self.fail_on in job.text:
            raise AgentError("boom")
        if job.text == "explode":
            raise ValueError("unexpected")
        session = "s%d" % (int(job.session_id[1:]) + 1 if job.session_id else 1)
        if self.script:
            fields = self.script.pop(0)
            return Outcome(fields.get("reply", ""), session, fields)
        if job.system is None:
            return Outcome("raw:" + job.text, session)
        return Outcome("re:" + job.text, session,
                       {"reply": "re:" + job.text, "brief": "b:" + job.text, "mood": "happy"})


def make_xiaoyou(folder, agents, router=None, **settings):
    """Xiaoyou over stand-in agents; the first one is the default and the voice."""
    loaded = config_module.load(write_config(folder), {})
    loaded = dataclasses.replace(
        loaded, default_agent=agents[0].name, voice_agent=agents[0].name, **settings)
    store = Store(loaded.state_dir)
    return Xiaoyou(loaded, agents, store, router), store


class ConfigTests(TempDirCase):
    def load(self, env=None, **overrides):
        return config_module.load(write_config(self.folder, **overrides), env or {})

    def test_defaults_and_relative_paths(self):
        loaded = self.load()
        self.assertEqual((loaded.host, loaded.port), ("127.0.0.1", 8765))
        self.assertEqual(loaded.persona, "PERSONA-MARKER")
        self.assertEqual(loaded.state_dir, (self.folder / "state").resolve())
        self.assertEqual([spec.name for spec in loaded.agents], ["echo"])
        self.assertEqual((loaded.default_agent, loaded.voice_agent), ("echo", "echo"))
        self.assertEqual((loaded.router_type, loaded.max_handoffs), ("mention", 2))

    def test_runtime_name(self):
        self.assertTrue(config_module.load(write_config(self.folder), {}).name)
        named = write_config(self.folder, server={"token": TOKEN, "name": " 书房的电脑 "})
        self.assertEqual(config_module.load(named, {}).name, "书房的电脑")
        self.assertEqual(config_module.load(named, {"XIAOYOU_NAME": "vm"}).name, "vm")
        with self.assertRaisesRegex(config_module.ConfigError, "server.name"):
            config_module.load(write_config(self.folder, server={"token": TOKEN, "name": "x" * 41}), {})

    def test_environment_overrides_file(self):
        loaded = self.load(env={
            "XIAOYOU_HOST": "0.0.0.0", "XIAOYOU_PORT": "9000",
            "XIAOYOU_TOKEN": "env-token-env-token-env", "XIAOYOU_STATE_DIR": "/var/lib/xiaoyou",
        })
        self.assertEqual((loaded.host, loaded.port), ("0.0.0.0", 9000))
        self.assertEqual(loaded.token, "env-token-env-token-env")
        self.assertEqual(loaded.state_dir, Path("/var/lib/xiaoyou"))

    def test_the_whole_chain_can_be_switched_to_echo_from_the_environment(self):
        agents = {"claude": {"type": "claude_code"}, "codex": {"type": "codex"}}
        for env in ({"XIAOYOU_BACKEND": "echo"}, {"XIAOYOU_DEFAULT_AGENT": "echo"}):
            loaded = self.load(env=env, agents=agents)
            self.assertEqual(loaded.default_agent, "echo")
            self.assertEqual([spec.name for spec in loaded.agents], ["claude", "codex", "echo"])
        self.assertEqual(self.load(env={"XIAOYOU_DEFAULT_AGENT": "codex"}, agents=agents).default_agent,
                         "codex")
        with self.assertRaisesRegex(config_module.ConfigError, "默认代理"):
            self.load(env={"XIAOYOU_DEFAULT_AGENT": "nobody"}, agents=agents)

    def test_placeholder_and_short_tokens_are_rejected(self):
        for token in (config_module.PLACEHOLDER_TOKEN, "short", ""):
            with self.assertRaises(config_module.ConfigError):
                self.load(server={"token": token})

    def test_bad_values_are_rejected(self):
        claude = {"type": "claude_code"}
        bad = [
            {"backend": "gpt"},
            {"server": {"token": TOKEN, "port": 70000}},
            {"server": {"token": TOKEN, "port": True}},
            {"brief_max_chars": 5},
            {"turn_timeout_seconds": 1},
            {"backend": "claude_code", "claude_code": {"command": []}},
            {"backend": "claude_code", "claude_code": {"allowed_tools": "Read"}},
            {"tools": [{"name": ""}]},
            {"persona_file": "missing.md"},
            {"agents": {}},
            {"agents": {"claude": dict(claude, enabled=False)}},
            {"agents": {"bad name": claude}},
            {"agents": {"claude": {"type": "gpt"}}},
            {"agents": {"claude": dict(claude, timeout_seconds=1)}},
            {"agents": {"claude": dict(claude, config_dir=" ")}},
            {"agents": {"claude": claude, "Claude": {"type": "echo"}}},
            {"agents": {"claude": claude, "b": {"type": "echo", "aliases": ["CLAUDE"]}}},
            {"agents": {"codex": {"type": "codex", "sandbox": "anything-goes"}}},
            {"agents": {"tool": {"type": "command"}}},
            {"agents": {"tool": {"type": "command", "command": ["x"], "model": "m"}}},
            {"agents": {"pc": {"type": "remote", "url": "ftp://x", "token": OTHER_TOKEN}}},
            {"agents": {"pc": {"type": "remote", "url": "http://x:1", "token": "short"}}},
            {"agents": {"pc": {"type": "remote", "url": "http://x:1", "token": OTHER_TOKEN,
                               "speaks": False}}},
            {"agents": {"claude": claude}, "xiaoyou": {"default_agent": "codex"}},
            {"agents": {"claude": claude}, "xiaoyou": {"voice_agent": "codex"}},
            {"agents": {"claude": claude, "codex": {"type": "codex"}},
             "xiaoyou": {"voice_agent": "codex"}},
            {"agents": {"claude": claude}, "xiaoyou": {"max_handoffs": 9}},
            {"agents": {"claude": claude}, "xiaoyou": {"router": {"type": "magic"}}},
            {"agents": {"claude": claude}, "xiaoyou": {"router": {"type": "command"}}},
            {"agents": {"claude": claude}, "claude_code": {}},
        ]
        for overrides in bad:
            with self.assertRaises(config_module.ConfigError, msg=str(overrides)):
                self.load(**overrides)
        with self.assertRaises(config_module.ConfigError):
            self.load(env={"XIAOYOU_PORT": "abc"})
        with self.assertRaises(config_module.ConfigError):
            config_module.load(self.folder / "nope.json", {})

    def test_agents_and_who_speaks(self):
        loaded = self.load(
            turn_timeout_seconds=300,
            agents={
                "codex": {"type": "codex", "description": " reviews code ", "aliases": ["科迪"]},
                "claude": {"type": "claude_code", "timeout_seconds": 120},
                "local": {"type": "command", "command": ["ollama", "run", "m"], "speaks": True},
                "off": {"type": "echo", "enabled": False},
                "pc": {"type": "remote", "url": "http://10.0.0.2:8765/", "token": OTHER_TOKEN},
            },
        )
        by_name = {spec.name: spec for spec in loaded.agents}
        self.assertEqual(list(by_name), ["codex", "claude", "local", "pc"])
        self.assertEqual({name: spec.speaks for name, spec in by_name.items()},
                         {"codex": False, "claude": True, "local": True, "pc": True})
        self.assertEqual((by_name["codex"].timeout_seconds, by_name["claude"].timeout_seconds),
                         (300, 120))
        self.assertEqual((by_name["codex"].description, by_name["codex"].aliases,
                          by_name["codex"].sandbox, by_name["codex"].command),
                         ("reviews code", ["科迪"], "read-only", ["codex"]))
        self.assertEqual(by_name["claude"].workdir, (self.folder / "workdir").resolve())
        self.assertEqual(by_name["pc"].url, "http://10.0.0.2:8765")
        # The first agent is the default; it only works, so the first one that speaks is the voice.
        self.assertEqual((loaded.default_agent, loaded.voice_agent), ("codex", "claude"))
        self.assertEqual(loaded.notices, [])

    def test_the_old_config_shape_still_loads_as_one_agent(self):
        loaded = self.load(
            backend="claude_code",
            claude_code={"command": ["claude"], "model": "m", "allowed_tools": ["Read"],
                         "config_dir": "claude-home"},
            tools=[{"name": "Off", "enabled": False, "allowed_tools": ["Bash(rm *)"]}],
        )
        self.assertEqual(len(loaded.agents), 1)
        claude = loaded.agents[0]
        self.assertEqual((claude.name, claude.type, claude.model, claude.allowed_tools, claude.speaks),
                         ("claude", "claude_code", "m", ["Read"], True))
        self.assertEqual(claude.config_dir, (self.folder / "claude-home").resolve())
        self.assertEqual(claude.permission_mode, "dontAsk")
        self.assertTrue(any("旧写法" in notice for notice in loaded.notices))
        # A tool that was switched on has to be moved by hand: it is now an agent of its own.
        with self.assertRaisesRegex(config_module.ConfigError, "agents"):
            self.load(backend="claude_code", tools=[{"name": "Codex", "allowed_tools": ["Bash(codex exec *)"]}])
        from_env = self.load(env={"XIAOYOU_CLAUDE_CONFIG_DIR": str(self.folder / "other")},
                             backend="claude_code")
        self.assertEqual(from_env.agents[0].config_dir, self.folder / "other")

    def test_example_config_is_valid_once_the_token_is_set(self):
        with self.assertRaises(config_module.ConfigError):
            config_module.load(RUNTIME / "config.example.json", {})
        loaded = config_module.load(RUNTIME / "config.example.json", {"XIAOYOU_TOKEN": TOKEN})
        # The Codex example ships disabled.
        self.assertEqual([(spec.name, spec.type) for spec in loaded.agents], [("claude", "claude_code")])
        self.assertEqual((loaded.default_agent, loaded.voice_agent), ("claude", "claude"))
        self.assertEqual(loaded.notices, [])
        example = json.loads((RUNTIME / "config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(example["agents"]["codex"]["enabled"], False)


class ShapeTests(unittest.TestCase):
    def test_clip_and_shape(self):
        self.assertEqual(agents_module.clip("a  b\n c", 10), "a b c")
        clipped = agents_module.clip("字" * 50, 20)
        self.assertEqual(len(clipped), 20)
        self.assertTrue(clipped.endswith("…"))
        turn = xiaoyou_module.shape("  hello  ", "", "angry", 20, "claude")
        self.assertEqual((turn.reply, turn.brief, turn.mood, turn.agent, turn.helpers),
                         ("hello", "hello", "idle", "claude", []))

    def test_a_structured_reply_written_as_text_is_recognised(self):
        fields = {"reply": "好", "brief": "好", "mood": "happy"}
        for text in (json.dumps(fields), "```json\n%s\n```" % json.dumps(fields),
                     "  %s \n" % json.dumps(fields)):
            self.assertEqual(agents_module.fields_from_text(text), fields)
        for text in ("just words", "{broken", '{"mood": "happy"}', '["reply"]', ""):
            self.assertIsNone(agents_module.fields_from_text(text))

    def test_the_reply_schema_offers_handoff_only_when_there_are_helpers(self):
        alone = xiaoyou_module.reply_schema([])
        self.assertNotIn("handoff", alone["properties"])
        helped = xiaoyou_module.reply_schema([Scripted("codex", speaks=False), Scripted("pc")])
        self.assertEqual(helped["properties"]["handoff"]["properties"]["agent"]["enum"], ["codex", "pc"])
        # Handing off is always optional.
        self.assertEqual(helped["required"], ["reply", "brief", "mood"])


class ClaudeCodeAgentTests(TempDirCase):
    def setUp(self):
        super().setUp()
        fake = self.folder / "fake_claude.py"
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        self.log = self.folder / "calls.jsonl"
        os.environ["FAKE_CLAUDE_LOG"] = str(self.log)
        os.environ.pop("FAKE_CLAUDE_MODE", None)
        self.addCleanup(os.environ.pop, "FAKE_CLAUDE_LOG", None)
        self.addCleanup(os.environ.pop, "FAKE_CLAUDE_MODE", None)
        self.config = config_module.load(write_config(self.folder, agents={"claude": {
            "type": "claude_code", "command": [sys.executable, str(fake)], "model": "some-model",
            "allowed_tools": ["Read", "Grep"], "extra_args": ["--max-turns", "9"],
        }}), {})
        self.agent = agents_module.create(self.config.agents[0])
        self.schema = xiaoyou_module.reply_schema([])

    def calls(self):
        return [json.loads(line) for line in self.log.read_text("utf-8").splitlines()]

    def test_speaking_as_xiaoyou_passes_the_persona_and_the_reply_shape(self):
        outcome = self.agent.run(Job("你好 -- $(x)", system="SYSTEM-TEXT", schema=self.schema))
        self.assertEqual((outcome.text, outcome.session_id), ("full 你好 -- $(x)", "s-new"))
        self.assertEqual(outcome.fields, {"reply": "full 你好 -- $(x)", "brief": "short", "mood": "happy"})
        call = self.calls()[0]
        args = call["args"]
        # The owner's text travels on stdin only, never as an argument.
        self.assertEqual(call["stdin"], "你好 -- $(x)")
        self.assertNotIn("你好 -- $(x)", args)
        self.assertEqual(Path(call["cwd"]).resolve(), self.config.agents[0].workdir)
        self.assertEqual(args[:3], ["-p", "--output-format", "json"])
        self.assertEqual(args[args.index("--append-system-prompt") + 1], "SYSTEM-TEXT")
        self.assertEqual(json.loads(args[args.index("--json-schema") + 1]), self.schema)
        self.assertEqual(args[args.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(args[args.index("--allowedTools") + 1], "Read,Grep")
        self.assertEqual(args[args.index("--model") + 1], "some-model")
        self.assertNotIn("--resume", args)
        self.assertEqual(args[-2:], ["--max-turns", "9"])

    def test_plain_work_gets_no_persona_and_returns_the_raw_result(self):
        outcome = self.agent.run(Job("count the files", session_id="s-prev"))
        self.assertEqual((outcome.text, outcome.session_id, outcome.fields),
                         ("plain count the files", "s-prev", None))
        args = self.calls()[0]["args"]
        self.assertNotIn("--append-system-prompt", args)
        self.assertNotIn("--json-schema", args)
        self.assertEqual(args[args.index("--resume") + 1], "s-prev")

    def test_plain_result_is_used_when_structured_output_is_missing(self):
        os.environ["FAKE_CLAUDE_MODE"] = "plain"
        outcome = self.agent.run(Job("x" * 300, system="S", schema=self.schema))
        self.assertEqual((outcome.text, outcome.fields), ("plain " + "x" * 300, None))

    def test_failures_become_agent_errors(self):
        os.environ["FAKE_CLAUDE_MODE"] = "crash"
        with self.assertRaisesRegex(AgentError, "not logged in"):
            self.agent.run(Job("x"))
        os.environ["FAKE_CLAUDE_MODE"] = "error"
        with self.assertRaisesRegex(AgentError, "usage limit reached"):
            self.agent.run(Job("x"))

    def test_missing_command_is_explained_at_start_and_when_used(self):
        spec = dataclasses.replace(self.config.agents[0], command=[str(self.folder / "no-such-claude")])
        missing = agents_module.create(spec)
        self.assertIn("no-such-claude", missing.check())
        with self.assertRaisesRegex(AgentError, "no-such-claude"):
            missing.run(Job("x"))
        self.assertIsNone(self.agent.check())

    def test_config_dir_is_passed_to_claude_code_only_when_set(self):
        seen = []

        def fake_run(command, **kwargs):
            seen.append(kwargs)
            return subprocess.CompletedProcess(command, 0, '{"result": "ok", "session_id": "s"}', "")

        plain = dataclasses.replace(self.config.agents[0], config_dir=None, allowed_tools=[])
        agent = agents_module.ClaudeCodeAgent(plain, run=fake_run)
        agent.run(Job("x"))
        self.assertNotIn("env", seen[-1])
        self.assertNotIn("--allowedTools", agent.command(Job("x")))

        separate = dataclasses.replace(plain, config_dir=self.folder / "claude-home")
        agents_module.ClaudeCodeAgent(separate, run=fake_run).run(Job("x"))
        self.assertEqual(seen[-1]["env"]["CLAUDE_CONFIG_DIR"], str(self.folder / "claude-home"))
        # 其余环境变量原样保留，否则连 PATH 都没有。
        self.assertEqual(seen[-1]["env"].get("PATH"), os.environ.get("PATH"))


class CodexAgentTests(TempDirCase):
    def setUp(self):
        super().setUp()
        fake = self.folder / "fake_codex.py"
        fake.write_text(FAKE_CODEX, encoding="utf-8")
        self.log = self.folder / "codex.jsonl"
        os.environ["FAKE_CODEX_LOG"] = str(self.log)
        os.environ.pop("FAKE_CODEX_MODE", None)
        self.addCleanup(os.environ.pop, "FAKE_CODEX_LOG", None)
        self.addCleanup(os.environ.pop, "FAKE_CODEX_MODE", None)
        self.config = config_module.load(write_config(self.folder, agents={"codex": {
            "type": "codex", "command": [sys.executable, str(fake)], "model": "some-model",
            "sandbox": "workspace-write", "extra_args": ["-c", "x=1"],
        }}), {})
        self.agent = agents_module.create(self.config.agents[0])

    def calls(self):
        return [json.loads(line) for line in self.log.read_text("utf-8").splitlines()]

    def test_a_task_runs_through_codex_exec(self):
        outcome = self.agent.run(Job("review -- $(x)"))
        # The last message Codex wrote to the -o file wins over the event stream.
        self.assertEqual((outcome.text, outcome.session_id, outcome.fields),
                         ("written review -- $(x)", "thread-1", None))
        call = self.calls()[0]
        args = call["args"]
        self.assertEqual(call["stdin"], "review -- $(x)")
        self.assertNotIn("review -- $(x)", args)
        self.assertEqual(Path(call["cwd"]).resolve(), self.config.agents[0].workdir)
        self.assertEqual(args[:3], ["exec", "--json", "--skip-git-repo-check"])
        self.assertEqual(args[args.index("--sandbox") + 1], "workspace-write")
        self.assertEqual(args[args.index("--model") + 1], "some-model")
        self.assertIn("x=1", args)
        self.assertNotIn("resume", args)
        self.assertEqual(args[-1], "-")
        # The file for the last message is a temporary one and is gone afterwards.
        self.assertFalse(Path(args[args.index("-o") + 1]).exists())

    def test_continuing_uses_exec_resume_with_the_thread(self):
        outcome = self.agent.run(Job("more", session_id="thread-7"))
        args = self.calls()[0]["args"]
        self.assertEqual(args[-3:], ["resume", "thread-7", "-"])
        # Options go before the subcommand: `codex exec resume` does not take --sandbox.
        self.assertLess(args.index("--sandbox"), args.index("resume"))
        self.assertEqual(outcome.session_id, "thread-7")

    def test_the_event_stream_is_used_when_no_file_was_written(self):
        os.environ["FAKE_CODEX_MODE"] = "no-file"
        self.assertEqual(self.agent.run(Job("q")).text, "final q")

    def test_failures_become_agent_errors(self):
        os.environ["FAKE_CODEX_MODE"] = "failed"
        with self.assertRaisesRegex(AgentError, "quota exceeded"):
            self.agent.run(Job("q"))
        spec = dataclasses.replace(self.config.agents[0], command=[str(self.folder / "no-codex")])
        with self.assertRaisesRegex(AgentError, "no-codex"):
            agents_module.create(spec).run(Job("q"))


class CommandAgentTests(TempDirCase):
    def agent(self, command, **extra):
        loaded = config_module.load(write_config(self.folder, agents={
            "tool": dict({"type": "command", "command": command}, **extra)}), {})
        return agents_module.create(loaded.agents[0])

    def test_the_text_goes_in_on_stdin_and_stdout_is_the_result(self):
        script = "import sys; print('got:' + sys.stdin.read().upper())"
        outcome = self.agent([sys.executable, "-c", script]).run(Job("hello"))
        self.assertEqual((outcome.text, outcome.session_id, outcome.fields), ("got:HELLO", None, None))

    def test_a_prompt_placeholder_replaces_stdin(self):
        script = "import sys; print(sys.argv[1] + '|' + repr(sys.stdin.read()))"
        outcome = self.agent([sys.executable, "-c", script, "<{prompt}>"]).run(Job("a b"))
        self.assertEqual(outcome.text, "<a b>|''")

    def test_a_command_that_speaks_gets_the_persona_and_may_answer_in_json(self):
        script = ("import sys, json; text = sys.stdin.read(); "
                  "print(json.dumps({'reply': str(len(text)), 'brief': 'n', 'mood': 'happy'}))")
        agent = self.agent([sys.executable, "-c", script], speaks=True)
        outcome = agent.run(Job("hello", system="PERSONA"))
        self.assertEqual(outcome.fields["reply"], str(len("PERSONA\n\nhello")))

    def test_failures_become_agent_errors(self):
        crash = "import sys; sys.stderr.write('model missing\\n'); sys.exit(3)"
        with self.assertRaisesRegex(AgentError, "model missing"):
            self.agent([sys.executable, "-c", crash]).run(Job("x"))
        with self.assertRaisesRegex(AgentError, "没有给出结果"):
            self.agent([sys.executable, "-c", "pass"]).run(Job("x"))
        slow = self.agent([sys.executable, "-c", "import time; time.sleep(30)"])
        slow = agents_module.CommandAgent(dataclasses.replace(slow.spec, timeout_seconds=1))
        with self.assertRaisesRegex(AgentError, "超过 1 秒"):
            slow.run(Job("x"))


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.agents = [Scripted("claude"), Scripted("codex", speaks=False, aliases=["科迪", "科迪克斯"])]

    def test_naming_an_agent_at_the_start_of_the_sentence(self):
        cases = {
            "@codex 看看这个": ("codex", "看看这个"),
            "＠Codex，看看这个": ("codex", "看看这个"),
            "@codex": ("codex", "@codex"),
            "@科迪克斯 看看": ("codex", "看看"),
            "让codex看看这个报错": ("codex", "让codex看看这个报错"),
            "问问 科迪 这段代码": ("codex", "问问 科迪 这段代码"),
            "交给Claude吧": ("claude", "交给Claude吧"),
            "  用 codex 跑一下": ("codex", "用 codex 跑一下"),
        }
        for text, expected in cases.items():
            self.assertEqual(router_module.mention(text, self.agents), expected, msg=text)
        for text in ("codex 是什么", "看看 @codex", "@codexes 看看", "@nobody 看看", "让我想想",
                     "用codexes试试", "问一下天气", ""):
            self.assertIsNone(router_module.mention(text, self.agents), msg=text)

    def test_the_order_of_decisions(self):
        class Fixed(router_module.Router):
            def __init__(self, choice):
                self.choice = choice
                self.asked = []

            def pick(self, text, agents, default):
                self.asked.append(text)
                return self.choice

        decide = router_module.decide
        quiet = router_module.Router()
        self.assertEqual(decide("hi", None, self.agents, "claude", quiet),
                         router_module.Route("claude", "hi", "default"))
        # Asked for by the caller beats a name in the sentence, which beats the router.
        loud = Fixed("claude")
        self.assertEqual(decide("@claude hi", "codex", self.agents, "claude", loud).agent, "codex")
        self.assertEqual(decide("@codex hi", None, self.agents, "claude", loud),
                         router_module.Route("codex", "hi", "mention"))
        self.assertEqual(loud.asked, [])
        self.assertEqual(decide("hi", None, self.agents, "claude", Fixed("codex")),
                         router_module.Route("codex", "hi", "router"))
        # A choice that is not available this turn falls back to the default.
        self.assertEqual(decide("hi", None, self.agents, "claude", Fixed("pc")).reason, "default")
        self.assertEqual(decide("hi", "pc", self.agents, "claude", quiet).reason, "default")

    def test_a_command_can_be_the_router(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            script = folder / "route.py"
            script.write_text(
                "import json, os, sys\n"
                "request = json.load(sys.stdin)\n"
                "open(os.path.join(os.path.dirname(__file__), 'seen.json'), 'w').write(json.dumps(request))\n"
                "text = request['text']\n"
                "if 'crash' in text: sys.exit(2)\n"
                "print('CODEX' if 'code' in text else ('nobody' if 'who' in text else ''))\n",
                encoding="utf-8")
            notes = []
            router = router_module.CommandRouter([sys.executable, str(script)], folder, 10, notes.append)
            self.assertIsNone(router.check())
            self.assertEqual(router.pick("fix this code", self.agents, "claude"), "codex")
            seen = json.loads((folder / "seen.json").read_text("utf-8"))
            self.assertEqual((seen["text"], seen["default"]), ("fix this code", "claude"))
            self.assertEqual(seen["agents"][1],
                             {"name": "codex", "type": "echo", "description": "codex helper"})
            # No opinion, an unknown name or a broken router all mean "the default agent".
            self.assertIsNone(router.pick("hello", self.agents, "claude"))
            self.assertEqual(notes, [])
            self.assertIsNone(router.pick("who?", self.agents, "claude"))
            self.assertIsNone(router.pick("crash", self.agents, "claude"))
            self.assertEqual(len(notes), 2)
            missing = router_module.CommandRouter([str(folder / "no-router")], folder, 10, notes.append)
            self.assertIn("no-router", missing.check())
            self.assertIsNone(missing.pick("hello", self.agents, "claude"))


class StoreTests(TempDirCase):
    def test_sessions_are_kept_per_agent_and_survive_a_restart(self):
        store = Store(self.folder / "state")
        self.assertIsNone(store.session("default", "claude"))
        store.remember("default", "claude", "s1")
        store.remember("default", "codex", "t1")
        store.remember("work", "claude", "s2")
        again = Store(self.folder / "state")
        self.assertEqual((again.session("default", "claude"), again.session("default", "codex"),
                          again.session("work", "claude"), again.session("work", "codex")),
                         ("s1", "t1", "s2", None))
        self.assertTrue(again.forget("default"))
        self.assertFalse(again.forget("default"))
        fresh = Store(self.folder / "state")
        self.assertIsNone(fresh.session("default", "codex"))
        self.assertEqual(fresh.session("work", "claude"), "s2")

    def test_sessions_from_before_agents_existed_go_to_the_default_agent(self):
        (self.folder / "sessions.json").write_text('{"default": "old-session"}', encoding="utf-8")
        store = Store(self.folder, legacy_agent="claude")
        self.assertEqual(store.session("default", "claude"), "old-session")
        self.assertIsNone(store.session("default", "codex"))

    def test_a_corrupt_file_is_not_silently_discarded(self):
        (self.folder / "sessions.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            Store(self.folder)

    def test_the_transcript_knows_who_has_seen_what(self):
        transcript = Store(self.folder).transcript
        transcript.add("default", "t1", "问1", "答1", "claude", ["claude"])
        transcript.add("default", "t2", "问2", "答2", "codex", ["codex", "claude", "codex"])
        self.assertEqual([turn["id"] for turn in transcript.unseen("default", "codex")], ["t1"])
        self.assertEqual(transcript.unseen("default", "claude"), [])
        self.assertEqual([turn["id"] for turn in transcript.unseen("default", "local")], ["t1", "t2"])
        transcript.mark("default", "codex", ["t1", "missing"])
        again = Store(self.folder).transcript
        self.assertEqual(again.unseen("default", "codex"), [])
        self.assertEqual([(turn["by"], turn["seen"]) for turn in again.turns("default")],
                         [("claude", ["claude", "codex"]), ("codex", ["claude", "codex"])])
        self.assertEqual(again.unseen("other", "codex"), [])
        self.assertTrue(again.clear("default"))
        self.assertFalse(again.clear("default"))
        # Cleared turns are still recognised when the phone offers them again.
        self.assertEqual(again.offer("default", [{"id": "t1", "text": "问1", "reply": "答1", "at": 1}]), 0)

    def test_carried_turns_waiting_from_the_previous_version_are_kept(self):
        (self.folder / "shared.json").write_text(json.dumps({"default": {
            "known": ["a"], "pending": [{"id": "b", "text": "问", "reply": "答", "at": 5}]}}), "utf-8")
        transcript = Store(self.folder).transcript
        self.assertEqual([turn["id"] for turn in transcript.unseen("default", "claude")], ["b"])
        self.assertEqual(transcript.offer("default", [{"id": "a", "text": "x", "reply": "y", "at": 1}]), 0)


class XiaoyouTests(TempDirCase):
    """Xiaoyou is her own identity: she routes, hands work over and reports back."""

    def setUp(self):
        super().setUp()
        self.claude = Scripted("claude")
        self.codex = Scripted("codex", speaks=False, aliases=["科迪"])
        self.xiaoyou, self.store = make_xiaoyou(self.folder, [self.claude, self.codex])
        self.events = []
        self.count = 0

    def ask(self, text, conversation="default", **extra):
        self.count += 1
        return self.xiaoyou.answer(
            text, conversation, "turn-%d" % self.count,
            report=lambda kind, agent, detail: self.events.append((kind, agent, detail)), **extra)

    def test_she_answers_herself_when_she_can(self):
        turn = self.ask("几点了")
        self.assertEqual((turn.reply, turn.brief, turn.mood, turn.agent, turn.helpers),
                         ("re:几点了", "b:几点了", "happy", "claude", []))
        job = self.claude.jobs[0]
        self.assertEqual((job.text, job.session_id, job.conversation), ("几点了", None, "default"))
        self.assertIn("PERSONA-MARKER", job.system)
        # She is told who she can hand work to, and the reply shape lets her do it.
        self.assertIn("codex：codex helper", job.system)
        self.assertEqual(job.schema["properties"]["handoff"]["properties"]["agent"]["enum"], ["codex"])
        self.assertEqual(self.codex.jobs, [])
        self.assertEqual(self.events, [("route", "claude", "default")])
        self.assertEqual(self.store.session("default", "claude"), "s1")
        self.assertEqual(self.ask("再问").agent, "claude")
        self.assertEqual(self.claude.jobs[1].session_id, "s1")

    def test_she_hands_work_to_a_helper_and_reports_back_in_her_own_words(self):
        self.claude.script = [
            {"reply": "我让 codex 看看", "brief": "去问了", "mood": "busy",
             "handoff": {"agent": "codex", "task": "review retry() in retry.py"}},
            {"reply": "codex 说第 42 行有问题", "brief": "第 42 行", "mood": "happy"},
        ]
        turn = self.ask("帮我看看 retry 的问题")
        self.assertEqual((turn.reply, turn.brief, turn.mood, turn.agent, turn.helpers),
                         ("codex 说第 42 行有问题", "第 42 行", "happy", "claude", ["codex"]))
        # The helper gets the task only: no persona, no chat.
        work = self.codex.jobs[0]
        self.assertEqual((work.text, work.system, work.schema), ("review retry() in retry.py", None, None))
        # Its raw result goes back to her, in the same session, not to the owner.
        back = self.claude.jobs[1]
        self.assertEqual(back.session_id, "s1")
        self.assertIn("raw:review retry() in retry.py", back.text)
        self.assertIn("帮手 codex 做完了", back.text)
        self.assertEqual(self.events, [
            ("route", "claude", "default"),
            ("handoff", "codex", "我让 codex 看看"),
            ("result", "codex", "codex 做完了"),
        ])
        self.assertEqual((self.store.session("default", "claude"), self.store.session("default", "codex")),
                         ("s2", "s1"))
        recorded = self.store.transcript.turns("default")[0]
        self.assertEqual((recorded["text"], recorded["reply"], recorded["by"], recorded["seen"]),
                         ("帮我看看 retry 的问题", "codex 说第 42 行有问题", "claude", ["claude"]))

    def test_a_helper_that_fails_or_does_not_exist_is_reported_to_her_not_hidden(self):
        self.codex.fail_on = "task"
        self.claude.script = [
            {"reply": "", "brief": "", "mood": "busy", "handoff": {"agent": "codex", "task": "the task"}},
            {"reply": "没做成", "brief": "没做成", "mood": "oops"},
        ]
        turn = self.ask("做件事")
        self.assertEqual((turn.reply, turn.mood, turn.helpers), ("没做成", "oops", ["codex"]))
        self.assertIn("帮手 codex 没做成：boom", self.claude.jobs[1].text)
        self.assertEqual(self.events[1:], [("handoff", "codex", "交给 codex 了"),
                                           ("result", "codex", "codex 没做成")])

        self.claude.script = [
            {"reply": "", "brief": "", "mood": "busy", "handoff": {"agent": "gemini", "task": "x"}},
            {"reply": "找不到这个帮手", "brief": "找不到", "mood": "oops"},
        ]
        turn = self.ask("再做一件")
        self.assertEqual((turn.reply, turn.helpers), ("找不到这个帮手", []))
        self.assertIn("没有叫 gemini 的帮手", self.claude.jobs[-1].text)
        self.assertEqual(len(self.codex.jobs), 1)

    def test_handing_off_cannot_go_on_for_ever(self):
        again = {"reply": "再找一次", "brief": "", "mood": "busy",
                 "handoff": {"agent": "codex", "task": "again"}}
        self.claude.script = [dict(again), dict(again), dict(again), dict(again)]
        turn = self.ask("绕圈")
        # Two hand-offs are allowed; the last report back no longer offers the option.
        self.assertEqual(len(self.codex.jobs), 2)
        self.assertEqual(len(self.claude.jobs), 3)
        self.assertIn("handoff", self.claude.jobs[1].schema["properties"])
        self.assertNotIn("handoff", self.claude.jobs[2].schema["properties"])
        self.assertIn("现在没有别的帮手", self.claude.jobs[2].system)
        self.assertEqual((turn.reply, turn.helpers), ("再找一次", ["codex", "codex"]))

        never, _ = make_xiaoyou(self.folder / "b", [Scripted("claude"), self.codex], max_handoffs=0)
        lead = never.agents()[0]
        never.answer("hi", "default", "t")
        self.assertNotIn("handoff", lead.jobs[0].schema["properties"])

    def test_naming_a_helper_sends_the_work_there_and_she_still_does_the_talking(self):
        self.ask("我们在聊重试逻辑")
        turn = self.ask("@codex 看看 parse()")
        # The reply is hers (the stand-in voices it as "re:" + what it was told), not the raw result.
        self.assertEqual((turn.agent, turn.reply), ("codex", "re:" + self.claude.jobs[-1].text))
        work = self.codex.jobs[0]
        # The helper was not there for the earlier turn, so it gets it as background.
        self.assertIn("我们在聊重试逻辑", work.text)
        self.assertTrue(work.text.endswith("看看 parse()"))
        self.assertIsNone(work.system)
        told = self.claude.jobs[-1].text
        self.assertIn("主人点名让 codex 做这件事，原话是：@codex 看看 parse()", told)
        self.assertIn("raw:", told)
        self.assertEqual(self.claude.jobs[-1].schema, xiaoyou_module.reply_schema([]))
        self.assertEqual(self.events[-3:], [("route", "codex", "mention"),
                                            ("handoff", "codex", "交给 codex 了"),
                                            ("result", "codex", "codex 做完了")])
        self.assertEqual(self.store.transcript.turns("default")[1]["seen"], ["claude", "codex"])
        # Next time the helper already knows that turn.
        self.ask("让科迪再看看")
        self.assertNotIn("我们在聊重试逻辑", self.codex.jobs[1].text)
        self.assertEqual(self.codex.jobs[1].session_id, "s1")

    def test_the_result_still_reaches_the_owner_when_nobody_can_voice_it(self):
        self.claude.fail_on = "帮手 codex 做完了"
        turn = self.ask("@codex list files")
        self.assertEqual((turn.agent, turn.reply, turn.mood), ("codex", "raw:list files", "idle"))
        self.assertEqual(self.events[-1][0], "note")
        # The helper itself failing is a failure of the turn.
        self.codex.fail_on = "again"
        with self.assertRaisesRegex(AgentError, "boom"):
            self.ask("@codex again")
        self.assertEqual(len(self.store.transcript.turns("default")), 1)

    def test_an_agent_that_missed_some_turns_is_caught_up_once(self):
        local = Scripted("local")
        local.script = [{"reply": "记住了", "brief": "记住了", "mood": "happy"}]
        xiaoyou, store = make_xiaoyou(self.folder / "c", [self.claude, local])
        xiaoyou.answer("我叫小明", "default", "t1")
        xiaoyou.answer("记住了吗", "default", "t2", asked="local")
        caught_up = local.jobs[0].text
        self.assertIn("主人：我叫小明", caught_up)
        self.assertIn("小幽：re:我叫小明", caught_up)
        self.assertTrue(caught_up.endswith("记住了吗"))
        xiaoyou.answer("再说一遍", "default", "t3", asked="local")
        self.assertEqual(local.jobs[1].text, "再说一遍")
        # And the first agent hears what happened while the other one was answering.
        xiaoyou.answer("回来了", "default", "t4")
        self.assertIn("主人：记住了吗\n小幽：记住了", self.claude.jobs[-1].text)
        self.assertNotIn("我叫小明", self.claude.jobs[-1].text)
        # The owner's own words are recorded as said, without the catch-up.
        self.assertEqual([turn["text"] for turn in store.transcript.turns("default")],
                         ["我叫小明", "记住了吗", "再说一遍", "回来了"])

    def test_a_failed_turn_keeps_what_was_waiting_to_be_told(self):
        self.xiaoyou.share("default", [{"id": "o1", "text": "别处问的", "reply": "别处答的", "at": 1}])
        self.claude.fail_on = "试一次"
        with self.assertRaises(AgentError):
            self.ask("试一次")
        self.claude.fail_on = None
        self.ask("再试")
        self.assertIn("别处问的", self.claude.jobs[-1].text)

    def test_starting_over_forgets_every_agents_session(self):
        self.ask("@codex one")
        self.assertEqual((self.store.session("default", "claude"), self.store.session("default", "codex")),
                         ("s1", "s1"))
        self.assertTrue(self.xiaoyou.reset("default"))
        self.assertFalse(self.xiaoyou.reset("default"))
        self.ask("@codex two")
        self.assertIsNone(self.codex.jobs[-1].session_id)
        self.assertEqual(self.codex.jobs[-1].text, "two")

    def test_words_passed_on_by_another_runtime_are_not_passed_on_again(self):
        other = Scripted("pc", kind="remote")
        xiaoyou, _ = make_xiaoyou(self.folder / "d", [self.claude, other])
        xiaoyou.answer("hi", "default", "t1")
        self.assertIn("pc：pc helper", self.claude.jobs[-1].system)
        xiaoyou.answer("hi", "default", "t2", hop=1)
        self.assertEqual(self.claude.jobs[-1].hop, 1)
        self.assertNotIn("pc helper", self.claude.jobs[-1].system)
        # Even when it is named.
        self.assertEqual(xiaoyou.answer("@pc hi", "default", "t3", hop=1).agent, "claude")
        self.assertEqual(xiaoyou.answer("hi", "default", "t4", asked="pc", hop=1).agent, "claude")
        self.assertEqual(other.jobs, [])
        only_remote, _ = make_xiaoyou(self.folder / "e", [other])
        with self.assertRaises(AgentError):
            only_remote.answer("hi", "default", "t", hop=1)


class ServiceTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.agent = Scripted("claude")
        self.codex = Scripted("codex", speaks=False)
        xiaoyou, self.store = make_xiaoyou(self.folder, [self.agent, self.codex])
        self.service = Service(xiaoyou, self.store)
        self.addCleanup(self.service.close)
        self.addCleanup(self.agent.gate.set)

    def finish(self, message):
        done = self.service.get(message["id"], wait=5)
        self.assertIn(done["status"], ("done", "failed"))
        return done

    def test_turns_run_in_order_and_continue_the_session(self):
        self.agent.gate.clear()
        first = self.service.submit("one slow")
        second = self.service.submit("two")
        self.assertEqual(first["status"], "queued")
        self.assertEqual(self.service.get(second["id"], wait=0.05)["status"], "queued")
        self.agent.gate.set()
        self.assertEqual(self.finish(second)["reply"], "re:two")
        self.assertEqual([(job.text, job.session_id) for job in self.agent.jobs],
                         [("one slow", None), ("two", "s1")])
        self.assertEqual(self.store.session("default", "claude"), "s2")
        done = self.finish(first)
        self.assertEqual((done["brief"], done["mood"], done["error"], done["agent"]),
                         ("b:one slow", "happy", None, "claude"))

    def test_one_long_conversation_does_not_hold_up_another(self):
        self.agent.gate.clear()
        stuck = self.service.submit("slow", "work")
        quick = self.finish(self.service.submit("quick", "walk"))
        self.assertEqual(quick["reply"], "re:quick")
        self.assertEqual(self.service.get(stuck["id"])["status"], "running")
        self.agent.gate.set()
        self.assertEqual(self.finish(stuck)["status"], "done")

    def test_conversations_are_independent_and_can_be_reset(self):
        self.finish(self.service.submit("a", "home"))
        self.finish(self.service.submit("b", "work"))
        self.assertTrue(self.service.reset("home"))
        self.finish(self.service.submit("c", "home"))
        self.assertEqual([(job.text, job.session_id) for job in self.agent.jobs],
                         [("a", None), ("b", None), ("c", None)])

    def test_same_client_id_is_not_run_twice(self):
        first = self.service.submit("pay", client_id="cmd-1")
        self.finish(first)
        again = self.service.submit("pay", client_id="cmd-1")
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(again["status"], "done")
        self.assertEqual(len(self.agent.jobs), 1)

    def test_a_message_shows_who_is_working_on_it(self):
        self.agent.script = [
            {"reply": "我去问 codex", "brief": "", "mood": "busy",
             "handoff": {"agent": "codex", "task": "check slow"}},
            {"reply": "好了", "brief": "好了", "mood": "happy"},
        ]
        self.codex.gate.clear()
        self.addCleanup(self.codex.gate.set)
        message = self.service.submit("帮我查一下")
        for _ in range(100):
            waiting = self.service.get(message["id"], wait=0.05)
            if waiting["agent"] == "codex":
                break
        self.assertEqual((waiting["status"], waiting["agent"], waiting["stage"]),
                         ("running", "codex", "我去问 codex"))
        self.codex.gate.set()
        done = self.finish(message)
        self.assertEqual((done["status"], done["reply"], done["agent"], done["stage"]),
                         ("done", "好了", "claude", None))
        self.assertEqual([(event["kind"], event["agent"]) for event in done["events"]],
                         [("route", "claude"), ("handoff", "codex"), ("result", "codex")])
        # A caller may name the agent; one that does not exist is refused up front.
        named = self.finish(self.service.submit("list", agent="codex"))
        self.assertEqual((named["asked"], named["agent"]), ("codex", "codex"))
        with self.assertRaisesRegex(RequestError, "gemini"):
            self.service.submit("hi", agent="gemini")
        self.assertEqual([agent["name"] for agent in self.service.agents()], ["claude", "codex"])
        self.assertEqual([agent["default"] for agent in self.service.agents()], [True, False])

    def test_failure_is_reported_and_keeps_the_session_and_the_worker(self):
        self.finish(self.service.submit("ok"))
        self.agent.fail_on = "bad"
        failed = self.finish(self.service.submit("bad"))
        self.assertEqual((failed["status"], failed["error"], failed["mood"]), ("failed", "boom", "oops"))
        crashed = self.finish(self.service.submit("explode"))
        self.assertEqual(crashed["status"], "failed")
        self.assertIn("ValueError", crashed["error"])
        self.assertEqual(self.store.session("default", "claude"), "s1")
        self.assertEqual(self.finish(self.service.submit("after"))["status"], "done")

    def test_bad_requests(self):
        for args in (("",), ("   ",), (None,), ("x" * 8001,), ("hi", "a/b"), ("hi", ""),
                     ("hi", "default", "has space"), ("hi", 5), ("hi", "default", None, "a b"),
                     ("hi", "default", None, None, 9), ("hi", "default", None, None, True),
                     ("hi", "default", None, None, "1")):
            with self.assertRaises(RequestError, msg=repr(args)[:60]):
                self.service.submit(*args)
        self.assertIsNone(self.service.get("nope"))


class SharedHistoryTests(TempDirCase):
    """Turns that happened on another runtime are carried over by the phone."""

    def setUp(self):
        super().setUp()
        self.agent = Scripted("claude")
        xiaoyou, self.store = make_xiaoyou(self.folder, [self.agent])
        self.service = Service(xiaoyou, self.store)
        self.addCleanup(self.service.close)

    def done(self, text):
        return self.service.get(self.service.submit(text)["id"], wait=5)

    @staticmethod
    def turn(number, at=None):
        return {"id": "other-%d" % number, "text": "问题%d" % number,
                "reply": "回答%d" % number, "at": at if at is not None else number}

    def test_carried_turns_reach_the_model_once_with_the_next_message(self):
        self.assertEqual(self.service.share("default", [self.turn(2), self.turn(1)]), 2)
        self.assertEqual(self.agent.jobs, [])  # nothing is sent until the owner speaks
        first = self.done("接着说")
        prompt = self.agent.texts[0]
        self.assertTrue(prompt.endswith("接着说"))
        # Oldest first, whatever order they arrived in.
        self.assertLess(prompt.index("问题1"), prompt.index("回答1"))
        self.assertLess(prompt.index("回答1"), prompt.index("问题2"))
        # The owner's own words are stored as typed, without the recap.
        self.assertEqual(first["text"], "接着说")
        self.done("再来")
        self.assertEqual(self.agent.texts[1], "再来")
        # Offering the same turns again, or a turn this runtime answered itself, adds nothing.
        own = {"id": first["id"], "text": "接着说", "reply": first["reply"], "at": 9}
        self.assertEqual(self.service.share("default", [self.turn(1), self.turn(2), own]), 0)
        self.assertEqual(self.service.share("default", [self.turn(3), self.turn(3), own]), 1)
        self.done("第三句")
        self.assertIn("问题3", self.agent.texts[2])
        self.assertNotIn("问题1", self.agent.texts[2])

    def test_conversations_do_not_mix(self):
        self.service.share("work", [self.turn(1)])
        self.done("家里的事")
        self.assertEqual(self.agent.texts[0], "家里的事")
        self.service.get(self.service.submit("工作的事", "work")["id"], wait=5)
        self.assertIn("问题1", self.agent.texts[-1])

    def test_survives_a_restart_and_reset_drops_what_was_not_said(self):
        self.service.share("default", [self.turn(1)])
        self.service.close()
        xiaoyou, _ = make_xiaoyou(self.folder, [self.agent])
        service = Service(xiaoyou, Store(self.folder / "state"))
        self.addCleanup(service.close)
        self.assertEqual(service.share("default", [self.turn(1)]), 0)
        service.reset("default")
        service.get(service.submit("新的开始")["id"], wait=5)
        self.assertEqual(self.agent.texts[-1], "新的开始")
        self.assertEqual(service.share("default", [self.turn(1)]), 0)

    def test_long_histories_are_trimmed_to_the_most_recent(self):
        turns = [{"id": "t%d" % n, "text": "问" * 3000, "reply": "答%d" % n + "x" * 3000, "at": n}
                 for n in range(30)]
        self.assertEqual(self.service.share("default", turns), 30)
        self.done("好")
        prompt = self.agent.texts[0]
        self.assertLess(len(prompt), 12000)
        self.assertIn("答29", prompt)
        self.assertNotIn("答0x", prompt)
        # What did not fit is not told later either: it would arrive out of order.
        self.done("然后呢")
        self.assertEqual(self.agent.texts[1], "然后呢")

    def test_bad_offers_are_refused(self):
        good = self.turn(1)
        for turns in (None, "x", [1], [{"id": "a"}], [dict(good, id="a b")], [dict(good, text=" ")],
                      [dict(good, reply=3)], [dict(good, at=True)], [good] * 31):
            with self.assertRaises(RequestError):
                self.service.share("default", turns)
        with self.assertRaises(RequestError):
            self.service.share("bad name", [good])


def start_runtime(case, folder, env=None, **overrides):
    """A whole runtime on a free port, built the way the command line builds it."""
    environment = {"XIAOYOU_PORT": str(free_port())}
    environment.update(env or {})
    loaded = config_module.load(write_config(folder, **overrides), environment)
    store = Store(loaded.state_dir, legacy_agent=loaded.default_agent)
    xiaoyou = Xiaoyou(loaded, [agents_module.create(spec) for spec in loaded.agents], store)
    service = Service(xiaoyou, store, stt_module.create(loaded))
    server = make_server(loaded, service)
    server.RequestHandlerClass.log_message = lambda *args: None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    case.addCleanup(service.close)
    case.addCleanup(server.server_close)
    case.addCleanup(server.shutdown)
    return "http://127.0.0.1:%d" % server.server_address[1], loaded


class HttpTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.base, self.config = start_runtime(self, self.folder)

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
        self.assertTrue(body["name"])
        self.assertEqual(sorted(body), ["backend", "name", "ok", "version"])
        self.assertNotIn(TOKEN, json.dumps(body))

    def test_everything_else_needs_the_token(self):
        for token in (None, "wrong-token-wrong-token"):
            self.assertEqual(self.call("POST", "/v1/messages", {"text": "hi"}, token=token)[0], 401)
            self.assertEqual(self.call("GET", "/v1/messages/abc", token=token)[0], 401)
            self.assertEqual(self.call("GET", "/v1/agents", token=token)[0], 401)
            self.assertEqual(self.call("POST", "/v1/conversations/default/reset", {}, token=token)[0], 401)

    def test_send_then_long_poll_and_continue(self):
        status, message = self.call("POST", "/v1/messages", {"text": "你好", "client_id": "c1"})
        self.assertEqual(status, 202)
        status, done = self.call("GET", "/v1/messages/%s?wait=5" % message["id"])
        self.assertEqual((status, done["status"], done["reply"]), (200, "done", "（回声）你好"))
        self.assertEqual((done["mood"], done["agent"]), ("happy", "echo"))
        # Same client_id: the same message comes back, already done.
        self.assertEqual(self.call("POST", "/v1/messages", {"text": "你好", "client_id": "c1"})[1]["id"],
                         message["id"])
        self.assertEqual(self.call("POST", "/v1/conversations/default/reset", {})[1], {"reset": True})
        self.assertEqual(self.call("POST", "/v1/conversations/default/reset", {})[1], {"reset": False})

    def test_the_agents_are_listed_and_can_be_asked_for(self):
        status, body = self.call("GET", "/v1/agents")
        self.assertEqual((status, body["default"]), (200, "echo"))
        self.assertEqual(body["agents"], [{
            "name": "echo", "type": "echo", "description": "原样复述，用来测试链路",
            "speaks": True, "default": True,
        }])
        message = self.call("POST", "/v1/messages", {"text": "hi", "agent": "echo"})[1]
        done = self.call("GET", "/v1/messages/%s?wait=5" % message["id"])[1]
        self.assertEqual((done["asked"], done["events"][0]["text"]), ("echo", "asked"))
        status, body = self.call("POST", "/v1/messages", {"text": "hi", "agent": "codex"})
        self.assertEqual(status, 400)
        self.assertIn("codex", body["error"])

    def test_history_is_carried_in_over_http(self):
        turns = [{"id": "elsewhere-1", "text": "我叫什么", "reply": "你叫小明", "at": 1}]
        self.assertEqual(self.call("POST", "/v1/conversations/default/history", {"turns": turns}),
                         (200, {"accepted": 1}))
        self.assertEqual(self.call("POST", "/v1/conversations/default/history", {"turns": turns}),
                         (200, {"accepted": 0}))
        message = self.call("POST", "/v1/messages", {"text": "继续"})[1]
        done = self.call("GET", "/v1/messages/%s?wait=5" % message["id"])[1]
        # The echo agent repeats what it was given, so the carried turn shows up in its reply.
        self.assertIn("你叫小明", done["reply"])
        self.assertEqual(done["text"], "继续")
        self.assertEqual(self.call("POST", "/v1/conversations/default/history", {"turns": "x"})[0], 400)
        self.assertEqual(self.call("POST", "/v1/conversations/default/history", ["x"])[0], 400)
        self.assertEqual(
            self.call("POST", "/v1/conversations/default/history", {"turns": turns}, token=None)[0], 401)

    def test_bad_requests(self):
        self.assertEqual(self.call("POST", "/v1/messages", {"text": ""})[0], 400)
        self.assertEqual(self.call("POST", "/v1/messages", {"text": "hi", "hop": 9})[0], 400)
        self.assertEqual(self.call("POST", "/v1/messages", ["x"])[0], 400)
        self.assertEqual(self.call("POST", "/v1/messages", raw=b"{nope")[0], 400)
        self.assertEqual(self.call("POST", "/v1/messages", raw=b"x" * 70000)[0], 413)
        self.assertEqual(self.call("GET", "/v1/messages/unknown")[0], 404)
        self.assertEqual(self.call("GET", "/v1/messages/unknown?wait=999")[0], 400)
        self.assertEqual(self.call("GET", "/v1/messages/unknown?wait=nan")[0], 400)
        self.assertEqual(self.call("GET", "/v1/other")[0], 404)
        self.assertEqual(self.call("POST", "/v1/other", {})[0], 404)


class RemoteAgentTests(TempDirCase):
    """Another computer's runtime is one more agent."""

    def setUp(self):
        super().setUp()
        # "The other computer": a second runtime with its own token and state.
        self.other, _ = start_runtime(
            self, self.folder / "other", server={"token": OTHER_TOKEN, "name": "windows"})

    def remote(self, token=OTHER_TOKEN, url=None):
        return agents_module.create(config_module.AgentSpec(
            name="pc", type="remote", description="the other computer", aliases=[], speaks=True,
            timeout_seconds=20, url=url or self.other, token=token))

    def test_a_message_is_answered_by_the_other_runtime(self):
        outcome = self.remote().run(Job("在吗", conversation="walk"))
        self.assertEqual((outcome.text, outcome.session_id), ("（回声）在吗", None))
        self.assertEqual((outcome.fields["reply"], outcome.fields["mood"]), ("（回声）在吗", "happy"))
        # It keeps the conversation on its side, under the same name.
        self.assertEqual(self.remote().run(Job("还在吗", conversation="walk")).text, "（回声）还在吗")
        state = json.loads((self.folder / "other" / "state" / "sessions.json").read_text("utf-8"))
        self.assertEqual(state, {"walk": {"echo": "echo-2"}})

    def test_problems_are_explained(self):
        with self.assertRaisesRegex(AgentError, "401"):
            self.remote(token="wrong-token-wrong-token").run(Job("hi"))
        with self.assertRaisesRegex(AgentError, "连不上 pc"):
            self.remote(url="http://127.0.0.1:%d" % free_port()).run(Job("hi"))
        with self.assertRaisesRegex(AgentError, "400"):
            self.remote().run(Job("hi", conversation="bad name"))

    def test_xiaoyou_can_hand_work_to_the_other_computer(self):
        lead = Scripted("claude")
        lead.script = [
            {"reply": "我让那台电脑看看", "brief": "", "mood": "busy",
             "handoff": {"agent": "pc", "task": "列出桌面上的文件"}},
            {"reply": "它说好了", "brief": "好了", "mood": "happy"},
        ]
        xiaoyou, _ = make_xiaoyou(self.folder / "here", [lead, self.remote()])
        turn = xiaoyou.answer("那台电脑上有什么", "default", "t1")
        self.assertEqual((turn.reply, turn.helpers), ("它说好了", ["pc"]))
        self.assertIn("（回声）列出桌面上的文件", lead.jobs[1].text)

    def test_two_runtimes_that_list_each_other_do_not_bounce_a_message_for_ever(self):
        # Each side's default agent is the other side. Whoever is asked first passes it on
        # once; the other side has nobody left to pass it to and says so.
        port_a, port_b = free_port(), free_port()

        def side(name, own_port, other_port):
            return start_runtime(
                self, self.folder / name, env={"XIAOYOU_PORT": str(own_port)},
                server={"token": OTHER_TOKEN},
                agents={"peer": {"type": "remote", "url": "http://127.0.0.1:%d" % other_port,
                                 "token": OTHER_TOKEN, "timeout_seconds": 20}})[0]

        base_a = side("a", port_a, port_b)
        side("b", port_b, port_a)
        with self.assertRaisesRegex(AgentError, "没有能接这句话的代理"):
            self.remote(url=base_a).run(Job("ping"))


FAKE_STT = r'''
import os, sys, wave
with wave.open(sys.argv[1], "rb") as audio:
    seconds = audio.getnframes() / audio.getframerate()
mode = os.environ.get("FAKE_STT_MODE", "ok")
if mode == "crash":
    sys.stderr.write("model not found\n")
    sys.exit(3)
if mode == "silence":
    sys.exit(0)
sys.stdout.buffer.write(("  你好小幽 %.1f\n" % seconds).encode("utf-8"))
'''


def make_wav(seconds=1.0, rate=16000, channels=1, width=2):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(rate)
        audio.writeframes(b"\x01\x00" * int(seconds * rate) * channels * (width // 2 or 1))
    return buffer.getvalue()


class VoiceTests(TempDirCase):
    def setUp(self):
        super().setUp()
        script = self.folder / "fake_stt.py"
        script.write_text(FAKE_STT, encoding="utf-8")
        self.stt_config = {"engine": "command", "command": [sys.executable, str(script), "{audio}"]}
        self.agent = Scripted("claude")
        self.addCleanup(os.environ.pop, "FAKE_STT_MODE", None)

    def service(self, **stt):
        loaded = config_module.load(write_config(self.folder, stt=stt or self.stt_config), {})
        loaded = dataclasses.replace(loaded, default_agent="claude", voice_agent="claude")
        store = Store(loaded.state_dir)
        service = Service(Xiaoyou(loaded, [self.agent], store), store, stt_module.create(loaded))
        self.addCleanup(service.close)
        return service, loaded

    def leftovers(self, loaded):
        return sorted(path.name for path in (loaded.state_dir / "voice").glob("*"))

    def test_config_is_checked(self):
        def load(**stt):
            return config_module.load(write_config(self.folder, stt=stt), {})

        self.assertEqual(load().stt_engine, "none")
        with self.assertRaisesRegex(config_module.ConfigError, "stt.engine"):
            load(engine="whisper")
        with self.assertRaisesRegex(config_module.ConfigError, "{audio}"):
            load(engine="command", command=["stt"])
        with self.assertRaisesRegex(config_module.ConfigError, "model_dir"):
            load(engine="sense_voice")
        with self.assertRaisesRegex(config_module.ConfigError, "stt.language"):
            load(engine="sense_voice", model_dir="m", language="klingon")
        loaded = load(engine="sense_voice", model_dir="models/sv", language="zh")
        self.assertEqual(loaded.stt_model_dir, (self.folder / "models" / "sv").resolve())
        # The model folder does not exist: said at start-up, not at the first voice message.
        self.assertIn("model", stt_module.check(stt_module.create(loaded)))

    def test_a_recording_is_transcribed_then_answered_like_typed_text(self):
        service, loaded = self.service()
        message = service.submit_voice(make_wav(1.5), client_id="v1")
        self.assertEqual((message["kind"], message["text"]), ("voice", ""))
        done = service.get(message["id"], wait=10)
        self.assertEqual((done["status"], done["text"]), ("done", "你好小幽 1.5"))
        self.assertEqual(done["reply"], "re:你好小幽 1.5")
        self.assertEqual([(job.text, job.session_id) for job in self.agent.jobs], [("你好小幽 1.5", None)])
        # The recording is gone once it has been transcribed.
        self.assertEqual(self.leftovers(loaded), [])
        # A retry with the same client_id does not transcribe or answer again.
        self.assertEqual(service.submit_voice(make_wav(1.5), client_id="v1")["id"], message["id"])
        self.assertEqual(len(self.agent.jobs), 1)
        # Typed text still works and continues the same conversation.
        typed = service.get(service.submit("打字")["id"], wait=10)
        self.assertEqual((typed["kind"], typed["status"]), ("text", "done"))
        self.assertEqual((self.agent.jobs[-1].text, self.agent.jobs[-1].session_id), ("打字", "s1"))

    def test_nothing_heard_and_engine_failures_fail_the_message_only(self):
        service, loaded = self.service()
        os.environ["FAKE_STT_MODE"] = "silence"
        quiet = service.get(service.submit_voice(make_wav())["id"], wait=10)
        self.assertEqual((quiet["status"], quiet["mood"]), ("failed", "oops"))
        self.assertIn("没听清", quiet["error"])
        os.environ["FAKE_STT_MODE"] = "crash"
        broken = service.get(service.submit_voice(make_wav())["id"], wait=10)
        self.assertEqual(broken["status"], "failed")
        self.assertIn("model not found", broken["error"])
        self.assertEqual(self.agent.jobs, [])
        self.assertEqual(self.leftovers(loaded), [])
        os.environ["FAKE_STT_MODE"] = "ok"
        self.assertEqual(service.get(service.submit_voice(make_wav())["id"], wait=10)["status"], "done")

    def test_bad_recordings_are_refused_before_queueing(self):
        service, loaded = self.service()
        for audio in (b"", b"not a wav file at all", make_wav(0.05), make_wav(1, channels=2),
                      make_wav(1, width=1), make_wav(1, rate=4000), "text"):
            with self.assertRaises(RequestError):
                service.submit_voice(audio)
        self.assertEqual(self.leftovers(loaded), [])
        self.assertEqual(self.agent.jobs, [])

    def test_voice_is_refused_when_no_engine_is_configured(self):
        loaded = config_module.load(write_config(self.folder), {})
        store = Store(loaded.state_dir)
        loaded = dataclasses.replace(loaded, default_agent="claude", voice_agent="claude")
        service = Service(Xiaoyou(loaded, [self.agent], store), store, stt_module.create(loaded))
        self.addCleanup(service.close)
        with self.assertRaisesRegex(RequestError, "语音识别"):
            service.submit_voice(make_wav())

    def test_leftover_recordings_are_removed_at_start(self):
        voice = self.folder / "state" / "voice"
        voice.mkdir(parents=True)
        (voice / "old.wav").write_bytes(make_wav())
        Store(self.folder / "state")
        self.assertEqual(list(voice.glob("*")), [])

    def test_http_voice_endpoint(self):
        base, _ = start_runtime(self, self.folder, stt=self.stt_config)

        def post(path, data, token=TOKEN):
            request = urllib.request.Request(base + path, data=data, method="POST")
            request.add_header("Content-Type", "audio/wav")
            if token:
                request.add_header("Authorization", "Bearer " + token)
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())

        self.assertEqual(post("/v1/voice", make_wav(), token=None)[0], 401)
        self.assertEqual(post("/v1/voice", b"junk")[0], 400)
        self.assertEqual(post("/v1/voice?agent=nobody", make_wav())[0], 400)
        status, message = post("/v1/voice?client_id=p1&conversation=walk&agent=echo", make_wav(2))
        self.assertEqual((status, message["kind"], message["conversation"], message["asked"]),
                         (202, "voice", "walk", "echo"))
        request = urllib.request.Request(base + "/v1/messages/%s?wait=10" % message["id"])
        request.add_header("Authorization", "Bearer " + TOKEN)
        with urllib.request.urlopen(request, timeout=15) as response:
            done = json.loads(response.read())
        self.assertEqual((done["status"], done["text"], done["reply"]),
                         ("done", "你好小幽 2.0", "（回声）你好小幽 2.0"))


class CommandLineTests(TempDirCase):
    def run_main(self, *arguments):
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = main(list(arguments))
        return code, out.getvalue(), err.getvalue()

    def test_check_and_once(self):
        path = str(write_config(self.folder))
        code, out, err = self.run_main("--config", path, "--check")
        self.assertEqual(code, 0)
        self.assertIn("echo（echo）", out)
        # An old-style config is read, and the owner is told it is the old style.
        self.assertIn("旧写法", err)
        self.assertEqual(self.run_main("--config", path, "--once", "hi")[0], 0)
        code, out, err = self.run_main("--config", path, "--once", "hi", "--agent", "echo")
        self.assertEqual(code, 0)
        self.assertIn("[happy]", out)
        self.assertIn("asked", err)
        self.assertEqual(Store(self.folder / "state").session("default", "echo"), "echo-2")
        self.assertEqual(self.run_main("--config", path, "--once", "hi", "--agent", "nobody")[0], 2)
        code, out, err = self.run_main("--config", str(self.folder / "missing.json"), "--check")
        self.assertEqual(code, 2)
        self.assertIn("missing.json", err)
        self.assertNotIn(TOKEN, out + err)

    def test_check_names_an_agent_whose_command_is_missing(self):
        path = str(write_config(self.folder, agents={
            "echo": {"type": "echo"},
            "codex": {"type": "codex", "command": [str(self.folder / "no-codex")]},
        }))
        code, out, err = self.run_main("--config", path, "--check")
        self.assertEqual(code, 2)
        self.assertIn("codex（codex，只干活）", out)
        self.assertIn("no-codex", err)


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
