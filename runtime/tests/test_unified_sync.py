"""统一消息结构第 2 步：消息日志是权威记录，/v1/sync，runtime_id，一致性测试。

见 docs/development/unified-message-model.zh_CN.md。
"""

import json
import time
import unittest

import test_runtime
from test_runtime import Scripted, TempDirCase, make_xiaoyou, start_runtime, until

import xiaoyou_runtime.message_log as message_log
from xiaoyou_runtime import conformance
from xiaoyou_runtime.cards import Cards
from xiaoyou_runtime.desktop_sync import CONVERSATION, DesktopSync, Session
from xiaoyou_runtime.service import Service


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
        # 还在卡上的消息不会被挤掉。
        on_cards = {entry["id"] for card in xiaoyou.cards.recent(None, 1000) for entry in card["entries"]}
        self.assertTrue(on_cards <= set(self.all_messages(service)))

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
        self.assertEqual(sorted(info), ["capabilities", "epoch", "name", "protocol", "runtime_id",
                                        "version"])
        self.assertEqual(self.call("GET", "/v1/sync?after=x")[0], 400)
        status, body = self.call("GET", "/v1/sync?after=0")
        self.assertEqual((status, body["runtime_id"], body["reset"]), (200, info["runtime_id"], False))


if __name__ == "__main__":
    unittest.main()
