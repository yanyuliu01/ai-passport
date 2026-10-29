"""External reports: sessions that are not on this computer (claude.ai on the web, cloud
sessions, other computers) report their progress; they show read-only next to the observed
desktop sessions. See docs/development/unified-message-model ("Source connection")."""

import contextlib
import io
import json
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest import mock

from test_runtime import TOKEN, Scripted, TempDirCase, make_xiaoyu, start_runtime, write_config
from xiaoyu_runtime.api import conformance
from xiaoyu_runtime.events import reports
from xiaoyu_runtime.__main__ import main
from xiaoyu_runtime.events.desktop_sync import DesktopSync
from xiaoyu_runtime.events.message_log import DESKTOP_CONVERSATION
from xiaoyu_runtime.api.service import RequestError, Service


class ReportsTest(TempDirCase):
    def setUp(self):
        super().setUp()
        self.xiaoyu, self.store = make_xiaoyu(self.folder, [Scripted("claude")])
        self.service = Service(self.xiaoyu, self.store)
        self.addCleanup(self.service.close)

    def everything(self):
        page, messages, tasks = self.service.sync("default", 0), {}, {}
        while True:
            messages.update({item["id"]: item for item in page["messages"]})
            tasks.update({item["id"]: item for item in page["tasks"]})
            if not page["more"]:
                return messages, tasks
            page = self.service.sync("default", page["seq"])

    def test_a_report_is_a_read_only_card_with_deduplicated_messages(self):
        first = self.service.report({"origin": "claude-web", "session_id": "chat-1", "title": "同步方案",
                                     "text": "在看代码", "upstream_id": "m1"})
        card = self.xiaoyu.cards.get(first["card"])
        self.assertEqual((DESKTOP_CONVERSATION, "claude", "external", True, "working"),
                         (card["conversation"], card["agent"], card["source"], card["monitoring_only"], card["state"]))
        self.assertEqual("同步方案", card["title"])
        # Same upstream id: the message is updated, not added again.
        self.service.report({"origin": "claude-web", "session_id": "chat-1", "state": "turn_ended",
                             "text": "看完了", "upstream_id": "m1"})
        self.service.report({"origin": "claude-web", "session_id": "chat-1", "text": "主人说的",
                             "upstream_id": "m0", "role": "owner"})
        messages, tasks = self.everything()
        mine = sorted((item for item in messages.values() if item["task"] == first["card"]),
                      key=lambda item: item["created_at"])
        self.assertEqual(["看完了", "主人说的"], [item["text"] for item in mine])
        self.assertEqual({"type": "external", "origin": "claude-web", "session_id": "chat-1"}, mine[0]["source"])
        self.assertEqual({"kind": "external", "name": "claude-web"}, mine[0]["author"])
        self.assertEqual("owner", mine[1]["author"]["kind"])
        self.assertEqual("working", tasks[first["card"]]["state"])  # the last report did not change the state
        # Another session gets its own card; a status-only report adds no message.
        other = self.service.report({"origin": "claude-web", "session_id": "chat-2", "state": "done"})
        self.assertNotEqual(first["card"], other["card"])
        self.assertEqual([], self.xiaoyu.cards.get(other["card"])["entries"])

    def test_turn_ended_and_stale_reports(self):
        clock = mock.Mock(return_value=1000.0)
        board = reports.Reports(self.xiaoyu.cards, clock=clock)
        card = board.report({"origin": "codex-cloud", "session_id": "s", "state": "turn_ended"})["card"]
        self.assertEqual("codex", self.xiaoyu.cards.get(card)["agent"])
        self.assertEqual("turn_ended", next(task for task in self.service.sync("default", 0)["tasks"]
                                            if task["id"] == card)["state"])
        board.report({"origin": "codex-cloud", "session_id": "s", "state": "working"})
        clock.return_value = 1000.0 + reports.STALE_SECONDS + 1
        board.sweep()
        self.assertTrue(self.xiaoyu.cards.get(card)["stale"])
        # Not updated for a while: /v1/sync says unavailable (never finished); /v1/feed is unchanged.
        def synced():
            return next(task for task in self.service.sync("default", 0, states=["unavailable"])["tasks"]
                        if task["id"] == card)
        self.assertEqual("unavailable", synced()["state"])
        # An app that did not declare the state keeps the last known state and the stale mark.
        older = next(task for task in self.service.sync("default", 0)["tasks"] if task["id"] == card)
        self.assertEqual(("working", True), (older["state"], older["stale"]))
        self.assertEqual("working", next(item for item in self.service.feed("default", 0)["cards"]
                                         if item["id"] == card)["state"])
        board.report({"origin": "codex-cloud", "session_id": "s", "state": "working"})
        self.assertFalse(self.xiaoyu.cards.get(card)["stale"])
        self.assertEqual("working", synced()["state"])
        # A restarted runtime finds the same card again.
        self.assertEqual(card, reports.Reports(self.xiaoyu.cards).report(
            {"origin": "codex-cloud", "session_id": "s"})["card"])

    def test_bad_reports_are_refused(self):
        for payload in ([], {"origin": "Claude", "session_id": "s"}, {"origin": "a", "session_id": "has space"},
                        {"origin": "a", "session_id": "s", "state": "busy"},
                        {"origin": "a", "session_id": "s", "text": "没有编号"},
                        {"origin": "a", "session_id": "s", "text": "  ", "upstream_id": "u"},
                        {"origin": "a", "session_id": "s", "role": "xiaoyou"}):
            with self.subTest(payload=payload), self.assertRaises(RequestError):
                self.service.report(payload)

    def test_a_reported_session_cannot_be_continued_from_the_phone(self):
        card = self.service.report({"origin": "claude-web", "session_id": "chat", "state": "turn_ended"})["card"]
        with self.assertRaises(RequestError) as refused:
            self.service.submit("接着说", card=card, pin=True)
        self.assertIn("只能查看", str(refused.exception))

    def test_desktop_observation_leaves_reported_sessions_alone(self):
        card = self.service.report({"origin": "claude-web", "session_id": "chat", "state": "working"})["card"]
        sync = DesktopSync(self.xiaoyu.cards, self.folder / "state", home=self.folder / "home")
        self.assertNotIn(("claude", "chat"), sync.sessions)
        sync.close()
        self.assertEqual("working", self.xiaoyu.cards.get(card)["state"])


