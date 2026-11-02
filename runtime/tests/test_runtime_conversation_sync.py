"""End-to-end Runtime chat history across owner routing and peer recovery."""

from test_ownership import Mesh, Network, Node, Service
from xiaoyu_runtime.delivery.mesh import Peer
from test_runtime import TempDirCase, until
from unittest import mock


class RuntimeConversationSyncTests(TempDirCase):
    def test_known_remote_owner_marks_placeholder_before_it_can_be_exported(self):
        network = Network()
        mac = Node(self, network, "Mac", home=True)
        win = Node(self, network, "Windows", peers=[mac.url])
        self.assertTrue(until(lambda: win.service.owner("default")["owner"] == mac.id, 15))
        with mock.patch.object(win.service, "_line_up"):
            sent = win.service.submit("queued Windows message", client_id="queued-win")
            message = win.xiaoyu.cards.message(sent["id"] + ":you")
            self.assertEqual(mac.id, message.get("relay_to"))
            self.assertNotIn(sent["id"] + ":you", [m["id"] for m in win.xiaoyu.cards.own_since(0)[2]])
            with mock.patch.object(win.xiaoyu.cards._log, "_put", wraps=win.xiaoyu.cards._log._put) as put:
                win.service._update(sent["id"], status="running")
            self.assertFalse(any(call.args[0].get("id") == message["id"] for call in put.call_args_list),
                             "an unchanged route must not add a message-log write")

    def test_owner_change_updates_pending_placeholder_before_execution(self):
        network = Network()
        mac = Node(self, network, "Mac", home=True)
        win = Node(self, network, "Windows", peers=[mac.url])
        self.assertTrue(until(lambda: win.service.owner("default")["owner"] == mac.id, 15))
        with mock.patch.object(win.service, "_line_up"):
            sent = win.service.submit("owner changed", client_id="owner-changed")
        view = {"owner": win.id, "generation": 2, "name": "Windows", "conflict": False}
        with mock.patch.object(win.mesh, "owner_of", return_value=view):
            with win.service._changed:
                self.assertTrue(win.service._route(win.service._messages[sent["id"]]))
            win.service._update(sent["id"], status="running")
        question = win.xiaoyu.cards.message(sent["id"] + ":you")
        self.assertNotIn("relay_to", question)
        self.assertIn(question["id"], [m["id"] for m in win.xiaoyu.cards.own_since(0)[2]])

    def test_restart_reconciles_route_saved_before_placeholder_update(self):
        network = Network()
        mac = Node(self, network, "Mac", home=True)
        win = Node(self, network, "Windows", peers=[mac.url])
        self.assertTrue(until(lambda: win.service.owner("default")["owner"] == mac.id, 15))
        with mock.patch.object(win.service, "_line_up"):
            sent = win.service.submit("recover route", client_id="recover-route")
        new_owner = "c" * 32
        with win.service._write_lock:
            with win.service._changed:
                win.service._messages[sent["id"]]["relay_to"] = new_owner
                captured = win.service._capture()
            win.service._flush(captured, strict=True)
        self.assertEqual(mac.id, win.xiaoyu.cards.message(sent["id"] + ":you")["relay_to"])
        win.close()
        with mock.patch.object(Mesh, "start"), mock.patch.object(Service, "_line_up"):
            restored = Node(self, network, "Windows")
        self.assertEqual(new_owner, restored.xiaoyu.cards.message(sent["id"] + ":you")["relay_to"])

    def test_route_projection_failure_does_not_launch_or_forward_work(self):
        network = Network()
        mac = Node(self, network, "Mac", home=True)
        win = Node(self, network, "Windows", peers=[mac.url])
        self.assertTrue(until(lambda: win.service.owner("default")["owner"] == mac.id, 15))
        with mock.patch.object(win.service, "_line_up"):
            sent = win.service.submit("route fault", client_id="route-fault")
        view = {"owner": win.id, "generation": 2, "name": "Windows", "conflict": False}
        with mock.patch.object(win.mesh, "owner_of", return_value=view):
            with win.service._changed:
                self.assertTrue(win.service._route(win.service._messages[sent["id"]]))
            with mock.patch.object(win.xiaoyu.cards._log, "_append", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    win.service._update(sent["id"], strict=True, status="running")
        self.assertEqual([], win.agent.texts)
        self.assertFalse(any(kind == "command" for kind, _ in win.mesh._outbox))
        self.assertEqual(mac.id, win.xiaoyu.cards.message(sent["id"] + ":you")["relay_to"])

    def test_both_runtime_conversations_reach_both_contexts_once(self):
        network = Network()
        mac = Node(self, network, "Mac", home=True)
        win = Node(self, network, "Windows", peers=[mac.url])
        self.assertTrue(until(lambda: win.service.owner("default")["owner"] == mac.id, 15))
        windows_turn = win.service.submit("question from Windows", client_id="windows-turn")
        self.assertTrue(until(lambda: win.get(windows_turn["id"]).get("status") == "done", 20))
        mac_turn = mac.service.submit("question from Mac", client_id="mac-turn")
        self.assertTrue(until(lambda: mac.get(mac_turn["id"]).get("status") == "done", 20))

        def pairs(node):
            return [(turn["text"], turn["reply"]) for turn in node.xiaoyu.context.history("default")]

        wanted = ["question from Windows", "question from Mac"]
        def converged():
            left, right = pairs(mac), pairs(win)
            return all(sum(text == name for text, _ in left) == 1
                       and sum(text == name for text, _ in right) == 1
                       and next(reply for text, reply in left if text == name)
                       == next(reply for text, reply in right if text == name)
                       for name in wanted)
        self.assertTrue(until(converged, 20),
                        "both owner and peer must retain each Runtime chat turn exactly once")
        for node in (mac, win):
            visible = node.service.sync("default", 0)["messages"]
            for text in wanted:
                self.assertEqual(1, sum(message.get("text") == text for message in visible))
        self.assertEqual(win.agent.texts, [])

    def test_old_import_and_remote_canonical_turn_share_one_identity(self):
        network = Network()
        mac = Node(self, network, "Mac")
        win = Node(self, network, "Windows", peers=[mac.url])
        card = win.xiaoyu.record.task_open("default", "Windows Runtime turn", "done", "claude")
        win.xiaoyu.record.exchange(card["id"], "old Windows question", "old Windows reply", turn_id="old-turn")
        self.assertTrue(until(lambda: any(turn["text"] == "old Windows question"
            for turn in mac.xiaoyu.context.history("default")), 15))
        self.assertEqual("old-turn", next(turn["id"] for turn in mac.xiaoyu.context.history("default")
                                          if turn["text"] == "old Windows question"))
        mac.store.legacy_transcript.offer("default", [{"id": "old-turn", "text": "old Windows question",
                                                     "reply": "old Windows reply", "at": 1}])
        found = [turn for turn in mac.xiaoyu.context.history("default") if turn["text"] == "old Windows question"]
        self.assertEqual(1, len(found))
        self.assertEqual([], mac.xiaoyu.context.history("unrelated-side"))
        self.assertIn("old Windows reply", mac.xiaoyu.context.recall("default", "Windows question"))
        self.assertNotIn("old Windows reply", mac.xiaoyu.context.recall("unrelated-side", "Windows question"))

    def test_equal_remote_ids_from_distinct_computers_are_not_merged(self):
        network = Network()
        mac = Node(self, network, "Mac")
        first = Node(self, network, "Windows", peers=[mac.url])
        second = Node(self, network, "Linux", peers=[mac.url])
        for node, text in ((first, "first question"), (second, "second question")):
            card = node.xiaoyu.record.task_open("default", "task", "done", "claude")
            node.xiaoyu.record.exchange(card["id"], text, "answer", turn_id="same-remote-id")
        def questions():
            return {turn["text"] for turn in mac.xiaoyu.context.history("default")}
        self.assertTrue(until(lambda: {"first question", "second question"} <= questions(), 20))
        mac.store.legacy_transcript.offer("default", [{"id": "same-remote-id", "text": "unknown source",
                                                     "reply": "answer", "at": 1}])
        self.assertTrue({"first question", "second question", "unknown source"} <= questions())

    def test_offline_owner_recovers_one_shared_turn_without_duplicate_execution(self):
        network = Network()
        mac = Node(self, network, "Mac", home=True)
        win = Node(self, network, "Windows", peers=[mac.url])
        self.assertTrue(until(lambda: win.service.owner("default")["owner"] == mac.id, 15))
        network.down.add(mac.url)
        for node in (mac, win):
            peer = node.mesh._peers.get(mac.id)
            if peer is not None:
                peer.seen = peer.heard = 0
        sent = win.service.submit("offline question", client_id="offline-chat")
        self.assertTrue(until(lambda: sent["id"] in win.service._parked, 10))
        self.assertEqual([], win.agent.texts)
        network.down.discard(mac.url)
        self.assertTrue(until(lambda: win.get(sent["id"]).get("status") == "done", 30))
        def one_each():
            return all(sum(t["text"] == "offline question" for t in node.xiaoyu.context.history("default")) == 1
                       for node in (mac, win))
        self.assertTrue(until(one_each, 15))
        self.assertEqual(1, sum(text == "offline question" for text in mac.agent.texts))
        self.assertEqual([], win.agent.texts)

    def test_received_but_unprojected_runtime_history_is_recalled_on_demand(self):
        network = Network()
        with mock.patch.object(Mesh, "start"):
            mac = Node(self, network, "Mac")
            win = Node(self, network, "Windows")
        peer = Peer(win.id)
        peer.name = "Windows"
        mac.mesh._peers[win.id] = peer
        data = {"id": "missing:reply", "conversation": "default", "scope": "main", "status": "final",
                "author": {"kind": "xiaoyou", "name": "xiaoyou"}, "source": {"type": "runtime"},
                "text": "previous anemone-code", "created_at": 1}
        external = dict(data, id="external:reply", source={"type": "external"}, text="external anemone-code")
        mac.mesh._replicas.store(win.id, "epoch", [{"k": "message", "id": data["id"], "oseq": 1, "data": data},
                                                   {"k": "message", "id": external["id"], "oseq": 2, "data": external}], 2)
        with mock.patch.object(mac.xiaoyu.context, "_replica_recall", wraps=mac.xiaoyu.context._replica_recall) as lookup:
            mac.xiaoyu.context.window("default", "reader", query="anemone-code")
            lookup.assert_not_called()
            found = mac.xiaoyu.context.recall("default", "anemone-code")
            lookup.assert_called_once()
        self.assertIn("previous anemone-code", found)
        self.assertIn("Windows", found)
        self.assertNotIn("external anemone-code", found)
        self.assertNotIn("previous anemone-code", mac.xiaoyu.context.recall("side", "anemone-code"))
        mac.store.legacy_transcript.offer("default", [{"id": "missing", "text": "earlier question",
                                                       "reply": "previous anemone-code", "at": 1}])
        self.assertEqual(1, mac.xiaoyu.context.recall("default", "anemone-code").count("previous anemone-code"))
        self.assertIn("previous anemone-code", mac.xiaoyu.context.recall("default", "Windows"))
        card = mac.xiaoyu.record.task_open("default", "reset marker", "done", "claude")
        mac.xiaoyu.record.exchange(card["id"], "local question", "local answer", turn_id="local")
        self.assertTrue(mac.xiaoyu.context.clear("default"))
        self.assertNotIn("previous anemone-code", mac.xiaoyu.context.recall("default", "anemone-code"))

    def test_replica_recall_ignores_unapplied_and_fenced_records(self):
        network = Network()
        with mock.patch.object(Mesh, "start"):
            mac = Node(self, network, "Mac")
            win = Node(self, network, "Windows")
        data = {"id": "pending:reply", "conversation": "default", "scope": "main", "status": "final",
                "author": {"kind": "xiaoyou", "name": "xiaoyou"}, "source": {"type": "runtime"},
                "text": "unapplied jasmine-code", "created_at": 1}
        hidden = dict(data, id="worker:reply", text="delegated jasmine-code", task="worker-task")
        task = {"id": "worker-task", "delegated_from": {"runtime": mac.id}}
        mac.mesh._replicas.store(win.id, "epoch", [{"k": "message", "id": data["id"], "oseq": 1, "data": data},
            {"k": "task", "id": "worker-task", "oseq": 2, "data": task},
            {"k": "message", "id": hidden["id"], "oseq": 3, "data": hidden}], 3, track=True)
        self.assertNotIn("unapplied jasmine-code", mac.xiaoyu.context.recall("default", "jasmine-code"))
        mac.mesh._replicas.mark_applied(win.id, "epoch", 3)
        with mock.patch.object(mac.mesh, "_fenced", return_value=True):
            self.assertNotIn("unapplied jasmine-code", mac.xiaoyu.context.recall("default", "jasmine-code"))
        result = mac.xiaoyu.context.recall("default", "jasmine-code")
        self.assertIn("unapplied jasmine-code", result)
        self.assertNotIn("delegated jasmine-code", result)
