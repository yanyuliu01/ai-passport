"""R4.1：/v1/sync 的快照只给“现在”，增量按条数和大小分页，落后太多改给快照（问题清单 N10）。"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_runtime import Scripted, TempDirCase, make_xiaoyu  # noqa: E402
import xiaoyu_runtime.api.service as service_module  # noqa: E402
from xiaoyu_runtime.api.service import Service  # noqa: E402


class SyncWindowTests(TempDirCase):
    def setUp(self):
        super().setUp()
        agent = Scripted("claude")
        self.xiaoyu, store = make_xiaoyu(self.folder, [agent])
        self.service = Service(self.xiaoyu, store)
        self.addCleanup(self.service.close)
        self.cards = self.xiaoyu.cards
        self.cards.open("default", "先有一件事", "done")  # 游标从大于 0 开始才是增量

    def chat(self, count, conversation="default", words="说了一句"):
        """开几件事，每件说几句：日志里攒下 count 句小鱼说的话。"""
        card = None
        for index in range(count):
            if index % 20 == 0:
                card = self.cards.open(conversation, "第 %d 件" % index, "done", "codex")
            self.cards.update(card["id"], say="%s %d" % (words, index))
        return card

    @staticmethod
    def ids(page, kind="messages"):
        return [item["id"] for item in page[kind]]

    def test_a_fresh_sync_is_one_page_of_the_present(self):
        self.chat(600)
        finished = self.cards.open("default", "刚做完的事", "working", "codex")
        self.cards.update(finished["id"], say="结果在这里", state="done")
        page = self.service.sync("default", 0)
        # N10：以前从 0 一页页追整段历史（真机上 400 多页），结果排在最后才到。
        self.assertFalse(page["more"])
        self.assertEqual(page["seq"], self.cards.seq)
        self.assertEqual(page["snapshot"], {"seq": page["seq"], "truncated": True})
        texts = [item.get("text") for item in page["messages"]]
        self.assertIn("结果在这里", texts)
        self.assertIn(finished["id"], self.ids(page, "tasks"))
        self.assertLessEqual(sum(1 for item in page["messages"] if item.get("kind") != "activity"),
                             service_module.SNAPSHOT_MESSAGES)
        # 每句话所在的事都在：客户端认得出它属于哪件事。
        tasks = set(self.ids(page, "tasks"))
        self.assertTrue(all(item["task"] in tasks for item in page["messages"] if item.get("task")))
        # 之后拿快照的 seq 接着要：只有新的。
        self.cards.update(finished["id"], say="又补了一句")
        after = self.service.sync("default", page["seq"])
        texts = [item.get("text") for item in after["messages"]]
        self.assertIn("又补了一句", texts)
        self.assertLessEqual(len(texts), 2)  # 只有这件事上刚变的
        self.assertFalse(after["reset"])
        self.assertNotIn("snapshot", after)

    def test_busy_tasks_are_in_the_snapshot_even_past_the_budget(self):
        busy = self.cards.open("default", "还在做", "working", "codex")
        self.chat(80, words="很长的话" * 40)
        with mock.patch.object(service_module, "SNAPSHOT_BUDGET_BYTES", 2048):
            page = self.service.sync("default", 0)
        self.assertIn(busy["id"], self.ids(page, "tasks"))
        self.assertTrue(page["snapshot"]["truncated"])

    def test_deltas_are_paged_by_count_and_continue_where_they_stopped(self):
        start = self.cards.seq
        self.chat(250)
        seen, pages, after = [], 0, start
        while True:
            page = self.service.sync("default", after)
            pages += 1
            self.assertLessEqual(len(page["messages"]) + len(page["tasks"]), service_module.SYNC_PAGE_ITEMS)
            self.assertFalse(page["reset"])
            seen += self.ids(page)
            after = page["seq"]
            if not page["more"]:
                break
        self.assertGreater(pages, 1)
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), 250)

    def test_deltas_are_paged_by_size_too(self):
        start = self.cards.seq
        self.chat(30, words="很长的话" * 600)
        page = self.service.sync("default", start)
        self.assertTrue(page["more"])
        self.assertLess(len(page["messages"]), 30)

    def test_a_client_far_behind_gets_a_snapshot_instead_of_hundreds_of_pages(self):
        start = self.cards.seq
        with mock.patch.object(service_module, "SYNC_DELTA_WINDOW", 50):
            self.chat(40)
            near = self.service.sync("default", start)  # 还在窗口里：补增量
            self.assertFalse(near["reset"])
            self.assertNotIn("snapshot", near)
            self.chat(80)
            far = self.service.sync("default", near["seq"])
        self.assertTrue(far["reset"])
        self.assertFalse(far["more"])
        self.assertEqual(far["snapshot"]["seq"], self.cards.seq)


if __name__ == "__main__":
    unittest.main()
