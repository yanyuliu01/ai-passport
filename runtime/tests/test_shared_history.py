"""Cross-agent history and context delivery; no real model or session writes."""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xiaoyou_runtime import shared_history as history
from xiaoyou_runtime.desktop_content import visible_text


class SharedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "project"
        self.repo.mkdir()
        self.config = self.repo / "runtime" / "config.json"
        self.config.parent.mkdir()
        self.state = self.config.parent / "state"
        self.state.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.write(self.config, {"state_dir": "state", "agents": []})
        self.card = {"id": "c1", "title": "App update", "state": "done", "agent": "cc",
                     "created_at": 999, "updated_at": 999,
                     "entries": [{"role": "you", "text": "change the app", "at": 10},
                                 {"role": "xiaoyou", "text": "APK built, not installed", "at": 20}]}
        self.write(self.state / "cards.json", {"cards": [self.card]})

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def jsonl(self, path, events):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

    def collect(self, **kwargs):
        return history.snapshot(self.config, self.repo, home=self.home, now=1000, **kwargs)

    def codex(self, sid="codex-session", cwd=None, source="cli", root=None, text="Codex result"):
        path = (root or self.home / ".codex") / "sessions" / ("rollout-" + sid + ".jsonl")
        self.jsonl(path, [
            {"timestamp": "1970-01-01T00:00:30Z", "type": "session_meta",
             "payload": {"id": sid, "cwd": str(cwd or self.repo), "source": source}},
            {"timestamp": "1970-01-01T00:00:31Z", "type": "response_item",
             "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "request"}]}},
            {"timestamp": "1970-01-01T00:00:32Z", "type": "response_item",
             "payload": {"type": "message", "role": "assistant", "phase": "final_answer",
                         "content": [{"type": "output_text", "text": text}]}},
            {"timestamp": "1970-01-01T00:00:33Z", "type": "response_item",
             "payload": {"type": "message", "role": "assistant", "channel": "analysis",
                         "content": [{"type": "output_text", "text": "PRIVATE REASONING"}]}},
            {"timestamp": "1970-01-01T00:00:34Z", "type": "response_item",
             "payload": {"type": "function_call_output", "output": "PRIVATE TOOL"}},
        ])
        return path

    def claude(self, sid="cc-session", root=None):
        path = (root or self.home / ".claude") / "projects" / "project" / (sid + ".jsonl")
        self.jsonl(path, [
            {"timestamp": "1970-01-01T00:00:35Z", "sessionId": sid, "cwd": str(self.repo),
             "type": "user", "message": {"content": "Claude request"}},
            {"timestamp": "1970-01-01T00:00:36Z", "sessionId": sid, "cwd": str(self.repo),
             "type": "assistant", "message": {"stop_reason": "end_turn", "content": [
                 {"type": "text", "text": "Claude result"}, {"type": "thinking", "thinking": "SECRET THOUGHT"}]}},
            {"timestamp": "1970-01-01T00:00:37Z", "sessionId": sid, "cwd": str(self.repo),
             "type": "user", "toolUseResult": {}, "message": {"content": "SECRET TOOL RESULT"}},
        ])
        return path

    def test_collects_both_agents_and_native_tasks_without_mutating_sources(self):
        paths = [self.codex(), self.claude(), self.state / "cards.json"]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
        with mock.patch("subprocess.Popen", side_effect=AssertionError("must not start an agent")):
            data = self.collect()
        self.assertEqual({task["agent"] for task in data["tasks"]}, {"cc", "codex", "claude"})
        text = json.dumps(data)
        for secret in ("PRIVATE REASONING", "PRIVATE TOOL", "SECRET THOUGHT", "SECRET TOOL RESULT"):
            self.assertNotIn(secret, text)
        for path in paths:
            self.assertEqual(before[path], (path.read_bytes(), path.stat().st_mtime_ns))

    def test_import_timestamp_is_not_used_as_activity_time(self):
        self.assertEqual(self.collect()["tasks"][0]["updated_at"], 20)

    def test_configured_isolated_cc_directory_is_included(self):
        root = self.root / "isolated cc"
        self.claude(root=root)
        self.write(self.config, {"agents": [{"type": "claude_code", "config_dir": str(root)}]})
        self.assertIn("claude", {task["agent"] for task in self.collect()["tasks"]})

    def test_owned_current_subagent_and_other_project_sessions_are_excluded(self):
        self.codex("owned")
        self.codex("current")
        self.codex("sidechain", source={"subagent": {"thread_spawn": {}}})
        self.codex("other", cwd=self.root / "other-project")
        self.write(self.state / "sessions.json", {"default/c1": {"cc": "owned"}})
        tasks = self.collect(exclude_session="current")["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["card_id"], "c1")

    def test_other_projects_do_not_crowd_out_project_history(self):
        self.codex("project-history")
        for i in range(history.MAX_DESKTOP_FILES + 1):
            self.codex("other-%d" % i, cwd=self.root / "other-project")
        self.assertIn("project-history", {task["session_id"] for task in self.collect()["tasks"]})

    def test_dialog_deduplicates_card_turns_but_retains_other_conversation(self):
        self.write(self.state / "transcript.json", {"default": {"turns": [
            {"id": "one", "text": "change the app", "reply": "APK built, not installed", "at": 10},
            {"id": "two", "text": "hello", "reply": "hi", "at": 30}]}})
        data = self.collect()
        # A different reply timestamp is retained rather than falsely deduplicated.
        self.assertEqual([entry["text"] for entry in data["dialog"]],
                         ["APK built, not installed", "hello", "hi"])

    def test_credentials_and_injected_history_are_removed(self):
        self.card["entries"].append({"role": "xiaoyou", "text": "token=private-token-value", "at": 40})
        self.write(self.state / "cards.json", {"cards": [self.card]})
        self.codex(text="<xiaoyou-history-context>replayed private history</xiaoyou-history-context>real reply")
        text = json.dumps(self.collect())
        self.assertNotIn("private-token-value", text)
        self.assertNotIn("replayed private history", text)
        self.assertIn("real reply", text)
        self.assertEqual(visible_text("<xiaoyou-history-context>all history</xiaoyou-history-context>"), "")

    def test_attachment_path_is_only_derived_from_valid_hash(self):
        self.card["attachments"] = [{"name": "APK", "sha256": "../private-file"}]
        self.write(self.state / "cards.json", {"cards": [self.card]})
        self.assertIsNone(self.collect()["tasks"][0]["attachments"][0]["path"])

    def test_digest_only_sends_changes_and_always_replays_on_fresh_context(self):
        data = self.collect()
        first, cursor = history.context(data)
        self.assertIn("APK built, not installed", first)
        self.assertEqual(history.context(data, cursor)[0], "")
        data["tasks"][0]["entries"][-1]["text"] = "phone tested"
        self.assertIn("phone tested", history.context(data, cursor)[0])
        self.assertIn("phone tested", history.context(data)[0])

    def test_digest_is_bounded_and_does_not_acknowledge_omitted_tasks(self):
        data = self.collect()
        base = data["tasks"][0]
        data["tasks"] = [dict(base, id="task-%d" % i,
                              entries=[{"role": "assistant", "text": "长" * 4000}]) for i in range(20)]
        text, cursor = history.context(data)
        self.assertLessEqual(len(text), history.MAX_CONTEXT_CHARS)
        self.assertNotIn("task-19", cursor)

    def invoke_hook(self, payload, *, consumer="codex"):
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode()))
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", stdout), \
                mock.patch.object(sys, "stderr", stderr), mock.patch.object(Path, "home", return_value=self.home):
            result = history.main(["--config", str(self.config), "--project", str(self.repo),
                                   "--consumer", consumer, "--hook"])
        return result, stdout.getvalue(), stderr.getvalue()

    def test_same_hook_contract_works_for_codex_and_cc_and_refreshes(self):
        for consumer in ("codex", "claude"):
            payload = {"session_id": "same-session", "cwd": str(self.repo), "hook_event_name": "SessionStart"}
            result, output, error = self.invoke_hook(payload, consumer=consumer)
            self.assertEqual((result, error), (0, ""))
            hook = json.loads(output)["hookSpecificOutput"]
            self.assertEqual(hook["hookEventName"], "SessionStart")
            self.assertIn("APK built", hook["additionalContext"])
            payload["hook_event_name"] = "UserPromptSubmit"
            self.assertEqual(self.invoke_hook(payload, consumer=consumer)[1], "")
            payload["hook_event_name"] = "SessionStart"
            self.assertTrue(self.invoke_hook(payload, consumer=consumer)[1])
        cursors = list((self.state / "history-context").glob("*.json"))
        self.assertEqual(len(cursors), 2)
        if os.name != "nt":
            self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in cursors))
        self.assertIn("APK built", (self.state / "history-context" / "shared-history.md").read_text())

    def test_installer_is_idempotent_and_preserves_other_hooks_and_settings(self):
        import importlib.util
        path = Path(__file__).resolve().parents[2] / "tools" / "setup-history-sync.py"
        spec = importlib.util.spec_from_file_location("setup_history_sync", path)
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        unrelated = {"type": "command", "command": "existing-hook", "timeout": 2}
        other_reader = {"type": "command", "command":
                        "python3 /another/tool/shared_history.py --project %s --hook" % self.repo.resolve()}
        original = {"theme": "dark", "env": {"PRESERVE": "value"}, "hooks": {
            "SessionStart": [{"matcher": "startup", "hooks": [unrelated, other_reader]}],
            "PermissionRequest": [{"hooks": [unrelated]}]}}
        self.write(self.home / ".claude" / "settings.json", original)
        options = ["--home", str(self.home), "--config", str(self.config), "--project", str(self.repo)]
        with mock.patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(installer.main(options), 0)
            self.assertEqual(json.loads((self.home / ".claude" / "settings.json").read_text()), original)
            self.assertEqual(installer.main(options + ["--install"]), 0)
            first = (self.home / ".claude" / "settings.json").read_bytes()
            self.assertEqual(installer.main(options + ["--install"]), 0)
            self.assertEqual((self.home / ".claude" / "settings.json").read_bytes(), first)
        current = json.loads(first)
        self.assertEqual(current["env"], original["env"])
        self.assertEqual(current["hooks"]["SessionStart"][0], original["hooks"]["SessionStart"][0])
        self.assertEqual(current["hooks"]["PermissionRequest"], original["hooks"]["PermissionRequest"])
        self.assertEqual(len(list((self.home / ".claude").glob("*.bak"))), 1)

        # This representation was present in the real Codex approval hook and
        # rejected by hooks/list, disabling every hook in the source file.
        codex = self.home / ".codex" / "hooks.json"
        data = json.loads(codex.read_text())
        data["hooks"]["PermissionRequest"] = [{"hooks": [
            {"type": "command", "command": "approval-bridge", "timeout": 310.0}]}]
        self.write(codex, data)
        with mock.patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(installer.main(options + ["--install"]), 0)
        handler = json.loads(codex.read_text())["hooks"]["PermissionRequest"][0]["hooks"][0]
        self.assertIs(type(handler["timeout"]), int)
        self.assertEqual(handler["timeout"], 310)
        self.assertEqual(handler["command"], "approval-bridge")

    def test_relative_or_unknown_cwd_is_not_assumed_to_be_the_project(self):
        self.assertFalse(history._within("", self.repo.resolve()))
        self.assertFalse(history._within("relative", self.repo.resolve()))

    def test_hook_rejects_unrelated_project_relative_cwd_and_unsupported_event(self):
        for fields in ({"cwd": str(self.root / "other")}, {"cwd": "relative"}, {"cwd": ""},
                       {"hook_event_name": "PermissionRequest"}, {"parent_thread_id": "parent"}):
            payload = {"session_id": "s", "cwd": str(self.repo), "hook_event_name": "SessionStart"}
            payload.update(fields)
            self.assertEqual(self.invoke_hook(payload), (0, "", ""))
        self.assertFalse((self.state / "history-context").exists())

    def test_hook_read_failure_does_not_block_or_print_private_error(self):
        payload = {"session_id": "s", "cwd": str(self.repo), "hook_event_name": "SessionStart"}
        with mock.patch.object(history, "snapshot", side_effect=OSError("private secret")):
            result, stdout, stderr = self.invoke_hook(payload)
        self.assertEqual((result, stdout), (0, ""))
        self.assertNotIn("private secret", stderr)

    def test_missing_and_corrupt_sources_are_not_fatal(self):
        (self.state / "cards.json").write_text("{invalid")
        self.assertEqual(self.collect()["tasks"], [])

    def test_hook_configuration_quotes_paths_and_only_adds_context_events(self):
        config = history.hook_config(self.root / "space $x config.json", self.repo, "claude")
        self.assertEqual(set(config["hooks"]), set(history.EVENTS))
        command = config["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        import shlex
        args = shlex.split(command)
        self.assertIn(str((self.root / "space $x config.json").resolve()), args)
        self.assertIn("claude", args)


if __name__ == "__main__":
    unittest.main()
