"""Dispatch admission and storage failures must not lose or duplicate work."""

import threading
import unittest
from unittest import mock

from test_runtime import AgentError, Recorder, Scripted, TempDirCase, make_xiaoyou, until
from xiaoyou_runtime.tasks import Task, Tasks
from xiaoyou_runtime.store import Store
from xiaoyou_runtime import world


class DispatchSafetyTests(TempDirCase):
    def runtime(self):
        agent = Scripted("codex", speaks=False)
        workspace = Recorder()
        x, store = make_xiaoyou(self.folder, [agent], workspace=workspace)
        self.addCleanup(x.close)
        self.addCleanup(agent.gate.set)
        return x, agent, workspace

    def test_duplicate_dispatch_preserves_the_running_identity_and_result(self):
        x, agent, workspace = self.runtime()
        agent.gate.clear()
        turn = x.hear("@codex original slow", "default", "t1")
        self.assertTrue(until(lambda: agent.started))
        first = x.cards.get(turn.card)["run_id"]
        x._launch(turn.card, agent, "duplicate", "default", 0)
        self.assertEqual(x.cards.get(turn.card)["run_id"], first)
        self.assertEqual([r[0] for r in workspace.rows], ["task"])
        agent.gate.set()
        self.assertEqual(x.settle(turn.card, 5)["state"], "done")
        self.assertEqual(agent.texts, ["original slow"])

    def test_missing_task_cannot_dispatch_a_helper(self):
        x, agent, _ = self.runtime()
        with self.assertRaises(AgentError):
            x._launch("missing", agent, "do work", "default", 0)
        self.assertEqual(x._tasks.count(), 0)
        self.assertEqual(agent.started, [])

    def test_failed_admission_does_not_reserve_a_worker_or_dispatch(self):
        x, agent, workspace = self.runtime()
        card = x._writer.task_open("default", "work", "working", agent.name)
        with mock.patch.object(x.cards._journal, "record", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                x._launch(card["id"], agent, "do work", "default", 0)
        self.assertEqual(x._tasks.count(), 0)
        self.assertEqual(agent.started, [])
        self.assertEqual(workspace.rows, [])
        x._launch(card["id"], agent, "do work", "default", 0)
        self.assertEqual(x.settle(card["id"], 5)["state"], "done")
        self.assertEqual(agent.texts, ["do work"])

    def test_initial_binding_failure_prevents_external_execution(self):
        x, agent, _ = self.runtime()
        append = x.cards.audit.append
        def fail_binding(event):
            if event.get("phase") == "binding":
                raise OSError("disk full")
            return append(event)
        with mock.patch.object(x.cards.audit, "append", side_effect=fail_binding):
            turn = x.hear("@codex do work", "default", "t1")
            self.assertEqual(x.settle(turn.card, 5)["state"], "failed")
        self.assertEqual(agent.started, [])
        self.assertEqual(x._tasks.count(), 0)

    def test_required_binding_rejects_a_stale_run(self):
        x, agent, _ = self.runtime()
        card = x._writer.task_open("default", "work", "working", agent.name)
        x._writer.task(card["id"], run_id="current")
        with self.assertRaises(ValueError):
            x._writer.execution_binding(card["id"], "attempt", run_id="old", required=True)
        self.assertEqual(x.cards.get(card["id"])["execution_attempts"], [])
        self.assertTrue(x._writer.execution_binding(
            card["id"], "attempt", run_id="current", required=True))
        self.assertEqual(x.cards.get(card["id"])["execution_attempts"][0]["run_id"], "current")

    def test_run_identity_and_initial_binding_are_durable_before_agent_execution(self):
        x, agent, _ = self.runtime()
        run = agent.run
        saved = []
        def inspect(job):
            card = next(card for card in world.current(x._cards._path) if card["id"] == job.card)
            self.assertEqual(card["dispatch"]["state"], "running")
            saved.append((card["run_id"], card["execution_attempts"]))
            return run(job)
        with mock.patch.object(agent, "run", side_effect=inspect):
            turn = x.hear("@codex do work", "default", "t1")
            self.assertEqual(x.settle(turn.card, 5)["state"], "done")
        self.assertEqual(len(saved), 1)
        run_id, attempts = saved[0]
        self.assertTrue(run_id)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["run_id"], run_id)

    def test_task_journal_binding_failure_prevents_external_execution(self):
        x, agent, _ = self.runtime()
        record = x.cards._journal.record
        def fail_binding(card, *args, **kwargs):
            if card.get("execution_attempts"):
                raise OSError("binding journal unavailable")
            return record(card, *args, **kwargs)
        with mock.patch.object(x.cards._journal, "record", side_effect=fail_binding):
            turn = x.hear("@codex do work", "default", "t1")
            self.assertEqual(x.settle(turn.card, 5)["state"], "failed")
        self.assertEqual(agent.started, [])
        self.assertEqual(x.cards.get(turn.card)["execution_attempts"], [])

    def test_a_started_callback_failure_finishes_and_releases_the_slot(self):
        store = Store(self.folder / "state")
        agent = Scripted("codex", speaks=False)
        results = []
        def started(task):
            if task.card == "c1":
                raise OSError("cannot persist started state")
        tasks = Tasks(store, lambda task, outcome, error: results.append((task.card, error)),
                      max_parallel=1, started=started)
        self.addCleanup(tasks.close)
        tasks.start(Task("c1", "default", agent, "first"))
        tasks.start(Task("c2", "default", agent, "second"))
        self.assertTrue(until(lambda: len(results) == 2))
        self.assertIn("cannot persist started state", dict(results)["c1"])
        self.assertEqual(agent.texts, ["second"])
        self.assertEqual(tasks.count(), 0)

    def test_failed_thread_start_releases_admission_for_a_retry(self):
        store = Store(self.folder / "state")
        agent = Scripted("codex", speaks=False)
        finished = threading.Event()
        tasks = Tasks(store, lambda *args: finished.set())
        self.addCleanup(tasks.close)
        with mock.patch("xiaoyou_runtime.tasks.threading.Thread.start",
                        side_effect=RuntimeError("no thread available")):
            with self.assertRaises(RuntimeError):
                tasks.start(Task("c1", "default", agent, "first"))
        self.assertFalse(tasks.active("c1"))
        self.assertTrue(tasks.start(Task("c1", "default", agent, "retry")))
        self.assertTrue(finished.wait(5))
        self.assertEqual(agent.texts, ["retry"])


if __name__ == "__main__":
    unittest.main()
