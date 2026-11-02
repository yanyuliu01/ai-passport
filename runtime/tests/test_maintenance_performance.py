"""Background maintenance must preserve chat and share changes."""
import hashlib
import threading
from unittest import mock

from test_runtime import TempDirCase
from test_mesh import Network, Node
from xiaoyu_runtime.delivery import mesh as mesh_module
from xiaoyu_runtime.delivery.mesh_share import Shares


class MaintenancePerformanceTests(TempDirCase):
    def test_slow_first_share_does_not_block_start_or_chat(self):
        started, release = threading.Event(), threading.Event()

        def blocked(_shares, *args, **kwargs):
            started.set()
            release.wait(5)
            return 0

        self.addCleanup(release.set)
        with mock.patch.object(mesh_module, 'FIRST_SHARE_DELAY_SECONDS', 0), \
                mock.patch.object(Shares, 'scan', blocked):
            node = Node(self, Network(), 'cold-start')
            self.assertTrue(started.wait(2))
            sent = node.service.submit('hello', client_id='cold-test')
            self.assertTrue(node.done(sent['id'], 2))
            self.assertFalse(release.is_set())
            release.set()

    def test_history_fingerprint_skips_unchanged_and_rechecks_after_change(self):
        node = Node(self, Network(), 'history-cache')
        shares = node.mesh.shares
        shares.history.mkdir(parents=True, exist_ok=True)
        path = shares.history / 'example.md'
        path.write_text('one')
        self.assertGreaterEqual(shares.scan(), 1)
        with mock.patch.object(shares, '_read', wraps=shares._read) as read:
            shares.scan()
            self.assertNotIn(path, [call.args[0] for call in read.call_args_list])
        path.write_text('two')
        self.assertGreaterEqual(shares.scan(), 1)
        self.assertEqual(shares._latest['history:example.md'][2], hashlib.sha256(b'two').hexdigest())
