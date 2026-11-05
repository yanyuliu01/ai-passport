import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_runtime import TempDirCase, GateTalker, make_xiaoyu
from xiaoyu_runtime.errors import EffectUncertain
from xiaoyu_runtime.loop.voice_tools import Toolbox
from xiaoyu_runtime.world.memory import OwnerMemory
from xiaoyu_runtime.loop.standing_context import render, read_topic


class SharedMemoryContextTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.voice = GateTalker()
        self.x, _ = make_xiaoyu(self.folder, [self.voice])
        self.addCleanup(self.x.close)
        self.x.intake.writer('owner', actor='owner').conversation_create_weixin()

    def test_weixin_upkeep_updates_shared_memory_without_copying_chat(self):
        card = self.x._writer.task_open('weixin', 'wx')
        self.x._writer.exchange(card['id'], '最早一条', '好', turn_id='wx-1')
        later = time.time() + 3600
        self.x._pulse._maybe_weixin_upkeep(later)
        self.x._writer.exchange(card['id'], '以后自然说话', '好', turn_id='wx-2')
        self.x._writer.exchange(card['id'], '不要过度拆分', '明白', turn_id='wx-3')
        self.voice.tool_calls = [('remember', {'note': '聊天自然简短', 'kind': '偏好'})]
        self.voice.answers = ['（好了）']
        self.x._pulse._maybe_weixin_upkeep(later)
        self.assertIn('聊天自然简短', self.x.memory.texts())
        self.x.hear('你好', 'default', 'default-new', report=lambda *a: None)
        prompt = self.voice.tool_jobs[-1].system
        self.assertIn('聊天自然简短', prompt)
        self.assertNotIn('以后自然说话', self.voice.tool_jobs[-1].text)
        self.assertTrue(self.x._pulse._weixin_upkeep._marker.exists())
        self.assertFalse(self.x._pulse._upkeep._marker.exists())

    def test_upkeep_lanes_alternate_without_starving_weixin(self):
        pulse = self.x._pulse
        with mock.patch.object(pulse._upkeep, 'maybe') as default, \
             mock.patch.object(pulse._weixin_upkeep, 'maybe') as weixin, \
             mock.patch.object(pulse, '_maybe_summary'), \
             mock.patch.object(self.x.relationship, 'due', return_value=False):
            pulse.tick(now=time.time() + 3600)
            pulse.tick(now=time.time() + 3600)
        self.assertEqual(1, default.call_count)
        self.assertEqual(1, weixin.call_count)

    def test_busy_memory_lock_keeps_weixin_next_without_blocking_chat(self):
        pulse = self.x._pulse
        pulse._upkeep_next = 'weixin'
        with mock.patch.object(pulse._weixin_upkeep, 'maybe') as weixin, \
             mock.patch.object(self.x.relationship, 'due', return_value=False):
            pulse._memory_upkeep_lock.acquire()
            try:
                pulse.tick(now=time.time() + 3600)
                self.assertEqual('weixin', pulse._upkeep_next)
                self.assertFalse(weixin.called)
            finally:
                pulse._memory_upkeep_lock.release()
            pulse.tick(now=time.time() + 3600)
            self.assertEqual(1, weixin.call_count)

    def test_relationship_uses_shared_memory_and_invalidates_after_correction(self):
        self.x.memory.remember('喜欢自然聊天', kind='偏好')
        notes, rev = self.x.memory.snapshot()
        now = time.time()
        item = self.x.relationship.candidate('weixin', [], rev, now, memory_notes=notes)
        self.x.relationship.begin('weixin', item, now)
        output = json.dumps({'points': [{'text': '喜欢自然聊天', 'turns': ['memory:1']}]})
        self.assertTrue(self.x.relationship.save('weixin', item, output, now))
        self.assertIn('喜欢自然聊天', self.x.relationship.render('weixin', [], rev, now, memory_notes=notes))
        self.x.memory.replace(1, '希望聊天更简短')
        new_notes, new_rev = self.x.memory.snapshot()
        self.assertEqual('', self.x.relationship.render('weixin', [], new_rev, now, memory_notes=new_notes))
        self.assertGreater(self.x.memory.correction_after(), 0)
        item2 = self.x.relationship.candidate('default', [], new_rev, now, memory_notes=new_notes,
                                             correction_after=self.x.memory.correction_after())
        self.assertIn('希望聊天更简短', item2['source'][0]['fact'])
        self.assertNotIn('喜欢自然聊天', json.dumps(item2['source'], ensure_ascii=False))

    def test_default_memory_is_visible_in_weixin(self):
        self.voice.calls = [('remember', {'note': '共享偏好', 'kind': '偏好'})]
        self.x.hear('记住这个', 'default', 'd-1', report=lambda *a: None)
        self.x.hear('你好', 'weixin', 'wx-1', report=lambda *a: None)
        self.assertIn('共享偏好', self.voice.tool_jobs[-1].system)

    def test_old_derived_summary_does_not_return_after_forget(self):
        self.x.memory.remember('喜欢旧头像')
        card = self.x._writer.task_open('default', 'history')
        for i in range(65):
            self.x._writer.exchange(card['id'], '旧头像 %d' % i, '好', turn_id='history-%d' % i)
        now = time.time() + 1000
        segment = self.x.context.summary_candidate('default', now=now)
        self.assertIsNotNone(segment)
        self.assertTrue(self.x.context.begin_summary('default', segment, now=now))
        payload = json.dumps({'points': [{'text': '喜欢旧头像', 'turns': [segment['turns'][0]['id']]}]})
        self.assertTrue(self.x.context.save_summary('default', segment, payload))
        self.assertIn('喜欢旧头像', self.x.context.window('default', 'reader', seed=8)['summary_body'])
        self.x.memory.forget(1)
        window = self.x.context.window('default', 'reader', seed=8)
        self.assertNotIn('喜欢旧头像', window.get('summary_body', ''))
        self.assertIsNone(self.x.context.summary_candidate('default', now=time.time() + 2000))
        self.assertGreater(self.x.memory.correction_after(), 0)

    def test_stale_background_memory_write_cannot_restore_forgotten_preference(self):
        self.x.memory.remember('旧偏好')
        _, old_revision = self.x.memory.snapshot()
        box = Toolbox([], [], [], lambda _: None, lambda _: None, memory=self.x.memory,
                      only=['remember'], expected_memory_revision=old_revision)
        self.x.memory.forget(1)
        with self.assertRaises(EffectUncertain):
            box.call('remember', {'note': '旧偏好'})
        self.assertEqual([], self.x.memory.texts())

    def test_weixin_bootstrap_is_bounded_and_uses_its_own_cursor(self):
        card = self.x._writer.task_open('weixin', 'wx')
        for i in range(25):
            self.x._writer.exchange(card['id'], 'message-%02d' % i, 'ok', turn_id='wx-%d' % i)
        self.x._pulse._maybe_weixin_upkeep(time.time() + 3600)
        self.voice.answers = ['（好了）']
        self.x._pulse._maybe_weixin_upkeep(time.time() + 3600)
        self.assertEqual(1, len(self.voice.gate_jobs))
        self.assertNotIn('message-00', self.voice.gate_jobs[0].text)
        self.assertIn('message-05', self.voice.gate_jobs[0].text)
        self.assertIn('message-24', self.voice.gate_jobs[0].text)

    def test_correction_boundary_survives_reload_and_new_memory_writes(self):
        self.x.memory.remember('旧偏好')
        self.x.memory.forget(1)
        cutoff = self.x.memory.correction_after()
        self.x.memory.remember('新偏好')
        loaded = OwnerMemory(self.x.config.state_dir / 'memory' / 'owner.md')
        self.assertEqual(cutoff, loaded.correction_after())
        self.assertEqual(self.x.memory.revision(), loaded.revision())
        self.assertEqual(['新偏好'], loaded.texts())

    def test_one_upkeep_can_save_multiple_new_preferences(self):
        _, rev = self.x.memory.snapshot()
        box = Toolbox([], [], [], lambda _: None, lambda _: None, memory=self.x.memory,
                      only=['remember'], expected_memory_revision=rev)
        self.assertFalse(box.call('remember', {'note': '偏好一'})[1])
        self.assertFalse(box.call('remember', {'note': '偏好二'})[1])
        self.assertEqual(['偏好一', '偏好二'], self.x.memory.texts())

    def test_correction_fences_older_user_and_topic_files_without_deleting_them(self):
        root = self.x.config.state_dir / 'standing-context'
        (root / 'topics').mkdir(parents=True)
        user = root / 'USER.md'
        topic = root / 'topics' / '头像.md'
        user.write_text('旧偏好', encoding='utf-8')
        topic.write_text('旧头像资料', encoding='utf-8')
        self.x.memory.remember('旧偏好')
        self.x.memory.forget(1)
        cutoff = self.x.memory.correction_after()
        self.assertNotIn('旧偏好', render(self.x.config.state_dir, '头像', correction_after=cutoff))
        self.assertNotIn('旧头像资料', read_topic(self.x.config.state_dir, '头像.md', correction_after=cutoff))
        self.assertTrue(user.exists())
        self.assertTrue(topic.exists())
        time.sleep(0.01)
        topic.write_text('重新核对过的资料', encoding='utf-8')
        self.assertIn('重新核对过的资料', read_topic(self.x.config.state_dir, '头像.md', correction_after=cutoff))

    def test_correction_rotates_provider_sessions_in_both_channels(self):
        self.x.hear('旧话题只在这里', 'weixin', 'wx-old', report=lambda *a: None)
        self.x.hear('主聊天旧话题', 'default', 'd-old', report=lambda *a: None)
        self.x.memory.remember('旧偏好')
        self.x.memory.forget(1)
        self.x.hear('换个话题', 'weixin', 'wx-new', report=lambda *a: None)
        wx = self.voice.tool_jobs[-1]
        self.assertIsNone(wx.session_id)
        self.assertNotIn('旧话题只在这里', wx.text)
        self.x.hear('继续聊', 'weixin', 'wx-next', report=lambda *a: None)
        self.assertEqual('t1', self.voice.tool_jobs[-1].session_id)
        self.x.hear('新的话题', 'default', 'd-new', report=lambda *a: None)
        self.assertIsNone(self.voice.tool_jobs[-1].session_id)
        self.assertNotIn('主聊天旧话题', self.voice.tool_jobs[-1].text)
        self.assertTrue(any(t['text'] == '旧话题只在这里' for t in self.x.context.history('weixin')))

    def test_correction_session_fence_survives_restart(self):
        self.x.hear('旧话题', 'weixin', 'wx-old', report=lambda *a: None)
        self.x.memory.remember('旧偏好')
        self.x.memory.forget(1)
        self.x.hear('新的话题', 'weixin', 'wx-new', report=lambda *a: None)
        self.assertIsNone(self.voice.tool_jobs[-1].session_id)
        self.x.close()
        voice = GateTalker()
        restored, _ = make_xiaoyu(self.folder, [voice])
        self.addCleanup(restored.close)
        restored.hear('继续', 'weixin', 'wx-after-restart', report=lambda *a: None)
        self.assertEqual('t1', voice.tool_jobs[-1].session_id)
        self.assertNotIn('旧偏好', voice.tool_jobs[-1].system)


if __name__ == '__main__':
    unittest.main()
