"""R3.3：Service 的消息走“快照 + 日志”，每句话是一个有身份、有状态的操作（重构设计 1.5 节）。

确定性：随机操作用固定种子；崩溃用“磁盘停在某次写的中间”模拟（同 test_world.py）。
"""

import json
import random
import sys
import threading
import unittest
from collections import OrderedDict
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_runtime import Scripted, TempDirCase, make_xiaoyu, until  # noqa: E402
from xiaoyu_runtime.events import ledger as ledger_module, operations
from xiaoyu_runtime.world import world  # noqa: E402
from xiaoyu_runtime.events.ledger import Ledger, dedupe_key  # noqa: E402
from xiaoyu_runtime.api.service import RESTART_NOTE, Service  # noqa: E402

SEEDS = range(10)
STATUSES = ("queued", "running", "done", "failed")


class TransitionTests(unittest.TestCase):
    def test_finished_operations_never_run_again(self):
        for state in operations.TERMINAL:
            for target in operations.STATES:
                self.assertEqual(operations.allowed(state, target), target == state, (state, target))
        self.assertTrue(operations.allowed("pending", "running"))
        self.assertTrue(operations.allowed("running", "unknown"))
        self.assertFalse(operations.allowed("pending", "unknown"))  # 没开始过就谈不上不知道
        self.assertFalse(operations.allowed("unknown", "running"))  # 核对之前不能重做

    def test_a_late_update_is_dropped_whole(self):
        message = {"id": "m1", "status": "done", "operation_state": "succeeded"}
        self.assertIsNone(operations.transition(message, {"status": "running", "text": "x"}))
        self.assertEqual(operations.transition(message, {"stage": None}), {"stage": None})

    def test_old_records_get_an_operation_from_their_status(self):
        for status, state in (("queued", "pending"), ("running", "running"), ("done", "succeeded"),
                              ("failed", "failed"), ("odd", "unknown")):
            message = {"id": "m1", "status": status}
            operations.migrate(message)
            self.assertEqual((message["operation_id"], message["operation_state"]), ("m1:handle", state))


def state_of(messages, dedupe):
    return world._plain({"messages": list(messages.values()), "dedupe": list(dedupe.values())})


def on_disk(path):
    current = ledger_module.current(path)
    return {"messages": current["messages"], "dedupe": current["dedupe"]}


