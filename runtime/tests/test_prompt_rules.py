"""Replacing a character must preserve operating rules at every model entry."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import TempDirCase, ToolTalker, make_xiaoyou
from xiaoyou_runtime.loop.xiaoyou import lane_prompt, voice_prompt, task_prompt
from xiaoyou_runtime.loop.prompt_rules import RUNTIME_RULES
from xiaoyou_runtime.loop.proactive import Pulse, GATE_RULES
from xiaoyou_runtime.helpers.agents import Outcome


class PromptRulesTests(unittest.TestCase):
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
    def test_background_wake_keeps_rules_with_a_custom_character(self):
        voice = ToolTalker('voice')
        xiaoyou, _ = make_xiaoyou(self.folder, [voice], persona="A quiet owl.")
        self.addCleanup(xiaoyou.close)
        pulse = Pulse(xiaoyou)
        with patch.object(voice, 'run', return_value=Outcome('（不说）', None)) as run:
            pulse._ask('A task completed.', ['recall'], GATE_RULES)
        job = run.call_args[0][0]
        self.assertIn(RUNTIME_RULES, job.system)
        self.assertIn(GATE_RULES, job.system)
        self.assertLessEqual({t['name'] for t in job.tools.definitions()}, {'recall'})
