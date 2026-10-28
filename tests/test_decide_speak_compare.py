"""Regression checks for experiment completion and late-call accounting."""
import contextlib
import io
import json
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "experiments"))
import decide_speak_compare as harness


class ExperimentMeasurementsTest(unittest.TestCase):
    def speech(self, ending):
        class Provider:
            def stream(self, body, timeout, on_event):
                on_event({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "unfinished"}})
                if ending == "error":
                    raise OSError("connection lost")
                if ending:
                    on_event({"type": "message_delta", "delta": {"stop_reason": ending}})
        return harness.speak(Provider(), "fake", "", [], "", [], None, {}, time.monotonic(), 1)

    def test_broken_single_bubble_is_not_released(self):
        for ending in (None, "error", "max_tokens"):
            with self.subTest(ending=ending):
                speech = self.speech(ending)
                self.assertIsNone(speech["first_bubble_ms"])
                self.assertTrue(speech["error"])

    def test_completed_single_bubble_is_released(self):
        speech = self.speech("end_turn")
        self.assertIsNotNone(speech["first_bubble_ms"])
        self.assertIsNone(speech["error"])

    def test_timely_selection_only_records_simulated_queue(self):
        for agent, deadline in ((agent, deadline) for agent in ("cc", "codex", "missing")
                                for deadline in (1.3, None)):
            with self.subTest(agent=agent, deadline=deadline):
                arguments = {"agent": agent, "title": "test", "task": "run synthetic tests"}
                class Provider:
                    def complete(self, body, timeout):
                        return {"content": [{"type": "tool_use", "id": "synthetic",
                                             "name": "start_task", "input": arguments}]}
                world = harness.world_of({})
                recorder = harness.Recorder(world)
                decision = harness.decide(Provider(), "fake", "", [], world, recorder,
                                          time.monotonic(), deadline, 1)
                self.assertEqual(recorder.calls, [("start_task", arguments)])
                self.assertEqual(decision["status"], "refused" if agent == "missing" else "selected")
                if agent != "missing":
                    self.assertIn("queued", decision["facts"])
                self.assertEqual(world["tasks"], [])

    def test_unbounded_selection_waits_and_late_limited_selection_never_executes(self):
        class Provider:
            def complete(self, body, timeout):
                time.sleep(0.03)
                return {"content": [{"type": "tool_use", "id": "synthetic", "name": "start_task",
                                     "input": {"agent": "codex", "title": "test", "task": "synthetic"}}]}
        for deadline in (0.001, None):
            with self.subTest(deadline=deadline):
                world = harness.world_of({})
                recorder = harness.Recorder(world)
                decision = harness.decide(Provider(), "fake", "", [], world, recorder,
                                          time.monotonic(), deadline, 1)
                self.assertTrue(decision["thread"].wait(1))
                self.assertEqual(decision["status"], "selected" if deadline is None else "timeout")
                self.assertEqual(len(recorder.calls), 1 if deadline is None else 0)

    def test_review_keeps_context_and_leaves_editable_choices(self):
        case = harness.load_cases(harness.CASES, ["long-01"])[0]
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "results.jsonl"
            source.write_text("\n".join(json.dumps({"case": "long-01", "arm": arm, "repeat": 0,
                                                  "speech": {"text": "answer"}}) for arm in ("S", "P")))
            review = Path(folder) / "review"
            with contextlib.redirect_stdout(io.StringIO()):
                harness.main(["review", str(source), "--out-dir", str(review)])
            sheet = (review / "blind-review.md").read_text()
            self.assertIn(case["history"][0]["text"], sheet)
            self.assertIn("选择（填 A / B / 平）：", sheet)
            self.assertIn("备注（可选）：", sheet)
            self.assertNotIn('"arm"', sheet)
            form = (review / "public" / "index.html").read_text()
            self.assertIn('value="tie"', form)
            self.assertIn('导出标注', form)
            self.assertNotIn('blind-review-key', form)
            self.assertEqual([p.name for p in (review / "public").iterdir()], ["index.html"])

    def test_v2_rules_hide_internal_wording_and_keep_r7_unchanged(self):
        r7 = harness.speak_system("persona", harness.NOTHING_DONE)
        self.assertIn("这一轮已经发生的事实：" + harness.NOTHING_DONE, r7)
        v2 = harness.speak_system("persona", harness.NOTHING_DONE, "v2")
        self.assertNotIn(harness.NOTHING_DONE, v2)
        self.assertIn(harness.FACTS_V2[harness.NOTHING_DONE], v2)
        queued = "决定已落实：类型 start_task；任务 c100；当前状态 queued。"
        self.assertIn(queued, harness.speak_system("persona", queued, "v2"))
        for case in harness.load_cases(harness.CASES, None):
            for line in case["owner"]:
                if len(line) >= 4:  # “好”“ok”这类短句必然撞上，只查实质内容
                    self.assertNotIn(line, harness.SPEAK_RULES_V2, case["id"])

    def test_respeak_reuses_recorded_decision_without_calling_decide(self):
        decision = {"status": "selected", "name": "start_task", "args": {"agent": "codex", "title": "t", "task": "t"},
                    "facts": "决定已落实：类型 start_task；任务 c100；当前状态 queued。", "match": True}
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "results.jsonl"
            source.write_text("\n".join(json.dumps({"case": "mixed-01", "arm": arm, "repeat": 0, "decision": decision})
                                        for arm in ("S", "P")))
            target = Path(folder) / "respeak.jsonl"
            seen = []
            real_speak = harness.speak
            def spy(*positional, **named):
                seen.append((positional[4], positional[5], named["rules"]))
                return real_speak(*positional, **named)
            with patch.object(harness, "decide", side_effect=AssertionError("decide called")), \
                    patch.object(harness, "speak", side_effect=spy), contextlib.redirect_stdout(io.StringIO()):
                harness.main(["respeak", str(source), "--dry-run", "--only", "mixed-01", "--repeats", "1",
                              "--pause", "0", "--out", str(target)])
            rows = [json.loads(line) for line in target.read_text().splitlines()]
            self.assertEqual(sorted(row["arm"] for row in rows), ["r7", "v2"])
            self.assertTrue(all(row["decision"]["name"] == "start_task" and row["speech"]["text"] for row in rows))
            for facts, turn, _ in seen:
                self.assertEqual(facts, decision["facts"])
                self.assertEqual(turn[0]["content"][0]["input"], decision["args"])
            review = Path(folder) / "review"
            with contextlib.redirect_stdout(io.StringIO()):
                harness.main(["review", str(target), "--out-dir", str(review)])
            key = json.loads((review / "blind-review-key.json").read_text())
            self.assertEqual(sorted(key["mixed-01"].values()), ["r7", "v2"])

    def test_holdout_cases_are_new_and_self_consistent(self):
        old = harness.load_cases(harness.CASES, None)
        new = harness.load_cases(harness.CASES.with_name("decide_speak_cases_holdout.jsonl"), None)
        self.assertEqual(len({case["id"] for case in new}), len(new))
        self.assertFalse({case["id"] for case in new} & {case["id"] for case in old})
        old_lines = {line for case in old for line in case["owner"]}
        self.assertFalse({line for case in new for line in case["owner"]} & old_lines)
        count = lambda cases: {cat: sum(1 for case in cases if case["category"] == cat) for cat in {c["category"] for c in cases}}
        self.assertEqual({k: v for k, v in count(new).items() if k != "纠错与如实说明"}, count(old))
        tools = {tool["name"] for tool in harness.commitment_tools(harness.world_of({}))}
        for case in new:
            with self.subTest(case=case["id"]):
                world = harness.world_of(case)
                self.assertIn(case["expected"]["decision"], tools)
                cards = {task["card"] for task in world["tasks"]}
                text = " ".join([item["text"] for item in case["history"]] + [case["expected"].get("args_hint", "")])
                self.assertLessEqual(set(re.findall(r"\bc\d+\b", text)), cards)
                for number in re.findall(r"number=(\d+)", case["expected"].get("args_hint", "")):
                    self.assertLessEqual(int(number), len(world["memories"]))

    def test_companion_cases_are_new_and_light(self):
        earlier = [case for name in ("decide_speak_cases.jsonl", "decide_speak_cases_holdout.jsonl")
                   for case in harness.load_cases(harness.CASES.with_name(name), None)]
        new = harness.load_cases(harness.CASES.with_name("decide_speak_cases_companion.jsonl"), None)
        self.assertEqual(len({case["id"] for case in new}), len(new))
        self.assertFalse({case["id"] for case in new} & {case["id"] for case in earlier})
        self.assertFalse({line for case in new for line in case["owner"]} &
                         {line for case in earlier for line in case["owner"]})
        for case in new:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["category"].startswith("陪伴·"))
                self.assertEqual(case["expected"]["decision"], "just_reply")
                self.assertLessEqual(len(case["history"]), 6)  # 陪伴题只带很短的前文

    def test_late_decision_tokens_are_counted_without_executing(self):
        done = threading.Event()
        done.set()
        box = {"real_ms": 2100, "answer": {"usage": {"input_tokens": 123, "output_tokens": 45},
               "content": [{"type": "tool_use", "name": "start_task"}]}}
        record = {"arm": "S", "case": "chat-02", "category": "fixture", "release_ms": None,
                  "decision": {"status": "timeout", "usage": {}, "acceptable": ["just_reply"]},
                  "_decision_thread": done, "_decision_box": box}
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "results.jsonl"
            with patch.object(harness, "run_serial", return_value=record), contextlib.redirect_stdout(io.StringIO()):
                harness.main(["run", "--dry-run", "--only", "chat-02", "--arms", "S", "--repeats", "1",
                              "--pause", "0", "--out", str(target)])
            decision = json.loads(target.read_text())["decision"]
            self.assertEqual(decision["status"], "timeout")
            self.assertEqual(decision["late_name"], "start_task")
            self.assertEqual(decision["usage"], box["answer"]["usage"])
            self.assertFalse(decision["match"])


if __name__ == "__main__":
    unittest.main()
