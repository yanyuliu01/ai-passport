import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from xiaoyu_runtime import timing


class TurnTimingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sink = timing.TurnTiming(self.tmp.name)

    def records(self):
        timing._QUEUE.join()
        path = self.sink.path
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_only_allowlisted_phase_and_random_id_are_persisted(self):
        secret = "do-not-log-private-message-or-token"
        client = "a" * 32
        request = "b" * 32
        self.sink.mark(secret, request, "runtime_received")
        self.sink.mark(client, request, secret)
        self.sink.mark(client, secret, "runtime_received")
        with timing.turn(self.sink, client, request):
            timing.phase("runtime_started")
            timing.phase("runtime_partial", once=True)
            timing.phase("runtime_partial", once=True)
            timing.phase("model_start", attempt=2)
        records = self.records()
        self.assertEqual([x["phase"] for x in records],
                         ["runtime_received", "runtime_started", "runtime_partial", "model_start"])
        self.assertTrue(all(x["client_id"] == client for x in records))
        self.assertNotIn("request_id", records[0])
        self.assertEqual(records[-1]["attempt"], 2)
        self.assertNotIn(secret, self.sink.path.read_text())
        self.assertEqual({"v", "process", "client_id", "phase", "monotonic_ns"}, set(records[0]))
        if os.name != "nt":
            self.assertEqual(self.sink.path.stat().st_mode & 0o777, 0o600)

    def test_thread_contexts_do_not_mix_client_ids(self):
        gate = threading.Barrier(2)
        def emit(client):
            with timing.turn(self.sink, client, "c" * 32):
                gate.wait()
                timing.phase("model_first_text")
        one = threading.Thread(target=emit, args=("a" * 32,))
        two = threading.Thread(target=emit, args=("b" * 32,))
        one.start(); two.start(); one.join(); two.join()
        self.assertEqual({"a" * 32, "b" * 32}, {r["client_id"] for r in self.records()})

    def test_hashed_weixin_client_id_can_correlate_timing(self):
        from xiaoyu_runtime.channels.store import client_id
        channel_id = client_id('weixin', 'private-account', 'private-message')
        self.sink.mark(channel_id, 'b' * 32, 'model_first_text')
        self.sink.mark('channel-private-message', 'b' * 32, 'model_first_text')
        records = self.records()
        self.assertEqual([r['client_id'] for r in records], [channel_id])
        self.assertNotIn('private-', self.sink.path.read_text())

    def test_rotation_and_symlink_are_bounded(self):
        path = self.sink.path
        with patch.object(timing, "_LIMIT", 200):
            timing._append(path, b"x" * 120)
            timing._append(path, b"y" * 120)
            timing._append(path, b"z" * 120)
        self.assertEqual(path.read_bytes(), b"z" * 120)
        self.assertEqual(path.with_suffix(".jsonl.1").read_bytes(), b"y" * 120)
        if hasattr(os, "symlink"):
            outside = Path(self.tmp.name) / "outside"
            outside.write_bytes(b"unchanged")
            path.unlink()
            try:
                path.symlink_to(outside)
            except (OSError, NotImplementedError):
                return
            timing._append(path, b"not-written")
            self.assertEqual(outside.read_bytes(), b"unchanged")

    def test_unwritable_sink_and_queue_failure_do_not_affect_a_turn(self):
        with patch.object(timing, "_submit", side_effect=RuntimeError("unavailable")):
            self.sink.mark("a" * 32, None, "runtime_received")
        invalid = Path(self.tmp.name) / "not-a-directory"
        invalid.write_text("x")
        timing.TurnTiming(invalid).mark("b" * 32, None, "runtime_received")
        timing._QUEUE.join()  # The worker survives diagnostic I/O errors.
        self.sink.mark("c" * 32, None, "runtime_received")
        self.assertEqual(self.records()[0]["client_id"], "c" * 32)

    def test_late_event_does_not_recreate_removed_state_tree(self):
        missing = Path(self.tmp.name) / "removed-state" / "diagnostics" / "turn-timing.jsonl"
        timing._append(missing, b"{}\n")
        self.assertFalse(missing.parent.parent.exists())


if __name__ == "__main__":
    unittest.main()