class LedgerJournalTests(TempDirCase):
    def test_first_snapshot_failure_does_not_lose_an_accepted_message(self):
        path = self.folder / 'messages.json'
        ledger = Ledger(path)
        messages = OrderedDict(m1={'id': 'm1', 'conversation': 'default', 'status': 'queued', 'events': []})
        dedupe = OrderedDict({dedupe_key('default', 'retry'): {
            'id': 'm1', 'conversation': 'default', 'client_id': 'retry', 'hash': 'same'}})
        with mock.patch.object(ledger, 'snapshot', side_effect=OSError('disk unavailable')):
            ledger.save(messages, dedupe, 1)
        restored, identities, _ = Ledger(path).load()
        self.assertIn('m1', restored)
        self.assertIn(dedupe_key('default', 'retry'), identities)

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(world, "SNAPSHOT_EVERY", 7)
        patch.start()
        self.addCleanup(patch.stop)
        self.path = self.folder / "messages.json"

    def scenario(self, seed, steps, ledger):
        """用种子改消息和去重表，每步 save 一次；返回每步之后的状态。"""
        rng = random.Random(seed)
        messages, dedupe = OrderedDict(), OrderedDict()
        states = [state_of(messages, dedupe)]
        for step in range(steps):
            roll = rng.random()
            if not messages or roll < 0.3:
                message_id = "m%d" % step
                messages[message_id] = {"id": message_id, "conversation": "default", "status": "queued",
                                        "operation_id": operations.operation_id(message_id),
                                        "operation_state": "pending", "text": "第 %d 句" % step,
                                        "events": [], "partial": "", "rev": 0, "seq": step}
                dedupe[dedupe_key("default", "c%d" % step)] = {
                    "conversation": "default", "client_id": "c%d" % step, "id": message_id}
            elif roll < 0.4 and len(messages) > 3:
                messages.popitem(last=False)  # 挤掉最旧的
            else:
                message = messages[rng.choice(list(messages))]
                message.update(status=rng.choice(STATUSES), rev=message["rev"] + 1, seq=step,
                               reply="回复 %d" % rng.randrange(100))
                if rng.random() < 0.3:
                    message["events"].append({"kind": "route", "text": "第 %d 步" % step})
            ledger.save(messages, dedupe, step)
            states.append(state_of(messages, dedupe))
        return states

    def test_snapshot_plus_journal_matches_after_every_save(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                path = self.folder / ("s%d" % seed) / "messages.json"
                ledger = Ledger(path)
                ledger.load()
                states = self.scenario(seed, 50, ledger)
                self.assertEqual(on_disk(path), states[-1])
                messages, dedupe, _ = Ledger(path).load()
                self.assertEqual([m["id"] for m in messages.values()],
                                 [m["id"] for m in states[-1]["messages"]])

    def test_a_crash_at_any_write_recovers_a_state_that_really_existed(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                folder = self.folder / ("c%d" % seed)
                path, journal = folder / "messages.json", folder / "messages.jsonl"
                ledger = Ledger(path)
                ledger.load()
                seen, steps = [], []
                rng = random.Random(1000 + seed)
                append, replace = world.Journal._append, world.durable_write

                def read(target):
                    return target.read_bytes() if target.exists() else b""

                def appending(self_, entry):
                    before = read(journal)
                    append(self_, entry)
                    after = read(journal)
                    seen.append((len(steps), read(path), after[:rng.randrange(len(before), len(after))]))
                    seen.append((len(steps), read(path), after))

                def replacing(target, data):
                    seen.append((len(steps), read(path), read(journal)))
                    replace(target, data)
                    seen.append((len(steps), read(path), read(journal)))

                original_save = Ledger.save

                def save(self_, *args):
                    original_save(self_, *args)
                    steps.append(None)

                with mock.patch.object(world.Journal, "_append", appending), \
                        mock.patch.object(world, "durable_write", replacing), \
                        mock.patch.object(Ledger, "save", save):
                    states = self.scenario(seed, 40, ledger)
                crashed = self.folder / ("crashed%d" % seed)
                crashed.mkdir()
                for step, snapshot, data in seen:
                    (crashed / "messages.json").write_bytes(snapshot)
                    (crashed / "messages.jsonl").write_bytes(data)
                    if not snapshot:
                        (crashed / "messages.json").unlink()
                    # 第 step 次 save 写到一半断电：只能是它之前或之后的样子。
                    self.assertIn(on_disk(crashed / "messages.json"), states[step:step + 2],
                                  "seed %d step %d" % (seed, step))

    def test_the_snapshot_is_rewritten_only_every_few_saves(self):
        ledger = Ledger(self.path)
        ledger.load()
        messages, dedupe = OrderedDict(), OrderedDict()
        messages["m1"] = {"id": "m1", "conversation": "default", "status": "queued", "events": []}
        ledger.save(messages, dedupe, 1)  # 第一次：还没有快照，先写一份
        snapshot = self.path.read_bytes()
        for index in range(world.SNAPSHOT_EVERY - 1):
            messages["m1"]["rev"] = index
            ledger.save(messages, dedupe, 2 + index)
        self.assertEqual(self.path.read_bytes(), snapshot)
        messages["m1"]["rev"] = 99
        ledger.save(messages, dedupe, 99)
        self.assertNotEqual(self.path.read_bytes(), snapshot)
        self.assertEqual(world.read(self.folder / "messages.jsonl"), [])
        self.assertEqual(json.loads(self.path.read_text())["journal"], ledger._journal.n)

    def test_format_1_files_load_and_get_operations(self):
        self.path.write_text(json.dumps({"format": 1, "seq": 9, "messages": [
            {"id": "m1", "conversation": "default", "status": "done", "rev": 2, "seq": 7},
            {"id": "m2", "conversation": "default", "status": "queued", "rev": 0, "seq": 8}],
            "dedupe": [{"conversation": "default", "client_id": "a", "id": "m1"}]}), encoding="utf-8")
        messages, dedupe, seq = Ledger(self.path).load()
        self.assertEqual(seq, 9)
        self.assertEqual([(m["operation_id"], m["operation_state"]) for m in messages.values()],
                         [("m1:handle", "succeeded"), ("m2:handle", "pending")])
        self.assertEqual(list(dedupe), [dedupe_key("default", "a")])


class OperationServiceTests(TempDirCase):
    def start(self):
        agent = Scripted("claude")
        xiaoyu, store = make_xiaoyu(self.folder, [agent])
        service = Service(xiaoyu, store)
        self.addCleanup(service.close)
        self.addCleanup(agent.gate.set)
        return service, agent, xiaoyu, store

    @staticmethod
    def crash(service, xiaoyu):
        service._ledger.save = lambda *args: None
        xiaoyu.cards._save = lambda: None
        xiaoyu.cards._journal._append = lambda entry: None

    def disk(self, store, message_id):
        for item in ledger_module.current(store.state_dir / "messages.json")["messages"]:
            if item["id"] == message_id:
                return item
        return None

    def test_an_accepted_message_is_a_pending_operation_on_disk(self):
        service, agent, _, store = self.start()
        agent.gate.clear()
        first = service.submit("先占着 slow")
        queued = service.submit("排着")
        saved = self.disk(store, queued["id"])
        self.assertEqual((saved["operation_id"], saved["operation_state"]),
                         (queued["id"] + ":handle", "pending"))
        self.assertTrue(until(lambda: (self.disk(store, first["id"]) or {}).get("operation_state") == "running"))
        agent.gate.set()
        self.assertEqual(service.get(queued["id"], wait=5)["operation_state"], "succeeded")

    def test_a_duplicate_delivery_runs_once(self):
        service, agent, _, _ = self.start()
        agent.gate.clear()
        busy = service.submit("先占着 slow")
        message = service.submit("只做一次")
        with service._changed:
            service._line_up("default", message["id"])  # 同一句话又排进来一次
        agent.gate.set()
        service.get(busy["id"], wait=5)
        self.assertEqual(service.get(message["id"], wait=5)["status"], "done")
        self.assertTrue(until(lambda: not service._busy))
        self.assertEqual(agent.texts.count("只做一次"), 1)

    def test_a_late_update_cannot_reopen_a_finished_message(self):
        service, _, _, store = self.start()
        done = service.get(service.submit("说完了")["id"], wait=5)
        service._update(done["id"], status="running", stage="迟到的进展")
        again = service.get(done["id"])
        self.assertEqual((again["status"], again["operation_state"], again["rev"]),
                         ("done", "succeeded", done["rev"]))
        self.assertEqual(self.disk(store, done["id"])["operation_state"], "succeeded")

    def test_terminal_state_is_not_visible_until_its_journal_commit(self):
        service, agent, _, store = self.start()
        agent.gate.clear()
        sent = service.submit('finish slow')
        self.assertTrue(until(lambda: sent['id'] in service._messages
                              and service.get(sent['id'])['status'] == 'running'))
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = service._ledger.save

        def pause_terminal(messages, dedupe, seq):
            if messages[sent['id']]['operation_state'] == 'succeeded':
                entered.set()
                if not release.wait(5):
                    raise OSError('test commit timed out')
            return original(messages, dedupe, seq)

        with mock.patch.object(service._ledger, 'save', side_effect=pause_terminal):
            agent.gate.set()
            self.assertTrue(entered.wait(5))
            try:
                self.assertEqual(service.get(sent['id'])['status'], 'running')
                self.assertEqual(self.disk(store, sent['id'])['operation_state'], 'running')
            finally:
                release.set()
            self.assertEqual(service.get(sent['id'], wait=5)['status'], 'done')

    def test_failed_terminal_commit_keeps_the_last_durable_state(self):
        service, agent, _, store = self.start()
        agent.gate.clear()
        sent = service.submit('keep running slow')
        self.assertTrue(until(lambda: service.get(sent['id'])['status'] == 'running'))
        with mock.patch.object(service._ledger, 'save', side_effect=OSError('disk full')):
            service._update(sent['id'], status='done', reply='must not be announced')
        self.assertEqual(service.get(sent['id'])['status'], 'running')
        self.assertEqual(self.disk(store, sent['id'])['operation_state'], 'running')
        agent.gate.set()
        self.assertEqual(service.get(sent['id'], wait=5)['status'], 'done')

    def test_restart_continues_pending_once_and_marks_started_work_unknown(self):
        service, agent, first, store = self.start()
        agent.gate.clear()
        running = service.submit("第一句 slow")
        queued = service.submit("第二句")
        self.assertTrue(until(lambda: (self.disk(store, running["id"]) or {}).get("operation_state") == "running"))
        self.crash(service, first)
        again, again_agent, _, _ = self.start()
        done = again.get(queued["id"], wait=5)
        self.assertEqual((done["status"], done["operation_state"]), ("done", "succeeded"))
        self.assertEqual(again_agent.texts.count("第二句"), 1)
        lost = again.get(running["id"])
        self.assertEqual((lost["status"], lost["error"], lost["operation_state"]),
                         ("failed", RESTART_NOTE, "unknown"))
        self.assertNotIn("第一句 slow", again_agent.started)
        self.assertTrue(until(lambda: self.disk(store, running["id"])["operation_state"] == "unknown"))


if __name__ == "__main__":
    unittest.main()
