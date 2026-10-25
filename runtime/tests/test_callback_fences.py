"""Late helper callbacks from a replaced run cannot write onto the card's current run."""
import dataclasses
import unittest
from unittest import mock

from test_runtime import Scripted, TempDirCase, make_xiaoyou
from xiaoyou_runtime.agents import Control
from xiaoyou_runtime.tasks import Task


class CallbackFenceTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.worker = Scripted("worker", speaks=False)
        self.x, self.store = make_xiaoyou(self.folder, [Scripted("voice"), self.worker])
        self.addCleanup(self.x.close)
        card = self.x._writer.task_open("default", "original task", "working", "worker")
        self.card = card["id"]
        self.old = self.dispatch("old-run")
        self.old_control = Control()
        self.x._prepare(self.old, self.old_control)

    def dispatch(self, run_id):
        task = Task(self.card, "default", self.worker, "do work", run_id=run_id)
        self.x._writer.task(self.card, state="working", run_id=run_id,
                            dispatch=dict(task.intent(), state="running"))
        return task

    def relaunch(self):
        """The owner cancels, then the same card is started again under a new run."""
        self.x.cancel(self.card, announce=False)
        new = self.dispatch("new-run")
        control = Control()
        self.x._prepare(new, control)
        control.session("new-session")
        control.model("new-model")
        return new, control

    def event(self, control, phase, key):
        return self.x.cards._log.get("%s-activity-%s-%s" % (control.binding_attempt, phase, key))

    def test_late_session_and_model_do_not_replace_the_new_run(self):
        self.relaunch()
        self.old_control.session("old-session")
        self.old_control.model("old-model")
        card = self.x.cards.get(self.card)
        self.assertEqual("new-session", card["worker_session"])
        self.assertEqual("new-model", card["model"])
        self.assertEqual("new-run", card["run_id"])

    def test_late_session_binding_only_amends_its_own_attempt(self):
        _, control = self.relaunch()
        self.old_control.session("old-session")
        attempts = self.x.cards.get(self.card)["execution_attempts"]
        self.assertEqual(control.binding_attempt, attempts[-1]["attempt_id"])
        self.assertEqual("new-session", attempts[-1]["native_session_id"])
        old = next(row for row in attempts if row["attempt_id"] == self.old_control.binding_attempt)
        self.assertEqual("old-session", old["native_session_id"])

    def test_late_activity_is_not_recorded_on_the_new_run(self):
        self.relaunch()
        self.old_control.activity("message", "stale output", "late")
        self.assertIsNone(self.event(self.old_control, "message", "late"))

    def test_cancelled_run_still_records_its_own_end(self):
        self.x.cancel(self.card, announce=False)
        self.old_control.activity("cancelled", "本轮已停止", "end")
        self.assertIsNotNone(self.event(self.old_control, "cancelled", "end"))

    def test_late_release_keeps_the_new_runs_approval(self):
        new, control = self.relaunch()
        stale = self.x._approvals.ask(self.card, "default", "worker", "old tool", "detail")
        self.old_control.approvals.append(stale)
        current = self.x._approvals.ask(self.card, "default", "worker", "new tool", "detail")
        control.approvals.append(current)
        self.x._release(self.old, self.old_control)
        self.assertIsNone(self.x._approvals._items[current]["decision"])
        self.assertEqual("deny", self.x._approvals._items[stale]["decision"])
        self.assertEqual("new-run", self.x.cards.get(self.card)["run_id"])

    def test_current_release_still_drops_its_approvals(self):
        approval = self.x._approvals.ask(self.card, "default", "worker", "tool", "detail")
        self.x._release(self.old, self.old_control)
        self.assertEqual("deny", self.x._approvals._items[approval]["decision"])

    def test_late_begin_does_not_touch_the_new_run(self):
        new, _ = self.relaunch()
        self.x._writer.task(self.card, queued=True)
        limited = dataclasses.replace(self.x._config, max_parallel=1)
        with mock.patch.object(self.x, "_config", limited):
            self.x._begun(self.old)
            self.assertTrue(self.x.cards.get(self.card)["queued"])
            self.x._begun(new)
        self.assertFalse(self.x.cards.get(self.card)["queued"])


if __name__ == "__main__":
    unittest.main()
