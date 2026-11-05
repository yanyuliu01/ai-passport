"""Context is a read projection; delivery receipts never own message bodies."""

import json
import unittest
from unittest import mock

from test_runtime import Scripted, TempDirCase, make_xiaoyu
from xiaoyu_runtime.world.cards import Cards
from xiaoyu_runtime.events.intake import Intake
from xiaoyu_runtime.world.store import Store
from xiaoyu_runtime.loop.context import bind as bind_context


class ContextTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.state = self.folder / "state"
        self.cards = Cards(self.state / "cards.json")
        self.intake = Intake.for_cards(self.cards)
        self.writer = self.intake.writer("owner", actor="test")
        self.store = Store(self.state)
        self.store.transcript.resolve_with(lambda mid: (self.cards.message(mid) or {}).get("text"))
        self.context = bind_context(self.store, self.cards)
        self.card = self.writer.task_open("side", "original task")

    def exchange(self, tid="a", text="question", reply="saved answer"):
        self.writer.exchange(self.card["id"], text, reply, turn_id=tid)

    def test_messages_written_by_any_intake_producer_are_available_without_transcript(self):
        self.exchange()
        self.assertEqual([("question", "saved answer")],
                         [(turn["text"], turn["reply"]) for turn in self.context.turns("side")])
        self.assertFalse((self.state / "transcript.json").exists())
        self.assertFalse((self.state / "context.json").exists())
        self.assertEqual(self.context.turns("other"), [])

    def test_main_and_side_can_recall_weixin_without_mixing_timelines(self):
        self.writer.conversation_create_weixin()
        card = self.writer.task_open('weixin', '微信聊天')
        self.writer.exchange(card['id'], '微信 jasmine 问题', '微信 jasmine 回复', turn_id='wx1')
        for conversation in ('default', 'side'):
            self.assertIn('微信 jasmine 回复', self.context.recall(conversation, 'jasmine'))
            self.assertNotIn('微信 jasmine 回复', [turn['reply'] for turn in self.context.turns(conversation)])
        self.context.clear('weixin')
        self.assertNotIn('微信 jasmine 回复', self.context.recall('side', 'jasmine'))

    def test_recent_chat_tool_reads_both_channels_and_only_sent_weixin_bubbles(self):
        from xiaoyu_runtime.channels.store import ChannelStore
        from xiaoyu_runtime.loop.voice_tools import Toolbox
        self.writer.conversation_create_weixin()
        main = self.writer.task_open('default', '主对话')
        self.writer.exchange(main['id'], '主对话提问', '主对话回答', turn_id='main1')
        wx = self.writer.task_open('weixin', '微信聊天')
        self.writer.exchange(wx['id'], '微信提问', '已发送\n\n---\n\n未发送', turn_id='wx1')
        db = ChannelStore(self.state / 'channels/weixin/channel.sqlite')
        self.addCleanup(db.close)
        with db.db:
            db.db.execute("INSERT INTO outbox(destination,block_id,text,state,request_id,kind,sent_at) VALUES(?,?,?,?,?,?,?)",
                          ('owner', 'sent', '已发送', 'sent', 'wx1', 'text', 1))
            db.db.execute("INSERT INTO outbox(destination,block_id,text,state,request_id,kind) VALUES(?,?,?,?,?,?)",
                          ('owner', 'cancelled', '未发送', 'cancelled', 'wx1', 'text'))
        tools = Toolbox([], [], [], lambda _: None, lambda _: None, recent_chat=self.context.recent_chat)
        self.assertIn('read_recent_chat', [d['name'] for d in tools.definitions()])
        result, failed = tools.call('read_recent_chat', {'channel': 'weixin'})
        self.assertFalse(failed)
        self.assertIn('微信提问', result)
        self.assertIn('已发送', result)
        self.assertNotIn('未发送', result)
        result, failed = tools.call('read_recent_chat', {'channel': 'main'})
        self.assertFalse(failed)
        self.assertIn('主对话提问', result)
        batch, failed = tools.call('read_context', {'queries': [{'name': 'read_recent_chat', 'args': {'channel': 'main'}}]})
        self.assertFalse(failed)
        self.assertIn('主对话回答', batch)
        self.assertTrue(tools.call('read_recent_chat', {'channel': 'other'})[1])
        self.assertIn('微信提问', self.context.other_chat_note('default', '总结刚才微信聊了什么'))
        self.assertIn('主对话提问', self.context.other_chat_note('weixin', '看看主对话刚聊了什么'))
        self.assertEqual('', self.context.other_chat_note('default', '你好呀'))
        self.context.clear('weixin')
        self.assertNotIn('微信提问', json.dumps(self.context.recent_chat('weixin'), ensure_ascii=False))

    def test_recent_chat_pages_older_records_and_can_read_earliest(self):
        self.writer.conversation_create_weixin()
        main = self.writer.task_open('default', '主对话')
        for i in range(45):
            self.writer.exchange(main['id'], '提问%02d' % i, '回答%02d' % i, turn_id='page%02d' % i)
        first = self.context.recent_chat('main', limit=7)
        self.assertTrue(first['还有更早'])
        self.assertTrue(all('时间' in item for item in first['记录']))
        seen = [item['内容'] for item in first['记录']]
        page = first
        while page['还有更早']:
            page = self.context.recent_chat('main', limit=7, before=page['向前游标'])
            seen.extend(item['内容'] for item in page['记录'])
        self.assertEqual(len(seen), 90)
        self.assertEqual(len(set(seen)), 90)
        earliest = self.context.recent_chat('main', limit=3, earliest=True)
        self.assertEqual([item['内容'] for item in earliest['记录']], ['提问00', '回答00', '提问01'])
        with self.assertRaises(ValueError):
            self.context.recent_chat('weixin', before=first['向前游标'])
        with self.assertRaises(ValueError):
            self.context.recent_chat('main', before='not a cursor')

    def test_working_read_receipts_survive_restart_but_not_conversation_reset(self):
        self.writer.conversation_create_weixin()
        wx = self.writer.task_open('weixin', '微信')
        self.writer.exchange(wx['id'], '主对话最早到哪天', '我看看', turn_id='working1')
        result = json.dumps({'记录': [{'内容': '不应存入工作回执的原文'}],
                             '还有更早': True, '向前游标': 'cursor1'}, ensure_ascii=False)
        self.context.record_read('weixin', 'working1', 'read_recent_chat', {'channel': 'main'}, result, False)
        restored = bind_context(self.store, self.cards)
        note = restored.working_note('weixin')
        self.assertIn('主对话最早到哪天', note)
        self.assertIn('cursor1', note)
        self.assertNotIn('不应存入工作回执的原文', self.context._path.read_text())
        self.assertIn('不表示后台仍在执行', note)
        self.assertEqual('', restored.working_note('default'))
        restored.clear('weixin')
        self.assertEqual('', restored.working_note('weixin'))

    def test_actual_tool_read_is_recorded_and_clearing_target_invalidates_cursor(self):
        from xiaoyu_runtime.loop.voice_tools import Toolbox
        self.writer.conversation_create_weixin()
        wx = self.writer.task_open('weixin', '微信')
        self.writer.exchange(wx['id'], '看看主对话', '我看看', turn_id='read1')
        main = self.writer.task_open('default', '主对话')
        self.writer.exchange(main['id'], '主对话问题', '主对话回答', turn_id='main1')
        tools = Toolbox([], [], [], lambda _: None, lambda _: None, recent_chat=self.context.recent_chat,
                        observe_read=lambda name, args, result, failed: self.context.record_read(
                            'weixin', 'read1', name, args, result, failed))
        self.assertFalse(tools.call('read_recent_chat', {'channel': 'main', 'limit': 1})[1])
        note = self.context.working_note('weixin')
        self.assertIn('read_recent_chat', note)
        self.assertIn('还有更早', note)
        self.context.clear('default')
        self.assertEqual('', self.context.working_note('weixin'))

    def test_weixin_page_order_uses_bubble_delivery_not_original_request_time(self):
        from xiaoyu_runtime.channels.store import ChannelStore
        self.writer.conversation_create_weixin()
        wx = self.writer.task_open('weixin', '微信')
        with mock.patch('time.time', return_value=100):
            self.writer.exchange(wx['id'], '第一句', '第一答', turn_id='one')
        with mock.patch('time.time', return_value=102):
            self.writer.exchange(wx['id'], '第二句', '第二答', turn_id='two')
        db = ChannelStore(self.state / 'channels/weixin/channel.sqlite')
        self.addCleanup(db.close)
        with db.db:
            for tid, text, stamp in [('one', '第一答', 103), ('two', '第二答', 104)]:
                db.db.execute("INSERT INTO outbox(destination,block_id,text,state,request_id,kind,sent_at) VALUES(?,?,?,?,?,?,?)",
                              ('owner', tid, text, 'sent', tid, 'text', stamp))
        page = self.context.recent_chat('weixin', limit=1)
        items = [page['记录'][0]['内容']]
        while page['还有更早']:
            page = self.context.recent_chat('weixin', limit=1, before=page['向前游标'])
            items.extend(item['内容'] for item in page['记录'])
        self.assertEqual(items, ['第二答', '第一答', '第二句', '第一句'])

    def test_side_recall_uses_weixin_applied_peer_history(self):
        self.writer.conversation_create_weixin()
        calls = []
        def replicas(conversation, terms, covered):
            calls.append(conversation)
            if conversation == 'weixin':
                return ([('网络对话', 'Windows', 10, '微信 lavender 来自已同步副本')], False)
            return ([], False)
        self.context._replica_recall = replicas
        result = self.context.recall('side', 'lavender')
        self.assertIn('微信 lavender 来自已同步副本', result)
        self.assertEqual(calls[:2], ['side', 'weixin'])

    def test_weixin_can_recall_main_and_side_without_mixing_timelines(self):
        self.writer.conversation_create_weixin()
        main = self.writer.task_open('default', '主对话')
        self.writer.exchange(main['id'], 'main tangerine question', 'main tangerine reply', turn_id='main1')
        self.writer.exchange(self.card['id'], 'side tangerine question', 'side tangerine reply', turn_id='side1')
        result = self.context.recall('weixin', 'tangerine')
        self.assertIn('main tangerine', result)
        self.assertIn('side tangerine', result)
        self.assertEqual([], self.context.turns('weixin'))
        self.assertNotIn('main tangerine', self.context.recall('side', 'tangerine'))
        self.context.clear('side')
        result = self.context.recall('weixin', 'tangerine')
        self.assertIn('main tangerine', result)
        self.assertNotIn('side tangerine', result)

    def test_weixin_recalls_applied_replicas_with_one_shared_scan(self):
        self.writer.conversation_create_weixin()
        self.writer.conversation_create('side')
        calls = []
        def replicas(conversation, terms, covered):
            calls.append(conversation)
            if isinstance(conversation, set):
                return ([('网络对话', 'Windows', 10, 'remote peony history')], False)
            return ([], False)
        self.context._replica_recall = replicas
        self.assertIn('remote peony history', self.context.recall('weixin', 'peony'))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], 'weixin')
        self.assertIn('default', calls[1])
        self.assertIn('side', calls[1])

    def test_weixin_shared_archive_respects_reset_and_scans_once(self):
        self.writer.conversation_create_weixin()
        self.exchange('retained')
        archive = self.cards._log._path.with_suffix('.history')
        archive.mkdir()
        records = [{'id': 'old-' + name, 'conversation': name, 'scope': 'main', 'seq': index,
                    'created_at': index, 'status': 'final', 'text': name + ' old marigold',
                    'author': {'kind': 'xiaoyou'}, 'source': {'type': 'runtime'}}
                   for index, name in enumerate(('default', 'side'), 1)]
        (archive / 'segment.jsonl').write_text('\n'.join(json.dumps(row) for row in records) + '\n')
        from xiaoyu_runtime.events import message_history
        with mock.patch.object(message_history, 'page', wraps=message_history.page) as read:
            result = self.context.recall('weixin', 'marigold')
            self.assertEqual(read.call_count, 2)  # one WeChat scan, one shared-chat scan
        self.assertIn('default old marigold', result)
        self.assertIn('side old marigold', result)
        self.assertTrue(self.context.clear('side'))
        result = self.context.recall('weixin', 'marigold')
        self.assertIn('default old marigold', result)
        self.assertNotIn('side old marigold', result)

    def test_weixin_can_find_a_task_without_importing_worker_output_into_chat(self):
        self.writer.conversation_create_weixin()
        task = self.writer.task_open('default', 'riverstone background work', 'working', 'claude')
        result = self.context.recall('weixin', 'riverstone')
        self.assertIn('riverstone background', result)
        self.assertIn(task['id'], result)
        self.assertEqual([], self.context.turns('weixin'))

    def test_weixin_can_search_by_task_source_name(self):
        self.writer.conversation_create_weixin()
        task = self.writer.task_open('default', 'building hardware', 'working', 'claude')
        self.cards._cards[task['id']].update(origin_name='Windows', mesh_origin='b' * 32)
        result = self.context.recall('weixin', 'Windows')
        self.assertIn('Windows', result)
        self.assertIn('building hardware', result)

    def test_weixin_recall_reads_observed_desktop_text_without_importing_private_context(self):
        self.writer.conversation_create_weixin()
        desktop = self.intake.writer('desktop', actor='test')
        card = desktop.observed_open('__desktop__', '同步问题', 'done', 'codex')
        desktop.observed(card['id'], source='desktop', monitoring_only=True, session_id='native-1',
                         origin_name='Windows', entries=[
                             {'id': 'native-1:you', 'role': 'you', 'text': '排查 cedar 同步', 'at': 1},
                             {'id': 'native-1:reply', 'role': 'xiaoyou', 'text': 'cedar 已定位 token=secret123', 'at': 2}])
        result = self.context.recall('weixin', 'cedar')
        self.assertIn('排查 cedar 同步', result)
        self.assertIn('Windows', result)
        self.assertNotIn('secret123', result)
        self.assertEqual([], self.context.turns('weixin'))

    def test_journal_replay_rebuilds_context_without_model_or_transcript(self):
        self.exchange()
        # Simulate a checkpoint missing its final message/task update.
        (self.state / "cards.json").unlink()
        rebuilt = Cards(self.state / "cards.json")
        context = bind_context(Store(self.state), rebuilt)
        self.assertEqual(["saved answer"], [turn["reply"] for turn in context.turns("side")])

    def test_result_and_activity_do_not_duplicate_the_public_summary(self):
        self.exchange()
        self.writer.reply(self.card["id"], "raw worker output", turn_id="r-result",
                          kind="worker_result", scope="task", author="codex")
        self.writer.activity(self.card["id"], "codex", "tool_result", "tool output", "act")
        self.writer.reply(self.card["id"], "summary", turn_id="r-summary", kind="summary")
        self.assertEqual(["saved answer", "summary"],
                         [turn["reply"] for turn in self.context.turns("side")])
        self.assertEqual("（后台的事「original task」有结果了）", self.context.turns("side")[-1]["text"])

    def test_partial_and_failed_replies_are_not_facts_in_context(self):
        self.exchange("saved")
        self.writer.request_changed({"id": "failed", "text": "fail", "conversation": "side",
                                     "status": "failed", "error": "error", "rev": 0})
        self.writer.request_changed({"id": "stream", "text": "still", "conversation": "side",
                                     "status": "running", "partial": "uncommitted", "rev": 0})
        self.assertEqual(["saved"], [turn["id"] for turn in self.context.turns("side")])

    def test_seen_receipts_survive_restart_and_contain_no_message_body(self):
        self.exchange()
        self.context.mark("side", "speaker", self.context.unseen("side", "speaker"))
        raw = (self.state / "context.json").read_text("utf-8")
        self.assertNotIn("question", raw)
        self.assertNotIn("saved answer", raw)
        self.assertEqual([], bind_context(Store(self.state), self.cards).unseen("side", "speaker"))
        self.assertEqual(["a"], [turn["id"] for turn in self.context.unseen("side", "other")])

    def test_receipt_for_a_model_snapshot_does_not_hide_a_concurrent_message_revision(self):
        self.exchange()
        snapshot = self.context.unseen("side", "speaker")
        self.exchange(reply="corrected answer")
        self.context.mark("side", "speaker", snapshot)
        self.assertEqual(["corrected answer"],
                         [turn["reply"] for turn in self.context.unseen("side", "speaker")])

    def test_legacy_references_merge_once_and_the_canonical_revision_wins(self):
        self.store._transcript.add("side", "old-background", "old prompt", "old answer", "worker", [],
                                   reply_ref="a:reply")
        before = (self.state / "transcript.json").read_bytes()
        self.exchange()
        self.assertEqual(["saved answer"], [turn["reply"] for turn in self.context.turns("side")])
        self.assertEqual(before, (self.state / "transcript.json").read_bytes())

    def test_legacy_only_history_and_new_messages_are_both_visible(self):
        self.context.offer("side", [{"id": "old", "text": "older question", "reply": "older answer", "at": 1}])
        self.exchange()
        self.assertEqual(["old", "a"], [turn["id"] for turn in self.context.turns("side")])
        self.assertEqual(0, self.context.offer("side", [{"id": "old", "text": "older question", "reply": "older answer", "at": 1}]))

    def test_archived_remote_dialog_is_recalled_on_demand_without_crossing_conversations(self):
        archive = self.cards._log._path.with_suffix(".history")
        archive.mkdir()
        records = [
            {"id": "bbbbbbbbbbbb.old:reply", "conversation": "side", "scope": "main", "seq": 1,
             "created_at": 10, "status": "final", "text": "remote blue-orchid answer",
             "author": {"kind": "xiaoyou"}, "source": {"type": "runtime"}, "mesh_origin": "b" * 32,
             "task": self.card["id"]},
            {"id": "cccccccccccc.other:reply", "conversation": "other", "scope": "main", "seq": 2,
             "created_at": 11, "status": "final", "text": "private blue-orchid answer",
             "author": {"kind": "xiaoyou"}, "source": {"type": "runtime"}, "mesh_origin": "c" * 32},
            {"id": "external:reply", "conversation": "side", "scope": "main", "seq": 3,
             "created_at": 12, "status": "final", "text": "external blue-orchid answer",
             "author": {"kind": "xiaoyou"}, "source": {"type": "external"}},
            {"id": "relay:you", "conversation": "side", "scope": "main", "seq": 4,
             "created_at": 13, "status": "final", "text": "relayed blue-orchid placeholder",
             "author": {"kind": "owner"}, "relay_to": "other-runtime"},
            {"id": "worker:reply", "conversation": "side", "scope": "main", "seq": 5,
             "created_at": 14, "status": "final", "text": "delegated blue-orchid answer",
             "author": {"kind": "xiaoyou"}, "delegated_from": {"runtime": "local"}},
        ]
        (archive / "segment.jsonl").write_text("\n".join(json.dumps(record) for record in records) + "\n")
        from xiaoyu_runtime.events import message_history
        with mock.patch.object(message_history, "page", wraps=message_history.page) as read:
            self.context.window("side", "reader", query="blue-orchid")
            read.assert_not_called()
            found = self.context.recall("side", "blue-orchid")
            read.assert_called_once()
        self.assertIn("remote blue-orchid answer", found)
        self.assertNotIn("private blue-orchid answer", found)
        self.assertNotIn("external blue-orchid answer", found)
        self.assertNotIn("relayed blue-orchid placeholder", found)
        self.assertNotIn("delegated blue-orchid answer", found)
        self.assertNotIn("remote blue-orchid answer", self.context.recall("other", "blue-orchid"))
        self.cards._cards[self.card["id"]].update(origin_name="Windows", mesh_origin="b" * 32)
        self.assertIn("remote blue-orchid answer", self.context.recall("side", "Windows"))

    def test_archived_history_is_not_revived_after_context_reset(self):
        self.exchange("before-reset")
        archive = self.cards._log._path.with_suffix(".history")
        archive.mkdir()
        (archive / "segment.jsonl").write_text(json.dumps({"id": "archived:reply", "conversation": "side",
            "scope": "main", "seq": 1, "created_at": 1, "status": "final", "text": "old hidden orchid",
            "author": {"kind": "xiaoyou"}, "source": {"type": "runtime"}}) + "\n")
        self.assertIn("old hidden orchid", self.context.recall("side", "orchid"))
        self.assertTrue(self.context.clear("side"))
        self.assertNotIn("old hidden orchid", self.context.recall("side", "orchid"))

    def test_archive_recall_reports_partial_or_unavailable_coverage(self):
        archive = self.cards._log._path.with_suffix(".history")
        archive.mkdir()
        records = [{"id": "turn-%d:reply" % index, "conversation": "side", "scope": "main",
                    "seq": index + 1, "created_at": index + 1, "status": "final",
                    "text": "ancient orchid" if index == 0 else "unrelated filler " * 10,
                    "author": {"kind": "xiaoyou"}, "source": {"type": "runtime"}}
                   for index in range(20)]
        (archive / "segment.jsonl").write_text("\n".join(json.dumps(record) for record in records) + "\n")
        from xiaoyu_runtime.events import message_history
        with mock.patch.object(message_history, "SCAN_BYTES", 256), mock.patch.object(message_history, "LINE_BYTES", 128):
            self.assertIn("部分较近归档", self.context.recall("side", "orchid"))
        with mock.patch.object(message_history, "page", side_effect=OSError("unavailable")):
            self.assertIn("暂时不可用", self.context.recall("side", "orchid"))

    def test_empty_recall_terms_do_not_scan_archive_or_network(self):
        from xiaoyu_runtime.events import message_history
        self.context._replica_recall = mock.Mock()
        with mock.patch.object(message_history, "page") as page:
            self.context.recall("side", "！？")
        page.assert_not_called()
        self.context._replica_recall.assert_not_called()

    def test_reset_is_persistent_without_deleting_messages_and_hides_later_old_revisions(self):
        self.context.offer("side", [{"id": "old", "text": "older", "reply": "reply", "at": 1}])
        self.exchange()
        self.assertTrue(self.context.clear("side"))
        self.assertFalse(self.context.clear("side"))
        self.exchange(reply="a late revision")
        context = bind_context(Store(self.state), self.cards)
        self.assertEqual([], context.turns("side"))
        self.assertEqual("a late revision", self.cards.message("a:reply")["text"])
        self.exchange("new", text="new question", reply="new answer")
        self.assertEqual(["new"], [turn["id"] for turn in context.turns("side")])

    def test_reset_persistence_failure_does_not_claim_history_was_forgotten(self):
        self.exchange()
        with mock.patch("xiaoyu_runtime.loop.context.durable_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.context.clear("side")
        self.assertEqual(["a"], [turn["id"] for turn in self.context.turns("side")])

    def test_receipt_failure_keeps_saved_answer_available(self):
        self.exchange()
        with mock.patch("xiaoyu_runtime.loop.context.durable_write", side_effect=OSError("disk full")):
            self.context.delivered("side", "a", ["speaker"])
        self.assertEqual("saved answer", self.context.turns("side")[0]["reply"])

    def test_background_context_excludes_the_current_handoff(self):
        helper = Scripted("codex", speaks=False)
        x, _ = make_xiaoyu(self.folder / "runtime", [helper])
        self.addCleanup(x.close)
        card = x.hear("@codex inspect this", "default", "new").card
        x.settle(card, 5)
        self.assertEqual("inspect this", helper.jobs[0].text)

    def test_model_context_never_uses_the_unpublished_full_version_of_a_truncated_reply(self):
        self.exchange(reply="visible excerpt")
        self.store._transcript.add("side", "a", "question", "unpublished full answer", "speaker", [])
        self.assertEqual("visible excerpt", self.context.turns("side")[0]["reply"])

    def test_legacy_share_does_not_duplicate_a_current_canonical_turn(self):
        self.exchange()
        self.assertEqual(0, self.context.offer("side", [{"id": "a", "text": "question", "reply": "saved answer", "at": 1}]))
        self.assertFalse((self.state / "transcript.json").exists())

    def test_a_damaged_reset_boundary_does_not_silently_restore_old_context(self):
        (self.state / "context.json").write_text("[]", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            bind_context(Store(self.state), self.cards)

    def test_decision_and_speaker_frames_read_current_task_and_conversation_commitments(self):
        from xiaoyu_runtime.world.promises import Promises
        promises = Promises(self.state / "promises.json")
        context = bind_context(self.store, self.cards, promises=promises)
        promises.track("side commitment", conversation="side")
        promises.track("other commitment", conversation="other")
        before = context.frame("side", self.card)
        self.writer.task(self.card["id"], state="working", agent="codex")
        after = context.frame("side", self.card)
        self.assertEqual("done", before["focus"]["state"])
        self.assertEqual("working", after["focus"]["state"])
        self.assertEqual("codex", after["focus"]["agent"])
        self.assertEqual(["side commitment"], [item["title"] for item in after["promises"]])

    def test_active_tasks_are_not_hidden_by_a_page_of_later_finished_chats(self):
        self.writer.task(self.card["id"], state="working", agent="codex")
        for index in range(32):
            self.writer.task_open("side", "later chat %d" % index)
        frame = self.context.frame("side")
        self.assertIn(self.card["id"], [card["id"] for card in frame["cards"]])
        self.assertEqual(6, len(frame["cards"]))

    def test_speaking_does_not_mark_history_omitted_from_the_prompt_as_seen(self):
        voice = Scripted("speaker")
        x, _ = make_xiaoyu(self.folder / "bounded", [voice])
        self.addCleanup(x.close)
        card = x._writer.task_open("default", "retained history")
        for index in range(20):
            x._writer.exchange(card["id"], "question-%d" % index, "a" * 900,
                               turn_id="old-%d" % index)
        x._speak(voice, "current request", "default", "system", None, 0, catch_up=True)
        self.assertNotIn("question-0", voice.jobs[0].text)
        self.assertIn("old-0", [turn["id"] for turn in x.context.unseen("default", "speaker")])

    def test_a_partial_history_excerpt_is_not_a_full_turn_delivery_receipt(self):
        voice = Scripted("speaker")
        x, _ = make_xiaoyu(self.folder / "excerpt", [voice])
        self.addCleanup(x.close)
        card = x._writer.task_open("default", "long retained answer")
        x._writer.exchange(card["id"], "older question", "a" * 2000 + "unquoted tail", turn_id="long")
        x._speak(voice, "current request", "default", "system", None, 0, catch_up=True)
        self.assertNotIn("unquoted tail", voice.jobs[0].text)
        self.assertEqual(["long"], [turn["id"] for turn in x.context.unseen("default", "speaker")])

    def test_segment_boundaries_are_versioned_and_reading_never_writes_history(self):
        for index in range(65):
            self.exchange("turn-%03d" % index, text="question-%d" % index)
        pages = self.context.segments("side")
        self.assertEqual([32, 32, 1], [page["count"] for page in pages])
        self.assertEqual("turn-000", pages[0]["first"])
        self.assertEqual("turn-064", pages[-1]["last"])
        self.assertEqual(65, len({turn["id"] for page in pages for turn in page["turns"]}))
        self.assertEqual(pages[0], self.context.segment("side", pages[0]["id"]))
        self.context.mark("side", "reader", pages[0]["turns"])
        self.assertEqual([page["id"] for page in pages], [page["id"] for page in self.context.segments("side")])
        self.assertFalse((self.state / "transcript.json").exists())
        raw = (self.state / "context.json").read_text(encoding="utf-8")
        self.assertNotIn("question-", raw)
        self.assertNotIn("saved answer", raw)

    def test_revised_reset_and_foreign_segments_are_not_returned_under_old_ids(self):
        self.exchange()
        segment = self.context.segments("side")[0]
        with self.assertRaises(ValueError):
            self.context.segment("other", segment["id"])
        self.exchange(reply="revised authoritative answer")
        with self.assertRaises(ValueError):
            self.context.segment("side", segment["id"])
        revised = self.context.segments("side")[0]
        self.assertEqual("revised authoritative answer", revised["turns"][0]["reply"])
        self.context.clear("side")
        with self.assertRaises(ValueError):
            bind_context(Store(self.state), self.cards).segment("side", revised["id"])

    def test_returned_segment_is_a_snapshot_and_cannot_mutate_the_source(self):
        self.exchange()
        key = self.context.segments("side")[0]["id"]
        selected = self.context.segment("side", key)
        selected["turns"][0]["reply"] = "client changes snapshot"
        self.assertEqual("saved answer", self.context.segment("side", key)["turns"][0]["reply"])

    def test_window_budget_and_receipts_match_complete_visible_quotes_only(self):
        self.exchange("short", text="entire question", reply="entire answer")
        self.exchange("long", text="long question", reply="a" * 2000 + "not included")
        window = self.context.window("side", "reader", max_chars=2000)
        self.assertLessEqual(len(window["body"]), 2000)
        self.assertIn("不代表完整历史档案", window["body"])
        self.assertIn("片段", window["body"])
        self.assertNotIn("not included", window["body"])
        self.assertIn("long", [turn["id"] for turn in window["turns"]])
        self.assertNotIn("long", [turn["id"] for turn in window["receipts"]])
        self.context.mark("side", "reader", window["turns"])
        self.assertIn("long", [turn["id"] for turn in self.context.unseen("side", "reader")])
        for turn in window["receipts"]:
            self.assertIn(turn["text"], window["body"])
            self.assertIn(turn["reply"], window["body"])

    def test_an_oversized_single_turn_stays_within_the_total_budget(self):
        self.exchange(text="q" * 4000, reply="r" * 4000)
        window = self.context.window("side", "reader", max_chars=256)
        self.assertLessEqual(len(window["body"]), 256)
        self.assertEqual([], window["receipts"])
        self.assertEqual(1, window["coverage"]["excerpts"])

    def test_seed_only_uses_recent_turns_without_receipting_unselected_history(self):
        for index in range(5):
            self.exchange("old-%d" % index, text="q%d" % index)
        window = self.context.window("side", "reader", seed=2)
        self.assertEqual(["old-3", "old-4"], [turn["id"] for turn in window["turns"]])
        self.context.mark("side", "reader", window["receipts"])
        self.assertEqual(["old-0", "old-1", "old-2"],
                         [turn["id"] for turn in self.context.unseen("side", "reader")])
        self.assertEqual("", self.context.window("other", "reader")["body"])

    def test_query_adds_relevant_old_evidence_before_the_recent_seed_without_duplicate_quotes(self):
        for index in range(205):
            self.exchange("old-%03d" % index,
                          text="quartz-anchor " * 20 if index == 0 else "unrelated question %d" % index,
                          reply="original code ORCHID-472" if index == 0 else "later answer")
        window = self.context.window("side", "reader", seed=8, query="quartz-anchor")
        self.assertIn("ORCHID-472", window["body"])
        self.assertIn("old-204", window["body"])
        self.assertEqual(1, window["coverage"]["retrieved"])
        self.assertIn("本轮引用 %d 轮" % len(window["turns"]), window["body"])
        self.assertIn("留存段", window["body"])
        self.assertLessEqual(len(window["body"]), 8000)
        self.assertEqual(len(window["turns"]), len({turn["id"] for turn in window["turns"]}))
        self.assertIn("old-000", [turn["id"] for turn in window["receipts"]])
        self.assertEqual(205, len(self.context.history("side")))
        self.assertFalse((self.state / "context.json").exists())

    def test_query_matches_deep_in_an_old_answer_are_excerpted_and_never_marked_complete(self):
        self.exchange("old", text="old plan", reply="irrelevant " * 300 + "quartz-anchor ORCHID-472" + " tail" * 400)
        for index in range(10):
            self.exchange("recent-%d" % index, text="new question %d" % index)
        window = self.context.window("side", "reader", seed=2, query="quartz-anchor")
        self.assertIn("quartz-anchor ORCHID-472", window["body"])
        self.assertIn("片段", window["body"])
        self.assertNotIn("old", [turn["id"] for turn in window["receipts"]])
        self.context.mark("side", "reader", window["turns"])
        self.assertIn("old", [turn["id"] for turn in self.context.unseen("side", "reader")])

    def test_query_can_requote_seen_evidence_but_obeys_reset_and_conversation_scope(self):
        self.exchange("old", text="quartz-anchor", reply="saved code")
        self.context.mark("side", "reader", self.context.history("side"))
        for index in range(4): self.exchange("new-%d" % index, text="unrelated")
        self.assertIn("saved code", self.context.window("side", "reader", seed=2, query="quartz-anchor")["body"])
        self.assertNotIn("saved code", self.context.window("other", "reader", query="quartz-anchor")["body"])
        self.context.clear("side")
        self.exchange("after-reset", text="new question", reply="fresh answer")
        context = bind_context(Store(self.state), self.cards)
        self.assertNotIn("saved code", context.window("side", "reader", query="quartz-anchor")["body"])

    def test_related_evidence_never_exceeds_the_budget_or_displaces_the_latest_turn(self):
        self.exchange("old", text="quartz-anchor " * 100, reply="saved " * 400)
        for index in range(10): self.exchange("new-%d" % index, text="new question %d" % index)
        for budget in (256, 512, 2000, 8000):
            window = self.context.window("side", "reader", seed=2, query="quartz-anchor", max_chars=budget)
            self.assertLessEqual(len(window["body"]), budget)
            self.assertIn("new-9", [turn["id"] for turn in window["turns"]])

    def test_no_retrieval_scan_when_every_retained_turn_is_already_quoted(self):
        self.exchange()
        original = self.context.window("side", "reader")
        with mock.patch("xiaoyu_runtime.loop.recall.search") as search:
            queried = self.context.window("side", "reader", query="question")
        search.assert_not_called()
        self.assertEqual(original, queried)

    def test_delivered_recent_turns_are_not_requoted_into_short_chat_or_helper_summaries(self):
        self.exchange(text="saved question", reply="saved answer")
        self.context.mark("side", "reader", self.context.history("side"))
        with mock.patch("xiaoyu_runtime.loop.recall.search") as search:
            window = self.context.window("side", "reader", query="saved question 的处理结果")
        search.assert_not_called()
        self.assertEqual("", window["body"])
        self.assertEqual([], window["receipts"])

    def test_query_recognizes_chinese_and_identifiers_without_spaces(self):
        self.exchange("old", text="星河方案的代号是 ORCHID-472，别的方案不适用", reply="方案已保存")
        for index in range(12): self.exchange("new-%d" % index, text="unrelated question")
        for query in ("星河方案的代号是什么", "请查ORCHID-472原始记录"):
            window = self.context.window("side", "reader", seed=2, query=query)
            self.assertIn("ORCHID-472", window["body"])
            self.assertEqual(1, window["coverage"]["retrieved"])

    def test_common_repeated_query_terms_do_not_requote_old_boilerplate(self):
        for index in range(20):
            self.exchange("old-%d" % index, text="Transport verification %d" % index, reply="Ready%d" % index)
        original = self.context.window("side", "reader", seed=2)
        queried = self.context.window("side", "reader", seed=2,
                                      query="Transport verification: reply only Ready999")
        self.assertEqual(original, queried)

    def test_invalid_context_window_parameters_fail_before_a_model_call(self):
        for fields in ({"seed": 0}, {"seed": -1}, {"seed": True}, {"max_chars": 255},
                       {"text_chars": 0}, {"reply_chars": -1}, {"query": []}):
            with self.assertRaises(ValueError):
                self.context.window("side", "reader", **fields)

    def test_retained_history_before_the_recent_window_is_recallable_and_resettable(self):
        from xiaoyu_runtime.world.store import MAX_TURNS
        voice = Scripted("speaker")
        x, store = make_xiaoyu(self.folder / "archive", [voice])
        self.addCleanup(x.close)
        card = x._writer.task_open("default", "retained archive")
        for index in range(MAX_TURNS + 5):
            x._writer.exchange(card["id"], "quartz-old-anchor" if not index else "question %d" % index,
                               "older answer %d" % index, turn_id="retained-%03d" % index)
        self.assertEqual(MAX_TURNS, len(x.context.turns("default")))
        self.assertEqual(MAX_TURNS + 5, len(x.context.history("default")))
        self.assertIn("quartz-old-anchor", x.recall("default", "quartz-old-anchor"))
        self.assertTrue(x.recall("other", "quartz-old-anchor").startswith("没找到"))
        self.assertFalse((store.state_dir / "transcript.json").exists())
        x.context.clear("default")
        self.assertEqual([], x.context.history("default"))
        self.assertTrue(x.recall("default", "quartz-old-anchor").startswith("没找到"))

    def test_recall_reset_does_not_reintroduce_hidden_messages_via_task_entries(self):
        voice = Scripted("speaker")
        x, _ = make_xiaoyu(self.folder / "recall-reset", [voice])
        self.addCleanup(x.close)
        card = x._writer.task_open("default", "retained task facts")
        x._writer.exchange(card["id"], "private-reset-anchor", "saved reply", turn_id="old")
        self.assertIn("private-reset-anchor", x.recall("default", "private-reset-anchor"))
        x.context.clear("default")
        self.assertTrue(x.recall("default", "private-reset-anchor").startswith("没找到"))
        # Current task facts survive a model-context reset; new messages stay readable.
        self.assertIn("retained task facts", x.recall("default", "retained task facts"))
        x._writer.exchange(card["id"], "fresh-after-reset-anchor", "new reply", turn_id="fresh")
        self.assertIn("fresh-after-reset-anchor", x.recall("default", "fresh-after-reset-anchor"))

    def test_recall_keeps_global_memory_and_scoped_task_evidence_without_writes(self):
        voice = Scripted("speaker")
        x, store = make_xiaoyu(self.folder / "recall-sources", [voice])
        self.addCleanup(x.close)
        card = x._writer.task_open("default", "source-anchor task")
        x._writer.exchange(card["id"], "source-anchor question", "source-anchor reply", turn_id="old")
        x.memory.remember("source-anchor preference")
        other = x._writer.task_open("other", "foreign-only-anchor")
        x._writer.exchange(other["id"], "foreign-only-anchor", "other reply", turn_id="foreign")
        before = {p: p.read_bytes() for p in store.state_dir.rglob("*") if p.is_file()}
        result = x.recall("default", "source-anchor")
        for label in ("聊天", "记忆", "事"):
            self.assertIn("[" + label, result)
        self.assertTrue(x.recall("default", "foreign-only-anchor").startswith("没找到"))
        self.assertIn("source-anchor preference", x.recall("other", "source-anchor"))
        self.assertEqual(before, {p: p.read_bytes() for p in store.state_dir.rglob("*") if p.is_file()})
        self.assertEqual([], voice.jobs)

    def test_provider_failure_and_decision_only_do_not_mark_the_window_seen(self):
        from xiaoyu_runtime.helpers.agents import AgentError
        voice = Scripted("speaker")
        x, _ = make_xiaoyu(self.folder / "receipt-boundary", [voice])
        self.addCleanup(x.close)
        card = x._writer.task_open("default", "history")
        x._writer.exchange(card["id"], "old question", "old answer", turn_id="old")
        voice.fail_on = "failed current request"
        with self.assertRaises(AgentError):
            x._speak(voice, "failed current request", "default", "system", None, 0, catch_up=True)
        self.assertEqual(["old"], [turn["id"] for turn in x.context.unseen("default", "speaker")])
        voice.fail_on = None
        x._speak(voice, "decision", "default", "system", None, 0, catch_up=True, decision_only=True)
        self.assertEqual(["old"], [turn["id"] for turn in x.context.unseen("default", "speaker")])
        x._speak(voice, "reply", "default", "system", None, 0, catch_up=True)
        self.assertEqual([], x.context.unseen("default", "speaker"))


if __name__ == "__main__":
    unittest.main()
