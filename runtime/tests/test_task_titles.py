#!/usr/bin/env python3
"""Canonical title policy and storage, with synthetic names only."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xiaoyu_runtime.world.cards import Cards
from xiaoyu_runtime.world.task_titles import MAX_TITLE_CHARS, canonical_title, select_title


class TaskTitleTests(unittest.TestCase):
    def test_same_precedence_for_every_agent(self):
        with tempfile.TemporaryDirectory() as temp:
            cards = Cards(Path(temp) / "cards.json")
            for agent in ("codex", "claude", "cc", "dsh", "remote-helper", None):
                title = select_title(upstream="修复同步任务标题", task="local name",
                                     request="the latest follow-up", fallback="session-id")
                card = cards.open("test", "request", agent=agent)
                cards.update(card["id"], **title.fields())
                actual = cards.get(card["id"])
                self.assertEqual(actual["title"], "修复同步任务标题")
                self.assertEqual(actual["title_source"], "upstream")

    def test_fallbacks_are_explicit_and_do_not_use_replies(self):
        self.assertEqual(select_title(task="Local task", request="request").source, "task")
        request = select_title(upstream="  ", task=None, request="same request " * 4)
        self.assertEqual(request.source, "request")
        self.assertEqual(len(request.title), 24)
        self.assertTrue(request.truncated)
        fallback = select_title(upstream=[], task={}, request="", fallback="agent project id")
        self.assertEqual((fallback.title, fallback.source), ("agent project id", "fallback"))
        self.assertEqual(select_title(fallback="").title, "（没有标题）")

    def test_upstream_spelling_spaces_and_emoji_are_retained(self):
        original = "修复  同步 • ABC_ID 👩‍💻"
        title = select_title(upstream=original)
        self.assertEqual(title.title, original)
        self.assertFalse(title.truncated)
        self.assertEqual(canonical_title("a\nb\t标题", "task").title, "a b 标题")

    def test_explicit_names_have_a_separate_bound(self):
        title = select_title(upstream="名" * (MAX_TITLE_CHARS + 20))
        self.assertEqual(len(title.title), MAX_TITLE_CHARS)
        self.assertTrue(title.title.endswith("…"))
        self.assertTrue(title.truncated)
        self.assertEqual(title.source, "upstream")
        self.assertEqual(select_title(upstream="x" * 17000, task="safe fallback").source, "task")

    def test_credential_scrubbing_applies_to_all_sources(self):
        for source in ("upstream", "task", "request", "fallback"):
            title = canonical_title("Task password=EXAMPLEPRIVATE", source)
            self.assertNotIn("EXAMPLEPRIVATE", title.title)
            self.assertIn("[REDACTED]", title.title)

    def test_injected_context_is_not_a_request_title(self):
        text = "<environment_context>private metadata</environment_context>\n## My request:\n标题同步"
        self.assertEqual(select_title(request=text).title, "标题同步")

    def test_card_title_metadata_survives_reload(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cards.json"
            cards = Cards(path)
            local = cards.open("test", "named task " * 5, agent="arbitrary", title_source="task")
            self.assertGreater(len(local["title"]), 24)
            observed = cards.open("test", "request", agent="claude")
            title = select_title(upstream="Title " * 50)
            cards.update(observed["id"], **title.fields(), monitoring_only=True)
            restored = Cards(path)
            self.assertEqual(restored.get(local["id"])["title_source"], "task")
            self.assertEqual(restored.get(observed["id"])["title"], title.title)
            self.assertTrue(restored.get(observed["id"])["title_truncated"])
            self.assertEqual(restored.get(observed["id"])["title_source"], "upstream")

    def test_old_cards_and_bad_metadata_remain_compatible(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cards.json"
            path.write_text(json.dumps({"cards": [{"id": "c1", "title": "Old task"},
                                                   {"id": "c2", "title": "Other task",
                                                    "title_source": "unexpected"}]}))
            cards = Cards(path)
            self.assertEqual(cards.get("c1")["title"], "Old task")
            self.assertEqual(cards.get("c2")["title_source"], "task")


if __name__ == "__main__":
    unittest.main()
