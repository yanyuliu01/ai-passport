"""收下的话落盘、重启后恢复、按对话去重，以及 feed 用一个游标同时给卡和消息。

对应审查文档 docs/assets/xiaoyou-harness-review.zh_CN.md 里的 R1（收下的话只在内存）、
R2（排着的补充满了被悄悄丢掉）、R3（去重只看 client_id）和 R6（电脑会话传播太慢）。
"""

import http.client
import json
import threading
import time
import unittest
from unittest import mock

import test_runtime
from test_runtime import Scripted, TempDirCase, make_xiaoyou, start_runtime, until

import xiaoyou_runtime.desktop_sync as desktop_sync
import xiaoyou_runtime.service as service_module
from xiaoyou_runtime.service import ConflictError, RESTART_NOTE, Service, UnavailableError
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

    def saved(self, store, message_id, status):
        """等到这句话的这个状态真的写到盘上（写盘在锁外，比内存里晚一点）。"""
        def check():
            try:
                items = self.ledger(store)["messages"]
            except (OSError, ValueError):
                return False
            return any(item["id"] == message_id and item["status"] == status for item in items)
        self.assertTrue(until(check))

    @staticmethod
    def crash(service, xiaoyou):
        """这一台“断电”了：之后它的线程再怎么跑，也写不进同一份记录。"""
        service._ledger.save = lambda *args: None
        xiaoyou.cards._save = lambda: None

    def test_an_accepted_message_is_on_disk_before_the_caller_hears_back(self):
        service, agent, _, store = self.start()
        agent.gate.clear()
        message = service.submit("记下这句 slow", client_id="phone-1")
        saved = {item["id"]: item for item in self.ledger(store)["messages"]}
        self.assertEqual(saved[message["id"]]["text"], "记下这句 slow")
        self.assertEqual(self.ledger(store)["dedupe"][0]["client_id"], "phone-1")

    def test_after_a_restart_queued_text_goes_on_and_unfinished_work_is_not_redone(self):
        service, agent, first, store = self.start()
        agent.gate.clear()
        running = service.submit("第一句 slow", client_id="a")
        queued = service.submit("第二句", client_id="b")
        self.saved(store, running["id"], "running")
        self.assertEqual(service.get(queued["id"])["status"], "queued")

        # 第一台卡在模型里时断电，用同一份记录起一台新的：等于 Runtime 被重启。
        self.crash(service, first)
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

    def test_a_sentence_said_inside_a_task_keeps_its_restart_note_on_that_task(self):
        service, agent, first, store = self.start()
        done = service.get(service.submit("先开一件事")["id"], wait=5)
        agent.gate.clear()
        inside = service.submit("在里面补一句 slow", card=done["card"], pin=True)
        self.saved(store, inside["id"], "running")
        self.crash(service, first)
        again, _, xiaoyou, _ = self.start(self.folder)
        lost = again.get(inside["id"])
        self.assertEqual((lost["status"], lost["card"]), ("failed", done["card"]))
        texts = [entry["text"] for entry in xiaoyou.cards.get(done["card"])["entries"]]
        self.assertEqual(texts[-2:], ["在里面补一句 slow", RESTART_NOTE])
        self.assertEqual(len([card for card in xiaoyou.cards.recent("default")
                              if card["state"] == "failed"]), 0)

    def test_a_reply_already_on_its_card_is_not_reported_lost_after_a_crash(self):
        service, _, first, store = self.start()
        # 记录只写到“收下”为止：回复已经上了卡，消息记录还停在 queued。
        original = service._ledger.save
        calls = []

        def only_first(*args):
            if not calls:
                calls.append(1)
                original(*args)
        service._ledger.save = only_first
        message = service.submit("答完了才断电")
        done = service.get(message["id"], wait=5)
        self.assertEqual(done["status"], "done")
        self.crash(service, first)
        again, _, xiaoyou, _ = self.start(self.folder)
        recovered = again.get(message["id"])
        self.assertEqual((recovered["status"], recovered["reply"], recovered["card"]),
                         ("done", "re:答完了才断电", done["card"]))
        self.assertEqual([card["state"] for card in xiaoyou.cards.recent("default")], ["done"])

    def test_a_message_that_cannot_be_written_down_is_not_accepted(self):
        service, agent, _, _ = self.start()
        with mock.patch.object(service._ledger, "save", side_effect=OSError("disk full")):
            with self.assertRaises(UnavailableError):
                service.submit("写不下", client_id="w1")
        self.assertEqual(agent.started, [])
        # 磁盘好了以后用同一个 client_id 再发，正常收下。
        retry = service.submit("写不下", client_id="w1")
        self.assertEqual(service.get(retry["id"], wait=5)["status"], "done")

    def test_a_retry_during_a_failing_write_is_never_told_it_was_accepted(self):
        service, agent, _, _ = self.start()
        writing = threading.Event()
        original = service._ledger.save

        def slow_failure(*args):
            writing.set()
            time.sleep(0.3)
            raise OSError("disk full")
        service._ledger.save = slow_failure
        outcome = {}

        def first():
            try:
                outcome["first"] = service.submit("一句", client_id="r1")["status"]
            except UnavailableError:
                outcome["first"] = 503
        thread = threading.Thread(target=first)
        thread.start()
        writing.wait(5)
        service._ledger.save = original  # 磁盘好了；重试正好赶在第一次写的时候
        retry = service.submit("一句", client_id="r1")
        thread.join(5)
        self.assertEqual(outcome["first"], 503)
        # 重试等到第一次的结果（撤回）才处理，所以是一句新收下、真的写下去的话。
        self.assertIsNotNone(service.get(retry["id"]))
        self.assertEqual(service.get(retry["id"], wait=5)["status"], "done")
        self.assertEqual(agent.started, ["一句"])

    def test_sentences_run_in_the_order_they_were_accepted(self):
        service, agent, _, _ = self.start()
        original = service._flush
        delayed = threading.Event()

        def slow_once(*args, **kwargs):
            # 第一句收下后、开始写盘前停一下：第二句正好在这时候来。
            if not delayed.is_set():
                delayed.set()
                time.sleep(0.2)
            original(*args, **kwargs)
        service._flush = slow_once
        ids = {}
        thread = threading.Thread(target=lambda: ids.update(a=service.submit("A")["id"]))
        thread.start()
        delayed.wait(5)
        ids["b"] = service.submit("B")["id"]
        thread.join(5)
        service.get(ids["b"], wait=5)
        service.get(ids["a"], wait=5)
        self.assertEqual(agent.started, ["A", "B"])
        self.assertLess(service.get(ids["a"])["created_at"], service.get(ids["b"])["created_at"])

    def test_the_sequence_never_goes_back_even_when_streaming_was_never_written(self):
        service, agent, first, store = self.start()
        agent.gate.clear()
        message = service.submit("一直在说 slow")
        self.saved(store, message["id"], "running")
        for index in range(1500):  # 超过一段预留的号
            service._report(message["id"], "partial", None, "第 %d 段" % index)
        held = service.feed("default", 0, 0, messages=True)["seq"]
        self.crash(service, first)
        again, _, _, _ = self.start(self.folder)
        restarted = again.feed("default", 0, 0, messages=True)
        self.assertGreater(restarted["seq"], held)
        self.assertEqual(restarted["epoch"], service.feed("default")["epoch"])
        # 重启后新出现的东西（没做完的那张卡）序号都在客户端手里那个之后，拿得到。
        later = again.feed("default", held, 0, messages=True)
        self.assertIn(message["id"], [item["id"] for item in later["messages"]])
        self.assertTrue(later["cards"])

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
        self.assertTrue(until(lambda: service.get(message["id"])["status"] == "running"))
        feed = service.feed("default", base["seq"], 0, messages=True)
        self.assertEqual([(item["id"], item["text"]) for item in feed["messages"]],
                         [(message["id"], "还在想 slow")])
        self.assertEqual(feed["cards"], [])  # 卡要等回复出来才有

        # 生成中的回复也到得了另一端，而且只等到真的变了才返回。
        seen = {}
        waiter = threading.Thread(target=lambda: seen.update(
            service.feed("default", feed["seq"], 5, messages=True)))
        waiter.start()
        service._report(message["id"], "partial", None, "想到一半")
        waiter.join(3)
        self.assertFalse(waiter.is_alive())
        self.assertEqual([item["partial"] for item in seen["messages"]], ["想到一半"])

        agent.gate.set()
        service.get(message["id"], wait=5)
        self.assertTrue(until(lambda: service.feed("default", feed["seq"], 0, messages=True)["cards"]))
        final = service.feed("default", feed["seq"], 0, messages=True)
        self.assertEqual([item["status"] for item in final["messages"]], ["done"])
        entries = [entry["id"] for card in final["cards"] for entry in card["entries"]]
        self.assertIn(message["id"] + ":you", entries)
        self.assertGreater(final["seq"], feed["seq"])

    def test_old_clients_get_exactly_what_they_got_before(self):
        service, _, xiaoyou = self.start()
        feed = service.feed("default")
        self.assertNotIn("messages", feed)
        self.assertEqual(feed["cards"], [])
        service.get(service.submit("一句")["id"], wait=5)
        self.assertTrue(until(lambda: service.feed("default", 0)["cards"]))
        old = xiaoyou.cards.changed("default", 0, include_desktop=True,
                                    budget=service_module.FEED_BUDGET_BYTES)
        new = service.feed("default", 0)
        self.assertEqual((new["seq"], new["cards"]), (old["seq"], old["cards"]))
        feed = new
        # 别人的消息变化不会把只等卡的客户端叫醒。
        started = time.monotonic()
        service.submit("别的对话", conversation="other")
        self.assertEqual(service.feed("default", feed["seq"], 0.3)["cards"], [])
        self.assertGreaterEqual(time.monotonic() - started, 0.25)

    def test_one_cursor_keeps_going_across_a_restart_and_the_epoch_tells_a_new_record(self):
        service, _, xiaoyou = self.start()
        first = service.submit("一")
        service.get(first["id"], wait=5)
        ledger = xiaoyou._config.state_dir / "messages.json"
        self.assertTrue(until(lambda: '"status": "done"' in ledger.read_text(encoding="utf-8")))
        before = service.feed("default", 0, 0, messages=True)
        again, _, _ = self.start(self.folder)
        after = again.feed("default", before["seq"], 0, messages=True)
        self.assertEqual(after["epoch"], before["epoch"])
        self.assertEqual((after["cards"], after["messages"]), ([], []))
        newer = again.submit("二")
        again.get(newer["id"], wait=5)
        later = again.feed("default", before["seq"], 0, messages=True)
        self.assertEqual([item["id"] for item in later["messages"]], [newer["id"]])
        self.assertGreater(later["messages"][0]["seq"], before["seq"])

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


