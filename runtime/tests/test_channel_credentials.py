import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xiaoyu_runtime.channels.credentials import Credentials


class CredentialsTest(unittest.TestCase):
    def test_private_save_load_replace_and_remove(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'private.json'
            store = Credentials(path)
            data = {'token': 'secret', 'bot': 'bot', 'owner': 'owner',
                    'base': 'https://ilinkai.weixin.qq.com'}
            self.assertIsNone(store.load())
            store.save(data)
            self.assertEqual(store.load(), data)
            if os.name == 'posix':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with mock.patch('xiaoyu_runtime.channels.credentials.os.replace', side_effect=OSError('disk error')):
                with self.assertRaises(OSError):
                    store.save(dict(data, token='new'))
            self.assertEqual(store.load(), data)
            store.remove()
            self.assertIsNone(store.load())

    def test_invalid_endpoint_never_persists(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Credentials(Path(folder) / 'private.json')
            with self.assertRaises(ValueError):
                store.save({'token': 'secret', 'bot': 'bot', 'owner': 'owner', 'base': 'https://evil.example'})
            self.assertIsNone(store.load())
