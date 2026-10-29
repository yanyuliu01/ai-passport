"""All wake sources share conversation order, bounded concurrency and shutdown."""

import dataclasses
import hashlib
import json
import threading
import time
import unittest
from unittest import mock

from test_runtime import GateTalker, Scripted, TempDirCase, make_xiaoyu, until
from xiaoyu_runtime.helpers.agents import AgentError, Outcome
from xiaoyu_runtime.loop.effects import EffectFenceError
from xiaoyu_runtime.loop.loop import ConversationLoop
from xiaoyu_runtime.loop.proactive import GATE_RULES, UPKEEP_RULES
from xiaoyu_runtime.api.service import Service


class LoopTests(unittest.TestCase):
    def loop(self, handle=lambda item: None, parallel=4):
        loop = ConversationLoop(threading.Condition(), handle, parallel)
        self.addCleanup(loop.stop)
        return loop

    def test_chat_helper_result_and_timer_are_serial_in_one_conversation(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        seen = []
        def handle(item):
            seen.append(item)
            entered.set()
            release.wait(3)
        loop = self.loop(handle)
        loop.enqueue("side", "message")
        self.assertTrue(entered.wait(3))
        loop.enqueue("side", lambda: seen.append("helper"))
        loop.enqueue("side", lambda: (seen.append("timer"), finished.set()))
        self.assertEqual(["message"], seen)
        release.set()
        self.assertTrue(finished.wait(3))
        self.assertEqual(["message", "helper", "timer"], seen)

    def test_a_blocked_conversation_does_not_block_another(self):
        entered, release, other = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        loop = self.loop()
        loop.enqueue("a", lambda: (entered.set(), release.wait(3)))
        self.assertTrue(entered.wait(3))
        loop.enqueue("b", other.set)
        self.assertTrue(other.wait(3))

    def test_global_capacity_and_shutdown_do_not_start_queued_model_work(self):
        entered, release, queued = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        loop = self.loop(parallel=1)
        loop.enqueue("a", lambda: (entered.set(), release.wait(3)))
        self.assertTrue(entered.wait(3))
        loop.enqueue("b", queued.set)
        workers = loop.stop()
        self.assertFalse(loop.enqueue("c", queued.set))
        release.set()
        for worker in workers:
            worker.join(3)
        self.assertFalse(queued.is_set())
        self.assertEqual({}, loop.busy)

    def test_a_failed_event_does_not_strand_later_work(self):
        done = threading.Event()
        loop = self.loop()
        def fail():
            raise RuntimeError("synthetic")
        loop.enqueue("a", fail)
        loop.enqueue("a", done.set)
        self.assertTrue(done.wait(3))


class WakeTests(TempDirCase):
    def runtime(self, service=False):
        voice = GateTalker()
        x, store = make_xiaoyu(self.folder, [voice, Scripted("codex", speaks=False)])
        self.addCleanup(x.close)
        if service:
            server = Service(x, store)
            self.addCleanup(server.close)
        return x, voice

    def test_a_due_side_promise_is_queued_on_its_original_conversation_once(self):
        x, voice = self.runtime()
        x.promises.track("side reminder", due=time.time() - 5, conversation="side-work")
        queued = []
        x.defer = lambda conversation, work: queued.append((conversation, work))
        x._pulse.tick()
        x._pulse.tick()
        side = [work for conversation, work in queued if conversation == "side-work"]
        self.assertEqual(1, len(side))
        self.assertEqual([], voice.gate_jobs)
        voice.answers = ["side result"]
        side[0]()
        self.assertEqual("side-work", voice.gate_jobs[0].conversation)
        self.assertEqual(["side result"], [turn["reply"] for turn in x.context.turns("side-work")])
        self.assertEqual([], x.context.turns("default"))

    def test_a_resolved_queued_promise_does_not_wake_the_model(self):
        x, voice = self.runtime()
        item = x.promises.track("reminder", due=time.time() - 5)
        queued = []
        x.defer = lambda conversation, work: queued.append(work)
        x._pulse.tick()
        x.promises.resolve(item["id"])
        for work in queued:
            work()
        self.assertEqual([], voice.gate_jobs)

    def test_failed_model_delivery_does_not_consume_the_due_trigger(self):
        x, voice = self.runtime()
        item = x.promises.track("reminder", due=time.time() - 5)
        with mock.patch.object(voice, "run", side_effect=AgentError("offline")):
            x._pulse.tick()
        self.assertEqual([item["id"]], [value["id"] for value in x.promises.due()])
        voice.answers = ["try again"]
        x._pulse.tick()
        self.assertEqual([], x.promises.due())
        self.assertEqual(["try again"], [turn["reply"] for turn in x.context.turns("default")])

    def test_saved_reminder_is_not_generated_again_if_nudge_checkpoint_was_lost(self):
        x, voice = self.runtime()
        x.promises.track("reminder", due=time.time() - 5)
        voice.answers = ["saved reminder"]
        with mock.patch.object(x.promises, "mark_nudged", side_effect=OSError("checkpoint lost")):
            with self.assertRaises(OSError):
                x._pulse.tick()
        self.assertEqual(1, len(voice.gate_jobs))
        x._pulse.tick()
        self.assertEqual(1, len(voice.gate_jobs))
        self.assertEqual(["saved reminder"], [turn["reply"] for turn in x.context.turns("default")])
        self.assertEqual([], x.promises.due())

    def test_owner_generation_change_invalidates_a_queued_wake(self):
        x, voice = self.runtime()
        x.mesh = mock.Mock(runtime_id="local")
        x.mesh.owner_of.return_value = {"owner": "local", "generation": 1}
        x.promises.track("reminder", due=time.time() - 5)
        queued = []
        x.defer = lambda conversation, work: queued.append(work)
        x._pulse.tick()
        x.mesh.owner_of.return_value = {"owner": "local", "generation": 3}
        for work in queued:
            work()
        self.assertEqual([], voice.gate_jobs)
        self.assertEqual(1, len(x.promises.due()))

    def test_timer_model_waits_behind_the_active_conversation_lane(self):
        x, voice = self.runtime(service=True)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        x.defer("default", lambda: (entered.set(), release.wait(3)))
        self.assertTrue(entered.wait(3))
        x.promises.track("reminder", due=time.time() - 5)
        voice.answers = ["queued reminder"]
        x._pulse.tick()
        self.assertEqual([], voice.gate_jobs)
        release.set()
        self.assertTrue(until(lambda: len(x.context.turns("default")) == 1))
        self.assertEqual("queued reminder", x.context.turns("default")[0]["reply"])

    def test_a_timer_tool_with_a_lost_model_result_is_not_replayed_even_after_restart(self):
        x, voice = self.runtime()
        item = x.promises.track("original reminder", due=time.time() - 5)
        def run(job):
            self.assertFalse(job.tools.call("track", {"title": "follow-up"})[1])
            raise AgentError("response lost after tool")
        with mock.patch.object(voice, "run", side_effect=run):
            x._pulse.tick()
        saved = next(value for value in x.promises.open() if value["id"] == item["id"])
        self.assertEqual("unknown", saved["wake"]["state"])
        self.assertEqual(2, len(x.promises.open()))
        with mock.patch.object(voice, "run", side_effect=AssertionError("no replay")):
            x._pulse.tick()
        x.close()
        restarted, other = self.runtime()
        restarted._pulse.tick()
        self.assertEqual([], other.gate_jobs)
        self.assertEqual(2, len(restarted.promises.open()))
        self.assertNotIn("尚未确认", "\n".join(restarted.promise_lines("default")))
        self.assertIn("follow-up", restarted.context.turns("default")[-1]["reply"])

    def test_a_failed_timer_claim_cannot_execute_a_tool(self):
        x, voice = self.runtime()
        x.promises.track("original reminder", due=time.time() - 5)
        def run(job):
            job.tools.call("track", {"title": "must not exist"})
            raise AssertionError("claim must stop this path")
        with mock.patch.object(x.promises, "_write", side_effect=OSError("disk full")), \
                mock.patch.object(voice, "run", side_effect=run):
            x._pulse.tick()
        self.assertEqual(["original reminder"], [item["title"] for item in x.promises.open()])
        self.assertNotIn("wake", x.promises.open()[0])

    def test_an_interrupted_timer_does_not_publish_its_late_answer(self):
        x, voice = self.runtime()
        x.promises.track("reminder", due=time.time() - 5)
        original = voice.run
        def run(job):
            x.interrupt("default")
            return original(job)
        voice.answers = ["late answer"]
        with mock.patch.object(voice, "run", side_effect=run):
            x._pulse.tick()
        self.assertEqual([], x.context.turns("default"))
        self.assertEqual(1, len(x.promises.due()))


class WakeFenceTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.voice = GateTalker()
        self.x, self.store = make_xiaoyu(self.folder, [self.voice])
        self.addCleanup(self.x.close)
        self.x.mesh = mock.Mock(runtime_id="local")
        self.x.mesh.owner_of.return_value = {"owner": "local", "generation": 1}

    def change_owner(self):
        # Returning to this machine under a new generation still invalidates
        # work that began under the old grant.
        self.x.mesh.owner_of.return_value = {"owner": "local", "generation": 3}

    def upkeep(self):
        card = self.x._writer.task_open("default", "retained turns")
        for index in range(3):
            self.x._writer.exchange(card["id"], "question %d" % index, "answer", turn_id="u%d" % index)
        self.x._pulse._save_marker("u0")
        return self.x._pulse._marker.read_bytes()

    def test_an_upkeep_tool_after_owner_change_cannot_mutate_or_advance_the_marker(self):
        marker = self.upkeep()
        def run(job):
            self.change_owner()
            job.tools.call("remember", {"note": "obsolete preference", "kind": "偏好"})
            return Outcome("（好了）", "obsolete")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.x._pulse._maybe_upkeep(time.time() + 3600)
        self.assertEqual([], self.x.memory.notes())
        self.assertEqual(marker, self.x._pulse._marker.read_bytes())

    def test_an_interrupted_reminder_cannot_claim_or_call_a_tool(self):
        item = self.x.promises.track("original reminder", due=time.time() - 5)
        def run(job):
            self.x.interrupt("default")
            job.tools.call("track", {"title": "obsolete follow-up"})
            return Outcome("late", "obsolete")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.x._pulse._wake_for_promise(item)
        self.assertEqual([item["id"]], [value["id"] for value in self.x.promises.open()])
        self.assertNotIn("wake", self.x.promises.open()[0])

    def test_a_late_timer_result_cannot_replace_sessions_or_context_receipts(self):
        card = self.x._writer.task_open("default", "retained turn")
        self.x._writer.exchange(card["id"], "original question", "original answer", turn_id="old")
        def run(job):
            self.change_owner()
            return Outcome("late", "obsolete")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.assertIsNone(self.x._pulse._ask("timer event", ["recall"], GATE_RULES))
        self.assertIsNone(self.store.session("default", self.voice.name))
        self.assertEqual(["old"], [turn["id"] for turn in self.x.context.unseen("default", self.voice.name)])

    def test_a_reminder_keeps_its_original_generation_through_note_creation(self):
        item = self.x.promises.track("original reminder", due=time.time() - 5)
        def ask(*args, **kwargs):
            self.change_owner()
            return "obsolete reply"
        with mock.patch.object(self.x._pulse, "_ask", side_effect=ask):
            self.x._pulse._wake_for_promise(item)
        self.assertEqual([], self.x.cards.recent("default"))
        self.assertEqual([item["id"]], [value["id"] for value in self.x.promises.due()])

    def test_stopping_upkeep_rejects_the_tool_and_keeps_its_original_marker(self):
        marker = self.upkeep()
        def run(job):
            self.x._pulse.close()
            job.tools.call("remember", {"note": "obsolete preference", "kind": "偏好"})
            return Outcome("late")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.x._pulse._maybe_upkeep(time.time() + 3600)
        self.assertEqual([], self.x.memory.notes())
        self.assertEqual(marker, self.x._pulse._marker.read_bytes())

    def test_an_obsolete_read_only_tool_does_not_query_the_new_owner_state(self):
        def run(job):
            self.change_owner()
            job.tools.call("recall", {"query": "old topic"})
            return Outcome("late")
        with mock.patch.object(self.x, "recall") as recall, mock.patch.object(self.voice, "run", side_effect=run):
            self.assertIsNone(self.x._pulse._ask("event", ["recall"], GATE_RULES))
        recall.assert_not_called()

    def test_a_generation_change_during_the_claim_cannot_invoke_the_mutation(self):
        def run(job):
            job.tools.call("remember", {"note": "obsolete preference", "kind": "偏好"})
            return Outcome("late")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.assertIsNone(self.x._pulse._ask("event", ["remember"], UPKEEP_RULES,
                                                 before_effect=self.change_owner))
        self.assertEqual([], self.x.memory.notes())

    def test_valid_first_effect_is_kept_but_a_later_obsolete_effect_is_refused(self):
        item = self.x.promises.track("original reminder", due=time.time() - 5)
        def run(job):
            self.assertFalse(job.tools.call("track", {"title": "valid follow-up"})[1])
            self.change_owner()
            job.tools.call("track", {"title": "obsolete follow-up"})
            return Outcome("late")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.x._pulse._wake_for_promise(item)
        self.assertEqual(["original reminder", "valid follow-up"], [value["title"] for value in self.x.promises.open()])
        original = next(value for value in self.x.promises.open() if value["id"] == item["id"])
        self.assertEqual("unknown", original["wake"]["state"])
        self.assertIsNone(original["nudged"])
        self.assertEqual([], self.x.cards.recent("default"))

    def test_a_note_rechecks_its_owner_after_text_formatting(self):
        card = self.x._writer.task_open("default", "progress")
        def format_text(text):
            self.change_owner()
            return text
        with mock.patch.object(self.x, "chat_text", side_effect=format_text), \
                mock.patch.object(self.x.context, "delivered") as delivered:
            self.assertFalse(self.x._pulse._say(card["id"], "late", "note", "event", owner=("local", 1)))
        self.assertIsNone(self.x.cards.message("note:reply"))
        delivered.assert_not_called()

    def test_a_saved_note_does_not_consume_a_due_trigger_after_generation_change(self):
        item = self.x.promises.track("original reminder", due=time.time() - 5)
        version = hashlib.sha256(json.dumps([item.get("due"), item.get("nudged")]).encode()).hexdigest()[:16]
        turn_id = "%s-due-%s" % (item["id"], version)
        card = self.x._writer.task_open("default", "saved reminder")
        self.x._writer.reply(card["id"], "saved", turn_id=turn_id, kind="note")
        message = self.x.cards.message
        def read(mid):
            result = message(mid)
            if mid == turn_id + ":reply":
                self.change_owner()
            return result
        with mock.patch.object(self.x.cards, "message", side_effect=read):
            self.x._pulse._wake_for_promise(item)
        self.assertIsNone(self.x.promises.open()[0]["nudged"])

    def test_an_upkeep_result_cannot_advance_the_marker_after_owner_change(self):
        marker = self.upkeep()
        def ask(*args, **kwargs):
            self.change_owner()
            return "（好了）"
        with mock.patch.object(self.x._pulse, "_ask", side_effect=ask):
            self.x._pulse._maybe_upkeep(time.time() + 3600)
        self.assertEqual(marker, self.x._pulse._marker.read_bytes())

    def test_unresolved_conflicting_or_foreign_owners_do_not_start_a_model(self):
        for state in ({"owner": "other", "generation": 2},
                      {"owner": "local", "generation": 1, "unresolved": True},
                      {"owner": "local", "generation": 1, "conflict": True}):
            with self.subTest(state=state):
                self.x.mesh.owner_of.return_value = state
                with mock.patch.object(self.voice, "run") as run:
                    self.assertIsNone(self.x._pulse._ask("event", ["recall"], GATE_RULES))
                    run.assert_not_called()

    def test_callbacks_retained_after_a_completed_wake_cannot_mutate_or_lookup(self):
        retained = []
        def run(job):
            retained.append(job)
            return Outcome("done")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.assertEqual("done", self.x._pulse._ask("event", ["remember", "recall"], GATE_RULES))
        with mock.patch.object(self.x, "recall") as recall:
            for name, args in (("remember", {"note": "late", "kind": "偏好"}), ("recall", {"query": "old"})):
                with self.assertRaises(EffectFenceError):
                    retained[0].tools.call(name, args)
        self.assertEqual([], self.x.memory.notes())
        recall.assert_not_called()

    def test_decision_tools_cannot_mutate_after_the_speaking_phase_starts(self):
        self.x._config = dataclasses.replace(self.x.config, decide_first=True)
        retained = []
        def run(job):
            if job.decision_only:
                retained.append(job.tools)
                return Outcome("", fields={"_decision": {"facts": "no changes", "messages": []}})
            with self.assertRaises(EffectFenceError):
                retained[0].call("remember", {"note": "late", "kind": "偏好"})
            self.assertEqual({"recall"}, {tool["name"] for tool in job.tools.definitions()})
            return Outcome("spoken reply")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.assertEqual("spoken reply", self.x._pulse._ask("event", ["remember", "recall"], GATE_RULES))
        self.assertEqual([], self.x.memory.notes())

    def test_decision_owner_change_cannot_enter_speaking_or_save_a_session(self):
        self.x._config = dataclasses.replace(self.x.config, decide_first=True)
        def run(job):
            self.assertTrue(job.decision_only)
            self.change_owner()
            return Outcome("", "obsolete", fields={"_decision": {"facts": "no changes"}})
        with mock.patch.object(self.voice, "run", side_effect=run) as call:
            self.assertIsNone(self.x._pulse._ask("event", ["recall"], GATE_RULES))
        self.assertEqual(1, call.call_count)
        self.assertIsNone(self.store.session("default", self.voice.name))


if __name__ == "__main__":
    unittest.main()
