"""Quick review tests use synthetic approvals, never live grants or agent tools."""

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xiaoyu_runtime.world.approvals import Approvals
from xiaoyu_runtime.world.quick_review import QuickReview, ReviewConflict


class QuickReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.now = 1000.0
        self.approvals = Approvals()
        self.review = QuickReview(self.approvals, self.folder, lambda: self.now)

    def ask(self, card="c1", conversation="default", **kwargs):
        kwargs.setdefault("context", {"operation": "Read"})
        return self.approvals.ask(card, conversation, "helper", "Read", "private fixture", **kwargs)

    def mode(self, mode="auto_allow", **kwargs):
        snap = self.review.snapshot("default")
        return self.review.configure("default", dict(epoch=snap["epoch"], revision=snap["revision"],
                                                    mode=mode, **kwargs))

    def batch(self, snapshot):
        return self.review.batch("default", dict(epoch=snapshot["epoch"], items=[
            {"id": item["id"], "key": item["key"]} for item in snapshot["pending"]]))

    def test_batch_only_decides_the_displayed_snapshot_and_replays_once(self):
        first = self.ask()
        snapshot = self.review.snapshot("default")
        second = self.ask(card="c2")
        self.assertEqual(self.batch(snapshot)["results"], [{"id": first, "status": "allowed"}])
        self.assertIsNone(self.approvals.get(second)["decision"])
        audit_count = len(self.review.snapshot("default")["audit"])
        self.batch(snapshot)
        self.assertEqual(len(self.review.snapshot("default")["audit"]), audit_count)
        self.assertEqual(self.approvals.get(first)["by"], "phone_batch")

    def test_mode_applies_to_existing_and_future_local_helper_tools_only(self):
        local = self.ask()
        other = self.ask(conversation="work")
        stall = self.ask(kind="stall")
        desktop = self.ask(context=dict(source="desktop", remote_actionable=True, expires_at=2000),
                           responder=lambda decision: self.fail("automatic desktop grant"))
        self.approvals.mirror("mesh1", dict(id="mesh1", card="c3", conversation="default", agent="a",
                              tool="Bash", detail="remote", mesh_origin="other"))
        self.mode()
        future = self.ask(card="c4")
        for aid in (local, future):
            self.assertEqual((self.approvals.get(aid)["decision"], self.approvals.get(aid)["by"]),
                             ("allow", "auto_allow"))
        for aid in (other, stall, desktop, "mesh1"):
            self.assertIsNone(self.approvals.get(aid)["decision"])

    def test_mutations_spending_network_and_unclassified_commands_stay_manual(self):
        self.mode()
        for operation in ("Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch",
                          "mcp__payments__pay", "mcp__mail__send", "Unknown", None):
            aid = self.ask(context={"operation": operation})
            self.assertIsNone(self.approvals.get(aid)["decision"], operation)
        for operation in ("Read", "Glob", "Grep", "LS"):
            aid = self.ask(context={"operation": operation})
            self.assertEqual(self.approvals.get(aid)["decision"], "allow", operation)
        # Existing, unknown adapters do not gain authority from their display label.
        aid = self.approvals.ask("c1", "default", "helper", "Read", "cat /file")
        self.assertIsNone(self.approvals.get(aid)["decision"])

    def test_expiry_disable_and_restart_preserve_original_deadline_and_audit(self):
        self.mode(seconds=60)
        self.now += 61
        expired = self.ask()
        self.assertIsNone(self.approvals.get(expired)["decision"])
        self.assertEqual(self.review.snapshot("default")["mode"], "manual")
        self.mode()
        self.mode("manual")
        self.assertIsNone(self.approvals.get(self.ask())["decision"])
        self.mode()
        audit = self.review.snapshot("default")["audit"]
        restarted = QuickReview(Approvals(), self.folder, lambda: self.now)
        self.assertEqual(restarted.snapshot("default")["mode"], "auto_allow")
        self.assertEqual(restarted.snapshot("default")["expires_at"], self.now + 3600)
        self.assertEqual(restarted.snapshot("default")["audit"][:-1], audit)
        self.assertEqual(restarted.snapshot("default")["audit"][-1]["action"], "restored")
        self.assertNotEqual(restarted.epoch, self.review.epoch)

    def test_persistent_mode_survives_close_and_restart_until_explicitly_disabled(self):
        snapshot = self.mode(seconds=0)
        self.assertTrue(snapshot["persistent"])
        self.assertEqual(snapshot["expires_at"], 0)
        self.review.close()
        self.now += 400 * 86400
        approvals = Approvals()
        restarted = QuickReview(approvals, self.folder, lambda: self.now)
        self.assertTrue(restarted.snapshot("default")["persistent"])
        aid = approvals.ask("c1", "default", "helper", "Read", "fixture", context={"operation": "Read"})
        self.assertEqual(approvals.get(aid)["decision"], "allow")
        old_epoch = snapshot["epoch"]
        with self.assertRaises(ReviewConflict):
            restarted.configure("default", dict(epoch=old_epoch, revision=0, mode="manual"))
        snap = restarted.snapshot("default")
        restarted.configure("default", dict(epoch=snap["epoch"], revision=snap["revision"], mode="manual"))
        again = QuickReview(Approvals(), self.folder, lambda: self.now)
        self.assertEqual(again.snapshot("default")["mode"], "manual")

    def test_custom_deadline_is_not_extended_by_restart_and_can_be_reconfigured(self):
        original = self.mode(seconds=86400)
        self.now += 100
        restarted = QuickReview(Approvals(), self.folder, lambda: self.now)
        snap = restarted.snapshot("default")
        self.assertEqual(snap["expires_at"], original["expires_at"])
        self.assertEqual(snap["duration_seconds"], 86400)
        self.assertFalse(snap["persistent"])
        self.now = original["expires_at"]
        expired = QuickReview(Approvals(), self.folder, lambda: self.now)
        self.assertEqual(expired.snapshot("default")["mode"], "manual")
        updated = self.mode(seconds=0)
        self.assertTrue(updated["persistent"])
        timed = self.mode(seconds=120)
        self.assertFalse(timed["persistent"])
        self.assertEqual(timed["expires_at"], self.now + 120)

    def test_policies_are_scoped_and_do_not_depend_on_audit_retention(self):
        self.mode(seconds=0)
        for index in range(110): self.ask(card="c%d" % index)
        restarted = QuickReview(Approvals(), self.folder, lambda: self.now)
        self.assertEqual(restarted.snapshot("default")["mode"], "auto_allow")
        self.assertEqual(restarted.snapshot("work")["mode"], "manual")
        self.assertLessEqual(len(restarted._audit), 200)
        self.assertFalse(any(event["action"] == "auto_allow" for event in restarted._audit))

    def test_legacy_audit_and_invalid_stored_policies_never_create_grants(self):
        self.mode()
        old = {"audit": self.review.snapshot("default")["audit"]}
        path = self.folder / "approval-review.json"
        path.write_text(json.dumps(old))
        legacy = QuickReview(Approvals(), self.folder, lambda: self.now)
        self.assertEqual(legacy.snapshot("default")["mode"], "manual")
        self.assertEqual(legacy.snapshot("default")["audit"], old["audit"])
        for policy in (None, [], {"seconds": True, "expires_at": 0},
                       {"seconds": 0, "expires_at": 1}, {"seconds": 60, "expires_at": float("nan")},
                       {"seconds": 60, "expires_at": 0}, {"seconds": 31536001, "expires_at": 2000}):
            path.write_text(json.dumps(dict(old, policy_version=1, policies={"default": policy})))
            invalid = QuickReview(Approvals(), self.folder, lambda: self.now)
            self.assertEqual(invalid.snapshot("default")["mode"], "manual")
            self.assertTrue(invalid.snapshot("default")["error"])

    def test_failed_durable_write_cannot_restore_previous_grant_after_restart(self):
        self.mode(seconds=0)
        with patch("xiaoyu_runtime.world.quick_review.os.replace", side_effect=OSError("disk full")):
            aid = self.ask()
        self.assertIsNone(self.approvals.get(aid)["decision"])
        self.assertTrue((self.folder / "approval-review.disabled").exists())
        restarted = QuickReview(Approvals(), self.folder, lambda: self.now)
        self.assertEqual(restarted.snapshot("default")["mode"], "manual")
        snap = restarted.snapshot("default")
        restarted.configure("default", dict(epoch=snap["epoch"], revision=snap["revision"],
                                              mode="auto_allow", seconds=0))
        self.assertFalse((self.folder / "approval-review.disabled").exists())
        self.assertTrue(QuickReview(Approvals(), self.folder, lambda: self.now).snapshot("default")["persistent"])

    def test_old_epoch_and_changed_revision_cannot_enable_a_mode(self):
        old = self.review.snapshot("default")
        self.mode()
        with self.assertRaises(ReviewConflict):
            self.review.configure("default", dict(epoch=old["epoch"], revision=old["revision"], mode="manual"))
        with self.assertRaises(ReviewConflict):
            self.review.batch("default", dict(epoch="old-runtime", items=[]))

    def test_fingerprints_changed_or_other_conversation_cannot_approve(self):
        first = self.ask()
        snapshot = self.review.snapshot("default")
        snapshot["pending"][0]["key"] = "forged"
        self.assertEqual(self.batch(snapshot)["results"][0]["status"], "stale")
        other = self.ask(conversation="work")
        work = self.review.snapshot("work")
        self.assertEqual(self.batch(work)["results"][0]["status"], "stale")
        self.assertIsNone(self.approvals.get(first)["decision"])
        self.assertIsNone(self.approvals.get(other)["decision"])

    def test_reused_id_after_restart_cannot_reuse_a_displayed_batch(self):
        self.ask()
        old = self.review.snapshot("default")
        fresh_approvals = Approvals()
        fresh = QuickReview(fresh_approvals, self.folder, lambda: self.now)
        aid = fresh_approvals.ask("c1", "default", "helper", "Read", "private fixture",
                                  context={"operation": "Read"})
        self.assertEqual(aid, old["pending"][0]["id"])
        with self.assertRaises(ReviewConflict):
            fresh.batch("default", dict(epoch=old["epoch"], items=old["pending"]))
        result = fresh.batch("default", dict(epoch=fresh.epoch, items=old["pending"]))
        self.assertEqual(result["results"][0]["status"], "stale")
        self.assertIsNone(fresh_approvals.get(aid)["decision"])

    def test_writes_are_manual_until_owner_approves_the_displayed_batch(self):
        self.mode()
        writes = [self.ask(context={"operation": operation}) for operation in ("Bash", "Write", "Edit")]
        snapshot = self.review.snapshot("default")
        self.assertEqual([item["id"] for item in snapshot["pending"]], writes)
        self.assertTrue(all(self.approvals.get(aid)["decision"] is None for aid in writes))
        self.assertEqual([item["status"] for item in self.batch(snapshot)["results"]], ["allowed"] * 3)
        self.assertTrue(all(self.approvals.get(aid)["by"] == "phone_batch" for aid in writes))

    def test_desktop_live_channel_only_and_stale_desktop_is_not_reported_allowed(self):
        calls = []
        live = self.ask(context=dict(source="desktop", remote_actionable=True, expires_at=2000),
                        responder=lambda decision: calls.append(decision) or True)
        stale = self.ask(context=dict(source="desktop", remote_actionable=True, expires_at=2000),
                         responder=lambda decision: False)
        notify = self.ask(context=dict(source="desktop", remote_actionable=False))
        expired = self.ask(context=dict(source="desktop", remote_actionable=True, expires_at=999))
        snapshot = self.review.snapshot("default")
        self.assertEqual([item["id"] for item in snapshot["pending"]], [live, stale])
        self.assertEqual([item["status"] for item in self.batch(snapshot)["results"]], ["allowed", "stale"])
        self.assertEqual(calls, ["allow"])
        for aid in (notify, expired):
            self.assertIsNone(self.approvals.get(aid)["decision"])

    def test_manual_denial_wins_and_parallel_batch_resolves_exactly_once(self):
        first = self.ask()
        snapshot = self.review.snapshot("default")
        self.approvals.answer(first, "deny")
        self.assertEqual(self.batch(snapshot)["results"][0]["status"], "denied")
        aid = self.ask(context=dict(source="desktop", remote_actionable=True, expires_at=2000),
                       responder=lambda decision: calls.append(decision) or True)
        calls = []
        snapshot = self.review.snapshot("default")
        threads = [threading.Thread(target=lambda: self.batch(snapshot)) for _ in range(5)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(2)
        self.assertEqual(calls, ["allow"])
        self.assertEqual(self.approvals.get(aid)["decision"], "allow")

    def test_audit_storage_failure_does_not_silently_grant(self):
        self.mode()
        with patch.object(self.review, "_event", side_effect=OSError("disk full")):
            aid = self.ask()
        self.assertIsNone(self.approvals.get(aid)["decision"])
        self.assertEqual(self.review.snapshot("default")["mode"], "manual")
        self.assertTrue(self.review.snapshot("default")["error"])
        self.assertNotIn("private fixture", (self.folder / "approval-review.json").read_text())
        self.assertEqual((self.folder / "approval-review.json").stat().st_mode & 0o777, 0o600)

    def test_result_write_failure_retains_truthful_allowed_state_and_stops_future_grants(self):
        self.mode()
        event = self.review._event
        count = [0]
        def write(*args, **kwargs):
            count[0] += 1
            if count[0] == 2: raise OSError("disk full after decision")
            return event(*args, **kwargs)
        with patch.object(self.review, "_event", side_effect=write):
            aid = self.ask()
        self.assertEqual(self.approvals.get(aid)["decision"], "allow")
        self.assertEqual(self.review.snapshot("default")["mode"], "manual")
        self.assertEqual([row["action"] for row in self.review.snapshot("default")["audit"]][-2:],
                         ["requested", "storage_failed"])
        self.assertIsNone(self.approvals.get(self.ask())["decision"])

    def test_batch_storage_failure_also_revokes_an_enabled_automatic_grant(self):
        self.mode()
        self.ask(context=dict(source="desktop", remote_actionable=True, expires_at=2000),
                 responder=lambda decision: self.fail("must record intent before allowing"))
        snapshot = self.review.snapshot("default")
        with patch("xiaoyu_runtime.world.quick_review.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError): self.batch(snapshot)
        self.assertEqual(self.review.snapshot("default")["mode"], "manual")

    def test_invalid_mode_and_batch_have_no_effect(self):
        for seconds in (True, -1, 1, 59, 31536001, "3600", None):
            with self.assertRaises(ValueError): self.mode(seconds=seconds)
        with self.assertRaises(ValueError): self.mode("unknown")
        for items in ([], "all", [1], [{"id": "a1"}], [{"id": "x", "key": "k"}] * 101):
            with self.assertRaises(ValueError):
                self.review.batch("default", dict(epoch=self.review.epoch, items=items))
        self.assertEqual(self.review.snapshot("default")["mode"], "manual")

    def test_lifecycle_events_log_changes_expiry_restore_and_storage_failure(self):
        events = []
        self.review = QuickReview(self.approvals, self.folder, lambda: self.now, event_sink=events.append)
        self.mode(seconds=0)
        self.mode(seconds=60)
        self.mode("manual")
        self.mode(seconds=60)
        self.now += 60
        self.review.snapshot("default")
        self.assertEqual([event.get("change", event["action"]) for event in events],
                         ["enabled", "duration_changed", "disabled", "enabled", "expired"])
        self.mode(seconds=0)
        restored = QuickReview(Approvals(), self.folder, lambda: self.now, event_sink=events.append)
        self.assertEqual(events[-1]["action"], "restored")
        with patch.object(restored, "_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                restored._event("default", "requested", id="a1", card="c1", agent="helper")
        self.assertEqual(events[-1]["action"], "storage_failed")
        self.assertEqual(events[-1]["reason"], "storage_error")
        self.assertNotIn("private fixture", json.dumps(events))

    def test_service_restores_policy_before_recovered_work_can_ask_for_a_tool(self):
        from test_runtime import make_xiaoyu, Scripted
        from xiaoyu_runtime.api.service import Service
        xiaoyu, store = make_xiaoyu(self.folder / "startup", [Scripted("helper")])
        grant = QuickReview(xiaoyu.approvals, store.state_dir)
        snap = grant.snapshot("default")
        grant.configure("default", dict(epoch=snap["epoch"], revision=snap["revision"],
                                         mode="auto_allow", seconds=0))
        grant.close()
        decisions = []
        def recover(service):
            aid = service._xiaoyu.approvals.ask("c1", "default", "helper", "Read", "fixture",
                                                 context={"operation": "Read"})
            decisions.append(service._xiaoyu.approvals.get(aid)["decision"])
        with patch.object(Service, "_recover", recover):
            service = Service(xiaoyu, store)
        self.addCleanup(service.close)
        self.assertEqual(decisions, ["allow"])
        self.assertEqual([row["fields"]["action"] for row in service.events.all()],
                         ["restored", "requested", "allowed"])


class QuickReviewHttpTests(unittest.TestCase):
    def setUp(self):
        from test_runtime import write_config, config_module, factory, Xiaoyu, Store, Service, TOKEN
        from xiaoyu_runtime.api.server import make_server
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = config_module.load(write_config(Path(self.temp.name)), {"XIAOYU_PORT": "8765"})
        store = Store(self.config.state_dir)
        xiaoyu = Xiaoyu(self.config, [factory.create(spec) for spec in self.config.agents], store)
        self.service = Service(xiaoyu, store)
        self.addCleanup(self.service.close)
        # Config is frozen; a private test port never touches the running Runtime.
        from dataclasses import replace
        server = make_server(replace(self.config, host="127.0.0.1", port=0), self.service)
        server.RequestHandlerClass.log_message = lambda *args: None
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % server.server_address[1]
        self.approvals = xiaoyu.approvals
        self.token = TOKEN

    def call(self, path, body=None, token=True):
        request = urllib.request.Request(self.base + path,
                  data=json.dumps(body).encode() if body is not None else None)
        if token: request.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_authenticated_batch_sync_and_mode_roundtrip(self):
        self.assertEqual(self.call("/v1/review", token=False)[0], 401)
        self.assertEqual(self.call("/v1/review/mode", {}, token=False)[0], 401)
        self.assertEqual(self.call("/v1/review/batch", {}, token=False)[0], 401)
        aid = self.approvals.ask("c1", "default", "helper", "Bash", "fixture")
        status, snapshot = self.call("/v1/review")
        self.assertEqual(status, 200)
        body = dict(epoch=snapshot["epoch"], items=snapshot["pending"])
        self.assertEqual(self.call("/v1/review/batch", body)[1]["results"], [{"id": aid, "status": "allowed"}])
        delta = self.approvals.since("default", 0)
        self.assertEqual((delta[-1]["status"], delta[-1]["by"]), ("allowed", "phone_batch"))
        status, synced = self.call("/v1/sync?after=0&wait=0")
        self.assertEqual(status, 200)
        decision = next(item for item in synced["approvals"] if item["id"] == aid)
        self.assertEqual((decision["status"], decision["by"]), ("allowed", "phone_batch"))
        mode = dict(epoch=snapshot["epoch"], revision=snapshot["revision"], mode="auto_allow")
        self.assertEqual(self.call("/v1/review/mode", mode)[1]["mode"], "auto_allow")
        self.assertEqual(self.call("/v1/review/mode", mode)[0], 409)
        self.assertEqual(self.call("/v1/review/batch", dict(epoch="old", items=[]))[0], 409)
        self.assertEqual(self.call("/v1/review/batch", dict(epoch=snapshot["epoch"], items=[]))[0], 400)
        self.assertEqual(self.call("/v1/review?conversation=bad%20name")[0], 400)

    def test_configurable_and_persistent_modes_over_http(self):
        snapshot = self.call("/v1/review")[1]
        self.assertTrue(snapshot["duration_options"]["survives_restart"])
        for seconds in (0, 900, 86400, 604800, 31536000):
            status, snapshot = self.call("/v1/review/mode", dict(epoch=snapshot["epoch"],
                    revision=snapshot["revision"], mode="auto_allow", seconds=seconds))
            self.assertEqual(status, 200)
            self.assertEqual(snapshot["persistent"], seconds == 0)
            self.assertEqual(snapshot["duration_seconds"], seconds)
        self.assertEqual(self.call("/v1/review/mode", dict(epoch=snapshot["epoch"],
            revision=snapshot["revision"], mode="auto_allow", seconds=31536001))[0], 400)

    def test_operational_events_are_registered_persisted_and_sanitized(self):
        from xiaoyu_runtime.iterate.events import EventStore
        snapshot = self.call("/v1/review")[1]
        for seconds in (0, 5400):
            _, snapshot = self.call("/v1/review/mode", dict(epoch=snapshot["epoch"],
                revision=snapshot["revision"], mode="auto_allow", seconds=seconds))
        self.approvals.ask("c1", "default", "helper", "Read", "private command with secret",
                           context={"operation": "Read"})
        self.assertEqual(self.call("/v1/review/mode", dict(epoch=snapshot["epoch"], revision=-1,
                         mode="auto_allow", seconds=0))[0], 409)
        rows = EventStore(self.config.state_dir).all()
        self.assertTrue(all(row["type"] == "review" for row in rows))
        self.assertEqual([row["fields"]["action"] for row in rows],
                         ["enabled", "duration_changed", "requested", "allowed", "rejected"])
        self.assertEqual([row["fields"]["seconds"] for row in rows[:2]], [0, 5400])
        self.assertEqual(rows[-1]["fields"]["reason"], "conflict")
        self.assertEqual(rows[-1]["fields"]["result"], "failed")
        self.assertEqual(rows[0]["client"]["kind"], "runtime")
        self.assertTrue(rows[0]["origin"])
        self.assertNotIn("private command", json.dumps(rows))
        self.assertNotIn("secret", json.dumps(rows))
        self.assertIn("review", [kind["name"] for kind in self.call("/v1/info")[1]["events"]["types"]])

    def test_permission_gate_passes_a_typed_read_without_changing_manual_gate_protocol(self):
        from xiaoyu_runtime.world.approvals import Gate
        gate = Gate(self.approvals)
        self.addCleanup(gate.close)
        env = gate.open("c-read", "default", "claude")
        snapshot = self.call("/v1/review")[1]
        self.call("/v1/review/mode", dict(epoch=snapshot["epoch"], revision=snapshot["revision"],
                                        mode="auto_allow"))
        request = urllib.request.Request(env["XIAOYU_GATE_URL"],
                  data=json.dumps(dict(tool_name="Read", input=dict(file_path="/fixture.txt"))).encode())
        request.add_header("X-Xiaoyu-Key", env["XIAOYU_GATE_KEY"])
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(json.load(response)["decision"], "allow")
        item = self.approvals.since("default", 0)[-1]
        self.assertEqual((item["operation"], item["by"]), ("Read", "auto_allow"))


if __name__ == "__main__":
    unittest.main()
