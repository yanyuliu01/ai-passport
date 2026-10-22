"""Unified identity/management/history contracts; no model or private transcript."""
import dataclasses
import json
import unittest
from pathlib import Path
from unittest import mock

from test_runtime import TempDirCase, Scripted, make_xiaoyou
from xiaoyou_runtime.conversation_directory import uid, project, DirectoryMetadata
from xiaoyou_runtime.service import Service, RequestError
from xiaoyou_runtime.message_history import page


class DirectoryCase(TempDirCase):
    def setUp(self):
        super().setUp()
        self.x, self.store = make_xiaoyou(self.folder, [Scripted('codex')])
        self.service = Service(self.x, self.store)
        self.addCleanup(self.service.close)
        self.addCleanup(self.x.close)

    def row(self, card):
        return next(r for r in self.service.directory()['entries'] if r.get('task_id') == card['id'])


class DirectoryTests(DirectoryCase):
    def test_equal_names_and_directories_never_merge_distinct_native_sessions(self):
        for sid in ('native-one', 'native-two'):
            card = self.x.record.observed_open('__desktop__', 'same name', 'unknown', 'codex')
            self.x.record.observed(card['id'], monitoring_only=True, session_id=sid, source='desktop', project='workdir')
        rows = [r for r in self.service.directory()['entries'] if r['kind'] == 'native']
        self.assertEqual(2, len(rows))
        self.assertEqual(2, len({r['uid'] for r in rows}))

    def test_alias_shared_flags_and_revision_do_not_mutate_execution_or_original_name(self):
        card = self.x.record.task_open('default', 'original', 'working', 'codex')
        self.x.record.task(card['id'], worker_session='s1', worker_cwd='/actual/repo')
        before = self.x.cards.get(card['id'])
        row = self.row(card)
        old_seq = self.x.cards.seq
        body = {'fields': {'title': '统一名称', 'project': 'Display project', 'pinned': True, 'archived': True}, 'revision': 0}
        result = self.service.directory_edit(row['uid'], body)
        self.assertEqual('applied', result['status'])
        retry = self.service.directory_edit(row['uid'], body)
        self.assertEqual(result['metadata'], retry['metadata'])
        current = self.x.cards.get(card['id'])
        for key in ('state', 'title', 'worker_session', 'worker_cwd', 'last_meaningful_at'):
            self.assertEqual(before.get(key), current.get(key))
        delta = self.service.sync('default', after=old_seq, all_conversations=True)
        task = next(t for t in delta['tasks'] if t['id'] == card['id'])
        self.assertEqual(('统一名称', True, True), (task['title'], task['shared_pinned'], task['shared_archived']))
        with self.assertRaises(RequestError):
            self.service.directory_edit(row['uid'], {'fields': {'title': 'stale'}, 'revision': 0})

    def test_native_task_binding_is_one_source_and_epochs_do_not_reuse_task_identity(self):
        base = dict(id='c1', title='task', agent='codex', worker_session='s1')
        rows = project([base], [], {}, 'runtime', epoch='epoch-one')
        task = next(r for r in rows if r['kind'] == 'task')
        source = next(r for r in rows if r['kind'] == 'native')
        self.assertEqual([source['uid']], task['native_sources'])
        self.assertEqual([task['uid']], source['task_ids'])
        self.assertTrue(source['bound'])
        other = project([base], [], {}, 'runtime', epoch='epoch-two')
        self.assertNotEqual(task['uid'], next(r['uid'] for r in other if r['kind'] == 'task'))

    def test_conversation_alias_is_same_directory_result_and_main_cannot_be_archived(self):
        chat = self.service.conversation_create({'id': 'side-topic'})
        key = uid('chat', chat['id'])
        self.service.directory_edit(key, {'fields': {'title': 'Shared side title', 'pinned': True}, 'revision': 0})
        item = next(c for c in self.service.conversations()['conversations'] if c['id'] == chat['id'])
        self.assertEqual('Shared side title', item['title'])
        self.assertTrue(item['pinned'])
        with self.assertRaises(RequestError):
            self.service.directory_edit(uid('chat', 'default'), {'fields': {'archived': True}})

    def test_original_records_survive_compaction_and_history_cursor_is_scoped(self):
        card = self.x.record.task_open('default', 'history', 'done', 'codex')
        for i in range(8):
            self.x.record.reply(card['id'], 'message %s' % i, turn_id='history-%s' % i)
        path = self.x.cards._log._path
        with mock.patch('xiaoyou_runtime.message_log.MAX_MESSAGES', 3):
            self.x.cards._log.retain([])
        collected, cursor = {}, None
        for _ in range(30):
            result = page(path, lambda m: m.get('task') == card['id'], 'task-scope', cursor, limit=2)
            for m in result['messages']:
                if m['rev'] >= collected.get(m['id'], {}).get('rev', -1):
                    collected[m['id']] = m
            cursor = result['cursor']
            if cursor:
                with self.assertRaises(ValueError):
                    page(path, lambda m: True, 'different-scope', cursor)
            if not result['more']:
                break
        self.assertEqual({'message %s' % i for i in range(8)}, {m['text'] for m in collected.values()})

    def test_replica_metadata_only_follows_source_writer_and_newest_revision(self):
        store = DirectoryMetadata(self.folder/'display.json')
        item = {'uid': 'task-test', 'origin': 'peer', 'revision': 2, 'updated_at': 123, 'title': 'peer name', 'archived': True}
        self.assertFalse(store.merge('other', item))
        self.assertTrue(store.merge('peer', item))
        self.assertFalse(store.merge('peer', dict(item, revision=1, title='stale')))
        self.assertEqual('peer name', store.snapshot()['task-test']['title'])

    def test_native_history_reads_owned_source_without_execution_or_synthetic_chat(self):
        home = self.folder/'native-home'
        self.x._config = dataclasses.replace(self.x._config, desktop_codex_dir=home)
        path = home/'sessions'/'rollout-native-history.jsonl'
        path.parent.mkdir(parents=True)
        events = [{'type': 'session_meta', 'payload': {'id': 'native-history', 'cwd': '/project', 'source': 'cli', 'originator': 'xiaoyou-runtime'}}]
        for i in range(45):
            events.append({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user' if i % 2 == 0 else 'assistant', 'id': str(i), 'phase': None if i % 2 == 0 else 'final', 'content': [{'type': 'input_text' if i % 2 == 0 else 'output_text', 'text': 'public %s' % i}]}})
        events.append({'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'phase': 'analysis', 'content': [{'type': 'output_text', 'text': 'PRIVATE_REASONING'}]}})
        path.write_text(''.join(json.dumps(e)+'\n' for e in events))
        card = self.x.record.observed_open('__desktop__', 'native title', 'unknown', 'codex')
        self.x.record.observed(card['id'], monitoring_only=True, source='desktop', session_id='native-history')
        key = self.row(card)['uid']
        before = self.x.cards.seq
        first = self.service.directory_history(key)
        self.assertTrue(first['more'])
        second = self.service.directory_history(key, first['cursor'])
        self.assertEqual(45, len(first['messages']) + len(second['messages']))
        self.assertNotIn('PRIVATE_REASONING', json.dumps(first)+json.dumps(second))
        self.assertEqual(before, self.x.cards.seq)
        self.assertTrue(first['native_inputs_are_not_new_owner_requests'])

class DirectoryBoundaryTests(DirectoryCase):
    def test_short_turns_stay_in_one_logical_chat_instead_of_becoming_directory_tasks(self):
        for i in range(5):
            card = self.x.record.task_open('default','short message')
            self.x.record.exchange(card['id'],str(i),'reply',turn_id='short-%s'%i)
        rows = self.service.directory()['entries']
        self.assertEqual(1,len(rows))
        self.assertEqual('conversation',rows[0]['kind'])
        self.assertEqual(10,len(self.service.directory_history(rows[0]['uid'])['messages']))

    def test_search_pages_cover_all_records_and_cannot_reuse_another_query_cursor(self):
        for i in range(12):
            self.x.record.task_open('default','find me %s'%i,'done','codex')
        seen,cursor=set(),None
        for _ in range(10):
            result=self.service.directory_page(cursor,'find me',3)
            ids={r['uid'] for r in result['entries']}
            self.assertFalse(seen & ids)
            seen.update(ids);cursor=result['cursor']
            if cursor:
                with self.assertRaises(RequestError):self.service.directory_page(cursor,'another query',3)
            if not result['more']:break
        self.assertEqual(12,len(seen))
        self.assertFalse(any('legacy_message_ids' in r for r in result['entries']))

    def test_pruned_task_remains_named_and_readable_without_becoming_executable(self):
        card=self.x.record.task_open('default','old task','done','codex')
        self.x.record.reply(card['id'],'retained public result',turn_id='retained-result')
        row=self.row(card)
        self.service.directory_edit(row['uid'],{'revision':0,'fields':{'title':'owner alias','archived':True}})
        with mock.patch('xiaoyou_runtime.cards.MAX_CARDS',2),mock.patch('xiaoyou_runtime.cards.MAX_HISTORY',0):
            for i in range(5):self.x.record.task_open('default','new %s'%i,'done','codex')
        self.assertIsNone(self.x.cards.get(card['id']))
        retained=next(r for r in self.service.directory()['entries'] if r['uid']==row['uid'])
        self.assertEqual('owner alias',retained['title']);self.assertTrue(retained['retained_only'])
        self.assertTrue(retained['archived'])
        self.assertIn('retained public result',str(self.service.directory_history(row['uid'])))
        from xiaoyou_runtime.cards import Cards
        reopened=Cards(self.store.state_dir/'cards.json')
        self.assertTrue(any(c['id']==card['id'] for c in reopened.retained()))

    def test_large_history_page_reads_one_chunk_instead_of_one_megabyte_per_line(self):
        path=self.folder/'large.log.jsonl'
        path.write_text(''.join(json.dumps({'id':str(i),'rev':1,'seq':i,'status':'final','text':'x'*2000})+'\n' for i in range(700)))
        result=page(path,lambda _:True,'scope',limit=50)
        self.assertEqual(50,len(result['messages']))
        self.assertEqual('699',result['messages'][0]['id'])
        self.assertEqual('649',page(path,lambda _:True,'scope',result['cursor'],50)['messages'][0]['id'])

    def test_generation_is_extract_without_explicit_route_and_does_not_rename(self):
        card=self.x.record.task_open('default','original task','done','codex')
        self.x.record.reply(card['id'],'tests are complete',turn_id='summary-input')
        key=self.row(card)['uid']
        with mock.patch.object(self.service.metadata.gateway,'complete_json',side_effect=AssertionError('no model route')):
            self.assertEqual('generating',self.service.directory_metadata(key)['status'])
            self.service.metadata.pool.shutdown(wait=True)
        row=self.row(card)
        self.assertEqual('original task',row['title'])
        self.assertEqual('extract',row['generated']['source'])
        self.assertIn('tests are complete',row['generated']['summary'])
        self.assertEqual(self.service.runtime_id,row['generated']['origin'])

    def test_late_generated_name_cannot_replace_owner_metadata(self):
        key=uid('task','source','epoch','c1')
        self.x.record.directory_edit(key,{'title':'owner'},self.service.runtime_id,0)
        self.assertFalse(self.x.record.directory_generated(key,{'title':'late'},0))
        self.assertEqual({},self.x.cards.directory.generated_snapshot())

    def test_source_upserts_follow_authentic_writer_and_replay_revision(self):
        from xiaoyou_runtime.conversation_directory import home_scope
        source={'uid':uid('native','peer','codex','home-test','session'),'kind':'native','authority':'peer','agent':'codex','native_scope':'home-test','native_session_id':'session','title':'first','original_title':'first','observed_at':1,'updated_at':1,'source_revision':2}
        store=self.x.cards.directory
        self.assertFalse(store.observe_sources([source],'impostor'))
        self.assertTrue(store.observe_sources([source],'peer'))
        self.assertFalse(store.observe_sources([dict(source,title='late',source_revision=1)],'peer'))
        self.assertEqual('first',store.source_snapshot()[source['uid']]['title'])
        self.assertFalse(store.observe_sources([dict(source,uid='native-invalid')],'peer'))

    def test_owned_or_busy_native_sources_never_launch_a_copy(self):
        card=self.x.record.task_open('default','managed','done','codex')
        self.x.record.task(card['id'],worker_session='native-id')
        source=next(r for r in self.service.directory()['entries'] if r['kind']=='native')
        self.assertFalse(source['native_open']['supported'])
        with mock.patch('subprocess.run',side_effect=AssertionError('must not launch')):
            with self.assertRaises(RequestError):self.service.directory_open(source['uid'])

    def test_source_rename_and_disappearance_do_not_create_a_task_or_change_owner_alias(self):
        from xiaoyou_runtime.directory_discovery import Discovery
        home=self.folder/'codex-directory'
        path=home/'sessions'/'rollout-discovery-id.jsonl';path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'type':'session_meta','payload':{'id':'discovery-id','cwd':str(self.folder),'source':'cli'}})+'\n'+json.dumps({'type':'event_msg','payload':{'type':'task_complete'}})+'\n')
        config=dataclasses.replace(self.x._config,desktop_codex_dir=home,desktop_claude_dir=self.folder/'empty-claude',desktop_projects=(),agents=[])
        discovery=Discovery(config,self.x.record,self.service.runtime_id)
        count=len(self.x.cards.recent(None,1000));discovery.scan()
        source=next(iter(self.x.cards.directory.source_snapshot().values()))
        key=source['uid'];self.x.record.directory_edit(key,{'title':'owner alias'},self.service.runtime_id,0)
        (home/'.codex-global-state.json').write_text(json.dumps({'thread-titles':{'titles':{'discovery-id':'Native renamed'}}}))
        discovery.checked[path]=0;discovery.scan()
        self.assertEqual(count,len(self.x.cards.recent(None,1000)))
        self.assertEqual('Native renamed',self.x.cards.directory.source_snapshot()[key]['title'])
        self.assertEqual('owner alias',next(r for r in self.service.directory()['entries'] if r['uid']==key)['title'])
        path.unlink();discovery.scan()
        self.assertFalse(self.x.cards.directory.source_snapshot()[key]['available'])

    def test_http_directory_requires_auth_and_rejects_control_fields(self):
        import threading,urllib.request,urllib.error
        from xiaoyou_runtime.server import make_server
        server=make_server(dataclasses.replace(self.x._config,host='127.0.0.1',port=0),self.service)
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        base='http://127.0.0.1:%s'%server.server_address[1]
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(base+'/v1/directory')
        self.assertEqual(401,error.exception.code)
        card=self.x.record.task_open('default','task','done','codex');key=self.row(card)['uid']
        request=urllib.request.Request(base+'/v1/directory/'+key,data=json.dumps({'fields':{'cwd':'/elsewhere'},'revision':0}).encode(),headers={'Authorization':'Bearer '+self.x._config.token,'Content-Type':'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
        self.assertEqual(400,error.exception.code)

    def test_damaged_metadata_is_preserved_and_cannot_block_chat_source_reads(self):
        path=self.folder/'damaged.json';path.write_text('{broken')
        store=DirectoryMetadata(path)
        self.assertEqual({},store.snapshot())
        self.assertIn('damaged.json',store.errors)
        with self.assertRaises(ValueError):store.edit('task-id',{'title':'replacement'},'origin')
        self.assertEqual('{broken',path.read_text())

    def test_gateway_failure_keeps_extract_and_owner_name(self):
        from xiaoyou_runtime.model_gateway import GatewayError
        card=self.x.record.task_open('default','keep title','done','codex')
        self.x.record.reply(card['id'],'verified result',turn_id='gateway-fallback')
        self.service.metadata.gateway.settings.routes['conversation.metadata']=[('synthetic','non-astra')]
        # Use a non-Astra model identifier so this checks provider failure, not the cost guard.
        self.service.metadata.gateway.settings.routes['conversation.metadata']=[('synthetic','small-model')]
        with mock.patch.object(self.service.metadata.gateway,'complete_json',side_effect=GatewayError('offline')) as call:
            self.service.directory_metadata(self.row(card)['uid']);self.service.metadata.pool.shutdown(wait=True)
        self.assertEqual(1,call.call_count)
        row=self.row(card);self.assertEqual('keep title',row['title'])
        self.assertEqual('extract',row['generated']['source'])
        self.assertEqual('gateway_unavailable',row['generated']['generation_error'])

    def test_attempts_in_one_native_session_keep_independent_explicit_turn_ranges(self):
        card=self.x.record.task_open('default','goal','done','codex')
        for i in range(2):
            self.x.record.execution_binding(card['id'],'attempt-%s'%i,run_id='run',native_session_id='same-session',native_turn_id='turn-%s'%i,range_state='complete-turn')
        attempts=self.x.cards.get(card['id'])['execution_attempts']
        self.assertEqual(['turn-0','turn-1'],[i['native_turn_id'] for i in attempts])
        audit=self.x.cards.audit.page(card['id'])['events']
        self.assertEqual(2,len(audit));self.assertEqual('binding',audit[0]['phase'])
        self.assertEqual('same-session',audit[0]['binding']['native_session_id'])

    def test_binding_write_failure_does_not_strand_the_worker_and_reports_a_gap(self):
        card=self.x.record.task_open('default','goal','working','codex')
        with mock.patch.object(self.x.cards.audit,'append',side_effect=OSError('disk full')):
            self.assertFalse(self.x.record.execution_binding(card['id'],'attempt',native_session_id='real-source'))
        self.assertEqual('audit_gap',self.x.cards.get(card['id'])['binding_error'])
        self.x.record.reply(card['id'],'worker result still reconciled',turn_id='after-storage-failure')
        self.assertIn('worker result still reconciled',str(self.x.cards.get(card['id'])))

    def test_first_page_keeps_chats_and_goals_ahead_of_historical_native_sources(self):
        for i in range(6):
            card=self.x.record.observed_open('__desktop__','native %s'%i,'unknown','codex')
            self.x.record.observed(card['id'],monitoring_only=True,session_id='source-%s'%i)
        self.x.record.task_open('default','Current goal','done','codex')
        first=self.service.directory_page(limit=2)
        self.assertEqual(['conversation','task'],[r['kind'] for r in first['entries']])
        seen={r['uid'] for r in first['entries']};cursor=first['cursor']
        while cursor:
            more=self.service.directory_page(cursor,limit=2)
            ids={r['uid'] for r in more['entries']};self.assertFalse(ids & seen)
            seen.update(ids);cursor=more['cursor']
        self.assertEqual(8,len(seen))

    def test_directory_cursor_is_bound_to_runtime_and_epoch_as_well_as_query(self):
        for i in range(3):self.x.record.task_open('default','task %s'%i,'done','codex')
        first=self.service.directory_page(limit=1)
        original=self.service.runtime_id
        self.service.runtime_id='another-runtime'
        try:
            with self.assertRaises(RequestError):self.service.directory_page(first['cursor'],limit=1)
        finally:self.service.runtime_id=original
        self.assertEqual(1,len(self.service.directory_page(first['cursor'],limit=1)['entries']))

if __name__ == '__main__':
    unittest.main()
