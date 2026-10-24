"""All wake sources share conversation order, bounded concurrency and shutdown."""

import threading
import time
import unittest
from unittest import mock

from test_runtime import GateTalker, Scripted, TempDirCase, make_xiaoyou, until
from xiaoyou_runtime.agents import AgentError
from xiaoyou_runtime.loop import ConversationLoop
from xiaoyou_runtime.service import Service


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
        x, store = make_xiaoyou(self.folder, [voice, Scripted("codex", speaks=False)])
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
        self.assertIn("尚未确认", "\n".join(restarted.promise_lines("default")))

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


if __name__ == "__main__":
    unittest.main()
