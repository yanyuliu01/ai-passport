"""Saved effect claims/results survive provider, journal and process failures."""

import json
import threading
import unittest
from dataclasses import replace
from copy import deepcopy
from unittest import mock

from test_runtime import Scripted, ToolTalker, TempDirCase, make_xiaoyou, start, until
from xiaoyou_runtime.agents import AgentError
from xiaoyou_runtime.effects import Effects, EffectFenceError, EffectUncertain
from xiaoyou_runtime.ledger import current
from xiaoyou_runtime.service import Service


class EffectBoundaryTests(unittest.TestCase):
    def test_pending_and_running_are_saved_before_the_effect_and_result_after(self):
        saved = []
        scope = Effects("request", [], lambda records: saved.append(deepcopy(records)))
        def apply():
            self.assertEqual(["pending", "running"], [items[-1]["state"] for items in saved])
            return ("saved", False)
        self.assertEqual(("saved", False), scope.run("remember", {"note": "n"}, apply))
        self.assertEqual("succeeded", saved[-1][0]["state"])

    def test_a_failed_pending_or_claim_write_never_invokes_the_effect(self):
        for failed_state in ("pending", "running"):
            invoked = []
            def checkpoint(records):
                if records[-1]["state"] == failed_state:
                    raise OSError("disk full")
            scope = Effects("r", [], checkpoint)
            with self.assertRaises(OSError):
                scope.run("remember", {}, lambda: invoked.append(True))
            self.assertEqual([], invoked)

    def test_uncertain_effect_cannot_be_run_again(self):
        saved = []
        scope = Effects("r", [], lambda records: saved.append(deepcopy(records)))
        with self.assertRaises(EffectUncertain):
            scope.run("forget", {"number": 1}, lambda: (_ for _ in ()).throw(OSError("after effect")))
        restarted = Effects("r", saved[-1], lambda records: None)
        with self.assertRaises(EffectUncertain):
            restarted.run("forget", {"number": 1}, lambda: self.fail("no replay"))

    def test_saved_result_is_returned_without_invocation(self):
        scope = Effects("r", [], lambda records: None)
        scope.run("track", {"title": "a"}, lambda: ("saved commitment", False))
        restored = Effects("r", scope.records, lambda records: self.fail("no new write"))
        self.assertEqual(["saved commitment", False], restored.run("track", {"title": "a"},
                         lambda: self.fail("no invocation")))

    def test_an_uncertain_effect_blocks_further_mutations_in_the_same_turn(self):
        scope = Effects("r", [], lambda records: None)
        with self.assertRaises(EffectUncertain):
            scope.run("forget", {"number": 1}, lambda: (_ for _ in ()).throw(OSError("unknown")))
        with self.assertRaises(EffectUncertain):
            scope.run("forget", {"number": 1}, lambda: self.fail("no next mutation"))
        self.assertEqual(1, len(scope.records))


