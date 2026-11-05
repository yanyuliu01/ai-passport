"""Fault injection for replica persistence and exchange recovery."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_runtime import TempDirCase
from xiaoyu_runtime.delivery.mesh import Replicas


class ReplicaDurabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.origin = "a" * 32
        self.record = {"k": "message", "id": "m1", "oseq": 1,
                       "data": {"id": "m1", "text": "hello"}}

    def test_failed_first_write_does_not_advance_cursor_and_can_retry(self):
        replicas = Replicas(self.folder)
        with mock.patch.object(replicas, "_rewrite", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                replicas.store(self.origin, "epoch", [self.record], 1)
        self.assertEqual(replicas.position(self.origin), ("", 0))
        self.assertEqual(replicas.store(self.origin, "epoch", [self.record], 1), [self.record])
        restored = Replicas(self.folder)
        self.assertEqual(restored.position(self.origin), ("epoch", 1))
        self.assertEqual(restored.get(self.origin, "message", "m1"), self.record["data"])

    def test_applied_record_does_not_cross_a_pending_apply_or_epoch_reset(self):
        replicas = Replicas(self.folder)
        replicas.store(self.origin, 'epoch-a', [self.record], 1, track=True)
        self.assertIsNone(replicas.applied_record(self.origin, 'message', 'm1'))
        replicas.mark_applied(self.origin, 'epoch-a', 1)
        self.assertEqual(replicas.applied_record(self.origin, 'message', 'm1')['text'], 'hello')
        replacement = dict(self.record, data={'id': 'm1', 'text': 'new epoch'})
        replicas.store(self.origin, 'epoch-b', [replacement], 1, track=True)
        self.assertIsNone(replicas.applied_record(self.origin, 'message', 'm1'))
        replicas.mark_applied(self.origin, 'epoch-b', 1)
        self.assertEqual(replicas.applied_record(self.origin, 'message', 'm1')['text'], 'new epoch')

    def test_failed_incremental_write_preserves_previous_cursor_and_retry(self):
        replicas = Replicas(self.folder)
        replicas.store(self.origin, "epoch", [self.record], 1)
        second = {"k": "message", "id": "m2", "oseq": 2,
                  "data": {"id": "m2", "text": "world"}}
        original = Path.open

        def failing_open(path, *args, **kwargs):
            if path == self.folder / (self.origin + ".jsonl") and args and args[0] == "a":
                raise OSError("disk full")
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "open", failing_open):
            with self.assertRaises(OSError):
                replicas.store(self.origin, "epoch", [second], 2)
        self.assertEqual(replicas.position(self.origin), ("epoch", 1))
        self.assertIsNone(replicas.get(self.origin, "message", "m2"))
        self.assertEqual(replicas.store(self.origin, "epoch", [second], 2), [second])
        restored = Replicas(self.folder)
        self.assertEqual(restored.position(self.origin), ("epoch", 2))
        self.assertEqual(restored.get(self.origin, "message", "m2"), second["data"])

    def test_crash_between_record_and_cursor_replays_old_state_then_retries(self):
        import json

        replicas = Replicas(self.folder)
        replicas.store(self.origin, "epoch", [self.record], 1)
        changed = {"k": "message", "id": "m1", "oseq": 2,
                   "data": {"id": "m1", "text": "new"}}
        path = self.folder / (self.origin + ".jsonl")
        with path.open("ab") as handle:
            handle.write((json.dumps(changed) + "\n{\"upto\":").encode())
        restored = Replicas(self.folder)
        self.assertEqual(restored.position(self.origin), ("epoch", 1))
        self.assertEqual(restored.get(self.origin, "message", "m1"), self.record["data"])
        self.assertEqual(restored.store(self.origin, "epoch", [changed], 2), [changed])
        restarted = Replicas(self.folder)
        self.assertEqual(restarted.position(self.origin), ("epoch", 2))
        self.assertEqual(restarted.get(self.origin, "message", "m1"), changed["data"])

    def test_uncommitted_record_does_not_reappear_when_cursor_later_advances(self):
        import json

        replicas = Replicas(self.folder)
        replicas.store(self.origin, "epoch", [self.record], 1)
        abandoned = {"k": "message", "id": "m1", "oseq": 2,
                     "data": {"id": "m1", "text": "not committed"}}
        path = self.folder / (self.origin + ".jsonl")
        with path.open("ab") as handle:
            handle.write((json.dumps(abandoned) + "\n{\"upto\":").encode())
        restored = Replicas(self.folder)
        other = {"k": "message", "id": "m2", "oseq": 3,
                 "data": {"id": "m2", "text": "committed"}}
        restored.store(self.origin, "epoch", [other], 3)
        restarted = Replicas(self.folder)
        self.assertEqual(restarted.get(self.origin, "message", "m1"), self.record["data"])
        self.assertEqual(restarted.get(self.origin, "message", "m2"), other["data"])

    def test_partial_append_failure_is_repaired_before_retry(self):
        replicas = Replicas(self.folder)
        replicas.store(self.origin, "epoch", [self.record], 1)
        changed = {"k": "message", "id": "m1", "oseq": 2,
                   "data": {"id": "m1", "text": "new"}}
        original = Path.open

        class BrokenWriter:
            def __init__(self, handle):
                self.handle = handle
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.handle.close()
            def write(self, text):
                self.handle.write(text[:len(text) // 2])
                raise OSError("partial write")

        def failing_open(path, *args, **kwargs):
            handle = original(path, *args, **kwargs)
            if path == self.folder / (self.origin + ".jsonl") and args and args[0] == "a":
                return BrokenWriter(handle)
            return handle

        with mock.patch.object(Path, "open", failing_open):
            with self.assertRaises(OSError):
                replicas.store(self.origin, "epoch", [changed], 2)
        self.assertEqual(replicas.position(self.origin), ("epoch", 1))
        replicas.store(self.origin, "epoch", [changed], 2)
        restarted = Replicas(self.folder)
        self.assertEqual(restarted.position(self.origin), ("epoch", 2))
        self.assertEqual(restarted.get(self.origin, "message", "m1"), changed["data"])


class MeshRetryIntegrationTests(TempDirCase):
    def test_failed_replica_write_does_not_acknowledge_or_lose_remote_task(self):
        from test_mesh import Network, Node
        from xiaoyu_runtime.world.cards import Cards

        network = Network()
        sender = Node(self, network, "Mac")
        receiver = Node(self, network, "Windows")
        card = sender.xiaoyu.record.task_open("default", "shared task", "working", "claude")
        origin = sender.service.runtime_id
        seq = card["seq"]
        block = {origin: {"epoch": sender.xiaoyu.cards.epoch, "upto": seq,
                 "origin_name": "Mac", "records": [{"k": "task", "id": card["id"],
                 "oseq": seq, "data": Cards.task_view(card)}]}}
        with mock.patch.object(receiver.mesh._replicas, "_rewrite", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                receiver.mesh._take(block, origin)
        self.assertNotIn(origin, receiver.mesh.cursors())
        self.assertIsNone(receiver.card_from(sender, card["id"]))
        receiver.mesh._take(block, origin)
        self.assertEqual(receiver.mesh.cursors()[origin]["seq"], seq)
        self.assertEqual(receiver.card_from(sender, card["id"])["title"], "shared task")


class MeshHttpStorageFailureTests(TempDirCase):
    def test_exchange_reports_storage_failure_as_retryable_503(self):
        from test_mesh import MeshHttpTests, SECRET

        helper = MeshHttpTests("test_two_runtimes_sync_over_http_and_the_exchange_needs_the_mesh_secret")
        helper.folder = self.folder
        self.addCleanup(helper.doCleanups)
        url, service, _ = helper.runtime("Home")
        with mock.patch.object(service.mesh, "exchange", side_effect=OSError("disk full")):
            status, _body = helper.call("POST", url + "/v1/mesh/exchange", {}, token=SECRET)
        self.assertEqual(status, 503)
