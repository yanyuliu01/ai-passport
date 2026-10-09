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
from xiaoyou_runtime import approvals as approvals_module  # noqa: E402
from xiaoyou_runtime import permission_mcp  # noqa: E402
from xiaoyou_runtime import cards as cards_module  # noqa: E402
from xiaoyou_runtime import config as config_module  # noqa: E402
from xiaoyou_runtime import router as router_module  # noqa: E402
from xiaoyou_runtime import stt as stt_module  # noqa: E402
from xiaoyou_runtime import tasks as tasks_module  # noqa: E402
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
if "stream-json" in args:
    import subprocess, time
    def emit(event):
        print(json.dumps(event), flush=True)
    session = args[args.index("--resume") + 1] if "--resume" in args else "s-stream"
    print("a line that is not JSON", flush=True)
    emit({"type": "system", "subtype": "init", "session_id": session})
    emit({"type": "stream_event", "event": {}})
    emit({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "let me look"},
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls  -la\n/tmp"}}]}})
    verdict = "nobody to ask"
    if "--mcp-config" in args and mode == "ask":
        # Do what Claude Code does: start the permission tool and ask it before writing.
        target = os.environ["FAKE_CLAUDE_TARGET"]
        with open(args[args.index("--mcp-config") + 1], encoding="utf-8") as handle:
            server = json.load(handle)["mcpServers"]["xiaoyou"]
        tool = subprocess.Popen([server["command"]] + server["args"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, env=dict(os.environ, **server["env"]))
        def rpc(message):
            tool.stdin.write((json.dumps(message) + "\n").encode()); tool.stdin.flush()
            return json.loads(tool.stdout.readline()) if "id" in message else None
        rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        wanted = {"file_path": target, "content": "hello\n"}
        emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Write", "input": wanted}]}})
        answer = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "approve", "arguments": {"tool_name": "Write", "input": wanted, "tool_use_id": "t1"}}})
        answer = json.loads(answer["result"]["content"][0]["text"])
        verdict = answer["behavior"] + ":" + answer.get("message", "")
        if answer["behavior"] == "allow":
            with open(answer["updatedInput"]["file_path"], "w", encoding="utf-8") as handle:
                handle.write(answer["updatedInput"]["content"])
        tool.stdin.close(); tool.wait()
    if mode == "hang":
        time.sleep(60)
    emit({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "StructuredOutput", "input": {}}]}})
    out = {"type": "result", "is_error": mode == "stream-error", "session_id": session,
           "result": "plain " + text + " [" + verdict + "]"}
    if "--json-schema" in args:
        out["structured_output"] = {"reply": "did " + text + " [" + verdict + "]", "brief": "did", "mood": "happy"}
    emit(out)
    sys.exit(0)
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
    handle.write(json.dumps({"args": args, "stdin": text, "cwd": os.getcwd(),
                             "home": os.environ.get("CODEX_HOME")}) + "\n")
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
        self.started = []
        self.gate = threading.Event()
        self.gate.set()
        self.fail_on = None
        self.script = []

    @property
    def texts(self):
        return [job.text for job in self.jobs]

    def run(self, job):
        self.started.append(job.text)
        if job.text.endswith("slow"):
            self.gate.wait(5)
        self.jobs.append(job)
        if self.fail_on is not None and self.fail_on in job.text:
            raise AgentError("boom")
        if job.text.endswith("explode"):
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
        # Any number of things may run at once; her own line is kept short.
        self.assertEqual((loaded.max_parallel, loaded.voice_timeout_seconds), (0, 60))
        # A thing in the background may take an hour unless the agent says otherwise.
        self.assertEqual(loaded.agents[0].timeout_seconds, 3600)
        tuned = self.load(xiaoyou={"max_parallel": 3, "voice_timeout_seconds": 20})
        self.assertEqual((tuned.max_parallel, tuned.voice_timeout_seconds), (3, 20))

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
            {"backend": "claude_code", "claude_code": {"add_dirs": "~"}},
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
            {"agents": {"claude": claude}, "xiaoyou": {"max_parallel": -1}},
            {"agents": {"claude": claude}, "xiaoyou": {"max_parallel": True}},
            {"agents": {"claude": claude}, "xiaoyou": {"voice_timeout_seconds": 1}},
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
        # Anything Claude Code would ask about in a terminal is asked, of the owner.
        self.assertEqual((claude.permission_mode, claude.add_dirs),
                         ("manual", [Path(os.path.expanduser("~"))]))
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
        self.assertEqual((turn.reply, turn.brief, turn.mood, turn.agent, turn.card, turn.started),
                         ("hello", "hello", "idle", "claude", "", False))

    def test_a_structured_reply_written_as_text_is_recognised(self):
        fields = {"reply": "好", "brief": "好", "mood": "happy"}
        for text in (json.dumps(fields), "```json\n%s\n```" % json.dumps(fields),
                     "  %s \n" % json.dumps(fields)):
            self.assertEqual(agents_module.fields_from_text(text), fields)
        for text in ("just words", "{broken", '{"mood": "happy"}', '["reply"]', ""):
            self.assertIsNone(agents_module.fields_from_text(text))

    def test_what_she_may_ask_the_runtime_to_do_depends_on_the_helpers(self):
        plain = xiaoyou_module.reply_schema()
        self.assertEqual(plain["required"], ["reply", "brief", "mood"])
        self.assertNotIn("action", plain["properties"])
        alone = xiaoyou_module.lane_schema([])["properties"]["action"]["properties"]
        self.assertEqual(alone["type"]["enum"], ["none", "cancel"])
        self.assertNotIn("agent", alone)
        helped = xiaoyou_module.lane_schema([Scripted("codex", speaks=False), Scripted("pc")])
        action = helped["properties"]["action"]["properties"]
        self.assertEqual(action["type"]["enum"], ["none", "start", "amend", "cancel"])
        self.assertEqual(action["agent"]["enum"], ["codex", "pc"])
        # Doing nothing more is always allowed: the action itself is optional.
        self.assertEqual(helped["required"], ["reply", "brief", "mood"])

    def test_the_things_in_hand_are_listed_for_her(self):
        cards = [
            {"id": "c1", "title": "查天气", "state": "working", "agent": "codex", "started_at": 100},
            {"id": "c2", "title": "闲聊", "state": "done", "agent": None, "started_at": None},
            {"id": "c3", "title": "改文档", "state": "waiting", "agent": "claude", "started_at": 1},
        ]
        prompt = xiaoyou_module.lane_prompt("P", 60, [Scripted("codex")], cards, "c3", 290)
        self.assertIn("- c1「查天气」：codex 在做，已经 3 分钟", prompt)
        self.assertIn("- c2「闲聊」：做完了", prompt)
        self.assertIn("- c3「改文档」：claude 在做，等主人点头", prompt)
        self.assertIn("正看着 c3", prompt)
        nothing = xiaoyou_module.lane_prompt("P", 60, [], [], None, 0)
        self.assertIn("还没有。", nothing)
        self.assertNotIn("- start", nothing)
        self.assertNotIn("正看着", nothing)


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
        self.schema = xiaoyou_module.reply_schema()

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
        self.assertEqual(args[args.index("--permission-mode") + 1], "manual")
        self.assertEqual(args[args.index("--allowedTools") + 1], "Read,Grep")
        self.assertEqual(args[args.index("--model") + 1], "some-model")
        self.assertNotIn("--resume", args)
        self.assertEqual(args[-2:], ["--max-turns", "9"])
        # The whole home directory is within reach, not just the folder it starts in.
        self.assertEqual(args[args.index("--add-dir") + 1], os.path.expanduser("~"))
        self.assertNotIn("--permission-prompt-tool", args)

    def test_work_is_followed_step_by_step_and_the_session_is_known_at_once(self):
        control = agents_module.Control()
        lines, sessions = [], []
        control.progress, control.session = lines.append, sessions.append
        outcome = self.agent.run(Job("count", system="S", schema=self.schema, control=control))
        self.assertEqual((outcome.text, outcome.session_id), ("did count [nobody to ask]", "s-stream"))
        self.assertEqual(outcome.fields["brief"], "did")
        # Tool names and commands as they are; the tool that carries the reply is not a step.
        self.assertEqual(lines, ["Bash ls  -la\n/tmp"])
        self.assertEqual(sessions, ["s-stream"])
        args = self.calls()[0]["args"]
        self.assertEqual(args[:4], ["-p", "--output-format", "stream-json", "--verbose"])
        self.assertEqual(self.calls()[0]["stdin"], "count")
        # Nobody to ask this time, so no permission tool is attached.
        self.assertNotIn("--mcp-config", args)
        resumed = self.agent.run(Job("more", session_id="s-prev", control=agents_module.Control()))
        self.assertEqual((resumed.text, resumed.session_id, resumed.fields),
                         ("plain more [nobody to ask]", "s-prev", None))
        os.environ["FAKE_CLAUDE_MODE"] = "stream-error"
        with self.assertRaisesRegex(AgentError, "报告失败"):
            self.agent.run(Job("x", control=agents_module.Control()))
        os.environ["FAKE_CLAUDE_MODE"] = "crash"
        with self.assertRaisesRegex(AgentError, "not logged in"):
            self.agent.run(Job("x", control=agents_module.Control()))

    def test_what_needs_a_yes_is_put_to_the_owner_through_the_permission_tool(self):
        os.environ["FAKE_CLAUDE_MODE"] = "ask"
        target = self.folder / "note.txt"
        os.environ["FAKE_CLAUDE_TARGET"] = str(target)
        self.addCleanup(os.environ.pop, "FAKE_CLAUDE_TARGET", None)
        approvals = approvals_module.Approvals()
        gate = approvals_module.Gate(approvals)
        self.addCleanup(gate.close)

        def run(decision):
            control = agents_module.Control()
            control.gate = gate.open("c1", "default", "claude")
            control.scratch = self.folder / "state"
            result = {}
            thread = threading.Thread(target=lambda: result.update(
                outcome=self.agent.run(Job("write it", control=control))))
            thread.start()
            self.assertTrue(until(approvals.pending))
            asked = approvals.pending()[0]
            self.assertEqual((asked["card"], asked["agent"], asked["tool"], asked["detail"]),
                             ("c1", "claude", "claude · Write", "%s\nhello" % target))
            args = self.calls()[-1]["args"]
            self.assertEqual(args[args.index("--permission-prompt-tool") + 1], "mcp__xiaoyou__approve")
            config = Path(args[args.index("--mcp-config") + 1])
            # The file with the key is readable by the owner only, and lives in the state folder.
            self.assertEqual(config.parent, self.folder / "state")
            if os.name != "nt":
                self.assertEqual(config.stat().st_mode & 0o077, 0)
            self.assertNotIn(TOKEN, config.read_text("utf-8"))
            self.assertNotIn("--strict-mcp-config", args)
            self.assertTrue(approvals.answer(asked["id"], decision))
            thread.join(10)
            gate.shut(control.gate)
            self.assertFalse(config.exists())
            return result["outcome"].text

        self.assertEqual(run("deny"), "plain write it [deny:主人说不行]")
        self.assertFalse(target.exists())
        self.assertEqual(run("allow"), "plain write it [allow:]")
        self.assertEqual(target.read_text("utf-8"), "hello\n")

    def test_when_she_only_talks_it_gets_no_tools_and_little_time(self):
        seen = []

        def fake_run(command, **kwargs):
            seen.append(kwargs)
            return subprocess.CompletedProcess(command, 0, '{"result": "ok", "session_id": "s"}', "")

        agent = agents_module.ClaudeCodeAgent(self.config.agents[0], run=fake_run)
        args = agent.command(Job("hi", system="S", schema=self.schema, plain=True))
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertEqual(args[args.index("--permission-mode") + 1], "dontAsk")
        self.assertNotIn("--allowedTools", args)
        agent.run(Job("hi", plain=True, timeout=42))
        self.assertEqual(seen[-1]["timeout"], 42)
        agent.run(Job("hi"))
        self.assertEqual(seen[-1]["timeout"], 3600)
        self.assertNotIn("--tools", agent.command(Job("hi")))

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

    def test_extra_environment_points_claude_code_at_another_service(self):
        seen = []

        def fake_run(command, **kwargs):
            seen.append(kwargs)
            return subprocess.CompletedProcess(command, 0, '{"result": "ok", "session_id": "s"}', "")

        extra = {"ANTHROPIC_BASE_URL": "https://api.example.com/anthropic",
                 "ANTHROPIC_AUTH_TOKEN": "sk-example"}
        spec = dataclasses.replace(self.config.agents[0], config_dir=self.folder / "home", env=extra)
        agent = agents_module.ClaudeCodeAgent(spec, run=fake_run)
        agent.run(Job("x"))
        self.assertEqual(seen[-1]["env"]["ANTHROPIC_BASE_URL"], extra["ANTHROPIC_BASE_URL"])
        self.assertEqual(seen[-1]["env"]["CLAUDE_CONFIG_DIR"], str(self.folder / "home"))
        self.assertEqual(seen[-1]["env"].get("PATH"), os.environ.get("PATH"))
        self.assertIsNone(agent.check())
        keyless = dataclasses.replace(spec, env={"ANTHROPIC_BASE_URL": extra["ANTHROPIC_BASE_URL"]})
        saved = {key: os.environ.pop(key, None) for key in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")}
        try:
            self.assertIn("ANTHROPIC_AUTH_TOKEN", agents_module.ClaudeCodeAgent(keyless).check())
        finally:
            os.environ.update({key: value for key, value in saved.items() if value is not None})

    def test_agent_environment_is_validated(self):
        def agents(env):
            return {"claude": {"type": "claude_code", "command": ["claude"], "env": env}}

        loaded = config_module.load(write_config(self.folder, agents=agents({"A_B": "1"})), {})
        self.assertEqual(loaded.agents[0].env, {"A_B": "1"})
        for bad in ({"A-B": "1"}, {"A": 1}, {"CLAUDE_CONFIG_DIR": "/x"}):
            with self.assertRaises(config_module.ConfigError):
                config_module.load(write_config(self.folder, agents=agents(bad)), {})


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

    def test_a_separate_codex_home_is_used_only_when_set(self):
        self.agent.run(Job("x"))
        self.assertIsNone(self.calls()[0]["home"])
        self.assertIsNone(self.agent.check())
        home = self.folder / "codex-home"
        separate = agents_module.create(dataclasses.replace(self.config.agents[0], config_dir=home))
        # Codex refuses to start when the directory is missing; say so before it is asked anything.
        self.assertIn(str(home), separate.check())
        home.mkdir()
        self.assertIsNone(separate.check())
        separate.run(Job("x"))
        self.assertEqual(self.calls()[1]["home"], str(home))
        # From the config file, relative to it; an environment variable wins.
        folder = self.folder / "other"
        folder.mkdir()
        agents = {"codex": {"type": "codex", "config_dir": "codex-login"}}
        loaded = config_module.load(write_config(folder, agents=agents), {})
        self.assertEqual(loaded.agents[0].config_dir, (folder / "codex-login").resolve())
        from_env = config_module.load(write_config(folder, agents=agents),
                                      {"XIAOYOU_CODEX_CONFIG_DIR": str(home)})
        self.assertEqual(from_env.agents[0].config_dir, home)

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


def until(condition, seconds=5.0):
    """Poll until the condition holds; say whether it did."""
    import time
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return bool(condition())


def start(agent, task, title="", card="new", reply="我让它看看"):
    return {"reply": reply, "brief": "去问了", "mood": "busy", "card": card,
            "action": {"type": "start", "agent": agent, "title": title, "task": task}}


def amend(card, task, mode, reply="好，告诉它了"):
    return {"reply": reply, "brief": "", "mood": "busy", "card": card,
            "action": {"type": "amend", "mode": mode, "task": task}}


class CardsTests(TempDirCase):
    def cards(self):
        return cards_module.Cards(self.folder / "cards.json")

    def test_a_card_collects_what_was_said_and_numbers_every_change(self):
        cards = self.cards()
        first = cards.open("default", "  帮我 看看\n这个很长很长很长很长很长很长很长很长的标题  ")
        self.assertEqual((first["id"], first["state"], first["agent"], first["seq"]),
                         ("c1", "done", None, 1))
        self.assertEqual(len(first["title"]), 24)
        cards.update("c1", said="问", say="答", brief="b", mood="happy")
        second = cards.open("work", "别的对话", "working", "codex")
        self.assertEqual(second["id"], "c2")
        card = cards.get("c1")
        self.assertEqual([(entry["role"], entry["text"]) for entry in card["entries"]],
                         [("you", "问"), ("xiaoyou", "答")])
        self.assertEqual((card["brief"], card["mood"], card["seq"]), ("b", "happy", 2))
        self.assertIsNone(cards.update("c9", say="x"))
        for number in range(8):
            cards.update("c2", progress="Bash  step\n%d" % number + "x" * 300)
        progress = cards.get("c2")["progress"]
        self.assertEqual((len(progress), len(progress[-1])), (5, 200))
        self.assertTrue(progress[-1].startswith("Bash step 7"))
        self.assertEqual([card["id"] for card in cards.recent("default")], ["c1"])
        self.assertEqual([card["id"] for card in cards.recent(None)], ["c1", "c2"])

    def test_only_what_changed_is_fed_and_a_caller_can_wait_for_it(self):
        cards = self.cards()
        cards.open("default", "一")
        cards.open("default", "二")
        feed = cards.changed("default", 0)
        self.assertEqual(([card["id"] for card in feed["cards"]], feed["seq"]), (["c1", "c2"], 2))
        self.assertEqual(cards.changed("default", 2, wait=0.05), {"seq": 2, "cards": []})
        self.assertEqual(cards.changed("other", 0)["cards"], [])
        waiter = {}
        thread = threading.Thread(target=lambda: waiter.update(cards.changed("default", 2, wait=5)))
        thread.start()
        cards.update("c1", say="后来的话")
        thread.join(5)
        self.assertEqual(([card["id"] for card in waiter["cards"]], waiter["seq"]), (["c1"], 3))
        seen = []
        cards.subscribe(seen.append)
        cards.update("c2", state="failed")
        self.assertEqual([(card["id"], card["state"]) for card in seen], [("c2", "failed")])

    def test_cards_survive_a_restart_and_unfinished_ones_are_marked(self):
        cards = self.cards()
        cards.open("default", "做完的")
        cards.update("c1", said="问", say="答")
        cards.open("default", "没做完的", "working", "codex")
        cards.open("default", "等点头的", "waiting", "claude")
        again = self.cards()
        self.assertEqual([(card["id"], card["state"]) for card in again.recent("default")],
                         [("c1", "done"), ("c2", "failed"), ("c3", "failed")])
        lost = again.get("c2")
        self.assertEqual((lost["entries"][-1]["text"], lost["mood"]),
                         ("Runtime 重启了，这件事没做完", "oops"))
        # The changes made at start-up are fed like any other, and numbering goes on.
        self.assertEqual([card["id"] for card in again.changed("default", 4)["cards"]], ["c2", "c3"])
        self.assertEqual(again.open("default", "新的")["id"], "c4")
        (self.folder / "cards.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(self.cards().recent(None), [])

    def test_old_cards_are_dropped_but_never_one_that_is_still_going(self):
        cards = self.cards()
        cards.open("default", "还在做", "working", "codex")
        for number in range(cards_module.MAX_CARDS + 5):
            cards.open("default", "第 %d 件" % number)
        kept = cards.recent(None, 1000)
        self.assertEqual(len(kept), cards_module.MAX_CARDS)
        self.assertEqual(kept[0]["id"], "c1")
        self.assertIsNone(cards.get("c2"))


class TasksTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.store = Store(self.folder / "state")
        self.finished = []
        self.done = threading.Event()

    def tasks(self, limit=0, started=None):
        def finish(task, outcome, error):
            self.finished.append((task.card, outcome.text if outcome else None, error))
            self.done.set()

        tasks = tasks_module.Tasks(self.store, finish, limit, started)
        self.addCleanup(tasks.close)
        return tasks

    def command(self, script):
        loaded = config_module.load(write_config(self.folder, agents={
            "tool": {"type": "command", "command": [sys.executable, "-c", script]}}), {})
        return agents_module.create(loaded.agents[0])

    def test_changing_the_request_stops_the_process_and_runs_again(self):
        # Sleeps for ever unless it is told the request changed.
        agent = self.command(
            "import sys, time\ntext = sys.stdin.read()\n"
            "if '改了要求' not in text: time.sleep(60)\nprint(text)")
        tasks = self.tasks()
        self.assertTrue(tasks.start(tasks_module.Task("c1", "default", agent, "数一数文件")))
        self.assertFalse(tasks.start(tasks_module.Task("c1", "default", agent, "again")))
        self.assertTrue(until(lambda: tasks.active("c1")))
        import time
        time.sleep(0.3)  # let the process start, so there is something to stop
        self.assertEqual(tasks.amend("c1", "redo", "只数 Python 文件"), "redo")
        self.assertTrue(self.done.wait(10))
        card, text, error = self.finished[0]
        self.assertIsNone(error)
        # A command keeps no session, so the original task is given again with the change.
        self.assertIn("（原来的任务：数一数文件）", text)
        self.assertIn("主人改了要求：只数 Python 文件", text)
        self.assertFalse(tasks.active("c1"))
        self.assertIsNone(tasks.amend("c1", "redo", "x"))

    def test_cancelling_stops_the_process_and_nothing_is_reported(self):
        agent = self.command("import time; time.sleep(60)")
        tasks = self.tasks()
        tasks.start(tasks_module.Task("c1", "default", agent, "x"))
        import time
        time.sleep(0.3)
        started = time.monotonic()
        self.assertTrue(tasks.cancel("c1"))
        self.assertFalse(tasks.cancel("c1"))
        tasks.close()
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(self.finished, [])

    @unittest.skipIf(os.name == "nt", "process sessions are a POSIX notion")
    def test_cancelling_reaches_what_the_process_started_in_a_session_of_its_own(self):
        # Claude Code runs shell commands in a new session; killing its group alone misses them.
        marker = self.folder / "pid.txt"
        agent = self.command(
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
            " start_new_session=True)\n"
            "open(%r, 'w').write(str(child.pid))\ntime.sleep(60)" % str(marker))
        tasks = self.tasks()
        tasks.start(tasks_module.Task("c1", "default", agent, "x"))
        self.assertTrue(until(marker.exists) and until(lambda: marker.read_text().isdigit()))
        grandchild = int(marker.read_text())
        tasks.cancel("c1")

        def gone():
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                return True
            # Killed but not yet reaped by whoever inherited it: gone for our purposes.
            status = subprocess.run(["ps", "-o", "stat=", "-p", str(grandchild)],
                                    stdout=subprocess.PIPE, text=True).stdout.strip()
            return status == "" or status.startswith("Z")

        self.assertTrue(until(gone))

    def test_an_agent_that_can_be_steered_is_told_without_starting_over(self):
        heard = []

        class Steerable(Scripted):
            def run(self, job):
                job.control.can_steer(lambda text: heard.append(text) or True)
                return super().run(job)

        agent = Steerable("codex", speaks=False)
        agent.gate.clear()
        self.addCleanup(agent.gate.set)
        tasks = self.tasks()
        tasks.start(tasks_module.Task("c1", "default", agent, "one slow"))
        self.assertTrue(until(lambda: agent.started))
        self.assertEqual(tasks.amend("c1", "redo", "换个做法"), "steer")
        agent.gate.set()
        self.assertTrue(self.done.wait(5))
        self.assertEqual((heard, len(agent.jobs)), (["换个做法"], 1))

    def test_with_a_limit_the_rest_wait_their_turn(self):
        agent = Scripted("codex", speaks=False)
        agent.gate.clear()
        self.addCleanup(agent.gate.set)
        begun = []
        tasks = self.tasks(limit=1, started=lambda task: begun.append(task.card))
        tasks.start(tasks_module.Task("c1", "default", agent, "one slow"))
        tasks.start(tasks_module.Task("c2", "default", agent, "two slow"))
        self.assertTrue(until(lambda: agent.started == ["one slow"]))
        self.assertEqual((begun, tasks.count()), (["c1"], 2))
        agent.gate.set()
        self.assertTrue(until(lambda: len(self.finished) == 2))
        self.assertEqual(begun, ["c1", "c2"])


class ApprovalTests(TempDirCase):
    def test_an_approval_waits_for_one_answer(self):
        changes = []
        approvals = approvals_module.Approvals(lambda card, waiting: changes.append((card, waiting)))
        first = approvals.ask("c1", "default", "claude", "claude · Bash", "rm -rf build")
        second = approvals.ask("c1", "default", "claude", "claude · Write", "a.txt")
        other = approvals.ask("c2", "work", "codex", "codex · 命令", "make")
        self.assertEqual((first, second, other), ("a1", "a2", "a3"))
        self.assertEqual([item["id"] for item in approvals.pending()], ["a1", "a2", "a3"])
        self.assertEqual([item["id"] for item in approvals.pending("work")], ["a3"])
        self.assertEqual(sorted(approvals.pending("work")[0]),
                         ["agent", "card", "conversation", "created_at", "detail", "id", "tool"])
        got = {}
        thread = threading.Thread(target=lambda: got.update(decision=approvals.wait(first)))
        thread.start()
        self.assertTrue(approvals.answer(first, "allow"))
        thread.join(5)
        self.assertEqual(got, {"decision": "allow"})
        # Answered once; a second answer and an unknown number are told apart.
        self.assertIs(approvals.answer(first, "deny"), False)
        self.assertIsNone(approvals.answer("a99", "allow"))
        # The card is told what it is still waiting for.
        self.assertEqual(changes, [("c1", "a1"), ("c1", "a2"), ("c2", "a3"), ("c1", "a2")])
        # The thing stopped: whoever is still waiting is released with a no.
        approvals.drop("c1")
        self.assertEqual((approvals.wait(second), approvals.get(second)["gone"]), ("deny", True))
        self.assertEqual(changes[-1], ("c1", None))
        self.assertEqual([item["id"] for item in approvals.pending()], ["a3"])
        self.assertEqual(approvals.wait("a99"), "deny")

    def test_what_is_shown_is_the_tool_and_its_input_as_they_are(self):
        describe = approvals_module.describe
        self.assertEqual(describe("claude", "Bash", {"command": "git push --force"}),
                         ("claude · Bash", "git push --force"))
        self.assertEqual(describe("claude", "Edit", {"file_path": "/a.py", "old_string": "x = 1",
                                                     "new_string": "x = 2"}),
                         ("claude · Edit", "/a.py\n- x = 1\n+ x = 2"))
        self.assertEqual(describe("claude", "mcp__mail__send", {"to": "a@b.c"}),
                         ("claude · mcp__mail__send", '{"to": "a@b.c"}'))
        self.assertEqual(describe("claude", "Odd", "not an object"), ("claude · Odd", ""))
        self.assertEqual(len(describe("claude", "Write", {"content": "x" * 50000})[1]), 20000)
        line = approvals_module.progress_line
        self.assertEqual((line("Read", {"file_path": "/a"}), line("WebSearch", {"query": "q"}),
                          line("Task", {"prompt": "p"}), line("Bash", None)),
                         ("Read /a", "WebSearch q", "Task", "Bash"))

    def test_the_gate_only_opens_for_the_key_of_a_thing(self):
        approvals = approvals_module.Approvals()
        gate = approvals_module.Gate(approvals)
        self.addCleanup(gate.close)
        env = gate.open("c1", "default", "claude")
        self.assertTrue(env["XIAOYOU_GATE_URL"].startswith("http://127.0.0.1:"))

        def post(key, body):
            request = urllib.request.Request(env["XIAOYOU_GATE_URL"], data=body, method="POST")
            request.add_header("X-Xiaoyou-Key", key)
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())

        ask = json.dumps({"tool_name": "Bash", "input": {"command": "ls"}}).encode()
        self.assertEqual(post("wrong", ask)[0], 403)
        self.assertEqual(post(TOKEN, ask)[0], 403)
        self.assertEqual(post(env["XIAOYOU_GATE_KEY"], b"{nope")[0], 400)
        self.assertEqual(post(env["XIAOYOU_GATE_KEY"], b'{"input": {}}')[0], 400)
        self.assertEqual(approvals.pending(), [])
        answer = {}
        thread = threading.Thread(target=lambda: answer.update(reply=post(env["XIAOYOU_GATE_KEY"], ask)))
        thread.start()
        self.assertTrue(until(approvals.pending))
        approvals.answer(approvals.pending()[0]["id"], "allow")
        thread.join(5)
        self.assertEqual((answer["reply"][0], answer["reply"][1]["decision"]), (200, "allow"))
        # Once the round is over the key is worth nothing.
        gate.shut(env)
        self.assertEqual(post(env["XIAOYOU_GATE_KEY"], ask)[0], 403)

    def test_the_permission_tool_speaks_just_enough_mcp(self):
        handle = permission_mcp.handle
        self.assertIsNone(handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, None, None))
        self.assertIsNone(handle("junk", None, None))
        hello = handle({"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                       None, None)
        self.assertEqual((hello["result"]["protocolVersion"], hello["result"]["serverInfo"]["name"]),
                         ("2025-06-18", "xiaoyou"))
        tools = handle({"id": 2, "method": "tools/list"}, None, None)["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["approve"])
        self.assertIn("error", handle({"id": 3, "method": "resources/list"}, None, None))
        self.assertIn("error", handle({"id": 4, "method": "tools/call", "params": {"name": "other"}},
                                      None, None))

        def call(url, key, opener=urllib.request.urlopen):
            reply = handle({"id": 5, "method": "tools/call", "params": {"name": "approve", "arguments": {
                "tool_name": "Bash", "input": {"command": "ls"}, "tool_use_id": "t"}}}, url, key, opener)
            # The answer is a JSON object serialised into one piece of text.
            return json.loads(reply["result"]["content"][0]["text"])

        # Not started by the runtime, or the runtime is gone: a no, with the reason.
        self.assertEqual(call(None, None)["behavior"], "deny")
        refused = call("http://127.0.0.1:%d/approve" % free_port(), "k")
        self.assertEqual(refused["behavior"], "deny")
        self.assertIn("连不上", refused["message"])

        class Reply(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        seen = []

        def opener(decision):
            def open_(request, timeout):
                seen.append((request.get_header("X-xiaoyou-key"), json.loads(request.data)))
                return Reply(json.dumps({"decision": decision, "message": "主人说不行"}).encode())
            return open_

        self.assertEqual(call("http://gate", "k", opener("allow")),
                         {"behavior": "allow", "updatedInput": {"command": "ls"}})
        self.assertEqual(call("http://gate", "k", opener("deny")),
                         {"behavior": "deny", "message": "主人说不行"})
        self.assertEqual(seen[0], ("k", {"tool_name": "Bash", "input": {"command": "ls"},
                                         "tool_use_id": "t"}))

    def test_the_permission_tool_runs_as_a_process_on_standard_input_and_output(self):
        lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                 {"jsonrpc": "2.0", "method": "notifications/initialized"},
                 {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        text = "".join(json.dumps(line) + "\n" for line in lines) + "not json\n"
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("XIAOYOU_GATE")}
        done = subprocess.run(
            [sys.executable, "-m", "xiaoyou_runtime.permission_mcp"], input=text.encode(),
            stdout=subprocess.PIPE, cwd=str(RUNTIME), env=environment, timeout=20)
        replies = [json.loads(line) for line in done.stdout.decode().splitlines()]
        self.assertEqual((done.returncode, [reply["id"] for reply in replies]), (0, [1, 2]))


class WorkWithApprovalTests(TempDirCase):
    """The whole path: a thing in the background, a real child process, the owner's answer."""

    def setUp(self):
        super().setUp()
        fake = self.folder / "fake_claude.py"
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        self.target = self.folder / "note.txt"
        for name, value in (("FAKE_CLAUDE_LOG", str(self.folder / "calls.jsonl")),
                            ("FAKE_CLAUDE_MODE", "ask"), ("FAKE_CLAUDE_TARGET", str(self.target))):
            os.environ[name] = value
            self.addCleanup(os.environ.pop, name, None)
        loaded = config_module.load(write_config(self.folder, agents={"claude": {
            "type": "claude_code", "command": [sys.executable, str(fake)]}}), {})
        self.store = Store(loaded.state_dir)
        self.xiaoyou = Xiaoyou(loaded, [agents_module.create(spec) for spec in loaded.agents], self.store)
        self.service = Service(self.xiaoyou, self.store)
        self.addCleanup(self.service.close)

    def begin(self):
        message = self.service.get(self.service.submit("写个文件", agent="claude")["id"], wait=5)
        self.assertTrue(until(lambda: self.service.feed("default")["approvals"], 10))
        return message["card"], self.service.feed("default")["approvals"][0]

    def test_the_owner_says_yes(self):
        card_id, asked = self.begin()
        self.assertEqual((asked["card"], asked["tool"], asked["detail"]),
                         (card_id, "claude · Write", "%s\nhello" % self.target))
        card = self.xiaoyou.cards.get(card_id)
        self.assertEqual((card["state"], card["approval"]), ("waiting", asked["id"]))
        # Each step is on the card as it happened.
        self.assertEqual(card["progress"], ["Bash ls -la /tmp", "Write %s" % self.target])
        self.assertFalse(self.target.exists())
        self.assertIs(self.service.approve(asked["id"], "allow"), True)
        self.assertIs(self.service.approve(asked["id"], "allow"), False)
        card = self.xiaoyou.settle(card_id, 10)
        self.assertEqual((card["state"], card["approval"]), ("done", None))
        self.assertEqual(self.target.read_text("utf-8"), "hello\n")
        self.assertIn("[allow:]", card["entries"][-1]["text"])
        self.assertEqual(self.service.feed("default")["approvals"], [])
        # The session was known from the first event on.
        self.assertEqual(self.store.session("default/%s" % card_id, "claude"), "s-stream")
        self.assertEqual(list((self.folder / "state").glob("mcp-*.json")), [])

    def test_the_owner_says_no(self):
        card_id, asked = self.begin()
        self.assertIs(self.service.approve(asked["id"], "deny"), True)
        card = self.xiaoyou.settle(card_id, 10)
        self.assertEqual(card["state"], "done")
        self.assertIn("[deny:主人说不行]", card["entries"][-1]["text"])
        self.assertFalse(self.target.exists())
        with self.assertRaises(RequestError):
            self.service.approve(asked["id"], "maybe")
        self.assertIsNone(self.service.approve("a99", "allow"))

    def test_cancelling_while_it_waits_ends_the_process_and_the_question(self):
        card_id, asked = self.begin()
        self.assertEqual(self.service.cancel(card_id)["state"], "cancelled")
        self.assertTrue(until(lambda: not self.service.feed("default")["approvals"]))
        card = self.xiaoyou.cards.get(card_id)
        self.assertEqual((card["state"], card["approval"]), ("cancelled", None))
        self.assertFalse(self.target.exists())
        # Too late to answer; the card stays cancelled.
        self.assertIs(self.service.approve(asked["id"], "allow"), False)
        self.assertEqual(self.xiaoyou.cards.get(card_id)["state"], "cancelled")

    def test_a_round_that_is_started_over_does_not_leave_its_question_behind(self):
        os.environ["FAKE_CLAUDE_MODE"] = "hang"
        self.service.get(self.service.submit("慢慢做", agent="claude")["id"], wait=5)
        self.assertTrue(until(lambda: self.xiaoyou.cards.get("c1")["progress"], 10))
        os.environ["FAKE_CLAUDE_MODE"] = "ok"
        self.assertEqual(self.xiaoyou._tasks.amend("c1", "redo", "换个做法"), "redo")
        card = self.xiaoyou.settle("c1", 10)
        self.assertEqual(card["state"], "done")
        calls = [json.loads(line) for line in (self.folder / "calls.jsonl").read_text("utf-8").splitlines()]
        # The first round had already said which session it was, so the second continues it.
        self.assertEqual(calls[1]["args"][calls[1]["args"].index("--resume") + 1], "s-stream")
        self.assertEqual(calls[1]["stdin"], "（主人改了要求：换个做法。按新的要求继续，已经做过的不用重复。）")


class XiaoyouTests(TempDirCase):
    """Xiaoyou is her own identity: she answers, hands work out and reports back."""

    def setUp(self):
        super().setUp()
        self.claude = Scripted("claude")
        self.codex = Scripted("codex", speaks=False, aliases=["科迪"])
        self.xiaoyou, self.store = make_xiaoyou(self.folder, [self.claude, self.codex])
        self.addCleanup(self.xiaoyou.close)
        self.addCleanup(self.codex.gate.set)
        self.addCleanup(self.claude.gate.set)
        self.events = []
        self.count = 0

    def say(self, text, conversation="default", **extra):
        self.count += 1
        return self.xiaoyou.hear(
            text, conversation, "turn-%d" % self.count,
            report=lambda kind, agent, detail: self.events.append((kind, agent, detail)), **extra)

    def settled(self, card_id):
        card = self.xiaoyou.settle(card_id, 5)
        self.assertNotIn(card["state"], cards_module.ACTIVE)
        return card

    def words(self, card_id):
        return [(entry["role"], entry["text"]) for entry in self.xiaoyou.cards.get(card_id)["entries"]]

    def test_she_answers_herself_when_she_can(self):
        turn = self.say("几点了")
        self.assertEqual((turn.reply, turn.brief, turn.mood, turn.agent, turn.card, turn.started),
                         ("re:几点了", "b:几点了", "happy", "claude", "c1", False))
        job = self.claude.jobs[0]
        self.assertEqual((job.text, job.session_id, job.conversation), ("几点了", None, "default"))
        # On her own line she only talks: no tools, and not for long.
        self.assertEqual((job.plain, job.timeout), (True, 60))
        self.assertIn("PERSONA-MARKER", job.system)
        # She is told who she can hand work to (the agent that voices her included)...
        self.assertIn("codex：codex helper", job.system)
        self.assertEqual(job.schema["properties"]["action"]["properties"]["agent"]["enum"],
                         ["claude", "codex"])
        self.assertEqual(self.codex.jobs, [])
        self.assertEqual(self.events, [("route", "claude", "default")])
        card = self.xiaoyou.cards.get("c1")
        self.assertEqual((card["state"], card["agent"], card["title"], card["brief"], card["mood"]),
                         ("done", None, "几点了", "b:几点了", "happy"))
        self.assertEqual(self.words("c1"), [("you", "几点了"), ("xiaoyou", "re:几点了")])
        self.assertEqual(self.store.session("default", "claude"), "s1")
        # ...and the next sentence continues her session and gets a card of its own.
        self.assertEqual(self.say("再问").card, "c2")
        self.assertEqual(self.claude.jobs[1].session_id, "s1")
        self.assertIn("- c1「几点了」：做完了", self.claude.jobs[1].system)

    def test_a_follow_up_goes_on_the_card_it_belongs_to(self):
        self.say("几点了")
        self.claude.script = [{"reply": "北京时间", "brief": "北京", "mood": "idle", "card": "c1"}]
        turn = self.say("哪个时区", card="c1")
        self.assertEqual(turn.card, "c1")
        self.assertIn("正看着 c1", self.claude.jobs[-1].system)
        self.assertEqual(self.words("c1")[2:], [("you", "哪个时区"), ("xiaoyou", "北京时间")])
        self.assertEqual(len(self.xiaoyou.cards.recent("default")), 1)
        # A card of another conversation, or one that does not exist, is not "the one on screen".
        self.say("别处", "work", card="c1")
        self.assertNotIn("正看着", self.claude.jobs[-1].system)
        self.say("哪件", card="c77")
        self.assertNotIn("正看着", self.claude.jobs[-1].system)

    def test_she_hands_work_to_a_helper_and_is_free_at_once(self):
        self.codex.gate.clear()
        self.claude.script = [
            start("codex", "review retry() in retry.py slow", "retry 的问题", reply="我让 codex 看看"),
            {"reply": "还在看呢", "brief": "在看", "mood": "busy", "card": "c1"},
            {"reply": "codex 说第 42 行有问题", "brief": "第 42 行", "mood": "happy"},
        ]
        turn = self.say("帮我看看 retry 的问题")
        self.assertEqual((turn.reply, turn.mood, turn.agent, turn.card, turn.started),
                         ("我让 codex 看看", "busy", "codex", "c1", True))
        card = self.xiaoyou.cards.get("c1")
        self.assertEqual((card["state"], card["agent"], card["title"]),
                         ("working", "codex", "retry 的问题"))
        self.assertIsNotNone(card["started_at"])
        self.assertEqual(self.events, [("route", "claude", "default"),
                                       ("handoff", "codex", "我让 codex 看看")])
        # While the helper is busy she still answers, and knows what is going on.
        self.assertTrue(until(lambda: self.codex.started))
        self.assertEqual(self.say("好了吗").reply, "还在看呢")
        self.assertIn("- c1「retry 的问题」：codex 在做，已经 0 秒", self.claude.jobs[1].system)
        # A word about a thing that is still going does not replace its brief.
        self.assertEqual(self.xiaoyou.cards.get("c1")["brief"], "我让 codex 看看")
        self.codex.gate.set()
        card = self.settled("c1")
        self.assertEqual((card["state"], card["brief"], card["mood"]), ("done", "第 42 行", "happy"))
        self.assertEqual(self.words("c1")[-1], ("xiaoyou", "codex 说第 42 行有问题"))
        # The helper gets the task only: no persona, no chat, a session of its own for this card.
        work = self.codex.jobs[0]
        self.assertEqual((work.text, work.system, work.schema, work.plain, work.session_id),
                         ("review retry() in retry.py slow", None, None, False, None))
        self.assertEqual(self.store.session("default/c1", "codex"), "s1")
        self.assertIsNone(self.store.session("default", "codex"))
        # Its raw result goes back to her, on her own session, not to the owner.
        back = self.claude.jobs[2]
        self.assertEqual((back.session_id, back.plain), ("s2", True))
        self.assertIn("raw:review retry() in retry.py slow", back.text)
        self.assertIn("后台那件事「retry 的问题」，帮手 codex 做完了", back.text)
        self.assertNotIn("action", back.schema["properties"])
        turns = self.store.transcript.turns("default")
        self.assertEqual([(turn["text"], turn["reply"], turn["by"], turn["seen"]) for turn in turns], [
            ("帮我看看 retry 的问题", "我让 codex 看看", "codex", ["claude"]),
            ("好了吗", "还在看呢", "claude", ["claude"]),
            ("（后台的事「retry 的问题」有结果了）", "codex 说第 42 行有问题", "codex", ["claude"]),
        ])

    def test_a_helper_that_speaks_for_her_reports_in_her_words_itself(self):
        self.claude.script = [
            start("claude", "查一下明天的天气", "明天的天气"),
            {"reply": "明天晴", "brief": "晴", "mood": "happy"},
        ]
        turn = self.say("明天天气怎么样")
        self.assertEqual((turn.agent, turn.started), ("claude", True))
        card = self.settled("c1")
        self.assertEqual((card["state"], card["agent"], card["brief"]), ("done", "claude", "晴"))
        work = self.claude.jobs[1]
        # The work runs with tools, in a session of its own, with the persona.
        self.assertEqual((work.text, work.plain, work.session_id, work.timeout),
                         ("查一下明天的天气", False, None, None))
        self.assertIn("PERSONA-MARKER", work.system)
        self.assertIn("后台", work.system)
        self.assertEqual(work.schema, xiaoyou_module.reply_schema())
        self.assertEqual((self.store.session("default", "claude"),
                          self.store.session("default/c1", "claude")), ("s1", "s1"))
        # Nobody voiced it on her own line, so her line is told the next time it speaks.
        self.say("谢谢")
        self.assertIn("主人：（后台的事「明天的天气」有结果了）\n小幽：明天晴", self.claude.jobs[2].text)
        self.say("再见")
        self.assertEqual(self.claude.jobs[3].text, "再见")

    def test_things_run_side_by_side(self):
        self.codex.gate.clear()
        first = self.say("@codex one slow")
        second = self.say("@codex two slow")
        self.assertEqual((first.card, second.card), ("c1", "c2"))
        # Both are with the helper at the same time; neither waits for the other.
        self.assertTrue(until(lambda: len(self.codex.started) == 2))
        self.assertEqual(self.codex.jobs, [])
        self.assertTrue(self.say("几点了").reply.endswith("几点了"))
        self.codex.gate.set()
        self.assertEqual((self.settled("c1")["state"], self.settled("c2")["state"]), ("done", "done"))
        self.assertEqual((self.store.session("default/c1", "codex"),
                          self.store.session("default/c2", "codex")), ("s1", "s1"))

    def test_naming_a_helper_sends_the_work_there_without_asking_a_model(self):
        self.say("我们在聊重试逻辑")
        self.codex.gate.clear()
        turn = self.say("@codex 看看 parse() slow")
        self.assertEqual((turn.reply, turn.agent, turn.card, turn.started),
                         ("交给 codex 了", "codex", "c2", True))
        self.assertEqual(len(self.claude.jobs), 1)
        self.assertEqual(self.events[-2:], [("route", "codex", "mention"),
                                            ("handoff", "codex", "交给 codex 了")])
        card = self.xiaoyou.cards.get("c2")
        self.assertEqual((card["title"], card["state"]), ("看看 parse() slow", "working"))
        self.assertEqual(self.words("c2"), [("you", "@codex 看看 parse() slow"),
                                            ("xiaoyou", "交给 codex 了")])
        self.codex.gate.set()
        self.settled("c2")
        work = self.codex.jobs[0]
        # The helper was not there for the earlier turn, so it gets it as background.
        self.assertIn("我们在聊重试逻辑", work.text)
        self.assertTrue(work.text.endswith("看看 parse() slow"))
        self.assertIsNone(work.system)
        # She still does the talking: the result is voiced on her line.
        self.assertEqual(self.words("c2")[-1], ("xiaoyou", "re:" + self.claude.jobs[-1].text))
        self.assertIn("帮手 codex 做完了", self.claude.jobs[-1].text)
        # She was not there when it was handed over, so she is told that too.
        self.assertIn("主人：@codex 看看 parse() slow\n小幽：交给 codex 了", self.claude.jobs[-1].text)

    def test_naming_the_agent_that_speaks_for_her_makes_it_a_thing_too(self):
        turn = self.say("让 Claude 查一下明天的天气")
        self.assertEqual((turn.agent, turn.started, turn.reply), ("claude", True, "交给 claude 了"))
        self.settled("c1")
        job = self.claude.jobs[0]
        # It is told that it was named, and works with tools as her.
        self.assertIn("主人点名要 claude 来做这件事", job.text)
        self.assertTrue(job.text.endswith("让 Claude 查一下明天的天气"))
        self.assertIn("PERSONA-MARKER", job.system)
        self.assertEqual((job.plain, job.schema), (False, xiaoyou_module.reply_schema()))
        self.assertEqual(self.codex.jobs, [])
        self.assertEqual(self.events, [("route", "claude", "mention"),
                                       ("handoff", "claude", "交给 claude 了")])
        # Named by the caller instead of in the sentence: the same, with nothing to explain.
        self.events.clear()
        self.settled(self.say("几点了", asked="claude").card)
        self.assertEqual([event[:2] for event in self.events], [("route", "claude"), ("handoff", "claude")])
        self.assertTrue(self.claude.jobs[1].text.endswith("（任务：）\n几点了"))

    def test_more_can_be_added_to_a_thing_that_is_still_going(self):
        self.codex.gate.clear()
        self.say("@codex one slow")
        self.assertTrue(until(lambda: self.codex.started))
        self.claude.script = [amend("c1", "再加一个测试", "after", reply="好，做完接着加")]
        turn = self.say("顺便加个测试", card="c1")
        self.assertEqual((turn.card, turn.agent, turn.started, turn.mood), ("c1", "codex", True, "busy"))
        card = self.xiaoyou.cards.get("c1")
        self.assertEqual((card["state"], card["edits"]), ("working", 1))
        self.codex.gate.set()
        self.settled("c1")
        self.assertEqual([(job.text, job.session_id) for job in self.codex.jobs], [
            ("one slow", None), ("（主人补充了一句：再加一个测试。在刚才的基础上接着做。）", "s1")])
        # Only the final result is voiced.
        relays = [job.text for job in self.claude.jobs if "做完了" in job.text]
        self.assertEqual(len(relays), 1)
        self.assertIn("raw:（主人补充了一句", relays[0])

    def test_changing_the_request_starts_the_round_over_in_the_same_session(self):
        self.codex.gate.clear()
        self.say("@codex one slow")
        self.assertTrue(until(lambda: self.codex.started))
        # No card named: the one on screen is meant.
        self.claude.script = [amend("", "改成用 Python", "redo")]
        self.say("改成 Python", card="c1")
        self.assertEqual(self.xiaoyou.cards.get("c1")["edits"], 1)
        self.codex.gate.set()
        self.settled("c1")
        self.assertEqual([(job.text, job.session_id) for job in self.codex.jobs], [
            ("one slow", None),
            ("（主人改了要求：改成用 Python。按新的要求继续，已经做过的不用重复。）", "s1")])
        relays = [job.text for job in self.claude.jobs if "做完了" in job.text]
        self.assertEqual(len(relays), 1)
        self.assertIn("raw:（主人改了要求", relays[0])

    def test_a_finished_thing_can_be_picked_up_again(self):
        self.settled(self.say("@codex one").card)
        # She often writes the number without its letter; that is understood.
        self.claude.script = [amend(" 1 ", "再检查一遍", "redo", reply="让它再看一遍")]
        turn = self.say("再检查一遍", card="c1")
        self.assertEqual((turn.card, turn.started), ("c1", True))
        card = self.settled("c1")
        self.assertEqual((card["state"], card["edits"]), ("done", 1))
        self.assertEqual((self.codex.jobs[1].text, self.codex.jobs[1].session_id), ("再检查一遍", "s1"))
        # Starting on a card that is still going is the same as adding to it.
        self.codex.gate.clear()
        self.claude.script = [start("codex", "three slow", card="c1"), start("codex", "four", card="c1")]
        self.say("再来")
        self.assertTrue(until(lambda: "three slow" in self.codex.started))
        self.say("还有")
        self.codex.gate.set()
        self.settled("c1")
        self.assertIn("主人补充了一句：four", self.codex.jobs[-1].text)
        self.assertEqual(len(self.xiaoyou.cards.recent("default")), 1)

    def test_a_thing_can_be_cancelled(self):
        self.codex.gate.clear()
        self.say("@codex one slow")
        self.assertTrue(until(lambda: self.codex.started))
        self.claude.script = [{"reply": "好，不做了", "brief": "", "mood": "idle", "card": "c1",
                               "action": {"type": "cancel"}}]
        turn = self.say("算了不用了")
        self.assertEqual((turn.card, turn.started), ("c1", False))
        self.assertEqual(self.xiaoyou.cards.get("c1")["state"], "cancelled")
        self.codex.gate.set()
        self.assertTrue(until(lambda: len(self.codex.jobs) == 1))
        # The result that arrives afterwards is dropped: nothing is voiced, the card stays cancelled.
        self.assertFalse(any("做完了" in job.text for job in self.claude.jobs))
        self.assertEqual((self.xiaoyou.cards.get("c1")["state"], self.words("c1")[-1]),
                         ("cancelled", ("xiaoyou", "好，不做了")))
        self.assertFalse(self.xiaoyou.cancel("c1"))
        self.assertFalse(self.xiaoyou.cancel("c9"))

    def test_what_cannot_be_done_is_put_to_her_once_then_told_as_it_is(self):
        self.say("几点了")
        self.claude.script = [
            start("gemini", "x"),
            {"reply": "我这里没有 gemini", "brief": "没有", "mood": "oops"},
        ]
        turn = self.say("让 gemini 看看")
        self.assertEqual((turn.reply, turn.mood, turn.started), ("我这里没有 gemini", "oops", False))
        retry = self.claude.jobs[-1]
        self.assertIn("没有叫 gemini 的帮手（现在能找的：claude、codex）", retry.text)
        self.assertEqual(retry.session_id, "s2")
        # Still impossible the second time: she is not allowed to pretend.
        problems = [
            ({"card": "c9"}, "没有编号是 c9 的事"),
            ({"action": {"type": "cancel"}}, "cancel 要在 card 里写明"),
            ({"card": "c1", "action": {"type": "cancel"}}, "c1 这件事已经不在做了"),
            ({"card": "c1", "action": {"type": "amend", "task": "x"}}, "c1 这件事不是哪个帮手做的"),
            ({"card": "c1", "action": {"type": "amend"}}, "amend 要在 task 里"),
            ({"action": {"type": "start", "agent": "codex"}}, "start 要在 task 里"),
        ]
        for fields, problem in problems:
            wrong = dict({"reply": "好的", "brief": "", "mood": "happy"}, **fields)
            self.claude.script = [dict(wrong), dict(wrong)]
            turn = self.say("做不到的事")
            self.assertEqual(turn.mood, "oops", msg=problem)
            self.assertIn("这件事我没办成：" + problem, turn.reply)
        self.assertEqual(self.codex.jobs, [])
        with self.assertRaisesRegex(AgentError, "空的回复"):
            self.claude.script = [{"reply": " ", "brief": "", "mood": "idle"}]
            self.say("嗯")

    def test_a_helper_that_fails_is_reported_as_it_is(self):
        self.codex.fail_on = "task"
        card = self.settled(self.say("@codex the task").card)
        self.assertEqual((card["state"], card["mood"], card["brief"]),
                         ("failed", "oops", "codex 没做成：boom"))
        self.assertEqual(self.claude.jobs, [])
        self.assertEqual(self.store.transcript.turns("default")[-1]["reply"], "codex 没做成：boom")
        # Something unexpected inside the helper is a failure of that thing only.
        self.codex.fail_on = None
        card = self.settled(self.say("explode", asked="codex").card)
        self.assertEqual(card["state"], "failed")
        self.assertIn("ValueError", card["brief"])
        self.assertEqual(self.say("还在吗").reply[:3], "re:")

    def test_the_result_still_reaches_the_owner_when_nobody_can_voice_it(self):
        self.claude.fail_on = "帮手 codex 做完了"
        card = self.settled(self.say("@codex list files").card)
        self.assertEqual((card["state"], card["mood"]), ("done", "idle"))
        self.assertEqual(self.words("c1")[-1], ("xiaoyou", "raw:list files"))
        # No agent that speaks at all: the default agent just gets every sentence.
        alone, _ = make_xiaoyou(self.folder / "b", [self.codex])
        self.addCleanup(alone.close)
        turn = alone.hear("数一数", "default", "t1")
        self.assertEqual((turn.agent, turn.started), ("codex", True))
        self.assertEqual(alone.settle(turn.card, 5)["entries"][-1]["text"], "raw:数一数")

    def test_what_she_missed_is_told_once_and_survives_a_failed_turn(self):
        self.xiaoyou.share("default", [{"id": "o1", "text": "别处问的", "reply": "别处答的", "at": 1}])
        self.claude.fail_on = "试一次"
        with self.assertRaises(AgentError):
            self.say("试一次")
        self.claude.fail_on = None
        self.say("再试")
        self.assertIn("主人：别处问的\n小幽：别处答的", self.claude.jobs[-1].text)
        self.assertTrue(self.claude.jobs[-1].text.endswith("再试"))
        self.say("然后呢")
        self.assertEqual(self.claude.jobs[-1].text, "然后呢")
        self.assertEqual([turn["text"] for turn in self.store.transcript.turns("default")][-2:],
                         ["再试", "然后呢"])

    def test_starting_over_forgets_every_session_of_the_conversation(self):
        self.settled(self.say("@codex one").card)
        self.settled(self.say("@codex 别的对话", "work").card)
        self.assertEqual((self.store.session("default", "claude"),
                          self.store.session("default/c1", "codex")), ("s1", "s1"))
        self.assertTrue(self.xiaoyou.reset("default"))
        self.assertFalse(self.xiaoyou.reset("default"))
        self.assertEqual((self.store.session("default", "claude"),
                          self.store.session("default/c1", "codex")), (None, None))
        self.assertEqual(self.store.session("work/c2", "codex"), "s1")

    def test_words_passed_on_by_another_runtime_are_not_passed_on_again(self):
        other = Scripted("pc", kind="remote")
        xiaoyou, _ = make_xiaoyou(self.folder / "d", [self.claude, other])
        self.addCleanup(xiaoyou.close)
        xiaoyou.hear("hi", "default", "t1")
        self.assertIn("pc：pc helper", self.claude.jobs[-1].system)
        xiaoyou.hear("hi", "default", "t2", hop=1)
        self.assertEqual(self.claude.jobs[-1].hop, 1)
        self.assertNotIn("pc helper", self.claude.jobs[-1].system)
        # Even when it is named.
        self.assertEqual(xiaoyou.hear("@pc hi", "default", "t3", hop=1).agent, "claude")
        self.assertEqual(xiaoyou.hear("hi", "default", "t4", asked="pc", hop=1).agent, "claude")
        self.assertEqual(other.jobs, [])
        only_remote, _ = make_xiaoyou(self.folder / "e", [other])
        self.addCleanup(only_remote.close)
        with self.assertRaises(AgentError):
            only_remote.hear("hi", "default", "t", hop=1)

    def test_a_limit_on_how_many_things_run_at_once(self):
        xiaoyou, _ = make_xiaoyou(self.folder / "f", [self.claude, self.codex], max_parallel=1)
        self.addCleanup(xiaoyou.close)
        self.codex.gate.clear()
        xiaoyou.hear("@codex one slow", "default", "t1")
        xiaoyou.hear("@codex two slow", "default", "t2")
        self.assertTrue(until(lambda: self.codex.started == ["one slow"]))
        self.assertEqual([card["queued"] for card in xiaoyou.cards.recent("default")], [False, True])
        self.codex.gate.set()
        self.assertEqual(xiaoyou.settle("c2", 5)["state"], "done")
        self.assertEqual([card["queued"] for card in xiaoyou.cards.recent("default")], [False, False])

    def test_a_restart_marks_what_was_still_going(self):
        self.codex.gate.clear()
        self.say("@codex one slow")
        again, _ = make_xiaoyou(self.folder, [Scripted("claude"), Scripted("codex", speaks=False)])
        self.addCleanup(again.close)
        card = again.cards.get("c1")
        self.assertEqual((card["state"], card["entries"][-1]["text"]),
                         ("failed", "Runtime 重启了，这件事没做完"))


class ServiceTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.agent = Scripted("claude")
        self.codex = Scripted("codex", speaks=False)
        self.xiaoyou, self.store = make_xiaoyou(self.folder, [self.agent, self.codex])
        self.service = Service(self.xiaoyou, self.store)
        self.addCleanup(self.service.close)
        self.addCleanup(self.agent.gate.set)
        self.addCleanup(self.codex.gate.set)

    def finish(self, message):
        done = self.service.get(message["id"], wait=5)
        self.assertIn(done["status"], ("done", "failed"))
        return done

    def test_her_line_takes_one_sentence_at_a_time_and_continues_the_session(self):
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
        self.assertEqual((done["brief"], done["mood"], done["error"], done["agent"], done["card"]),
                         ("b:one slow", "happy", None, "claude", "c1"))

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
        self.assertEqual([card["id"] for card in self.service.cards("work")], ["c2"])

    def test_same_client_id_is_not_run_twice(self):
        first = self.service.submit("pay", client_id="cmd-1")
        self.finish(first)
        again = self.service.submit("pay", client_id="cmd-1")
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(again["status"], "done")
        self.assertEqual(len(self.agent.jobs), 1)

    def test_a_message_is_done_when_the_work_is_handed_out_and_the_feed_tells_the_rest(self):
        self.agent.script = [
            start("codex", "check slow", "查一下", reply="我去问 codex"),
            {"reply": "在查", "brief": "在查", "mood": "busy", "card": "c1"},
            {"reply": "好了", "brief": "好了", "mood": "happy"},
        ]
        self.codex.gate.clear()
        done = self.finish(self.service.submit("帮我查一下"))
        self.assertEqual((done["status"], done["reply"], done["agent"], done["card"], done["mood"]),
                         ("done", "我去问 codex", "codex", "c1", "busy"))
        self.assertEqual((done["stage"], done["helper"]), (None, None))
        self.assertEqual([(event["kind"], event["agent"]) for event in done["events"]],
                         [("route", "claude"), ("handoff", "codex")])
        feed = self.service.feed("default")
        self.assertEqual([(card["id"], card["state"]) for card in feed["cards"]], [("c1", "working")])
        self.assertEqual(feed["approvals"], [])
        # A second sentence does not wait for the first thing: it is answered while that goes on.
        quick = self.finish(self.service.submit("好了吗", card="c1"))
        self.assertEqual((quick["reply"], quick["card"]), ("在查", "c1"))
        self.assertEqual(self.xiaoyou.cards.get("c1")["state"], "working")
        feed = self.service.feed("default", feed["seq"])
        self.assertEqual(len(feed["cards"]), 1)
        # Nothing new: the caller waits, and hears as soon as the helper is done.
        self.assertEqual(self.service.feed("default", feed["seq"], wait=0.05)["cards"], [])
        waiter = {}
        thread = threading.Thread(
            target=lambda: waiter.update(self.service.feed("default", feed["seq"], wait=5)))
        thread.start()
        self.codex.gate.set()
        thread.join(5)
        self.assertGreater(waiter["seq"], feed["seq"])
        card = self.xiaoyou.settle("c1", 5)
        self.assertEqual((card["state"], card["entries"][-1]["text"]), ("done", "好了"))
        # The report was voiced on her line, after the sentences that were already there.
        self.assertIn("帮手 codex 做完了", self.agent.jobs[-1].text)
        self.assertEqual(self.agent.jobs[-1].session_id, "s2")
        # A caller may name the agent; one that does not exist is refused up front.
        named = self.finish(self.service.submit("list", agent="codex"))
        self.assertEqual((named["asked"], named["agent"], named["reply"], named["card"]),
                         ("codex", "codex", "交给 codex 了", "c2"))
        with self.assertRaisesRegex(RequestError, "gemini"):
            self.service.submit("hi", agent="gemini")
        self.assertEqual([agent["name"] for agent in self.service.agents()], ["claude", "codex"])
        self.assertEqual([agent["default"] for agent in self.service.agents()], [True, False])
        self.assertEqual([card["id"] for card in self.service.cards()], ["c1", "c2"])

    def test_a_thing_can_be_cancelled_by_the_caller(self):
        self.codex.gate.clear()
        message = self.finish(self.service.submit("one slow", agent="codex"))
        self.assertEqual(self.service.cancel(message["card"])["state"], "cancelled")
        # Cancelling again changes nothing; a card that does not exist is said to be missing.
        self.assertEqual(self.service.cancel(message["card"])["state"], "cancelled")
        self.assertIsNone(self.service.cancel("c9"))

    def test_another_runtime_waits_for_the_end_of_the_thing(self):
        self.codex.gate.clear()
        message = self.service.submit("check slow", agent="codex", hop=1)
        self.assertTrue(until(lambda: self.codex.started))
        waiting = self.service.get(message["id"])
        self.assertEqual((waiting["status"], waiting["reply"], waiting["card"]),
                         ("running", "交给 codex 了", "c1"))
        self.codex.gate.set()
        done = self.finish(message)
        self.assertEqual((done["status"], done["reply"]), ("done", "re:" + self.agent.jobs[-1].text))
        self.codex.fail_on = "bad"
        failed = self.finish(self.service.submit("bad", agent="codex", hop=1))
        self.assertEqual((failed["status"], failed["error"]), ("failed", "codex 没做成：boom"))

    def test_failure_is_reported_and_keeps_the_session_and_the_worker(self):
        self.finish(self.service.submit("ok"))
        self.agent.fail_on = "bad"
        failed = self.finish(self.service.submit("bad"))
        self.assertEqual((failed["status"], failed["error"], failed["mood"]), ("failed", "boom", "oops"))
        # The sentence and what went wrong are on a card, so the conversation shows them.
        card = self.xiaoyou.cards.get(failed["card"])
        self.assertEqual((card["state"], [entry["text"] for entry in card["entries"]]),
                         ("failed", ["bad", "boom"]))
        crashed = self.finish(self.service.submit("explode"))
        self.assertEqual(crashed["status"], "failed")
        self.assertIn("ValueError", crashed["error"])
        self.assertEqual(self.store.session("default", "claude"), "s1")
        self.assertEqual(self.finish(self.service.submit("after"))["status"], "done")

    def test_bad_requests(self):
        for args in (("",), ("   ",), (None,), ("x" * 8001,), ("hi", "a/b"), ("hi", ""),
                     ("hi", "default", "has space"), ("hi", 5), ("hi", "default", None, "a b"),
                     ("hi", "default", None, None, 9), ("hi", "default", None, None, True),
                     ("hi", "default", None, None, "1"), ("hi", "default", None, None, 0, "c 1"),
                     ("hi", "default", None, None, 0, 7)):
            with self.assertRaises(RequestError, msg=repr(args)[:60]):
                self.service.submit(*args)
        self.assertIsNone(self.service.get("nope"))
        for args in (("a b",), ("default", -1), ("default", "1"), ("default", True)):
            with self.assertRaises(RequestError, msg=repr(args)):
                self.service.feed(*args)
        with self.assertRaises(RequestError):
            self.service.cards("a/b")


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
        self.assertEqual((done["mood"], done["agent"], done["card"]), ("happy", "echo", "c1"))
        status, feed = self.call("GET", "/v1/feed?after=0&wait=5")
        self.assertEqual((status, feed["seq"] > 0, feed["approvals"]), (200, True, []))
        self.assertEqual([(card["id"], card["state"], card["entries"][-1]["text"])
                          for card in feed["cards"]], [("c1", "done", "（回声）你好")])
        self.assertEqual(self.call("GET", "/v1/feed?after=%d&wait=0.05" % feed["seq"])[1]["cards"], [])
        self.assertEqual(self.call("GET", "/v1/feed?conversation=work")[1]["cards"], [])
        self.assertEqual([card["id"] for card in self.call("GET", "/v1/cards")[1]["cards"]], ["c1"])
        # Nothing changes after the end, so asking with the last revision returns at once.
        self.assertEqual(
            self.call("GET", "/v1/messages/%s?wait=5&rev=%d" % (message["id"], done["rev"]))[1]["rev"],
            done["rev"])
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
        message = self.call("POST", "/v1/messages", {"text": "hi", "agent": "echo", "card": "c9"})[1]
        done = self.call("GET", "/v1/messages/%s?wait=5" % message["id"])[1]
        self.assertEqual((done["asked"], done["events"][0]["text"]), ("echo", "asked"))
        # Asked for by name: the message is over once the work is handed out; the card has the rest.
        self.assertEqual((done["status"], done["reply"], done["card"]), ("done", "交给 echo 了", "c1"))
        for _ in range(100):
            card = self.call("GET", "/v1/cards")[1]["cards"][0]
            if card["state"] == "done":
                break
        self.assertEqual((card["state"], card["entries"][-1]["text"]), ("done", "（回声）hi"))
        self.assertEqual(self.call("POST", "/v1/cards/c1/cancel", {})[1]["state"], "done")
        self.assertEqual(self.call("POST", "/v1/cards/c9/cancel", {})[0], 404)
        self.assertEqual(self.call("POST", "/v1/cards/c1/cancel", {}, token=None)[0], 401)
        self.assertEqual(self.call("POST", "/v1/approvals/a1", {"decision": "allow"})[0], 404)
        self.assertEqual(self.call("POST", "/v1/approvals/a1", {"decision": "perhaps"})[0], 400)
        self.assertEqual(self.call("POST", "/v1/approvals/a1", ["allow"])[0], 400)
        self.assertEqual(self.call("POST", "/v1/approvals/a1", {"decision": "allow"}, token=None)[0], 401)
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
        self.assertEqual(self.call("GET", "/v1/messages/unknown?rev=x")[0], 400)
        self.assertEqual(self.call("GET", "/v1/feed?wait=999")[0], 400)
        self.assertEqual(self.call("GET", "/v1/feed?after=x")[0], 400)
        self.assertEqual(self.call("GET", "/v1/feed?after=-1")[0], 400)
        self.assertEqual(self.call("GET", "/v1/feed?conversation=a%20b")[0], 400)
        self.assertEqual(self.call("GET", "/v1/cards?conversation=a%20b")[0], 400)
        self.assertEqual(self.call("GET", "/v1/feed", token=None)[0], 401)
        self.assertEqual(self.call("POST", "/v1/messages", {"text": "hi", "card": "c 1"})[0], 400)
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
        lead.script = [start("pc", "列出桌面上的文件", "桌面上有什么", reply="我让那台电脑看看")]
        xiaoyou, _ = make_xiaoyou(self.folder / "here", [lead, self.remote()])
        self.addCleanup(xiaoyou.close)
        turn = xiaoyou.hear("那台电脑上有什么", "default", "t1")
        self.assertEqual((turn.agent, turn.started), ("pc", True))
        # The other side is Xiaoyou too, so what it says is already in her words.
        card = xiaoyou.settle(turn.card, 10)
        self.assertEqual((card["state"], card["entries"][-1]["text"]), ("done", "（回声）列出桌面上的文件"))
        self.assertEqual(len(lead.jobs), 1)

    def test_a_named_agent_on_the_other_side_is_waited_for(self):
        # Over there the sentence becomes a thing in the background; the caller here still
        # gets the final words, not just "handed over".
        request = urllib.request.Request(
            self.other + "/v1/messages", method="POST",
            data=json.dumps({"text": "在吗", "agent": "echo", "hop": 1}).encode())
        request.add_header("Authorization", "Bearer " + OTHER_TOKEN)
        with urllib.request.urlopen(request, timeout=10) as response:
            message = json.loads(response.read())
        request = urllib.request.Request(self.other + "/v1/messages/%s?wait=10" % message["id"])
        request.add_header("Authorization", "Bearer " + OTHER_TOKEN)
        with urllib.request.urlopen(request, timeout=15) as response:
            done = json.loads(response.read())
        self.assertEqual((done["status"], done["reply"]), ("done", "（回声）在吗"))

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
        self.assertEqual(post("/v1/voice?card=c%201", make_wav())[0], 400)
        status, message = post("/v1/voice?client_id=p1&conversation=walk&card=c7", make_wav(2))
        self.assertEqual((status, message["kind"], message["conversation"], message["card"]),
                         (202, "voice", "walk", "c7"))
        request = urllib.request.Request(base + "/v1/messages/%s?wait=10" % message["id"])
        request.add_header("Authorization", "Bearer " + TOKEN)
        with urllib.request.urlopen(request, timeout=15) as response:
            done = json.loads(response.read())
        self.assertEqual((done["status"], done["text"], done["reply"], done["card"]),
                         ("done", "你好小幽 2.0", "（回声）你好小幽 2.0", "c1"))


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
        # Named: the work goes to the background; the command waits and prints the result.
        self.assertIn("[happy]", out)
        self.assertIn("（回声）", out)
        self.assertIn("asked", err)
        self.assertIn("c2：交给 echo 了", err)
        store = Store(self.folder / "state")
        self.assertEqual((store.session("default", "echo"), store.session("default/c2", "echo")),
                         ("echo-1", "echo-1"))
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
