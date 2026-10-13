#!/usr/bin/env python3
"""Desktop hook metadata validation; no real conversations or models are used."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNTIME))

from xiaoyou_runtime import desktop_hook as hook  # noqa: E402


class DesktopHookTests(unittest.TestCase):
    def payload(self, name="UserPromptSubmit", **fields):
        return dict(session_id="session-test", cwd="/example/project",
                    hook_event_name=name, **fields)

    def invoke(self, directory, raw):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdin", io.BytesIO(raw)), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = hook.main(["--state-dir", str(directory), "--agent", "codex"])
        return result, stdout.getvalue(), stderr.getvalue()

    def test_lifecycle_and_permission_mapping(self):
        for name, status in hook.EVENT_STATUS.items():
            for agent in ("codex", "claude"):
                with self.subTest(name=name, agent=agent):
                    self.assertEqual(hook.normalize(agent, self.payload(name), 10)["status"], status)
        event = self.payload("Notification", notification_type="permission_prompt")
        self.assertEqual(hook.normalize("claude", event, 10)["status"], "waiting")
        self.assertIsNone(hook.normalize("claude", self.payload("Notification"), 10))
        self.assertIsNone(hook.normalize("codex", self.payload("SubagentStop"), 10))

    def test_only_allowlisted_metadata_is_recorded(self):
        event = self.payload(prompt="private prompt", last_assistant_message="private reply",
                             tool_input={"command": "private command"}, token="private token",
                             tool_response="private output", session_title="Example",
                             model="test-model", prompt_id="prompt-test")
        clean = hook.normalize("claude", event, 10)
        self.assertEqual(clean, {"agent": "claude", "session_id": "session-test",
                                 "cwd": "/example/project", "status": "running",
                                 "observed_at": 10.0, "title": "Example",
                                 "model": "test-model", "turn_id": "prompt-test"})
        self.assertNotIn("private", json.dumps(clean))

    def test_required_metadata_is_validated_and_bounded(self):
        for field, value in (("session_id", None), ("session_id", ""),
                             ("session_id", "x" * (hook.MAX_SESSION_CHARS + 1)),
                             ("session_id", "session\nsecret"), ("cwd", False),
                             ("cwd", "relative"), ("cwd", "/bad\0path"),
                             ("cwd", "/" + "x" * hook.MAX_PATH_CHARS)):
            payload = self.payload()
            payload[field] = value
            with self.subTest(field=field, value_type=type(value).__name__), \
                    self.assertRaises(ValueError):
                hook.normalize("codex", payload, 10)
        for at in (True, -1, float("inf"), float("nan"), "10"):
            with self.subTest(at=str(at)), self.assertRaises(ValueError):
                hook.normalize("codex", self.payload(), at)
        for raw in ([], None, "secret"):
            with self.assertRaises(ValueError):
                hook.normalize("codex", raw, 10)

    def test_optional_bad_metadata_is_omitted(self):
        payload = self.payload(session_title="x" * (hook.MAX_TITLE_CHARS + 1),
                               model="secret\nvalue", turn_id=[])
        self.assertEqual(set(hook.normalize("codex", payload, 10)),
                         {"agent", "session_id", "cwd", "status", "observed_at"})

    def test_subagents_and_sidechains_are_ignored(self):
        for fields in ({"isSidechain": True}, {"is_sidechain": True},
                       {"agent_id": "subagent"}, {"parent_thread_id": "parent"},
                       {"source": {"subagent": {"thread_spawn": {}}}},
                       {"transcript_path": "/example/subagents/agent-test.jsonl"}):
            with self.subTest(fields=fields):
                self.assertIsNone(hook.normalize("claude", self.payload(**fields), 10))

    def test_cli_is_silent_and_fail_open_without_leaking_input(self):
        with tempfile.TemporaryDirectory() as directory:
            for raw in (b'{"private-secret":', b"[]", b"\xff",
                        b"x" * (hook.MAX_INPUT_BYTES + 1)):
                result, stdout, stderr = self.invoke(directory, raw)
                self.assertEqual(result, 0)
                self.assertEqual(stdout, "")
                self.assertIn("could not record", stderr)
                self.assertNotIn("private-secret", stderr)
            self.assertFalse((Path(directory) / "desktop-events").exists())
            raw = json.dumps(self.payload()).encode()
            result, stdout, stderr = self.invoke(directory, raw)
            self.assertEqual((result, stdout, stderr), (0, "", ""))
            self.assertEqual(len(list((Path(directory) / "desktop-events").glob("*.json"))), 1)

    def test_cli_write_failure_is_fail_open(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(hook, "record", side_effect=OSError("private path secret")):
            result, stdout, stderr = self.invoke(directory, json.dumps(self.payload()).encode())
            self.assertEqual((result, stdout), (0, ""))
            self.assertNotIn("private path secret", stderr)

    def test_atomic_private_spool_and_safe_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event = hook.normalize("codex", self.payload(), 10)
            event["session_id"] = "../../example/session"
            path = hook.record(root, event)
            self.assertEqual(path.parent, root / "desktop-events")
            self.assertEqual(len(path.stem), 64)
            self.assertEqual(json.loads(path.read_text()), event)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            snapshots = [dict(event, observed_at=float(at)) for at in range(20)]
            with ThreadPoolExecutor(max_workers=4) as workers:
                list(workers.map(lambda value: hook.record(root, value), snapshots))
            self.assertIn(json.loads(path.read_text()), snapshots)
            self.assertEqual(list(path.parent.iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
