"""Context is a read projection; delivery receipts never own message bodies."""

import json
import unittest
from unittest import mock

from test_runtime import Scripted, TempDirCase, make_xiaoyou
from xiaoyou_runtime.cards import Cards
from xiaoyou_runtime.intake import Intake
from xiaoyou_runtime.store import Store


class ContextTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.state = self.folder / "state"
        self.cards = Cards(self.state / "cards.json")
        self.intake = Intake.for_cards(self.cards)
        self.writer = self.intake.writer("owner", actor="test")
        self.store = Store(self.state)
        self.store.transcript.resolve_with(lambda mid: (self.cards.message(mid) or {}).get("text"))
        self.context = self.store.bind_context(self.cards)
        self.card = self.writer.task_open("side", "original task")

    def exchange(self, tid="a", text="question", reply="saved answer"):
        self.writer.exchange(self.card["id"], text, reply, turn_id=tid)

    def test_messages_written_by_any_intake_producer_are_available_without_transcript(self):
        self.exchange()
        self.assertEqual([("question", "saved answer")],
                         [(turn["text"], turn["reply"]) for turn in self.context.turns("side")])
        self.assertFalse((self.state / "transcript.json").exists())
        self.assertFalse((self.state / "context.json").exists())
        self.assertEqual(self.context.turns("other"), [])

    def test_journal_replay_rebuilds_context_without_model_or_transcript(self):
        self.exchange()
        # Simulate a checkpoint missing its final message/task update.
        (self.state / "cards.json").unlink()
        rebuilt = Cards(self.state / "cards.json")
        context = Store(self.state).bind_context(rebuilt)
        self.assertEqual(["saved answer"], [turn["reply"] for turn in context.turns("side")])

    def test_result_and_activity_do_not_duplicate_the_public_summary(self):
        self.exchange()
        self.writer.reply(self.card["id"], "raw worker output", turn_id="r-result",
                          kind="worker_result", scope="task", author="codex")
        self.writer.activity(self.card["id"], "codex", "tool_result", "tool output", "act")
        self.writer.reply(self.card["id"], "summary", turn_id="r-summary", kind="summary")
        self.assertEqual(["saved answer", "summary"],
                         [turn["reply"] for turn in self.context.turns("side")])
        self.assertEqual("（后台的事「original task」有结果了）", self.context.turns("side")[-1]["text"])

    def test_partial_and_failed_replies_are_not_facts_in_context(self):
        self.exchange("saved")
        self.writer.request_changed({"id": "failed", "text": "fail", "conversation": "side",
                                     "status": "failed", "error": "error", "rev": 0})
        self.writer.request_changed({"id": "stream", "text": "still", "conversation": "side",
                                     "status": "running", "partial": "uncommitted", "rev": 0})
        self.assertEqual(["saved"], [turn["id"] for turn in self.context.turns("side")])

    def test_seen_receipts_survive_restart_and_contain_no_message_body(self):
        self.exchange()
        self.context.mark("side", "speaker", self.context.unseen("side", "speaker"))
        raw = (self.state / "context.json").read_text("utf-8")
        self.assertNotIn("question", raw)
        self.assertNotIn("saved answer", raw)
        self.assertEqual([], Store(self.state).bind_context(self.cards).unseen("side", "speaker"))
        self.assertEqual(["a"], [turn["id"] for turn in self.context.unseen("side", "other")])

    def test_receipt_for_a_model_snapshot_does_not_hide_a_concurrent_message_revision(self):
        self.exchange()
        snapshot = self.context.unseen("side", "speaker")
        self.exchange(reply="corrected answer")
        self.context.mark("side", "speaker", snapshot)
        self.assertEqual(["corrected answer"],
                         [turn["reply"] for turn in self.context.unseen("side", "speaker")])

    def test_legacy_references_merge_once_and_the_canonical_revision_wins(self):
        self.store._transcript.add("side", "old-background", "old prompt", "old answer", "worker", [],
                                   reply_ref="a:reply")
        before = (self.state / "transcript.json").read_bytes()
        self.exchange()
        self.assertEqual(["saved answer"], [turn["reply"] for turn in self.context.turns("side")])
        self.assertEqual(before, (self.state / "transcript.json").read_bytes())

    def test_legacy_only_history_and_new_messages_are_both_visible(self):
        self.context.offer("side", [{"id": "old", "text": "older question", "reply": "older answer", "at": 1}])
        self.exchange()
        self.assertEqual(["old", "a"], [turn["id"] for turn in self.context.turns("side")])
        self.assertEqual(0, self.context.offer("side", [{"id": "old", "text": "older question", "reply": "older answer", "at": 1}]))

    def test_reset_is_persistent_without_deleting_messages_and_hides_later_old_revisions(self):
        self.context.offer("side", [{"id": "old", "text": "older", "reply": "reply", "at": 1}])
        self.exchange()
        self.assertTrue(self.context.clear("side"))
        self.assertFalse(self.context.clear("side"))
        self.exchange(reply="a late revision")
        context = Store(self.state).bind_context(self.cards)
        self.assertEqual([], context.turns("side"))
        self.assertEqual("a late revision", self.cards.message("a:reply")["text"])
        self.exchange("new", text="new question", reply="new answer")
        self.assertEqual(["new"], [turn["id"] for turn in context.turns("side")])

    def test_reset_persistence_failure_does_not_claim_history_was_forgotten(self):
        self.exchange()
        with mock.patch("xiaoyou_runtime.context.durable_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.context.clear("side")
        self.assertEqual(["a"], [turn["id"] for turn in self.context.turns("side")])

    def test_receipt_failure_keeps_saved_answer_available(self):
        self.exchange()
        with mock.patch("xiaoyou_runtime.context.durable_write", side_effect=OSError("disk full")):
            self.context.delivered("side", "a", ["speaker"])
        self.assertEqual("saved answer", self.context.turns("side")[0]["reply"])

    def test_background_context_excludes_the_current_handoff(self):
        helper = Scripted("codex", speaks=False)
        x, _ = make_xiaoyou(self.folder / "runtime", [helper])
        self.addCleanup(x.close)
        card = x.hear("@codex inspect this", "default", "new").card
        x.settle(card, 5)
        self.assertEqual("inspect this", helper.jobs[0].text)

    def test_model_context_never_uses_the_unpublished_full_version_of_a_truncated_reply(self):
        self.exchange(reply="visible excerpt")
        self.store._transcript.add("side", "a", "question", "unpublished full answer", "speaker", [])
        self.assertEqual("visible excerpt", self.context.turns("side")[0]["reply"])

    def test_legacy_share_does_not_duplicate_a_current_canonical_turn(self):
        self.exchange()
        self.assertEqual(0, self.context.offer("side", [{"id": "a", "text": "question", "reply": "saved answer", "at": 1}]))
        self.assertFalse((self.state / "transcript.json").exists())

    def test_a_damaged_reset_boundary_does_not_silently_restore_old_context(self):
        (self.state / "context.json").write_text("[]", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            Store(self.state).bind_context(self.cards)

    def test_decision_and_speaker_frames_read_current_task_and_conversation_commitments(self):
        from xiaoyou_runtime.promises import Promises
        promises = Promises(self.state / "promises.json")
        context = self.store.bind_context(self.cards, promises=promises)
        promises.track("side commitment", conversation="side")
        promises.track("other commitment", conversation="other")
        before = context.frame("side", self.card)
        self.writer.task(self.card["id"], state="working", agent="codex")
        after = context.frame("side", self.card)
        self.assertEqual("done", before["focus"]["state"])
        self.assertEqual("working", after["focus"]["state"])
        self.assertEqual("codex", after["focus"]["agent"])
        self.assertEqual(["side commitment"], [item["title"] for item in after["promises"]])

    def test_active_tasks_are_not_hidden_by_a_page_of_later_finished_chats(self):
        self.writer.task(self.card["id"], state="working", agent="codex")
        for index in range(32):
            self.writer.task_open("side", "later chat %d" % index)
        frame = self.context.frame("side")
        self.assertIn(self.card["id"], [card["id"] for card in frame["cards"]])
        self.assertEqual(6, len(frame["cards"]))

    def test_speaking_does_not_mark_history_omitted_from_the_prompt_as_seen(self):
        voice = Scripted("speaker")
        x, _ = make_xiaoyou(self.folder / "bounded", [voice])
        self.addCleanup(x.close)
        card = x._writer.task_open("default", "retained history")
        for index in range(20):
            x._writer.exchange(card["id"], "question-%d" % index, "a" * 900,
                               turn_id="old-%d" % index)
        x._speak(voice, "current request", "default", "system", None, 0, catch_up=True)
        self.assertNotIn("question-0", voice.jobs[0].text)
        self.assertIn("old-0", [turn["id"] for turn in x.context.unseen("default", "speaker")])

    def test_a_partial_history_excerpt_is_not_a_full_turn_delivery_receipt(self):
        voice = Scripted("speaker")
        x, _ = make_xiaoyou(self.folder / "excerpt", [voice])
        self.addCleanup(x.close)
        card = x._writer.task_open("default", "long retained answer")
        x._writer.exchange(card["id"], "older question", "a" * 2000 + "unquoted tail", turn_id="long")
        x._speak(voice, "current request", "default", "system", None, 0, catch_up=True)
        self.assertNotIn("unquoted tail", voice.jobs[0].text)
        self.assertEqual(["long"], [turn["id"] for turn in x.context.unseen("default", "speaker")])

    def test_segment_boundaries_are_versioned_and_reading_never_writes_history(self):
        for index in range(65):
            self.exchange("turn-%03d" % index, text="question-%d" % index)
        pages = self.context.segments("side")
        self.assertEqual([32, 32, 1], [page["count"] for page in pages])
        self.assertEqual("turn-000", pages[0]["first"])
        self.assertEqual("turn-064", pages[-1]["last"])
        self.assertEqual(65, len({turn["id"] for page in pages for turn in page["turns"]}))
        self.assertEqual(pages[0], self.context.segment("side", pages[0]["id"]))
        self.context.mark("side", "reader", pages[0]["turns"])
        self.assertEqual([page["id"] for page in pages], [page["id"] for page in self.context.segments("side")])
        self.assertFalse((self.state / "transcript.json").exists())
        raw = (self.state / "context.json").read_text(encoding="utf-8")
        self.assertNotIn("question-", raw)
        self.assertNotIn("saved answer", raw)

    def test_revised_reset_and_foreign_segments_are_not_returned_under_old_ids(self):
        self.exchange()
        segment = self.context.segments("side")[0]
        with self.assertRaises(ValueError):
            self.context.segment("other", segment["id"])
        self.exchange(reply="revised authoritative answer")
        with self.assertRaises(ValueError):
            self.context.segment("side", segment["id"])
        revised = self.context.segments("side")[0]
        self.assertEqual("revised authoritative answer", revised["turns"][0]["reply"])
        self.context.clear("side")
        with self.assertRaises(ValueError):
            Store(self.state).bind_context(self.cards).segment("side", revised["id"])

    def test_returned_segment_is_a_snapshot_and_cannot_mutate_the_source(self):
        self.exchange()
        key = self.context.segments("side")[0]["id"]
        selected = self.context.segment("side", key)
        selected["turns"][0]["reply"] = "client changes snapshot"
        self.assertEqual("saved answer", self.context.segment("side", key)["turns"][0]["reply"])

    def test_window_budget_and_receipts_match_complete_visible_quotes_only(self):
        self.exchange("short", text="entire question", reply="entire answer")
        self.exchange("long", text="long question", reply="a" * 2000 + "not included")
        window = self.context.window("side", "reader", max_chars=2000)
        self.assertLessEqual(len(window["body"]), 2000)
        self.assertIn("不代表完整历史档案", window["body"])
        self.assertIn("片段", window["body"])
        self.assertNotIn("not included", window["body"])
        self.assertIn("long", [turn["id"] for turn in window["turns"]])
        self.assertNotIn("long", [turn["id"] for turn in window["receipts"]])
        self.context.mark("side", "reader", window["turns"])
        self.assertIn("long", [turn["id"] for turn in self.context.unseen("side", "reader")])
        for turn in window["receipts"]:
            self.assertIn(turn["text"], window["body"])
            self.assertIn(turn["reply"], window["body"])

    def test_an_oversized_single_turn_stays_within_the_total_budget(self):
        self.exchange(text="q" * 4000, reply="r" * 4000)
        window = self.context.window("side", "reader", max_chars=256)
        self.assertLessEqual(len(window["body"]), 256)
        self.assertEqual([], window["receipts"])
        self.assertEqual(1, window["coverage"]["excerpts"])

    def test_seed_only_uses_recent_turns_without_receipting_unselected_history(self):
        for index in range(5):
            self.exchange("old-%d" % index, text="q%d" % index)
        window = self.context.window("side", "reader", seed=2)
        self.assertEqual(["old-3", "old-4"], [turn["id"] for turn in window["turns"]])
        self.context.mark("side", "reader", window["receipts"])
        self.assertEqual(["old-0", "old-1", "old-2"],
                         [turn["id"] for turn in self.context.unseen("side", "reader")])
        self.assertEqual("", self.context.window("other", "reader")["body"])

    def test_invalid_context_window_parameters_fail_before_a_model_call(self):
        for fields in ({"seed": 0}, {"seed": -1}, {"seed": True}, {"max_chars": 255},
                       {"text_chars": 0}, {"reply_chars": -1}):
            with self.assertRaises(ValueError):
                self.context.window("side", "reader", **fields)

    def test_retained_history_before_the_recent_window_is_recallable_and_resettable(self):
        from xiaoyou_runtime.store import MAX_TURNS
        voice = Scripted("speaker")
        x, store = make_xiaoyou(self.folder / "archive", [voice])
        self.addCleanup(x.close)
        card = x._writer.task_open("default", "retained archive")
        for index in range(MAX_TURNS + 5):
            x._writer.exchange(card["id"], "quartz-old-anchor" if not index else "question %d" % index,
                               "older answer %d" % index, turn_id="retained-%03d" % index)
        self.assertEqual(MAX_TURNS, len(x.context.turns("default")))
        self.assertEqual(MAX_TURNS + 5, len(x.context.history("default")))
        self.assertIn("quartz-old-anchor", x.recall("default", "quartz-old-anchor"))
        self.assertTrue(x.recall("other", "quartz-old-anchor").startswith("没找到"))
        self.assertFalse((store.state_dir / "transcript.json").exists())
        x.context.clear("default")
        self.assertEqual([], x.context.history("default"))
        self.assertTrue(x.recall("default", "quartz-old-anchor").startswith("没找到"))

    def test_provider_failure_and_decision_only_do_not_mark_the_window_seen(self):
        from xiaoyou_runtime.agents import AgentError
        voice = Scripted("speaker")
        x, _ = make_xiaoyou(self.folder / "receipt-boundary", [voice])
        self.addCleanup(x.close)
        card = x._writer.task_open("default", "history")
        x._writer.exchange(card["id"], "old question", "old answer", turn_id="old")
        voice.fail_on = "failed current request"
        with self.assertRaises(AgentError):
            x._speak(voice, "failed current request", "default", "system", None, 0, catch_up=True)
        self.assertEqual(["old"], [turn["id"] for turn in x.context.unseen("default", "speaker")])
        voice.fail_on = None
        x._speak(voice, "decision", "default", "system", None, 0, catch_up=True, decision_only=True)
        self.assertEqual(["old"], [turn["id"] for turn in x.context.unseen("default", "speaker")])
        x._speak(voice, "reply", "default", "system", None, 0, catch_up=True)
        self.assertEqual([], x.context.unseen("default", "speaker"))


if __name__ == "__main__":
    unittest.main()
