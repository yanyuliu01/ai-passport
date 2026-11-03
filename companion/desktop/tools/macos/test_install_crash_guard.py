#!/usr/bin/env python3
"""Installation checks use temporary files and a simulated launchd."""
import importlib.util
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


spec = importlib.util.spec_from_file_location('guard_install', Path(__file__).with_name('install-crash-guard.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='xiaoyu-guard-installer-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.app = self.root / 'Xiaoyu.app'
        (self.app / 'Contents').mkdir(parents=True)
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'com.folotoy.xiaoyu'}))
        self.source = self.root / 'guard.swift'
        self.source.write_text('// test guard source\n')
        self.support = self.home / 'Library/Application Support/xiaoyu-crash-guard'
        self.plist = self.home / 'Library/LaunchAgents' / (installer.LABEL + '.plist')

    def runner(self, *, existing=False, fail_bootstrap=False):
        def run(args, **kwargs):
            if args[:2] == ['launchctl', 'print']:
                return SimpleNamespace(returncode=0 if existing else 113)
            if args[0] == 'swiftc':
                Path(args[-1]).write_bytes(b'test binary')
            if args[:2] == ['launchctl', 'bootstrap']:
                self.assertEqual(plistlib.loads(self.plist.read_bytes())['ProgramArguments'],
                                 [str(self.support / 'crash-guard')])
                if fail_bootstrap:
                    raise subprocess.CalledProcessError(1, args)
            return SimpleNamespace(returncode=0)
        return run

    def test_installs_per_user_service_and_preserves_existing_files(self):
        log = installer.install(self.home, self.source, self.app, 501, self.runner())
        self.assertEqual(log, self.support / 'events.log')
        self.assertEqual((self.support / 'crash-guard').read_bytes(), b'test binary')
        with self.assertRaises(FileExistsError):
            installer.install(self.home, self.source, self.app, 501, self.runner())
        self.assertEqual((self.support / 'crash-guard').read_bytes(), b'test binary')

    def test_failed_bootstrap_removes_only_new_files(self):
        unrelated = self.home / 'unrelated'
        unrelated.write_text('keep')
        with self.assertRaises(subprocess.CalledProcessError):
            installer.install(self.home, self.source, self.app, 501, self.runner(fail_bootstrap=True))
        self.assertFalse(self.support.exists())
        self.assertFalse(self.plist.exists())
        self.assertEqual(unrelated.read_text(), 'keep')

    def test_loaded_service_is_not_replaced(self):
        with self.assertRaises(FileExistsError):
            installer.install(self.home, self.source, self.app, 501, self.runner(existing=True))
        self.assertFalse(self.support.exists())
        self.assertFalse(self.plist.exists())


if __name__ == '__main__':
    unittest.main()
