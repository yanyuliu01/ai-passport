"""Speaking preserves session/context receipts and rejects stale model output."""

from types import SimpleNamespace
import unittest
from unittest import mock

from test_runtime import Scripted, TempDirCase, make_xiaoyou
from xiaoyou_runtime.helpers.base import Outcome
from xiaoyou_runtime.errors import AgentError
from xiaoyou_runtime.loop.effects import EffectFenceError


class SpeakerTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.voice = Scripted("voice", speaks=True)
        self.x, self.store = make_xiaoyou(self.folder, [self.voice])
        self.addCleanup(self.x.close)
        card = self.x.record.task_open("side", "saved context")
        self.x.record.exchange(card["id"], "saved question", "saved answer", turn_id="old")

    def speak(self, **options):
        return self.x._speak(self.voice, "new question", "side", "system", None, 0,
                             catch_up=True, **options)

    def test_completed_speech_saves_native_session_and_only_quoted_receipts(self):
        with mock.patch.object(self.voice, "run", return_value=Outcome("answer", "new-session")) as provider:
            self.speak(timeout_seconds=.75)
        job = provider.call_args.args[0]
        self.assertIn("saved question", job.text)
        self.assertEqual(.75, job.timeout)
        self.assertEqual("side", job.conversation)
        self.assertEqual("new-session", self.store.session("side", "voice"))
        self.assertEqual([], self.x.context.unseen("side", "voice"))

    def test_current_question_selects_old_evidence_without_an_extra_provider_call(self):
        card = self.x.record.task_open("side", "older plan")
        self.x.record.exchange(card["id"], "quartz-anchor", "original code ORCHID-472", turn_id="anchor")
        for index in range(12):
            self.x.record.exchange(card["id"], "unrelated %d" % index, "recent answer", turn_id="recent-%d" % index)
        with mock.patch.object(self.voice, "run", return_value=Outcome("ORCHID-472", "new-session")) as provider:
            self.x._speak(self.voice, "quartz-anchor 的原始代号？", "side", "system", None, 0,
                          catch_up=True, tools=object())
        self.assertEqual(1, provider.call_count)
        self.assertIn("original code ORCHID-472", provider.call_args.args[0].text)
        self.assertIn("unrelated 11", provider.call_args.args[0].text)

    def test_provider_failure_does_not_save_session_or_receipts(self):
        with mock.patch.object(self.voice, "run", side_effect=AgentError("provider unavailable")), \
                mock.patch.object(self.store, "remember") as remember:
            with self.assertRaises(AgentError): self.speak()
        remember.assert_not_called()
        self.assertEqual(1, len(self.x.context.unseen("side", "voice")))

    def test_owner_guard_fences_admission_and_returned_session_receipts(self):
        for before in (True, False):
            with self.subTest(before=before):
                allowed = [not before]
                def guard():
                    if not allowed[0]: raise EffectFenceError("owner changed")
                def run(job):
                    allowed[0] = False
                    return Outcome("late answer", "late-session")
                with self.x.effects(SimpleNamespace(guard=guard)), \
                        mock.patch.object(self.voice, "run", side_effect=run) as provider, \
                        mock.patch.object(self.store, "remember") as remember:
                    with self.assertRaises(EffectFenceError): self.speak()
                self.assertEqual(not before, bool(provider.call_count))
                remember.assert_not_called()
                self.assertEqual(1, len(self.x.context.unseen("side", "voice")))

    def test_ephemeral_calls_do_not_read_or_replace_shared_native_context(self):
        self.store.remember("side", "voice", "original-session")
        with mock.patch.object(self.voice, "run", return_value=Outcome("answer", "temporary-session")) as provider, \
                mock.patch.object(self.x.context, "window") as window:
            self.speak(ephemeral=True, tools=object())
        window.assert_not_called()
        self.assertIsNone(provider.call_args.args[0].session_id)
        self.assertEqual("new question", provider.call_args.args[0].text)
        self.assertEqual("original-session", self.store.session("side", "voice"))
        self.assertEqual(1, len(self.x.context.unseen("side", "voice")))

    def test_decision_call_keeps_speaking_history_unread_and_preserves_inputs(self):
        tools, interrupt, messages = object(), object(), [{"role": "user", "content": "saved facts"}]
        with mock.patch.object(self.voice, "run", return_value=Outcome("", None)) as provider:
            self.speak(decision_only=True, tools=tools, interrupt=interrupt, decision_messages=messages)
        job = provider.call_args.args[0]
        self.assertTrue(job.decision_only)
        self.assertIs(tools, job.tools)
        self.assertIs(interrupt, job.interrupt)
        self.assertIs(messages, job.decision_messages)
        self.assertEqual(1, len(self.x.context.unseen("side", "voice")))

    def test_late_stream_callback_still_checks_original_guard(self):
        allowed, callbacks, shown = [True], [], []
        def guard():
            if not allowed[0]: raise EffectFenceError("wake ended")
        def run(job):
            callbacks.append(job.on_text)
            job.on_text("valid text")
            return Outcome("answer", None)
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.speak(on_text=shown.append, guard=guard)
        allowed[0] = False
        with self.assertRaises(EffectFenceError): callbacks[0]("late text")
        self.assertEqual(["valid text"], shown)

    def test_streamer_retains_throttle_retraction_and_complete_text_replacement(self):
        shown = []
        stream = self.x._streamer("voice", lambda kind, agent, text: shown.append((kind, agent, text)))
        with mock.patch("xiaoyou_runtime.loop.xiaoyou.time.monotonic", side_effect=[0, .01, .2]):
            stream('{"reply":"first"}')
            stream("")
            stream('{"reply":"too soon"}')
            stream('{"reply":"second"}')
        self.assertEqual([("partial", "voice", "first"), ("partial", "voice", ""),
                          ("partial", "voice", "second")], shown)


if __name__ == "__main__":
    unittest.main()
