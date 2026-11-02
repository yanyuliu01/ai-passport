"""Rebuildable retained-card metadata must never replace original history."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xiaoyu_runtime.world import retained_index
from xiaoyu_runtime.world.conversation_directory import project
from xiaoyu_runtime.world.cards import Cards


class RetainedIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'cards.retained'
        self.folder.mkdir()
        self.index = self.root / 'cards.retained-index.json'
        self.card = {'id':'c1', 'record_epoch':'epoch', 'retained_only':True,
                     'conversation':'side-one', 'title':'old task', 'state':'done', 'agent':'codex',
                     'worker_session':'s1', 'worker_cwd':'/tmp/project', 'worker_scope':'scope',
                     'updated_at':123, 'entries':[{'id':'one', 'role':'you', 'text':'private body', 'at':1}],
                     'attachments':[], 'progress':[], 'supplements':[], 'cost':None,
                     'execution_attempts':[{'attempt_id':'a'}]}
        self.write('one', self.card)

    def write(self, name, card):
        (self.folder / (name + '.json')).write_text(json.dumps(card))

    def test_projection_matches_original_and_index_has_no_message_body(self):
        items = retained_index.load(self.folder, self.index)
        self.assertEqual(project([self.card], [], {}, 'runtime'),
                         project(list(items.values()), [], {}, 'runtime'))
        self.assertNotIn('private body', self.index.read_text())
        with mock.patch.object(retained_index, 'summary', side_effect=AssertionError('reparsed')):
            self.assertEqual(items, retained_index.load(self.folder, self.index))
        self.assertEqual('private body', json.loads((self.folder/'one.json').read_text())['entries'][0]['text'])

    def test_changed_added_deleted_and_damaged_cache_reconcile_from_originals(self):
        retained_index.load(self.folder, self.index)
        changed = dict(self.card, title='changed')
        self.write('one', changed)
        self.write('two', dict(self.card, id='c2'))
        items = retained_index.load(self.folder, self.index)
        self.assertEqual({'one','two'}, set(items))
        self.assertEqual('changed', items['one']['title'])
        (self.folder/'one.json').unlink()
        self.index.write_text('{damaged')
        items = retained_index.load(self.folder, self.index)
        self.assertEqual({'two'}, set(items))
        self.assertEqual('c2', items['two']['id'])

    def test_equal_message_sets_share_memory_and_checksum_rejects_tampering(self):
        self.write('two', dict(self.card, id='c2'))
        retained_index.load(self.folder, self.index)
        items = retained_index.load(self.folder, self.index)
        self.assertIs(items['one']['canonical_message_ids'], items['two']['canonical_message_ids'])
        index = json.loads(self.index.read_text())
        index['data'][0]['one']['card']['title'] = 'incorrect cached title'
        self.index.write_text(json.dumps(index))
        items = retained_index.load(self.folder, self.index)
        self.assertEqual('old task', items['one']['title'])

    def test_unwritable_index_keeps_snapshots_and_returns_metadata(self):
        with mock.patch.object(retained_index.os, 'replace', side_effect=OSError('disk failure')):
            items = retained_index.load(self.folder, self.index)
        self.assertEqual('c1', items['one']['id'])
        self.assertFalse(self.index.exists())
        self.assertTrue((self.folder/'one.json').exists())

    def test_missing_original_is_not_silently_returned_as_full_history(self):
        cards = Cards(self.root/'cards.json')
        (self.folder/'one.json').unlink()
        with self.assertRaisesRegex(RuntimeError, 'Retained snapshot unavailable'):
            cards.retained()

    def test_full_retained_reads_original_and_conversation_erasure_is_scoped(self):
        self.write('two', dict(self.card, id='c2', conversation='side-two'))
        cards = Cards(self.root/'cards.json')
        self.assertEqual('private body', cards.retained()[0]['entries'][0]['text'])
        cards.erase_conversation('side-one')
        self.assertFalse((self.folder/'one.json').exists())
        self.assertTrue((self.folder/'two.json').exists())
        reopened = Cards(self.root/'cards.json')
        self.assertEqual(['c2'], [c['id'] for c in reopened.retained()])

    def test_batched_tombstones_remove_only_their_snapshots_and_replay(self):
        self.write('two', dict(self.card, id='c2', conversation='side-two'))
        self.write('three', dict(self.card, id='c3', conversation='side-keep'))
        cards = Cards(self.root/'cards.json')
        live_deleted = cards.open('side-one', 'deleted task', agent='codex')
        live_kept = cards.open('side-keep', 'kept task', agent='codex')
        with mock.patch.object(cards.directory, 'erase', wraps=cards.directory.erase) as erase, \
                mock.patch.object(cards, '_save', wraps=cards._save) as save:
            cards.erase_conversations(['side-one', 'side-two', 'side-one'])
        self.assertEqual(1, erase.call_count)
        self.assertEqual(1, save.call_count)
        self.assertFalse((self.folder/'one.json').exists())
        self.assertFalse((self.folder/'two.json').exists())
        self.assertTrue((self.folder/'three.json').exists())
        self.assertIsNone(cards.get(live_deleted['id']))
        self.assertIsNotNone(cards.get(live_kept['id']))
        reopened = Cards(self.root/'cards.json')
        self.assertEqual(['c3'], [item['id'] for item in reopened.retained()])
        self.assertIsNotNone(reopened.get(live_kept['id']))

    def test_batched_tombstones_retry_after_interrupted_file_removal(self):
        self.write('two', dict(self.card, id='c2', conversation='side-two'))
        self.write('three', dict(self.card, id='c3', conversation='side-keep'))
        cards = Cards(self.root/'cards.json')
        original = Path.unlink
        def fail_one(path, *args, **kwargs):
            if path.name == 'two.json':
                raise OSError('simulated storage failure')
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'unlink', autospec=True, side_effect=fail_one):
            with self.assertRaises(OSError):
                cards.erase_conversations(['side-one', 'side-two'])
        self.assertTrue((self.folder/'three.json').exists())
        recovered = Cards(self.root/'cards.json')
        recovered.erase_conversations(['side-one', 'side-two'])
        self.assertEqual(['c3'], [item['id'] for item in recovered.retained()])


if __name__ == '__main__':
    unittest.main()
