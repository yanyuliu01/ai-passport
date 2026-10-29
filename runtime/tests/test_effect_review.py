"""Human review closes uncertainty without inventing or replaying tool results."""

from copy import deepcopy
from dataclasses import replace
import json
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request

from test_runtime import TempDirCase, ToolTalker, Scripted, make_xiaoyu
from xiaoyu_runtime.loop.effects import EffectUncertain, valid_review, review_record
from xiaoyu_runtime.events.ledger import current
from xiaoyu_runtime.api.service import Service, ConflictError, RequestError, UnavailableError


class EffectReviewTests(TempDirCase):
    def runtime(self):
        voice, helper = ToolTalker(), Scripted("codex", speaks=False)
        x, store = make_xiaoyu(self.folder, [voice, helper])
        service = Service(x, store)
        self.addCleanup(service.close)
        return service, x, voice, helper, store

    def unknown(self, service, x, pending=False):
        with mock.patch.object(service, "_line_up", return_value=True):
            mid = service.submit("original input", conversation="side-review", client_id="original-client-id")["id"]
        service._update(mid, strict=True, status="running")
        scope = service._effects_for(mid)
        if pending:
            scope._save({"version": 1, "id": mid + ":effect:1", "name": "remember",
                         "args": {"note": "pending selection"}, "state": "pending"})
        else:
            def invoke():
                x.memory.remember("saved before response loss")
                raise OSError("result lost")
            with self.assertRaises(EffectUncertain):
                scope.run("remember", {"note": "saved before response loss"}, invoke)
        service._fail(mid, "side-review", "original input", "result uncertain", unknown=True)
        return mid

    def body(self, service, mid, outcome="applied", review_id="review-1"):
        view = service.effect_review(mid)
        return {"operation_id": view["operation_id"], "revision": view["revision"],
                "review_id": review_id, "outcomes": {item["id"]: outcome for item in view["effects"]
                                                       if item["state"] != "succeeded"}}

    def busy(self, service):
        with service._changed:
            return service._busy_reason("side-review")

    def test_manual_review_preserves_unknown_claim_and_does_not_reexecute(self):
        service, x, voice, helper, _ = self.runtime()
        mid = self.unknown(service, x)
        before = deepcopy(service._messages[mid]["_effects"])
        self.assertIsNotNone(self.busy(service))
        result = service.review_effects(mid, self.body(service, mid), actor="device:phone")
        self.assertEqual("reviewed", result["status"])
        self.assertEqual(before, service._messages[mid]["_effects"])
        self.assertEqual("unknown", service.get(mid)["operation_state"])
        self.assertIsNone(self.busy(service))
        self.assertEqual(["saved before response loss"], x.memory.texts())
        self.assertEqual([], voice.tool_jobs)
        self.assertEqual([], helper.started)
        self.assertIn("并非程序验证", x.cards.message(mid + "-effect-review:reply")["text"])

    def test_not_applied_is_a_disposition_not_a_retry_or_rollback(self):
        service, x, voice, _, _ = self.runtime()
        mid = self.unknown(service, x)
        result = service.review_effects(mid, self.body(service, mid, "not_applied"))
        self.assertEqual("reviewed", result["status"])
        self.assertEqual(["saved before response loss"], x.memory.texts())
        self.assertEqual([], voice.tool_jobs)
        service.retry_effects()
        self.assertEqual("failed", service.get(mid)["status"])

    def test_same_review_retries_publish_one_note_and_different_payload_conflicts(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        body = self.body(service, mid)
        first = service.review_effects(mid, body)
        self.assertEqual(first, service.review_effects(mid, body))
        self.assertEqual(1, sum(item["id"] == mid + "-effect-review:reply"
                               for item in x.cards.get(service.get(mid)["card"])["entries"]))
        for changed in (dict(body, review_id="different"),
                        dict(body, outcomes={key: "not_applied" for key in body["outcomes"]})):
            with self.assertRaises(ConflictError):
                service.review_effects(mid, changed)
        with self.assertRaises(ConflictError):
            service.review_effects(mid, body, actor="other-device")

    def test_stale_or_cross_request_identity_is_rejected(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        body = self.body(service, mid)
        for changed in (dict(body, revision="old"), dict(body, operation_id="other:handle")):
            with self.assertRaises(ConflictError):
                service.review_effects(mid, changed)
        service._messages[mid]["pin"] = True
        with self.assertRaises(ConflictError):
            service.review_effects(mid, body)
        self.assertIsNone(review_record(service._messages[mid]))

    def test_every_unconfirmed_selection_requires_an_explicit_supported_outcome(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        body = self.body(service, mid)
        for outcomes in ({}, {"foreign": "applied"}, {key: "retry" for key in body["outcomes"]}, []):
            with self.assertRaises(RequestError):
                service.review_effects(mid, dict(body, outcomes=outcomes))
        with self.assertRaises(RequestError):
            service.review_effects(mid, dict(body, actor="spoofed"))

    def test_pending_selection_can_only_be_closed_as_not_applied(self):
        service, x, voice, _, _ = self.runtime()
        mid = self.unknown(service, x, pending=True)
        with self.assertRaises(RequestError):
            service.review_effects(mid, self.body(service, mid))
        service.review_effects(mid, self.body(service, mid, "not_applied"))
        self.assertEqual([], x.memory.texts())
        self.assertEqual([], voice.tool_jobs)

    def test_active_requests_and_claimants_cannot_be_reviewed(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        body = self.body(service, mid)
        service._claimed.add(mid)
        with self.assertRaises(ConflictError):
            service.review_effects(mid, body)
        service._claimed.discard(mid)
        service._messages[mid]["status"] = "running"
        with self.assertRaises(ConflictError):
            service.review_effects(mid, body)

    def test_remote_requests_and_changed_owner_are_rejected(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        body = self.body(service, mid)
        service._messages[mid]["relay_to"] = "other"
        with self.assertRaises(ConflictError):
            service.review_effects(mid, body)
        service._messages[mid].pop("relay_to")
        with mock.patch.object(service, "_effect_owner", return_value={"owner": "other", "generation": 1}):
            with self.assertRaises(ConflictError):
                service.review_effects(mid, body)

    def test_unavailable_permission_authority_and_closed_service_refuse_writes(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        body = self.body(service, mid)
        with mock.patch.object(service, "permission_problem", return_value="Mac unavailable"):
            with self.assertRaises(ConflictError):
                service.review_effects(mid, body)
        service.close()
        with self.assertRaises(UnavailableError):
            service.review_effects(mid, body)

    def test_failed_journal_write_leaves_uncertainty_and_can_retry(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        body = self.body(service, mid)
        with mock.patch.object(service._ledger, "save", side_effect=OSError("disk full")):
            with self.assertRaises(UnavailableError):
                service.review_effects(mid, body)
        self.assertIsNone(review_record(service._messages[mid]))
        self.assertIsNotNone(self.busy(service))
        self.assertEqual("reviewed", service.review_effects(mid, body)["status"])

    def test_post_commit_receipt_loss_keeps_the_review_and_repairs_without_replay(self):
        service, x, _, _, store = self.runtime()
        mid = self.unknown(service, x)
        body = self.body(service, mid)
        original = service._ledger.save
        def lost_receipt(messages, dedupe, seq):
            original(messages, dedupe, seq)
            if review_record(messages[mid]):
                raise OSError("lost receipt")
        with mock.patch.object(service._ledger, "save", side_effect=lost_receipt):
            with self.assertRaises(UnavailableError):
                service.review_effects(mid, body)
        self.assertEqual("recorded", service.effect_review(mid)["status"])
        self.assertIsNotNone(self.busy(service))
        disk = next(item for item in current(store.state_dir / "messages.json")["messages"] if item["id"] == mid)
        self.assertEqual(body["review_id"], review_record(disk)["id"])
        self.assertEqual("reviewed", service.review_effects(mid, body)["status"])

    def test_note_failure_does_not_release_handoff_until_publication_and_survives_restart(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        body = self.body(service, mid)
        original = x.cards.update
        def unavailable(*args, **fields):
            if fields.get("turn_id") == mid + "-effect-review":
                raise OSError("note storage unavailable")
            return original(*args, **fields)
        with mock.patch.object(x.cards, "update", side_effect=unavailable):
            with self.assertRaises(UnavailableError):
                service.review_effects(mid, body)
        self.assertIsNotNone(self.busy(service))
        service.close()
        restored, again, voice, _, _ = self.runtime()
        self.assertEqual("recorded", restored.effect_review(mid)["status"])
        self.assertEqual("reviewed", restored.review_effects(mid, body)["status"])
        self.assertIsNone(self.busy(restored))
        self.assertEqual([], voice.tool_jobs)
        self.assertEqual(["saved before response loss"], again.memory.texts())

    def test_review_durable_replay_keeps_unknown_claim_and_does_not_block_idle_handoff(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        body = self.body(service, mid)
        service.review_effects(mid, body)
        service.close()
        restored, _, voice, _, _ = self.runtime()
        self.assertEqual("reviewed", restored.effect_review(mid)["status"])
        self.assertEqual("unknown", restored.get(mid)["operation_state"])
        self.assertIsNone(self.busy(restored))
        self.assertEqual([], voice.tool_jobs)

    def test_review_is_invalidated_if_saved_effect_payload_changes(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        service.review_effects(mid, self.body(service, mid))
        self.assertTrue(valid_review(service._messages[mid]))
        service._messages[mid]["_effects"][0]["args"]["note"] = "changed"
        self.assertFalse(valid_review(service._messages[mid]))
        self.assertIsNotNone(self.busy(service))
        with self.assertRaises(ConflictError):
            service.effect_review(mid)

    def test_private_review_and_inputs_are_not_in_generic_projections(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        service.review_effects(mid, self.body(service, mid))
        for value in (service.get(mid), service.feed("side-review", messages=True), service.sync("side-review")):
            text = json.dumps(value)
            self.assertNotIn('"_effect_review"', text)
            self.assertNotIn('"outcomes"', text)
            self.assertNotIn('"_effects"', text)
        self.assertIn("request_effect_review", service.info()["capabilities"])

    def test_concurrent_identical_reviews_have_one_disposition_and_note(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        body = self.body(service, mid)
        results, errors = [], []
        def run():
            try:
                results.append(service.review_effects(mid, body))
            except Exception as error:
                errors.append(error)
        workers = [threading.Thread(target=run) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(2, len(results))
        self.assertEqual(results[0], results[1])

    def test_final_publication_receipt_failure_cannot_release_handoff_early(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        body = self.body(service, mid)
        original = service._ledger.save
        def unavailable(messages, dedupe, seq):
            if (review_record(messages[mid]) or {}).get("published"):
                raise OSError("publication receipt disk failure")
            return original(messages, dedupe, seq)
        with mock.patch.object(service._ledger, "save", side_effect=unavailable):
            with self.assertRaises(UnavailableError):
                service.review_effects(mid, body)
        self.assertIsNotNone(x.cards.message(mid + "-effect-review:reply"))
        self.assertIsNotNone(self.busy(service))
        self.assertEqual("reviewed", service.review_effects(mid, body)["status"])
        self.assertIsNone(self.busy(service))

    def test_note_retention_does_not_resurrect_a_durably_reviewed_operation(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        body = self.body(service, mid)
        service.review_effects(mid, body)
        with mock.patch.object(x.cards, "message", return_value=None):
            self.assertIsNone(self.busy(service))
            self.assertEqual("reviewed", service.review_effects(mid, body)["status"])

    def test_original_request_retry_keeps_identity_and_cannot_reinvoke_effects(self):
        service, x, voice, _, _ = self.runtime()
        mid = self.unknown(service, x)
        service.review_effects(mid, self.body(service, mid))
        retried = service.submit("original input", conversation="side-review", client_id="original-client-id")
        self.assertEqual(mid, retried["id"])
        self.assertEqual("failed", retried["status"])
        self.assertEqual([], voice.tool_jobs)
        with self.assertRaises(EffectUncertain):
            service._effects_for(mid).run("remember", {"note": "saved before response loss"},
                                         lambda: self.fail("review cannot permit replay"))

    def test_corrupted_review_cannot_release_handoff(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        service.review_effects(mid, self.body(service, mid))
        review = review_record(service._messages[mid])
        for field, value in (("published", "yes"), ("at", 10 ** 10000), ("actor", None)):
            original = review[field]
            review[field] = value
            self.assertIsNotNone(self.busy(service))
            with self.assertRaises(ConflictError):
                service.effect_review(mid)
            review[field] = original

    def test_owner_change_during_note_publication_keeps_review_unconfirmed(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        body = self.body(service, mid)
        original_owner = service._effect_owner("side-review")
        owner = dict(original_owner)
        original = x.cards.update
        def moved(*args, **fields):
            result = original(*args, **fields)
            if fields.get("turn_id") == mid + "-effect-review":
                owner["generation"] += 1
            return result
        with mock.patch.object(service, "_effect_owner", side_effect=lambda _: dict(owner)), \
                mock.patch.object(x.cards, "update", side_effect=moved):
            with self.assertRaises(ConflictError):
                service.review_effects(mid, body)
        self.assertEqual("recorded", service.effect_review(mid)["status"])
        self.assertIsNotNone(self.busy(service))

    def test_http_requires_auth_binds_actor_and_rejects_revoked_devices(self):
        from xiaoyu_runtime.api.server import make_server
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        server = make_server(replace(x._config, host="127.0.0.1", port=0), service)
        server.RequestHandlerClass.log_message = lambda *args: None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:%d/v1/messages/%s/effects" % (server.server_address[1], mid)
        def request(body=None, token=None):
            headers = {"Content-Type": "application/json", "Connection": "close"}
            if token:
                headers["Authorization"] = "Bearer " + token
            req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                         headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, json.loads(error.read())
        self.assertEqual(401, request()[0])
        device = service.device_add("phone")
        credential, device_id = device["credential"], device["device"]["id"]
        self.assertEqual(200, request(token=credential)[0])
        body = self.body(service, mid)
        status, result = request(body, credential)
        self.assertEqual(200, status)
        self.assertEqual("device:" + device_id, result["review"]["actor"])
        self.assertEqual(409, request(dict(body, review_id="other"), credential)[0])
        service.device_revoke(device_id)
        self.assertEqual(401, request(body, credential)[0])

    def test_manual_request_review_cannot_settle_an_unknown_helper_execution(self):
        from xiaoyu_runtime.helpers.tasks import Task
        service, x, _, helper, _ = self.runtime()
        mid = self.unknown(service, x)
        card = x._writer.task_open("side-review", "unknown helper result")
        task = Task(card=card["id"], conversation="side-review", agent=helper, text="original job", system=None)
        intent = task.intent()
        intent["state"] = "unknown"
        x._writer.task(card["id"], state="unknown", run_id=task.run_id, dispatch=intent)
        service.review_effects(mid, self.body(service, mid))
        self.assertIn("执行结果待核对", self.busy(service))
        self.assertEqual("unknown", x.cards.get(card["id"])["state"])
        self.assertEqual([], helper.started)

    def test_foreign_effect_ids_and_invalid_unicode_review_ids_are_rejected(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        with self.assertRaises(RequestError):
            service.review_effects(mid, dict(self.body(service, mid), review_id="bad" + chr(0xD800)))
        service._messages[mid]["_effects"][0]["id"] = "another-request:effect:1"
        with self.assertRaises(ConflictError):
            service.effect_review(mid)

    def test_pinned_supplement_with_main_reply_scope_keeps_review_in_main_chat(self):
        service, x, _, _, _ = self.runtime()
        mid = self.unknown(service, x)
        service._messages[mid].update(pin=True, reply_scope="main")
        service.review_effects(mid, self.body(service, mid))
        self.assertEqual("main", x.cards.message(mid + "-effect-review:reply")["scope"])


if __name__ == "__main__":
    unittest.main()
