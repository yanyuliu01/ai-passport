"""Continuing a session the owner opened on the computer, from the phone.

The run resumes that session in its own directory, but the session stays the owner's: the
Runtime does not record it as its own, so desktop observation keeps showing it and the owner
can go back to the computer. A turn still running on the computer is never joined.
"""

import unittest

from test_runtime import Scripted, TempDirCase, make_xiaoyou, until  # noqa: F401
from xiaoyou_runtime.service import RequestError, Service


class DesktopContinueTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.voice = Scripted("claude")
        self.cc = Scripted("cc", speaks=True, kind="claude_code")
        self.codex = Scripted("codex", speaks=False, kind="codex")
        self.xiaoyou, self.store = make_xiaoyou(self.folder, [self.voice, self.cc, self.codex])
        self.service = Service(self.xiaoyou, self.store)
        self.addCleanup(self.service.close)
        self.project = self.folder / "project"
        self.project.mkdir()

    def desktop(self, agent="claude", status="idle", **extra):
        cards = self.xiaoyou.cards
        card = cards.open("__desktop__", "整理同步方案", "unknown", agent)
        fields = dict(state="working" if status == "running" else "done", source="desktop",
                      session_id="desk-1", desktop_cwd=str(self.project), monitoring_only=True,
                      desktop_status=status, stale=False)
        fields.update(extra)
        cards.update(card["id"], **fields)
        return card["id"]

    def finish(self, message):
        done = self.service.get(message["id"], wait=5)
        self.assertIn(done["status"], ("done", "failed"))
        return done

    def test_resumes_the_owners_session_in_its_directory_without_taking_it_over(self):
        desk = self.desktop()
        done = self.finish(self.service.submit("再补一段测试", card=desk, pin=True))
        self.assertEqual("done", done["status"])
        task = self.xiaoyou.cards.get(done["card"])
        self.assertNotEqual(desk, task["id"])
        self.assertEqual(("desk-1", str(self.project), desk),
                         (task["borrowed_session"], task["borrowed_cwd"], task["desktop_card"]))
        until(lambda: self.cc.jobs)
        job = self.cc.jobs[0]
        self.assertEqual(("再补一段测试", "desk-1", str(self.project)), (job.text, job.session_id, job.cwd))
        # The owner's own session: no Xiaoyou persona or reply format injected into it; only
        # the note on where to put files so the phone can show them.
        self.assertIsNone(job.schema)
        self.assertTrue((job.system or "").startswith("\n\n## Task files for the phone"), job.system)
        self.xiaoyou.settle(task["id"], 5)
        # Not recorded as the Runtime's own, so desktop observation keeps it.
        self.assertNotIn("desk-1", self.store.session_ids())
        self.assertNotIn("s1", self.store.session_ids())
        self.assertEqual([], self.voice.jobs)

    def test_a_follow_up_in_the_new_task_keeps_using_the_same_session(self):
        desk = self.desktop()
        first = self.finish(self.service.submit("先看一下", card=desk, pin=True))
        self.xiaoyou.settle(first["card"], 5)
        self.finish(self.service.submit("再改一下", card=first["card"], pin=True, agent="cc"))
        until(lambda: len(self.cc.jobs) == 2)
        self.assertEqual(["desk-1", "desk-1"], [job.session_id for job in self.cc.jobs])
        self.assertEqual({str(self.project)}, {job.cwd for job in self.cc.jobs})

    def test_codex_sessions_go_to_a_codex_agent(self):
        desk = self.desktop(agent="codex")
        self.finish(self.service.submit("继续", card=desk, pin=True))
        until(lambda: self.codex.jobs)
        self.assertEqual("desk-1", self.codex.jobs[0].session_id)
        self.assertEqual([], self.cc.jobs)

    def test_a_turn_still_running_on_the_computer_is_not_joined(self):
        desk = self.desktop(status="running")
        with self.assertRaises(RequestError) as caught:
            self.service.submit("插一句", card=desk, pin=True)
        self.assertIn("还在跑", str(caught.exception))
        # A stale "running" is not proof the computer is busy: it may be continued.
        self.xiaoyou.cards.update(desk, stale=True)
        self.assertEqual("done", self.finish(self.service.submit("接着", card=desk, pin=True))["status"])

    def test_only_from_inside_the_session(self):
        desk = self.desktop()
        with self.assertRaises(RequestError):
            self.service.submit("hi", card=desk)

    def test_missing_directory_fails_visibly(self):
        desk = self.desktop(desktop_cwd="")
        done = self.finish(self.service.submit("继续", card=desk, pin=True))
        self.assertEqual("failed", done["status"])
        self.assertIn("目录", done["error"])


if __name__ == "__main__":
    unittest.main()
