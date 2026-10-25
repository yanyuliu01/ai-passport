#!/usr/bin/env python3
"""Native handoff, shared-service isolation and CLI state safety; no models."""

import contextlib
import dataclasses
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xiaoyou_runtime.helpers import agents, codex_app, desktop_handoff, factory
from xiaoyou_runtime.config import AgentSpec, ConfigError, load
from xiaoyou_runtime.helpers.tasks import Task, Tasks
from xiaoyou_runtime.world.store import Store
from test_runtime import FAKE_CODEX_APP, write_config

export_session = desktop_handoff.export_session


class DesktopHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.spec = AgentSpec(name="cc", type="claude_code", description="", aliases=[],
                              speaks=False, timeout_seconds=60, command=["claude"],
                              workdir=self.root, config_dir=self.root / "isolated")
        self.agent = factory.create(self.spec)
        self.env = mock.patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        exporter = mock.patch.object(desktop_handoff, "export_session", return_value="")
        self.exporter = exporter.start()
        self.addCleanup(exporter.stop)

    def test_claude_opens_original_session_with_its_own_home_without_prompt(self):
        run = mock.Mock(return_value=SimpleNamespace(returncode=0))
        self.assertEqual(desktop_handoff.open_session(self.agent, "session-1", run=run,
                                                      platform="darwin"), "")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["claude", "--desktop", "--resume", "session-1"])
        self.assertEqual(kwargs["env"]["CLAUDE_CONFIG_DIR"], str(self.spec.config_dir))
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["timeout"], 15)

    def test_missing_original_transcript_does_not_open_wrong_thread(self):
        spec = dataclasses.replace(self.spec, type="codex", command=["codex"])
        run = mock.Mock()
        self.exporter.return_value = "找不到原始记录"
        self.assertIn("找不到", desktop_handoff.open_session(
            factory.create(spec), "thread-1", run=run, platform="darwin"))
        run.assert_not_called()

    def test_import_is_session_scoped_idempotent_and_never_overwrites_desktop_work(self):
        source = self.root / "isolated" / "projects" / "project"
        source.mkdir(parents=True)
        original = source / "session-1.jsonl"
        original.write_text('{"message":"original"}\n')
        (source / "unrelated.jsonl").write_text("private unrelated conversation")
        home = self.root / "desktop"
        env = {"CLAUDE_CONFIG_DIR": str(self.root / "isolated")}
        self.assertEqual(export_session(self.spec, env, "session-1", home=home), "")
        target = home / ".claude" / "projects" / "project" / original.name
        self.assertEqual(target.read_bytes(), original.read_bytes())
        self.assertEqual(list(target.parent.glob("*.jsonl")), [target])
        self.assertEqual(export_session(self.spec, env, "session-1", home=home), "")
        target.write_text("desktop follow-up")
        self.assertIn("未覆盖", export_session(self.spec, env, "session-1", home=home))
        self.assertEqual(target.read_text(), "desktop follow-up")
        self.assertEqual(original.read_text(), '{"message":"original"}\n')
        self.assertFalse(list(target.parent.glob("*.tmp")))

    def test_codex_import_retains_date_layout(self):
        source = self.root / "isolated" / "sessions" / "2026" / "10" / "15"
        source.mkdir(parents=True)
        original = source / "rollout-date-thread-1.jsonl"
        original.write_text('{"type":"session_meta"}\n')
        spec = dataclasses.replace(self.spec, type="codex")
        home = self.root / "desktop"
        self.assertEqual(export_session(spec, {"CODEX_HOME": str(self.root / "isolated")},
                                        "thread-1", home=home), "")
        self.assertEqual((home / ".codex" / "sessions" / "2026" / "10" / "15" /
                          original.name).read_bytes(), original.read_bytes())

    def test_import_refuses_ambiguous_sessions_and_destination_symlinks(self):
        root = self.root / "isolated" / "projects"
        for project in ("one", "two"):
            (root / project).mkdir(parents=True)
            (root / project / "session-1.jsonl").write_text("original")
        env = {"CLAUDE_CONFIG_DIR": str(self.root / "isolated")}
        home = self.root / "desktop"
        self.assertIn("唯一", export_session(self.spec, env, "session-1", home=home))
        (root / "two" / "session-1.jsonl").unlink()
        outside = self.root / "outside.jsonl"
        outside.write_text("unrelated")
        target = home / ".claude" / "projects" / "one" / "session-1.jsonl"
        target.parent.mkdir(parents=True)
        target.symlink_to(outside)
        self.assertTrue(export_session(self.spec, env, "session-1", home=home))
        self.assertEqual(outside.read_text(), "unrelated")

    def test_codex_uses_native_deep_link_with_shared_home(self):
        spec = dataclasses.replace(self.spec, type="codex", command=["codex"], config_dir=None)
        run = mock.Mock(return_value=SimpleNamespace(returncode=0))
        self.assertEqual(desktop_handoff.open_session(factory.create(spec), "thread-1",
                                                      run=run, platform="darwin"), "")
        self.assertEqual(run.call_args.args[0], ["open", "codex://threads/thread-1"])

    def test_failure_is_bounded_and_does_not_echo_cli_credentials(self):
        run = mock.Mock(return_value=SimpleNamespace(returncode=1, stderr="secret"))
        warning = desktop_handoff.open_session(self.agent, "session-1", run=run, platform="darwin")
        self.assertIn("退出码 1", warning)
        self.assertNotIn("secret", warning)
        run.side_effect = subprocess.TimeoutExpired("claude", 15)
        self.assertIn("失败", desktop_handoff.open_session(self.agent, "session-1",
                                                           run=run, platform="darwin"))

    @unittest.skipIf(os.name == "nt", "POSIX PTY")
    def test_real_launcher_supplies_terminal_and_bounds_a_hung_child(self):
        command = [sys.executable, "-c", "import sys; sys.exit(0 if all(s.isatty() for s in "
                   "(sys.stdin, sys.stdout, sys.stderr)) else 1)"]
        self.assertEqual(desktop_handoff._terminal_launch(command, self.root, None).returncode, 0)
        with self.assertRaises(subprocess.TimeoutExpired):
            desktop_handoff._terminal_launch([sys.executable, "-c", "import time; time.sleep(20)"],
                                             self.root, None, timeout=0.1)

    def test_invalid_session_and_api_agents_do_not_launch_desktop(self):
        run = mock.Mock()
        for sid in (None, "", "--flag", "a/b", "x?query=secret"):
            self.assertTrue(desktop_handoff.open_session(self.agent, sid, run=run, platform="darwin"))
        spec = dataclasses.replace(self.spec, env={"ANTHROPIC_API_KEY": "secret"})
        self.assertIn("订阅", desktop_handoff.open_session(factory.create(spec), "session-1",
                                                           run=run, platform="darwin"))
        run.assert_not_called()

    def test_launcher_does_not_inherit_hosts_provider_routing(self):
        os.environ.update(ANTHROPIC_BASE_URL="https://provider.invalid", ANTHROPIC_AUTH_TOKEN="secret")
        run = mock.Mock(return_value=SimpleNamespace(returncode=0))
        self.assertEqual(desktop_handoff.open_session(self.agent, "session-1", run=run,
                                                      platform="darwin"), "")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", run.call_args.kwargs["env"])
        self.assertEqual(os.environ["ANTHROPIC_AUTH_TOKEN"], "secret")

    def test_only_final_successful_background_task_is_opened(self):
        class Worker(agents.Agent):
            def run(inner, job):
                if job.text == "first":
                    task.after.append("second")
                return agents.Outcome("done", "session-1")
        worker = Worker(dataclasses.replace(self.spec, desktop_open=True))
        task = Task("c1", "default", worker, "first")
        store = Store(self.root / "state")
        finish = mock.Mock()
        tasks = Tasks(store, finish)
        with mock.patch.object(desktop_handoff, "open_session", return_value="") as opened:
            tasks._work(task)
            opened.assert_called_once_with(worker, "session-1")
            finish.assert_called_once()
            desktop_handoff.completed(task, None, "failure")
            opened.assert_called_once()
            task.cancelled = True
            desktop_handoff.completed(task, agents.Outcome("done", "session-1"), None)
            opened.assert_called_once()

    def test_cli_list_and_refusal_leave_running_state_files_untouched(self):
        config = load(write_config(self.root, agents={"cc": {"type": "claude_code"}}), {})
        config.state_dir.mkdir(parents=True, exist_ok=True)
        for filename, data in (("cards.json", {"cards": [{"id": "c1", "state": "working",
                                  "conversation": "default", "title": "Work"}]}),
                               ("sessions.json", {"default/c1": {"cc": "session-1"}})):
            (config.state_dir / filename).write_text(json.dumps(data))
        before = {p: p.read_bytes() for p in config.state_dir.iterdir()}
        args = SimpleNamespace(action="list", task="c1", agent="cc", conversation=None)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(desktop_handoff.run_cli(config, args), 0)
        self.assertIn("session-1", output.getvalue())
        args.action = "open"
        with mock.patch.object(desktop_handoff, "open_session") as opened, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(desktop_handoff.run_cli(config, args), 2)
            opened.assert_not_called()
        self.assertEqual(before, {p: p.read_bytes() for p in config.state_dir.iterdir()})

    def test_config_rejects_wrong_modes_and_types(self):
        for spec in ({"type": "echo", "desktop_open": True},
                     {"type": "claude_code", "app_server_socket": "/tmp/socket"},
                     {"type": "codex", "mode": "exec", "app_server_socket": "/tmp/socket"},
                     {"type": "codex", "desktop_open": "true"}):
            with self.assertRaises(ConfigError):
                load(write_config(self.root, agents={"worker": spec}), {})

    def test_proxy_protocol_ignores_other_threads_and_preserves_turn_result(self):
        fake = self.root / "fake.py"
        # Emit unrelated progress/completion and an approval request. None may
        # contaminate this task or be answered by its permission handler.
        injection = '''
send({"method": "item/completed", "params": {"threadId": "other", "item": {"type": "agentMessage", "text": "wrong"}}})
send({"method": "turn/completed", "params": {"threadId": "other", "turn": {"id": "turn-1", "status": "completed"}}})
send({"id": 999, "method": "item/commandExecution/requestApproval", "params": {"threadId": "other"}})
'''
        marker = 'send({"method": "turn/started",'
        source = FAKE_CODEX_APP.replace(marker, injection + '\n' + marker, 1)
        fake.write_text(source)
        log = self.root / "events.jsonl"
        os.environ["FAKE_CODEX_LOG"] = str(log)
        config = load(write_config(self.root, agents={"codex": {
            "type": "codex", "command": [sys.executable, str(fake)],
            "app_server_socket": "service.sock"}}), {})
        control = agents.Control()
        control.ask = mock.Mock(return_value=False)
        outcome = factory.create(config.agents[0]).run(agents.Job("hello", control=control))
        self.assertEqual(outcome.text, "final hello")
        control.ask.assert_not_called()
        events = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(events[0]["args"], ["app-server", "proxy", "--sock",
                                              str((self.root / "service.sock").resolve())])
        self.assertFalse(any(e.get("got", {}).get("method") == "turn/interrupt" for e in events))

    def test_shared_timeout_sends_targeted_interrupt_before_disconnect(self):
        fake = self.root / "fake.py"
        fake.write_text(FAKE_CODEX_APP)
        log = self.root / "events.jsonl"
        os.environ.update(FAKE_CODEX_LOG=str(log), FAKE_CODEX_MODE="hang")
        config = load(write_config(self.root, agents={"codex": {
            "type": "codex", "command": [sys.executable, str(fake)],
            "app_server_socket": "service.sock"}}), {})
        with self.assertRaises(agents.AgentError):
            factory.create(config.agents[0]).run(agents.Job("hello", timeout=0.2,
                                                          control=agents.Control()))
        events = [json.loads(line).get("got", {}) for line in log.read_text().splitlines()]
        interrupts = [e["params"] for e in events if e.get("method") == "turn/interrupt"]
        self.assertTrue(interrupts)
        self.assertTrue(all(p == {"threadId": "thread-1", "turnId": "turn-1"} for p in interrupts))


if __name__ == "__main__":
    unittest.main()