class SequenceReservationTests(TempDirCase):
    def test_a_failed_write_does_not_count_as_reserving_numbers(self):
        from unittest import mock as _mock
        import xiaoyou_runtime.cards as cards_module
        cards = cards_module.Cards(self.folder / "cards.json")
        on_disk = json.loads((self.folder / "cards.json").read_text(encoding="utf-8"))["seq"]
        with _mock.patch.object(cards_module, "_write", side_effect=OSError("disk full")):
            for _ in range(cards_module.SEQ_BLOCK + 5):
                cards.bump()  # 写不下去也不抛给取号的人
        self.assertLessEqual(cards._reserved, on_disk)
        cards.bump()  # 磁盘好了：下一次取号补上
        saved = json.loads((self.folder / "cards.json").read_text(encoding="utf-8"))["seq"]
        self.assertGreater(saved, cards.seq)


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

    def test_a_409_is_the_only_answer_on_its_connection(self):
        import socket
        host, port = self.base.split("//")[1].split(":")
        self.assertEqual(self.call("POST", "/v1/messages", {"text": "一", "client_id": "k1"})[0], 202)
        body = json.dumps({"text": "二", "client_id": "k1"}).encode("utf-8")
        request = ("POST /v1/messages HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer %s\r\n"
                   "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n"
                   % (test_runtime.TOKEN, len(body))).encode("ascii") + body
        with socket.create_connection((host, int(port)), timeout=10) as connection:
            connection.sendall(request)
            received = b""
            while True:  # 出错后服务端会关掉这条连接：一直读到关
                chunk = connection.recv(65536)
                if not chunk:
                    break
                received += chunk
        # 只有一个回答（以前 409 后面还跟着一个多余的 404，会被当成下一个请求的回答）。
        self.assertEqual(received.count(b"HTTP/1."), 1)
        self.assertTrue(received.startswith(b"HTTP/1.1 409") or received.startswith(b"HTTP/1.0 409"))


