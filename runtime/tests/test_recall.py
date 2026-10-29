"""Recall keeps relevance when recent incidental hits fill the result window."""

import sqlite3
import unittest
from unittest import mock

import test_runtime  # Shared direct-script package-path bootstrap.

from xiaoyu_runtime.loop import recall


class RecallTests(unittest.TestCase):
    def records(self):
        return [("chat", "original", 1, "needle " * 30)] + [
            ("chat", "recent-%d" % i, i + 2, "needle " + "noise " * 30)
            for i in range(12)]

    def check_best_match(self, query):
        hits = recall.search(query, self.records())
        self.assertEqual(recall.MAX_HITS, len(hits))
        self.assertIn("original", [record[1] for record in hits])
        self.assertEqual(sorted([r[2] for r in hits], reverse=True), [r[2] for r in hits])

    def test_short_query_keeps_the_strong_old_match_before_ordering_for_display(self):
        self.check_best_match("ne")

    def test_unavailable_full_text_search_keeps_the_strong_old_match(self):
        with mock.patch.object(recall, "_fts", side_effect=sqlite3.Error("unavailable")):
            self.check_best_match("needle")

    def test_full_text_search_keeps_the_strong_old_match(self):
        self.check_best_match("needle")

    def test_smaller_retrieval_budget_keeps_relevance_before_display_order(self):
        hits = recall.search("needle", self.records(), limit=2)
        self.assertEqual(2, len(hits))
        self.assertIn("original", [record[1] for record in hits])

    def test_equal_relevance_prefers_recent_timestamps_in_either_backend(self):
        records = [("chat", "turn-%d" % i, i + 1, "same needle text") for i in range(24)]
        expected = ["turn-%d" % i for i in range(23, 17, -1)]
        self.assertEqual(expected, [r[1] for r in recall.search("needle", records)])
        with mock.patch.object(recall, "_fts", side_effect=sqlite3.Error("unavailable")):
            self.assertEqual(expected, [r[1] for r in recall.search("needle", records)])

    def test_case_insensitive_match_is_visible_in_the_rendered_excerpt(self):
        body = "unrelated preface " * 30 + "NEEDLE is the relevant evidence" + " tail" * 50
        hits = recall.search("needle", [("chat", "old", 1, body)])
        self.assertEqual(1, len(hits))
        self.assertIn("NEEDLE", recall.render("needle", hits))

    def test_empty_query_and_nonmatches_do_not_invent_evidence(self):
        for query in ("", " ", "absent"):
            self.assertEqual([], recall.search(query, self.records()))


if __name__ == "__main__":
    unittest.main()
