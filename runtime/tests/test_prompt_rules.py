"""Replacing a character must preserve operating rules at every model entry."""
import sys
import unittest
import time
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import TempDirCase, ToolTalker, Scripted, make_xiaoyu
from xiaoyu_runtime.loop.xiaoyu import lane_prompt, voice_prompt, task_prompt, WEIXIN_CHAT_STYLE, weixin_paragraph_bubbles
from xiaoyu_runtime.loop.prompt_rules import RUNTIME_RULES
from xiaoyu_runtime.loop.proactive import Pulse, GATE_RULES
from xiaoyu_runtime.helpers.agents import Outcome


class PromptRulesTests(unittest.TestCase):
    def test_weixin_plain_paragraphs_split_but_structured_or_requested_text_stays_whole(self):
        reply = '能用，这版读着清爽\n\n精简掉了哪些内容？'
        self.assertEqual(weixin_paragraph_bubbles(reply, '试试'),
                         '能用，这版读着清爽\n\n---\n\n精简掉了哪些内容？')
        for text, owner in [
            ('好呀', '你好'),
            ('一句\n\n另一句', '请放在一个气泡'),
            ('一句\n\n另一句', '这次不分段'),
            ('一句\n\n另一句', '请给我原文'),
            ('三个点子：\n\n1. 走走\n2. 看书', '列三个点子'),
            ('说明\n\n```\na\n---\nb\n```', '看看'),
            ('先说\n---\n再说', '试试'),
            ('“一段引用”\n\n“另一段引用”', '解释'),
            ('地址\n\nhttps://example.com', '看看'),
            ('甲\n\n乙\n\n丙', '看看'),
        ]:
            with self.subTest(text=text, owner=owner):
                self.assertEqual(weixin_paragraph_bubbles(text, owner), text)

    def test_time_remains_present_in_c_locale_as_used_by_launchd(self):
        env = dict(os.environ, LC_ALL='C', LANG='C', PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        code = ("import sys; sys.stdout.reconfigure(encoding='utf-8'); "
                "from xiaoyu_runtime.loop.xiaoyu import _turn_context; "
                "from xiaoyu_runtime.loop.project_brief import _when; "
                "print(_turn_context(1730000000, [], [])[1]); "
                "print(_when(1730000000, 1730000000))")
        result = subprocess.run([sys.executable, '-c', code], env=env, check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        lines = result.stdout.decode('utf-8').strip().splitlines()
        self.assertRegex(lines[0], r'^现在是 \d{4}-\d{2}-\d{2} \d{2}:\d{2}（周[一二三四五六日]）。$')
        self.assertRegex(lines[1], r'^今天 \d{2}:\d{2}$')

    def test_chat_and_result_formats_share_current_time_and_memory(self):
        now = 1730000000
        memory = ['真实偏好']
        promises = [{'id': 'p1', 'title': '约定', 'due': None}]
        chat = lane_prompt('P', 80, [], [], None, now, tools=True,
                           memory=memory, promises=promises)
        expected_time = time.strftime('%Y-%m-%d %H:%M', time.localtime(now))
        for text in (False, True):
            with self.subTest(text=text):
                result = voice_prompt('P', 80, text, memory, now, promises)
                for value in (expected_time, '真实偏好', 'p1', '约定'):
                    self.assertIn(value, chat)
                    self.assertIn(value, result)
                self.assertNotIn('用 resolve 关掉', result)

    def test_custom_character_keeps_contract_in_each_role_and_format(self):
        character = 'A quiet owl who speaks English.'
        prompts = [lane_prompt(character, 80, [], [], None, 0, tools=tools)
                   for tools in (False, True)]
        prompts += [voice_prompt(character, 80, text=text) for text in (False, True)]
        prompts += [task_prompt(character, 80)]
        for prompt in prompts:
            with self.subTest(prompt=prompt[-80:]):
                self.assertIn(character, prompt)
                self.assertEqual(prompt.count(RUNTIME_RULES), 1)
                self.assertLess(prompt.index(character), prompt.index(RUNTIME_RULES))
        self.assertIn('JSON', prompts[0])
        self.assertIn('start_task', prompts[1])
        self.assertIn('## 这一次', prompts[-1])


class WakeRulesTests(TempDirCase):
    def test_final_weixin_reply_uses_two_bubbles_without_changing_main_chat(self):
        voice = ToolTalker()
        xiaoyu, _ = make_xiaoyu(self.folder, [voice])
        self.addCleanup(xiaoyu.close)
        xiaoyu.intake.writer('owner', actor='owner').conversation_create_weixin()
        weixin = xiaoyu.hear('试试', 'weixin', 'weixin-split-turn', report=lambda *a: None)
        main = xiaoyu.hear('试试', 'default', 'main-split-turn', report=lambda *a: None)
        self.assertEqual(weixin.reply, '好的。\n\n---\n\n我来安排。')
        self.assertEqual(main.reply, '好的。\n\n我来安排。')

    def test_completed_weixin_helper_result_splits_two_short_paragraphs(self):
        class RelayVoice(ToolTalker):
            def run(self, job):
                if job.tools is None:
                    self.jobs.append(job)
                    return Outcome('查到了\n\n这里是补充说明', 't1')
                return super().run(job)
        voice, helper = RelayVoice(), Scripted('codex', speaks=False)
        xiaoyu, _ = make_xiaoyu(self.folder, [voice, helper])
        self.addCleanup(xiaoyu.close)
        xiaoyu.intake.writer('owner', actor='owner').conversation_create_weixin()
        voice.calls = [('start_task', {'agent': 'codex', 'title': '查', 'task': '查一下'})]
        turn = xiaoyu.hear('查一下', 'weixin', 'weixin-relay-turn', report=lambda *a: None)
        card = xiaoyu.settle(turn.card, 5)
        self.assertEqual(card['entries'][-1]['text'], '查到了\n\n---\n\n这里是补充说明')

    def test_background_wake_keeps_rules_with_a_custom_character(self):
        voice = ToolTalker('voice')
        xiaoyu, _ = make_xiaoyu(self.folder, [voice], persona="A quiet owl.")
        self.addCleanup(xiaoyu.close)
        pulse = Pulse(xiaoyu)
        with patch.object(voice, 'run', return_value=Outcome('（不说）', None)) as run:
            pulse._ask('A task completed.', ['recall'], GATE_RULES)
        job = run.call_args[0][0]
        self.assertIn(RUNTIME_RULES, job.system)
        self.assertIn(GATE_RULES, job.system)
        self.assertLessEqual({t['name'] for t in job.tools.definitions()}, {'recall'})

    def test_weixin_style_is_applied_to_chat_and_helper_result_only(self):
        voice, helper = ToolTalker(), Scripted('codex', speaks=False)
        xiaoyu, _ = make_xiaoyu(self.folder, [voice, helper])
        self.addCleanup(xiaoyu.close)
        xiaoyu.memory.remember('测试共享偏好')
        xiaoyu.intake.writer('owner', actor='owner').conversation_create_weixin()
        xiaoyu.hear('你好', 'default', 'default-turn', report=lambda *a: None)
        self.assertNotIn(WEIXIN_CHAT_STYLE, voice.tool_jobs[-1].system)
        self.assertIn('测试共享偏好', voice.tool_jobs[-1].system)
        voice.calls = [('start_task', {'agent': 'codex', 'title': '查', 'task': '查一下'})]
        turn = xiaoyu.hear('查一下', 'weixin', 'weixin-turn', report=lambda *a: None)
        self.assertIn(WEIXIN_CHAT_STYLE, voice.tool_jobs[-1].system)
        xiaoyu.settle(turn.card, 5)
        self.assertIn(WEIXIN_CHAT_STYLE, voice.jobs[-1].system)
        self.assertIn('现在是 ', voice.jobs[-1].system)
        self.assertIn('测试共享偏好', voice.jobs[-1].system)

    def test_other_chat_is_available_on_actual_voice_turns(self):
        voice = ToolTalker()
        xiaoyu, _ = make_xiaoyu(self.folder, [voice])
        self.addCleanup(xiaoyu.close)
        writer = xiaoyu.intake.writer('owner', actor='owner')
        writer.conversation_create_weixin()
        main = writer.task_open('default', '主对话')
        writer.exchange(main['id'], '主对话里测试原话', '主对话测试回答', turn_id='main-source')
        voice.calls = [('read_recent_chat', {'channel': 'main'})]
        xiaoyu.hear('你能看到主对话的消息吗？', 'weixin', 'wx-test', report=lambda *a: None)
        job = voice.tool_jobs[-1]
        self.assertIn('read_recent_chat', [tool['name'] for tool in job.tools.definitions()])
        self.assertIn('主对话里测试原话', job.system)
        self.assertIn('主对话里测试原话', voice.results[-1][0])

    def test_shared_rules_allow_optional_splitting(self):
        prompt = lane_prompt('P', 80, [], [], None, 0, tools=True)
        self.assertIn('简单回答一条说完整就好', prompt)
        self.assertNotIn('必须在这里写出分隔行', prompt)
        self.assertNotIn('答案后还有独立的原因或补充时，必须', prompt)
        self.assertIn('空行只是在同一条里换段', prompt)

    def test_main_chat_helper_result_uses_same_current_context(self):
        voice, helper = ToolTalker(), Scripted('codex', speaks=False)
        xiaoyu, _ = make_xiaoyu(self.folder, [voice, helper])
        self.addCleanup(xiaoyu.close)
        xiaoyu.memory.remember('测试共享偏好')
        voice.calls = [('start_task', {'agent': 'codex', 'title': '查', 'task': '查一下'})]
        turn = xiaoyu.hear('查一下', 'default', 'main-turn', report=lambda *a: None)
        xiaoyu.settle(turn.card, 5)
        self.assertNotIn(WEIXIN_CHAT_STYLE, voice.jobs[-1].system)
        self.assertIn('现在是 ', voice.jobs[-1].system)
        self.assertIn('测试共享偏好', voice.jobs[-1].system)
