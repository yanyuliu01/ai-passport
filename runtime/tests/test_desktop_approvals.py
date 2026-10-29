#!/usr/bin/env python3
"""Permission hook bridge tests use private fixtures, never live agent grants."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNTIME))

from xiaoyu_runtime.world.approvals import Approvals
from xiaoyu_runtime.world.cards import Cards
from xiaoyu_runtime.events.desktop_approvals import (
    DesktopApprovals, _read, _write, create_request, hook_output, wait_for_decision
)
from xiaoyu_runtime.events.desktop_hook import configuration, normalize, record
from xiaoyu_runtime.events.desktop_sync import DesktopSync
from xiaoyu_runtime.api.service import Service


class DesktopApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.now = time.time()
        self.approvals = Approvals()
        self.bridge = DesktopApprovals(self.state, self.approvals, lambda request: "c-test",
                                       clock=lambda: self.now)

    def payload(self, **fields):
        return dict(session_id="desktop-session", cwd="/example/project", turn_id="turn-1",
                    hook_event_name="PermissionRequest", tool_name="Bash",
                    tool_input={"command": "python3 -m unittest"}, **fields)

    def request(self, agent="codex", actionable=True, timeout=300, **fields):
        return create_request(self.state, agent, self.payload(**fields), actionable=actionable,
                              timeout=timeout, clock=lambda: self.now)

    def test_provider_hook_roundtrip_uses_only_official_once_decision(self):
        for agent, decision in (("codex", "allow"), ("claude", "deny")):
            with self.subTest(agent=agent):
                cards = Cards(self.state / (agent + "-cards.json"))
                approvals = Approvals(lambda card, aid: cards.update(card, approval=aid))
                sync = DesktopSync(cards, self.state, home=Path(self.temp.name), approvals=approvals)
                proc = subprocess.Popen([sys.executable, "-m", "xiaoyu_runtime.events.desktop_hook",
                    "--state-dir", str(self.state), "--agent", agent, "--approval-bridge",
                    "--approval-timeout", "5"], cwd=str(RUNTIME), stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.addCleanup(lambda p=proc: p.kill() if p.poll() is None else None)
                proc.stdin.write(json.dumps(self.payload()))
                proc.stdin.close()
                deadline = time.monotonic() + 3
                while not approvals.pending() and time.monotonic() < deadline:
                    sync.poll()
                    time.sleep(0.02)
                pending = approvals.pending("phone", include_desktop=True)
                self.assertEqual(len(pending), 1)
                approval = pending[0]
                self.assertEqual((approval["agent"], approval["session_id"], approval["source"]),
                                 (agent, "desktop-session", "desktop"))
                self.assertTrue(approval["remote_actionable"])
                self.assertTrue(cards.get(approval["card"])["monitoring_only"])
                self.assertEqual(cards.get(approval["card"])["approval"], approval["id"])
                self.assertTrue(approvals.answer(approval["id"], decision))
                self.assertFalse(approvals.answer(approval["id"], decision))
                proc.wait(timeout=3)
                output = json.loads(proc.stdout.read())
                self.assertEqual(output, hook_output(decision))
                self.assertEqual(proc.stderr.read(), "")
                proc.stdout.close()
                proc.stderr.close()
                sync.close()

    def test_notification_is_visible_across_conversations_but_cannot_grant(self):
        self.request(actionable=False)
        self.bridge.poll()
        pending = self.approvals.pending("phone", include_desktop=True)
        self.assertEqual(len(pending), 1)
        self.assertFalse(pending[0]["remote_actionable"])
        self.assertFalse(self.approvals.answer(pending[0]["id"], "allow"))
        self.assertEqual(len(self.approvals.pending()), 1)

    def test_expiry_and_desktop_completion_reject_late_answers(self):
        for mode in ("timeout", "resolved"):
            path = self.request()
            self.bridge.poll()
            aid = self.approvals.pending()[0]["id"]
            if mode == "timeout":
                self.now += 301
            else:
                event = normalize("codex", dict(session_id="desktop-session", cwd="/example/project",
                                  hook_event_name="PostToolUse", turn_id="turn-1", tool_name="Bash",
                                  tool_input={"command": "python3 -m unittest"}), self.now + 1)
                record(self.state, event)
                self.now += 1
            self.assertFalse(self.approvals.answer(aid, "allow"))
            self.assertNotEqual(_read(path).get("decision"), "allow")
            self.assertEqual(self.approvals.pending(), [])
            self.bridge.poll()

    def test_dead_hook_tamper_and_runtime_restart_do_not_reuse_answers(self):
        path = self.request()
        self.bridge.poll()
        aid = self.approvals.pending()[0]["id"]
        request = _read(path)
        request["session_id"] = "another-session"
        _write(path, request)
        self.assertFalse(self.approvals.answer(aid, "allow"))
        self.assertNotEqual(_read(path).get("decision"), "allow")
        path.unlink()
        path = self.request()
        self.bridge.poll()
        old = self.approvals.pending()[0]["id"]
        another = Approvals()
        fresh = DesktopApprovals(self.state, another, lambda request: "c-test", clock=lambda: self.now)
        fresh.poll()
        new = another.pending()[0]["id"]
        self.assertNotEqual(old, new)
        request = _read(path)
        request["pid"] = -1
        _write(path, request)
        self.assertFalse(another.answer(new, "allow"))

    def test_timeout_defers_without_implicit_allow_or_deny(self):
        path = self.request(timeout=1)
        tick = [self.now]
        def sleep(seconds):
            tick[0] += seconds
        self.assertIsNone(wait_for_decision(self.state, path, clock=lambda: tick[0], sleep=sleep))
        self.assertIsNone(hook_output(None))
        self.assertNotIn("decision", _read(path))

    def test_credentials_are_redacted_and_subagent_identity_is_preserved(self):
        payload = self.payload(agent_id="child-1")
        payload["tool_input"] = {"command": "TOKEN=private-secret python test.py"}
        path = create_request(self.state, "claude", payload, actionable=True)
        request = _read(path)
        self.assertNotIn("private-secret", path.read_text())
        self.assertEqual(request["subagent_id"], "child-1")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_templates_enable_only_permission_request_decisions(self):
        for agent in ("codex", "claude"):
            config = configuration(agent, self.state, RUNTIME)
            hooks = config["hooks"]
            self.assertIn("--approval-bridge", hooks["PermissionRequest"][0]["hooks"][0]["command"])
            self.assertEqual(hooks["PermissionRequest"][0]["hooks"][0]["timeout"], 310)
            self.assertIs(type(hooks["PermissionRequest"][0]["hooks"][0]["timeout"]), int)
            self.assertNotIn("--approval-bridge", hooks["PostToolUse"][0]["hooks"][0]["command"])
            if agent == "codex":
                self.assertEqual(hooks["SessionEnd"][0]["hooks"][0]["timeout"], 1)
                self.assertEqual(hooks["Interrupt"][0]["hooks"][0]["timeout"], 1)

    def test_fractional_bridge_wait_has_an_integer_outer_timeout(self):
        for agent in ("codex", "claude"):
            config = configuration(agent, self.state, RUNTIME, timeout=300.5)
            handler = config["hooks"]["PermissionRequest"][0]["hooks"][0]
            self.assertIs(type(handler["timeout"]), int)
            self.assertEqual(handler["timeout"], 311)
            self.assertIn("--approval-timeout 300.5", handler["command"])

    def test_missing_operation_context_can_only_notify(self):
        for data in (None, {}, "", {"description": "run something"}, {"command": ""}):
            payload = self.payload()
            payload["tool_input"] = data
            path = create_request(self.state, "codex", payload, actionable=True,
                                  clock=lambda: self.now)
            self.assertFalse(_read(path)["remote_actionable"])
        payload = self.payload()
        payload.update(tool_name="apply_patch", tool_input="*** Begin Patch\n*** End Patch")
        path = create_request(self.state, "codex", payload, actionable=True,
                              clock=lambda: self.now)
        self.assertTrue(_read(path)["remote_actionable"])
        self.assertIn("*** Begin Patch", _read(path)["detail"])

    def test_live_permission_and_repeated_notifications_do_not_duplicate(self):
        path = self.request(agent="claude")
        payload = self.payload()
        payload.update(hook_event_name="Notification", notification_type="permission_prompt",
                       message="Needs permission")
        observed = create_request(self.state, "claude", payload, actionable=False,
                                  clock=lambda: self.now)
        self.assertEqual(observed, path)
        self.bridge.poll()
        self.assertEqual(len(self.approvals.pending(include_desktop=True)), 1)
        _write(path, {**_read(path), "state": "closed"})
        one = create_request(self.state, "claude", payload, actionable=False,
                             clock=lambda: self.now)
        two = create_request(self.state, "claude", payload, actionable=False,
                             clock=lambda: self.now + 1)
        self.assertEqual(one, two)

    def test_permission_supersedes_earlier_notification(self):
        payload = self.payload()
        payload.update(hook_event_name="Notification", message="Needs permission")
        notice = create_request(self.state, "claude", payload, actionable=False,
                                clock=lambda: self.now)
        real = self.request(agent="claude")
        self.assertNotEqual(notice, real)
        self.assertEqual(_read(notice)["state"], "superseded")

    def test_invalid_operation_never_waits_on_another_live_permission(self):
        valid = self.request(agent="claude")
        payload = self.payload()
        payload["tool_input"] = None
        invalid = create_request(self.state, "claude", payload, actionable=True,
                                 clock=lambda: self.now)
        self.assertNotEqual(valid, invalid)
        self.assertFalse(_read(invalid)["remote_actionable"])
        self.assertIsNone(wait_for_decision(self.state, invalid))
        self.assertEqual(_read(valid)["state"], "pending")

    def test_parallel_same_session_requests_do_not_answer_or_expire_each_other(self):
        first = self.request()
        payload = self.payload()
        payload["tool_input"] = {"command": "git status"}
        second = create_request(self.state, "codex", payload, actionable=True, clock=lambda: self.now)
        self.bridge.poll()
        pending = self.approvals.pending()
        self.assertEqual(len(pending), 2)
        by_request = {item["request_id"]: item["id"] for item in pending}
        aid1, aid2 = by_request[first.stem], by_request[second.stem]
        event = self.payload()
        event["hook_event_name"] = "PostToolUse"
        record(self.state, normalize("codex", event, self.now + 1))
        self.now += 1
        self.assertFalse(self.approvals.answer(aid1, "allow"))
        self.assertTrue(self.approvals.answer(aid2, "deny"))
        self.assertEqual(_read(second)["decision"], "deny")
        self.assertNotIn("decision", _read(first))

    def test_lease_loss_and_changed_ownership_reject_answers_immediately(self):
        for cause in ("lease", "ownership"):
            path = self.request()
            self.bridge.poll()
            aid = self.approvals.pending()[0]["id"]
            if cause == "lease":
                self.now += 11
            else:
                self.bridge.eligible = lambda request: False
            self.assertFalse(self.approvals.answer(aid, "allow"))
            self.assertNotIn("decision", _read(path))
            path.unlink()
            self.bridge.poll()

    def test_service_feed_and_approve_use_the_same_desktop_request(self):
        self.request()
        self.bridge.poll()
        cards = Cards(self.state / "cards.json")
        service = Service.__new__(Service)
        service._xiaoyu = SimpleNamespace(approvals=self.approvals, cards=cards,
            usage=SimpleNamespace(poke=lambda: None, snapshot=lambda: {}, rev=0))
        self.approvals.ask("c-runtime", "private-conversation", "claude", "Bash", "ls")
        feed = service.feed("phone", wait=0)
        self.assertEqual(len(feed["approvals"]), 1)
        aid = feed["approvals"][0]["id"]
        self.assertTrue(service.approve(aid, "allow"))
        self.assertEqual(service.feed("another-phone", wait=0)["approvals"], [])


if __name__ == "__main__":
    unittest.main()
