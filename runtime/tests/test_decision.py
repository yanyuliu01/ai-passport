"""Commitment selection, safe timeout, and dispatch-before-speech integration."""

import dataclasses
import io
import json
import threading
import time
import unittest
from unittest.mock import patch

from test_streaming import FakeOpener, dsh_spec, sse, tool_sse
from test_runtime import TempDirCase, Scripted, make_xiaoyu, write_config
from xiaoyu_runtime import config
from xiaoyu_runtime.loop import decision
from xiaoyu_runtime.helpers import dsh_stream
from xiaoyu_runtime.helpers.agents import DshAgent, Job
from xiaoyu_runtime.loop.voice_tools import Toolbox


def selected(name="just_reply", args=None, count=1):
    return json.dumps({"content": [{"type": "tool_use", "id": "d%d" % i,
                                    "name": name, "input": args or {}} for i in range(count)]}).encode()


class DecisionTests(TempDirCase):
    def box(self, problem=None):
        return Toolbox(["codex"], [], [], lambda fields: problem, lambda card: "仍在做")

    def choose(self, opener, box=None, **kwargs):
        box = box or self.box()
        job = Job(text="查日志", tools=box, decision_only=True, **kwargs)
        return decision.choose(dsh_spec(self.folder), job, {"DEEPSEEK_API_KEY": "k"}, "m", opener)

    def test_default_is_off_and_config_is_strict(self):
        self.assertFalse(config.load(write_config(self.folder), {}).decide_first)
        self.assertTrue(config.load(write_config(self.folder, xiaoyu={"decide_first": True}), {}).decide_first)
        with self.assertRaises(config.ConfigError):
            config.load(write_config(self.folder, xiaoyu={"decide_first": "yes"}), {})

    def test_one_nonstreamed_decision_has_commitments_only(self):
        opener = FakeOpener(selected("start_task", {"agent": "codex", "title": "日志", "task": "检查日志"}))
        result = self.choose(opener)
        body = opener.requests[0][1]
        self.assertFalse(body["stream"])
        self.assertNotIn("task_detail", {item["name"] for item in body["tools"]})
        self.assertNotIn("recall", {item["name"] for item in body["tools"]})
        self.assertEqual(result.fields["action"]["type"], "start")

    def test_refused_action_is_a_fact_and_is_not_committed(self):
        result = self.choose(FakeOpener(selected("start_task", {"agent": "codex", "task": "x"})), self.box("队列满了"))
        self.assertEqual(result.fields["action"]["type"], "none")
        self.assertEqual(result.fields["_decision"]["status"], "refused")
        self.assertIn("队列满了", result.fields["_decision"]["facts"])

    def test_multiple_or_unknown_decisions_execute_nothing(self):
        for payload in (selected("cancel_task", count=2), selected("task_detail"), selected("unknown")):
            box = self.box()
            result = self.choose(FakeOpener(payload), box)
            self.assertEqual(box.calls, [])
            self.assertEqual(result.fields["_decision"]["status"], "refused")

    def test_deadline_discards_a_late_commitment(self):
        box = self.box()
        ready = threading.Event()

        def slow(request, timeout):
            ready.wait(.15)
            return io.BytesIO(selected("cancel_task"))

        began = time.monotonic()
        with patch.object(decision, "DEADLINE_SECONDS", .02):
            result = self.choose(slow, box)
        self.assertLess(time.monotonic() - began, .12)
        self.assertEqual(result.fields["_decision"]["status"], "timeout")
        ready.set()
        time.sleep(.02)
        self.assertEqual(box.calls, [])

    def test_interruption_before_commitment_executes_nothing(self):
        cut = threading.Event()
        cut.set()
        box = self.box()
        self.choose(FakeOpener(selected("cancel_task")), box, interrupt=cut)
        self.assertEqual(box.calls, [])

    def test_speaking_can_lookup_but_cannot_change_anything_or_nudge(self):
        box = self.box()
        speaking = box.speaking()
        self.assertEqual(speaking.call("task_detail", {"card": "c1"}), ("仍在做", False))
        for name in ("start_task", "cancel_task", "remember", "track"):
            self.assertTrue(speaking.call(name, {})[1])
        self.assertIsNone(box.action)
        self.assertFalse(speaking.promised("我这就去查"))

    def test_task_is_launched_before_first_spoken_text_and_recorded_once(self):
        launched = []
        helper = Scripted("codex", speaks=False)
        base = FakeOpener(selected("start_task", {"agent": "codex", "title": "查日志", "task": "查日志"}),
                          sse("日志检查已交给 codex。"))
        owner = []

        def opener(request, timeout=None):
            body = json.loads(request.data)
            if body["stream"]:
                self.assertEqual(len(launched), 1)
                card = xiaoyu.cards.get(launched[0])
                self.assertEqual([entry["role"] for entry in card["entries"]], ["you"])
                self.assertNotIn("start_task", {tool["name"] for tool in body.get("tools", [])})
            return base(request, timeout)

        voice = DshAgent(dsh_spec(self.folder), opener=opener)
        xiaoyu, store = make_xiaoyu(self.folder, [voice, helper], decide_first=True)
        self.addCleanup(xiaoyu.close)
        with patch.object(xiaoyu, "_launch", side_effect=lambda card, *a, **k: launched.append(card)):
            turn = xiaoyu._converse(voice, "检查一下日志", "default", "r1", None, [helper], 0,
                                     lambda kind, agent, text: owner.append((kind, text)))
        self.assertEqual(turn.reply, "日志检查已交给 codex。")
        entries = xiaoyu.cards.get(turn.card)["entries"]
        self.assertEqual([entry["id"] for entry in entries], ["r1:you", "r1:reply"])
        self.assertEqual(len(store.transcript.turns("default")), 1)
        history = dsh_stream._load(dsh_stream.sessions_dir(voice.spec), store.session("default", voice.name))
        human_messages = [item["content"] for item in history if item["role"] == "user" and isinstance(item["content"], str)]
        self.assertEqual(human_messages, ["检查一下日志"])

    def test_speaking_tool_attempt_is_refused_without_dispatching(self):
        box = self.box()
        opener = FakeOpener(tool_sse("", "start_task", {"agent": "codex", "task": "x"}), sse("我还没有启动任务。"))
        result = dsh_stream.run(dsh_spec(self.folder), Job(text="x", tools=box.speaking()),
                                 {"DEEPSEEK_API_KEY": "k"}, "m", lambda text: None, opener)
        self.assertEqual(result.fields["action"]["type"], "none")
        self.assertIsNone(box.action)

    def test_lookup_after_decision_continues_speaking_on_the_same_card(self):
        opener = FakeOpener(selected(), tool_sse("", "task_detail", {"card": "c1"}), sse("这件事仍在做。"))
        voice = DshAgent(dsh_spec(self.folder), opener=opener)
        helper = Scripted("codex", speaks=False)
        xiaoyu, store = make_xiaoyu(self.folder, [voice, helper], decide_first=True)
        self.addCleanup(xiaoyu.close)
        card = xiaoyu.record.task_open("default", "检查", "working", "codex")
        self.assertEqual(card["id"], "c1")
        turn = xiaoyu._converse(voice, "检查得怎样", "default", "r1", None, [helper], 0, lambda *args: None)
        self.assertEqual((turn.card, turn.reply), (card["id"], "这件事仍在做。"))
        self.assertEqual(len(opener.requests), 3)

    def test_timeout_falls_back_to_speaking_without_late_dispatch_or_hidden_reminder(self):
        ready = threading.Event()
        late_done = threading.Event()

        def opener(request, timeout=None):
            body = json.loads(request.data)
            if not body["stream"]:
                ready.wait(.2)
                late_done.set()
                return io.BytesIO(selected("start_task", {"agent": "codex", "task": "查日志"}))
            self.assertIn("超时", body["system"])
            return io.BytesIO(sse("这轮还没有安排检查。"))

        voice = DshAgent(dsh_spec(self.folder), opener=opener)
        helper = Scripted("codex", speaks=False)
        xiaoyu, store = make_xiaoyu(self.folder, [voice, helper], decide_first=True)
        self.addCleanup(xiaoyu.close)
        with patch.object(decision, "DEADLINE_SECONDS", .02), patch.object(xiaoyu, "_launch") as launch:
            turn = xiaoyu._converse(voice, "检查日志", "default", "r1", None, [helper], 0, lambda *args: None)
            ready.set()
            self.assertTrue(late_done.wait(.2))
            launch.assert_not_called()
        self.assertEqual(turn.reply, "这轮还没有安排检查。")
        history = dsh_stream._load(dsh_stream.sessions_dir(voice.spec), store.session("default", voice.name))
        self.assertFalse(any("Runtime 提醒" in str(item.get("content")) for item in history))

    def test_refusal_is_spoken_once_without_any_dispatch(self):
        opener = FakeOpener(selected("start_task", {"agent": "missing", "title": "x", "task": "y"}),
                            sse("这个帮手不可用，我还没有安排任务。"))
        voice = DshAgent(dsh_spec(self.folder), opener=opener)
        helper = Scripted("codex", speaks=False)
        xiaoyu, store = make_xiaoyu(self.folder, [voice, helper], decide_first=True)
        self.addCleanup(xiaoyu.close)
        with patch.object(xiaoyu, "_launch") as launch:
            turn = xiaoyu._converse(voice, "检查日志", "default", "r1", None, [helper], 0, lambda *args: None)
            launch.assert_not_called()
        self.assertIn("办不了", opener.requests[1][1]["system"])
        self.assertFalse(turn.started)
        self.assertEqual(len(xiaoyu.cards.get(turn.card)["entries"]), 2)


if __name__ == "__main__":
    unittest.main()
