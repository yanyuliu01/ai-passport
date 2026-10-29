"""Derived segment summaries never replace history, receipts or current facts."""

import json
import time
import unittest
from unittest import mock

from test_runtime import GateTalker, TempDirCase, make_xiaoyu
from xiaoyu_runtime.helpers.base import Outcome
from xiaoyu_runtime.loop.context import bind
from xiaoyu_runtime.world.store import Store
from xiaoyu_runtime.world.cards import Cards
from xiaoyu_runtime.events.intake import Intake
from xiaoyu_runtime.api.service import Service


class ContextSummaryTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.state = self.folder / "state"
        self.cards = Cards(self.state / "cards.json")
        self.writer = Intake.for_cards(self.cards).writer("owner", actor="test")
        self.context = bind(Store(self.state), self.cards)
        self.card = self.writer.task_open("side", "original task")

    def exchange(self, tid, text="question", reply="saved answer"):
        self.writer.exchange(self.card["id"], text, reply, turn_id=tid)

    def seed(self):
        for index in range(65):
            self.exchange("s%02d" % index, text="historical question %d" % index)
        return self.context.summary_candidate("side", now=time.time() + 3600)

    @staticmethod
    def response(candidate, text="The owner chose the original plan."):
        return json.dumps({"points": [{"text": text, "turns": [candidate["turns"][0]["id"]]}]})

    def save(self, candidate):
        self.context.begin_summary("side", candidate, now=1000)
        return self.context.save_summary("side", candidate, self.response(candidate))

    def test_summary_reservation_reuses_identical_evidence_search(self):
        from xiaoyu_runtime.loop import recall
        for index in range(97):
            self.exchange("s%02d" % index, text="quartz marker" if index == 0 else "question %d" % index)
        candidate = self.context.summary_candidate("side", now=time.time() + 3600)
        self.assertTrue(self.save(candidate))
        with mock.patch.object(recall, "search", wraps=recall.search) as search:
            window = self.context.window("side", "reader", seed=8, query="quartz")
        self.assertIn("quartz marker", window["body"])
        self.assertIn("The owner chose", window["body"])
        self.assertEqual(1, search.call_count)

    def test_search_reuse_preserves_quotes_receipts_and_budget_selection(self):
        from xiaoyu_runtime.loop import context_window
        for index in range(97):
            self.exchange("s%02d" % index,
                          text=("quartz" if index == 0 else "topaz" if index == 1 else "question"),
                          reply="saved answer " * (20 if index >= 85 else 1))
        self.assertTrue(self.save(self.context.summary_candidate("side", now=time.time() + 3600)))
        def uncached(query, turns):
            return lambda candidates: context_window._lookup(query, turns, candidates)
        changed_candidates = False
        for seed in (None, 8):
            for budget in (700, 1000, 2000, 4000, 8000):
                for query in (None, "quartz", "topaz", "question", "absent"):
                    with self.subTest(seed=seed, budget=budget, query=query):
                        actual = self.context.window("side", "reader", seed=seed, max_chars=budget, query=query)
                        with mock.patch.object(context_window, "prepare_lookup", side_effect=uncached), \
                                mock.patch.object(context_window, "_lookup", wraps=context_window._lookup) as lookup:
                            expected = self.context.window("side", "reader", seed=seed, max_chars=budget, query=query)
                        self.assertEqual(expected, actual)
                        self.assertLessEqual(len(actual["body"]), budget)
                        if len(lookup.call_args_list) == 2:
                            first, second = lookup.call_args_list
                            changed_candidates |= tuple(first.args[2]) != tuple(second.args[2])
        self.assertTrue(changed_candidates, "fixture must exercise changed candidate membership")
        self.exchange("s00", text="amethyst replacement")
        revised = self.context.window("side", "reader", seed=8, query="amethyst")
        self.assertIn("amethyst replacement", revised["body"])
        self.assertNotIn("quartz", revised["body"])
        self.context.clear("side")
        self.assertEqual("", self.context.window("side", "reader", query="amethyst")["body"])

    def test_lookup_recomputes_when_candidate_order_changes(self):
        from xiaoyu_runtime.loop import context_window
        turns = [{"_key": "a", "text": "quartz", "reply": ""},
                 {"_key": "b", "text": "quartz", "reply": ""}]
        lookup = context_window.prepare_lookup("quartz", turns)
        forward = {turn["_key"]: turn for turn in turns}
        reverse = dict(reversed(list(forward.items())))
        with mock.patch.object(context_window, "_lookup", return_value=("quartz", [])) as search:
            lookup(forward)
            lookup(forward)
            lookup(reverse)
        self.assertEqual(2, search.call_count)

    def test_summary_keeps_originals_and_only_quotes_receive_delivery_receipts(self):
        candidate = self.seed()
        self.assertTrue(self.save(candidate))
        window = self.context.window("side", "new-reader", seed=8)
        self.assertIn("The owner chose the original plan.", window["body"])
        self.assertIn(candidate["id"], window["body"])
        summarized_id = candidate["turns"][0]["id"]
        self.assertIn(summarized_id, window["body"])
        self.assertGreater(window["coverage"]["summarized"], 0)
        self.context.mark("side", "new-reader", window["receipts"])
        self.assertIn(summarized_id, [turn["id"] for turn in self.context.unseen("side", "new-reader")])
        self.assertEqual(65, len(self.context.history("side")))
        self.assertIn("saved answer", self.context.recall("side", "historical"))
        self.assertNotIn("The owner chose", (self.state / "context.json").read_text())

    def test_summary_does_not_leak_to_another_conversation_and_survives_restart(self):
        self.save(self.seed())
        restored = bind(Store(self.state), self.cards)
        self.assertIn("The owner chose", restored.window("side", "reader", seed=8)["body"])
        self.assertEqual("", restored.window("elsewhere", "reader")["body"])

    def test_revision_and_reset_reject_stale_summary_and_model_output(self):
        candidate = self.seed()
        self.save(candidate)
        self.exchange(candidate["turns"][0]["id"], text="revised source")
        self.assertFalse(self.context.save_summary("side", candidate, self.response(candidate)))
        self.assertNotIn("The owner chose", self.context.window("side", "reader", seed=8)["body"])
        candidate = self.context.summary_candidate("side", now=time.time() + 3600)
        self.assertTrue(self.save(candidate))
        self.context.clear("side")
        self.assertFalse(self.context.save_summary("side", candidate, self.response(candidate)))
        self.assertEqual("", self.context.window("side", "reader")["body"])

    def test_invalid_citations_oversize_and_malformed_results_are_rejected(self):
        candidate = self.seed()
        bad = ["not json", "[]", "[" * 1500 + "]" * 1500, '{"points": []}',
               json.dumps({"points": [{"text": "invented", "turns": ["other-turn"]}]}),
               self.response(candidate, "x" * 1001)]
        for result in bad:
            with self.subTest(result=result[:40]):
                self.assertFalse(self.context.save_summary("side", candidate, result))
        self.assertNotIn("invented", self.context.window("side", "reader", seed=8)["body"])

    def test_budgets_are_preserved_and_cached_summaries_never_call_a_model(self):
        self.save(self.seed())
        for budget in (256, 512, 1000, 2000, 8000):
            for query in (None, "historical question"):
                window = self.context.window("side", "reader", max_chars=budget, query=query)
                self.assertLessEqual(len(window["body"]), budget)

    def test_attempts_are_durable_bounded_and_throttled(self):
        candidate = self.seed()
        for now in (1000, 2000, 3000):
            self.assertTrue(self.context.begin_summary("side", candidate, now=now))
            self.assertFalse(self.context.begin_summary("side", candidate, now=now + 1))
            self.context = bind(Store(self.state), self.cards)
        self.assertFalse(self.context.begin_summary("side", candidate, now=10000))

    def test_failed_cache_write_does_not_create_a_claim_or_hide_history(self):
        candidate = self.seed()
        with mock.patch("xiaoyu_runtime.loop.context_summaries.durable_write", side_effect=OSError("disk full")):
            self.assertFalse(self.context.begin_summary("side", candidate, now=1000))
        self.assertTrue(self.context.begin_summary("side", candidate, now=1000))
        self.assertEqual(65, len(self.context.history("side")))

    def test_oversized_sources_and_recent_or_partial_segments_are_not_summarized(self):
        for index in range(40):
            self.exchange("long%02d" % index, reply="x" * 2000)
        self.assertIsNone(self.context.summary_candidate("side", now=time.time() + 3600))

    def test_appending_new_turns_keeps_a_closed_summary_version_valid(self):
        candidate = self.seed()
        self.save(candidate)
        self.exchange("new", text="new request")
        self.assertIn("The owner chose", self.context.window("side", "reader", seed=8)["body"])

    def test_cited_original_is_readable_by_version_and_never_from_another_conversation(self):
        candidate = self.seed()
        self.save(candidate)
        turn = candidate["turns"][0]
        query = "source:" + json.dumps({"segment": candidate["id"], "turn": turn["id"]})
        original = json.loads(self.context.recall("side", query))
        self.assertIn(turn["text"], original["text"])
        self.assertIn(turn["reply"], original["text"])
        self.assertFalse(original["excerpt"])
        self.assertIsNone(original["next"])
        self.assertNotIn(turn["text"], self.context.recall("elsewhere", query))
        self.exchange(turn["id"], text="changed source")
        self.assertNotIn(turn["text"], self.context.recall("side", query))
        self.assertNotIn("changed source", self.context.recall("side", query))

    def test_cited_original_pages_are_bounded_complete_and_reset_aware(self):
        self.exchange("long", text="original question", reply="abcdefghij" * 600)
        segment = self.context.segments("side")[0]
        query = "source:" + json.dumps({"segment": segment["id"], "turn": "long"})
        first = query
        chunks = []
        for index in range(20):
            result = self.context.recall("side", query)
            self.assertLessEqual(len(result), 4096)
            page = json.loads(result)
            chunks.append(page["text"])
            if page["next"] is None:
                break
            query = page["next"]
        self.assertEqual("主人：original question\n小鱼：" + "abcdefghij" * 600, "".join(chunks))
        self.context.clear("side")
        self.assertNotIn("original question", self.context.recall("side", first))

    def test_malformed_source_references_do_not_fall_through_to_keyword_search(self):
        self.seed()
        for value in ("source:bad", "source:[]", 'source:{"segment": 1}',
                      'source:{"segment":"x","turn":"s00","offset":-1}'):
            self.assertIn("原文引用不可用", self.context.recall("side", value))

    def test_corrupt_optional_cache_never_loses_originals_or_reset_boundaries(self):
        candidate = self.seed()
        self.save(candidate)
        self.context.clear("side")
        (self.state / "context-summaries.json").write_text('{"version": 1, "entries": []}')
        restored = bind(Store(self.state), self.cards)
        self.assertEqual([], restored.history("side"))
        self.assertEqual("", restored.window("side", "reader")["body"])
        self.assertIsNotNone(self.cards.message(candidate["turns"][0]["_key"]))

    def test_extreme_or_deep_cache_corruption_is_discarded_without_blocking_history(self):
        self.save(self.seed())
        path = self.state / "context-summaries.json"
        state = json.loads(path.read_text())
        next(iter(state["entries"].values()))["at"] = 10 ** 400
        for raw in (json.dumps(state), "[" * 1500 + "]" * 1500):
            path.write_text(raw)
            restored = bind(Store(self.state), self.cards)
            self.assertEqual(65, len(restored.history("side")))
            self.assertNotIn("The owner chose", restored.window("side", "reader", seed=8)["body"])


class SummaryWakeTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.voice = GateTalker()
        self.x, self.store = make_xiaoyu(self.folder, [self.voice])
        self.addCleanup(self.x.close)
        card = self.x._writer.task_open("default", "history")
        for index in range(65):
            self.x._writer.exchange(card["id"], "old request %d" % index, "saved response", turn_id="s%02d" % index)
        self.now = time.time() + 3600

    def result(self, candidate, **unused):
        return Outcome(ContextSummaryTests.response(candidate), "must-not-replace-native-session")

    def test_idle_summary_is_ephemeral_tool_free_and_not_a_chat_message(self):
        candidate = self.x.context.summary_candidate("default", now=self.now)
        with mock.patch.object(self.x, "_speak", return_value=self.result(candidate)) as speak:
            self.x._pulse._maybe_summary(self.now)
        self.assertEqual(1, speak.call_count)
        self.assertTrue(speak.call_args.kwargs["ephemeral"])
        self.assertIsNone(speak.call_args.kwargs.get("tools"))
        self.assertFalse(speak.call_args.args[6])
        self.assertIsNone(self.store.session("default", self.voice.name))
        self.assertEqual(65, len(self.x.context.history("default")))
        self.assertIn("The owner chose", self.x.context.window("default", "reader", seed=8)["body"])

    def test_active_chat_shutdown_interruption_and_changed_owner_cannot_save_a_summary(self):
        for cause in ("active", "stop", "cut", "owner"):
            with self.subTest(cause=cause):
                candidate = self.x.context.summary_candidate("default", now=self.now)
                def run(*args, **kwargs):
                    if cause == "stop": self.x._pulse.close()
                    if cause == "cut": self.x.interrupt("default")
                    if cause == "owner": self.x._pulse._owner = lambda conversation: ("someone-else", 1)
                    return self.result(candidate)
                wall = time.time() if cause == "active" else self.now
                with mock.patch.object(self.x, "_speak", side_effect=run) as speak:
                    self.x._pulse._maybe_summary(wall)
                if cause == "active": speak.assert_not_called()
                self.assertNotIn("The owner chose", self.x.context.window("default", "reader", seed=8)["body"])
                self.x._pulse._stop.clear()
                self.x._pulse._owner = lambda conversation: ("local", 0)
                self.now += 1000

    def test_accepted_message_prevents_summary_even_before_its_reply_enters_history(self):
        service = Service(self.x, self.store)
        self.addCleanup(service.close)
        with mock.patch.object(service, "_line_up"):
            service.submit("new accepted message", client_id="accepted-without-reply")
        self.assertEqual(65, len(self.x.context.history("default")))
        with mock.patch.object(self.x, "_speak") as speak:
            self.x._pulse._maybe_summary(self.now)
        speak.assert_not_called()

    def test_new_input_during_summary_closes_its_phase_without_replacing_chat_session(self):
        service = Service(self.x, self.store)
        self.addCleanup(service.close)
        candidate = self.x.context.summary_candidate("default", now=self.now)
        def run(job):
            with mock.patch.object(service, "_line_up"):
                service.submit("new request", client_id="arrived-during-summary")
            return self.result(candidate)
        with mock.patch.object(self.voice, "run", side_effect=run):
            self.x._pulse._maybe_summary(self.now)
        self.assertIsNone(self.store.session("default", self.voice.name))
        self.assertNotIn("The owner chose", self.x.context.window("default", "reader", seed=8)["body"])

    def test_summary_background_does_not_accumulate_as_native_chat_messages(self):
        candidate = self.x.context.summary_candidate("default", now=self.now)
        self.x.context.begin_summary("default", candidate, now=self.now)
        self.x.context.save_summary("default", candidate, ContextSummaryTests.response(candidate))
        self.x.context.mark("default", self.voice.name, self.x.context.history("default"))
        self.store.remember("default", self.voice.name, "apt-existing")
        tools = self.x._toolbox("default", None, [self.voice], False)
        with mock.patch.object(self.voice, "run", return_value=Outcome("answer", "apt-existing")) as run:
            for _ in range(3):
                self.x._speak(self.voice, "current request", "default", "original rules", None, 0,
                              catch_up=True, tools=tools)
        self.assertEqual(3, run.call_count)
        for call in run.call_args_list:
            job = call.args[0]
            self.assertIn("The owner chose", job.system)
            self.assertIn(candidate["id"], job.system)
            self.assertEqual("current request", job.text)


if __name__ == "__main__":
    unittest.main()
