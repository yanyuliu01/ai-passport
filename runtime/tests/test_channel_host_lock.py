import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from xiaoyu_runtime.channels.host_lock import HostLock


class HostLockTest(unittest.TestCase):
    def test_another_process_cannot_own_same_channel_until_release(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'host.lock'
            lock = HostLock(path)
            self.assertTrue(lock.acquire())
            script = ('import sys; from xiaoyu_runtime.channels.host_lock import HostLock; '
                      'lock = HostLock(sys.argv[1]); sys.exit(0 if lock.acquire() else 7)')
            try:
                blocked = subprocess.run([sys.executable, '-c', script, str(path)], check=False)
                self.assertEqual(blocked.returncode, 7)
            finally:
                lock.release()
            available = subprocess.run([sys.executable, '-c', script, str(path)], check=False)
            self.assertEqual(available.returncode, 0)
