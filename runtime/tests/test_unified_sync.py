"""统一消息结构第 2 步：消息日志是权威记录，/v1/sync，runtime_id，一致性测试。

见 docs/development/unified-message-model.zh_CN.md。
"""

import json
import threading
import time
import unittest

import test_runtime
from test_runtime import Scripted, TempDirCase, make_xiaoyou, start_runtime, until

import xiaoyou_runtime.message_log as message_log
from xiaoyou_runtime import conformance
from xiaoyou_runtime.cards import Cards
from xiaoyou_runtime.desktop_sync import CONVERSATION, DesktopSync, Session
from xiaoyou_runtime.service import RequestError, Service


class ConformanceTests(TempDirCase):
    def test_the_runtime_passes_its_own_conformance_suite(self):
        base, _ = start_runtime(self, self.folder)
        results = conformance.run(base, test_runtime.TOKEN, reply_timeout=10)
        failed = [(name, detail) for name, ok, detail in results if not ok]
        self.assertEqual(failed, [])
        self.assertGreaterEqual(len(results), 20)

    def test_the_suite_notices_a_runtime_that_breaks_the_contract(self):
        base, _ = start_runtime(self, self.folder)
        original = Service.sync

        def forgetful(service, *args, **kwargs):
            body = original(service, *args, **kwargs)
            body["reset"] = False  # 不肯承认游标对不上
            return body
        Service.sync = forgetful
        self.addCleanup(setattr, Service, "sync", original)
        results = dict((name, ok) for name, ok, _ in
                       conformance.run(base, test_runtime.TOKEN, reply_timeout=10))
        self.assertFalse(results["sync.epoch_mismatch_resets"])
        self.assertFalse(results["sync.cursor_ahead_resets"])

    def test_the_suite_notices_a_reset_without_a_snapshot(self):
        base, _ = start_runtime(self, self.folder)
        original = Service.sync

        def empty(service, *args, **kwargs):
            body = original(service, *args, **kwargs)
            if body["reset"]:
                body.update(messages=[], tasks=[])  # 说要重来，却不给快照
            return body
        Service.sync = empty
        self.addCleanup(setattr, Service, "sync", original)
        results = dict((name, ok) for name, ok, _ in
                       conformance.run(base, test_runtime.TOKEN, reply_timeout=10))
        self.assertFalse(results["sync.epoch_mismatch_resets"])


