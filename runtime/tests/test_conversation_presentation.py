"""Conversation identity, provenance and durable audit regression coverage."""
import json
import unittest

from test_runtime import TempDirCase, Scripted, make_xiaoyu
from xiaoyu_runtime.world.cards import Cards
from xiaoyu_runtime.events.record import Record
from xiaoyu_runtime.api.service import Service, RequestError
from xiaoyu_runtime.world.conversations import ConversationCatalog


class ConversationTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.xiaoyu, self.store = make_xiaoyu(self.folder, [Scripted('claude')])
        self.service = Service(self.xiaoyu, self.store)
        self.addCleanup(self.service.close)
        self.addCleanup(self.xiaoyu.close)

    def test_catalog_create_retry_and_manual_title_survive_restart(self):
        a = self.service.conversation_create({'id': 'side-test'})
        b = self.service.conversation_create({'id': 'side-test'})
        self.assertEqual(a, b)
        self.service.conversation_rename(a['id'], '我的主题')
        saved = ConversationCatalog(self.store.state_dir / 'conversations.json')
        self.assertEqual('我的主题', saved.get(a['id'])['title'])
        revision = saved.get(a['id'])['revision']
        saved.rename(a['id'], '迟到的自动标题', expected=revision - 1, source='gateway')
        self.assertEqual('我的主题', saved.get(a['id'])['title'])

    def test_branch_is_attributed_context_not_a_new_owner_message(self):
        record = self.xiaoyu.record
        card = record.task_open('default', 'source')
        record.exchange(card['id'], '一句话', '答复', turn_id='original')
        before = self.xiaoyu.cards.sync_since(None, 0)[2]
        side = self.service.conversation_create({'id': 'side-branch', 'from_message': 'original:you'})
        self.assertEqual('original:you', side['context']['id'])
        self.assertEqual('owner', side['context']['author']['kind'])
        after = self.xiaoyu.cards.sync_since(None, 0)[2]
        self.assertEqual([m['id'] for m in before], [m['id'] for m in after])
        self.assertFalse(any(m['conversation'] == side['id'] for m in after))
        with self.assertRaises(RequestError):
            self.service.conversation_create({'from_message': 'missing'})

    def test_all_conversations_sync_is_explicit_and_scoped_sync_stays_private(self):
        for conversation in ('default', 'side-one'):
            card = self.xiaoyu.record.task_open(conversation, 'title')
            self.xiaoyu.record.exchange(card['id'], conversation, 'answer', turn_id=conversation)
        scoped = self.service.sync('default')
        self.assertFalse(any(m['conversation'] == 'side-one' for m in scoped['messages']))
        all_ = self.service.sync('default', all_conversations=True)
        self.assertTrue(any(m['conversation'] == 'side-one' for m in all_['messages']))
        self.assertTrue(any(t['conversation'] == 'side-one' for t in all_['tasks']))

    def test_audit_survives_sync_retention_and_queued_relay_is_not_worker_progress(self):
        record = self.xiaoyu.record
        card = record.task_open('default', 'task', 'working', 'codex')
        record.activity(card['id'], 'codex', 'tool_start', 'run tests', 'event-1')
        at = self.xiaoyu.cards.get(card['id'])['last_meaningful_at']
        record.activity(card['id'], 'xiaoyou', 'relay_queued', '等待转交', 'event-2')
        self.assertEqual(at, self.xiaoyu.cards.get(card['id'])['last_meaningful_at'])
        page = self.service.task_audit(card['id'])
        self.assertEqual(['tool_start', 'relay_queued'], [e['phase'] for e in page['events']])
        # A fresh store can still inspect the public audit independently of transient sync cache.
        reopened = Cards(self.store.state_dir / 'cards.json')
        self.assertEqual(['event-1', 'event-2'], [e['id'] for e in reopened.audit.page(card['id'])['events']])
        self.assertEqual(at, reopened.get(card['id'])['last_meaningful_at'])
        self.assertEqual(['event-1'], [e['id'] for e in reopened.audit.page(card['id'], before=page['events'][1]['seq'])['events']])

    def test_all_conversations_sync_includes_side_approval_and_its_terminal_state(self):
        card = self.xiaoyu.record.task_open('side-approval', 'task', 'working', 'codex')
        approvals = self.xiaoyu.approvals
        approval = approvals.ask(card['id'], 'side-approval', 'codex', 'command', 'run tests')
        self.assertFalse(any(a['id'] == approval for a in self.service.sync('default')['approvals']))
        shared = self.service.sync('default', all_conversations=True)
        self.assertEqual('pending', next(a['status'] for a in shared['approvals'] if a['id'] == approval))
        self.assertTrue(approvals.answer(approval, 'deny'))
        delta = self.service.sync('default', after=shared['seq'], all_conversations=True)
        self.assertEqual('denied', next(a['status'] for a in delta['approvals'] if a['id'] == approval))

    def test_supplement_intent_is_durable_before_a_worker_queue_accepts_it(self):
        import threading
        from xiaoyu_runtime.helpers.tasks import Tasks, Task
        worker = Scripted('codex', speaks=False)
        worker.gate.clear()
        background = Tasks(self.store, lambda *args: None)
        self.addCleanup(background.close)
        task = Task('c1', 'default', worker, 'slow')
        background.start(task)
        persisted = []
        self.assertEqual('after', background.amend('c1', 'after', 'extra', before_queue=lambda: persisted.append('stored')))
        self.assertEqual(['stored'], persisted)
        self.assertEqual(['extra'], list(task.after))
        def fails():
            raise OSError('disk full')
        with self.assertRaises(OSError):
            background.amend('c1', 'after', 'must not run', before_queue=fails)
        self.assertEqual(['extra'], list(task.after))
        worker.gate.set()

    def test_binding_survives_restart_and_chat_supplement_does_not_become_new_task(self):
        record = self.xiaoyu.record
        card = record.task_open('side-one', 'task', 'done', 'claude')
        record.task(card['id'], worker_session='native-1', worker_cwd='/project', worker_agent='claude')
        reopened = Cards(self.store.state_dir / 'cards.json')
        self.assertEqual('native-1', reopened.get(card['id'])['worker_session'])
        with record.opening(reply_scope='main'):
            record.exchange(card['id'], '补充要求', '收到', turn_id='add', scope='task')
        self.assertEqual('main', self.xiaoyu.cards.message('add:you')['scope'])
        with self.assertRaises(RequestError):
            self.service.submit('补充', conversation='default', card=card['id'], pin=True, reply_scope='main')


if __name__ == '__main__':
    unittest.main()
