"""The record hub is the only way to change records (docs/development/unified-message-model,
"Target architecture"). These tests keep it that way: no module writes cards or takes
sequence numbers directly, and the hub's events refuse what they do not own."""

import ast
import unittest
from pathlib import Path

from test_runtime import TempDirCase
from xiaoyou_runtime.cards import Cards
from xiaoyou_runtime.record import Record, RecordError

PACKAGE = Path(__file__).resolve().parent.parent / "xiaoyou_runtime"
# The hub itself and the stores underneath it.
ALLOWED = {"record.py", "cards.py", "message_log.py"}
WRITES = {"open", "update", "add_attachments", "bump", "request", "request_changed", "replica",
          "replicate_message", "activity"}


class SingleWritePathTest(unittest.TestCase):
    def test_no_module_writes_cards_or_takes_sequence_numbers_directly(self):
        offenders = []
        for path in sorted(PACKAGE.glob("*.py")):
            if path.name in ALLOWED:
                continue
            for node in ast.walk(ast.parse(path.read_text("utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in WRITES):
                    base = ast.unparse(node.func.value)
                    if base.split(".")[-1] in ("cards", "_cards", "cards_store"):
                        offenders.append("%s:%d %s.%s" % (path.name, node.lineno, base, node.func.attr))
        self.assertEqual([], offenders, "write through record.Record instead")


class RecordTest(TempDirCase):
    def setUp(self):
        super().setUp()
        self.cards = Cards(self.folder / "cards.json")
        self.record = Record(self.cards)

    def test_events_build_the_entries(self):
        card = self.record.task_open("default", "查一下", "working", "codex")
        self.record.exchange(card["id"], said="查一下", say="交给 codex 了", turn_id="m1", kind="handoff")
        self.record.progress(card["id"], "读了 3 个文件")
        self.record.reply(card["id"], "查好了", turn_id="m1-result", kind="worker_result", author="codex")
        self.record.task(card["id"], state="done", brief="查好了")
        stored = self.cards.get(card["id"])
        self.assertEqual(["m1:you", "m1:reply", "m1-result:reply"], [entry["id"] for entry in stored["entries"]])
        self.assertEqual(["user", "handoff", "worker_result"], [entry["kind"] for entry in stored["entries"]])
        self.assertEqual(("done", ["读了 3 个文件"]), (stored["state"], stored["progress"]))

    def test_events_refuse_what_they_do_not_own(self):
        card = self.record.task_open("default", "事", "working", "codex")
        with self.assertRaises(RecordError):
            self.record.task(card["id"], said="走 exchange")
        with self.assertRaises(RecordError):
            self.record.task(card["id"], entries=[])
        with self.assertRaises(RecordError):
            self.record.exchange(card["id"], said="x", say="y", kind="whatever")
        with self.assertRaises(RecordError):
            self.record.reply(card["id"], "y", scope="elsewhere")
        with self.assertRaises(RecordError):
            self.record.observed(card["id"], state="done")  # not an observed card
        observed = self.record.observed_open("__desktop__", "电脑上的", "unknown", "claude")
        self.record.observed(observed["id"], monitoring_only=True, state="working", entries=[
            {"id": "e1", "role": "xiaoyou", "text": "在做", "at": 1.0}])
        self.assertEqual("working", self.cards.get(observed["id"])["state"])

    def test_a_request_change_revises_numbers_and_logs_in_one_step(self):
        message = {"id": "r1", "conversation": "default", "text": "你好", "status": "queued", "rev": 1,
                   "seq": 0, "card": None, "reply": "", "partial": "", "created_at": 1.0}
        before = self.cards.seq
        self.record.request_changed(message)
        self.assertEqual(2, message["rev"])
        self.assertGreater(message["seq"], before)
        # The log gives each of the two records (question, reply) its own later number.
        self.assertGreaterEqual(self.cards.seq, message["seq"])
        logged = self.cards.message("r1:you")
        self.assertIsNotNone(logged)
        self.assertEqual("你好", logged["text"])
        seq = message["seq"]
        self.record.request_changed(message, revise=False)
        self.assertEqual((2, True), (message["rev"], message["seq"] > seq))
        self.assertGreater(self.record.sequence(), message["seq"])


class TranscriptFromLogTest(TempDirCase):
    """Step 4: the model's conversation record takes its text from the message log."""

    def test_turns_keep_only_references_and_read_the_log(self):
        from test_runtime import Scripted, make_xiaoyou
        import json
        claude = Scripted("claude")
        xiaoyou, store = make_xiaoyou(self.folder, [claude, Scripted("codex", speaks=False)])
        self.addCleanup(xiaoyou.close)
        xiaoyou.hear("你好", "default", "t1")
        turn = store.transcript.turns("default")[-1]
        self.assertEqual(("你好", "re:你好"), (turn["text"], turn["reply"]))
        saved = json.loads((self.folder / "state" / "transcript.json").read_text("utf-8"))["default"]["turns"][-1]
        self.assertEqual(("t1:you", "t1:reply"), (saved.get("you_ref"), saved.get("reply_ref")))
        self.assertNotIn("text", saved)
        self.assertNotIn("reply", saved)

    def test_text_not_in_the_log_is_kept_and_missing_log_entries_are_dropped(self):
        from xiaoyou_runtime.store import Transcript
        log = {"a:you": "问", "a:reply": "答"}
        transcript = Transcript(self.folder / "transcript.json")
        transcript.resolve_with(log.get)
        transcript.add("default", "a", "问", "答", "claude", [])
        # A synthesized prompt is not a message: its text is kept; the reply is linked to the log.
        transcript.add("default", "r", "（后台的事「x」有结果了）", "结果", "codex", [], reply_ref="run-summary:reply")
        log["run-summary:reply"] = "结果"
        turns = transcript.turns("default")
        self.assertEqual([("问", "答"), ("（后台的事「x」有结果了）", "结果")],
                         [(turn["text"], turn["reply"]) for turn in turns])
        # A reference whose message left the log is no longer shown.
        del log["a:you"]
        self.assertEqual(["r"], [turn["id"] for turn in transcript.turns("default")])
        # Records written before this version (text only) still load and show.
        legacy = self.folder / "legacy.json"
        legacy.write_text('{"default": {"turns": [{"id": "old", "text": "旧问", "reply": "旧答", "at": 1, "by": "claude", "seen": []}], "known": ["old"]}}', "utf-8")
        old = Transcript(legacy)
        old.resolve_with(lambda message_id: None)
        self.assertEqual(("旧问", "旧答"), (old.turns("default")[0]["text"], old.turns("default")[0]["reply"]))


if __name__ == "__main__":
    unittest.main()
