"""Gateway selection, environment binding and actual existing dispatch integration."""
import dataclasses
import json
import threading
import urllib.request
import urllib.error
from pathlib import Path
from unittest import mock

from test_runtime import Scripted, TempDirCase, make_xiaoyu, until
from xiaoyu_runtime.api.service import Service
from xiaoyu_runtime.api.service import RequestError
from xiaoyu_runtime.api.server import make_server
from xiaoyu_runtime.errors import AgentError
from xiaoyu_runtime.helpers.agents import CodexAgent, ClaudeCodeAgent, DshAgent, PiAgent, CommandAgent
from xiaoyu_runtime.world.conversation_directory import home_scope, uid


class SessionGatewayTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.voice = Scripted('voice')
        self.codex = Scripted('codex', speaks=False, kind='codex')
        self.codex.native_resume = True
        self.x, self.store = make_xiaoyu(self.folder, [self.voice, self.codex])
        self.service = Service(self.x, self.store)
        self.addCleanup(self.service.close)
        self.project = self.folder / 'ai-passport'
        self.project.mkdir()

    def desktop(self, kind='codex', **extra):
        card = self.x.cards.open('__desktop__', '冷启动排查', 'done', kind)
        self.x.cards.update(card['id'], monitoring_only=True, source='desktop',
            session_id='original-thread', desktop_cwd=str(self.project), desktop_status='idle',
            project='ai-passport', **extra)
        return card['id']

    def plan(self, target, task='continue original work'):
        from xiaoyu_runtime.helpers.base import Outcome
        return self.x._plan(Outcome('', None, {'card':target, 'action':{'type':'resume', 'task':task}}),
                            'default', None, self.x.agents())

    def test_main_chat_search_finds_native_work_but_not_private_side_task(self):
        key = self.desktop()
        private = self.x.cards.open('private-side', 'ai-passport private task', 'done', 'codex')
        result = self.x.sessions.search('default', 'ai passport')
        self.assertEqual([key], [v['target'] for v in result['matches']])
        self.assertTrue(result['matches'][0]['resumable'])
        self.assertIsNone(self.x.sessions.target(private['id'], 'default'))
        self.assertFalse(any('entries' in row for row in self.x.cards.session_targets()))

    def test_multiple_matches_are_returned_without_starting_or_guessing(self):
        keys = {self.desktop(), self.desktop()}
        result = self.x.sessions.search('default', 'ai-passport')
        self.assertEqual(keys, {v['target'] for v in result['matches']})
        self.assertEqual('explicit-target-required', result['selection'])
        self.assertEqual([], self.codex.jobs)

    def test_existing_action_path_resumes_the_selected_native_session(self):
        key = self.desktop()
        self.voice.script = [{'reply':'提交原会话续接', 'card':key,
                              'action':{'type':'resume', 'task':'finish the fix'}}]
        message = self.service.submit('继续项目里的冷启动问题', client_id='same-user-request')
        done = self.service.get(message['id'], wait=5)
        self.assertEqual('done', done['status'])
        until(lambda: self.codex.jobs)
        self.assertEqual(('original-thread', str(self.project), 'finish the fix'),
            (self.codex.jobs[0].session_id, self.codex.jobs[0].cwd, self.codex.jobs[0].text))
        retry = self.service.submit('继续项目里的冷启动问题', client_id='same-user-request')
        self.assertEqual(message['id'], retry['id'])
        self.x.settle(done['card'], 5)
        self.assertEqual(1, len(self.codex.jobs))
        self.assertEqual(key, self.x.cards.get(done['card'])['desktop_card'])

    def test_protocol_adapters_share_selection_without_codex_specific_dispatch(self):
        for kind in ('claude_code', 'dsh', 'pi'):
            with self.subTest(kind=kind):
                agent = Scripted(kind, speaks=False, kind=kind)
                agent.native_resume = True
                self.x._agents[kind] = agent
                if kind not in self.x._order:
                    self.x._order.append(kind)
                key = self.desktop(kind='claude' if kind == 'claude_code' else kind)
                plan, problem = self.plan(key)
                self.assertIsNone(problem)
                result = self.x._continue_desktop(plan['card'], 'continue', 'default', 'turn-'+kind,
                    0, lambda *args: None, task=plan['task'])
                until(lambda: agent.jobs)
                self.assertEqual('original-thread', agent.jobs[0].session_id)
                self.x.settle(result.card, 5)

    def test_non_resumable_native_adapter_is_explicitly_unavailable(self):
        self.x._agents['command'] = Scripted('command', speaks=False, kind='command')
        self.x._order.append('command')
        key = self.desktop(kind='command')
        plan, problem = self.plan(key)
        self.assertIsNone(plan)
        self.assertIn('适配器', problem)
        self.assertEqual([], self.x._agents['command'].jobs)

    def test_managed_tasks_of_every_connected_type_use_existing_followup(self):
        for kind in ('codex','claude_code','dsh','pi','remote','command','echo'):
            with self.subTest(kind=kind):
                agent = Scripted(kind, speaks=False, kind=kind)
                self.x._agents[kind] = agent
                if kind not in self.x._order:
                    self.x._order.append(kind)
                key = self.x.cards.open('default', kind+' work', 'done', kind)['id']
                plan, problem = self.plan(key)
                self.assertIsNone(problem)
                self.assertEqual(('amend', key, 'after'), (plan['type'], plan['card']['id'], plan['mode']))

    def test_busy_waiting_and_missing_targets_never_fall_back_to_new_work(self):
        for status in ('running','waiting'):
            key = self.desktop()
            self.x.cards.update(key, desktop_status=status)
            plan, problem = self.plan(key)
            self.assertIsNone(plan)
            self.assertIn('尚未投递', problem)
        self.assertIsNotNone(self.plan('missing')[1])
        # Open in the owner's client window: the phone's words would only become a hidden branch.
        key = self.desktop()
        self.x.cards.update(key, desktop_status='idle', client_open=True)
        plan, problem = self.plan(key)
        self.assertIsNone(plan)
        self.assertIn('正开在电脑上', problem)
        self.assertEqual([], self.codex.jobs)

    def test_wrong_environment_fails_without_changing_original_agent(self):
        key = self.desktop(native_scope=home_scope(self.folder / 'unregistered-home'))
        original = self.codex.spec
        self.assertIn('运行环境', self.plan(key)[1])
        self.assertIs(original, self.codex.spec)

    def test_gateway_binds_verified_default_native_home_instead_of_worker_home(self):
        home, isolated = self.folder/'native-home', self.folder/'worker-home'
        (home/'sessions').mkdir(parents=True)
        (home/'sessions'/'rollout-original-thread.jsonl').write_text(json.dumps({
            'type':'session_meta', 'payload':{'id':'original-thread', 'cwd':str(self.project)}})+'\n')
        self.x.sessions.config = dataclasses.replace(self.x.config, desktop_codex_dir=home)
        self.codex.spec = dataclasses.replace(self.codex.spec, config_dir=isolated)
        target = self.x.cards.get(self.desktop(native_scope=home_scope(home)))
        with mock.patch('xiaoyu_runtime.loop.session_gateway.create', side_effect=lambda spec: CodexAgent(spec)):
            bound = self.x.sessions.adapter(target, bind=True)
        self.assertEqual(home, bound.spec.config_dir)
        self.assertEqual(isolated, self.codex.spec.config_dir)
        self.assertIsNone(bound.spec.app_server_socket)

    def test_catalog_only_native_source_is_readable_and_selectable_without_new_card(self):
        origin = self.service.runtime_id
        scope = home_scope(Path.home()/'.codex')
        key = uid('native', origin, 'codex', scope, 'catalog-thread')
        row = dict(uid=key, kind='native', authority=origin, agent='codex', native_scope=scope,
            native_session_id='catalog-thread', title='目录中的冷启动任务', cwd=str(self.project),
            state='turn_ended', observed_at=1, updated_at=1, source_revision=1)
        self.x._writer.directory_sources([row], origin)
        target = self.x.sessions.target(key, 'default')
        self.assertEqual('catalog-thread', target['session_id'])
        plan, problem = self.plan(key)
        self.assertIsNone(problem)
        self.assertEqual(key, plan['card']['id'])
        self.assertIsNone(self.x.cards.get(key))

    def test_remote_binding_needs_original_mesh_route(self):
        key = self.desktop(mesh_origin='other-runtime', mesh_id='c-original')
        self.assertIn('来源转交', self.plan(key)[1])
        self.x.mesh = mock.Mock()
        self.assertIsNone(self.plan(key)[1])

    def test_same_toolset_and_speaking_read_boundary_use_gateway(self):
        key = self.desktop()
        box = self.x._toolbox('default', None, self.x.agents(), False)
        found, error = box.call('find_sessions', {'query':'ai passport'})
        self.assertFalse(error)
        self.assertEqual(key, json.loads(found)['matches'][0]['target'])
        _, error = box.call('continue_session', {'card':key, 'task':'continue'})
        self.assertFalse(error)
        self.assertEqual('resume', box.fields('submitted')['action']['type'])
        self.assertIn('find_sessions', {v['name'] for v in box.speaking().definitions()})
        self.assertNotIn('continue_session', {v['name'] for v in box.speaking().definitions()})

    def test_real_adapters_explicitly_advertise_native_resume(self):
        for cls in (CodexAgent, ClaudeCodeAgent, DshAgent, PiAgent):
            self.assertTrue(cls.native_resume)
        self.assertFalse(CommandAgent.native_resume)

    def test_an_existing_resume_blocks_a_second_native_writer(self):
        key = self.desktop()
        active = self.x._writer.task_open('default', 'pending continuation', 'working', 'codex')
        self.x._writer.task(active['id'], borrowed_session='original-thread', borrowed_cwd=str(self.project),
                            borrowed_scope='', desktop_card=key)
        self.assertIn('已有续接', self.plan(key)[1])
        self.x._writer.task(active['id'], state='done')
        self.assertIsNone(self.plan(key)[1])

    def test_codex_wrong_resume_identity_is_rejected_before_starting_a_turn(self):
        from xiaoyu_runtime.helpers import codex_app
        from xiaoyu_runtime.helpers.base import Job, Control
        server = mock.Mock()
        server.expect.return_value = {'thread':{'id':'different-thread'}}
        with mock.patch.object(codex_app, 'handshake'):
            with self.assertRaises(AgentError):
                codex_app._turn(server, self.codex.spec, Job('continue', session_id='original-thread'),
                                Control(), mock.Mock())
        self.assertNotIn('turn/start', [call.args[0] for call in server.request.call_args_list])

    def test_service_continuation_preserves_request_identity_and_scope(self):
        key = self.desktop()
        body = {'text':'continue in the original session', 'client_id':'gateway-request'}
        accepted = self.service.session_continue(key, body)
        done = self.service.get(accepted['id'], wait=5)
        until(lambda: self.codex.jobs)
        self.assertEqual('original-thread', self.codex.jobs[0].session_id)
        self.assertEqual(accepted['id'], self.service.session_continue(key, body)['id'])
        other = self.desktop()
        with self.assertRaises(RequestError):
            self.service.session_continue(other, body)
        self.x.settle(done['card'], 5)
        self.assertEqual(1, len(self.codex.jobs))
        with self.assertRaises(RequestError):
            self.service.session_continue(key, {'text':'continue without retry identity'})

    def test_http_gateway_shares_authorization_and_returns_observed_waiting_state(self):
        key = self.desktop()
        self.x.cards.update(key, desktop_status='waiting')
        server = make_server(dataclasses.replace(self.x.config, host='127.0.0.1', port=0), self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = 'http://127.0.0.1:%s/v1/sessions?query=ai%%20passport' % server.server_port
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(url)
        self.assertEqual(401, error.exception.code)
        request = urllib.request.Request(url, headers={'Authorization':'Bearer '+self.x.config.token})
        with urllib.request.urlopen(request) as response:
            result = json.load(response)
        self.assertEqual('waiting', result['matches'][0]['state'])
        self.assertFalse(result['matches'][0]['resumable'])
        bad = urllib.request.Request(url+'&limit=999', headers={'Authorization':'Bearer '+self.x.config.token})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(bad)
        self.assertEqual(400, error.exception.code)

    def test_search_only_probes_the_bounded_result_window(self):
        for _ in range(4):
            self.desktop()
        with mock.patch.object(self.x.sessions, 'validate', wraps=self.x.sessions.validate) as probe:
            result = self.x.sessions.search('default', limit=2)
        self.assertEqual(2, len(result['matches']))
        self.assertTrue(result['more'])
        self.assertEqual(2, probe.call_count)

    def test_dsh_native_resume_failure_never_creates_a_new_session(self):
        from xiaoyu_runtime.helpers import dsh_app
        from xiaoyu_runtime.helpers.base import Job, Control
        server = mock.Mock()
        server.expect.side_effect = AgentError('session missing')
        with mock.patch.object(dsh_app, 'handshake'):
            with self.assertRaises(AgentError):
                dsh_app._turn(server, self.codex.spec,
                    Job('continue', session_id='original-thread', cwd=str(self.project), require_session=True),
                    Control(), mock.Mock())
        self.assertEqual([mock.call('session/resume', {'sessionId':'original-thread', 'cwd':str(self.project)})], server.request.call_args_list)

    def test_pi_native_resume_requires_exact_existing_source_and_never_creates(self):
        import subprocess
        from xiaoyu_runtime.helpers.base import Job
        home = self.folder/'pi-agent'
        spec = dataclasses.replace(self.codex.spec, type='pi', env={'PI_CODING_AGENT_DIR':str(home)}, env_files={})
        run = mock.Mock(return_value=subprocess.CompletedProcess([], 0, 'finished', ''))
        agent = PiAgent(spec, run=run)
        job = Job('continue', session_id='pi-original', cwd=str(self.project), require_session=True)
        with self.assertRaises(AgentError):
            agent.run(job)
        run.assert_not_called()
        source = home/'sessions'/'project'/'original.jsonl'
        source.parent.mkdir(parents=True)
        source.write_text(json.dumps({'type':'session', 'id':'pi-original', 'cwd':str(self.project)})+'\n')
        result = agent.run(job)
        command = run.call_args.args[0]
        self.assertEqual(str(source.resolve()), command[command.index('--session')+1])
        self.assertNotIn('--session-id', command)
        self.assertEqual('pi-original', result.session_id)

    def test_binding_failure_before_dispatch_does_not_leave_a_phantom_writer(self):
        key = self.desktop()
        with mock.patch.object(self.x.sessions, 'execution_agent', side_effect=AgentError('source changed')):
            with self.assertRaises(AgentError):
                self.x._continue_desktop(self.x.cards.get(key), 'continue', 'default', 'binding-race',
                                         0, lambda *args: None)
        self.assertEqual([], self.codex.jobs)
        self.assertIsNone(self.plan(key)[1])
        failed = [row for row in self.x.cards.session_targets() if row.get('borrowed_session')]
        self.assertEqual(['failed'], [row['state'] for row in failed])
