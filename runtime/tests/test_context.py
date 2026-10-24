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


if __name__ == "__main__":
    unittest.main()