class QuickFixTests(TempDirCase):
    def test_a_full_follow_up_queue_says_so_instead_of_pretending(self):
        tasks = Tasks(make_xiaoyou(self.folder, [Scripted("claude")])[1], lambda *args: None)
        task = Task("c1", "default", Scripted("worker", speaks=False), "做事")
        tasks._active[task.card] = task
        results = [tasks.amend(task.card, "after", "补充 %d" % index) for index in range(MAX_PENDING + 1)]
        self.assertEqual(results, ["after"] * MAX_PENDING + ["full"])
        self.assertEqual(len(task.after), MAX_PENDING)

    def test_desktop_tool_changes_are_published_within_three_seconds(self):
        from pathlib import Path
        from xiaoyou_runtime.cards import Cards
        cards = Cards(self.folder / "observed.json")
        sync = desktop_sync.DesktopSync(cards, self.folder, home=self.folder, clock=lambda: 100)
        session = desktop_sync.Session("codex", "s1", cwd=str(self.folder), status="idle",
                                       observed_at=100)
        sync._publish(session, 100)
        session.transition("running", 101)
        sync._publish(session, 101)
        seq = cards.get(session.card)["seq"]
        session.tool = "read_file"
        session.observed_at = 102
        sync._publish(session, 102)
        sync._publish(session, 104)  # 工具变化后 3 秒内一定发出去
        self.assertGreater(cards.get(session.card)["seq"], seq)
        # 新开的会话：找一次 + 读一次，加起来不超过 3 秒。
        self.assertLessEqual(desktop_sync.DISCOVERY_SECONDS + desktop_sync.POLL_SECONDS, 3.0)


if __name__ == "__main__":
    unittest.main()