class RequestEffectsTests(TempDirCase):
    def runtime(self, voice=None, **settings):
        voice = voice or ToolTalker()
        helper = Scripted("codex", speaks=False)
        x, store = make_xiaoyou(self.folder, [voice, helper], **settings)
        server = Service(x, store)
        self.addCleanup(server.close)
        self.addCleanup(helper.gate.set)
        return server, x, voice, helper, store

    def stage(self, server, text="original request"):
        with mock.patch.object(server, "_line_up", return_value=True):
            sent = server.submit(text, conversation="side-work")
        server._update(sent["id"], strict=True, status="running")
        return sent["id"]

    def disk(self, store, mid):
        return next(message for message in current(store.state_dir / "messages.json")["messages"] if message["id"] == mid)

    def test_lost_model_response_after_memory_effect_does_not_fall_back_or_repeat(self):
        server, x, voice, _, store = self.runtime()
        calls = []
        def run(job):
            calls.append(job)
            self.assertFalse(job.tools.call("remember", {"note": "keep replies short", "kind": "偏好"})[1])
            raise AgentError("provider response lost")
        with mock.patch.object(voice, "run", side_effect=run):
            done = server.get(server.submit("remember this")["id"], wait=5)
        self.assertEqual("done", done["status"])
        self.assertEqual(1, len(calls))
        self.assertEqual(["keep replies short"], x.memory.texts())
        self.assertIn("记下了", done["reply"])
        self.assertEqual([], x.context.turns("default")[-1]["seen"])
        self.assertEqual("succeeded", self.disk(store, done["id"])["_effects"][0]["state"])

    def test_successful_effect_payloads_never_enter_client_projections(self):
        server, _, voice, _, store = self.runtime()
        voice.calls = [("track", {"title": "private selection"})]
        done = server.get(server.submit("save commitment")["id"], wait=5)
        self.assertIn("_effects", self.disk(store, done["id"]))
        for value in (done, server.feed("default", messages=True), server.sync("default")):
            text = json.dumps(value)
            self.assertNotIn('"_effects"', text)
            self.assertNotIn('"_effect_owner"', text)

    def test_two_claimants_cannot_invoke_the_same_effect_twice(self):
        server, _, _, _, _ = self.runtime()
        mid = self.stage(server)
        scopes = [server._effects_for(mid), server._effects_for(mid)]
        invocations = []
        def apply():
            invocations.append(True)
            with self.assertRaises(EffectUncertain):
                scopes[1].run("track", {"title": "one"}, lambda: invocations.append(False))
            return ("saved", False)
        scopes[0].run("track", {"title": "one"}, apply)
        self.assertEqual([True], invocations)

    def test_pending_memory_input_resumes_after_restart_without_a_model(self):
        server, x, _, _, store = self.runtime()
        mid = self.stage(server)
        scope = server._effects_for(mid)
        checkpoint = scope._checkpoint
        def fail_claim(records):
            if records[-1]["state"] == "running":
                raise OSError("claim unavailable")
            checkpoint(records)
        scope._checkpoint = fail_claim
        with self.assertRaises(OSError):
            scope.run("remember", {"note": "saved pending note"}, lambda: self.fail("no effect"))
        server.close()
        again, restored, voice, _, _ = self.runtime()
        done = again.get(mid, wait=5)
        self.assertEqual("done", done["status"])
        self.assertEqual(["saved pending note"], restored.memory.texts())
        self.assertEqual([], voice.jobs + voice.tool_jobs)
        self.assertEqual("side-work", again.card_store.get(done["card"])["conversation"])
        self.assertEqual("succeeded", self.disk(store, mid)["_effects"][0]["state"])

    def test_saved_memory_result_is_delivered_after_restart_without_invoking_it_again(self):
        server, x, _, _, _ = self.runtime()
        mid = self.stage(server)
        scope = server._effects_for(mid)
        scope.run("remember", {"note": "only once"}, lambda: (x.memory.remember("only once"), False))
        server.close()
        again, restored, voice, _, _ = self.runtime()
        self.assertEqual("done", again.get(mid, wait=5)["status"])
        self.assertEqual(["only once"], restored.memory.texts())
        self.assertEqual([], voice.jobs + voice.tool_jobs)

    def test_lost_effect_result_stays_unknown_after_restart_and_blocks_handoff(self):
        server, x, _, _, _ = self.runtime()
        mid = self.stage(server)
        scope = server._effects_for(mid)
        def apply():
            x.memory.remember("may have happened")
            raise OSError("after writing memory")
        with self.assertRaises(EffectUncertain):
            scope.run("remember", {"note": "may have happened"}, apply)
        server.close()
        again, restored, voice, _, _ = self.runtime()
        self.assertEqual("unknown", again.get(mid)["operation_state"])
        self.assertEqual([], voice.jobs + voice.tool_jobs)
        self.assertEqual(["may have happened"], restored.memory.texts())
        with again._changed:
            self.assertIn("操作结果待核对", again._busy_reason("side-work"))

    def test_a_changed_owner_generation_stops_effects_before_invocation(self):
        server, _, _, _, _ = self.runtime()
        mid = self.stage(server)
        with mock.patch.object(server, "_effect_owner", return_value={"owner": server.runtime_id, "generation": 1}):
            scope = server._effects_for(mid)
        with mock.patch.object(server, "_effect_owner", return_value={"owner": server.runtime_id, "generation": 3}):
            with self.assertRaises(EffectFenceError):
                scope.run("track", {"title": "never"}, lambda: self.fail("stale owner"))

    def test_memory_secrets_are_rejected_before_being_saved_as_an_effect(self):
        server, x, _, _, store = self.runtime()
        mid = self.stage(server)
        with x.effects(server._effects_for(mid)):
            tools = x._toolbox("side-work", None, [], False)
            result = tools.call("remember", {"note": "password: secret-example-value"})
        self.assertTrue(result[1])
        self.assertNotIn("_effects", self.disk(store, mid))

    def test_task_dispatch_has_a_saved_decision_claim_before_the_worker_runs(self):
        server, _, voice, helper, store = self.runtime()
        observed = []
        original = helper.run
        def run(job):
            messages = current(store.state_dir / "messages.json")["messages"]
            observed.extend(effect["state"] for message in messages for effect in message.get("_effects", [])
                            if effect["name"] == "task_action")
            return original(job)
        voice.calls = [("start_task", {"agent": "codex", "title": "inspect", "task": "inspect"})]
        with mock.patch.object(helper, "run", side_effect=run):
            done = server.get(server.submit("inspect")["id"], wait=5)
            server._xiaoyou.settle(done["card"], 5)
        self.assertEqual(1, len(helper.jobs))
        self.assertEqual(1, len(observed))
        self.assertIn(observed[0], ("running", "succeeded"))
        self.assertEqual("succeeded", self.disk(store, done["id"])["_effects"][0]["state"])

    def pending_action(self, server, x, voice, helper, mid):
        scope = server._effects_for(mid)
        checkpoint = scope._checkpoint
        def fail_claim(records):
            if records[-1]["state"] == "running":
                raise OSError("claim unavailable")
            checkpoint(records)
        scope._checkpoint = fail_claim
        with x.effects(scope), self.assertRaises(OSError):
            x._apply_conversation(voice, "inspect original", "side-work", mid,
                {"type": "start", "card": None, "agent": helper.name, "title": "inspect", "task": "original task"},
                "检查已经完成", None, "busy", 0, lambda *args: None, False, None, None)

    def test_pending_task_decision_resumes_exact_input_without_reasking_a_model(self):
        server, x, voice, helper, store = self.runtime()
        mid = self.stage(server, "inspect original")
        self.pending_action(server, x, voice, helper, mid)
        self.assertEqual([], helper.jobs)
        server.close()
        again, restored, other_voice, other_helper, _ = self.runtime()
        done = again.get(mid, wait=5)
        self.assertEqual("done", done["status"])
        self.assertNotIn("检查已经完成", done["reply"])
        restored.settle(done["card"], 5)
        self.assertEqual(["original task"], [job.text for job in other_helper.jobs])
        self.assertEqual([], other_voice.tool_jobs)
        self.assertEqual("succeeded", self.disk(store, mid)["_effects"][0]["state"])

    def test_changed_helper_configuration_cannot_execute_a_saved_pending_decision(self):
        server, x, voice, helper, _ = self.runtime()
        mid = self.stage(server)
        self.pending_action(server, x, voice, helper, mid)
        server.close()
        changed_voice = ToolTalker()
        changed_voice.spec = replace(changed_voice.spec, description="changed configuration")
        again, _, _, other_helper, _ = self.runtime(changed_voice)
        done = again.get(mid, wait=5)
        self.assertEqual("unknown", done["operation_state"])
        self.assertEqual([], other_helper.jobs)
        self.assertEqual([], changed_voice.tool_jobs)

    def test_a_transient_claim_failure_can_resume_on_the_same_runtime_without_a_model_retry(self):
        server, _, voice, _, store = self.runtime()
        voice.calls = [("remember", {"note": "recover locally"})]
        original = server._ledger.save
        def fail_claim(messages, dedupe, seq):
            if any(effect["state"] == "running" for message in messages.values() for effect in message.get("_effects", [])):
                raise OSError("temporary disk failure")
            return original(messages, dedupe, seq)
        with mock.patch.object(server._ledger, "save", side_effect=fail_claim):
            sent = server.submit("save one note")
            self.assertTrue(until(lambda: not server._busy))
        self.assertEqual("pending", self.disk(store, sent["id"])["_effects"][0]["state"])
        server.retry_effects()
        done = server.get(sent["id"], wait=5)
        self.assertEqual("done", done["status"])
        self.assertEqual(["recover locally"], server._xiaoyou.memory.texts())
        self.assertEqual(1, len(voice.tool_jobs))

    def test_a_post_commit_receipt_failure_does_not_retract_the_effect_result(self):
        server, x, _, _, store = self.runtime()
        mid = self.stage(server)
        scope = server._effects_for(mid)
        original = server._ledger.save
        def lose_receipt(messages, dedupe, seq):
            original(messages, dedupe, seq)
            if any(effect["state"] == "succeeded" for message in messages.values() for effect in message.get("_effects", [])):
                raise OSError("receipt failed after durable save")
        with mock.patch.object(server._ledger, "save", side_effect=lose_receipt):
            result = scope.run("remember", {"note": "committed"}, lambda: (x.memory.remember("committed"), False))
        self.assertFalse(result[1])
        self.assertEqual("succeeded", self.disk(store, mid)["_effects"][0]["state"])

    def test_an_undelivered_saved_result_keeps_the_conversation_busy(self):
        server, x, _, _, _ = self.runtime()
        mid = self.stage(server)
        server._effects_for(mid).run("remember", {"note": "saved but undelivered"},
            lambda: (x.memory.remember("saved but undelivered"), False))
        with server._changed:
            self.assertIsNotNone(server._busy_reason("side-work"))
        server.retry_effects()
        self.assertEqual("done", server.get(mid, wait=5)["status"])
        self.assertTrue(until(lambda: not server._busy))
        with server._changed:
            self.assertIsNone(server._busy_reason("side-work"))

    def test_recovery_does_not_expose_a_started_request_as_unstarted_to_an_old_binary(self):
        server, x, _, _, store = self.runtime()
        mid = self.stage(server)
        server._effects_for(mid).run("remember", {"note": "saved once"},
            lambda: (x.memory.remember("saved once"), False))
        server.close()
        with mock.patch.object(Service, "_line_up", return_value=True):
            held, _, _, _, _ = self.runtime()
        saved = self.disk(store, mid)
        self.assertEqual("queued", saved["status"])
        self.assertEqual("running", saved["operation_state"])
        self.assertTrue(saved["_resume_effects"])
        self.assertEqual("succeeded", saved["_effects"][0]["state"])
        held.close()
        again, restored, voice, _, _ = self.runtime()
        self.assertEqual("done", again.get(mid, wait=5)["status"])
        self.assertEqual(["saved once"], restored.memory.texts())
        self.assertEqual([], voice.tool_jobs)


if __name__ == "__main__":
    unittest.main()
