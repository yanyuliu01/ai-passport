#!/usr/bin/env python3
"""Desktop observation fixtures; no model calls or private local logs."""

import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xiaoyou_runtime.agents import create
from xiaoyou_runtime.cards import Cards
from xiaoyou_runtime.config import load
from xiaoyou_runtime.desktop_hook import record
from xiaoyou_runtime.desktop_sync import DesktopSync, MAX_LINE_BYTES, STALE_SECONDS
from xiaoyou_runtime.service import RequestError, Service
from xiaoyou_runtime.store import Store
from xiaoyou_runtime.xiaoyou import Xiaoyou


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 2000000000.0
        self.state = self.root / "state"
        self.cards = Cards(self.state / "cards.json")
        self.owned = set()
        self.sync = DesktopSync(self.cards, self.state, home=self.root,
                                clock=lambda: self.now, owned=lambda: self.owned)

    def stamp(self, seconds=None):
        return datetime.fromtimestamp(self.now if seconds is None else seconds,
                                      timezone.utc).isoformat()

    def append(self, path, events):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            for event in events:
                stream.write(json.dumps(dict(timestamp=self.stamp(), **event)) + "\n")
        os.utime(path, (self.now, self.now))

    def codex(self, sid="s-1", cwd="/project/a", source="vscode"):
        path = self.root / ".codex/sessions/2033/rollout-" / (sid + ".jsonl")
        path = path.parent / ("rollout-" + sid + ".jsonl")
        self.append(path, [{"type": "session_meta", "payload": {
            "id": sid, "cwd": cwd, "source": source, "originator": "Codex Desktop"}}])
        return path

    def cevent(self, kind, **payload):
        return {"type": "event_msg", "payload": dict(type=kind, **payload)}

    def hook(self, event):
        path = record(self.state, event)
        os.utime(path, (self.now, self.now))
        return path

    def observed(self):
        return [card for card in self.cards.recent(None, 1000) if card.get("monitoring_only")]

    def test_codex_lifecycle_multiple_sessions_and_privacy(self):
        path = self.codex()
        other = self.codex("s-2", "/project/b")
        self.append(path, [self.cevent("task_started", turn_id="turn1"),
                           {"type": "response_item", "payload": {"type": "function_call",
                            "name": "exec", "arguments": "SECRET command"}},
                           {"type": "response_item", "payload": {"type": "message", "role": "user",
                            "content": [{"type": "input_text", "text": "SECRET prompt"}]}}])
        self.append(other, [self.cevent("task_started"), self.cevent("task_complete")])
        self.sync.poll()
        cards = {card["session_id"]: card for card in self.observed()}
        self.assertEqual(cards["s-1"]["state"], "working")
        self.assertEqual(cards["s-1"]["progress"], ["正在运行", "exec"])
        self.assertEqual(cards["s-2"]["desktop_status"], "idle")
        self.assertNotIn("SECRET", json.dumps(cards))
        before = self.cards.changed("phone", include_desktop=True)["seq"]
        self.sync.poll()
        self.assertEqual(before, self.cards.changed("phone", include_desktop=True)["seq"])
        self.now += 1
        self.append(path, [self.cevent("turn_aborted")])
        self.sync.poll()
        self.assertEqual(self.cards.get(cards["s-1"]["id"])["state"], "cancelled")

    def test_claude_desktop_terminal_retry_and_interrupt(self):
        path = self.root / ".claude/projects/project/s-cc.jsonl"
        base = {"sessionId": "s-cc", "cwd": "/project/a", "entrypoint": "claude-desktop"}
        self.append(path, [dict(base, type="user", message={"content": "SECRET prompt"}),
                           dict(base, type="assistant", message={"model": "claude-test",
                            "stop_reason": "tool_use", "content": [{"type": "tool_use",
                            "name": "Bash", "input": {"command": "SECRET command"}}]}),
                           dict(base, type="system", subtype="api_error", retryAttempt=1)])
        self.sync.poll()
        card = self.observed()[0]
        self.assertEqual(card["state"], "working")
        self.now += 1
        self.append(path, [dict(base, type="assistant", message={"stop_reason": "end_turn",
                           "content": [{"type": "text", "text": "SECRET reply"}]})])
        self.sync.poll()
        self.assertEqual(self.cards.get(card["id"])["desktop_status"], "idle")
        self.now += 1
        self.append(path, [dict(base, type="user", message={"content": [
            {"type": "text", "text": "[Request interrupted by user]"}]})])
        self.sync.poll()
        result = self.cards.get(card["id"])
        self.assertEqual(result["state"], "cancelled")
        self.assertNotIn("SECRET", json.dumps(result))

    def test_stale_is_unknown_and_restart_reuses_card(self):
        path = self.codex()
        self.append(path, [self.cevent("task_started")])
        self.sync.poll()
        cid = self.observed()[0]["id"]
        reloaded = Cards(self.state / "cards.json")
        self.assertEqual(reloaded.get(cid)["state"], "unknown")
        self.cards = reloaded
        self.sync = DesktopSync(reloaded, self.state, home=self.root, clock=lambda: self.now)
        self.sync.poll()
        self.assertEqual(self.observed()[0]["id"], cid)
        self.now += STALE_SECONDS + 1
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "unknown")
        self.assertIsNone(self.cards.get(cid)["finished_at"])
        self.append(path, [self.cevent("task_complete")])
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["desktop_status"], "idle")

    def test_unreadable_source_is_unknown_immediately(self):
        path = self.codex()
        self.append(path, [self.cevent("task_started")])
        self.sync.poll()
        cid = self.observed()[0]["id"]
        with mock.patch.object(self.sync, "_read", side_effect=PermissionError):
            self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "unknown")
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "working")

    def test_partial_record_rotation_and_malformed_payload(self):
        path = self.codex()
        self.sync.poll()
        cid = self.observed()[0]["id"]
        event = json.dumps(dict(timestamp=self.stamp(), **self.cevent("task_started")))
        with path.open("a") as stream:
            stream.write(event[:20])
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "unknown")
        with path.open("a") as stream:
            stream.write(event[20:] + "\n")
        os.utime(path, (self.now, self.now))
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "working")
        self.append(path, [self.cevent([]), self.cevent("task_complete")])
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "done")
        path.unlink()
        self.codex()
        self.append(path, [self.cevent("task_started")])
        self.sync.poll()
        self.assertEqual(self.observed()[0]["id"], cid)

    def test_hooks_waiting_order_and_stale_metadata(self):
        path = self.codex()
        self.append(path, [self.cevent("task_started")])
        self.sync.poll()
        cid = self.observed()[0]["id"]
        self.now += 1
        hook = {"agent": "codex", "session_id": "s-1", "cwd": "/project/a",
                "status": "waiting", "observed_at": self.now, "model": "new-model"}
        self.hook(hook)
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "waiting")
        self.assertIsNone(self.cards.get(cid)["approval"])
        self.hook(dict(hook, observed_at=self.now - 10, cwd="/WRONG", model="old"))
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["project"], "a")
        self.assertEqual(self.cards.get(cid)["model"], "new-model")
        self.now += 1
        self.append(path, [self.cevent("task_started", turn_id="next")])
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "working")

    def test_project_filter_sidechains_and_runtime_ownership(self):
        self.sync.projects = {"/project/a"}
        for sid, cwd, source in [("yes", "/project/a", "vscode"),
                                  ("other", "/project/b", "vscode"),
                                  ("child", "/project/a", {"subagent": "spawn"}),
                                  ("owned", "/project/a", "cli")]:
            path = self.codex(sid, cwd, source)
            self.append(path, [self.cevent("task_started")])
        self.owned.add("owned")
        self.append(self.root / ".claude/projects/a/subagents/child.jsonl", [
            {"sessionId": "cc-child", "cwd": "/project/a", "type": "user",
             "message": {"content": "sidechain"}}])
        self.sync.poll()
        self.assertEqual([card["session_id"] for card in self.observed()], ["yes"])

    def test_global_phone_feed_does_not_enter_model_context(self):
        path = self.codex()
        self.append(path, [self.cevent("task_started")])
        self.sync.poll()
        self.assertEqual(self.cards.recent("phone"), [])
        self.assertEqual(len(self.cards.changed("phone", include_desktop=True)["cards"]), 1)
        seq = self.cards.changed("phone", include_desktop=True)["seq"]
        seen = []
        thread = threading.Thread(target=lambda: seen.append(
            self.cards.changed("phone", seq, 2, include_desktop=True)))
        thread.start()
        self.now += 1
        self.append(path, [self.cevent("task_complete")])
        self.sync.poll()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(seen[0]["cards"][0]["desktop_status"], "idle")

    def test_oversized_record_does_not_stall_following_events(self):
        path = self.codex()
        with path.open("a") as stream:
            stream.write("x" * (MAX_LINE_BYTES + 100) + "\n")
        self.append(path, [self.cevent("task_started"), self.cevent("task_complete")])
        self.sync.poll()
        self.assertEqual(self.observed()[0]["desktop_status"], "idle")

    def test_hook_discovery_uses_newest_not_old_directory_order(self):
        for index in range(130):
            path = record(self.state, {"agent": "codex", "session_id": "old-%d" % index,
                         "cwd": "/old", "status": "idle", "observed_at": self.now - 100})
            os.utime(path, (self.now - 100, self.now - 100))
        path = record(self.state, {"agent": "claude", "session_id": "latest",
                     "cwd": "/new", "status": "running", "observed_at": self.now})
        os.utime(path, (self.now, self.now))
        self.sync.poll()
        self.assertIn("latest", [card["session_id"] for card in self.observed()])
        seq = self.cards.changed("phone", include_desktop=True)["seq"]
        self.sync.poll()
        self.assertEqual(self.cards.changed("phone", include_desktop=True)["seq"], seq)

    def test_service_refuses_desktop_control(self):
        config_path = self.root / "config.json"
        (self.root / "persona.txt").write_text("Test persona")
        config_path.write_text(json.dumps({"server": {"token": "test-token-long-enough"},
            "agents": {"echo": {"type": "echo"}}, "xiaoyou": {"default_agent": "echo"}}))
        config = load(config_path)
        store = Store(config.state_dir)
        xiaoyou = Xiaoyou(config, [create(spec) for spec in config.agents], store)
        service = Service(xiaoyou, store)
        self.addCleanup(service.close)
        sync = DesktopSync(xiaoyou.cards, config.state_dir, home=self.root, clock=lambda: self.now)
        path = self.codex()
        self.append(path, [self.cevent("task_started")])
        sync.poll()
        card = service.cards("phone")[0]
        with self.assertRaises(RequestError):
            service.submit("continue", "phone", card=card["id"], pin=True)
        with self.assertRaises(RequestError):
            service.cancel(card["id"])
        self.assertEqual(service.feed("phone")["cards"][0]["state"], "working")
        self.assertEqual(store.session_ids(), set())

    def test_displaced_active_session_cannot_keep_running_badge(self):
        path = self.codex("older-running")
        self.append(path, [self.cevent("task_started")])
        self.sync.poll()
        cid = self.observed()[0]["id"]
        self.now += 20
        for index in range(32):
            path = self.codex("newer-%d" % index)
            self.append(path, [self.cevent("task_started"), self.cevent("task_complete")])
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "unknown")
        seq = self.cards.changed("phone", include_desktop=True)["seq"]
        self.sync.poll()
        self.assertEqual(seq, self.cards.changed("phone", include_desktop=True)["seq"])


if __name__ == "__main__":
    unittest.main()
