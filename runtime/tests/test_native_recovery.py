"""Identity-bound native queries use fixtures, never accounts or model calls."""

import json
import os
import threading
from types import SimpleNamespace
import unittest
from dataclasses import replace
from unittest import mock

from test_runtime import Scripted, TempDirCase, make_xiaoyou
from xiaoyou_runtime import native_history
from xiaoyou_runtime.config import AgentSpec
from xiaoyou_runtime.conversation_directory import home_scope
from xiaoyou_runtime.service import ConflictError, RequestError, Service


def event(kind, **payload):
    return {"type": "event_msg", "payload": {"type": kind, **payload}}


def final(text="original result", **fields):
    return {"type": "response_item", "payload": dict(type="message", role="assistant",
        phase="final_answer", content=[{"type": "output_text", "text": text}], **fields)}


class NativeExecutionTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.home = self.folder / "native"
        self.spec = AgentSpec(name="worker", type="codex", config_dir=self.home,
                              description="worker", aliases=[], speaks=False, timeout_seconds=60)
        self.binding = dict(native_scope=home_scope(self.home), native_session_id="session-1",
                            native_turn_id="turn-1", range_state="open-turn")
        self.path = self.home / "sessions/2026/rollout-session-1.jsonl"
        self.path.parent.mkdir(parents=True)
        self.write({"type": "session_meta", "payload": {"id": "session-1", "originator": "xiaoyou-runtime"}})

    def write(self, *events):
        with self.path.open("a", encoding="utf-8") as stream:
            for value in events:
                stream.write(json.dumps(value) + "\n")

    def complete(self, text="original result"):
        self.write(event("task_started", turn_id="turn-1"), final(text),
                   event("task_complete", turn_id="turn-1", last_agent_message=text))

    def query(self):
        return native_history.execution(self.spec, self.binding)

    def test_completion_is_bound_to_the_original_range_and_does_not_modify_source(self):
        self.complete()
        self.write(event("task_started", turn_id="turn-2"), final("unrelated later result"),
                   event("task_complete", turn_id="turn-2", last_agent_message="unrelated later result"))
        before = self.path.read_bytes()
        self.assertEqual("original result", self.query())
        self.assertEqual(before, self.path.read_bytes())

    def test_explicit_terminal_result_without_a_message_item_is_supported(self):
        self.write(event("task_started", turn_id="turn-1"),
                   event("task_complete", turn_id="turn-1", last_agent_message="saved final"))
        self.assertEqual("saved final", self.query())

    def test_bound_final_message_requires_matching_completion(self):
        self.write(event("task_started", turn_id="turn-1"), final(), event("task_complete", turn_id="other"))
        self.assertIsNone(self.query())

    def test_final_message_and_an_audit_end_marker_do_not_prove_completion(self):
        self.binding["range_state"] = "complete-turn"
        self.write(event("task_started", turn_id="turn-1"), final())
        self.assertIsNone(self.query())

    def test_a_completion_without_the_matching_start_remains_unknown(self):
        self.write(final(), event("task_complete", turn_id="turn-1", last_agent_message="orphan"))
        self.assertIsNone(self.query())

    def test_commentary_and_private_channels_are_not_final_results(self):
        self.write(event("task_started", turn_id="turn-1"), final("private", channel="analysis"),
                   {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                    "phase": "commentary", "content": [{"type": "output_text", "text": "working"}]}},
                   event("task_complete", turn_id="turn-1"))
        self.assertIsNone(self.query())

    def test_cancellation_is_not_reconciled_as_success(self):
        self.write(event("task_started", turn_id="turn-1"), final(),
                   event("turn_aborted", turn_id="turn-1"), event("task_complete", turn_id="turn-1"))
        self.assertIsNone(self.query())

    def test_conflicting_final_evidence_remains_unknown(self):
        self.write(event("task_started", turn_id="turn-1"), final(),
                   event("task_complete", turn_id="turn-1", last_agent_message="different result"))
        self.assertIsNone(self.query())

    def test_reused_turn_identity_is_ambiguous(self):
        self.complete()
        self.write(event("task_started", turn_id="turn-1"))
        self.assertIsNone(self.query())

    def test_a_gap_or_context_change_cannot_borrow_another_turns_answer(self):
        self.write(event("task_started", turn_id="turn-1"),
                   {"type": "turn_context", "payload": {"turn_id": "other"}}, final(),
                   event("task_complete", turn_id="turn-1"))
        self.assertIsNone(self.query())

    def test_incomplete_or_malformed_source_is_not_evidence(self):
        self.complete()
        with self.path.open("ab") as stream:
            stream.write(b'{"type":')
        self.assertIsNone(self.query())

    def test_invalid_unicode_cannot_poison_the_recovered_message_log(self):
        self.complete("invalid " + chr(0xD800))
        self.assertIsNone(self.query())

    def test_home_scope_session_and_native_harness_must_match(self):
        self.complete()
        original = self.path.read_text()
        for field, value in (("native_scope", "other"), ("native_session_id", "../session-1"),
                             ("native_turn_id", None)):
            with self.subTest(field=field):
                binding = dict(self.binding, **{field: value})
                self.assertIsNone(native_history.execution(self.spec, binding))
        self.assertIsNone(native_history.execution(replace(self.spec, type="dsh"), self.binding))
        self.path.write_text(original.replace('"id": "session-1"', '"id": "other"'))
        self.assertIsNone(self.query())

    def test_forked_and_duplicate_sources_remain_unknown(self):
        self.complete()
        contents = self.path.read_text()
        other = self.path.with_name("duplicate-session-1.jsonl")
        other.write_text(contents)
        self.assertIsNone(self.query())
        other.unlink()
        self.path.write_text(contents.replace('"originator": "xiaoyou-runtime"', '"parent_thread_id": "parent"'))
        self.assertIsNone(self.query())

    def test_scan_limit_leaves_long_sources_unknown(self):
        self.complete()
        with mock.patch.object(native_history, "MAX_EXECUTION_BYTES", 50):
            self.assertIsNone(self.query())

    def test_a_source_changing_during_the_read_is_not_a_stable_receipt(self):
        self.complete()
        stat = self.path.stat()
        changed = SimpleNamespace(st_size=stat.st_size + 1, st_mtime_ns=stat.st_mtime_ns, st_ino=stat.st_ino)
        with mock.patch.object(os, "fstat", side_effect=[stat, changed]):
            self.assertIsNone(self.query())

    def test_recognizable_credentials_are_redacted_from_recovered_public_text(self):
        self.complete("done; password=fixture-secret")
        result = self.query()
        self.assertIn("done", result)
        self.assertNotIn("fixture-secret", result)


