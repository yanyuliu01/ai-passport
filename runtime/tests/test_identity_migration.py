"""The new name must preserve installed identity, state and command inputs."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import TempDirCase, write_config
from xiaoyu_runtime import config
from xiaoyu_runtime.compat import environment


class IdentityMigrationTests(TempDirCase):
    def test_old_imports_share_types_and_modules_with_canonical_package(self):
        for old, new in [('config', 'config'), ('helpers.agents', 'helpers.agents'),
                         ('world.cards', 'world.cards'), ('loop.xiaoyou', 'loop.xiaoyu')]:
            self.assertIs(importlib.import_module('xiaoyou_runtime.' + old),
                          importlib.import_module('xiaoyu_runtime.' + new))

    def test_old_configuration_and_new_configuration_read_the_same_state(self):
        path = write_config(self.folder)
        raw = json.loads(path.read_text())
        settings = raw.pop('xiaoyou', raw.pop('xiaoyu', {}))
        raw['xiaoyou'] = settings
        path.write_text(json.dumps(raw))
        old = config.load(path, {'XIAOYOU_NAME': 'Legacy', 'XIAOYOU_STATE_DIR': 'retained'})
        raw.pop('xiaoyou'); raw['xiaoyu'] = settings
        path.write_text(json.dumps(raw))
        new = config.load(path, {'XIAOYU_NAME': 'Legacy', 'XIAOYU_STATE_DIR': 'retained'})
        self.assertEqual(old, new)
        self.assertEqual(new.state_dir, (self.folder / 'retained').resolve())

    def test_ambiguous_configuration_is_refused_without_rewriting_it(self):
        path = write_config(self.folder)
        raw = json.loads(path.read_text())
        raw['xiaoyou'] = {'voice_timeout_seconds': 30}
        raw['xiaoyu'] = {'voice_timeout_seconds': 60}
        text = json.dumps(raw); path.write_text(text)
        with self.assertRaises(config.ConfigError):
            config.load(path, {})
        self.assertEqual(path.read_text(), text)

    def test_new_environment_takes_precedence_over_legacy_alias(self):
        self.assertEqual(environment({'XIAOYU_PORT': '2222', 'XIAOYOU_PORT': '1111'})['XIAOYU_PORT'], '2222')

    def test_both_command_names_and_installed_hook_path_are_runnable(self):
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONPATH=str(root))
        for module in ('xiaoyu_runtime', 'xiaoyou_runtime'):
            out = subprocess.run([sys.executable, '-m', module, '--help'], cwd=root, env=env,
                                 capture_output=True, text=True, timeout=15)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn('xiaoyu_runtime', out.stdout)
        out = subprocess.run([sys.executable, str(root/'xiaoyou_runtime/shared_history.py'), '--help'],
                             cwd=root, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(out.returncode, 0, out.stderr)
