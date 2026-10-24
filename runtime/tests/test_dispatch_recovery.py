"""Recover saved helper inputs, never blindly replay an external execution."""

import io
import json
import threading
import unittest
from unittest import mock

from test_runtime import Scripted, TempDirCase, make_xiaoyou, until
from xiaoyou_runtime import world
from xiaoyou_runtime.service import Service


class DispatchRecoveryTests(TempDirCase):
    def runtime(self, agent=None, **settings):
        agent = agent or Scripted("codex", speaks=False)
        x, store = make_xiaoyou(self.folder, [agent], **settings)
        self.addCleanup(x.close)
        return x, store, agent

    def stage(self, x, agent, text="original", **kwargs):
        card = x._writer.task_open("side-test", "work", "working", agent.name)
        # Persist through real admission; only hold the actual worker body.
        with mock.patch.object(x._tasks, "_run", return_value=None):
            x._launch(card["id"], agent, text, "side-test", 0, **kwargs)
        return card["id"], x.cards.get(card["id"])["run_id"]

    def intent(self, x, identity):
        return next(value for card, value in x.cards.dispatches() if card["id"] == identity)

    def test_pending_input_recovers_with_the_same_identity_after_policy_initialization(self):
        old, _, agent = self.runtime()
        card, run_id = self.stage(old, agent)
        old.close()
        x, store, helper = self.runtime()
        self.assertEqual(x.cards.get(card)["state"], "working")
        self.assertEqual(x.recover_tasks(), 0)
        self.assertEqual(helper.started, [])
        service = Service(x, store)
        self.addCleanup(service.close)
        self.assertEqual(x.settle(card, 5)["state"], "done")
        self.assertEqual(helper.texts, ["original"])
        self.assertEqual(x.cards.get(card)["run_id"], run_id)
        self.assertEqual(service.recover_tasks(), 0)
        self.assertEqual(len(helper.jobs), 1)

    def test_started_but_unconfirmed_execution_stays_unknown_across_restarts(self):
        old, _, agent = self.runtime()
        card, run_id = self.stage(old, agent)
        old._writer.execution_binding(card, "attempt", run_id=run_id, required=True)
        old.close()
        for _ in range(2):
            x, _, helper = self.runtime()
            self.assertEqual(x.cards.get(card)["state"], "unknown")
            self.assertEqual(self.intent(x, card)["state"], "unknown")
            self.assertEqual(x.recover_tasks(ready=True), 0)
            self.assertEqual(helper.started, [])
            x.close()

    def test_saved_worker_result_returns_to_its_conversation_once_without_model_calls(self):
        old, _, agent = self.runtime()
        card, run_id = self.stage(old, agent)
        old._writer.execution_binding(card, "attempt", run_id=run_id, required=True)
        old._writer.reply(card, "saved result", turn_id=run_id + "-result",
                          scope="task", kind="worker_result", author=agent.name, result_ready=True)
        old.close()
        x, _, helper = self.runtime()
        self.assertEqual(x.recover_tasks(ready=True), 1)
        self.assertEqual(x.cards.get(card)["state"], "done")
        message = x.cards.message(run_id + "-summary:reply")
        self.assertEqual(message["conversation"], "side-test")
        self.assertIn("saved result", message["text"])
        self.assertEqual(helper.started, [])
        self.assertEqual(x.recover_tasks(), 0)
        x.close()
        again, _, helper = self.runtime()
        self.assertEqual(again.recover_tasks(ready=True), 0)
        self.assertEqual(sum(e.get("id") == run_id + "-summary:reply"
                             for e in again.cards.get(card)["entries"]), 1)
        self.assertEqual(helper.started, [])

    def test_reply_log_survives_failure_of_the_terminal_task_write(self):
        old, _, agent = self.runtime()
        card, run_id = self.stage(old, agent)
        old._writer.execution_binding(card, "attempt", run_id=run_id, required=True)
        old._writer.reply(card, "saved result", turn_id=run_id + "-result",
                          scope="task", kind="worker_result", author=agent.name, result_ready=True)
        with mock.patch.object(old.cards._journal, "record", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                old._writer.reply(card, "already spoken", state="done",
                                  turn_id=run_id + "-summary", kind="summary")
        old.close()
        x, _, helper = self.runtime()
        self.assertEqual(x.recover_tasks(ready=True), 1)
        self.assertEqual(x.cards.message(run_id + "-summary:reply")["text"], "already spoken")
        self.assertEqual(helper.started, [])

    def test_changed_helper_does_not_receive_an_accepted_input(self):
        old, _, agent = self.runtime()
        card, _ = self.stage(old, agent)
        old.close()
        changed = Scripted("codex", speaks=False, model="another-model")
        x, _, helper = self.runtime(changed)
        self.assertEqual(x.recover_tasks(ready=True), 0)
        self.assertEqual(helper.started, [])
        self.assertEqual(self.intent(x, card)["state"], "pending")
        self.assertIn("配置已改变", x.cards.get(card)["brief"])

    def test_missing_helper_waits_and_resumes_when_the_original_binding_returns(self):
        old, _, agent = self.runtime()
        card, run_id = self.stage(old, agent)
        old.close()
        x, _, other = self.runtime(Scripted("other", speaks=False))
        self.assertEqual(x.recover_tasks(ready=True), 0)
        self.assertEqual(other.started, [])
        helper = Scripted("codex", speaks=False)
        x.add_agent(helper)
        self.assertEqual(x.settle(card, 5)["state"], "done")
        self.assertEqual(helper.texts, ["original"])
        self.assertEqual(x.cards.get(card)["run_id"], run_id)

    def test_pending_supplements_redo_and_tiers_survive_without_running_stale_input(self):
        make = lambda: Scripted("codex", speaks=False, efforts=("low", "high"), models=("m1", "m2"))
        old, _, agent = self.runtime(make())
        card, run_id = self.stage(old, agent, effort="low", model="m1")
        self.assertEqual(old._amend(old.cards.get(card), agent, "after", "supplement", "high", "m2"), "after")
        self.assertEqual(old._amend(old.cards.get(card), agent, "redo", "latest requirement", None, None), "redo")
        old.close()
        x, _, helper = self.runtime(make())
        self.assertEqual(x.recover_tasks(ready=True), 1)
        self.assertEqual(x.settle(card, 5)["state"], "done")
        self.assertEqual(len(helper.jobs), 2)
        self.assertIn("latest requirement", helper.texts[0])
        self.assertIn("supplement", helper.texts[1])
        self.assertEqual([(job.effort, job.model) for job in helper.jobs], [("high", "m2")] * 2)
        self.assertEqual(x.cards.get(card)["run_id"], run_id)

    def test_pending_task_is_not_started_by_a_different_conversation_owner(self):
        old, _, agent = self.runtime()
        card, _ = self.stage(old, agent)
        old.close()
        x, _, helper = self.runtime()
        x.mesh = mock.Mock(runtime_id="local")
        x.mesh.owner_of.return_value = {"owner": "another"}
        self.assertEqual(x.recover_tasks(ready=True), 0)
        self.assertEqual(helper.started, [])
        self.assertEqual(self.intent(x, card)["state"], "pending")
        x.mesh = None

    def test_private_inputs_are_excluded_from_public_cards_sync_and_mesh(self):
        x, _, agent = self.runtime()
        card, _ = self.stage(x, agent, text="private recovery input")
        self.assertEqual(self.intent(x, card)["input"]["text"], "private recovery input")
        public = [x.cards.get(card), x.cards.recent(None, 100), x.cards.since("side-test", 0),
                  x.cards.sync_since(None, 0), x.cards.own_since(0)]
        self.assertNotIn("dispatch", json.dumps(public))
        self.assertNotIn("private recovery input", json.dumps(public))
        raw = next(i for i in world.current(x.cards._path) if i["id"] == card)
        self.assertEqual(raw["dispatch"]["input"]["text"], "private recovery input")

    def test_cancelled_pending_input_is_not_recovered(self):
        old, _, agent = self.runtime()
        card, _ = self.stage(old, agent)
        old.cancel(card, announce=False)
        old.close()
        x, _, helper = self.runtime()
        self.assertEqual(x.cards.get(card)["state"], "cancelled")
        self.assertEqual(x.recover_tasks(ready=True), 0)
        self.assertEqual(helper.started, [])

    def test_reconciliation_cannot_overwrite_a_newly_dispatched_run(self):
        old, _, agent = self.runtime()
        card, run_id = self.stage(old, agent)
        old._writer.execution_binding(card, "attempt", run_id=run_id, required=True)
        old._writer.reply(card, "old result", turn_id=run_id + "-result",
                          scope="task", kind="worker_result", author=agent.name)
        old.close()
        x, _, helper = self.runtime()
        read = x.cards.message
        def replace_during_read(identity):
            result = read(identity)
            if identity == run_id + "-result:reply":
                with mock.patch.object(x._tasks, "_run", return_value=None):
                    x._launch(card, helper, "new work", "side-test", 0)
            return result
        with mock.patch.object(x.cards, "message", side_effect=replace_during_read):
            self.assertEqual(x.recover_tasks(ready=True), 0)
        self.assertNotEqual(x.cards.get(card)["run_id"], run_id)
        self.assertEqual(x.cards.get(card)["state"], "working")
        self.assertIsNone(read(run_id + "-summary:reply"))

    def test_unrecognized_private_input_is_unknown_and_never_reexecuted(self):
        old, _, agent = self.runtime()
        card, _ = self.stage(old, agent)
        intent = self.intent(old, card)
        intent["version"] = 99
        old._writer.task(card, dispatch=intent)
        old.close()
        x, _, helper = self.runtime()
        self.assertEqual(x.cards.get(card)["state"], "unknown")
        self.assertIn("无法核对", x.cards.get(card)["brief"])
        self.assertEqual(x.recover_tasks(ready=True), 0)
        self.assertEqual(helper.started, [])

    def stage_image(self):
        from PIL import Image
        old, _, agent = self.runtime()
        out = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(out, "PNG")
        meta = old.artifacts._snapshot("owner-image", io.BytesIO(out.getvalue()), "image.png", "owner")
        card = old._writer.task_open("side-test", "image", "working", agent.name)["id"]
        old._writer.attach(card, [meta])
        path = old.artifacts._blobs / meta["sha256"]
        with mock.patch.object(old._tasks, "_run", return_value=None):
            old._launch(card, agent, "describe this image", "side-test", 0, images=(str(path),))
        old.close()
        return card, path

    def test_recovered_image_keeps_the_original_snapshot(self):
        card, path = self.stage_image()
        x, _, helper = self.runtime()
        self.assertEqual(x.recover_tasks(ready=True), 1)
        self.assertEqual(x.settle(card, 5)["state"], "done")
        self.assertEqual(helper.jobs[0].images, (str(path),))

    def test_missing_or_changed_image_cannot_be_silently_dropped(self):
        card, path = self.stage_image()
        path.write_bytes(b"not the accepted image")
        x, _, helper = self.runtime()
        self.assertEqual(x.recover_tasks(ready=True), 0)
        self.assertEqual(helper.started, [])
        self.assertEqual(self.intent(x, card)["state"], "pending")
        path.unlink()
        self.assertEqual(x.recover_tasks(), 0)
        self.assertEqual(helper.started, [])

    def test_a_real_parallel_queue_recovers_only_the_worker_not_yet_started(self):
        from xiaoyou_runtime.agents import Outcome
        class Blocking(Scripted):
            def run(self, job):
                self.started.append(job.text)
                while not job.control.cancelled:
                    threading.Event().wait(.005)
                return Outcome("stopped", None)
        old, _, agent = self.runtime(Blocking("codex", speaks=False), max_parallel=1)
        first = old._writer.task_open("side-test", "first", "working", agent.name)["id"]
        second = old._writer.task_open("side-test", "second", "working", agent.name)["id"]
        old._launch(first, agent, "first", "side-test", 0)
        old._launch(second, agent, "second", "side-test", 0)
        self.assertTrue(until(lambda: agent.started == ["first"]))
        queued_id = old.cards.get(second)["run_id"]
        old.close()
        # Restore with the same configured spec, without the blocking test body.
        x, _, helper = self.runtime()
        self.assertEqual(x.recover_tasks(ready=True), 1)
        self.assertEqual(x.settle(second, 5)["state"], "done")
        self.assertEqual(helper.texts, ["second"])
        self.assertEqual(x.cards.get(second)["run_id"], queued_id)
        self.assertEqual(x.cards.get(first)["state"], "unknown")

    def test_failed_recovery_write_leaves_input_pending_and_can_retry(self):
        old, _, agent = self.runtime()
        card, run_id = self.stage(old, agent)
        old.close()
        x, _, helper = self.runtime()
        with mock.patch.object(x.cards._journal, "record", side_effect=OSError("disk full")):
            self.assertEqual(x.recover_tasks(ready=True), 0)
        self.assertEqual(helper.started, [])
        self.assertEqual(self.intent(x, card)["state"], "pending")
        self.assertEqual(x.recover_tasks(), 1)
        self.assertEqual(x.settle(card, 5)["state"], "done")
        self.assertEqual(x.cards.get(card)["run_id"], run_id)

    def test_failed_result_reconciliation_remains_unknown_until_it_is_durable(self):
        old, _, agent = self.runtime()
        card, run_id = self.stage(old, agent)
        old._writer.execution_binding(card, "attempt", run_id=run_id, required=True)
        old._writer.reply(card, "saved result", turn_id=run_id + "-result",
                          scope="task", kind="worker_result", author=agent.name)
        old.close()
        x, _, helper = self.runtime()
        with mock.patch.object(x.cards._journal, "record", side_effect=OSError("disk full")):
            self.assertEqual(x.recover_tasks(ready=True), 0)
        self.assertEqual(x.cards.get(card)["state"], "unknown")
        self.assertEqual(self.intent(x, card)["state"], "unknown")
        self.assertEqual(x.recover_tasks(), 1)
        self.assertEqual(x.cards.get(card)["state"], "done")
        self.assertEqual(helper.started, [])

    def test_failed_checkpoint_does_not_reject_a_durable_pending_amendment(self):
        old, _, agent = self.runtime()
        card, run_id = self.stage(old, agent)
        with mock.patch("xiaoyou_runtime.world.SNAPSHOT_EVERY", 1), mock.patch.object(
                old.cards, "_save", side_effect=OSError("snapshot unavailable")):
            self.assertEqual(old._amend(old.cards.get(card), agent, "redo", "new requirement",
                                       None, None), "redo")
        old.close()
        x, _, helper = self.runtime()
        self.assertEqual(x.recover_tasks(ready=True), 1)
        self.assertEqual(x.settle(card, 5)["state"], "done")
        self.assertIn("new requirement", helper.texts[0])
        self.assertEqual(x.cards.get(card)["run_id"], run_id)

    def test_closing_runtime_cannot_start_pending_recovery_workers(self):
        x, _, helper = self.runtime()
        self.stage(x, helper)
        x.close()
        self.assertEqual(x.recover_tasks(ready=True), 0)
        self.assertEqual(helper.started, [])

    def test_legacy_context_failure_does_not_retract_a_durable_recovered_result(self):
        old, _, agent = self.runtime()
        card, run_id = self.stage(old, agent)
        old._writer.execution_binding(card, "attempt", run_id=run_id, required=True)
        old._writer.reply(card, "saved result", turn_id=run_id + "-result",
                          scope="task", kind="worker_result", author=agent.name)
        old.close()
        x, store, helper = self.runtime()
        with mock.patch.object(store._transcript, "add", side_effect=AssertionError("no transcript writes")):
            self.assertEqual(x.recover_tasks(ready=True), 1)
        self.assertEqual(x.cards.get(card)["state"], "done")
        self.assertIn("saved result", x.cards.message(run_id + "-summary:reply")["text"])
        self.assertEqual(helper.started, [])

    def delegated(self):
        x, store, agent = self.runtime()
        service = Service(x, store)
        self.addCleanup(service.close)
        with mock.patch.object(x._tasks, "_run", return_value=None):
            message = service.submit("original", agent="codex", hop=1)
            self.assertTrue(until(lambda: bool(service._held)))
        saved = service.get(message["id"])
        return x, service, agent, message["id"], saved["card"]

    def test_delegated_pending_work_keeps_its_original_waiter_after_restart(self):
        old, service, _, message_id, card = self.delegated()
        run_id = old.cards.get(card)["run_id"]
        service.close()
        x, store, helper = self.runtime()
        current = Service(x, store)
        self.addCleanup(current.close)
        self.assertEqual(current.get(message_id, wait=5)["status"], "done")
        self.assertEqual(helper.texts, ["original"])
        self.assertEqual(x.cards.get(card)["run_id"], run_id)
        self.assertEqual(len(x.cards.recent(None)), 1)

    def test_delegated_unknown_run_waits_for_reconciliation_instead_of_reporting_failure(self):
        old, service, _, message_id, card = self.delegated()
        run_id = old.cards.get(card)["run_id"]
        old._writer.execution_binding(card, "attempt", run_id=run_id, required=True)
        service.close()
        x, store, helper = self.runtime()
        current = Service(x, store)
        self.addCleanup(current.close)
        self.assertEqual(current.get(message_id)["status"], "running")
        self.assertEqual(current.get(message_id)["operation_state"], "unknown")
        x._writer.reply(card, "saved result", turn_id=run_id + "-result",
                        scope="task", kind="worker_result", author=helper.name)
        self.assertEqual(current.get(message_id)["status"], "running")
        self.assertEqual(current.recover_tasks(), 1)
        self.assertEqual(current.get(message_id, wait=5)["status"], "done")
        self.assertEqual(helper.started, [])

    def test_delegated_terminal_result_settles_a_receipt_lost_before_restart(self):
        old, service, _, message_id, card = self.delegated()
        run_id = old.cards.get(card)["run_id"]
        service._held.clear()  # Lose only the in-memory waiter, not its durable request.
        old._writer.reply(card, "saved final", state="done", turn_id=run_id + "-summary",
                          scope="main", kind="summary")
        self.assertEqual(service.get(message_id)["status"], "running")
        service.close()
        x, store, helper = self.runtime()
        current = Service(x, store)
        self.addCleanup(current.close)
        self.assertEqual(current.get(message_id)["status"], "done")
        self.assertEqual(current.get(message_id)["reply"], "saved final")
        self.assertEqual(helper.started, [])

    def test_thread_start_failure_keeps_durably_accepted_work_queued_for_restart(self):
        old, _, agent = self.runtime()
        card = old._writer.task_open("side-test", "work", "working", agent.name)["id"]
        with mock.patch("xiaoyou_runtime.tasks.threading.Thread.start",
                        side_effect=RuntimeError("no thread available")):
            old._launch(card, agent, "original", "side-test", 0)
        self.assertTrue(old.cards.get(card)["queued"])
        self.assertEqual(self.intent(old, card)["state"], "pending")
        run_id = old.cards.get(card)["run_id"]
        old.close()
        x, _, helper = self.runtime()
        self.assertEqual(x.recover_tasks(ready=True), 1)
        self.assertEqual(x.settle(card, 5)["state"], "done")
        self.assertEqual(helper.texts, ["original"])
        self.assertEqual(x.cards.get(card)["run_id"], run_id)

    def test_unknown_execution_blocks_a_conversation_handoff_until_reconciled(self):
        old, _, agent = self.runtime()
        card, run_id = self.stage(old, agent)
        old._writer.execution_binding(card, "attempt", run_id=run_id, required=True)
        old.close()
        x, store, _ = self.runtime()
        service = Service(x, store)
        self.addCleanup(service.close)
        with service._changed:
            self.assertIn("待核对", service._busy_reason("side-test"))

    def test_recovery_preserves_the_entire_saved_result_at_the_entry_limit(self):
        from xiaoyou_runtime.cards import MAX_ENTRY_CHARS
        old, _, agent = self.runtime()
        card, run_id = self.stage(old, agent)
        text = "x" * (MAX_ENTRY_CHARS - 4) + "tail"
        old._writer.execution_binding(card, "attempt", run_id=run_id, required=True)
        old._writer.reply(card, text, turn_id=run_id + "-result",
                          scope="task", kind="worker_result", author=agent.name)
        old.close()
        x, _, _ = self.runtime()
        self.assertEqual(x.recover_tasks(ready=True), 1)
        self.assertEqual(x.cards.message(run_id + "-summary:reply")["text"], text)


if __name__ == "__main__":
    unittest.main()
