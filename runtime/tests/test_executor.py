"""The task execution boundary preserves saved identities across adapter changes."""

from copy import deepcopy
from dataclasses import replace
import unittest
from unittest import mock
from types import SimpleNamespace

from test_runtime import Scripted, TempDirCase, make_xiaoyu
from xiaoyu_runtime.helpers.tasks import fingerprint
from xiaoyu_runtime.errors import AgentError
from xiaoyu_runtime.loop.effects import Effects, EffectFenceError, EffectUncertain
from xiaoyu_runtime.loop.xiaoyu import Turn


class ExecutorTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.voice = Scripted("voice", speaks=True)
        self.helper = Scripted("worker", speaks=False)
        self.x, _ = make_xiaoyu(self.folder, [self.voice, self.helper])
        self.addCleanup(self.x.close)
        self.card = self.x.record.task_open("side", "original", "working", "worker")
        self.message = {"id": "r1", "conversation": "side"}

    def legacy_selection(self):
        # The pre-extraction journal format, independent of Executor.apply.
        return {"version": 1, "id": "r1:action", "name": "task_action", "state": "pending",
                "args": {"lead": "voice", "text": "original input", "conversation": "side", "turn_id": "r1",
                         "plan": {"type": "amend", "card": self.card["id"], "task": "original work", "mode": "after"},
                         "target_seq": self.card["seq"], "reply": "accepted", "brief": None, "mood": "busy", "hop": 0,
                         "pinned": True, "effort": None, "model": None, "defer_reply": True,
                         "agents": {agent.name: fingerprint(agent) for agent in (self.voice, self.helper)}}}

    def apply(self, scope, plan, **extra):
        return self.x._executor.apply(scope, self.voice, plan, lambda *args: None,
            text="original input", conversation="side", turn_id="r1", reply="accepted", brief=None,
            mood="busy", hop=0, pinned=True, effort=None, model=None, defer_reply=True, **extra)

    def action(self, plan, **extra):
        options = dict(reply="accepted", brief=None, mood="happy", hop=0, pinned=False,
                       effort=None, model=None, defer_reply=False, seen=None, interrupted=False)
        options.update(extra)
        return self.x._apply_conversation_now(self.voice, "original input", "side", "r1", plan,
                                             say=lambda *args: None, **options)

    def test_start_keeps_selected_task_title_tier_and_original_request_pair(self):
        with mock.patch.object(self.x, "_launch") as launch:
            turn = self.action({"type": "start", "card": None, "agent": "worker",
                                "title": "selected title", "task": "selected input", "effort": "high", "model": "chosen"})
        card = self.x.cards.get(turn.card)
        self.assertEqual("selected title", card["title"])
        self.assertEqual("working", card["state"])
        self.assertEqual(["r1:you", "r1:reply"], [entry["id"] for entry in card["entries"]])
        launch.assert_called_once_with(turn.card, self.helper, "selected input", "side", 0, "high", "chosen")
        self.assertTrue(turn.started)

    def test_amend_full_queued_and_idle_keep_the_existing_task(self):
        for admission in ("full", "after", None):
            with self.subTest(admission=admission), \
                    mock.patch.object(self.x, "_amend", return_value=admission) as amend, \
                    mock.patch.object(self.x, "_launch") as launch, \
                    mock.patch.object(self.x._workspace, "note") as note:
                before = self.x.cards.get(self.card["id"])
                turn = self.action({"type": "amend", "card": before, "task": "supplement", "mode": "after"})
                amend.assert_called_once_with(before, self.helper, "after", "supplement", None, None)
                current = self.x.cards.get(turn.card)
                self.assertEqual(before["id"], turn.card)
                self.assertEqual(before["edits"] + (admission != "full"), current["edits"])
                self.assertEqual(admission is None, bool(launch.call_count))
                self.assertEqual(admission == "after", bool(note.call_count))
                if admission == "full": self.assertIn("这句没记上", turn.reply)

    def test_cancel_uses_original_task_and_explicit_actor_without_relaunch(self):
        with mock.patch.object(self.x, "cancel") as cancel, mock.patch.object(self.x, "_launch") as launch:
            turn = self.action({"type": "cancel", "card": self.card})
        cancel.assert_called_once_with(self.card["id"], actor="xiaoyou", reason="original input", announce=False)
        launch.assert_not_called()
        self.assertEqual(self.card["id"], turn.card)
        self.assertFalse(turn.started)

    def test_remote_actions_use_late_bound_mesh_and_never_write_the_replica(self):
        for kind in ("amend", "desktop", "cancel"):
            with self.subTest(kind=kind):
                before = self.x.cards.get(self.card["id"])
                target = dict(before, mesh_origin="remote", monitoring_only=True)
                mesh = SimpleNamespace(amend=mock.Mock(), cancel=mock.Mock())
                self.x.mesh = mesh
                with mock.patch.object(self.x, "_launch") as launch:
                    turn = self.action({"type": kind, "card": target, "task": "remote input"})
                if kind == "cancel": mesh.cancel.assert_called_once_with(target["id"])
                else: mesh.amend.assert_called_once_with(target, "remote input", continue_desktop=True)
                launch.assert_not_called()
                self.assertNotEqual(target["id"], turn.card)
                self.assertEqual(before, self.x.cards.get(target["id"]))
                self.assertFalse(turn.started)

    def test_desktop_continuation_and_failure_preserve_local_reply_attribution(self):
        selected = Turn("continued", "continued", "busy", "worker", "continued", True)
        target = dict(self.card, monitoring_only=True)
        with mock.patch.object(self.x, "_continue_desktop", return_value=selected) as continuation:
            self.assertEqual(selected, self.action({"type": "desktop", "card": target, "task": "continue"}))
        continuation.assert_called_once_with(target, "original input", "side", "r1", 0,
                                             mock.ANY, None, None, "continue", "accepted", defer_reply=False)
        with mock.patch.object(self.x, "_continue_desktop", side_effect=AgentError("unavailable")):
            turn = self.action({"type": "desktop", "card": target, "task": "continue"})
        self.assertNotEqual(target["id"], turn.card)
        self.assertIn("unavailable", turn.reply)
        self.assertFalse(turn.started)

    def test_deferred_reply_writes_only_owner_entry_and_no_delivery_receipt(self):
        with mock.patch.object(self.x.context, "delivered") as delivered:
            turn = self.action({"type": "none", "card": None}, reply="", defer_reply=True)
        card = self.x.cards.get(turn.card)
        self.assertEqual(["r1:you"], [entry["id"] for entry in card["entries"]])
        delivered.assert_not_called()

    def test_direct_assignment_saves_handoff_before_preparing_worker_context(self):
        route = SimpleNamespace(text="named input", reason="api")
        def context(*args, **kwargs):
            latest = self.x.cards.recent("side")[-1]
            self.assertEqual(["r1:you", "r1:reply"], [entry["id"] for entry in latest["entries"]])
            return []
        with mock.patch.object(self.x.context, "turns", side_effect=context), \
                mock.patch.object(self.x, "_launch") as launch:
            turn = self.x._assign(self.helper, route, "owner input", "side", "r1", 0, lambda *args: None)
        launch.assert_called_once_with(turn.card, self.helper, "named input", "side", 0, None, None)
        self.assertTrue(turn.started)

    def test_named_supplement_only_prepares_history_when_no_execution_is_active(self):
        route = SimpleNamespace(text="named supplement", reason="api")
        for admission in ("after", "full", None):
            with self.subTest(admission=admission), \
                    mock.patch.object(self.x, "_amend", return_value=admission), \
                    mock.patch.object(self.x.context, "turns", return_value=[]) as history, \
                    mock.patch.object(self.x._store, "session", return_value=None), \
                    mock.patch.object(self.x._workspace, "note"), \
                    mock.patch.object(self.x, "_launch") as launch:
                card = self.x.cards.get(self.card["id"])
                turn = self.x._resume(card, self.helper, route, "owner input", "side", "r1", 0, lambda *args: None)
                self.assertEqual(card["id"], turn.card)
                self.assertEqual(admission is None, bool(history.call_count))
                self.assertEqual(admission is None, bool(launch.call_count))
                if admission == "full": self.assertIn("这句没记上", turn.reply)
                if admission == "after": self.assertIn("补充已记下", turn.reply)

    def test_all_task_actions_save_claim_and_reuse_the_original_result(self):
        for kind in ("start", "amend", "cancel", "desktop"):
            with self.subTest(kind=kind):
                saved = []
                scope = Effects("r1", [], lambda records: saved.append(deepcopy(records)))
                plan = {"type": kind, "card": None if kind == "start" else self.card,
                        "agent": "worker", "task": "original work"}
                def invoke(**args):
                    self.assertEqual(["pending", "running"], [records[-1]["state"] for records in saved])
                    self.assertTrue(args["interrupted"])
                    return Turn("accepted", "accepted", "busy", "worker", self.card["id"], True)
                with mock.patch.object(self.x._executor, "execute", side_effect=invoke) as adapter:
                    result = self.apply(scope, plan, interrupted=True)
                    self.assertEqual(1, adapter.call_count)
                self.assertEqual("succeeded", saved[-1][0]["state"])
                self.assertEqual("r1:action", saved[-1][0]["id"])
                self.assertNotIn("interrupted", saved[-1][0]["args"])
                self.assertNotIn("interrupted", result)
                restored = Effects("r1", saved[-1], lambda records: self.fail("no second claim"))
                with mock.patch.dict(self.x._agents, {}, clear=True), \
                        mock.patch.object(self.x._executor, "execute", side_effect=AssertionError("no second execution")):
                    again = self.x._executor.resume(restored, saved[-1][0], self.message, lambda *args: None)
                self.assertEqual(result, again)

    def test_existing_pending_record_keeps_exact_inputs_and_ignores_extra_metadata(self):
        record = self.legacy_selection()
        record["args"]["extra_metadata"] = {"future": True}
        original = deepcopy(record)
        scope = Effects("r1", [record], lambda records: None)
        turn = Turn("accepted", "accepted", "busy", "worker", self.card["id"], True)
        with mock.patch.object(self.x._executor, "execute", return_value=turn) as adapter:
            self.x._executor.resume(scope, record, self.message, lambda *args: None)
        args = adapter.call_args.kwargs
        self.assertEqual("original input", args["text"])
        self.assertEqual("r1", args["turn_id"])
        self.assertEqual("original work", args["plan"]["task"])
        self.assertEqual(self.card["seq"], args["plan"]["card"]["seq"])
        self.assertTrue(args["defer_reply"])
        self.assertNotIn("extra_metadata", args)
        self.assertEqual(original, record)
        self.assertEqual([], self.voice.jobs + self.helper.jobs)

    def test_changed_request_target_or_helper_cannot_resume_pending_work(self):
        for change in ("conversation", "turn", "revision", "missing", "helper"):
            with self.subTest(change=change):
                record = self.legacy_selection()
                message = dict(self.message)
                if change == "conversation": message["conversation"] = "other"
                if change == "turn": message["id"] = "other"
                target = dict(self.card, seq=self.card["seq"] + 1) if change == "revision" else self.card
                if change == "missing": target = None
                helper = self.x._agents["worker"]
                if change == "helper":
                    helper = Scripted("worker", speaks=False)
                    helper.spec = replace(helper.spec, description="changed")
                scope = Effects("r1", [record], lambda records: None)
                with mock.patch.object(self.x._cards, "get", return_value=target), \
                        mock.patch.dict(self.x._agents, {"worker": helper}), \
                        mock.patch.object(self.x._executor, "execute") as adapter:
                    with self.assertRaises(EffectUncertain):
                        self.x._executor.resume(scope, record, message, lambda *args: None)
                    adapter.assert_not_called()
                self.assertEqual("unknown", scope.records[0]["state"])

    def test_uncertain_execution_is_never_repeated(self):
        for state in ("running", "unknown"):
            with self.subTest(state=state):
                record = dict(self.legacy_selection(), state=state)
                scope = Effects("r1", [record], lambda records: self.fail("no new claim"))
                with mock.patch.object(self.x._executor, "execute") as adapter:
                    with self.assertRaises(EffectUncertain):
                        self.x._executor.resume(scope, record, self.message, lambda *args: None)
                    adapter.assert_not_called()

    def test_remote_action_receipt_keeps_execution_unconfirmed(self):
        target = dict(self.card, mesh_origin="another-runtime")
        turn = Turn("accepted", "accepted", "happy", "voice", self.card["id"], False)
        scope = Effects("r1", [], lambda records: None)
        with mock.patch.object(self.x._executor, "execute", return_value=turn):
            result = self.apply(scope, {"type": "cancel", "card": target})
        self.assertFalse(result["started"])
        self.assertIn("等待那边确认", result["_facts"])
        self.assertIn("尚不能当作远端执行完成", result["_facts"])

    def test_failed_pending_or_claim_write_never_enters_the_action_adapter(self):
        for failed in ("pending", "running"):
            with self.subTest(failed=failed):
                def checkpoint(records):
                    if records[-1]["state"] == failed: raise OSError("disk full")
                scope = Effects("r1", [], checkpoint)
                with mock.patch.object(self.x._executor, "execute") as adapter:
                    with self.assertRaises(OSError):
                        self.apply(scope, {"type": "start", "card": None, "agent": "worker"})
                    adapter.assert_not_called()

    def test_owner_change_after_claim_fences_both_new_and_restored_actions(self):
        for restoring in (False, True):
            with self.subTest(restoring=restoring):
                record = self.legacy_selection()
                def guard():
                    if scope.records and scope.records[0]["state"] == "running":
                        raise EffectFenceError("owner changed")
                scope = Effects("r1", [record] if restoring else [], lambda records: None, guard)
                with mock.patch.object(self.x._executor, "execute") as adapter:
                    with self.assertRaises(EffectFenceError):
                        if restoring:
                            self.x._executor.resume(scope, record, self.message, lambda *args: None)
                        else:
                            self.apply(scope, {"type": "start", "card": None, "agent": "worker"})
                    adapter.assert_not_called()
                self.assertEqual("unknown", scope.records[0]["state"])


if __name__ == "__main__":
    unittest.main()