class UnifiedSyncTests(TempDirCase):
    def start(self, folder=None):
        agent = Scripted("claude")
        xiaoyou, store = make_xiaoyou(folder or self.folder, [agent])
        service = Service(xiaoyou, store)
        self.addCleanup(service.close)
        self.addCleanup(agent.gate.set)
        return service, agent, xiaoyou

    def all_messages(self, service, conversation="default"):
        page, found = service.sync(conversation, 0), {}
        while True:
            found.update({item["id"]: item for item in page["messages"]})
            if not page["more"]:
                return found
            page = service.sync(conversation, page["seq"])

    def test_a_desktop_session_that_finished_its_turn_is_turn_ended_only_in_sync(self):
        service, _, xiaoyou = self.start()
        card = xiaoyou.cards.open(CONVERSATION, "电脑上的会话", "unknown", "codex")
        xiaoyou.cards.update(card["id"], state="done", monitoring_only=True, source="desktop",
                             desktop_status="idle", session_id="s1")
        task = next(item for item in service.sync("default", 0)["tasks"] if item["id"] == card["id"])
        self.assertEqual("turn_ended", task["state"])
        # Older apps keep seeing done on /v1/feed, and the card itself is unchanged.
        fed = next(item for item in service.feed("default", 0)["cards"] if item["id"] == card["id"])
        self.assertEqual("done", fed["state"])
        self.assertEqual("done", xiaoyou.cards.get(card["id"])["state"])
        # A session whose state can no longer be seen is unavailable in sync, unknown on the feed.
        lost = xiaoyou.cards.open(CONVERSATION, "看不到的会话", "unknown", "codex")
        xiaoyou.cards.update(lost["id"], state="unknown", monitoring_only=True, source="desktop",
                             desktop_status="unknown", session_id="s2", stale=True)
        self.assertEqual("unavailable", next(item for item in service.sync("default", 0, states=["unavailable"])["tasks"]
                                             if item["id"] == lost["id"])["state"])
        self.assertEqual("unknown", next(item for item in service.sync("default", 0)["tasks"]
                                         if item["id"] == lost["id"])["state"])
        self.assertEqual("unknown", next(item for item in service.feed("default", 0)["cards"]
                                         if item["id"] == lost["id"])["state"])
        # A task the runtime itself finished is simply done.
        own = xiaoyou.cards.open("default", "自己的事", "done", "codex")
        self.assertEqual("done", next(item for item in service.sync("default", 0)["tasks"]
                                      if item["id"] == own["id"])["state"])

    def test_subject_is_separate_from_the_runtime_and_records_carry_their_origin(self):
        service, _, xiaoyou = self.start()
        info = service.info()
        self.assertRegex(info["subject_id"], "^[0-9a-f]{32}$")
        self.assertNotEqual(info["subject_id"], info["runtime_id"])
        message = service.submit("记一句", client_id="o1")
        service.get(message["id"], wait=5)
        page = service.sync("default", 0)
        self.assertEqual(info["subject_id"], page["subject_id"])
        mine = [item for item in self.all_messages(service).values() if item["id"].startswith(message["id"])]
        self.assertTrue(mine)
        self.assertTrue(all(item["source"].get("runtime_id") == info["runtime_id"] for item in mine))
        # On disk too, so a moved or merged log still knows where each record was made.
        lines = [json.loads(line) for line in (self.folder / "state" / "cards.log.jsonl").read_text("utf-8").splitlines()]
        written = [line for line in lines if line.get("id", "").startswith(message["id"])]
        self.assertTrue(written)
        self.assertTrue(all(line["source"].get("runtime_id") == info["runtime_id"] for line in written))
        # The same state directory keeps both ids after a restart.
        again, _, _ = self.start()
        self.assertEqual((info["subject_id"], info["runtime_id"]),
                         (again.info()["subject_id"], again.info()["runtime_id"]))

    def test_approvals_come_by_sequence_with_their_outcome(self):
        service, _, xiaoyou = self.start()
        card = xiaoyou.cards.open("default", "要确认的事", "working", "claude")
        cursor = self.all_seq(service)
        asked = xiaoyou.approvals.ask(card["id"], "default", "claude", "Bash", "ls")
        page = service.sync("default", cursor)
        item = next(entry for entry in page["approvals"] if entry["id"] == asked)
        self.assertEqual(("pending", "Bash"), (item["status"], item["tool"]))
        self.assertGreater(item["seq"], cursor)
        # An approval that appears and is answered between two syncs still shows its outcome.
        quick = xiaoyou.approvals.ask(card["id"], "default", "claude", "Write", "x")
        xiaoyou.approvals.answer(quick, "allow")
        xiaoyou.approvals.withdraw(asked)
        later = {entry["id"]: entry for entry in service.sync("default", page["seq"])["approvals"]}
        self.assertEqual(("allowed", "phone"), (later[quick]["status"], later[quick]["by"]))
        self.assertEqual(("withdrawn", "unanswered"), (later[asked]["status"], later[asked]["reason"]))
        self.assertGreater(later[asked]["seq"], later[quick]["seq"])
        # Another conversation's approvals are not included.
        other = xiaoyou.cards.open("other", "别的对话", "working", "claude")
        hidden = xiaoyou.approvals.ask(other["id"], "other", "claude", "Bash", "pwd")
        self.assertNotIn(hidden, {entry["id"] for entry in service.sync("default", 0)["approvals"]})
        # Only an approval change still wakes a waiting sync.
        seq = max(self.all_seq(service), max(entry["seq"] for entry in service.sync("default", 0)["approvals"]))
        timer = threading.Timer(0.3, lambda: xiaoyou.approvals.ask(card["id"], "default", "claude", "Edit", "y"))
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        woke = service.sync("default", seq, None, 5)
        self.assertLess(time.monotonic() - started, 4)
        self.assertIn("Edit", {entry["tool"] for entry in woke["approvals"]})

    def all_seq(self, service):
        page = service.sync("default", 0)
        while page["more"]:
            page = service.sync("default", page["seq"])
        return page["seq"]

    def test_sync_carries_usage_and_wakes_up_when_it_changes(self):
        service, _, xiaoyou = self.start()
        page = service.sync("default", 0)
        self.assertEqual(xiaoyou.usage.rev, page["usage"]["rev"])
        seq = service.sync("default", page["seq"])["seq"]
        # Nothing changed and the caller already has this usage: it waits the whole time.
        started = time.monotonic()
        service.sync("default", seq, None, 0.3, xiaoyou.usage.rev)
        self.assertGreaterEqual(time.monotonic() - started, 0.25)
        # An older usage revision returns at once, with the current usage.
        started = time.monotonic()
        quick = service.sync("default", seq, None, 5, xiaoyou.usage.rev - 1)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(xiaoyou.usage.rev, quick["usage"]["rev"])
        with self.assertRaises(RequestError):
            service.sync("default", seq, None, 0, "3")

    def test_a_question_and_its_reply_are_two_messages_with_stable_ids(self):
        service, agent, _ = self.start()
        agent.gate.clear()
        message = service.submit("一个问题 slow", client_id="q1")
        first = service.sync("default", 0)
        you = {item["id"]: item for item in first["messages"]}[message["id"] + ":you"]
        self.assertEqual((you["text"], you["status"], you["author"]["kind"]),
                         ("一个问题 slow", "final", "owner"))
        until(lambda: service.get(message["id"])["status"] == "running")
        service._report(message["id"], "partial", None, "想到一半")
        streaming = self.all_messages(service)[message["id"] + ":reply"]
        self.assertEqual((streaming["status"], streaming["partial"]), ("streaming", "想到一半"))
        agent.gate.set()
        service.get(message["id"], wait=5)
        self.assertTrue(until(lambda: self.all_messages(service)[message["id"] + ":reply"]["status"]
                              == "final"))
        final = self.all_messages(service)
        reply = final[message["id"] + ":reply"]
        self.assertEqual((reply["text"], reply["partial"], reply["reply_to"]),
                         ("re:一个问题 slow", "", message["id"] + ":you"))
        self.assertGreater(reply["rev"], streaming["rev"])
        # 卡上的条目就是这两条消息的副本，编号一样。
        task = next(task for task in service.sync("default", 0)["tasks"] if task["id"] == reply["task"])
        self.assertEqual(task["members"], [message["id"] + ":you", message["id"] + ":reply"])
        self.assertNotIn("entries", task)
        # 定稿之后，请求那边再报什么都不会让它退回生成中。
        service._xiaoyou.cards.request(dict(service.get(message["id"]), status="running",
                                            partial="旧的一段"))
        self.assertEqual(self.all_messages(service)[message["id"] + ":reply"]["status"], "final")

    def test_identity_survives_restarts_and_the_log_wins_over_card_copies(self):
        service, _, xiaoyou = self.start()
        done = service.get(service.submit("记住这句")["id"], wait=5)
        self.assertTrue(until(lambda: done["id"] + ":reply" in self.all_messages(service)))
        before = service.info()
        state = xiaoyou._config.state_dir
        # 把卡文件里的副本改坏：重启后以日志为准。
        raw = json.loads((state / "cards.json").read_text(encoding="utf-8"))
        for card in raw["cards"]:
            for entry in card["entries"]:
                if entry["id"] == done["id"] + ":reply":
                    entry["text"] = "被改坏的副本"
        (state / "cards.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        again, _, xiaoyou_again = self.start(self.folder)
        self.assertEqual((again.info()["runtime_id"], again.info()["epoch"]),
                         (before["runtime_id"], before["epoch"]))
        card = xiaoyou_again.cards.get(done["card"])
        self.assertEqual(card["entries"][-1]["text"], "re:记住这句")
        other, _, _ = self.start(self.folder / "elsewhere")
        self.assertNotEqual(other.info()["runtime_id"], before["runtime_id"])

    def test_cards_written_by_an_older_runtime_are_adopted_with_ids_that_stay(self):
        state = self.folder / "state"
        state.mkdir(parents=True)
        (state / "cards.json").write_text(json.dumps({"next": 2, "cards": [{
            "id": "c1", "conversation": "default", "title": "旧的", "state": "done", "seq": 7,
            "entries": [{"role": "you", "text": "旧问题", "at": 1.0},
                        {"role": "xiaoyou", "text": "旧回答", "at": 2.0}]}]},
            ensure_ascii=False), encoding="utf-8")
        service, _, xiaoyou = self.start()
        first = self.all_messages(service)
        self.assertEqual(sorted(item["text"] for item in first.values()), ["旧回答", "旧问题"])
        self.assertTrue(all(key.startswith("c1-") for key in first))
        again, _, _ = self.start(self.folder)
        self.assertEqual(sorted(self.all_messages(again)), sorted(first))

    def test_desktop_sessions_are_read_only_messages_with_their_own_source(self):
        service, _, xiaoyou = self.start()
        sync = DesktopSync(xiaoyou.cards, self.folder, home=self.folder, clock=lambda: 100)
        session = Session("codex", "s1", cwd=str(self.folder), status="running", observed_at=100)
        sync._content(session, "you", "电脑上问的", 99, identity="u1")
        sync._content(session, "xiaoyou", "电脑上答的", 100, identity="a1")
        sync._publish(session, 100)
        found = [item for item in self.all_messages(service).values()
                 if item["conversation"] == CONVERSATION]
        self.assertEqual(sorted((item["text"], item["author"]["kind"], item["source"]["type"])
                                for item in found),
                         sorted([("电脑上问的", "owner", "desktop"), ("电脑上答的", "desktop", "desktop")]))

    def test_a_streaming_summary_becomes_the_message_that_finally_lands_on_the_card(self):
        service, _, xiaoyou = self.start()
        card = xiaoyou.cards.open("default", "后台的事", "working", "codex")
        xiaoyou.cards.update(card["id"], run_id="r1", summary_partial="转述到一半")
        live = self.all_messages(service)["r1-summary:reply"]
        self.assertEqual((live["status"], live["partial"]), ("streaming", "转述到一半"))
        xiaoyou.cards.update(card["id"], say="转述完了", turn_id="r1-summary", kind="summary",
                             state="done", summary_partial="")
        final = self.all_messages(service)["r1-summary:reply"]
        self.assertEqual((final["status"], final["text"], final["partial"]),
                         ("final", "转述完了", ""))

    def test_revisions_never_go_back_even_for_streaming_that_was_never_written(self):
        service, agent, xiaoyou = self.start()
        agent.gate.clear()
        message = service.submit("说很久 slow")
        until(lambda: service.get(message["id"])["status"] == "running")
        for index in range(50):
            service._report(message["id"], "partial", None, "第 %d 段" % index)
        before = self.all_messages(service)[message["id"] + ":reply"]
        self.assertEqual(before["status"], "streaming")
        # 断电：之后旧的这一台什么都写不进去了。
        service._ledger.save = lambda *args: None
        xiaoyou.cards._save = lambda: None
        xiaoyou.cards._log._append = lambda records: None
        again, _, _ = self.start(self.folder)
        after = self.all_messages(again)[message["id"] + ":reply"]
        self.assertEqual(after["status"], "failed")
        self.assertGreater(after["rev"], before["rev"])

    def test_a_lost_card_file_does_not_make_sequence_numbers_repeat(self):
        path = self.folder / "alone" / "cards.json"
        cards = Cards(path)
        for index in range(5):
            card = cards.open("default", "第 %d 件" % index)
            cards.update(card["id"], said="问 %d" % index, say="答 %d" % index, turn_id="t%d" % index)
        highest = max(cards.message("t%d:%s" % (index, role))["seq"]
                      for index in range(5) for role in ("you", "reply"))
        path.unlink()  # 卡文件丢了，日志还在
        again = Cards(path)
        self.assertGreaterEqual(again.seq, highest)
        _, _, messages, _ = again.sync_since("default", 0)
        self.assertEqual(len(messages), 10)
        card = again.open("default", "新的")
        again.update(card["id"], said="新问", turn_id="n1")
        self.assertGreater(again.message("n1:you")["seq"], highest)

    def test_a_summary_that_never_lands_is_closed_when_its_task_ends(self):
        service, _, xiaoyou = self.start()
        card = xiaoyou.cards.open("default", "后台的事", "working", "codex")
        xiaoyou.cards.update(card["id"], run_id="r2", summary_partial="说到一半")
        xiaoyou.cards.update(card["id"], state="cancelled", summary_partial="")
        closed = self.all_messages(service)["r2-summary:reply"]
        self.assertEqual((closed["status"], closed["partial"]), ("cancelled", ""))

    def test_a_torn_last_line_does_not_swallow_the_next_message(self):
        service, _, xiaoyou = self.start()
        service.get(service.submit("一")["id"], wait=5)
        log = xiaoyou._config.state_dir / "cards.log.jsonl"
        with log.open("a", encoding="utf-8") as handle:
            handle.write('{"id": "torn", "text": "半')
        second, _, _ = self.start(self.folder)
        kept = second.get(second.submit("二")["id"], wait=5)
        # 之后写的每一行都完整可读：没有接在那半行后面。
        lines = log.read_text(encoding="utf-8").splitlines()
        parsed = [json.loads(line) for line in lines]
        self.assertIn(kept["id"] + ":you", [item.get("id") for item in parsed])
        third, _, _ = self.start(self.folder)
        self.assertIn(kept["id"] + ":you", self.all_messages(third))

    def test_old_cursors_beyond_the_retained_log_ask_for_a_snapshot(self):
        old = message_log.MAX_MESSAGES
        message_log.MAX_MESSAGES = 6
        self.addCleanup(setattr, message_log, "MAX_MESSAGES", old)
        service, _, xiaoyou = self.start()
        early = service.sync("default", 0)["seq"]
        for index in range(70):  # 卡只留 50 张：前面那些卡上的消息就不再受保护
            card = xiaoyou.cards.open("default", "第 %d 件" % index)
            xiaoyou.cards.update(card["id"], said="问 %d" % index, say="答 %d" % index,
                                 turn_id="t%d" % index)
        self.assertGreater(xiaoyou.cards.log_floor, early)
        self.assertTrue(service.sync("default", early + 1)["reset"])
        # 还在卡上的消息不会被日志挤掉（快照只放最近的，见 test_sync_window.py）。
        on_cards = {entry["id"] for card in xiaoyou.cards.recent(None, 1000) for entry in card["entries"]}
        self.assertTrue(all(xiaoyou.cards._log.get(message_id) for message_id in on_cards))

    def test_a_half_written_last_line_loses_only_that_line(self):
        service, _, xiaoyou = self.start()
        service.get(service.submit("一")["id"], wait=5)
        log = xiaoyou._config.state_dir / "cards.log.jsonl"
        self.assertTrue(until(lambda: log.exists() and log.stat().st_size > 0))
        with log.open("a", encoding="utf-8") as handle:
            handle.write('{"id": "broken", "text": "半')
        again, _, _ = self.start(self.folder)
        texts = {item["text"] for item in self.all_messages(again).values()}
        self.assertIn("一", texts)
        self.assertNotIn("半", texts)

    def test_sync_waits_for_changes_and_returns_them_promptly(self):
        service, _, _ = self.start()
        cursor = service.sync("default", 0)["seq"]
        started = time.monotonic()
        quiet = service.sync("default", cursor, wait=0.3)
        self.assertGreaterEqual(time.monotonic() - started, 0.25)
        self.assertEqual((quiet["messages"], quiet["tasks"], quiet["reset"]), ([], [], False))
        import threading
        result = {}
        waiter = threading.Thread(target=lambda: result.update(service.sync("default", cursor, wait=5)))
        waiter.start()
        time.sleep(0.1)
        message = service.submit("来了")
        waiter.join(3)
        self.assertFalse(waiter.is_alive())
        self.assertIn(message["id"] + ":you", [item["id"] for item in result["messages"]])


class HttpTests(TempDirCase):
    call = test_runtime.HttpTests.call

    def setUp(self):
        super().setUp()
        self.base, self.config = start_runtime(self, self.folder)

    def test_info_and_sync_need_the_token_and_describe_the_runtime(self):
        self.assertEqual(self.call("GET", "/v1/info", token=None)[0], 401)
        self.assertEqual(self.call("GET", "/v1/sync", token=None)[0], 401)
        status, info = self.call("GET", "/v1/info")
        self.assertEqual(status, 200)
        self.assertEqual(sorted(info), ["capabilities", "epoch", "events", "host", "iterate", "name", "presentation_policy", "protocol", "runtime_id",
                                        "subject_id", "version"])
        self.assertEqual(1, info['presentation_policy']['version'])
        self.assertIn('presentation_v1', info['capabilities'])
        self.assertIn("events_v1", info["capabilities"])
        self.assertEqual(info["events"]["version"], 1)
        self.assertTrue(info["events"]["types"])
        self.assertEqual(info["iterate"]["stages"], ["collect", "detect"])
        self.assertEqual(self.call("GET", "/v1/sync?after=x")[0], 400)
        status, body = self.call("GET", "/v1/sync?after=0")
        self.assertEqual((status, body["runtime_id"], body["reset"]), (200, info["runtime_id"], False))


if __name__ == "__main__":
    unittest.main()
