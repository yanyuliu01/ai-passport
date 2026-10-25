"""Talking about a session the owner opened on the computer, from the phone.

What the owner says while looking at such a session goes to Xiaoyou first: she explains,
translates or summarizes it herself. The session is continued only when the owner names the
helper that can take it over, or when Xiaoyou decides the owner wants it to keep working.
A continued run resumes that session in its own directory, but the session stays the owner's:
the Runtime does not record it as its own, so desktop observation keeps showing it and the
owner can go back to the computer. A turn still running on the computer is never joined.
"""

import unittest

from test_runtime import Scripted, TempDirCase, make_xiaoyou, until  # noqa: F401
from xiaoyou_runtime.api.service import Service


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
                      desktop_status=status, stale=False,
                      entries=[{"role": "xiaoyou", "text": "Mesh sync is done; PR #5 is up.",
                                "at": 1.0}])
        fields.update(extra)
        cards.update(card["id"], **fields)
        return card["id"]

    def finish(self, message):
        done = self.service.get(message["id"], wait=5)
        self.assertIn(done["status"], ("done", "failed"))
        return done

    def test_an_unnamed_message_goes_to_xiaoyou_not_the_computer_session(self):
        desk = self.desktop()
        done = self.finish(self.service.submit("中文说一下", card=desk, pin=True))
        self.assertEqual("done", done["status"])
        self.assertEqual("re:中文说一下", done["reply"])
        # She saw what the session said, and was told when it may be continued.
        system = self.voice.jobs[0].system
        self.assertIn("Mesh sync is done; PR #5 is up.", system)
        self.assertIn("card 写 %s" % desk, system)
        # Her answer is filed on its own card, never written into the owner's session.
        self.assertNotEqual(desk, done["card"])
        self.assertEqual([], self.cc.jobs)
        self.assertEqual([], self.codex.jobs)

    def test_xiaoyou_continues_the_session_when_the_owner_wants_it_to_keep_working(self):
        desk = self.desktop()
        self.voice.script = [{"reply": "好，让电脑上那个会话接着补测试", "brief": "接着补测试",
                              "mood": "busy", "card": desk,
                              "action": {"type": "amend", "task": "补上同步的端到端测试"}}]
        done = self.finish(self.service.submit("照它说的把测试补上", card=desk, pin=True))
        self.assertEqual("done", done["status"])
        task = self.xiaoyou.cards.get(done["card"])
        self.assertNotEqual(desk, task["id"])
        self.assertEqual(("desk-1", desk), (task["borrowed_session"], task["desktop_card"]))
        until(lambda: self.cc.jobs)
        self.assertEqual(("补上同步的端到端测试", "desk-1"),
                         (self.cc.jobs[0].text, self.cc.jobs[0].session_id))

    def test_naming_the_helper_resumes_the_owners_session_without_taking_it_over(self):
        desk = self.desktop()
        done = self.finish(self.service.submit("再补一段测试", card=desk, pin=True, agent="cc"))
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

    def test_naming_another_helper_starts_a_new_task_instead(self):
        desk = self.desktop()
        done = self.finish(self.service.submit("翻译一下", card=desk, pin=True, agent="codex"))
        until(lambda: self.codex.jobs)
        self.assertIsNone(self.codex.jobs[0].session_id)
        self.assertNotEqual(desk, done["card"])
        self.assertNotIn("borrowed_session", self.xiaoyou.cards.get(done["card"]) or {})

    def test_a_follow_up_in_the_new_task_keeps_using_the_same_session(self):
        desk = self.desktop()
        first = self.finish(self.service.submit("先看一下", card=desk, pin=True, agent="cc"))
        self.xiaoyou.settle(first["card"], 5)
        self.finish(self.service.submit("再改一下", card=first["card"], pin=True, agent="cc"))
        until(lambda: len(self.cc.jobs) == 2)
        self.assertEqual(["desk-1", "desk-1"], [job.session_id for job in self.cc.jobs])
        self.assertEqual({str(self.project)}, {job.cwd for job in self.cc.jobs})

    def test_codex_sessions_go_to_a_codex_agent(self):
        desk = self.desktop(agent="codex")
        self.finish(self.service.submit("继续", card=desk, pin=True, agent="codex"))
        until(lambda: self.codex.jobs)
        self.assertEqual("desk-1", self.codex.jobs[0].session_id)
        self.assertEqual([], self.cc.jobs)

    def test_a_turn_still_running_on_the_computer_is_not_joined(self):
        desk = self.desktop(status="running")
        done = self.finish(self.service.submit("插一句", card=desk, pin=True, agent="cc"))
        self.assertEqual("failed", done["status"])
        self.assertIn("还在跑", done["error"])
        # Asking Xiaoyou about it is still fine.
        self.assertEqual("done", self.finish(self.service.submit("它在干嘛", card=desk, pin=True))["status"])
        self.assertEqual([], self.cc.jobs)
        # A stale "running" is not proof the computer is busy: it may be continued.
        self.xiaoyou.cards.update(desk, stale=True)
        self.assertEqual("done", self.finish(
            self.service.submit("接着", card=desk, pin=True, agent="cc"))["status"])
        until(lambda: self.cc.jobs)

    def test_looking_at_the_session_without_opening_it_is_just_context(self):
        desk = self.desktop()
        done = self.finish(self.service.submit("hi", card=desk))
        self.assertEqual("done", done["status"])
        self.assertIn("Mesh sync is done", self.voice.jobs[0].system)
        self.assertEqual([], self.cc.jobs)

    def test_missing_directory_fails_visibly(self):
        desk = self.desktop(desktop_cwd="")
        done = self.finish(self.service.submit("继续", card=desk, pin=True, agent="cc"))
        self.assertEqual("failed", done["status"])
        self.assertIn("目录", done["error"])


if __name__ == "__main__":
    unittest.main()
