"""Public helper execution events survive restarts without displacing chat history."""
import unittest
import threading
from unittest import mock

from test_runtime import TempDirCase
from xiaoyu_runtime.world.cards import Cards
from xiaoyu_runtime.events.record import Record
from xiaoyu_runtime.events import message_log


class ActivityTest(TempDirCase):
    def setUp(self):
        super().setUp()
        self.path = self.folder / "cards.json"
        self.cards = Cards(self.path)
        self.record = Record(self.cards)
        self.card = self.record.task_open("default", "Inspect", "working", "codex")["id"]
        self.record.exchange(self.card, "Inspect", "Delegated", turn_id="request", kind="handoff")

    def activity(self, key, phase="tool_start", text="Read file"):
        self.record.activity(self.card, "codex", phase, text, key)

    def events(self, cards=None, after=0):
        return [m for m in (cards or self.cards).sync_since("default", after)[2] if m["kind"] == "activity"]

    def test_events_are_durable_and_independent_of_card_entries_and_short_progress(self):
        for index in range(12):
            self.record.progress(self.card, "step %s" % index)
            self.activity("event-%s" % index, text="step %s" % index)
        self.assertEqual(12, len(self.events()))
        self.assertEqual(2, len(self.cards.get(self.card)["entries"]))
        self.assertEqual(5, len(self.cards.get(self.card)["progress"]))
        restored = self.events(Cards(self.path))
        self.assertEqual(["step %s" % i for i in range(12)], [m["text"] for m in restored])
        self.assertTrue(all(m["scope"] == "task" and m["author"]["name"] == "codex" for m in restored))
        self.assertFalse(self.cards.sync_since("other", 0)[2])

    def test_duplicate_ids_and_public_message_snapshots_keep_one_event(self):
        self.activity("one")
        cursor = self.cards.seq
        self.activity("one")
        self.assertEqual(cursor, self.cards.seq)
        self.activity("public", "message", "Checking")
        at = self.events()[-1]["created_at"]
        self.activity("public", "message", "Checking files now")
        self.assertEqual(2, len(self.events()))
        self.assertEqual(at, self.events()[-1]["created_at"])
        self.assertEqual("Checking files now", self.events(after=cursor)[-1]["text"])

    def test_credentials_and_long_output_are_scrubbed_and_labelled(self):
        self.activity("secret", text="API_KEY=synthetic-private-value\nAuthorization: Bearer hidden-value")
        self.activity("long", "tool_result", "x" * 8000)
        stored = self.path.with_name("cards.log.jsonl").read_text()
        self.assertNotIn("synthetic-private-value", stored)
        self.assertNotIn("hidden-value", stored)
        self.assertIn("REDACTED", stored)
        self.assertLess(len(self.events()[-1]["text"]), 4100)
        self.assertIn("4000", self.events()[-1]["text"])

    def test_activity_retention_never_evicts_conversation_messages(self):
        with mock.patch.object(message_log, "MAX_ACTIVITIES", 5), mock.patch.object(message_log, "MAX_MESSAGES", 3):
            for index in range(12):
                self.activity("event-%s" % index)
        self.assertLessEqual(len(self.events()), 5)
        self.assertIsNotNone(self.cards.message("request:you"))
        self.assertIsNotNone(self.cards.message("request:reply"))
        self.assertGreater(self.cards.log_floor, 0)
        self.assertLessEqual(len(self.events(Cards(self.path))), 5)


class AcpActivityTest(TempDirCase):
    def test_public_chunks_and_tool_results_exclude_thoughts_and_other_sessions(self):
        from test_streaming import dsh_spec
        from xiaoyu_runtime.helpers import dsh_app
        from xiaoyu_runtime.helpers.agents import Control, Job

        def event(kind, session="session-1", **fields):
            return {"method": "session/update", "params": {"sessionId": session,
                    "update": dict(sessionUpdate=kind, **fields)}}

        control = Control()
        events = []
        control.activity = lambda phase, text, key=None: events.append((phase, text, key))
        messages = [event("agent_message_chunk", content={"type": "text", "text": "Checking files"}),
                    event("agent_thought_chunk", content={"type": "text", "text": "private thought"}),
                    event("agent_message_chunk", session="other", content={"type": "text", "text": "other session"}),
                    event("tool_call", toolCallId="call-1", title="Read", rawInput={"path": "src.py"}),
                    event("tool_call_update", toolCallId="call-1", status="in_progress"),
                    event("tool_call_update", toolCallId="call-1", status="completed", content=[
                        {"type": "content", "content": {"type": "text", "text": "file contents"}}]),
                    event("agent_message_chunk", content={"type": "text", "text": "Done"}),
                    {"id": 3, "result": {"stopReason": "end_turn"}}]
        server = mock.Mock()
        server.request.side_effect = [1, 2, 3]
        server.expect.side_effect = [{}, {"sessionId": "session-1"}]

        def next_message(timeout):
            if len(messages) == 3:  # in_progress must not finish the busy tool.
                self.assertTrue(control.quiet()[1])
            return messages.pop(0)

        server.next.side_effect = next_message
        result = dsh_app._turn(server, dsh_spec(self.folder), Job("inspect"), control, mock.Mock())
        self.assertEqual("Checking filesDone", result.text)
        self.assertTrue(any(phase == "tool_result" and "file contents" in text for phase, text, _ in events))
        self.assertTrue(any(phase == "tool_start" and key == "call-1" for phase, _, key in events))
        self.assertFalse(any("private thought" in text or "other session" in text for _, text, _ in events))
        self.assertFalse(control.quiet()[1])


