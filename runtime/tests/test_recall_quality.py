import unittest

from xiaoyu_runtime.loop import recall


class RecallQualityTests(unittest.TestCase):
    def setUp(self):
        self.records = [("聊天", "", 1, "主人：昨天讨论了小鱼头像；小鱼：选暖橘色"),
                        ("聊天", "", 2, "主人：今天讨论了电池；小鱼：好的")]

    def test_full_chinese_question_finds_a_candidate(self):
        hits = recall.search("上次头像是什么颜色", self.records)
        self.assertEqual(hits[0], self.records[0])
        self.assertIn("头像", recall.render("上次头像是什么颜色", hits))

    def test_generic_question_does_not_match_every_conversation(self):
        self.assertEqual(recall.search("上次我们聊了什么", self.records), [])

    def test_no_semantic_guess_and_scope_is_explicit(self):
        self.assertEqual(recall.search("照片", self.records), [])
        self.assertIn("不代表其他范围没有", recall.render("照片", []))

    def test_missing_ascii_identifier_is_not_partially_matched(self):
        records = [("聊天", "", 1, "unrelated-only-anchor")]
        self.assertEqual(recall.search("private-only-anchor", records), [])

    def test_full_question_snippet_centers_on_partial_match(self):
        body = "甲" * 300 + "小鱼头像选暖橘色" + "乙" * 300
        self.assertIn("头像", recall.snippet(body, "上次头像是什么颜色"))

    def test_excerpt_truncation_respects_custom_limit(self):
        self.assertTrue(recall.snippet("abcdefghijkl", "abc", max_chars=3).endswith("…"))


if __name__ == "__main__":
    unittest.main()
