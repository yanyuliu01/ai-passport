"""收下的话落盘、重启后恢复、按对话去重，以及 feed 用一个游标同时给卡和消息。

对应审查文档 docs/assets/xiaoyou-harness-review.zh_CN.md 里的 R1（收下的话只在内存）、
R2（排着的补充满了被悄悄丢掉）、R3（去重只看 client_id）和 R6（电脑会话传播太慢）。
"""

import json
import threading
import time
import unittest

import test_runtime
from test_runtime import Scripted, TempDirCase, make_xiaoyou, start_runtime, until

import xiaoyou_runtime.desktop_sync as desktop_sync
import xiaoyou_runtime.service as service_module
from xiaoyou_runtime.service import ConflictError, RESTART_NOTE, Service
from xiaoyou_runtime.tasks import MAX_PENDING, Task, Tasks


class MessageLedgerTests(TempDirCase):
    def start(self, folder=None):
        agent = Scripted("claude")
        xiaoyou, store = make_xiaoyou(folder or self.folder, [agent])
        service = Service(xiaoyou, store)
        self.addCleanup(service.close)
        self.addCleanup(agent.gate.set)
        return service, agent, xiaoyou, store

    def ledger(self, store):
        return json.loads((store.state_dir / "messages.json").read_text(encoding="utf-8"))

    def test_an_accepted_message_is_on_disk_before_the_caller_hears_back(self):
        service, agent, _, store = self.start()
        agent.gate.clear()
        message = service.submit("记下这句 slow", client_id="phone-1")
        saved = {item["id"]: item for item in self.ledger(store)["messages"]}
        self.assertEqual(saved[message["id"]]["text"], "记下这句 slow")
        self.assertEqual(self.ledger(store)["dedupe"][0]["client_id"], "phone-1")

    def test_after_a_restart_queued_text_goes_on_and_unfinished_work_is_not_redone(self):
        service, agent, _, store = self.start()
        agent.gate.clear()
        running = service.submit("第一句 slow", client_id="a")
        queued = service.submit("第二句", client_id="b")
        self.assertTrue(until(lambda: service.get(running["id"])["status"] == "running"))
        self.assertEqual(service.get(queued["id"])["status"], "queued")

        # 第一台还卡在模型里时，用同一份记录起一台新的：等于 Runtime 被重启。
        again, again_agent, xiaoyou, _ = self.start(self.folder)
        done = again.get(queued["id"], wait=5)
        self.assertEqual((done["status"], done["reply"]), ("done", "re:第二句"))
        # 已经开始处理的不知道做到哪一步：不重做，标成没做完，并在对话里留下这句话和原因。
        lost = again.get(running["id"])
        self.assertEqual((lost["status"], lost["error"]), ("failed", RESTART_NOTE))
        card = xiaoyou.cards.get(lost["card"])
        self.assertEqual([(entry["role"], entry["text"], entry["id"]) for entry in card["entries"]],
                         [("you", "第一句 slow", running["id"] + ":you"),
                          ("xiaoyou", RESTART_NOTE, running["id"] + ":reply")])
        self.assertNotIn("第一句 slow", again_agent.started)
        # 拿着原来的编号来问、带着原来的 client_id 重试，都还是那一句。
        self.assertEqual(again.submit("第二句", client_id="b")["id"], queued["id"])
        self.assertEqual(again.submit("第一句 slow", client_id="a")["status"], "failed")

    def test_client_ids_are_scoped_to_a_conversation_and_bound_to_their_content(self):
        service, _, _, _ = self.start()
        first = service.submit("你好", conversation="A", client_id="same")
        self.assertEqual(service.submit("你好", conversation="A", client_id="same")["id"], first["id"])
        with self.assertRaises(ConflictError):
            service.submit("别的内容", conversation="A", client_id="same")
        other = service.submit("另一个对话", conversation="B", client_id="same")
        self.assertNotEqual(other["id"], first["id"])
        self.assertEqual(other["conversation"], "B")

    def test_a_retry_long_after_the_message_was_trimmed_is_still_recognised(self):
        old = service_module.MAX_KEPT_MESSAGES
        service_module.MAX_KEPT_MESSAGES = 2
        self.addCleanup(setattr, service_module, "MAX_KEPT_MESSAGES", old)
        service, _, _, _ = self.start()
        first = service.submit("一", client_id="c1")
        service.get(first["id"], wait=5)
        for index in range(3):
            service.get(service.submit("后面 %d" % index)["id"], wait=5)
        self.assertIsNone(service.get(first["id"]))
        retry = service.submit("一", client_id="c1")
        self.assertEqual((retry["id"], retry["status"], retry.get("expired")),
                         (first["id"], "done", True))

    def test_a_broken_ledger_does_not_stop_the_runtime(self):
        _, _, _, store = self.start()
        (store.state_dir / "messages.json").write_text("{not json", encoding="utf-8")
        service, _, _, _ = self.start(self.folder)
        self.assertEqual(service.get(service.submit("还能用")["id"], wait=5)["status"], "done")
        self.assertTrue((store.state_dir / "messages.json.broken").exists())


