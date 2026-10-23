"""World / 任务日志：从 cards.tasks.jsonl 重建出来的，必须和当时的卡一模一样。

都是确定性测试：随机操作用固定种子生成，失败时打印种子，用同一个种子就能复现；
崩溃用“把日志截断在任意位置”模拟，不依赖真线程和等待（见重构设计 8.3 第 2 条）。
"""

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from xiaoyou_runtime import world  # noqa: E402
from xiaoyou_runtime.cards import MAX_CARDS, RESTART_NOTE, Cards  # noqa: E402

SEEDS = range(12)
STATES = ("working", "waiting", "done", "failed", "cancelled")


def on_disk(path):
    """cards.json 里现在的卡（以 id 为键）。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {card["id"]: card for card in raw["cards"]}


class Scenario:
    """用种子生成一串对卡的操作；每做完一步记下当时的卡和日志长度。"""

    def __init__(self, folder, seed):
        self.random = random.Random(seed)
        self.path = Path(folder) / "cards.json"
        self.journal = self.path.with_name("cards" + world.JOURNAL_SUFFIX)
        self.cards = Cards(self.path)
        self.ids = []
        self.checkpoints = []  # [(日志字节数, 这一步之后的卡)]

    def step(self):
        roll = self.random.random()
        if not self.ids or roll < 0.15:
            card = self.cards.open("default", "事情 %d" % len(self.ids), "working", "codex")
            self.ids.append(card["id"])
        else:
            card_id = self.random.choice(self.ids)
            if self.cards.get(card_id) is None:
                return self.step()
            kind = self.random.choice(("say", "said", "progress", "state", "brief"))
            if kind == "say":
                self.cards.update(card_id, say="回复 %d" % self.random.randrange(1000))
            elif kind == "said":
                self.cards.update(card_id, said="主人说 %d" % self.random.randrange(1000),
                                  say="好", turn_id="t%d" % self.random.randrange(10 ** 6))
            elif kind == "progress":
                self.cards.update(card_id, progress="第 %d 步" % self.random.randrange(100))
            elif kind == "state":
                self.cards.update(card_id, state=self.random.choice(STATES))
            else:
                self.cards.update(card_id, brief="简报 %d" % self.random.randrange(100), mood="happy")
        self.checkpoints.append((self.journal.stat().st_size, on_disk(self.path)))

    def run(self, steps):
        for _ in range(steps):
            self.step()


class WorldTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def test_rebuilding_from_the_journal_matches_the_cards_after_every_step(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                scenario = Scenario(self.folder / ("s%d" % seed), seed)
                scenario.run(120)
                self.assertEqual(world.rebuild(world.read(scenario.journal)), on_disk(scenario.path),
                                 "seed %d" % seed)

    def test_long_cards_record_only_what_changed(self):
        scenario = Scenario(self.folder, 0)
        card = scenario.cards.open("default", "长对话", "working", "codex")
        for index in range(60):  # 超过 MAX_ENTRIES：前面的条目会被挤掉
            scenario.cards.update(card["id"], say="第 %d 句" % index)
        records = world.read(scenario.journal)
        self.assertEqual(world.rebuild(records), on_disk(scenario.path))
        deltas = [record for record in records if record["id"] == card["id"] and "card" not in record]
        self.assertTrue(deltas)
        self.assertTrue(all("entries" not in record for record in deltas))  # 只记追加和挤掉几条
        self.assertTrue(any(record.get("drop") for record in deltas))

    def test_a_crash_at_any_point_rebuilds_a_state_that_really_existed(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                scenario = Scenario(self.folder / ("c%d" % seed), seed)
                scenario.run(60)
                data = scenario.journal.read_bytes()
                rng = random.Random(1000 + seed)
                for _ in range(25):
                    index = rng.randrange(1, len(scenario.checkpoints))
                    start, before = scenario.checkpoints[index - 1]
                    end, after = scenario.checkpoints[index]
                    # 截在这一步写的字节中间：最后一行写了一半，必须当它没写过。
                    cut = rng.randrange(start, end) if end > start else start
                    truncated = self.folder / "truncated.jsonl"
                    truncated.write_bytes(data[:cut])
                    rebuilt = world.rebuild(world.read(truncated))
                    if cut == start:
                        self.assertEqual(rebuilt, before, "seed %d cut %d" % (seed, cut))
                    else:
                        self.assertIn(rebuilt, (before, after), "seed %d cut %d" % (seed, cut))
                    # 截在一步刚写完的地方：就是那一步之后的样子。
                    truncated.write_bytes(data[:end])
                    self.assertEqual(world.rebuild(world.read(truncated)), after)

    def test_removed_cards_leave_the_rebuilt_state(self):
        scenario = Scenario(self.folder, 0)
        for index in range(MAX_CARDS + 10):
            card = scenario.cards.open("default", "旧事 %d" % index, "working", "codex")
            scenario.cards.update(card["id"], state="done")
        records = world.read(scenario.journal)
        self.assertTrue(any(record.get("removed") for record in records))
        self.assertEqual(world.rebuild(records), on_disk(scenario.path))

    def test_restart_reconciles_the_journal_with_the_cards(self):
        scenario = Scenario(self.folder, 3)
        scenario.run(40)
        working = scenario.cards.open("default", "还在做", "working", "codex")
        # 重启：还在做的事被标成没做完；日志补上这些变化后，重建出来的仍和 cards.json 一样。
        restarted = Cards(scenario.path)
        rebuilt = world.rebuild(world.read(scenario.journal))
        self.assertEqual(rebuilt, on_disk(scenario.path))
        self.assertEqual(rebuilt[working["id"]]["state"], "failed")
        self.assertEqual(rebuilt[working["id"]]["brief"], RESTART_NOTE)
        self.assertEqual(rebuilt, {card_id: card for card_id, card in on_disk(scenario.path).items()})
        restarted.update(working["id"], brief="又改了一次")
        self.assertEqual(world.rebuild(world.read(scenario.journal)), on_disk(scenario.path))

    def test_a_lost_tail_is_repaired_on_the_next_start(self):
        scenario = Scenario(self.folder, 5)
        scenario.run(30)
        # 模拟日志最后几行丢了（比如磁盘坏块）：下次启动时以 cards.json 为准补齐。
        lines = scenario.journal.read_text(encoding="utf-8").splitlines(keepends=True)
        scenario.journal.write_text("".join(lines[:-3]), encoding="utf-8")
        Cards(scenario.path)
        self.assertEqual(world.rebuild(world.read(scenario.journal)), on_disk(scenario.path))

    def test_unknown_or_broken_records_are_ignored(self):
        state = world.rebuild([{"id": "c9", "set": {"state": "done"}}, {"no": "id"}, {"id": "c1", "removed": True}])
        self.assertEqual(state, {})
        journal = self.folder / "broken.jsonl"
        journal.write_text('{"seq": 1, "id": "c1", "card": {"id": "c1", "entries": []}}\nnot json\n{"seq": 2', encoding="utf-8")
        self.assertEqual(world.rebuild(world.read(journal)), {"c1": {"id": "c1", "entries": []}})


if __name__ == "__main__":
    unittest.main()
