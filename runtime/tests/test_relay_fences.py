"""Saved helper results survive obsolete summary calls without publishing them."""
import unittest
from unittest import mock

from test_runtime import Scripted, TempDirCase, make_xiaoyou
from xiaoyou_runtime.helpers.agents import AgentError, Outcome
from xiaoyou_runtime.loop.effects import EffectFenceError
from xiaoyou_runtime.helpers.tasks import Task


class RelayFenceTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.voice = Scripted("voice")
        self.worker = Scripted("worker", speaks=False)
        self.x, self.store = make_xiaoyou(self.folder, [self.voice, self.worker])
        self.addCleanup(self.x.close)
        self.x.mesh = mock.Mock(runtime_id="local")
        self.x.mesh.owner_of.return_value = {"owner": "local", "generation": 1}
        card = self.x._writer.task_open("default", "original task", "working", "worker")
        self.task = Task(card["id"], "default", self.worker, "do work", run_id="original-run")
        self.x._writer.task(card["id"], run_id=self.task.run_id,
                           dispatch=dict(self.task.intent(), state="running"))
        history = self.x._writer.task_open("default", "background")
        self.x._writer.exchange(history["id"], "question", "answer", turn_id="history")

    def finish(self):
        self.x._finished(self.task, Outcome("saved worker result", "worker-session"), None)

    def change_owner(self):
        self.x.mesh.owner_of.return_value = {"owner": "local", "generation": 3}

    def assert_saved_only(self):
        self.assertEqual("saved worker result", self.x.cards.message("original-run-result:reply")["text"])
        summary = self.x.cards.message("original-run-summary:reply")
        # Withdrawing an already projected partial leaves the same identity
        # failed/empty, rather than inventing a completed assistant reply.
        self.assertTrue(summary is None or summary["status"] == "failed"
                        and not summary["text"] and not summary.get("partial"))
        self.assertIsNone(self.store.session("default", "voice"))
        self.assertIn("history", [turn["id"] for turn in self.x.context.unseen("default", "voice")])

    def test_owner_change_during_summary_preserves_result_without_session_or_receipts(self):
        def run(job):
            self.change_owner()
            return Outcome("obsolete summary", "obsolete-session")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.finish()
        self.assert_saved_only()

    def test_cancel_during_summary_does_not_commit_the_obsolete_session(self):
        def run(job):
            self.x.cancel(self.task.card, announce=False)
            return Outcome("obsolete summary", "obsolete-session")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.finish()
        self.assert_saved_only()
        self.assertEqual("cancelled", self.x.cards.get(self.task.card)["state"])

    def test_owner_message_interrupts_summary_without_losing_the_worker_result(self):
        interrupted = []
        def run(job):
            interrupted.append(self.x.interrupt("default"))
            return Outcome("obsolete summary", "obsolete-session")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.finish()
        self.assertEqual([True], interrupted)
        self.assert_saved_only()

    def test_queued_summary_cannot_adopt_a_later_owner_generation(self):
        queued = []
        self.x.defer = lambda conversation, work: queued.append(work)
        self.finish()
        self.change_owner()
        with mock.patch.object(self.voice, "run", return_value=Outcome("must not run")) as run:
            queued[0]()
        run.assert_not_called()
        self.assert_saved_only()

    def test_stale_summary_cannot_mark_history_seen_or_modify_the_new_run(self):
        def run(job):
            self.x._writer.task(self.task.card, run_id="replacement", state="working")
            return Outcome("obsolete summary", "obsolete-session")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.finish()
        self.assert_saved_only()
        self.assertEqual("replacement", self.x.cards.get(self.task.card)["run_id"])
        self.assertEqual("working", self.x.cards.get(self.task.card)["state"])

    def test_shutdown_during_summary_does_not_publish_or_commit_a_session(self):
        def run(job):
            self.x._pulse.close()
            return Outcome("obsolete summary", "obsolete-session")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.finish()
        self.assert_saved_only()

    def test_retained_text_callback_is_closed_after_summary_completion(self):
        retained = []
        def run(job):
            retained.append(job.on_text)
            return Outcome("valid summary", "valid-session")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.finish()
        self.assertEqual("done", self.x.cards.get(self.task.card)["state"])
        before = self.x.cards.get(self.task.card)
        with self.assertRaises(EffectFenceError):
            retained[0]("obsolete partial")
        self.assertEqual(before, self.x.cards.get(self.task.card))

    def test_interrupted_summary_is_queryable_without_running_another_model(self):
        def run(job):
            self.x.interrupt("default")
            return Outcome("obsolete summary", "obsolete-session")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.finish()
        self.assertEqual("unknown", self.x.cards.get(self.task.card)["state"])
        self.x._task_recovery_ready = True
        with mock.patch.object(self.voice, "run") as voice, mock.patch.object(self.worker, "run") as worker:
            self.assertTrue(self.x.reconcile_task(self.task.card, self.task.run_id))
        voice.assert_not_called()
        worker.assert_not_called()
        self.assertEqual("saved worker result", self.x.cards.message("original-run-summary:reply")["text"])

    def test_unresolved_owner_or_conflict_cannot_start_a_summary_model(self):
        queued = []
        self.x.defer = lambda conversation, work: queued.append(work)
        self.finish()
        for fields in ({"owner": "other", "generation": 2},
                       {"owner": "local", "generation": 1, "unresolved": True},
                       {"owner": "local", "generation": 1, "conflict": True}):
            self.x.mesh.owner_of.return_value = fields
            with mock.patch.object(self.voice, "run") as run:
                queued[0]()
            run.assert_not_called()
        self.assert_saved_only()

    def test_delegated_result_still_returns_for_its_remote_conversation_owner(self):
        self.task.hop = 1
        self.x.mesh.owner_of.return_value = {"owner": "source-runtime", "generation": 4}
        with mock.patch.object(self.voice, "run", return_value=Outcome("valid delegated summary")):
            self.finish()
        self.assertEqual("valid delegated summary", self.x.cards.message("original-run-summary:reply")["text"])

    def test_partial_callback_checks_the_original_owner(self):
        def run(job):
            job.on_text('{"reply":"valid partial')
            self.change_owner()
            job.on_text('{"reply":"obsolete partial')
            return Outcome("obsolete summary")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.finish()
        self.assert_saved_only()
        self.assertEqual("", self.x.cards.get(self.task.card)["summary_partial"])

    def test_formatting_cannot_cross_an_owner_change_before_publication(self):
        from xiaoyou_runtime.loop import xiaoyou as module
        shape = module.shape
        def format_text(*args, **kwargs):
            self.change_owner()
            return shape(*args, **kwargs)
        with mock.patch.object(self.voice, "run", return_value=Outcome("obsolete summary")), \
                mock.patch.object(module, "shape", side_effect=format_text):
            self.finish()
        self.assertIsNone(self.x.cards.message("original-run-summary:reply"))
        self.assertIsNotNone(self.x.cards.message("original-run-result:reply"))

    def test_final_publication_compares_the_run_inside_the_card_write(self):
        update = self.x.cards.update
        def race(card_id, **fields):
            if fields.get("turn_id") == "original-run-summary":
                update(card_id, run_id="replacement", state="working")
            return update(card_id, **fields)
        with mock.patch.object(self.voice, "run", return_value=Outcome("obsolete summary")), \
                mock.patch.object(self.x.cards, "update", side_effect=race):
            self.finish()
        self.assertIsNone(self.x.cards.message("original-run-summary:reply"))
        self.assertEqual("replacement", self.x.cards.get(self.task.card)["run_id"])
        self.assertEqual("working", self.x.cards.get(self.task.card)["state"])

    def test_an_unavailable_owner_at_queue_time_cannot_be_adopted_later(self):
        queued = []
        self.x.defer = lambda conversation, work: queued.append(work)
        self.x.mesh.owner_of.return_value = {"owner": "other", "generation": 1}
        self.finish()
        self.x.mesh.owner_of.return_value = {"owner": "local", "generation": 1}
        with mock.patch.object(self.voice, "run") as run:
            queued[0]()
        run.assert_not_called()
        self.assert_saved_only()

    def test_a_provider_error_after_owner_change_cannot_publish_a_fallback(self):
        def run(job):
            self.change_owner()
            raise AgentError("provider unavailable")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.finish()
        self.assert_saved_only()

    def test_valid_summary_still_streams_and_completes_under_the_same_identity(self):
        def run(job):
            self.assertIsNotNone(job.interrupt)
            job.on_text('{"reply":"valid partial')
            self.assertIn("valid partial", self.x.cards.get(self.task.card)["summary_partial"])
            return Outcome("valid summary", "valid-session")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.finish()
        self.assertEqual("valid summary", self.x.cards.message("original-run-summary:reply")["text"])
        self.assertEqual("valid-session", self.store.session("default", "voice"))
        self.assertEqual("", self.x.cards.get(self.task.card)["summary_partial"])
        self.assertNotIn("history", [turn["id"] for turn in self.x.context.unseen("default", "voice")])

    def test_failed_interruption_checkpoint_keeps_result_for_restart_recovery(self):
        def run(job):
            self.x.interrupt("default")
            return Outcome("obsolete summary")
        with mock.patch.object(self.voice, "run", side_effect=run), \
                mock.patch.object(self.x._writer, "task", side_effect=OSError("disk unavailable")):
            self.finish()
        self.assert_saved_only()
        self.x.close()
        candidate, _ = make_xiaoyou(self.folder, [self.voice, self.worker])
        self.addCleanup(candidate.close)
        with mock.patch.object(self.voice, "run") as voice, mock.patch.object(self.worker, "run") as worker:
            candidate.recover_tasks(ready=True)
        voice.assert_not_called()
        worker.assert_not_called()
        self.assertEqual("done", candidate.cards.get(self.task.card)["state"])
        self.assertEqual("saved worker result", candidate.cards.message("original-run-summary:reply")["text"])

    def test_read_only_provider_failure_still_publishes_saved_result(self):
        with mock.patch.object(self.voice, "run", side_effect=AgentError("provider unavailable")):
            self.finish()
        self.assertEqual("done", self.x.cards.get(self.task.card)["state"])
        self.assertIsNotNone(self.x.cards.message("original-run-summary:reply"))
        self.assertIsNotNone(self.x.cards.message("original-run-result:reply"))


if __name__ == "__main__":
    unittest.main()
