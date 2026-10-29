"""Offline checks for the Runtime-only dependency closure used in desktop packages."""
import json
import tempfile
import unittest
from pathlib import Path
from bundle_agents import prune_packages


class BundleGraphTests(unittest.TestCase):
    def test_keeps_shared_nested_optional_and_peer_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            specs = {
                'node_modules/@deepseek-ai/dsh': {'name': '@deepseek-ai/dsh', 'dependencies': {
                    '@deepseek-ai/dsh-base': '1', '@deepseek-ai/dsh-web-app': '1'}},
                'node_modules/@deepseek-ai/dsh-base': {'name': '@deepseek-ai/dsh-base', 'dependencies': {'shared': '1'},
                    'optionalDependencies': {'native': '1'}, 'peerDependencies': {'peer': '1'}},
                'node_modules/@deepseek-ai/dsh-web-app': {'name': '@deepseek-ai/dsh-web-app', 'dependencies': {'web-only': '1', 'shared': '1'}},
                'node_modules/@mariozechner/pi-coding-agent': {'name': '@mariozechner/pi-coding-agent', 'dependencies': {'shared': '1'}},
                'node_modules/@mariozechner/pi-coding-agent/node_modules/shared': {'name': 'shared', 'dependencies': {}},
                **{'node_modules/' + name: {'name': name} for name in ['shared', 'native', 'peer', 'web-only']},
            }
            for name, metadata in specs.items():
                folder = root / name
                folder.mkdir(parents=True)
                (folder / 'package.json').write_text(json.dumps(metadata))
            (root / 'package-lock.json').write_text(json.dumps({'packages': {'': {}, **specs}}))
            excluded = prune_packages(root)
            self.assertEqual(set(excluded), {'node_modules/@deepseek-ai/dsh-web-app', 'node_modules/web-only'})
            for name in set(specs) - set(excluded):
                self.assertTrue((root / name / 'package.json').exists(), name)


if __name__ == '__main__':
    unittest.main()
