"""Timer effects use the shared runner without another workflow store."""

import json
from copy import deepcopy
import time
import unittest
from unittest import mock

from test_runtime import GateTalker, TempDirCase, make_xiaoyou
from xiaoyou_runtime.agents import AgentError
from xiaoyou_runtime.effects import EffectUncertain, SavedWake
from xiaoyou_runtime.proactive import Pulse
from xiaoyou_runtime.promises import wake_source
from xiaoyou_runtime.service import Service


class TimerEffectTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.voice = GateTalker()
        self.x, self.store = make_xiaoyou(self.folder, [self.voice])
        self.addCleanup(self.x.close)

    def promise(self):
        return self.x.promises.track("original reminder", due=time.time() - 5, conversation="side")

    def upkeep(self):
        card = self.x._writer.task_open("default", "retained turns")
        for index in range(3):
            self.x._writer.exchange(card["id"], "question %d" % index, "answer", turn_id="u%d" % index)
        self.x._pulse._save_marker("u0")

    def restart(self):
        self.x.close()
        self.voice = GateTalker()
        self.x, self.store = make_xiaoyou(self.folder, [self.voice])
        self.addCleanup(self.x.close)

    def pending_reminder(self):
        item = self.promise()
        checkpoint = self.x.promises.checkpoint_wake
        def save(source, before, after):
            if after["effects"][-1]["state"] == "running":
                raise OSError("claim unavailable")
            return checkpoint(source, before, after)
        self.voice.tool_calls = [("track", {"title": "saved follow-up"})]
        with mock.patch.object(self.x.promises, "checkpoint_wake", side_effect=save):
            self.x._pulse.tick()
        return item

    def pending_upkeep(self, name="remember", args=None):
        self.upkeep()
        checkpoint = self.x._pulse._checkpoint_upkeep
        def save(target, before, after):
            if after["effects"][-1]["state"] == "running":
                raise OSError("claim unavailable")
            return checkpoint(target, before, after)
        self.voice.tool_calls = [(name, args or {"note": "saved preference", "kind": "偏好"})]
        with mock.patch.object(self.x._pulse, "_checkpoint_upkeep", side_effect=save):
            self.x._pulse._maybe_upkeep(time.time() + 3600)

    def test_saved_reminder_effects_finish_after_a_lost_model_reply_without_another_model(self):
        item = self.promise()
        def run(job):
            self.assertFalse(job.tools.call("track", {"title": "saved follow-up"})[1])
            raise AgentError("model reply lost")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.x._pulse.tick()
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("must not regenerate")):
            self.x._pulse.tick()
        original = next(value for value in self.x.promises.open() if value["id"] == item["id"])
        self.assertIsNotNone(original["nudged"])
        self.assertNotIn("wake", original)
        self.assertEqual(2, len(self.x.promises.open()))
        self.assertIn("saved follow-up", self.x.context.turns("side")[0]["reply"])

    def test_a_saved_reply_survives_publication_failure_without_another_model_or_card(self):
        self.promise()
        self.voice.answers = ["the original saved reply"]
        with mock.patch.object(self.x._pulse, "_say", side_effect=OSError("publication lost")):
            with self.assertRaises(OSError):
                self.x._pulse.tick()
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("must not regenerate")):
            self.x._pulse.tick()
        self.assertEqual(["the original saved reply"], [turn["reply"] for turn in self.x.context.turns("side")])
        self.assertEqual(1, len(self.x.cards.recent("side")))

    def test_upkeep_saves_the_actual_effect_outcome_before_losing_the_model_reply(self):
        self.upkeep()
        def run(job):
            self.assertFalse(job.tools.call("remember", {"note": "saved preference", "kind": "偏好"})[1])
            raise AgentError("model reply lost")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.x._pulse._maybe_upkeep(time.time() + 3600)
        saved = json.loads(self.x._pulse._marker.read_text(encoding="utf-8"))
        self.assertEqual("succeeded", saved["wake"]["effects"][0]["state"])
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("must not regenerate")):
            self.x._pulse._maybe_upkeep(time.time() + 3600)
        self.assertEqual(1, len(self.x.memory.notes()))
        self.assertNotIn("wake", json.loads(self.x._pulse._marker.read_text(encoding="utf-8")))

    def test_a_pending_reminder_resumes_its_original_input_after_restart(self):
        item = self.pending_reminder()
        self.assertEqual(1, len(self.x.promises.open()))
        self.restart()
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no model decision")):
            self.x._pulse.tick()
        self.assertEqual(2, len(self.x.promises.open()))
        self.assertNotIn("wake", next(value for value in self.x.promises.open() if value["id"] == item["id"]))

    def test_a_running_claim_without_a_result_is_preserved_without_repeating_the_tool(self):
        item = self.promise()
        write = self.x.promises._write
        def save(items, durable=True):
            for value in items:
                if any(effect["state"] in ("succeeded", "unknown") for effect in value.get("wake", {}).get("effects", [])):
                    raise OSError("outcome unavailable")
            return write(items, durable)
        self.voice.tool_calls = [("track", {"title": "may have applied"})]
        with mock.patch.object(self.x.promises, "_write", side_effect=save):
            self.x._pulse.tick()
        self.restart()
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no model decision")):
            self.x._pulse.tick()
        original = next(value for value in self.x.promises.open() if value["id"] == item["id"])
        self.assertEqual("running", original["wake"]["effects"][0]["state"])
        self.assertEqual(2, len(self.x.promises.open()))
        self.assertTrue(self.x._pulse.unconfirmed("side"))

    def test_a_failed_selection_checkpoint_does_not_execute_the_tool(self):
        self.promise()
        self.voice.tool_calls = [("track", {"title": "must not exist"})]
        with mock.patch.object(self.x.promises, "_write", side_effect=OSError("disk full")):
            self.x._pulse.tick()
        self.assertEqual(["original reminder"], [value["title"] for value in self.x.promises.open()])
        self.assertEqual([], self.x.promises.wakes())

    def test_a_committed_effect_receipt_error_does_not_retract_success_or_repeat(self):
        self.promise()
        write = self.x.promises._write
        def save(items, durable=True):
            write(items, durable)
            if any(effect["state"] == "succeeded" for value in items for effect in value.get("wake", {}).get("effects", [])):
                raise OSError("receipt lost after commit")
        self.voice.tool_calls = [("track", {"title": "saved follow-up"})]
        with mock.patch.object(self.x.promises, "_write", side_effect=save):
            self.x._pulse.tick()
        self.assertEqual(2, len(self.x.promises.open()))
        self.assertEqual([], self.x.promises.wakes())

    def test_legacy_unknown_wakes_are_not_assumed_to_have_safe_inputs(self):
        item = self.promise()
        self.x.promises.claim_wake(item["id"], "legacy-wake")
        self.restart()
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("legacy wake must not replay")):
            self.x._pulse.tick()
        self.assertEqual("legacy-wake", self.x.promises.open()[0]["wake"]["id"])
        self.assertTrue(self.x._pulse.unconfirmed("side"))

    def test_a_resolved_original_reminder_still_delivers_its_saved_outcome(self):
        item = self.promise()
        def run(job):
            self.assertFalse(job.tools.call("resolve", {"id": item["id"], "outcome": "finished"})[1])
            raise AgentError("reply lost")
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.x._pulse.tick()
        self.assertEqual([], self.x.promises.open())
        self.restart()
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no regeneration")):
            self.x._pulse.tick()
        self.assertEqual([], self.x.promises.wakes())
        self.assertIn("关掉了", self.x.context.turns("side")[0]["reply"])

    def test_a_silent_reply_is_saved_before_the_nudge_checkpoint(self):
        self.promise()
        self.voice.answers = ["（不说）"]
        with mock.patch.object(self.x.promises, "mark_nudged", side_effect=OSError("nudge lost")):
            with self.assertRaises(OSError):
                self.x._pulse.tick()
        self.restart()
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no regeneration")):
            self.x._pulse.tick()
        self.assertEqual([], self.x.promises.due())
        self.assertEqual([], self.x.context.turns("side"))

    def test_a_repeating_reminder_advances_once_after_a_lost_checkpoint(self):
        item = self.x.promises.track("daily reminder", due=time.time() - 5, every="day")
        with mock.patch.object(self.x.promises, "mark_nudged", side_effect=OSError("nudge lost")):
            with self.assertRaises(OSError):
                self.x._pulse.tick()
        self.restart()
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no regeneration")):
            self.x._pulse.tick()
        after = self.x.promises.open()[0]
        self.assertEqual(item["due"] + 86400, after["due"])
        self.assertEqual([], self.x.promises.due())

    def test_a_changed_owner_generation_cannot_resume_old_pending_inputs(self):
        self.pending_reminder()
        self.x.mesh = mock.Mock(runtime_id="local")
        self.x.mesh.owner_of.return_value = {"owner": "local", "generation": 3}
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no new model")):
            self.x._pulse.tick()
        self.assertEqual(1, len(self.x.promises.open()))
        self.assertEqual("pending", self.x.promises.wakes()[0]["wake"]["effects"][0]["state"])

    def test_changed_reminder_inputs_do_not_reuse_a_previous_selection(self):
        self.pending_reminder()
        items = self.x.promises._read()
        items[0]["detail"] = "changed outside the original wake"
        self.x.promises._write(items)
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no new model")):
            self.x._pulse.tick()
        self.assertEqual(1, len(self.x.promises.open()))
        self.assertEqual("pending", self.x.promises.wakes()[0]["wake"]["effects"][0]["state"])

    def test_timer_inputs_never_enter_client_or_mesh_projections(self):
        self.pending_reminder()
        service = Service(self.x, self.store)
        self.addCleanup(service.close)
        for value in (service.feed("side", messages=True), service.sync("side")):
            text = json.dumps(value)
            self.assertNotIn("saved follow-up", text)
            self.assertNotIn('"effects"', text)
            self.assertNotIn('"wake"', text)

    def test_pending_upkeep_resumes_saved_arguments_after_restart_without_a_model(self):
        self.pending_upkeep()
        self.assertEqual([], self.x.memory.notes())
        self.restart()
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no model decision")):
            self.x._pulse._maybe_upkeep(time.time() + 3600)
        self.assertEqual(["saved preference"], self.x.memory.texts())
        self.assertFalse(self.x._pulse.unconfirmed("default"))

    def test_a_pending_forget_cannot_delete_a_different_memory_version(self):
        self.x.memory.remember("original preference")
        self.pending_upkeep("forget", {"number": 1})
        self.x.memory.replace(1, "changed outside the wake")
        self.restart()
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no model decision")):
            self.x._pulse._maybe_upkeep(time.time() + 3600)
        self.assertEqual(["changed outside the wake"], self.x.memory.texts())
        self.assertTrue(self.x._pulse.unconfirmed("default"))

    def test_a_pending_forget_with_the_same_memory_version_executes_once(self):
        self.x.memory.remember("original preference")
        self.pending_upkeep("forget", {"number": 1})
        self.restart()
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no model decision")):
            self.x._pulse._maybe_upkeep(time.time() + 3600)
            self.x._pulse._maybe_upkeep(time.time() + 3600)
        self.assertEqual([], self.x.memory.notes())
        self.assertFalse(self.x._pulse.unconfirmed("default"))

    def test_two_timer_claimants_cannot_invoke_one_effect_twice(self):
        item = self.promise()
        saves = lambda before, after: self.x.promises.checkpoint_wake(item, before, after)
        scopes = [SavedWake("timer", ("local", 0), wake_source(item), None, saves, lambda: None) for _ in range(2)]
        invoked = []
        def run():
            invoked.append(True)
            return "applied", False
        scopes[0].effects.run("track", {"title": "only once"}, run)
        with self.assertRaises(EffectUncertain):
            scopes[1].effects.run("track", {"title": "only once"}, run)
        self.assertEqual([True], invoked)

    def test_retention_preserves_unconfirmed_reminders_even_if_the_item_is_closed(self):
        item = self.promise()
        self.x.promises.claim_wake(item["id"], "legacy-wake")
        self.x.promises.resolve(item["id"])
        for index in range(205):
            self.x.promises.track("later %d" % index)
        self.assertEqual([item["id"]], [value["id"] for value in self.x.promises.wakes()])

    def test_a_damaged_upkeep_marker_is_not_reset_into_new_work(self):
        self.upkeep()
        self.x._pulse._marker.write_text("broken-json", encoding="utf-8")
        self.restart()
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no model decision")):
            self.x._pulse._maybe_upkeep(time.time() + 3600)
        self.assertEqual("broken-json", self.x._pulse._marker.read_text(encoding="utf-8"))
        self.assertTrue(self.x._pulse.unconfirmed("default"))

    def test_reset_history_invalidates_pending_upkeep_without_mutating_memory(self):
        self.pending_upkeep()
        self.x.context.clear("default")
        self.restart()
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no model decision")):
            self.x._pulse._maybe_upkeep(time.time() + 3600)
        self.assertEqual([], self.x.memory.notes())
        self.assertTrue(self.x._pulse.unconfirmed("default"))

    def test_an_unknown_upkeep_effect_cannot_be_bypassed_by_new_turns(self):
        self.pending_upkeep()
        path = self.x._pulse._marker
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["wake"]["effects"][0]["state"] = "running"
        path.write_text(json.dumps(saved), encoding="utf-8")
        card = self.x.cards.recent("default")[0]
        self.x._writer.exchange(card["id"], "new question", "answer", turn_id="u3")
        self.x._writer.exchange(card["id"], "other question", "answer", turn_id="u4")
        self.restart()
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no new model")):
            self.x._pulse._maybe_upkeep(time.time() + 3600)
        self.assertEqual([], self.x.memory.notes())
        self.assertTrue(self.x._pulse.unconfirmed("default"))

    def test_a_committed_marker_receipt_error_does_not_repeat_memory_work(self):
        self.upkeep()
        from xiaoyou_runtime import proactive
        write = proactive.durable_write
        def save(path, state):
            write(path, state)
            raise OSError("receipt lost after commit")
        self.voice.tool_calls = [("remember", {"note": "saved preference", "kind": "偏好"})]
        with mock.patch.object(proactive, "durable_write", side_effect=save):
            self.x._pulse._maybe_upkeep(time.time() + 3600)
        self.restart()
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no model decision")):
            self.x._pulse._maybe_upkeep(time.time() + 3600)
        self.assertEqual(["saved preference"], self.x.memory.texts())
        self.assertFalse(self.x._pulse.unconfirmed("default"))

    def test_pending_timer_results_block_handoff_until_the_original_receipt_is_saved(self):
        self.pending_reminder()
        service = Service(self.x, self.store)
        self.addCleanup(service.close)
        with service._changed:
            self.assertIsNotNone(service._busy_reason("side"))
            self.assertIsNone(service._busy_reason("other"))
        # Use the original conversation queue and wait until it drains.
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no model decision")):
            self.x._pulse.tick()
            from test_runtime import until
            self.assertTrue(until(lambda: not service._busy))
        with service._changed:
            self.assertIsNone(service._busy_reason("side"))

    def test_a_pending_replace_cannot_overwrite_a_new_memory_revision(self):
        self.x.memory.remember("original preference")
        self.pending_upkeep("replace", {"number": 1, "note": "selected replacement"})
        self.x.memory.remember("new preference added meanwhile")
        self.restart()
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no model decision")):
            self.x._pulse._maybe_upkeep(time.time() + 3600)
        self.assertEqual(["original preference", "new preference added meanwhile"], self.x.memory.texts())

    def test_memory_revision_is_checked_atomically_at_the_actual_delete(self):
        self.upkeep()
        self.x.memory.remember("original preference")
        checkpoint = self.x._pulse._checkpoint_upkeep
        def save(target, before, after):
            checkpoint(target, before, after)
            if after["effects"][-1]["state"] == "running":
                self.x.memory.replace(1, "new version after claim")
        self.voice.tool_calls = [("forget", {"number": 1})]
        with mock.patch.object(self.x._pulse, "_checkpoint_upkeep", side_effect=save):
            self.x._pulse._maybe_upkeep(time.time() + 3600)
        self.assertEqual(["new version after claim"], self.x.memory.texts())
        saved = json.loads(self.x._pulse._marker.read_text(encoding="utf-8"))
        self.assertEqual("unknown", saved["wake"]["effects"][0]["state"])

    def test_a_lost_card_checkpoint_reuses_the_original_empty_delivery_card(self):
        self.promise()
        checkpoint = self.x.promises.checkpoint_wake
        def save(source, before, after):
            if "card" in after:
                raise OSError("card checkpoint unavailable")
            return checkpoint(source, before, after)
        self.voice.answers = ["saved reply"]
        with mock.patch.object(self.x.promises, "checkpoint_wake", side_effect=save):
            with self.assertRaises(OSError):
                self.x._pulse.tick()
        self.restart()
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no regeneration")):
            self.x._pulse.tick()
        self.assertEqual(1, len(self.x.cards.recent("side")))
        self.assertEqual(["saved reply"], [turn["reply"] for turn in self.x.context.turns("side")])

    def test_malformed_wake_metadata_is_preserved_without_a_model_or_mutation(self):
        self.pending_reminder()
        original = self.x.promises._read()
        variants = [{"version": True}, {"effects": "unreadable"}, {"id": "another-event"},
                    {"card": 123}, {"reply": 123}, {"source": {"id": "another-source"}}]
        for fields in variants:
            with self.subTest(fields=fields):
                items = deepcopy(original)
                items[0]["wake"].update(fields)
                self.x.promises._write(items)
                with mock.patch.object(self.voice, "run", side_effect=AssertionError("no model decision")):
                    self.x._pulse.tick()
                self.assertEqual(items, self.x.promises._read())


if __name__ == "__main__":
    unittest.main()