class ActivityMeshTest(TempDirCase):
    def test_relay_retention_separates_activity_from_chat_and_survives_reload(self):
        from xiaoyu_runtime.delivery import mesh
        records = [{"k": "message", "id": "chat-%s" % n, "oseq": n,
                    "data": {"kind": "reply"}} for n in range(1, 4)]
        records += [{"k": "message", "id": "event-%s" % n, "oseq": n,
                     "data": {"kind": "activity"}} for n in range(4, 8)]
        folder = self.folder / "replicas"
        with mock.patch.dict(mesh.LIMITS, message=3), mock.patch.object(mesh, "MAX_ACTIVITIES", 2):
            replicas = mesh.Replicas(folder)
            replicas.store("a" * 32, "epoch", records, 7)
            for store in (replicas, mesh.Replicas(folder)):
                kept = store.since("a" * 32, 0)
                self.assertEqual(3, sum(r["data"]["kind"] == "reply" for r in kept))
                self.assertEqual(2, sum(r["data"]["kind"] == "activity" for r in kept))

    def test_late_projection_cannot_restore_working_after_cancelled(self):
        from test_mesh import Network, Node, Mesh
        from test_runtime import until

        with mock.patch.object(Mesh, "start"):
            network = Network()
            a = Node(self, network, "Mac")
            b = Node(self, network, "Windows")
        card = a.xiaoyu.record.task_open("default", "Inspect", "working", "codex")
        origin, epoch = a.service.runtime_id, a.xiaoyu.cards.epoch

        def block(snapshot):
            return {origin: {"epoch": epoch, "upto": snapshot["seq"], "origin_name": "Mac", "records": [
                {"k": "task", "id": card["id"], "oseq": snapshot["seq"], "data": Cards.task_view(snapshot)}]}}

        old = block(card)
        new = block(a.xiaoyu.record.task(card["id"], state="cancelled"))
        entered, release = threading.Event(), threading.Event()
        original = b.mesh._task

        def delayed(origin, epoch, data):
            if data["state"] == "working":
                entered.set()
                release.wait(3)
            return original(origin, epoch, data)

        with mock.patch.object(b.mesh, "_task", side_effect=delayed):
            first = threading.Thread(target=b.mesh._take, args=(old, origin))
            second = threading.Thread(target=b.mesh._take, args=(new, origin))
            first.start()
            try:
                self.assertTrue(entered.wait(2))
                second.start()
                self.assertTrue(until(lambda: (b.mesh._replicas.get(origin, "task", card["id"]) or {}).get("state") == "cancelled"))
                # Allow an unlocked newer projection to complete before the old one.
                second.join(.1)
            finally:
                release.set()
                first.join(3)
                if second.ident is not None:
                    second.join(3)
        self.assertEqual("cancelled", b.card_from(a, card["id"])["state"])
        b.mesh._apply(origin, epoch, old[origin]["records"])
        self.assertEqual("cancelled", b.card_from(a, card["id"])["state"])

    def test_activity_replicates_without_changing_the_task_or_creating_chat_entries(self):
        from test_mesh import Network, Node
        from test_runtime import until

        network = Network()
        a = Node(self, network, "Mac")
        b = Node(self, network, "Windows", peers=[a.url])
        card = a.xiaoyu.record.task_open("default", "Inspect", "working", "codex")
        self.assertTrue(until(lambda: b.card_from(a, card["id"]) is not None))
        a.xiaoyu.record.activity(card["id"], "codex", "tool_start", "Read src.py", "activity-one")

        def received():
            return [message for message in b.service.sync("default", 0)["messages"]
                    if message.get("kind") == "activity"]

        self.assertTrue(until(received))
        event = received()[0]
        self.assertEqual("Read src.py", event["text"])
        self.assertEqual(a.service.runtime_id, event["source"]["runtime_id"])
        self.assertEqual(b.card_from(a, card["id"])["id"], event["task"])
        self.assertFalse(b.card_from(a, card["id"])["entries"])


if __name__ == "__main__":
    unittest.main()
