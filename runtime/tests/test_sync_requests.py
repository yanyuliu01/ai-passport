"""Current requests share sync without changing its history cursor or exposing private recovery inputs."""
import json
import threading
import unittest

from test_runtime import Scripted, TempDirCase, make_xiaoyou, until, start_runtime
from xiaoyou_runtime.api.service import Service, RequestError


class SyncRequestTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.agent = Scripted("claude")
        self.agent.gate.clear()
        self.x, self.store = make_xiaoyou(self.folder, [self.agent])
        self.service = Service(self.x, self.store)
        self.addCleanup(self.x.close)
        self.addCleanup(self.service.close)
        self.addCleanup(self.agent.gate.set)

    def queued(self, conversation="default"):
        item = self.service.submit("synthetic slow", conversation=conversation)
        self.assertTrue(until(lambda: self.service.get(item["id"])["status"] == "running"))
        return item["id"]

    def test_current_reply_uses_canonical_revisions_without_advancing_a_history_page(self):
        key = self.queued()
        self.service._update(key, partial="visible partial", stage="synthetic progress")
        after = self.x.cards.seq
        page = self.service.sync("default", after=after, requests=[key])
        self.assertEqual(page["seq"], after)
        current = page["requests"][0]
        self.assertEqual(current["partial"], "visible partial")
        self.assertEqual(current["stage"], "synthetic progress")
        self.assertEqual(current["message_snapshot"], self.service.get(key)["message_snapshot"])
        self.agent.gate.set()
        self.assertTrue(until(lambda: self.service.get(key)["status"] == "done"))
        final = self.service.sync("default", requests=[key])["requests"][0]
        self.assertEqual((final["status"], final["brief"], final["mood"]), ("done", "b:synthetic slow", "happy"))
        self.assertTrue(final["message_snapshot"]["messages"][-1]["presentation"]["complete"])

    def test_private_fields_and_foreign_conversations_never_leak(self):
        key = self.queued("side")
        with self.service._changed:
            self.service._messages[key].update(_effects=[{"private": "recovery-input"}], _future_private="recovery-input")
        page = self.service.sync("default", requests=[key, "missing"])
        self.assertEqual(page["requests"], [])
        self.assertEqual(page["requests_missing"], [key, "missing"])
        own = self.service.sync("side", requests=[key])
        self.assertNotIn("recovery-input", json.dumps(own))
        self.assertNotIn("events", own["requests"][0])
        self.assertEqual(self.service.sync("default", all_conversations=True, requests=[key])["requests"][0]["id"], key)

    def test_stage_only_change_wakes_the_existing_subscription(self):
        key = self.queued()
        before = self.x.cards.seq
        result = []
        thread = threading.Thread(target=lambda: result.append(self.service.sync("default", after=before, wait=2, requests=[key])))
        thread.start()
        self.service._update(key, stage="stage without a new body")
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0]["requests"][0]["stage"], "stage without a new body")

    def test_id_validation_budget_and_legacy_shape(self):
        key = self.queued()
        for invalid in ("one", [key] * 33, [False], ["bad/id"]):
            with self.assertRaises(RequestError): self.service.sync("default", requests=invalid)
        self.assertNotIn("requests", self.service.sync("default"))
        self.assertEqual(self.service.sync("default", requests=[key, key])["requests_more"], [])
        # The first item always makes progress, even if it alone exceeds the byte budget.
        with self.service._changed:
            original = self.service._messages[key]
            original["reply"] = "x" * (600 * 1024)
            self.service._messages["second"] = dict(original, id="second")
        page = self.service.sync("default", requests=[key, "second"])
        self.assertEqual([item["id"] for item in page["requests"]], [key])
        self.assertEqual(page["requests_more"], ["second"])

    def test_http_query_negotiation_and_authentication(self):
        import urllib.request
        import urllib.error
        base, config = start_runtime(self, self.folder / "http", backend="echo")
        def call(path, body=None):
            request = urllib.request.Request(base + path,
                data=json.dumps(body).encode() if body is not None else None,
                headers={"Authorization": "Bearer " + config.token, "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=3) as response: return json.load(response)
        accepted = call("/v1/messages", {"text": "synthetic HTTP turn", "client_id":"once"})
        self.assertTrue(accepted["sync_requests_v1"])
        page = call("/v1/sync?requests=" + accepted["id"])
        self.assertEqual(page["requests"][0]["id"], accepted["id"])
        with self.assertRaises(urllib.error.HTTPError) as error: call("/v1/sync?requests=" + ",".join([accepted["id"]] * 33))
        self.assertEqual(error.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(base + "/v1/sync?requests=" + accepted["id"])
        self.assertEqual(error.exception.code, 401)


if __name__ == "__main__": unittest.main()
