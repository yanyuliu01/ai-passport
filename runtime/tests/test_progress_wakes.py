"""Progress admission, model claims and reply recovery use the task journal."""

import json
import threading
import time
import unittest
from unittest import mock

from test_runtime import GateTalker, Scripted, TempDirCase, make_xiaoyu
from xiaoyu_runtime.helpers.agents import AgentError, Outcome
from xiaoyu_runtime.loop import proactive
from xiaoyu_runtime.world import world
from xiaoyu_runtime.loop.proactive import Pulse
from xiaoyu_runtime.api.service import Service


class ProgressWakeTests(TempDirCase):
    def setUp(self):
        super().setUp()
        for name in ("WATCH_AFTER", "WATCH_EVERY"):
            patch = mock.patch.object(proactive, name, 0)
            patch.start()
            self.addCleanup(patch.stop)
        self.voice = GateTalker()
        self.x, self.store = make_xiaoyu(self.folder, [self.voice, Scripted("codex", speaks=False)])
        self.addCleanup(self.x.close)
        self.card = self.x._writer.task_open("side-progress", "original task", "working", "codex")["id"]
        self.x._writer.task(self.card, run_id="run-progress", started_at=time.time() - 120)
        self.progress("first step")

    def progress(self, line):
        self.x._pulse.progress(self.card, "run-progress", "codex", line)

    def watch(self):
        return next(card for card in world.current(self.x.cards._path) if card["id"] == self.card).get("progress_watch")

    def restart_pulse(self):
        self.x._pulse.close()
        self.x._pulse = Pulse(self.x)
        return self.x._pulse

    def notes(self):
        return [entry for entry in self.x.cards.get(self.card)["entries"] if entry.get("kind") == "note"]

    def test_pending_admission_is_durable_and_a_lost_queue_resumes_once(self):
        queued = []
        self.x.defer = lambda conversation, work: queued.append(work)
        self.x._pulse.tick()
        before = self.watch()
        self.assertEqual("pending", before["event"]["state"])
        self.assertEqual([], self.voice.gate_jobs)
        self.restart_pulse()
        self.x.defer = lambda conversation, work: work()
        self.voice.answers = ["original progress reply"]
        self.x._pulse.tick()
        for work in queued:
            work()  # A stopped Pulse cannot execute its old queued closure.
        self.assertEqual(before["event"]["id"], self.watch()["event"]["id"])
        self.assertEqual("done", self.watch()["event"]["state"])
        self.assertEqual(1, len(self.voice.gate_jobs))
        self.assertEqual("side-progress", self.voice.gate_jobs[0].conversation)
        self.assertEqual(["original progress reply"], [entry["text"] for entry in self.notes()])

    def test_saved_reply_is_delivered_without_a_second_model_call(self):
        self.voice.answers = ["saved progress"]
        with mock.patch.object(self.x._pulse, "_say", side_effect=OSError("publication unavailable")):
            with self.assertRaises(OSError):
                self.x._pulse.tick()
        self.assertEqual("ready", self.watch()["event"]["state"])
        self.restart_pulse().tick()
        self.x._pulse.tick()
        self.assertEqual(1, len(self.voice.gate_jobs))
        self.assertEqual(["saved progress"], [entry["text"] for entry in self.notes()])
        self.assertEqual("done", self.watch()["event"]["state"])

    def test_saved_note_reconciles_a_lost_completion_checkpoint(self):
        self.voice.answers = ["saved note"]
        write = self.x.cards.checkpoint_watch
        def failed(card, run_id, before, after):
            if after["event"]["state"] == "done":
                raise OSError("receipt unavailable")
            return write(card, run_id, before, after)
        with mock.patch.object(self.x.cards, "checkpoint_watch", side_effect=failed):
            with self.assertRaises(OSError):
                self.x._pulse.tick()
        self.restart_pulse().tick()
        self.assertEqual(1, len(self.voice.gate_jobs))
        self.assertEqual(1, len(self.notes()))
        self.assertEqual("done", self.watch()["event"]["state"])

    def test_canonical_note_repairs_a_lost_card_projection_without_regeneration(self):
        self.voice.answers = ["canonical saved note"]
        append = self.x.cards._journal.record
        def failed(card):
            if any(entry.get("id") == "run-progress-gate-1:reply" for entry in card["entries"]):
                raise OSError("card journal unavailable")
            return append(card)
        with mock.patch.object(self.x.cards._journal, "record", side_effect=failed):
            with self.assertRaises(OSError):
                self.x._pulse.tick()
        self.assertEqual([], self.notes())
        self.assertEqual("final", self.x.cards.message("run-progress-gate-1:reply")["status"])
        self.restart_pulse().tick()
        self.assertEqual(["canonical saved note"], [entry["text"] for entry in self.notes()])
        self.assertEqual(1, len(self.voice.gate_jobs))
        self.assertEqual("done", self.watch()["event"]["state"])

    def test_running_without_a_result_is_unknown_and_not_automatically_regenerated(self):
        pulse = self.x._pulse
        _, entry = pulse._due_watches(time.time())[0]
        card = self.x.cards.get(self.card)
        running = pulse._watch_state(entry["watch"], "running")
        running["attempts"] += 1
        self.assertTrue(pulse._checkpoint_watch(card, entry["watch"], running))
        self.restart_pulse().tick()
        self.assertEqual("unknown", self.watch()["event"]["state"])
        self.assertEqual([], self.voice.gate_jobs)
        self.progress("new evidence")
        self.voice.answers = ["a later independent progress update"]
        self.x._pulse.tick()
        self.assertEqual(2, self.watch()["wakes"])
        self.assertEqual(1, len(self.voice.gate_jobs))
        self.assertEqual("run-progress-gate-2:reply", self.notes()[0]["id"])

    def test_read_only_provider_failure_keeps_the_same_trigger_retryable(self):
        with mock.patch.object(self.voice, "run", side_effect=AgentError("provider unavailable")):
            self.x._pulse.tick()
        pending = self.watch()
        self.assertEqual("pending", pending["event"]["state"])
        self.voice.answers = ["retry saved input"]
        self.x._pulse.tick()
        self.assertEqual(pending["event"]["id"], self.watch()["event"]["id"])
        self.assertEqual(1, self.watch()["wakes"])
        self.assertEqual(["retry saved input"], [entry["text"] for entry in self.notes()])

    def test_failed_admission_never_calls_a_model_and_can_retry(self):
        with mock.patch.object(self.x.cards._journal, "record", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.x._pulse.tick()
        self.assertIsNone(self.watch())
        self.assertEqual([], self.voice.gate_jobs)
        self.voice.answers = ["after storage recovery"]
        self.x._pulse.tick()
        self.assertEqual(1, len(self.voice.gate_jobs))

    def test_failed_claim_keeps_saved_input_pending_without_model_execution(self):
        write = self.x.cards.checkpoint_watch
        def failed(card, run_id, before, after):
            if after["event"]["state"] == "running":
                raise OSError("claim unavailable")
            return write(card, run_id, before, after)
        with mock.patch.object(self.x.cards, "checkpoint_watch", side_effect=failed):
            with self.assertRaises(OSError):
                self.x._pulse.tick()
        self.assertEqual("pending", self.watch()["event"]["state"])
        self.assertEqual([], self.voice.gate_jobs)
        self.x._pulse.tick()
        self.assertEqual(1, len(self.voice.gate_jobs))

    def test_maximum_wake_count_and_silent_decisions_survive_pulse_restart(self):
        for index in range(5):
            self.progress("step %d" % index)
            self.voice.answers = ["（不说）"]
            self.restart_pulse().tick()
        self.assertEqual(proactive.WATCH_MAX, len(self.voice.gate_jobs))
        self.assertEqual(proactive.WATCH_MAX, self.watch()["wakes"])
        self.assertEqual([], self.notes())

    def test_late_old_run_progress_and_queued_wakes_cannot_affect_the_next_run(self):
        queued = []
        self.x.defer = lambda conversation, work: queued.append(work)
        self.x._pulse.tick()
        self.x._writer.task(self.card, run_id="next-run", fresh=True)
        self.progress("late old progress")
        for work in queued:
            work()
        self.assertIsNone(self.watch())
        self.assertEqual([], self.x.cards.get(self.card)["progress"])
        self.assertEqual([], self.voice.gate_jobs)

    def test_changed_owner_generation_cannot_claim_a_queued_progress_trigger(self):
        self.x.mesh = mock.Mock(runtime_id="local")
        self.x.mesh.owner_of.return_value = {"owner": "local", "generation": 1}
        queued = []
        self.x.defer = lambda conversation, work: queued.append(work)
        self.x._pulse.tick()
        self.x.mesh.owner_of.return_value = {"owner": "local", "generation": 2}
        for work in queued:
            work()
        self.assertEqual([], self.voice.gate_jobs)
        self.assertEqual(["local", 1], self.watch()["event"]["owner"])
        self.assertEqual("pending", self.watch()["event"]["state"])

    def test_finished_task_does_not_execute_a_queued_progress_trigger(self):
        queued = []
        self.x.defer = lambda conversation, work: queued.append(work)
        self.x._pulse.tick()
        self.x._writer.task(self.card, state="done")
        for work in queued:
            work()
        self.assertEqual([], self.voice.gate_jobs)
        self.assertEqual([], self.notes())

    def test_a_running_conversation_wake_is_not_misclassified_by_the_next_tick(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def model(job):
            entered.set()
            release.wait(3)
            return Outcome("one update", None, None)
        with mock.patch.object(self.voice, "run", side_effect=model) as call:
            thread = threading.Thread(target=self.x._pulse.tick)
            thread.start()
            self.assertTrue(entered.wait(3))
            self.x._pulse.tick()
            self.assertEqual("running", self.watch()["event"]["state"])
            release.set()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(1, call.call_count)
        self.assertEqual("done", self.watch()["event"]["state"])
        self.assertEqual(1, len(self.notes()))

    def test_private_watch_inputs_and_results_never_enter_client_projections(self):
        self.voice.answers = ["public note"]
        self.x._pulse.tick()
        service = Service(self.x, self.store)
        self.addCleanup(service.close)
        self.assertIsNotNone(self.watch())
        for value in (self.x.cards.get(self.card), service.feed("side-progress", messages=True),
                      service.sync("side-progress"), service.directory()):
            self.assertNotIn('"progress_watch"', json.dumps(value))

    def test_wall_clock_cooldown_survives_a_new_pulse_instance(self):
        with mock.patch.object(proactive, "WATCH_EVERY", 240):
            self.x._pulse.tick()
            woke = self.watch()["woke_at"]
            self.progress("later progress")
            pulse = self.restart_pulse()
            self.assertEqual([], pulse._due_watches(woke + 239))
            self.assertEqual(1, len(pulse._due_watches(woke + 241)))
            self.assertEqual(2, self.watch()["wakes"])

    def test_restart_replays_watch_state_without_reviving_an_interrupted_task(self):
        self.voice.answers = ["before shutdown"]
        self.x._pulse.tick()
        saved = self.watch()
        self.x.close()
        voice = GateTalker()
        restored, _ = make_xiaoyu(self.folder, [voice, Scripted("codex", speaks=False)])
        self.addCleanup(restored.close)
        self.assertEqual(saved, restored.cards._cards[self.card]["progress_watch"])
        restored._pulse.tick()
        self.assertEqual([], voice.gate_jobs)
        self.assertNotIn(restored.cards.get(self.card)["state"], ("working", "waiting"))

    def test_competing_callbacks_can_claim_the_same_trigger_only_once(self):
        pulse = self.x._pulse
        run_id, entry = pulse._due_watches(time.time())[0]
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def model(job):
            entered.set()
            release.wait(3)
            return Outcome("one winner", None, None)
        with mock.patch.object(self.voice, "run", side_effect=model) as call:
            thread = threading.Thread(target=lambda: pulse._wake_for_task(run_id, entry))
            thread.start()
            self.assertTrue(entered.wait(3))
            pulse._wake_for_task(run_id, entry)
            release.set()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(1, call.call_count)
        self.assertEqual(["one winner"], [entry["text"] for entry in self.notes()])

    def test_lost_model_result_checkpoint_is_not_automatically_reasked(self):
        write = self.x.cards.checkpoint_watch
        def failed(card, run_id, before, after):
            if after["event"]["state"] == "ready":
                raise OSError("result unavailable")
            return write(card, run_id, before, after)
        self.voice.answers = ["not durably saved"]
        with mock.patch.object(self.x.cards, "checkpoint_watch", side_effect=failed):
            with self.assertRaises(OSError):
                self.x._pulse.tick()
        self.assertEqual("running", self.watch()["event"]["state"])
        self.restart_pulse().tick()
        self.assertEqual("unknown", self.watch()["event"]["state"])
        self.assertEqual(1, len(self.voice.gate_jobs))
        self.assertEqual([], self.notes())

    def test_run_change_between_source_check_and_note_write_is_fenced(self):
        self.voice.answers = ["old run result"]
        original = self.x.record.reply
        def changed(*args, **kwargs):
            if kwargs.get("turn_id") == "run-progress-gate-1":
                self.x._writer.task(self.card, run_id="new-run", fresh=True)
            return original(*args, **kwargs)
        with mock.patch.object(self.x.record, "reply", new=changed):
            self.x._pulse.tick()
        self.assertEqual([], self.notes())
        self.assertEqual("new-run", self.x.cards.get(self.card)["run_id"])
        self.assertIsNone(self.x.cards.message("run-progress-gate-1:reply"))

    def test_missing_voice_does_not_consume_progress_admission(self):
        with mock.patch.object(self.x._pulse, "_voice", return_value=None):
            self.x._pulse.tick()
        self.assertIsNone(self.watch())
        self.x._pulse.tick()
        self.assertEqual(1, self.watch()["wakes"])

    def test_a_malformed_watch_does_not_reset_counts_or_block_other_reminders(self):
        self.x._pulse._due_watches(time.time())
        self.x.cards._cards[self.card]["progress_watch"]["woke_at"] = 10 ** 400
        self.x.promises.track("independent reminder", due=time.time() - 5)
        self.voice.answers = ["the reminder still works"]
        self.x._pulse.tick()
        self.assertEqual(10 ** 400, self.x.cards._cards[self.card]["progress_watch"]["woke_at"])
        self.assertEqual(["the reminder still works"], [turn["reply"] for turn in self.x.context.turns("default")])

    def test_provider_retries_share_the_persisted_per_run_attempt_limit(self):
        with mock.patch.object(self.voice, "run", side_effect=AgentError("provider unavailable")) as call:
            for _ in range(proactive.WATCH_MAX + 2):
                self.restart_pulse().tick()
            self.progress("new progress after exhausting the budget")
            self.restart_pulse().tick()
            self.assertEqual(proactive.WATCH_MAX, call.call_count)
        self.assertEqual(proactive.WATCH_MAX, self.watch()["attempts"])
        self.assertEqual(1, self.watch()["wakes"])
        self.assertEqual("unknown", self.watch()["event"]["state"])


if __name__ == "__main__":
    unittest.main()