class NativeRecoveryTests(TempDirCase):
    def helper(self):
        agent = Scripted("codex", kind="codex", speaks=False)
        agent.spec = replace(agent.spec, config_dir=self.folder / "native")
        return agent

    def runtime(self):
        agent = self.helper()
        x, store = make_xiaoyou(self.folder, [agent])
        self.addCleanup(x.close)
        return x, store, agent

    def stage(self, text="native result"):
        x, _, agent = self.runtime()
        card = x._writer.task_open("side-native", "original work", "working", agent.name)["id"]
        with mock.patch.object(x._tasks, "_run", return_value=None):
            x._launch(card, agent, "original input", "side-native", 0)
        run_id = x.cards.get(card)["run_id"]
        x._writer.execution_binding(card, "attempt-1", required=True, run_id=run_id, agent=agent.name,
            native_scope=home_scope(agent.spec.config_dir), native_session_id="session-1", native_turn_id="turn-1")
        path = agent.spec.config_dir / "sessions/rollout-session-1.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        values = [{"type": "session_meta", "payload": {"id": "session-1"}},
                  event("task_started", turn_id="turn-1"), final(text)]
        path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")
        x.close()
        return card, run_id, path

    def complete(self, path, text="native result"):
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event("task_complete", turn_id="turn-1", last_agent_message=text)) + "\n")

    def service(self):
        x, store, helper = self.runtime()
        service = Service(x, store)
        self.addCleanup(service.close)
        return service, x, helper

    def test_startup_recovers_original_result_without_any_model_or_tool_call(self):
        card, run_id, path = self.stage()
        self.complete(path)
        service, x, helper = self.service()
        self.assertEqual("done", x.cards.get(card)["state"])
        self.assertEqual("native result", x.cards.message(run_id + "-summary:reply")["text"])
        self.assertEqual("side-native", x.cards.message(run_id + "-summary:reply")["conversation"])
        self.assertEqual([], helper.started)
        self.assertEqual(0, service.recover_tasks())

    def test_query_can_reconcile_a_later_completion_and_is_idempotent(self):
        card, run_id, path = self.stage()
        service, x, helper = self.service()
        self.assertEqual("unconfirmed", service.reconcile_task(card, {"run_id": run_id})["status"])
        self.complete(path)
        for _ in range(2):
            result = service.reconcile_task(card, {"run_id": run_id})
            self.assertEqual("confirmed", result["status"])
            self.assertNotIn("dispatch", result["card"])
        self.assertEqual(1, sum(e["id"] == run_id + "-summary:reply" for e in x.cards.get(card)["entries"]))
        self.assertEqual([], helper.started)

    def test_newer_attempt_with_missing_identity_cannot_use_an_older_turn(self):
        card, run_id, path = self.stage()
        self.complete(path)
        x, _, _ = self.runtime()
        x._writer.execution_binding(card, "attempt-2", run_id=run_id, agent="codex")
        self.assertEqual(0, x.recover_tasks(ready=True))
        self.assertEqual("unknown", x.cards.get(card)["state"])

    def test_replaced_helper_configuration_does_not_change_the_source_of_truth(self):
        card, _, path = self.stage()
        self.complete(path)
        agent = self.helper()
        agent.spec = replace(agent.spec, description="changed helper")
        x, _ = make_xiaoyou(self.folder, [agent])
        self.addCleanup(x.close)
        self.assertEqual(0, x.recover_tasks(ready=True))
        self.assertEqual("unknown", x.cards.get(card)["state"])

    def test_lost_result_write_keeps_unknown_and_can_be_queried_again(self):
        card, run_id, path = self.stage()
        service, x, helper = self.service()
        self.complete(path)
        original = x.record.reply
        def write(*args, **kwargs):
            if kwargs.get("kind") == "worker_result":
                raise OSError("disk full")
            return original(*args, **kwargs)
        with mock.patch.object(x.record, "reply", new=write):
            self.assertEqual("unconfirmed", service.reconcile_task(card, {"run_id": run_id})["status"])
        self.assertEqual("unknown", x.cards.get(card)["state"])
        self.assertIsNone(x.cards.message(run_id + "-result:reply"))
        self.assertEqual("confirmed", service.reconcile_task(card, {"run_id": run_id})["status"])
        self.assertEqual([], helper.started)

    def test_saved_native_result_survives_failure_of_the_summary_write(self):
        card, run_id, path = self.stage()
        service, x, helper = self.service()
        self.complete(path)
        with mock.patch.object(x._writer, "reply", side_effect=OSError("disk full")):
            service.reconcile_task(card, {"run_id": run_id})
        self.assertIsNotNone(x.cards.message(run_id + "-result:reply"))
        with mock.patch.object(native_history, "execution", side_effect=AssertionError("use saved result")):
            self.assertEqual("confirmed", service.reconcile_task(card, {"run_id": run_id})["status"])

    def test_query_does_not_publish_over_a_new_run(self):
        card, run_id, path = self.stage()
        service, x, helper = self.service()
        self.complete(path)
        query = native_history.execution
        def changed(spec, binding):
            result = query(spec, binding)
            with mock.patch.object(x._tasks, "_run", return_value=None):
                x._launch(card, helper, "new input", "side-native", 0)
            return result
        with mock.patch.object(native_history, "execution", side_effect=changed), self.assertRaises(ConflictError):
            service.reconcile_task(card, {"run_id": run_id})
        self.assertIsNone(x.cards.message(run_id + "-result:reply"))

    def test_an_attempt_change_during_the_query_cannot_publish_the_old_result(self):
        card, run_id, path = self.stage()
        service, x, _ = self.service()
        self.complete(path)
        query = native_history.execution
        def changed(spec, binding):
            result = query(spec, binding)
            x._writer.execution_binding(card, "attempt-2", run_id=run_id, agent="codex")
            return result
        with mock.patch.object(native_history, "execution", side_effect=changed):
            self.assertEqual("unconfirmed", service.reconcile_task(card, {"run_id": run_id})["status"])
        self.assertIsNone(x.cards.message(run_id + "-result:reply"))
        self.assertEqual("unknown", x.cards.get(card)["state"])

    def test_an_attempt_change_after_result_save_cannot_settle_the_next_attempt(self):
        card, run_id, path = self.stage()
        service, x, _ = self.service()
        self.complete(path)
        with mock.patch.object(x._writer, "reply", side_effect=OSError("summary unavailable")):
            service.reconcile_task(card, {"run_id": run_id})
        x._writer.execution_binding(card, "attempt-2", run_id=run_id, agent="codex")
        self.assertEqual("unconfirmed", service.reconcile_task(card, {"run_id": run_id})["status"])
        self.assertIsNone(x.cards.message(run_id + "-summary:reply"))

    def test_owner_change_or_shutdown_during_the_query_prevents_publication(self):
        card, run_id, path = self.stage()
        service, x, _ = self.service()
        self.complete(path)
        query = native_history.execution
        def closing(spec, binding):
            result = query(spec, binding)
            x._task_recovery_ready = False
            return result
        with mock.patch.object(native_history, "execution", side_effect=closing):
            self.assertEqual("unconfirmed", service.reconcile_task(card, {"run_id": run_id})["status"])
        self.assertIsNone(x.cards.message(run_id + "-result:reply"))
        x._task_recovery_ready = True
        view = {"owner": "local", "generation": 1}
        x.mesh = SimpleNamespace(runtime_id="local", owner_of=lambda _: dict(view))
        def moved(spec, binding):
            result = query(spec, binding)
            view["owner"] = "other"
            return result
        with mock.patch.object(native_history, "execution", side_effect=moved):
            self.assertEqual("unconfirmed", service.reconcile_task(card, {"run_id": run_id})["status"])
        self.assertIsNone(x.cards.message(run_id + "-result:reply"))

    def test_query_refuses_stale_ids_and_unrecognized_body_fields(self):
        card, run_id, _ = self.stage()
        service, _, helper = self.service()
        with self.assertRaises(ConflictError):
            service.reconcile_task(card, {"run_id": "other"})
        with self.assertRaises(RequestError):
            service.reconcile_task(card, {"run_id": run_id, "repeat": True})
        self.assertEqual([], helper.started)

    def test_saved_reply_schema_is_unwrapped_without_calling_a_model(self):
        text = json.dumps({"reply": "actual saved answer", "mood": "calm", "brief": "done"})
        card, run_id, path = self.stage(text)
        self.complete(path, text)
        _, x, helper = self.service()
        self.assertEqual("actual saved answer", x.cards.message(run_id + "-summary:reply")["text"])
        self.assertEqual([], helper.started)

    def test_invalid_unicode_inside_a_saved_reply_schema_remains_unknown(self):
        text = json.dumps({"reply": "invalid " + chr(0xD800)})
        card, run_id, path = self.stage(text)
        self.complete(path, text)
        service, x, _ = self.service()
        self.assertEqual("unknown", x.cards.get(card)["state"])
        self.assertIsNone(x.cards.message(run_id + "-result:reply"))
        self.assertEqual("unconfirmed", service.reconcile_task(card, {"run_id": run_id})["status"])

    def test_concurrent_queries_publish_one_result_and_one_summary(self):
        card, run_id, path = self.stage()
        service, x, helper = self.service()
        self.complete(path)
        results = []
        threads = [threading.Thread(target=lambda: results.append(
            service.reconcile_task(card, {"run_id": run_id}))) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(2, len(results))
        self.assertTrue(all(item["status"] == "confirmed" for item in results))
        entries = x.cards.get(card)["entries"]
        for suffix in ("-result:reply", "-summary:reply"):
            self.assertEqual(1, sum(e["id"] == run_id + suffix for e in entries))
        self.assertEqual([], helper.started)

    def test_long_result_keeps_the_complete_text_in_a_saved_attachment(self):
        from xiaoyou_runtime.agents import MAX_OUTPUT_CHARS
        text = "x" * (MAX_OUTPUT_CHARS + 100) + "tail"
        card, run_id, path = self.stage(text)
        self.complete(path, text)
        service, x, _ = self.service()
        result = x.cards.message(run_id + "-summary:reply")
        self.assertTrue(result["attachments"])
        stream, _, _, _ = x.artifacts.open_content(result["attachments"][0]["id"])
        with stream:
            self.assertEqual(text, stream.read().decode("utf-8"))
        self.assertEqual("confirmed", service.reconcile_task(card, {"run_id": run_id})["status"])

    def test_native_query_does_not_hold_the_polling_lock(self):
        card, run_id, _ = self.stage()
        service, _, _ = self.service()
        def query(*_):
            acquired = threading.Event()
            def read():
                with service._changed:
                    acquired.set()
            reader = threading.Thread(target=read)
            reader.start()
            reader.join(2)
            self.assertTrue(acquired.is_set())
            return None
        with mock.patch.object(native_history, "execution", side_effect=query):
            self.assertEqual("unconfirmed", service.reconcile_task(card, {"run_id": run_id})["status"])
            self.assertEqual(0, service.recover_tasks())

    def test_closed_service_cannot_reenable_task_recovery(self):
        card, _, _ = self.stage()
        service, x, helper = self.service()
        service.close()
        self.assertEqual(0, service.recover_tasks())
        self.assertFalse(x._task_recovery_ready)
        self.assertEqual([], helper.started)

    def test_http_query_requires_authentication_and_original_run_identity(self):
        import urllib.error
        import urllib.request
        from xiaoyou_runtime.server import make_server
        card, run_id, path = self.stage()
        service, x, helper = self.service()
        server = make_server(replace(x._config, host="127.0.0.1", port=0), service)
        server.RequestHandlerClass.log_message = lambda *args: None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:%d/v1/cards/%s/reconcile" % (server.server_address[1], card)
        def request(body, token=True):
            headers = {"Content-Type": "application/json", "Connection": "close"}
            if token:
                headers["Authorization"] = "Bearer " + x._config.token
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, json.loads(error.read())
        self.assertEqual(401, request({"run_id": run_id}, token=False)[0])
        self.assertEqual(409, request({"run_id": "stale"})[0])
        self.assertEqual("unconfirmed", request({"run_id": run_id})[1]["status"])
        self.complete(path)
        status, body = request({"run_id": run_id})
        self.assertEqual(200, status)
        self.assertEqual("confirmed", body["status"])
        self.assertEqual([], helper.started)


if __name__ == "__main__":
    unittest.main()