class FeedMessagesTests(TempDirCase):
    def start(self, folder=None):
        agent = Scripted("claude")
        xiaoyou, store = make_xiaoyou(folder or self.folder, [agent])
        service = Service(xiaoyou, store)
        self.addCleanup(service.close)
        self.addCleanup(agent.gate.set)
        return service, agent, xiaoyou

    def test_another_client_sees_a_question_while_it_is_still_being_answered(self):
        service, agent, _ = self.start()
        base = service.feed("default")
        agent.gate.clear()
        message = service.submit("还在想 slow", client_id="p1")
        feed = service.feed("default", base["seq"], 0, messages=True)
        self.assertEqual([(item["id"], item["text"]) for item in feed["messages"]],
                         [(message["id"], "还在想 slow")])
        self.assertEqual(feed["cards"], [])  # 卡要等回复出来才有

        # 生成中的回复也到得了另一端，而且只等到真的变了才返回。
        seen = {}
        waiter = threading.Thread(target=lambda: seen.update(
            service.feed("default", feed["seq"], 5, messages=True)))
        waiter.start()
        until(lambda: service.get(message["id"])["status"] == "running")
        service._report(message["id"], "partial", None, "想到一半")
        waiter.join(3)
        self.assertFalse(waiter.is_alive())
        partials = [item["partial"] for item in seen["messages"] if item["id"] == message["id"]]
        self.assertIn("想到一半", partials + [service.get(message["id"])["partial"]])

        agent.gate.set()
        service.get(message["id"], wait=5)
        final = service.feed("default", feed["seq"], 0, messages=True)
        self.assertEqual([item["status"] for item in final["messages"]], ["done"])
        entries = [entry["id"] for card in final["cards"] for entry in card["entries"]]
        self.assertIn(message["id"] + ":you", entries)
        self.assertGreater(final["seq"], feed["seq"])

    def test_old_clients_get_exactly_what_they_got_before(self):
        service, _, _ = self.start()
        feed = service.feed("default")
        self.assertNotIn("messages", feed)
        self.assertEqual(feed["cards"], [])
        # 别人的消息变化不会把只等卡的客户端叫醒。
        started = time.monotonic()
        service.submit("别的对话", conversation="other")
        self.assertEqual(service.feed("default", feed["seq"], 0.3)["cards"], [])
        self.assertGreaterEqual(time.monotonic() - started, 0.25)

    def test_one_cursor_keeps_going_across_a_restart_and_the_epoch_tells_a_new_record(self):
        service, _, xiaoyou = self.start()
        service.get(service.submit("一")["id"], wait=5)
        before = service.feed("default", 0, 0, messages=True)
        again, _, _ = self.start(self.folder)
        after = again.feed("default", before["seq"], 0, messages=True)
        self.assertEqual(after["epoch"], before["epoch"])
        self.assertEqual((after["cards"], after["messages"]), ([], []))
        newer = again.submit("二")
        self.assertGreater(again.feed("default", before["seq"], 0, messages=True)["messages"][0]["seq"],
                           before["seq"])
        again.get(newer["id"], wait=5)

        (xiaoyou._config.state_dir / "cards.json").unlink()
        replaced, _, _ = self.start(self.folder)
        self.assertNotEqual(replaced.feed("default")["epoch"], before["epoch"])

    def test_pages_stay_in_sequence_order_across_cards_and_messages(self):
        old = service_module.FEED_BUDGET_BYTES
        service_module.FEED_BUDGET_BYTES = 1500
        self.addCleanup(setattr, service_module, "FEED_BUDGET_BYTES", old)
        service, _, _ = self.start()
        for index in range(4):
            service.get(service.submit("第 %d 句 %s" % (index, "字" * 200))["id"], wait=5)
        after, seen, pages = 0, [], 0
        while True:
            page = service.feed("default", after, 0, messages=True)
            pages += 1
            items = page["cards"] + page["messages"]
            seen.extend(item["seq"] for item in items)
            if not page.get("more"):
                break
            self.assertEqual(page["seq"], max(item["seq"] for item in items))
            after = page["seq"]
        self.assertGreater(pages, 1)
        self.assertEqual(len(seen), len(set(seen)))


class HttpSyncTests(TempDirCase):
    call = test_runtime.HttpTests.call

    def setUp(self):
        super().setUp()
        self.base, self.config = start_runtime(self, self.folder)

    def test_conflicting_retry_is_409_and_the_feed_carries_messages_on_request(self):
        status, first = self.call("POST", "/v1/messages", {"text": "你好", "client_id": "h1"})
        self.assertEqual(status, 202)
        status, again = self.call("POST", "/v1/messages", {"text": "你好", "client_id": "h1"})
        self.assertEqual((status, again["id"]), (202, first["id"]))
        status, body = self.call("POST", "/v1/messages", {"text": "不一样", "client_id": "h1"})
        self.assertEqual(status, 409)
        self.call("GET", "/v1/messages/%s?wait=5" % first["id"])
        status, feed = self.call("GET", "/v1/feed?after=0&messages=1")
        self.assertEqual((status, [item["id"] for item in feed["messages"]]), (200, [first["id"]]))
        self.assertTrue(feed["epoch"])
        self.assertNotIn("messages", self.call("GET", "/v1/feed?after=0")[1])


class QuickFixTests(TempDirCase):
    def test_a_full_follow_up_queue_says_so_instead_of_pretending(self):
        tasks = Tasks(make_xiaoyou(self.folder, [Scripted("claude")])[1], lambda *args: None)
        task = Task("c1", "default", Scripted("worker", speaks=False), "做事")
        tasks._active[task.card] = task
        results = [tasks.amend(task.card, "after", "补充 %d" % index) for index in range(MAX_PENDING + 1)]
        self.assertEqual(results, ["after"] * MAX_PENDING + ["full"])
        self.assertEqual(len(task.after), MAX_PENDING)

    def test_desktop_changes_reach_clients_within_three_seconds(self):
        self.assertLessEqual(desktop_sync.DISCOVERY_SECONDS + desktop_sync.POLL_SECONDS, 3.0)
        self.assertLessEqual(desktop_sync.MINOR_SECONDS, 3.0)


if __name__ == "__main__":
    unittest.main()
