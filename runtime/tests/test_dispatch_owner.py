"""A queued helper task starts only while this Runtime owns its conversation."""

import threading
import unittest
from unittest import mock

from test_runtime import Scripted, TempDirCase, make_xiaoyu, until
from xiaoyu_runtime.helpers.agents import Outcome


class Blocking(Scripted):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.release = threading.Event()

    def run(self, job):
        self.started.append(job.text)
        if job.text == "first":
            self.release.wait(5)
        return Outcome("done: " + job.text, None)


class DispatchOwnerTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.agent = Blocking("codex", speaks=False)
        self.x, _ = make_xiaoyu(self.folder, [self.agent], max_parallel=1)
        self.addCleanup(self.x.close)
        self.addCleanup(self.agent.release.set)
        self.x.mesh = mock.Mock(runtime_id="local")
        self.owner = {"owner": "local", "generation": 1}
        self.x.mesh.owner_of.side_effect = lambda conversation: dict(self.owner)
        self.x._task_recovery_ready = True

    def queue_two(self, hop=0):
        first = self.x._writer.task_open("side-test", "first", "working", "codex")["id"]
        second = self.x._writer.task_open("side-test", "second", "working", "codex")["id"]
        self.x._launch(first, self.agent, "first", "side-test", hop)
        self.x._launch(second, self.agent, "second", "side-test", hop)
        self.assertTrue(until(lambda: self.agent.started == ["first"]))
        return first, second

    def intent(self, card):
        return self.x.cards.dispatch(card)

    def test_queued_task_does_not_start_after_the_conversation_moves_away(self):
        first, second = self.queue_two()
        run_id = self.x.cards.get(second)["run_id"]
        self.owner = {"owner": "another", "generation": 2}
        self.agent.release.set()
        self.assertTrue(until(lambda: not self.x._tasks.active(second)))
        self.assertEqual(["first"], self.agent.started)
        self.assertEqual("pending", self.intent(second)["state"])
        card = self.x.cards.get(second)
        self.assertEqual(run_id, card["run_id"])
        self.assertEqual("working", card["state"])
        self.assertIn("别的电脑", card["brief"])
        self.assertFalse(card.get("execution_bindings"))

    def test_held_task_resumes_with_the_same_identity_when_ownership_returns(self):
        first, second = self.queue_two()
        run_id = self.x.cards.get(second)["run_id"]
        self.owner = {"owner": "another", "generation": 2}
        self.agent.release.set()
        self.assertTrue(until(lambda: not self.x._tasks.active(second)))
        self.assertEqual(0, self.x.recover_tasks())
        self.owner = {"owner": "local", "generation": 3}
        # The first run's summary was fenced too: its saved result is delivered
        # without rerunning, and the held input starts once.
        self.assertEqual(2, self.x.recover_tasks())
        self.assertEqual("done", self.x.settle(second, 5)["state"])
        self.assertEqual("done", self.x.settle(first, 5)["state"])
        self.assertEqual(["first", "second"], self.agent.started)
        self.assertEqual(run_id, self.x.cards.get(second)["run_id"])

    def test_unresolved_or_conflicting_ownership_holds_the_queued_task(self):
        for view in ({"owner": None, "unresolved": True}, {"owner": "local", "conflict": True}):
            with self.subTest(view=view):
                self.agent.started.clear()
                self.agent.release.clear()
                self.owner = {"owner": "local", "generation": 1}
                first, second = self.queue_two()
                self.owner = view
                self.agent.release.set()
                self.assertTrue(until(lambda: not self.x._tasks.active(second)))
                self.assertEqual(["first"], self.agent.started)
                self.assertEqual("pending", self.intent(second)["state"])
                self.assertTrue(until(lambda: not self.x._tasks.active(first)))

    def test_delegated_task_still_runs_for_its_source_runtime(self):
        first, second = self.queue_two(hop=1)
        self.owner = {"owner": "another", "generation": 2}
        self.agent.release.set()
        self.assertTrue(until(lambda: self.agent.started == ["first", "second"]))


if __name__ == "__main__":
    unittest.main()
