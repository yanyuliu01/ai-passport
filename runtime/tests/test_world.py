"""World / 任务日志：快照（cards.json）+ 之后的日志（cards.tasks.jsonl）重放出来的，必须和当时的卡一模一样。

都是确定性测试：随机操作用固定种子生成，失败时打印种子，用同一个种子就能复现；
崩溃用“磁盘停在某次写的中间”模拟：记下每次追加、每次整份替换之后两个文件的样子，
追加还截在那一行中间，不依赖真线程和等待（见重构设计 8.3 第 2 条）。
"""

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from xiaoyou_runtime import world  # noqa: E402
from xiaoyou_runtime.cards import MAX_CARDS, RESTART_NOTE, Cards  # noqa: E402

SEEDS = range(12)
STATES = ("working", "waiting", "done", "failed", "cancelled")


def live(path):
    """快照 + 日志重放出来的卡（以 id 为键）。"""
    return {card["id"]: card for card in world.current(path)}


def memory(cards):
    """Runtime 内存里的卡，规整成落盘的样子。"""
    return world._plain(cards._cards)


class Scenario:
    """用种子生成一串对卡的操作；每做完一步记下当时的卡。"""

    def __init__(self, folder, seed):
        self.random = random.Random(seed)
        self.path = Path(folder) / "cards.json"
        self.journal = world.journal_of(self.path)
        self.cards = Cards(self.path)
        self.ids = []
        self.states = [memory(self.cards)]  # 第 i 步之后的卡

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
        self.states.append(memory(self.cards))

    def run(self, steps):
        for _ in range(steps):
            self.step()


class Disk:
    """记下每次写之后磁盘上的样子：(第几步, cards.json 的字节, 日志的字节)。"""

    def __init__(self, scenario, rng):
        self.scenario, self.rng, self.seen = scenario, rng, []
        append, replace = world.Journal._append, world.durable_write
        disk = self

        def appending(journal, entry):
            before = disk.read(scenario.journal)
            append(journal, entry)
            after = disk.read(scenario.journal)
            for cut in sorted({disk.rng.randrange(len(before), len(after)) for _ in range(3)}):
                disk.note(journal=after[:cut])  # 这一行写了一半就断电
            disk.note()

        def replacing(path, data):
            disk.note(**{"leftover": path})  # 临时文件写了一半：原来的文件还在
            replace(path, data)
            disk.note()

        self.patches = [mock.patch.object(world.Journal, "_append", appending),
                        mock.patch.object(world, "durable_write", replacing)]

    @staticmethod
    def read(path):
        return path.read_bytes() if path.exists() else b""

    def note(self, journal=None, leftover=None):
        self.seen.append((len(self.scenario.states) - 1, self.read(self.scenario.path),
                          self.read(self.scenario.journal) if journal is None else journal))

    def __enter__(self):
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self.patches:
            patch.stop()


class WorldTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        # 快照攒得少一点，几十步里就会写好几次快照、压缩好几次日志。
        patch = mock.patch.object(world, "SNAPSHOT_EVERY", 5)
        patch.start()
        self.addCleanup(patch.stop)

    def test_snapshot_plus_journal_matches_the_cards_after_every_step(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                scenario = Scenario(self.folder / ("s%d" % seed), seed)
                for _ in range(120):
                    scenario.step()
                    self.assertEqual(live(scenario.path), scenario.states[-1], "seed %d" % seed)

    def test_a_crash_at_any_write_recovers_a_state_that_really_existed(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                scenario = Scenario(self.folder / ("c%d" % seed), seed)
                with Disk(scenario, random.Random(1000 + seed)) as disk:
                    scenario.run(60)
                crashed = self.folder / ("crashed%d" % seed) / "cards.json"
                crashed.parent.mkdir()
                for step, snapshot, journal in disk.seen:
                    crashed.write_bytes(snapshot)
                    world.journal_of(crashed).write_bytes(journal)
                    # 第 step 步做完、下一步写到一半断电：只能是这一步之后或下一步之后的样子。
                    self.assertIn(live(crashed), scenario.states[step:step + 2],
                                  "seed %d step %d" % (seed, step))
                # 下一步的最后一次写完成了，就是下一步之后的卡。
                finals = {}
                for step, snapshot, journal in disk.seen:
                    finals[step] = (snapshot, journal)
                for step, (snapshot, journal) in finals.items():
                    crashed.write_bytes(snapshot)
                    world.journal_of(crashed).write_bytes(journal)
                    self.assertEqual(live(crashed), scenario.states[step + 1])

    def test_the_snapshot_is_rewritten_only_every_few_changes(self):
        scenario = Scenario(self.folder, 0)
        card = scenario.cards.open("default", "长对话", "working", "codex")
        snapshot = scenario.path.read_bytes()
        for index in range(world.SNAPSHOT_EVERY - 2):
            scenario.cards.update(card["id"], say="第 %d 句" % index)
        self.assertEqual(scenario.path.read_bytes(), snapshot)  # 只追加了日志
        self.assertEqual(len(world.read(scenario.journal)), world.SNAPSHOT_EVERY - 1)
        scenario.cards.update(card["id"], say="攒够了")
        saved = json.loads(scenario.path.read_text(encoding="utf-8"))
        self.assertEqual(world.read(scenario.journal), [])  # 快照落稳后日志压缩掉
        self.assertEqual(saved["journal"], scenario.cards._journal.n)
        self.assertEqual(live(scenario.path), memory(scenario.cards))

    def test_long_cards_record_only_what_changed(self):
        scenario = Scenario(self.folder, 0)
        card = scenario.cards.open("default", "长对话", "working", "codex")
        records = []
        with mock.patch.object(world.Journal, "compact", lambda journal, applied: None):
            for index in range(60):  # 超过 MAX_ENTRIES：前面的条目会被挤掉
                scenario.cards.update(card["id"], say="第 %d 句" % index)
            records = world.read(scenario.journal)
        deltas = [record for record in records if record["id"] == card["id"] and "card" not in record]
        self.assertTrue(deltas)
        self.assertTrue(all("entries" not in record for record in deltas))  # 只记追加和挤掉几条
        self.assertTrue(any(record.get("drop") for record in deltas))
        self.assertEqual(live(scenario.path), memory(scenario.cards))

    def test_removed_cards_leave_the_rebuilt_state(self):
        scenario = Scenario(self.folder, 0)
        removed = False
        for index in range(MAX_CARDS + 10):
            card = scenario.cards.open("default", "旧事 %d" % index, "working", "codex")
            scenario.cards.update(card["id"], state="done")
            removed = removed or any(record.get("removed") for record in world.read(scenario.journal))
            self.assertEqual(live(scenario.path), memory(scenario.cards))
        self.assertTrue(removed)
        self.assertEqual(len(live(scenario.path)), MAX_CARDS)

    def test_restart_replays_the_journal_and_marks_unfinished_work(self):
        scenario = Scenario(self.folder, 3)
        scenario.run(40)
        working = scenario.cards.open("default", "还在做", "working", "codex")
        self.assertTrue(world.read(scenario.journal))  # 快照之后还有没写进快照的变化
        restarted = Cards(scenario.path)
        rebuilt = live(scenario.path)
        self.assertEqual(rebuilt, memory(restarted))
        self.assertEqual(rebuilt[working["id"]]["state"], "failed")
        self.assertEqual(rebuilt[working["id"]]["brief"], RESTART_NOTE)
        self.assertEqual(world.read(scenario.journal), [])  # 启动时写了快照、压缩了日志
        restarted.update(working["id"], brief="又改了一次")
        self.assertEqual(live(scenario.path), memory(restarted))
        # 编号接着往上走：重启前开过的卡号不会再发一次。
        self.assertNotIn(restarted.open("default", "新的")["id"], rebuilt)

    def test_leftover_temporary_files_are_ignored(self):
        scenario = Scenario(self.folder, 4)
        scenario.run(23)
        expected = memory(scenario.cards)
        Path(str(scenario.path) + ".tmp").write_text('{"cards": [', encoding="utf-8")
        Path(str(scenario.journal) + ".tmp").write_text('{"n": 1', encoding="utf-8")
        self.assertEqual(live(scenario.path), expected)
        restarted = Cards(scenario.path)  # 能起来；还在做的照常标成没做完
        self.assertEqual(memory(restarted), live(scenario.path))
        self.assertEqual(set(memory(restarted)), set(expected))

    def test_r31_files_load_with_cards_json_as_the_authority(self):
        scenario = Scenario(self.folder, 6)
        scenario.run(20)
        expected = memory(scenario.cards)
        # R3.1 写的：快照没有 "journal"，日志记录没有 "n"，而且可能比快照多一条。
        raw = json.loads(scenario.path.read_text(encoding="utf-8"))
        raw.pop("journal")
        raw["cards"] = list(expected.values())
        scenario.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        legacy = [{key: value for key, value in record.items() if key != "n"}
                  for record in world.read(scenario.journal)]
        legacy.append({"seq": 1, "id": next(iter(expected)), "set": {"brief": "多出来的一条"}})
        scenario.journal.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in legacy),
                                    encoding="utf-8")
        self.assertEqual(live(scenario.path), expected)
        upgraded = Cards(scenario.path)
        self.assertEqual(memory(upgraded), live(scenario.path))
        self.assertEqual(set(memory(upgraded)), set(expected))
        self.assertNotIn("多出来的一条", json.dumps(memory(upgraded), ensure_ascii=False))
        saved = json.loads(scenario.path.read_text(encoding="utf-8"))
        self.assertIsInstance(saved["journal"], int)
        self.assertEqual(world.read(scenario.journal), [])

    def test_appending_after_a_torn_tail_preserves_the_next_commit(self):
        path = self.folder / "tail.jsonl"
        first = {"id": "c1", "brief": "before"}
        for tail in (b'{"n":2,"card":', b'{"text":"\xe4\xb8'):
            with self.subTest(tail=tail):
                path.write_bytes(b'')
                journal = world.Journal(path, {}, 0, {"c1": first}, 1)
                with path.open("ab") as stream:
                    stream.write(tail)
                records = world.read(path)
                base = world.rebuild(records)
                restarted = world.Journal(path, base, records[-1]["n"], base, 1)
                restarted.record({"id": "c1", "brief": "after"}, 2)
                self.assertEqual(world.rebuild(world.read(path))["c1"]["brief"], "after")
                self.assertEqual([row["n"] for row in world.read(path)], [1, 2])

    def test_a_complete_tail_without_newline_is_not_lost_on_append(self):
        path = self.folder / "tail.jsonl"
        first = {"id": "c1", "brief": "before"}
        world.Journal(path, {}, 0, {"c1": first}, 1)
        path.write_bytes(path.read_bytes().rstrip(b'\r\n'))
        base = world.rebuild(world.read(path))
        restarted = world.Journal(path, base, 1, base, 1)
        restarted.record({"id": "c2", "brief": "after"}, 2)
        self.assertEqual(set(world.rebuild(world.read(path))), {"c1", "c2"})

    def test_a_failed_partial_append_can_be_retried_without_losing_data(self):
        path = self.folder / 'retry.jsonl'
        journal = world.Journal(path, {}, 0, {'c1': {'id': 'c1'}}, 1)
        before = path.read_bytes()
        write = world.os.write
        calls = 0

        def disk_full(fd, data):
            nonlocal calls
            calls += 1
            if calls == 1:
                return write(fd, data[:8])
            raise OSError('disk full')

        with mock.patch.object(world.os, 'write', side_effect=disk_full):
            with self.assertRaises(OSError):
                journal.record({'id': 'c2'}, 2)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(journal.n, 1)
        journal.record({'id': 'c2'}, 2)
        self.assertEqual(set(world.rebuild(world.read(path))), {'c1', 'c2'})

    def test_missing_checkpoint_cannot_silently_replay_only_a_suffix(self):
        state, last, highest = world.replay({}, [{'n': 65, 'id': 'c9', 'card': {'id': 'c9'}}])
        self.assertEqual(state, {})
        self.assertEqual((last, highest), (65, 9))

    def test_readers_retry_when_compaction_changes_the_snapshot(self):
        path = self.folder / "cards.json"
        journal = world.journal_of(path)
        world.durable_write(path, {"journal": 1, "cards": [{"id": "c1", "brief": "before"}]})
        journal.write_text('', encoding='utf-8')
        original = world.read
        switched = False

        def compact_during_read(target):
            nonlocal switched
            if not switched:
                switched = True
                world.durable_write(path, {"journal": 2, "cards": [{"id": "c1", "brief": "after"}]})
                world.durable_write(journal, '')
            return original(target)

        with mock.patch.object(world, 'read', side_effect=compact_during_read):
            self.assertEqual(world.current(path)[0]['brief'], 'after')

    def test_unknown_or_broken_records_are_ignored(self):
        state = world.rebuild([{"id": "c9", "set": {"state": "done"}}, {"no": "id"}, {"id": "c1", "removed": True}])
        self.assertEqual(state, {})
        journal = self.folder / "broken.jsonl"
        journal.write_text('{"seq": 1, "id": "c1", "card": {"id": "c1", "entries": []}}\nnot json\n{"seq": 2', encoding="utf-8")
        self.assertEqual(world.rebuild(world.read(journal)), {"c1": {"id": "c1", "entries": []}})


if __name__ == "__main__":
    unittest.main()
