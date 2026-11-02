"""Startup must reconcile all tombstones without scanning history per chat."""
from unittest import mock

from test_runtime import TempDirCase, Scripted, make_xiaoyu
from xiaoyu_runtime.api.service import Service


class StartupReconciliationTests(TempDirCase):
    def test_multiple_tombstones_are_reconciled_once_and_keep_other_chats(self):
        xiaoyu, store = make_xiaoyu(self.folder, [Scripted('codex')])
        cards = xiaoyu.cards
        for name in ('side-deleted-a', 'side-deleted-b', 'side-kept'):
            cards.conversations.create(name)
            cards.open(name, 'task', agent='codex')
            store.remember(name, 'codex', 'session-' + name)
        # Simulate a crash after tombstones were committed but before cleanup.
        for name in ('side-deleted-a', 'side-deleted-b'):
            cards.conversations.delete(name)
        with mock.patch.object(cards, 'erase_conversations', wraps=cards.erase_conversations) as erase:
            service = Service(xiaoyu, store)
        self.addCleanup(service.close)
        self.assertEqual(1, erase.call_count)
        self.assertEqual({'side-deleted-a', 'side-deleted-b'}, set(erase.call_args.args[0]))
        for name in ('side-deleted-a', 'side-deleted-b'):
            self.assertEqual([], cards.recent(name, 100))
            self.assertIsNone(store.session(name, 'codex'))
            self.assertTrue(cards.conversations.get(name)['deleted'])
        self.assertEqual(1, len(cards.recent('side-kept', 100)))
        self.assertEqual('session-side-kept', store.session('side-kept', 'codex'))
