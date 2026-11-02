"""Fault injection for application progress and command outcome recovery."""
import threading
import time
from pathlib import Path
from unittest import mock

from test_mesh import Mesh, Network, Node
from test_runtime import TempDirCase, until
from xiaoyu_runtime.world.cards import Cards


class ProjectionRecoveryTests(TempDirCase):
    def nodes(self):
        network = Network()
        with mock.patch.object(Mesh, "start"):
            sender = Node(self, network, "Mac")
            receiver = Node(self, network, "Windows")
        return network, sender, receiver

    @staticmethod
    def task_block(sender):
        card = sender.xiaoyu.record.task_open("default", "recovery task", "working", "claude")
        origin = sender.service.runtime_id
        return card, origin, {origin: {"epoch": sender.xiaoyu.cards.epoch, "upto": card["seq"],
            "origin_name": "Mac", "records": [{"k": "task", "id": card["id"],
                "oseq": card["seq"], "data": Cards.task_view(card)}]}}

    def test_projection_failure_does_not_ack_and_same_batch_recovers(self):
        _, sender, receiver = self.nodes()
        card, origin, block = self.task_block(sender)
        with mock.patch.object(receiver.mesh, "_task", side_effect=OSError("projection unavailable")):
            with self.assertRaises(OSError):
                receiver.mesh._take(block, origin)
        self.assertEqual(receiver.mesh.cursors()[origin]["seq"], 0)
        receiver.mesh._take(block, origin)
        self.assertEqual(receiver.mesh.cursors()[origin]["seq"], card["seq"])
        self.assertIsNotNone(receiver.card_from(sender, card["id"]))

    def test_pending_records_survive_capacity_pressure_and_restart(self):
        from xiaoyu_runtime.delivery import mesh as mesh_module
        folder = self.folder / "replicas-capacity"
        peer = "a" * 32
        records = [{"k": "command", "id": str(i), "oseq": i, "data": {}} for i in (1, 2)]
        with mock.patch.dict(mesh_module.LIMITS, command=1):
            replicas = mesh_module.Replicas(folder)
            replicas.store(peer, "epoch", records, 2, track=True)
            restored = mesh_module.Replicas(folder)
            self.assertEqual(len(restored.since(peer, 0)), 2)
            restored.mark_applied(peer, "epoch", 2)
            self.assertEqual(len(restored.since(peer, 0)), 1)

    def test_strict_message_append_compacts_after_in_memory_update(self):
        from xiaoyu_runtime.events import message_log
        path = self.folder / "compact.jsonl"
        sequence = iter(range(1, 10))
        log = message_log.MessageLog(path, lambda: next(sequence))
        with mock.patch.object(message_log, "MAX_MESSAGES", 1), mock.patch.object(message_log, "COMPACT_FACTOR", 1):
            log._put({"id": "one", "source": {}, "created_at": 1}, strict=True)
            log._put({"id": "two", "source": {}, "created_at": 2}, strict=True)
        restored = message_log.MessageLog(path, lambda: next(sequence))
        self.assertIn("two", restored._messages)

    def test_crash_after_save_before_projection_recovers_on_startup(self):
        network, sender, receiver = self.nodes()
        card, origin, block = self.task_block(sender)
        with mock.patch.object(receiver.mesh, "_apply_current", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                receiver.mesh._take(block, origin)
        receiver.close()
        restored = Node(self, network, "Windows")
        self.assertTrue(until(lambda: restored.card_from(sender, card["id"]) is not None
                              and restored.mesh.cursors()[origin]["seq"] == card["seq"], 10))

    def test_command_result_write_failure_never_reexecutes_after_restart(self):
        network, sender, receiver = self.nodes()
        origin = sender.service.runtime_id
        cmd = {"id": "a" * 32, "target": receiver.service.runtime_id, "from": origin,
               "kind": "cancel", "args": {"card": "c1"}, "expires_at": time.time() + 600}
        block = {origin: {"epoch": sender.xiaoyu.cards.epoch, "upto": 1,
                         "records": [{"k": "command", "id": cmd["id"], "oseq": 1, "data": cmd}]}}
        with mock.patch.object(receiver.service, "cancel", return_value={}) as cancel:
            with mock.patch.object(receiver.mesh, "_post", side_effect=OSError("result write failed")):
                with self.assertRaises(OSError):
                    receiver.mesh._take(block, origin)
            cancel.assert_called_once()
        receiver.close()
        with mock.patch.object(Mesh, "start"):
            restored = Node(self, network, "Windows")
        with mock.patch.object(restored.service, "cancel") as cancel:
            restored.mesh.start()
            key = ("result", cmd["id"] + ":accepted")
            self.assertTrue(until(lambda: key in restored.mesh._outbox, 10))
            cancel.assert_not_called()
            result = restored.mesh._outbox[key]["data"]
            self.assertFalse(result["ok"])
            self.assertTrue(result.get("unknown"))

    def test_command_write_failure_reports_not_sent_and_clears_relay(self):
        _, sender, receiver = self.nodes()
        message = {"id": "a" * 32, "relay_to": receiver.service.runtime_id,
                   "conversation": "default", "text": "hello"}
        with mock.patch.object(sender.mesh, "_reachable", return_value=None):
            with mock.patch.object(sender.mesh, "_save", side_effect=OSError("disk full")):
                problem = sender.mesh.relay(message)
        self.assertTrue(problem)
        self.assertIn("没发出", problem)
        self.assertNotIn(message["id"], sender.mesh._relays)
        self.assertFalse(any(k == "command" for k, _ in sender.mesh._outbox))

    def test_normal_increment_does_not_rescan_replica_history(self):
        _, sender, receiver = self.nodes()
        card, origin, block = self.task_block(sender)
        with mock.patch.object(receiver.mesh._replicas, "since", wraps=receiver.mesh._replicas.since) as scan:
            receiver.mesh._take(block, origin)
            scan.assert_not_called()

    def test_new_epoch_does_not_discard_unapplied_old_epoch(self):
        _, sender, receiver = self.nodes()
        card, origin, block = self.task_block(sender)
        replacement = {origin: {"epoch": "replacement", "upto": 1, "records": []}}
        with mock.patch.object(receiver.mesh, "_task", side_effect=OSError("projection unavailable")):
            with self.assertRaises(OSError):
                receiver.mesh._take(block, origin)
            with self.assertRaises(OSError):
                receiver.mesh._take(replacement, origin)
        self.assertEqual(receiver.mesh._replicas.position(origin)[0], sender.xiaoyu.cards.epoch)
        receiver.mesh._take(replacement, origin)
        self.assertEqual(receiver.mesh._replicas.position(origin)[0], "replacement")
        self.assertIsNotNone(receiver.card_from(sender, card["id"]))

    def test_uncertain_command_result_stays_unknown_at_origin(self):
        _, sender, receiver = self.nodes()
        command = "d" * 32
        message = "e" * 32
        receiver.mesh._outbox[("command", command)] = {"data": {"kind": "message",
                                                       "args": {"message_id": message}}}
        receiver.mesh._relays[message] = sender.service.runtime_id
        result = {"id": command, "phase": "accepted", "ok": False, "unknown": True,
                  "error": "执行结果不明"}
        with mock.patch.object(receiver.service, "relay_done") as done:
            receiver.mesh._result(sender.service.runtime_id, result)
        done.assert_called_once_with(message, False, "执行结果不明", unknown=True, strict=True)

    def test_failed_result_projection_keeps_relay_for_retry(self):
        _, sender, receiver = self.nodes()
        command, message = "d" * 32, "e" * 32
        receiver.mesh._outbox[("command", command)] = {"data": {"kind": "message",
                                                       "args": {"message_id": message}}}
        receiver.mesh._relays[message] = sender.service.runtime_id
        result = {"id": command, "phase": "done", "ok": True, "reply": "finished"}
        with mock.patch.object(receiver.service, "relay_done", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                receiver.mesh._result(sender.service.runtime_id, result)
        self.assertIn(message, receiver.mesh._relays)
        with mock.patch.object(receiver.service, "relay_done") as done:
            receiver.mesh._result(sender.service.runtime_id, result)
        done.assert_called_once_with(message, True, "finished", strict=True)
        self.assertNotIn(message, receiver.mesh._relays)

    def test_late_record_below_received_cursor_is_still_projected(self):
        _, sender, receiver = self.nodes()
        card, origin, block = self.task_block(sender)
        receiver.mesh._take(block, origin)
        another = sender.xiaoyu.record.task_open("default", "late task", "working", "claude")
        late = {origin: {"epoch": sender.xiaoyu.cards.epoch, "upto": card["seq"],
                        "records": [{"k": "task", "id": another["id"], "oseq": card["seq"],
                                     "data": Cards.task_view(another)}]}}
        receiver.mesh._take(late, origin)
        self.assertIsNotNone(receiver.card_from(sender, another["id"]))

    def test_fresh_late_command_is_not_mistaken_for_recovered_command(self):
        _, sender, receiver = self.nodes()
        _, origin, block = self.task_block(sender)
        receiver.mesh._take(block, origin)
        seq = block[origin]["upto"]
        command = {"id": "f" * 32, "target": receiver.service.runtime_id,
                   "from": origin, "kind": "cancel", "args": {"card": "c1"}, "expires_at": time.time() + 600}
        late = {origin: {"epoch": block[origin]["epoch"], "upto": seq,
                         "records": [{"k": "command", "id": command["id"], "oseq": seq, "data": command}]}}
        with mock.patch.object(receiver.service, "cancel", return_value={}) as cancel:
            receiver.mesh._take(late, origin)
            cancel.assert_called_once()

    def test_overlapping_new_batches_do_not_misclassify_unstarted_commands(self):
        _, sender, receiver = self.nodes()
        origin, epoch = sender.service.runtime_id, sender.xiaoyu.cards.epoch
        def block(seq):
            command = {"id": str(seq) * 32, "target": receiver.service.runtime_id,
                       "from": origin, "kind": "cancel", "args": {"card": "c1"}, "expires_at": time.time() + 600}
            return {origin: {"epoch": epoch, "upto": seq, "records": [
                {"k": "command", "id": command["id"], "oseq": seq, "data": command}]}}
        first_saved, release = threading.Event(), threading.Event()
        original = receiver.mesh._replicas.store
        def delayed(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[3] == 1:
                first_saved.set()
                release.wait(3)
            return result
        with mock.patch.object(receiver.mesh._replicas, "store", side_effect=delayed):
            with mock.patch.object(receiver.service, "cancel", return_value={}) as cancel:
                first = threading.Thread(target=receiver.mesh._take, args=(block(1), origin))
                first.start()
                try:
                    self.assertTrue(first_saved.wait(2))
                    receiver.mesh._take(block(2), origin)
                finally:
                    release.set()
                    first.join(3)
                self.assertEqual(cancel.call_count, 2)
                for seq in (1, 2):
                    self.assertTrue(receiver.mesh._outbox[("result", str(seq) * 32 + ":accepted")]["data"]["ok"])

    def test_unexpired_receipt_survives_outbox_size_pressure(self):
        import time
        from xiaoyu_runtime.delivery import mesh as mesh_module
        network, _, receiver = self.nodes()
        expiry = time.time() + 600
        with mock.patch.dict(mesh_module.LIMITS, result=2):
            for index in range(3):
                key = str(index) * 32 + ":accepted"
                receiver.mesh._post("result", key, {"id": str(index) * 32, "phase": "accepted",
                                                   "ok": True, "expires_at": expiry}, strict=True)
            receiver.close()
            with mock.patch.object(Mesh, "start"):
                restored = Node(self, network, "Windows")
            self.assertIn(("result", "0" * 32 + ":accepted"), restored.mesh._outbox)

    def test_command_without_expiry_is_rejected_before_side_effect(self):
        _, sender, receiver = self.nodes()
        command = {"id": "c" * 32, "target": receiver.service.runtime_id,
                   "from": sender.service.runtime_id, "kind": "cancel", "args": {"card": "c1"}}
        with mock.patch.object(receiver.service, "cancel") as cancel:
            receiver.mesh._command(sender.service.runtime_id, command)
            cancel.assert_not_called()
        result = receiver.mesh._outbox[("result", command["id"] + ":accepted")]["data"]
        self.assertFalse(result["ok"])
        self.assertIn("期限", result["error"])

    def test_command_with_huge_expiry_is_rejected_before_side_effect(self):
        _, sender, receiver = self.nodes()
        command = {"id": "c" * 32, "target": receiver.service.runtime_id,
                   "from": sender.service.runtime_id, "kind": "cancel", "args": {"card": "c1"},
                   "expires_at": 10 ** 500}
        with mock.patch.object(receiver.service, "cancel") as cancel:
            receiver.mesh._command(sender.service.runtime_id, command)
            cancel.assert_not_called()
        result = receiver.mesh._outbox[("result", command["id"] + ":accepted")]["data"]
        self.assertFalse(result["ok"])
        self.assertIn("期限", result["error"])

    def test_message_log_failure_retries_without_losing_projection(self):
        network, sender, receiver = self.nodes()
        card = sender.xiaoyu.record.task_open("default", "message fault", "working", "claude")
        sender.xiaoyu.record.exchange(card["id"], "hello", "answer", turn_id="t1")
        _, _, messages = sender.xiaoyu.cards.own_since(0)
        message = next(item for item in messages if item["id"] == "t1:you")
        origin = sender.service.runtime_id
        block = {origin: {"epoch": sender.xiaoyu.cards.epoch, "upto": message["seq"],
                  "records": [{"k": "message", "id": message["id"], "oseq": message["seq"], "data": message}]}}
        log = receiver.xiaoyu.cards._log
        original = Path.open
        def fail_log(path, *args, **kwargs):
            if path == log._path and args and args[0] == "a":
                raise OSError("disk full")
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "open", fail_log):
            with self.assertRaises(OSError):
                receiver.mesh._take(block, origin)
        self.assertEqual(receiver.mesh.cursors()[origin]["seq"], 0)
        receiver.mesh._take(block, origin)
        local_id = origin[:12] + "." + message["id"]
        self.assertEqual(receiver.xiaoyu.cards.message(local_id)["text"], "hello")
        receiver.close()
        with mock.patch.object(Mesh, "start"):
            restarted = Node(self, network, "Windows")
        self.assertEqual(restarted.xiaoyu.cards.message(local_id)["text"], "hello")

    def test_alias_storage_failure_retries_without_duplicate_task(self):
        network, sender, receiver = self.nodes()
        card, origin, block = self.task_block(sender)
        with mock.patch.object(receiver.mesh, "_save", side_effect=OSError("alias storage unavailable")):
            with self.assertRaises(OSError):
                receiver.mesh._take(block, origin)
        receiver.close()
        with mock.patch.object(Mesh, "start"):
            restarted = Node(self, network, "Windows")
        restarted.mesh._retry_unapplied()
        matches = [item for item in restarted.xiaoyu.cards.recent(None, 1000, include_desktop=True)
                   if item.get("mesh_origin") == origin and item.get("mesh_id") == card["id"]]
        self.assertEqual(len(matches), 1)

    def test_history_projection_write_failure_is_retried(self):
        from xiaoyu_runtime.delivery import mesh_share
        _, sender, receiver = self.nodes()
        origin = sender.service.runtime_id
        data = {"key": "history:test", "kind": "history", "path": "test.md", "text": "synthetic", "at": 1}
        block = {origin: {"epoch": sender.xiaoyu.cards.epoch, "upto": 1,
                         "records": [{"k": "share", "id": data["key"], "oseq": 1, "data": data}]}}
        with mock.patch.object(mesh_share, "_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                receiver.mesh._take(block, origin)
        self.assertEqual(receiver.mesh.cursors()[origin]["seq"], 0)
        receiver.mesh._take(block, origin)
        paths = list(receiver.mesh.shares.mirror.glob("*/test.md"))
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].read_text(), "synthetic")

    def test_context_offer_write_failure_does_not_mark_turn_seen(self):
        _, _, receiver = self.nodes()
        legacy = receiver.xiaoyu.context._legacy
        turn = {"id": "t", "text": "hello", "reply": "answer", "at": 1}
        with mock.patch.object(legacy, "_save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                legacy.offer("default", [turn])
        self.assertEqual(legacy.turns("default"), [])
        self.assertEqual(legacy.offer("default", [turn]), 1)

    def test_shared_file_state_write_failure_is_retried(self):
        from xiaoyu_runtime.delivery import mesh_share
        _, sender, receiver = self.nodes()
        origin = sender.service.runtime_id
        folder = self.folder / "shared"
        folder.mkdir()
        receiver.mesh.shares.folders["test"] = folder
        data = {"key": "folder:test/a.txt", "kind": "folder", "name": "test",
                "path": "a.txt", "text": "synthetic", "at": 1}
        block = {origin: {"epoch": sender.xiaoyu.cards.epoch, "upto": 1,
                         "records": [{"k": "share", "id": data["key"], "oseq": 1, "data": data}]}}
        original = mesh_share._write
        def fail_state(path, *args, **kwargs):
            if path == receiver.mesh.shares._state:
                raise OSError("state unavailable")
            return original(path, *args, **kwargs)
        with mock.patch.object(mesh_share, "_write", side_effect=fail_state):
            with self.assertRaises(OSError):
                receiver.mesh._take(block, origin)
        self.assertNotIn(data["key"], receiver.mesh.shares._latest)
        receiver.mesh._take(block, origin)
        self.assertEqual((folder / "a.txt").read_text(), "synthetic")
        self.assertIn(data["key"], receiver.mesh.shares._latest)

    def test_remote_context_uses_canonical_messages_without_legacy_write_or_full_scan(self):
        _, sender, receiver = self.nodes()
        card = sender.xiaoyu.record.task_open("default", "context fault", "working", "claude")
        sender.xiaoyu.record.exchange(card["id"], "hello", "answer", turn_id="t2")
        _, _, messages = sender.xiaoyu.cards.own_since(0)
        selected = [m for m in messages if m["id"] in ("t2:you", "t2:reply")]
        origin = sender.service.runtime_id
        upto = max(m["seq"] for m in selected)
        block = {origin: {"epoch": sender.xiaoyu.cards.epoch, "upto": upto,
                         "records": [{"k": "message", "id": m["id"], "oseq": m["seq"], "data": m}
                                     for m in selected]}}
        legacy = receiver.xiaoyu.context._legacy
        with mock.patch.object(legacy, "_save", side_effect=OSError("context unavailable")) as save, \
                mock.patch.object(receiver.mesh._replicas, "since", wraps=receiver.mesh._replicas.since) as scan:
            receiver.mesh._take(block, origin)
        save.assert_not_called()
        scan.assert_not_called()
        self.assertEqual(receiver.mesh.cursors()[origin]["seq"], upto)
        turns = [t for t in receiver.xiaoyu.context.history("default") if t["text"] == "hello"]
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["reply"], "answer")

    def test_terminal_receipt_write_failure_recovers_without_reexecuting(self):
        _, _, receiver = self.nodes()
        command, message = "a" * 32, "b" * 32
        final = {"id": message, "status": "done", "reply": "finished", "mood": "idle"}
        key = ("result", command + ":done")
        with mock.patch.object(receiver.service, "get", return_value=final) as get:
            with mock.patch.object(receiver.mesh, "_save", side_effect=OSError("disk full")):
                receiver.mesh._watch(command, message)
                self.assertTrue(until(lambda: command in receiver.mesh._pending_results, 3))
                self.assertNotIn(key, receiver.mesh._outbox)
            receiver.mesh._retry_pending_results()
            self.assertEqual(receiver.mesh._outbox[key]["data"]["reply"], "finished")
            self.assertEqual(get.call_count, 1)

    def test_unacknowledged_terminal_receipt_survives_retention_pressure(self):
        from xiaoyu_runtime.delivery import mesh as mesh_module
        network, _, receiver = self.nodes()
        target = "b" * 32
        first = "a" * 32 + ":done"
        with mock.patch.dict(mesh_module.LIMITS, result=2):
            receiver.mesh._post("result", first, {"id": "a" * 32, "phase": "done", "to": target,
                                                    "ok": True, "reply": "finished"}, strict=True)
            for i in (1, 2):
                receiver.mesh._post("result", str(i) * 32 + ":done", {"id": str(i) * 32,
                    "phase": "done", "to": target, "ok": True}, strict=True)
            receiver.close()
            with mock.patch.object(Mesh, "start"):
                restored = Node(self, network, "Windows")
            self.assertEqual(restored.mesh._outbox[("result", first)]["data"]["reply"], "finished")

    def test_accepted_long_running_command_survives_retention_pressure(self):
        from xiaoyu_runtime.delivery import mesh as mesh_module
        network, _, node = self.nodes()
        target, command = "b" * 32, "a" * 32
        with mock.patch.dict(mesh_module.LIMITS, result=2):
            node.mesh._post("command", command, {"id": command, "target": target,
                              "expires_at": time.time() - 1}, strict=True)
            node.mesh._replicas.store(target, "epoch", [{"k": "result", "id": command + ":accepted",
                                  "oseq": 1, "data": {"id": command, "ok": True}}], 1)
            for i in (1, 2):
                node.mesh._post("result", str(i) * 32 + ":done", {"id": str(i) * 32,
                                      "phase": "done", "to": target, "ok": True}, strict=True)
            node.close()
            with mock.patch.object(Mesh, "start"):
                restarted = Node(self, network, "Windows")
            self.assertIn(("command", command), restarted.mesh._outbox)
