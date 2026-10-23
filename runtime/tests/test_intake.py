"""Intake：写入经它走，日志和改造前逐条一致（对拍），并带上来源（provenance）。

对拍：同一段固定的剧本（聊一句、派一件事做完并转述、派一件慢事再从 App 停掉）跑完后，
统一消息日志和卡片归一化（去掉时间、随机编号换成按出现先后的记号、去掉 provenance /
changed_by）后，必须和 intake_golden.json 一致。基准用改造前的代码生成；以后改动若
有意改变了日志，设 INTAKE_GOLDEN_WRITE=1 重新生成并在提交里说明。
"""

import ast
import concurrent.futures
import threading
import json
import os
import re
import sys
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_runtime import Scripted, TempDirCase, make_xiaoyou, until  # noqa: E402
from xiaoyou_runtime.intake import Intake, Writer, WRITES  # noqa: E402
from xiaoyou_runtime.service import Service  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "intake_golden.json"
VOLATILE = re.compile(r"[0-9a-f]{12,32}")
DROP = {"provenance", "changed_by"}


def normalize(value, tokens):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in DROP:
                continue
            if (key == "at" or key.endswith("_at")) and isinstance(item, (int, float)):
                out[key] = 0 if item else item
                continue
            out[key] = normalize(item, tokens)
        return out
    if isinstance(value, list):
        return [normalize(item, tokens) for item in value]
    if isinstance(value, str):
        return VOLATILE.sub(lambda match: tokens.setdefault(match.group(0), "<id%d>" % len(tokens)), value)
    return value


class IntakeTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.voice = Scripted("claude")
        self.codex = Scripted("codex", speaks=False)
        self.xiaoyou, self.store = make_xiaoyou(self.folder, [self.voice, self.codex])
        self.service = Service(self.xiaoyou, self.store)
        self.addCleanup(self.service.close)
        self.addCleanup(self.codex.gate.set)

    def finish(self, message):
        done = self.service.get(message["id"], wait=5)
        self.assertIn(done["status"], ("done", "failed"))
        self.assertTrue(until(lambda: not self.service._busy))
        return done

    def play(self, controlled=True):
        """固定剧本。每一步都等它停稳，日志的先后才是确定的。"""
        # Production workers remain concurrent. Only the golden scenario queues their
        # start until the HTTP/request lane has persisted its handoff reply.
        tasks = self.xiaoyou._tasks
        pending = []
        start = tasks.start
        if controlled:
            patch = mock.patch.object(tasks, "start", side_effect=lambda task: pending.append(task) or True)
            patch.start()
            self.addCleanup(patch.stop)

        def release_start():
            if controlled:
                self.assertEqual(len(pending), 1)
                self.assertTrue(start(pending.pop()))

        self.finish(self.service.submit("你好", client_id="m1"))
        handed = self.finish(self.service.submit("查日志", client_id="m2", agent="codex"))
        release_start()
        settled = self.xiaoyou.settle(handed["card"], 5)
        self.assertEqual(settled["state"], "done")
        self.assertTrue(self.until_quiet(handed["card"]))
        self.assertTrue(until(lambda: not self.service._busy))
        self.codex.gate.clear()
        slow = self.finish(self.service.submit("one slow", client_id="m3", agent="codex"))
        release_start()
        # 帮手真正开始了再停：不然停和开始谁先谁后不一定。
        self.assertTrue(until(lambda: any(text.endswith("one slow") for text in self.codex.started)))
        self.service.cancel(slow["card"], actor="app", reason="不要了")
        self.codex.gate.set()
        # 等被停的帮手线程自己收完尾（它会记一条 cancelled 的进展）。
        log = self.store.state_dir / "cards.log.jsonl"
        self.assertTrue(until(lambda: '"phase": "cancelled"' in log.read_text()))
        self.assertTrue(until(lambda: not self.xiaoyou._tasks.active(slow["card"])))
        for card_id in (handed["card"], slow["card"]):
            self.assertTrue(until(lambda card_id=card_id: all(
                attempt.get("ended_at") for attempt in self.xiaoyou.cards.get(card_id).get("execution_attempts", []))))
        self.assertTrue(self.until_still(log))

    @staticmethod
    def until_still(path, quiet=0.5, limit=10.0):
        """日志连续 quiet 秒没有再变，才算剧本跑完。"""
        import time
        deadline, last, since = time.monotonic() + limit, None, time.monotonic()
        while time.monotonic() < deadline:
            size = path.stat().st_size
            if size != last:
                last, since = size, time.monotonic()
            elif time.monotonic() - since >= quiet:
                return True
            time.sleep(0.05)
        return False

    def until_quiet(self, card_id):
        """转述那句话写上卡才算这件事收尾了。"""
        return until(lambda: any(entry["kind"] == "summary"
                                 for entry in self.xiaoyou.cards.get(card_id)["entries"]))

    def snapshot(self):
        state = self.store.state_dir
        log = [json.loads(line) for line in (state / "cards.log.jsonl").read_text().splitlines() if line.strip()]
        cards = json.loads((state / "cards.json").read_text())
        tokens = {}
        return {"log": normalize(log, tokens), "cards": normalize(cards, tokens)}

    def test_the_log_matches_the_record_before_intake(self):
        self.play()
        got = self.snapshot()
        if os.environ.get("INTAKE_GOLDEN_WRITE") == "1":
            GOLDEN.write_text(json.dumps(got, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        self.assertEqual(json.loads(GOLDEN.read_text()), got)

    def test_a_writer_stamps_where_a_write_came_from(self):
        writer = self.xiaoyou.intake.writer("owner", actor="app")
        self.assertIsInstance(writer, Writer)
        card = writer.task_open("default", "试一下", "working", "codex")
        self.assertEqual(card["changed_by"], {"source": "owner", "actor": "app"})
        card = writer.by(cause="m9").exchange(card["id"], said="继续", say="好", turn_id="t1")
        stamped = [entry["provenance"] for entry in card["entries"]]
        self.assertEqual(stamped, [{"source": "owner", "actor": "app", "cause": "m9"}] * 2)
        # Outside a writer nothing is stamped; reads pass straight through.
        plain = self.xiaoyou.record.task_open("default", "不经 Intake", "done")
        self.assertNotIn("changed_by", plain)
        self.assertEqual(writer.cards, self.xiaoyou.record.cards)
        # The log carries the same provenance on the messages.
        log = (self.store.state_dir / "cards.log.jsonl").read_text()
        self.assertIn('"provenance": {"actor": "app", "cause": "m9", "source": "owner"}',
                      json.dumps([json.loads(line) for line in log.splitlines()], sort_keys=True, ensure_ascii=False))

    def test_concurrent_workers_preserve_order_and_terminal_results(self):
        self.play(controlled=False)
        state = self.snapshot()
        events = state["log"]
        sequences = [event["seq"] for event in events]
        self.assertEqual(sequences, sorted(set(sequences)))
        self.assertTrue(all(event["rev"] == event["seq"] for event in events))
        cards = self.xiaoyou.cards.recent("default", 10)
        self.assertEqual([card["state"] for card in cards], ["done", "done", "cancelled"])
        completed, cancelled = cards[1:]
        self.assertEqual(sum(e.get("kind") == "worker_result" for e in completed["entries"]), 1)
        self.assertEqual(sum(e.get("kind") == "summary" for e in completed["entries"]), 1)
        self.assertEqual(sum(e.get("kind") == "summary" for e in cancelled["entries"]), 1)
        self.assertFalse(any(e.get("kind") == "worker_result" for e in cancelled["entries"]))
        self.assertEqual(cancelled["cancelled_by"], "app")

    def test_writer_context_is_isolated_and_restored_after_failure(self):
        cards = self.xiaoyou.cards
        barrier = threading.Barrier(2)
        def write(source):
            writer = self.xiaoyou.intake.writer(source, actor=source, cause=source + "-event")
            # Force concurrent contexts across a barrier, not just sequential calls.
            with cards.provenance(source=source, actor=source):
                barrier.wait(timeout=5)
                card = writer.task_open("default", source)
                self.assertEqual(cards.current_provenance(), {"source": source, "actor": source})
            self.assertIsNone(cards.current_provenance())
            return card
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, ("owner", "helper")))
        self.assertEqual([c["changed_by"]["source"] for c in results], ["owner", "helper"])
        writer = self.xiaoyou.intake.writer("helper", actor="codex")
        with cards.provenance(source="owner", actor="app", cause="owner-event"):
            with self.assertRaises(ValueError):
                writer.task(results[0]["id"], invalid_field=True)
            self.assertEqual(cards.current_provenance()["cause"], "owner-event")
            updated = writer.task(results[0]["id"], state="failed")
            self.assertEqual(updated["changed_by"], {"source": "helper", "actor": "codex"})
        self.assertIsNone(cards.current_provenance())

    def test_requests_and_helper_activity_carry_their_source(self):
        self.play()
        events = [json.loads(line) for line in
                  (self.store.state_dir / "cards.log.jsonl").read_text().splitlines()]
        first = events[0]
        self.assertEqual(first["provenance"]["source"], "owner")
        self.assertEqual(first["provenance"]["cause"] + ":you", first["id"])
        activity = [e for e in events if e.get("kind") == "activity"]
        self.assertTrue(activity)
        self.assertTrue(all(e["provenance"]["source"] == "helper" for e in activity))
        self.assertTrue(all(e["provenance"]["actor"] == "codex" for e in activity))

    def test_observed_sessions_cannot_write_the_main_chat(self):
        writer = self.xiaoyou.intake.writer("desktop", actor="observer")
        with self.assertRaises(ValueError):
            writer.observed_open("default", "wrong", "unknown", "codex")
        card = writer.observed_open("__desktop__", "session", "unknown", "codex")
        self.assertTrue(card["monitoring_only"])
        writer.observed(card["id"], entries=[{"id": "d1", "role": "you", "text": "observed", "at": 1}])
        self.assertEqual(self.xiaoyou.cards.recent("default"), [])
        restored = type(self.xiaoyou.cards)(self.store.state_dir / "cards.json").get(card["id"])
        self.assertEqual(restored["entries"][0]["provenance"]["source"], "desktop")
        self.assertEqual(restored["changed_by"]["source"], "desktop")

    def test_unknown_sources_are_refused(self):
        with self.assertRaises(ValueError):
            Intake(self.xiaoyou.record).writer("somewhere")


class IntakeBoundaryTests(unittest.TestCase):

    def test_every_record_mutator_is_wrapped(self):
        from xiaoyou_runtime.record import Record
        methods = {name for name, value in vars(Record).items()
                   if not name.startswith("_") and callable(value)}
        self.assertEqual(methods - {"opening"}, set(WRITES))


if __name__ == "__main__":
    unittest.main()
