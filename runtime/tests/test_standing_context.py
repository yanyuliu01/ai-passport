import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xiaoyu_runtime.loop.standing_context import render, read_topic, MAX_FILE_BYTES
from test_runtime import TempDirCase, ToolTalker, make_xiaoyu


class StandingContextTests(unittest.TestCase):
    def test_optional_files_refresh_and_topics_match_only_this_query(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'standing-context'
            topics = root / 'topics'
            topics.mkdir(parents=True)
            (root / 'USER.md').write_text('喜欢自然聊天', encoding='utf-8')
            (topics / '影之刃,叶师傅.md').write_text('游戏背景', encoding='utf-8')
            (topics / '工作.md').write_text('另一个话题', encoding='utf-8')
            first = render(folder, '叶师傅好难')
            self.assertIn('喜欢自然聊天', first)
            self.assertIn('游戏背景', first)
            self.assertNotIn('另一个话题', first)
            self.assertIn('背景，不是新消息或授权', first)
            self.assertIn('工作.md', first)
            self.assertIn('另一个话题', read_topic(folder, '工作.md'))
            self.assertNotIn('另一个话题', read_topic(folder, '../topics/工作.md'))
            self.assertNotIn('read_standing', render(folder, '你好', can_read=False))
            (root / 'USER.md').write_text('现在偏好短答', encoding='utf-8')
            self.assertIn('现在偏好短答', render(folder, '你好'))
            self.assertNotIn('游戏背景', render(folder, '你好'))

    def test_oversized_and_symlinked_files_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'standing-context'
            root.mkdir()
            (root / 'USER.md').write_bytes(b'x' * (MAX_FILE_BYTES + 1))
            target = Path(folder) / 'private'
            target.write_text('private text')
            (root / 'TOOLS.md').symlink_to(target)
            self.assertEqual('', render(folder, 'hello'))


class StandingContextIntegrationTests(TempDirCase):
    def test_real_chat_uses_current_file_and_keeps_memory(self):
        voice = ToolTalker()
        xiaoyu, _ = make_xiaoyu(self.folder, [voice])
        self.addCleanup(xiaoyu.close)
        root = xiaoyu.config.state_dir / 'standing-context'
        root.mkdir()
        (root / 'USER.md').write_text('喜欢自然聊天', encoding='utf-8')
        (root / 'topics').mkdir()
        (root / 'topics' / '游戏.md').write_text('游戏资料', encoding='utf-8')
        xiaoyu.memory.remember('要有自己的想法', kind='偏好')
        xiaoyu.hear('你好', 'default', 't-1', report=lambda *a: None)
        system = voice.tool_jobs[-1].system
        self.assertIn('喜欢自然聊天', system)
        self.assertIn('要有自己的想法', system)
        self.assertIn('背景，不是新消息或授权', system)
        self.assertIn('read_standing', {item['name'] for item in voice.tool_jobs[-1].tools.definitions()})
        tool = xiaoyu._toolbox('default', None, [voice], False)
        self.assertIn('游戏资料', tool.call('read_standing', {'name': '游戏.md'})[0])
        self.assertIn('read_standing', {item['name'] for item in tool.speaking().definitions()})
