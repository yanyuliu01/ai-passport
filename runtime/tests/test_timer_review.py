"""Timer review preserves original claims and never invokes their effects."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request

from test_runtime import GateTalker, TempDirCase, make_xiaoyou, until
from xiaoyou_runtime.effects import EffectUncertain, SavedWake
from xiaoyou_runtime.promises import wake_source
from xiaoyou_runtime.service import Service, ConflictError, RequestError, UnavailableError


class TimerReviewTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.voice = GateTalker()
        self.x, self.store = make_xiaoyou(self.folder, [self.voice])
        self.service = Service(self.x, self.store)
        self.addCleanup(self.service.close)

    def promise(self, pending=False, every=""):
        item = self.x.promises.track("original reminder", due=time.time() - 5, conversation="side-review", every=every)
        version = hashlib.sha256(json.dumps([item["due"], item["nudged"]]).encode()).hexdigest()[:16]
        event_id = item["id"] + "-due-" + version
        wake = SavedWake(event_id, ("local", 0), wake_source(item), None,
            lambda before, after: self.x.promises.checkpoint_wake(item, before, after), lambda: None)
        if pending:
            wake.effects._save({"version": 1, "id": event_id + ":effect:1", "name": "track",
                                "args": {"title": "private follow-up"}, "state": "pending"})
        else:
            def invoke():
                self.x.promises.track("private follow-up")
                raise OSError("result lost")
            with self.assertRaises(EffectUncertain):
                wake.effects.run("track", {"title": "private follow-up"}, invoke)
        return event_id

    def upkeep(self):
        card = self.x._writer.task_open("default", "retained turns")
        for index in range(3):
            self.x._writer.exchange(card["id"], "question %d" % index, "answer", turn_id="u%d" % index)
        self.x._pulse._save_marker("u0")
        source = [[turn["_key"], turn["_version"]] for turn in self.x.context.turns("default")[1:]]
        event_id = "upkeep-" + hashlib.sha256(json.dumps(["default", source]).encode()).hexdigest()[:32]
        wake = SavedWake(event_id, ("local", 0), source, None,
            lambda before, after: self.x._pulse._checkpoint_upkeep("u2", before, after), lambda: None)
        def invoke():
            self.x.memory.remember("private preference")
            raise OSError("result lost")
        with self.assertRaises(EffectUncertain):
            wake.effects.run("remember", {"note": "private preference"}, invoke)
        return event_id

    def body(self, event_id, outcome="applied"):
        view = self.service.timer_effect_review(event_id)
        return {"operation_id": view["operation_id"], "revision": view["revision"], "review_id": "review-1",
                "outcomes": {item["id"]: outcome for item in view["effects"] if item["state"] != "succeeded"}}

    def review(self, event_id, outcome="applied"):
        return self.service.review_timer_effects(event_id, self.body(event_id, outcome), actor="device:phone")

    def test_promise_review_preserves_the_original_unknown_effect_and_releases_handoff(self):
        event_id = self.promise()
        original = deepcopy(self.service.timer_effect_review(event_id)["effects"])
        self.assertTrue(self.x._pulse.unconfirmed("side-review"))
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no model")):
            result = self.review(event_id)
        self.assertEqual("reviewed", result["status"])
        self.assertEqual(original, result["effects"])
        self.assertEqual("unknown", result["effects"][0]["state"])
        self.assertFalse(self.x._pulse.unconfirmed("side-review"))
        self.assertEqual(2, len(self.x.promises.open()))
        self.assertIn("并非程序验证", self.x.cards.message(event_id + "-effect-review:reply")["text"])

    def test_upkeep_review_preserves_memory_and_the_original_claim_in_its_archive(self):
        event_id = self.upkeep()
        original = deepcopy(self.service.timer_effect_review(event_id)["effects"])
        self.assertEqual("reviewed", self.review(event_id, "not_applied")["status"])
        self.assertEqual(["private preference"], self.x.memory.texts())
        state = self.x._pulse._marker_state
        self.assertNotIn("wake", state)
        self.assertEqual(original, state["reviews"][0]["effects"])
        self.assertFalse(self.x._pulse.unconfirmed("default"))

    def test_unclaimed_effects_can_only_be_confirmed_not_applied(self):
        event_id = self.promise(pending=True)
        with self.assertRaises(RequestError):
            self.review(event_id)
        self.review(event_id, "not_applied")
        self.assertEqual(["original reminder"], [item["title"] for item in self.x.promises.open()])

    def test_same_identity_is_idempotent_and_conflicting_reviews_are_rejected(self):
        event_id = self.promise()
        body = self.body(event_id)
        result = self.service.review_timer_effects(event_id, body, actor="device:phone")
        self.assertEqual(result, self.service.review_timer_effects(event_id, body, actor="device:phone"))
        for changed in (dict(body, review_id="other"), dict(body, outcomes={key: "not_applied" for key in body["outcomes"]})):
            with self.assertRaises(ConflictError):
                self.service.review_timer_effects(event_id, changed, actor="device:phone")
        with self.assertRaises(ConflictError):
            self.service.review_timer_effects(event_id, body, actor="other-device")
        note = self.x.cards.message(event_id + "-effect-review:reply")
        self.assertEqual(1, sum(entry["id"] == note["id"] for entry in self.x.cards.get(note["task"])["entries"]))

    def test_stale_revision_foreign_identity_and_unsupported_outcomes_cannot_write(self):
        event_id = self.promise()
        body = self.body(event_id)
        for changed in (dict(body, revision="old"), dict(body, operation_id="other")):
            with self.assertRaises(ConflictError):
                self.service.review_timer_effects(event_id, changed)
        for outcomes in ({}, {key: "retry" for key in body["outcomes"]}, {"other": "applied"}, []):
            with self.assertRaises(RequestError):
                self.service.review_timer_effects(event_id, dict(body, outcomes=outcomes))
        with self.assertRaises(RequestError):
            self.service.review_timer_effects(event_id, dict(body, actor="spoofed"))
        self.assertEqual("needs_review", self.service.timer_effect_review(event_id)["status"])

    def test_busy_conversations_authority_loss_and_owner_changes_refuse_new_review(self):
        event_id = self.promise()
        body = self.body(event_id)
        self.service._busy["side-review"] = object()
        try:
            with self.assertRaises(ConflictError):
                self.service.review_timer_effects(event_id, body)
        finally:
            self.service._busy.pop("side-review")
        with mock.patch.object(self.service, "permission_problem", return_value="authority offline"):
            with self.assertRaises(ConflictError):
                self.service.review_timer_effects(event_id, body)
        self.x.mesh = mock.Mock(runtime_id="local")
        self.x.mesh.owner_of.return_value = {"owner": "local", "generation": 2}
        with self.assertRaises(ConflictError):
            self.service.review_timer_effects(event_id, body)
        self.assertEqual("needs_review", self.service.timer_effect_review(event_id)["status"])

    def test_a_lost_note_is_repaired_after_restart_without_model_or_effects(self):
        event_id = self.promise()
        body = self.body(event_id)
        self.x._pulse._reminder_card("side-review", "review", event_id + "-effect-review", None)
        write = self.x.cards._journal.record
        def save(value):
            if any(entry.get("id") == event_id + "-effect-review:reply" for entry in value.get("entries", [])):
                raise OSError("projection lost")
            return write(value)
        with mock.patch.object(self.x.cards._journal, "record", side_effect=save):
            with self.assertRaises(UnavailableError):
                self.service.review_timer_effects(event_id, body)
        self.assertEqual("recorded", self.service.timer_effect_review(event_id)["status"])
        self.service.close()
        self.voice = GateTalker()
        self.x, self.store = make_xiaoyou(self.folder, [self.voice])
        self.service = Service(self.x, self.store)
        self.addCleanup(self.service.close)
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no model")):
            self.x._pulse.tick()
            self.assertTrue(until(lambda: not self.service._busy))
            result = self.service.review_timer_effects(event_id, body)
        self.assertEqual("reviewed", result["status"])
        self.assertEqual(2, len(self.x.promises.open()))

    def test_publication_receipt_failure_keeps_handoff_blocked_until_retry(self):
        event_id = self.promise()
        body = self.body(event_id)
        checkpoint = self.x._pulse.checkpoint_timer
        def save(entry, wake):
            if wake["review"]["published"]:
                raise OSError("receipt unavailable")
            return checkpoint(entry, wake)
        with mock.patch.object(self.x._pulse, "checkpoint_timer", side_effect=save):
            with self.assertRaises(UnavailableError):
                self.service.review_timer_effects(event_id, body)
        self.assertTrue(self.x._pulse.unconfirmed("side-review"))
        self.assertIsNotNone(self.x.cards.message(event_id + "-effect-review:reply"))
        self.assertEqual("reviewed", self.service.review_timer_effects(event_id, body)["status"])

    def test_archive_failure_cannot_release_handoff_or_repeat_the_original_effect(self):
        event_id = self.promise()
        body = self.body(event_id)
        with mock.patch.object(self.x._pulse, "archive_timer_review", side_effect=OSError("archive unavailable")):
            with self.assertRaises(UnavailableError):
                self.service.review_timer_effects(event_id, body)
        self.assertEqual("recorded", self.service.timer_effect_review(event_id)["status"])
        self.assertTrue(self.x._pulse.unconfirmed("side-review"))
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no model")):
            self.x._pulse._wake_for_promise(self.x.promises.wakes()[0])
            self.service.review_timer_effects(event_id, body)
        self.assertFalse(self.x._pulse.unconfirmed("side-review"))
        self.assertEqual(2, len(self.x.promises.open()))

    def test_periodic_reminders_keep_their_schedule_and_old_review_identity(self):
        event_id = self.promise(every="day")
        original = self.x.promises.wakes()[0]["due"]
        body = self.body(event_id)
        self.service.review_timer_effects(event_id, body)
        item = next(item for item in self.x.promises.open() if item["every"] == "day")
        self.assertEqual(original + 86400, item["due"])
        self.assertIsNone(item["nudged"])
        self.assertEqual("reviewed", self.service.review_timer_effects(event_id, body)["status"])
        self.assertEqual(original + 86400, self.x.promises.open()[0]["due"])

    def test_legacy_and_damaged_sources_are_visible_but_not_given_invented_outcomes(self):
        item = self.x.promises.track("legacy", due=time.time() - 5)
        self.x.promises.claim_wake(item["id"], "legacy-wake")
        self.x._pulse._marker_state = None
        rows = self.service.timer_reviews()["timers"]
        self.assertEqual(2, len(rows))
        self.assertTrue(all(not row["reviewable"] for row in rows))
        with self.assertRaises(ConflictError):
            self.service.timer_effect_review("legacy-wake")

    def test_paginated_listing_excludes_payloads_and_invalidates_changed_cursors(self):
        first, second = self.promise(), self.promise()
        page = self.service.timer_reviews(limit=1)
        self.assertEqual(1, len(page["timers"]))
        self.assertIsNotNone(page["next_cursor"])
        other = self.service.timer_reviews(cursor=page["next_cursor"], limit=1)
        self.assertEqual({first, second}, {page["timers"][0]["timer_id"], other["timers"][0]["timer_id"]})
        self.assertNotIn("private follow-up", json.dumps(page))
        self.assertNotIn('"effects"', json.dumps(page))
        self.review(first)
        with self.assertRaises(ConflictError):
            self.service.timer_reviews(cursor=page["next_cursor"], limit=1)
        for args in ({"limit": 0}, {"limit": True}, {"cursor": "broken"}):
            with self.assertRaises(RequestError):
                self.service.timer_reviews(**args)

    def test_review_details_stay_off_general_feed_and_sync(self):
        event_id = self.promise()
        self.review(event_id)
        for value in (self.service.feed("side-review", messages=True), self.service.sync("side-review")):
            text = json.dumps(value)
            self.assertNotIn("private follow-up", text)
            self.assertNotIn('"outcomes"', text)
            self.assertNotIn('"wake_reviews"', text)
        self.assertIn("timer_effect_review", self.service.info()["capabilities"])

    def test_http_requires_auth_binds_device_actor_and_rejects_revoked_credentials(self):
        from xiaoyou_runtime.server import make_server
        event_id = self.promise()
        server = make_server(replace(self.x._config, host="127.0.0.1", port=0), self.service)
        server.RequestHandlerClass.log_message = lambda *args: None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:%d" % server.server_address[1]
        def request(path, body=None, token=None):
            headers = {"Content-Type": "application/json", "Connection": "close"}
            if token:
                headers["Authorization"] = "Bearer " + token
            req = urllib.request.Request(base + path, None if body is None else json.dumps(body).encode(), headers)
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, json.load(error)
        path = "/v1/timers/" + event_id + "/effects"
        self.assertEqual(401, request(path)[0])
        self.assertEqual(401, request("/v1/timers/effects")[0])
        token = self.x._config.token
        status, device = request("/v1/devices", {"name": "phone", "kind": "phone"}, token)
        self.assertEqual(200, status, device)
        credential = device["credential"]
        self.assertEqual(200, request("/v1/timers/effects?limit=1", token=credential)[0])
        self.assertEqual(400, request("/v1/timers/effects?limit=bad", token=credential)[0])
        self.assertEqual(200, request(path, token=credential)[0])
        body = self.body(event_id)
        self.assertEqual(400, request(path, dict(body, actor="spoofed"), credential)[0])
        status, result = request(path, body, credential)
        self.assertEqual(200, status)
        self.assertEqual("device:" + device["device"]["id"], result["review"]["actor"])
        self.assertEqual(200, request("/v1/devices/" + device["device"]["id"] + "/revoke", {}, token)[0])
        self.assertEqual(401, request(path, token=credential)[0])

    def test_upkeep_review_publication_resumes_after_restart_without_memory_changes(self):
        event_id = self.upkeep()
        body = self.body(event_id)
        with mock.patch.object(self.x._pulse, "archive_timer_review", side_effect=OSError("archive unavailable")):
            with self.assertRaises(UnavailableError):
                self.service.review_timer_effects(event_id, body)
        self.service.close()
        self.voice = GateTalker()
        self.x, self.store = make_xiaoyou(self.folder, [self.voice])
        self.service = Service(self.x, self.store)
        self.addCleanup(self.service.close)
        with mock.patch.object(self.voice, "run", side_effect=AssertionError("no model")):
            self.x._pulse._maybe_upkeep(time.time() + 3600)
            self.assertEqual("reviewed", self.service.review_timer_effects(event_id, body)["status"])
        self.assertEqual(["private preference"], self.x.memory.texts())

    def test_upkeep_archives_survive_later_normal_cleanup(self):
        event_id = self.upkeep()
        body = self.body(event_id)
        first = self.service.review_timer_effects(event_id, body)
        card = next(card for card in self.x.cards.recent("default") if card["title"] == "retained turns")
        self.x._writer.exchange(card["id"], "later question 1", "answer", turn_id="u3")
        self.x._writer.exchange(card["id"], "later question 2", "answer", turn_id="u4")
        self.x._pulse._maybe_upkeep(time.time() + 3600)
        self.assertEqual("u4", self.x._pulse._upkept)
        self.assertEqual(first, self.service.timer_effect_review(event_id))
        self.assertEqual(1, len(self.x._pulse._marker_state["reviews"]))

    def test_promise_archives_survive_the_next_regular_reminder(self):
        event_id = self.promise(every="day")
        first = self.review(event_id)
        item = next(item for item in self.x.promises.open() if item["every"] == "day")
        with mock.patch("xiaoyou_runtime.proactive.time.time", return_value=item["due"] + 1):
            self.x._pulse.tick()
            self.assertTrue(until(lambda: not self.service._busy))
        self.assertEqual(first, self.service.timer_effect_review(event_id))
        self.assertEqual(1, len(next(item for item in self.x.promises.effect_records()
                                     if item["every"] == "day")["wake_reviews"]))

    def test_a_pruned_note_does_not_reopen_a_completed_review(self):
        event_id = self.promise()
        body = self.body(event_id)
        self.service.review_timer_effects(event_id, body)
        with mock.patch.object(self.x.cards, "message", return_value=None):
            self.assertEqual("reviewed", self.service.review_timer_effects(event_id, body)["status"])
        self.assertFalse(self.x._pulse.unconfirmed("side-review"))

    def test_concurrent_retries_share_one_review_and_one_schedule_advance(self):
        event_id = self.promise(every="day")
        body = self.body(event_id)
        results, errors = [], []
        def review():
            try:
                results.append(self.service.review_timer_effects(event_id, body))
            except Exception as error:
                errors.append(error)
        workers = [threading.Thread(target=review) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(2, len(results))
        self.assertEqual(results[0], results[1])
        note = self.x.cards.message(event_id + "-effect-review:reply")
        self.assertEqual(1, sum(entry["id"] == note["id"] for entry in self.x.cards.get(note["task"])["entries"]))

    def test_a_changed_selection_invalidates_the_original_review_revision(self):
        event_id = self.promise()
        body = self.body(event_id)
        items = self.x.promises._read()
        original = next(item for item in items if item.get("wake"))
        original["wake"]["effects"][0]["args"]["title"] = "changed selection"
        self.x.promises._write(items)
        with self.assertRaises(ConflictError):
            self.service.review_timer_effects(event_id, body)
        self.assertEqual("needs_review", self.service.timer_effect_review(event_id)["status"])

    def test_a_failed_first_checkpoint_does_not_publish_or_record_confirmation(self):
        event_id = self.promise()
        with mock.patch.object(self.x._pulse, "checkpoint_timer", side_effect=OSError("disk full")):
            with self.assertRaises(UnavailableError):
                self.review(event_id)
        self.assertEqual("needs_review", self.service.timer_effect_review(event_id)["status"])
        self.assertIsNone(self.x.cards.message(event_id + "-effect-review:reply"))
        self.assertTrue(self.x._pulse.unconfirmed("side-review"))
        delivery = [card for card in self.x.cards.recent("side-review")
                    if (card.get("changed_by") or {}).get("cause") == event_id + "-effect-review"]
        self.assertEqual(["waiting"], [card["state"] for card in delivery])

    def test_a_committed_review_with_a_lost_receipt_keeps_the_same_identity(self):
        event_id = self.promise()
        body = self.body(event_id)
        checkpoint = self.x._pulse.checkpoint_timer
        def save(entry, wake):
            checkpoint(entry, wake)
            raise OSError("receipt lost after commit")
        with mock.patch.object(self.x._pulse, "checkpoint_timer", side_effect=save):
            with self.assertRaises(UnavailableError):
                self.service.review_timer_effects(event_id, body)
        self.assertEqual("recorded", self.service.timer_effect_review(event_id)["status"])
        self.assertEqual("reviewed", self.service.review_timer_effects(event_id, body)["status"])

    def test_owner_change_after_saving_a_review_cannot_publish_or_release_handoff(self):
        event_id = self.promise()
        body = self.body(event_id)
        guard = self.service._timer_review_guard
        calls = []
        def check(entry):
            calls.append(True)
            if len(calls) == 2:
                self.x.mesh = mock.Mock(runtime_id="local")
                self.x.mesh.owner_of.return_value = {"owner": "local", "generation": 2}
            return guard(entry)
        with mock.patch.object(self.service, "_timer_review_guard", side_effect=check):
            with self.assertRaises(ConflictError):
                self.service.review_timer_effects(event_id, body)
        self.assertEqual("recorded", self.service.timer_effect_review(event_id)["status"])
        self.assertIsNone(self.x.cards.message(event_id + "-effect-review:reply"))
        self.assertTrue(self.x._pulse.unconfirmed("side-review"))

    def test_authority_loss_after_saving_a_review_cannot_publish(self):
        event_id = self.promise()
        body = self.body(event_id)
        with mock.patch.object(self.service, "permission_problem", side_effect=[None, "authority offline"]):
            with self.assertRaises(ConflictError):
                self.service.review_timer_effects(event_id, body)
        self.assertEqual("recorded", self.service.timer_effect_review(event_id)["status"])
        self.assertIsNone(self.x.cards.message(event_id + "-effect-review:reply"))

    def test_reset_history_does_not_prevent_explicit_review_of_the_saved_original_effect(self):
        event_id = self.upkeep()
        self.x.context.clear("default")
        self.review(event_id, "not_applied")
        self.assertEqual(["private preference"], self.x.memory.texts())
        self.assertFalse(self.x._pulse.unconfirmed("default"))
        self.assertEqual(1, len(self.x.context.turns("default")))

    def test_a_published_review_can_finish_archiving_after_its_card_is_pruned(self):
        event_id = self.upkeep()
        body = self.body(event_id)
        with mock.patch.object(self.x._pulse, "archive_timer_review", side_effect=OSError("archive unavailable")):
            with self.assertRaises(UnavailableError):
                self.service.review_timer_effects(event_id, body)
        with mock.patch.object(self.x.cards, "get", return_value=None):
            self.assertEqual("reviewed", self.service.review_timer_effects(event_id, body)["status"])
        self.assertFalse(self.x._pulse.unconfirmed("default"))

    def test_closed_promise_review_survives_ordinary_retention(self):
        event_id = self.promise()
        self.review(event_id)
        source_id = self.service.timer_effect_review(event_id)["source_id"]
        self.x.promises.resolve(source_id)
        for index in range(205):
            self.x.promises.track("later item %d" % index)
        self.assertEqual("reviewed", self.service.timer_effect_review(event_id)["status"])

    def test_outcomes_require_a_real_actor_and_complete_typed_body(self):
        event_id = self.promise()
        body = self.body(event_id)
        for value in (None, [], {key: value for key, value in body.items() if key != "review_id"},
                      dict(body, review_id="bad id"), dict(body, review_id="\ud800")):
            with self.assertRaises(RequestError):
                self.service.review_timer_effects(event_id, value)
        with self.assertRaises(RequestError):
            self.service.review_timer_effects(event_id, body, actor="\ud800")


if __name__ == "__main__":
    unittest.main()