class ReportsHttpTest(TempDirCase):
    def test_post_reports_and_the_command_line(self):
        base, config = start_runtime(self, self.folder)
        request = urllib.request.Request(base + "/v1/reports", method="POST", data=json.dumps(
            {"origin": "claude-web", "session_id": "chat", "text": "你好", "upstream_id": "1"}).encode(),
            headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual("working", json.loads(response.read())["state"])
        bad = urllib.request.Request(base + "/v1/reports", method="POST", data=b'{"origin":"X"}',
                                     headers={"Authorization": "Bearer " + TOKEN})
        with self.assertRaises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(bad, timeout=10)
        self.assertEqual(400, refused.exception.code)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(0, main(["--config", str(self.folder / "config.json"), "report", "--origin", "claude-web",
                                      "--session", "chat", "--state", "turn_ended", "--text", "好了",
                                      "--upstream-id", "2", "--url", base]))
        self.assertIn("报上了", printed.getvalue())
        self.assertIn("turn_ended", printed.getvalue())

    def test_conformance_checks_reports_when_asked(self):
        base, _ = start_runtime(self, self.folder)
        results = dict((name, ok) for name, ok, _ in conformance.run(base, TOKEN, reply_timeout=30, reports=True))
        for name in ("reports.accepted", "reports.visible_once", "reports.marked_external",
                     "reports.turn_ended", "reports.read_only", "reports.rejects_bad_state"):
            self.assertTrue(results.get(name), name)


if __name__ == "__main__":
    unittest.main()
