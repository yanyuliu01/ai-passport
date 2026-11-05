import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xiaoyu_runtime.loop.relationship import Relationship, INTERVAL, EXPIRES
from xiaoyu_runtime.loop.proactive import Pulse
from xiaoyu_runtime.helpers.agents import Outcome
from test_runtime import TempDirCase, ToolTalker, make_xiaoyu


def turns():
    return [{'id': str(i), 'text': '主人 %d' % i, 'reply': '小鱼 %d' % i} for i in range(8)]


class RelationshipTests(unittest.TestCase):
    def test_save_refresh_is_scoped_and_invalidated_by_source_or_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Relationship(folder)
            history = turns()
            candidate = cache.candidate('weixin', history, 'rev1', 100)
            cache.begin('weixin', candidate, 100)
            result = json.dumps({'points': [{'text': '偏好自然接话', 'turns': ['2']}]}, ensure_ascii=False)
            self.assertTrue(cache.save('weixin', candidate, result, 101))
            self.assertIn('偏好自然接话', cache.render('weixin', history + [turns()[0]], 'rev1', 102))
            self.assertEqual('', cache.render('default', history, 'rev1', 102))
            self.assertEqual('', cache.render('weixin', history, 'rev2', 102))
            self.assertEqual('', cache.render('weixin', [t for t in history if t['id'] != '2'], 'rev1', 102))
            self.assertEqual('', cache.render('weixin', history, 'rev1', 102 + EXPIRES))
            self.assertIsNone(cache.candidate('weixin', history, 'rev1', 100 + INTERVAL - 1))

    def test_rejects_unverifiable_and_secret_outputs_and_secret_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Relationship(folder)
            history = turns()
            history[0]['text'] = '密码：abcdef'
            history[1]['text'] = '（小鱼主动说）'
            candidate = cache.candidate('weixin', history, 'rev', 100)
            self.assertNotIn('0', [t['id'] for t in candidate['source']])
            self.assertNotIn('1', [t['id'] for t in candidate['source']])
            cache.begin('weixin', candidate, 100)
            for item in ({'text': '不存在', 'turns': ['wrong']},
                         {'text': '密码：abcdef', 'turns': ['2']}):
                self.assertFalse(cache.save('weixin', candidate, json.dumps({'points': [item]}), 101))
            self.assertEqual('', cache.render('weixin', history, 'rev', 102))


class RelationshipLifecycleTests(TempDirCase):
    def test_chat_injects_valid_summary_and_reset_removes_it(self):
        voice = ToolTalker('voice')
        xiaoyu, _ = make_xiaoyu(self.folder, [voice])
        self.addCleanup(xiaoyu.close)
        for i in range(4):
            xiaoyu.hear('你好%d' % i, 'default', 't-%d' % i, report=lambda *a: None)
        history = xiaoyu.context.history('default')
        now = time.time()
        revision = xiaoyu.memory.revision()
        candidate = xiaoyu.relationship.candidate('default', history, revision, now)
        xiaoyu.relationship.begin('default', candidate, now)
        result = json.dumps({'points': [{'text': '喜欢自然接话', 'turns': [candidate['source'][0]['id']]}]})
        self.assertTrue(xiaoyu.relationship.save('default', candidate, result, now))
        xiaoyu.hear('再聊聊', 'default', 't-new', report=lambda *a: None)
        self.assertIn('喜欢自然接话', voice.tool_jobs[-1].system)
        xiaoyu.reset('default')
        xiaoyu.hear('重新开始', 'default', 't-after', report=lambda *a: None)
        self.assertNotIn('喜欢自然接话', voice.tool_jobs[-1].system)

    def test_quiet_background_call_is_scoped_and_not_repeated(self):
        voice = ToolTalker('voice')
        xiaoyu, _ = make_xiaoyu(self.folder, [voice])
        self.addCleanup(xiaoyu.close)
        pulse = Pulse(xiaoyu)
        history = turns()
        result = json.dumps({'points': [{'text': '说话自然', 'turns': ['2']}]}, ensure_ascii=False)
        with patch.object(xiaoyu.context, 'summary_activity', return_value=(1, 1, False)), \
             patch.object(xiaoyu.context, 'history', return_value=history), \
             patch.object(xiaoyu, '_speak', return_value=Outcome(result, None)) as call:
            pulse._maybe_relationship('weixin', 10000)
            pulse._maybe_relationship('weixin', 10001)
        self.assertEqual(1, call.call_count)
        self.assertEqual('weixin', call.call_args.args[2])
        self.assertTrue(call.call_args.kwargs['ephemeral'])
        self.assertIn('说话自然', xiaoyu.relationship.render('weixin', history, xiaoyu.memory.revision(), 10001))
        self.assertEqual('', xiaoyu.relationship.render('default', history, xiaoyu.memory.revision(), 10001))

    def test_interrupted_generation_cannot_publish_stale_result(self):
        voice = ToolTalker('voice')
        xiaoyu, _ = make_xiaoyu(self.folder, [voice])
        self.addCleanup(xiaoyu.close)
        pulse = Pulse(xiaoyu)
        history = turns()
        result = json.dumps({'points': [{'text': '过期内容', 'turns': ['2']}]}, ensure_ascii=False)
        def speak(*args, **kwargs):
            xiaoyu.interrupt('weixin')
            return Outcome(result, None)
        with patch.object(xiaoyu.context, 'summary_activity', return_value=(1, 1, False)), \
             patch.object(xiaoyu.context, 'history', return_value=history), \
             patch.object(xiaoyu, '_speak', side_effect=speak):
            pulse._maybe_relationship('weixin', 10000)
        self.assertEqual('', xiaoyu.relationship.render('weixin', history, xiaoyu.memory.revision(), 10001))


if __name__ == '__main__':
    unittest.main()
