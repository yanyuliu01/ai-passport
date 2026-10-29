"""Speech retries share one deadline and obsolete callbacks cannot act."""

import threading
import unittest
from unittest import mock

from test_runtime import ToolTalker, TempDirCase, make_xiaoyu
from xiaoyu_runtime.helpers.base import AgentError, Outcome
from xiaoyu_runtime.helpers.deadline import Deadline
from xiaoyu_runtime.loop.effects import Effects


class SpeechBudgetTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.voice = ToolTalker()
        self.x, self.store = make_xiaoyu(self.folder, [self.voice], voice_timeout_seconds=10)
        self.addCleanup(self.x.close)
        self.now = 0.0
        self.cut = threading.Event()

    def converse(self):
        tools = self.x._toolbox("side", None, [self.voice], False)
        return self.x._converse_with(self.voice, "question", "side", "turn", None, [self.voice], 0,
            lambda *args: None, False, None, None, lambda tools: "system", {}, tools, self.cut)

    @staticmethod
    def answer():
        return Outcome("answer", "session", {"reply": "answer", "action": {"type": "none"}})

    def test_json_fallback_uses_the_original_remaining_budget(self):
        jobs = []
        def run(job):
            jobs.append(job)
            if len(jobs) == 1:
                self.now = 7.3
                raise AgentError("synthetic connection failure")
            return self.answer()
        with mock.patch("time.monotonic", side_effect=lambda: self.now), mock.patch.object(self.voice, "run", side_effect=run):
            self.assertEqual("answer", self.converse().reply)
        self.assertEqual(2, len(jobs))
        self.assertEqual(10, jobs[0].timeout)
        self.assertAlmostEqual(2.7, jobs[1].timeout)
        self.assertIs(self.cut, jobs[1].interrupt)

    def test_exhausted_tool_attempt_does_not_start_json_fallback(self):
        def run(job):
            self.now = 10.1
            raise AgentError("synthetic timeout")
        with mock.patch("time.monotonic", side_effect=lambda: self.now), mock.patch.object(self.voice, "run", side_effect=run) as provider:
            with self.assertRaises(AgentError): self.converse()
        self.assertEqual(1, provider.call_count)

    def test_interruption_cannot_start_another_attempt(self):
        def run(job):
            self.cut.set()
            raise AgentError("synthetic interrupted connection")
        with mock.patch.object(self.voice, "run", side_effect=run) as provider:
            with self.assertRaises(AgentError): self.converse()
        self.assertEqual(1, provider.call_count)

    def test_format_retry_keeps_the_remaining_budget_and_interrupt(self):
        jobs = []
        def run(job):
            jobs.append(job)
            if len(jobs) == 1:
                self.now = 4.0
                return Outcome("invalid action", "session", {"reply": "invalid action",
                    "action": {"type": "start", "agent": "missing", "task": "test"}})
            return self.answer()
        with mock.patch("time.monotonic", side_effect=lambda: self.now), mock.patch.object(self.voice, "run", side_effect=run):
            self.assertEqual("answer", self.converse().reply)
        self.assertEqual(2, len(jobs))
        self.assertEqual(6, jobs[1].timeout)
        self.assertIs(self.cut, jobs[1].interrupt)

    def test_context_preparation_consumes_the_same_budget_before_provider_start(self):
        window = self.x.context.window
        def prepare(*args, **kwargs):
            result = window(*args, **kwargs)
            self.now = 10.1
            return result
        with mock.patch("time.monotonic", side_effect=lambda: self.now), mock.patch.object(self.x.context, "window", side_effect=prepare), mock.patch.object(self.voice, "run", return_value=self.answer()) as provider:
            with self.assertRaises(AgentError): self.converse()
        provider.assert_not_called()

    def test_late_output_does_not_save_session_or_context_receipts(self):
        def run(job):
            self.now = 10.1
            return self.answer()
        with mock.patch("time.monotonic", side_effect=lambda: self.now), mock.patch.object(self.voice, "run", side_effect=run) as provider:
            with self.assertRaises(AgentError): self.converse()
        self.assertEqual(1, provider.call_count)
        self.assertIsNone(self.store.session("side", self.voice.name))

    def test_completed_or_failed_phase_closes_text_and_tool_callbacks(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                captured, shown = [], []
                def run(job):
                    captured.append(job)
                    if failure: raise AgentError("synthetic failure")
                    return self.answer()
                tools = self.x._toolbox("side", None, [self.voice], False)
                with mock.patch.object(self.voice, "run", side_effect=run):
                    try:
                        self.x._speak(self.voice, "question", "side", "system", None, 0,
                                      catch_up=True, on_text=shown.append, tools=tools)
                    except AgentError:
                        self.assertTrue(failure)
                with self.assertRaises(AgentError): captured[0].on_text("obsolete text")
                with self.assertRaises(AgentError):
                    captured[0].tools.call("remember", {"note": "obsolete preference", "kind": "偏好"})
                self.assertEqual([], self.x.memory.notes())
                self.assertEqual([], shown)

    def test_expiry_at_the_effect_boundary_refuses_the_memory_write(self):
        def before_effect():
            self.now = 10.1
        tools = self.x._toolbox("side", None, [self.voice], False)
        tools._before_effect = before_effect
        def run(job):
            job.tools.call("remember", {"note": "obsolete preference", "kind": "偏好"})
            return self.answer()
        with mock.patch("time.monotonic", side_effect=lambda: self.now), mock.patch.object(self.voice, "run", side_effect=run):
            with self.assertRaises(AgentError):
                self.x._speak(self.voice, "question", "side", "system", None, 0,
                              catch_up=True, tools=tools, budget=Deadline(10))
        self.assertEqual([], self.x.memory.notes())

    def test_interrupted_provider_cannot_start_a_new_tool(self):
        tools = self.x._toolbox("side", None, [self.voice], False)
        def run(job):
            self.cut.set()
            job.tools.call("remember", {"note": "obsolete preference", "kind": "偏好"})
            return self.answer()
        with mock.patch.object(self.voice, "run", side_effect=run):
            with self.assertRaises(AgentError):
                self.x._speak(self.voice, "question", "side", "system", None, 0,
                              catch_up=True, tools=tools, interrupt=self.cut)
        self.assertEqual([], self.x.memory.notes())

    def test_saved_mutation_survives_expiry_without_another_model_or_operation(self):
        saved = []
        scope = Effects("turn", [], lambda records: saved.append(records))
        def run(job):
            job.tools.call("remember", {"note": "owner prefers short replies", "kind": "偏好"})
            self.now = 10.1
            return self.answer()
        with mock.patch("time.monotonic", side_effect=lambda: self.now), mock.patch.object(self.voice, "run", side_effect=run) as provider, self.x.effects(scope):
            result = self.converse()
        self.assertEqual(1, provider.call_count)
        self.assertEqual(1, len(self.x.memory.notes()))
        self.assertEqual(["succeeded"], [record["state"] for record in scope.records])
        self.assertEqual(["remember"], [record["name"] for record in saved[-1]])
        self.assertIn("回复中断", result.reply)
        self.assertIsNone(self.store.session("side", self.voice.name))


if __name__ == "__main__":
    unittest.main()
