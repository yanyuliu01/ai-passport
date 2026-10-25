"""One golden wire contract consumed by Python, Android and desktop tests."""
import copy
import json
import unittest
from pathlib import Path

from test_runtime import TempDirCase, Scripted, make_xiaoyou, until
from xiaoyou_runtime.delivery import presentation
from xiaoyou_runtime.api.service import Service

FIXTURE = Path(__file__).resolve().parents[2] / 'tests/fixtures/presentation-v1.json'


class PolicyTests(unittest.TestCase):
    def test_shared_golden_contract_and_no_input_mutation(self):
        for case in json.loads(FIXTURE.read_text(encoding='utf-8'))['cases']:
            with self.subTest(case=case['name']):
                before = copy.deepcopy(case['input'])
                actual = presentation.message(case['input'])
                self.assertEqual(case['wire'], actual)
                self.assertEqual(before, case['input'])
                self.assertEqual(case['visible'], [b['text'] for b in actual['presentation']['blocks'] if b['complete']])
                self.assertEqual(case['rendered'], [''.join(r['text'] for r in b['runs']) for b in actual['presentation']['blocks'] if b['complete']])

    def test_replica_has_same_identity_order_and_input_revision(self):
        raw = json.loads(FIXTURE.read_text(encoding='utf-8'))['cases'][0]['input']
        peer = dict(raw, id='a' * 12 + '.' + raw['id'], reply_to='a' * 12 + '.' + raw['reply_to'],
                    mesh_origin='a' * 32, rev=900, seq=901, task='c999')
        self.assertEqual(presentation.message(raw)['presentation'], presentation.message(peer)['presentation'])

    def test_native_turn_end_is_never_task_success(self):
        self.assertEqual('turn_ended', presentation.task({'state':'done','source':'desktop'})['presentation']['status'])
        self.assertEqual('done', presentation.task({'state':'done'})['presentation']['status'])
        self.assertEqual('unavailable', presentation.task({'state':'working','stale':True})['presentation']['status'])
        self.assertTrue(all(row['status'] == 'incomplete' for row in presentation.policy()['native_surfaces'].values()))

    def test_card_projection_preserves_legacy_order_without_making_ids(self):
        entries=[{'role':'you','text':'legacy'}, {'id':'new:reply','role':'xiaoyou','text':'A\n---\nB'}]
        card={'id':'c1','conversation':'default','state':'done','entries':entries}
        result=presentation.card(card)
        self.assertEqual(entries[0], result['entries'][0])
        self.assertEqual(['A','B'], [b['text'] for b in result['entries'][1]['presentation']['blocks']])
        self.assertNotIn('presentation', card)


class ProjectionIntegrationTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.agent = Scripted('claude')
        self.x, self.store = make_xiaoyou(self.folder, [self.agent])
        self.service = Service(self.x, self.store)
        self.addCleanup(self.service.close)
        self.addCleanup(self.x.close)

    def test_sync_card_history_and_stream_use_the_same_blocks(self):
        card=self.x.record.task_open('default','test')
        self.x.record.exchange(card['id'],'request','First\n---\nSecond',turn_id='turn')
        sync=self.service.sync('default')
        view=next(m['presentation'] for m in sync['messages'] if m['id']=='turn:reply')
        self.assertEqual(['First','Second'], [b['text'] for b in view['blocks']])
        cards=self.service.cards()
        entry=next(e for c in cards for e in c['entries'] if e.get('id')=='turn:reply')
        self.assertEqual(view,entry['presentation'])
        row=next(r for r in self.service.directory()['entries'] if r['kind']=='conversation' and r['conversation_id']=='default')
        history=self.service.directory_history(row['uid'])
        self.assertEqual(view,next(m['presentation'] for m in history['messages'] if m['id']=='turn:reply'))
        response={'id':'turn','conversation':'default','status':'done','text':'request','reply':'First\n---\nSecond',
                  'created_at':view['order'][0], 'events':[]}
        snapshot=self.service._snapshot(response)
        self.assertEqual(view['blocks'],snapshot['presentation']['blocks'])
        live=snapshot['message_snapshot']
        self.assertEqual(sync['epoch'],live['epoch'])
        self.assertEqual(sync['runtime_id'],live['runtime_id'])
        self.assertEqual(sync['subject_id'],live['subject_id'])
        self.assertNotIn('seq',live)  # A partial current-turn read cannot advance history.
        self.assertEqual([m for m in sync['messages'] if m['id'] in ('turn:you','turn:reply')],live['messages'])
        self.assertNotIn('presentation',self.x.cards.message('turn:reply'))

    def test_a_new_client_follows_a_formatted_streamed_reply_from_sync_alone(self):
        # R5 acceptance: no per-message wait or stream, no Markdown parsing on the client.
        gate = self.agent.gate
        gate.clear()
        self.addCleanup(gate.set)
        start = self.service.sync('default')
        sent = self.service.submit('**稍等** slow')
        self.assertTrue(until(lambda: self.service.get(sent['id'])['status'] == 'running'))
        self.service._report(sent['id'], 'partial', None, '先说 **一半**\n---\n还在想 `x`')

        def reply(after):
            page = self.service.sync('default', after=after, epoch=start['epoch'], wait=5)
            found = [m for m in page['messages'] if m['id'] == sent['id'] + ':reply']
            return page, found[-1]['presentation'] if found else None

        page, view = reply(start['seq'])
        while view is None or view['status'] != 'streaming':
            page, view = reply(page['seq'])
        self.assertEqual(('assistant_message', False), (view['event'], view['complete']))
        self.assertEqual([True, False], [b['complete'] for b in view['blocks']])
        self.assertEqual([{'text': '先说 ', 'bold': False}, {'text': '一半', 'bold': True}], view['blocks'][0]['runs'])
        self.assertEqual({'text': 'x', 'bold': False, 'code': 'inline'}, view['blocks'][1]['runs'][-1])
        gate.set()
        while view['status'] != 'final':
            page, view = reply(page['seq'])
        self.assertTrue(view['complete'])
        self.assertEqual('re:稍等 slow', view['plain'])
        owner = next(m['presentation'] for m in self.service.sync('default')['messages'] if m['id'] == sent['id'] + ':you')
        self.assertEqual(('said', '**稍等** slow'), (owner['event'], owner['plain']))
        task = next(t for t in self.service.sync('default')['tasks'] if t['presentation'].get('brief'))
        self.assertNotIn('**', task['presentation']['brief'])

    def test_delta_projection_and_epoch_reset_do_not_change_block_identity(self):
        card=self.x.record.task_open('default','test')
        self.x.record.exchange(card['id'],'request','answer',turn_id='turn')
        initial=self.service.sync('default')
        self.assertEqual([],self.service.sync('default',after=initial['seq'],epoch=initial['epoch'])['messages'])
        reset=self.service.sync('default',after=initial['seq'],epoch='old')
        self.assertTrue(reset['reset'])
        self.assertEqual(initial['messages'],reset['messages'])


if __name__ == '__main__':
    unittest.main()
