#!/usr/bin/env python3
"""Desktop observation fixtures; no model calls or private local logs."""

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xiaoyu_runtime.helpers.factory import create
from xiaoyu_runtime.world.cards import Cards
from xiaoyu_runtime.config import load
from xiaoyu_runtime.events.desktop_hook import record
from xiaoyu_runtime.events.desktop_sync import DesktopSync, MAX_LINE_BYTES, STALE_SECONDS
from xiaoyu_runtime.api.service import RequestError, Service
from xiaoyu_runtime.world.store import Store
from xiaoyu_runtime.loop.xiaoyu import Xiaoyu


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 2000000000.0
        self.state = self.root / "state"
        self.cards = Cards(self.state / "cards.json")
        self.owned = set()
        self.sync = DesktopSync(self.cards, self.state, home=self.root,
                                clock=lambda: self.now, owned=lambda: self.owned)

    def stamp(self, seconds=None):
        return datetime.fromtimestamp(self.now if seconds is None else seconds,
                                      timezone.utc).isoformat()

    def append(self, path, events):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            for event in events:
                stream.write(json.dumps(dict(timestamp=self.stamp(), **event)) + "\n")
        os.utime(path, (self.now, self.now))

    def codex(self, sid="s-1", cwd="/project/a", source="vscode"):
        path = self.root / ".codex/sessions/2033/rollout-" / (sid + ".jsonl")
        path = path.parent / ("rollout-" + sid + ".jsonl")
        self.append(path, [{"type": "session_meta", "payload": {
            "id": sid, "cwd": cwd, "source": source, "originator": "Codex Desktop"}}])
        return path

    def cevent(self, kind, **payload):
        return {"type": "event_msg", "payload": dict(type=kind, **payload)}

    def message(self, role, text, **fields):
        return {"type": "response_item", "payload": dict(type="message", role=role,
                content=[{"type": "input_text" if role == "user" else "output_text", "text": text}],
                **fields)}

    def hook(self, event):
        path = record(self.state, event)
        os.utime(path, (self.now, self.now))
        return path

    def observed(self):
        return [card for card in self.cards.recent(None, 1000) if card.get("monitoring_only")]

    def test_codex_lifecycle_multiple_sessions_and_privacy(self):
        path = self.codex()
        other = self.codex("s-2", "/project/b")
        self.append(path, [self.cevent("task_started", turn_id="turn1"),
                           {"type": "response_item", "payload": {"type": "function_call",
                            "name": "exec", "arguments": "SECRET command"}},
                           {"type": "response_item", "payload": {"type": "message", "role": "user",
                            "content": [{"type": "input_text", "text": "检查显示。password=SECRET"}]}}])
        self.append(other, [self.cevent("task_started"), self.cevent("task_complete")])
        self.sync.poll()
        cards = {card["session_id"]: card for card in self.observed()}
        self.assertEqual(cards["s-1"]["state"], "working")
        self.assertEqual(cards["s-1"]["progress"], ["正在运行", "exec"])
        self.assertEqual(cards["s-2"]["desktop_status"], "idle")
        self.assertNotIn("SECRET", json.dumps(cards))
        before = self.cards.changed("phone", include_desktop=True)["seq"]
        self.sync.poll()
        self.assertEqual(before, self.cards.changed("phone", include_desktop=True)["seq"])
        self.now += 1
        self.append(path, [self.cevent("turn_aborted")])
        self.sync.poll()
        self.assertEqual(self.cards.get(cards["s-1"]["id"])["state"], "cancelled")

    def test_tool_churn_is_batched_but_new_messages_and_states_are_not(self):
        path = self.codex()
        self.append(path, [self.cevent("task_started", turn_id="turn1"), self.message("user", "整理一下")])
        self.sync.poll()
        card = self.observed()[0]["id"]
        seq = self.cards.changed("phone", include_desktop=True)["seq"]
        # Another tool call a second later only moves progress: held back for a while.
        self.now += 1
        self.append(path, [{"type": "response_item", "payload": {"type": "function_call",
                            "name": "read", "arguments": "{}"}}])
        self.sync.poll()
        self.assertEqual(seq, self.cards.changed("phone", include_desktop=True)["seq"])
        self.now += 10
        self.sync.poll()
        self.assertIn("read", self.cards.get(card)["progress"])
        # A visible reply and the end of the turn go out at once.
        seq = self.cards.changed("phone", include_desktop=True)["seq"]
        self.now += 1
        self.append(path, [self.message("assistant", "好了"), self.cevent("task_complete")])
        self.sync.poll()
        self.assertGreater(self.cards.changed("phone", include_desktop=True)["seq"], seq)
        self.assertEqual(self.cards.get(card)["desktop_status"], "idle")

    def test_codex_sidebar_rename_wakes_feed_without_transcript_changes(self):
        path = self.codex()
        self.append(path, [self.message("user", "original request"),
                           self.cevent("task_started")])
        db_path = self.root / ".codex/state_5.sqlite"
        with closing(sqlite3.connect(db_path)) as db, db:
            db.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, name TEXT, title TEXT)")
            db.execute("INSERT INTO threads VALUES (?, ?, ?)",
                       ("s-1", "Sidebar task name with more than twenty-four characters", "old prompt"))
        self.sync.poll()
        card = self.observed()[0]
        self.assertEqual(card["title_source"], "upstream")
        self.assertGreater(len(card["title"]), 24)
        self.assertNotEqual(card["title"], "old prompt")
        original_stat = path.stat()
        before = self.cards.changed("phone", include_desktop=True)["seq"]
        with closing(sqlite3.connect(db_path)) as db, db:
            db.execute("UPDATE threads SET name=? WHERE id=?", ("改名后的任务", "s-1"))
        self.sync.poll()
        changed = self.cards.changed("phone", before, include_desktop=True)
        self.assertEqual([item["id"] for item in changed["cards"]], [card["id"]])
        self.assertEqual(changed["cards"][0]["title"], "改名后的任务")
        self.assertEqual(path.stat().st_mtime_ns, original_stat.st_mtime_ns)
        self.now += 1
        self.append(path, [self.message("user", "a later follow-up")])
        self.sync.poll()
        self.assertEqual(self.cards.get(card["id"])["title"], "改名后的任务")
        with closing(sqlite3.connect(db_path)) as db, db:
            db.execute("UPDATE threads SET name=NULL WHERE id=?", ("s-1",))
        self.sync.poll()
        fallback = self.cards.get(card["id"])
        self.assertEqual((fallback["title"], fallback["title_source"]),
                         ("original request", "request"))

    def test_claude_custom_title_and_index_rename_use_same_card(self):
        path = self.root / ".claude/projects/project/s-cc.jsonl"
        self.append(path, [{"sessionId": "s-cc", "cwd": "/project/a", "type": "user",
                            "message": {"content": "the visible user request"}},
                           {"sessionId": "s-cc", "type": "custom-title", "customTitle": "Claude title"}])
        self.sync.poll()
        card = self.observed()[0]
        self.assertEqual((card["title"], card["title_source"]), ("Claude title", "upstream"))
        self.append(path, [{"sessionId": "s-cc", "type": "custom-title", "customTitle": "Renamed Claude title"}])
        self.sync.poll()
        self.assertEqual(self.cards.get(card["id"])["title"], "Renamed Claude title")
        self.assertEqual(len(self.observed()), 1)
        other = self.root / ".claude/projects/project/s-index.jsonl"
        self.append(other, [{"sessionId": "s-index", "cwd": "/project/a", "type": "user",
                             "message": {"content": "some request"}}])
        index = path.parent / "sessions-index.json"
        index.write_text(json.dumps({"entries": [{"sessionId": "s-index", "summary": "Index title"}]}))
        self.now += 11
        self.sync.poll()
        second = next(item for item in self.observed() if item["session_id"] == "s-index")
        index.write_text(json.dumps({"entries": [{"sessionId": "s-index", "summary": "Index renamed"}]}))
        self.sync.poll()
        self.assertEqual(self.cards.get(second["id"])["title"], "Index renamed")

    def test_title_redaction_and_restore_with_hook_without_content(self):
        path = self.codex()
        self.append(path, [self.message("user", "retained request"), self.cevent("task_complete")])
        index = self.root / ".codex/session_index.jsonl"
        index.write_text(json.dumps({"id": "s-1", "thread_name": "Task password=EXAMPLESECRET"}) + "\n")
        self.sync.poll()
        card = self.observed()[0]
        self.assertEqual(card["title_source"], "upstream")
        self.assertNotIn("EXAMPLESECRET", json.dumps(card))
        reloaded = Cards(self.state / "cards.json")
        self.sync = DesktopSync(reloaded, self.state, home=self.root, clock=lambda: self.now)
        path.unlink()
        index.unlink()
        self.now += 1
        self.hook({"agent": "codex", "session_id": "s-1", "cwd": "/project/a",
                   "status": "waiting", "observed_at": self.now})
        self.sync.poll()
        restored = reloaded.get(card["id"])
        self.assertEqual((restored["title"], restored["title_source"]), ("retained request", "request"))
        self.assertEqual([(entry["role"], entry["text"], entry["at"])
                          for entry in restored["entries"]],
                         [(entry["role"], entry["text"], entry["at"])
                          for entry in card["entries"]])

    def test_fallback_title_keeps_topic_when_window_rotates_and_skips_task_notifications(self):
        path = self.codex()
        self.append(path, [self.message("user", "<task-notification><task-id>system</task-id></task-notification>"),
                           self.message("user", "修复图片上传"), self.cevent("task_started")])
        self.sync.poll()
        card = self.observed()[0]
        self.assertEqual('修复图片上传', card['title'])
        self.now += 1
        self.append(path, [self.message('user', '继续 %s' % i) for i in range(25)])
        self.sync.poll()
        current = self.cards.get(card['id'])
        self.assertEqual('修复图片上传', current['title'])
        self.assertFalse(any(e['text'] == '修复图片上传' for e in current['entries']))
        self.assertFalse(any('task-notification' in e['text'] for e in current['entries']))

    def test_old_technical_fallback_is_repaired_without_changing_status_or_history(self):
        card = self.sync.writer.observed_open('__desktop__', 'codex workdir abcdef', 'unknown', 'codex', title_source='fallback')
        card = self.sync.writer.observed(card['id'], source='desktop', monitoring_only=True,
            session_id='old-session', project='workdir', observed_at=self.now - 864000,
            stale=True, desktop_cwd='/project/workdir')
        reloaded = Cards(self.state / 'cards.json')
        self.assertEqual('/project/workdir', reloaded.get(card['id'])['desktop_cwd'])
        observer = DesktopSync(reloaded, self.state, home=self.root, clock=lambda: self.now)
        observer.poll()
        changed = reloaded.get(card['id'])
        self.assertEqual(('未命名 Codex 会话', 'fallback'), (changed['title'], changed['title_source']))
        self.assertEqual(('unknown', True, self.now - 864000),
                         (changed['state'], changed['stale'], changed['observed_at']))
        self.assertEqual(card['entries'], changed['entries'])

    def test_retained_history_title_renames_without_reopening_transcript(self):
        path = self.codex()
        self.append(path, [self.message("user", "old visible request"), self.cevent("task_complete")])
        index = self.root / ".codex/session_index.jsonl"
        index.write_text(json.dumps({"id": "s-1", "thread_name": "Old sidebar name"}) + "\n")
        self.sync.poll()
        original = self.observed()[0]
        reloaded = Cards(self.state / "cards.json")
        self.sync = DesktopSync(reloaded, self.state, home=self.root, clock=lambda: self.now)
        path.unlink()
        index.write_text(json.dumps({"id": "s-1", "thread_name": "Renamed history"}) + "\n")
        self.sync.poll()
        card = reloaded.get(original["id"])
        self.assertEqual((card["title"], card["title_source"]), ("Renamed history", "upstream"))
        self.assertEqual((card["state"], card["desktop_status"], card["observed_at"]),
                         (original["state"], original["desktop_status"], original["observed_at"]))
        self.assertEqual(card["entries"][0]["text"], "old visible request")
        before = card["seq"]
        self.sync.poll()
        self.assertEqual(reloaded.get(card["id"])["seq"], before)

    def test_claude_desktop_terminal_retry_and_interrupt(self):
        path = self.root / ".claude/projects/project/s-cc.jsonl"
        base = {"sessionId": "s-cc", "cwd": "/project/a", "entrypoint": "claude-desktop"}
        self.append(path, [dict(base, type="user", message={"content": "检查电量。password=SECRET"}),
                           dict(base, type="assistant", message={"model": "claude-test",
                            "stop_reason": "tool_use", "content": [{"type": "tool_use",
                            "name": "Bash", "input": {"command": "SECRET command"}}]}),
                           dict(base, type="system", subtype="api_error", retryAttempt=1)])
        self.sync.poll()
        card = self.observed()[0]
        self.assertEqual(card["state"], "working")
        self.now += 1
        self.append(path, [dict(base, type="assistant", message={"stop_reason": "end_turn",
                           "content": [{"type": "text", "text": "电量正常。password=SECRET"}]})])
        self.sync.poll()
        self.assertEqual(self.cards.get(card["id"])["desktop_status"], "idle")
        self.now += 1
        self.append(path, [dict(base, type="user", message={"content": [
            {"type": "text", "text": "[Request interrupted by user]"}]})])
        self.sync.poll()
        result = self.cards.get(card["id"])
        self.assertEqual(result["state"], "cancelled")
        self.assertNotIn("SECRET", json.dumps(result))

    def test_client_window_presence_is_published_and_expires(self):
        from xiaoyu_runtime.events.desktop_sync import CLIENT_OPEN_SECONDS
        self.codex()
        event = {"agent": "codex", "session_id": "s-1", "cwd": "/project/a", "status": "idle",
                 "observed_at": self.now, "client": "open", "client_at": self.now}
        self.hook(event)
        self.sync.poll()
        card = self.observed()[0]
        self.assertTrue(card["client_open"])
        self.now += 1
        self.hook(dict(event, observed_at=self.now, client="closed", client_at=self.now))
        self.sync.poll()
        self.assertFalse(self.cards.get(card["id"])["client_open"])
        self.now += 1
        self.hook(dict(event, observed_at=self.now, client_at=self.now))
        self.sync.poll()
        self.assertTrue(self.cards.get(card["id"])["client_open"])
        # A window that crashed never says goodbye: long silence counts as closed.
        self.now += CLIENT_OPEN_SECONDS + 1
        self.sync.poll()
        self.assertFalse(self.cards.get(card["id"])["client_open"])

    def test_stale_is_unknown_and_restart_reuses_card(self):
        path = self.codex()
        self.append(path, [self.cevent("task_started")])
        self.sync.poll()
        cid = self.observed()[0]["id"]
        reloaded = Cards(self.state / "cards.json")
        self.assertEqual(reloaded.get(cid)["state"], "unknown")
        self.cards = reloaded
        self.sync = DesktopSync(reloaded, self.state, home=self.root, clock=lambda: self.now)
        self.sync.poll()
        self.assertEqual(self.observed()[0]["id"], cid)
        self.now += STALE_SECONDS + 1
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "unknown")
        self.assertIsNone(self.cards.get(cid)["finished_at"])
        self.append(path, [self.cevent("task_complete")])
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["desktop_status"], "idle")

    def test_unreadable_source_is_unknown_immediately(self):
        path = self.codex()
        self.append(path, [self.cevent("task_started")])
        self.sync.poll()
        cid = self.observed()[0]["id"]
        with mock.patch.object(self.sync, "_read", side_effect=PermissionError):
            self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "unknown")
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "working")

    def test_partial_record_rotation_and_malformed_payload(self):
        path = self.codex()
        self.sync.poll()
        cid = self.observed()[0]["id"]
        event = json.dumps(dict(timestamp=self.stamp(), **self.cevent("task_started")))
        with path.open("a") as stream:
            stream.write(event[:20])
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "unknown")
        with path.open("a") as stream:
            stream.write(event[20:] + "\n")
        os.utime(path, (self.now, self.now))
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "working")
        self.append(path, [self.cevent([]), self.cevent("task_complete")])
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "done")
        path.unlink()
        self.codex()
        self.append(path, [self.cevent("task_started")])
        self.sync.poll()
        self.assertEqual(self.observed()[0]["id"], cid)

    def test_hooks_waiting_order_and_stale_metadata(self):
        path = self.codex()
        self.append(path, [self.cevent("task_started")])
        self.sync.poll()
        cid = self.observed()[0]["id"]
        self.now += 1
        hook = {"agent": "codex", "session_id": "s-1", "cwd": "/project/a",
                "status": "waiting", "observed_at": self.now, "model": "new-model"}
        self.hook(hook)
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "waiting")
        self.assertIsNone(self.cards.get(cid)["approval"])
        self.hook(dict(hook, observed_at=self.now - 10, cwd="/WRONG", model="old"))
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["project"], "a")
        self.assertEqual(self.cards.get(cid)["model"], "new-model")
        self.now += 1
        self.append(path, [self.cevent("task_started", turn_id="next")])
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "working")

    def test_project_filter_sidechains_and_runtime_ownership(self):
        self.sync.projects = {"/project/a"}
        for sid, cwd, source in [("yes", "/project/a", "vscode"),
                                  ("other", "/project/b", "vscode"),
                                  ("child", "/project/a", {"subagent": "spawn"}),
                                  ("owned", "/project/a", "cli")]:
            path = self.codex(sid, cwd, source)
            self.append(path, [self.cevent("task_started")])
        self.owned.add("owned")
        self.append(self.root / ".claude/projects/a/subagents/child.jsonl", [
            {"sessionId": "cc-child", "cwd": "/project/a", "type": "user",
             "message": {"content": "sidechain"}}])
        self.sync.poll()
        self.assertEqual([card["session_id"] for card in self.observed()], ["yes"])

    def test_recap_context_is_not_imported_as_a_human_message(self):
        from xiaoyu_runtime.loop.project_brief import recap_text
        recap = recap_text({"when": self.now, "turns": [{"role": "user", "text": "private recap"}]}, now=self.now)
        path = self.codex()
        self.append(path, [self.message("user", recap), self.message("user", "real request")])
        self.sync.poll()
        card = self.observed()[0]
        self.assertEqual([e["text"] for e in card["entries"]], ["real request"])
        self.assertEqual(card["changed_by"]["source"], "desktop")
        self.assertEqual(self.cards.recent("default"), [])
        seq = self.cards.seq
        self.sync.poll()
        self.assertEqual(self.cards.seq, seq)

    def test_global_phone_feed_does_not_enter_model_context(self):
        path = self.codex()
        self.append(path, [self.cevent("task_started")])
        self.sync.poll()
        self.assertEqual(self.cards.recent("phone"), [])
        self.assertEqual(len(self.cards.changed("phone", include_desktop=True)["cards"]), 1)
        seq = self.cards.changed("phone", include_desktop=True)["seq"]
        seen = []
        thread = threading.Thread(target=lambda: seen.append(
            self.cards.changed("phone", seq, 2, include_desktop=True)))
        thread.start()
        self.now += 1
        self.append(path, [self.cevent("task_complete")])
        self.sync.poll()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(seen[0]["cards"][0]["desktop_status"], "idle")

    def test_oversized_record_does_not_stall_following_events(self):
        path = self.codex()
        with path.open("a") as stream:
            stream.write("x" * (MAX_LINE_BYTES + 100) + "\n")
        self.append(path, [self.cevent("task_started"), self.cevent("task_complete")])
        self.sync.poll()
        self.assertEqual(self.observed()[0]["desktop_status"], "idle")

    def test_hook_discovery_uses_newest_not_old_directory_order(self):
        for index in range(130):
            path = record(self.state, {"agent": "codex", "session_id": "old-%d" % index,
                         "cwd": "/old", "status": "idle", "observed_at": self.now - 100})
            os.utime(path, (self.now - 100, self.now - 100))
        path = record(self.state, {"agent": "claude", "session_id": "latest",
                     "cwd": "/new", "status": "running", "observed_at": self.now})
        os.utime(path, (self.now, self.now))
        self.sync.poll()
        self.assertIn("latest", [card["session_id"] for card in self.observed()])
        seq = self.cards.changed("phone", include_desktop=True)["seq"]
        self.sync.poll()
        self.assertEqual(self.cards.changed("phone", include_desktop=True)["seq"], seq)

    def test_service_refuses_desktop_control(self):
        config_path = self.root / "config.json"
        (self.root / "persona.txt").write_text("Test persona")
        config_path.write_text(json.dumps({"server": {"token": "test-token-long-enough"},
            "agents": {"echo": {"type": "echo"}}, "xiaoyou": {"default_agent": "echo"}}))
        config = load(config_path)
        store = Store(config.state_dir)
        xiaoyu = Xiaoyu(config, [create(spec) for spec in config.agents], store)
        service = Service(xiaoyu, store)
        self.addCleanup(service.close)
        sync = DesktopSync(xiaoyu.cards, config.state_dir, home=self.root, clock=lambda: self.now)
        path = self.codex()
        self.append(path, [self.cevent("task_started")])
        sync.poll()
        card = service.cards("phone")[0]
        # Talking about a running desktop session goes to Xiaoyu; it never joins that session.
        message = service.get(service.submit("continue", "phone", card=card["id"], pin=True)["id"],
                              wait=5)
        self.assertEqual("done", message["status"])
        self.assertNotEqual(card["id"], message["card"])
        with self.assertRaises(RequestError):
            service.cancel(card["id"])
        self.assertEqual(service.feed("phone")["cards"][0]["state"], "working")
        # Xiaoyu's own conversation session only; the desktop session is not taken over.
        self.assertNotIn(card["session_id"], store.session_ids())

    def test_displaced_active_session_cannot_keep_running_badge(self):
        path = self.codex("older-running")
        self.append(path, [self.cevent("task_started")])
        self.sync.poll()
        cid = self.observed()[0]["id"]
        self.now += 20
        for index in range(32):
            path = self.codex("newer-%d" % index)
            self.append(path, [self.cevent("task_started"), self.cevent("task_complete")])
        self.sync.poll()
        self.assertEqual(self.cards.get(cid)["state"], "unknown")
        seq = self.cards.changed("phone", include_desktop=True)["seq"]
        self.sync.poll()
        self.assertEqual(seq, self.cards.changed("phone", include_desktop=True)["seq"])

    def test_visible_content_and_progress_exclude_internal_records(self):
        path = self.codex()
        self.append(path, [self.cevent("task_started"),
            self.message("user", "# AGENTS.md instructions for /project/a\n<INSTRUCTIONS>internal rules</INSTRUCTIONS>"),
            self.message("user", "<in-app-browser-context>hidden URL</in-app-browser-context>\n## My request:\n同步任务内容"),
            self.message("developer", "hidden developer instructions"),
            self.message("assistant", "hidden analysis", phase="analysis"),
            self.message("assistant", "hidden channel", phase="commentary", channel="analysis"),
            self.message("assistant", "hidden future phase", phase="internal_notes"),
            self.message("assistant", "正在检查同步链路。", phase="commentary"),
            {"type": "response_item", "payload": {"type": "function_call_output", "output": "hidden tool output"}},
            self.message("assistant", "已把需求和回复接入同步。", phase="final_answer"),
            self.cevent("task_complete")])
        self.sync.poll()
        card = self.observed()[0]
        self.assertEqual(card["title"], "同步任务内容")
        self.assertEqual([entry["role"] for entry in card["entries"]], ["you", "xiaoyou", "xiaoyou"])
        self.assertEqual(card["brief"], "已把需求和回复接入同步。")
        self.assertIn("正在检查同步链路。", card["progress"])
        self.assertNotIn("hidden", json.dumps(card))
        self.assertNotIn("internal rules", json.dumps(card))
        self.assertEqual(card["content_scope"], "recent")
        seq = self.cards.changed("phone", include_desktop=True)["seq"]
        self.sync.poll()
        self.assertEqual(seq, self.cards.changed("phone", include_desktop=True)["seq"])

    def test_new_request_does_not_inherit_old_reply_as_its_brief(self):
        path = self.codex()
        self.append(path, [self.cevent("task_started"), self.message("user", "第一个需求"),
                           self.message("assistant", "第一个需求的结果", phase="final_answer"),
                           self.cevent("task_complete")])
        self.sync.poll()
        self.now += 1
        self.append(path, [self.cevent("task_started", turn_id="new"),
                           self.message("user", "第二个需求")])
        self.sync.poll()
        card = self.observed()[0]
        self.assertEqual(card["title"], "第一个需求")  # The session's topic is stable; its brief is per turn.
        self.assertNotIn("第一个需求的结果", card["brief"])
        self.assertEqual(len(card["entries"]), 3)

    def test_claude_multipart_id_does_not_erase_or_duplicate_visible_reply(self):
        path = self.root / ".claude/projects/a/main.jsonl"
        base = {"sessionId": "cc-main", "cwd": "/project/a"}
        self.append(path, [dict(base, type="user", uuid="u1", message={"content": "检查按键"}),
            dict(base, type="assistant", uuid="a-thinking", apiBlockIndex=0,
                 message={"id": "response1", "content": [{"type": "thinking", "thinking": "hidden thinking"}]}),
            dict(base, type="assistant", uuid="a-text", apiBlockIndex=1,
                 message={"id": "response1", "content": [{"type": "text", "text": "正在读取按键状态"}]}),
            dict(base, type="assistant", uuid="a-tool", apiBlockIndex=2,
                 message={"id": "response1", "stop_reason": "tool_use", "content": [{"type": "tool_use", "name": "Read"}]}),
            dict(base, type="user", uuid="tool-result", toolUseResult="hidden tool output",
                 message={"content": [{"type": "tool_result", "content": "hidden tool output"}]}),
            dict(base, type="user", isCompactSummary=True, message={"content": "hidden compact summary"})])
        self.sync.poll()
        card = self.observed()[0]
        self.assertEqual([entry["text"] for entry in card["entries"]], ["检查按键", "正在读取按键状态"])
        self.assertNotIn("hidden", json.dumps(card))
        seq = self.cards.changed("phone", include_desktop=True)["seq"]
        self.sync.poll()
        self.assertEqual(seq, self.cards.changed("phone", include_desktop=True)["seq"])

    def test_malformed_claude_block_identity_cannot_export_raw_metadata(self):
        path = self.root / ".claude/projects/a/main.jsonl"
        self.append(path, [{"sessionId": "cc-main", "cwd": "/project/a", "type": "assistant",
            "apiBlockIndex": {"password": "hidden identity secret"},
            "message": {"id": "hidden identity secret", "content": [{"type": "text", "text": "可见回复"}]}}])
        self.sync.poll()
        card = self.observed()[0]
        self.assertEqual(card["entries"][0]["text"], "可见回复")
        self.assertNotIn("hidden identity secret", json.dumps(card))
        self.assertEqual(len(card["entries"][0]["id"]), 64)

    def test_copied_parent_metadata_cannot_unskip_subagent(self):
        path = self.codex("child", source={"subagent": "spawn"})
        self.append(path, [{"type": "session_meta", "payload": {
            "id": "parent", "cwd": "/project/a", "source": "vscode"}},
            self.cevent("task_started"), self.message("user", "hidden inherited content"),
            self.message("assistant", "hidden subagent content", phase="final_answer")])
        self.sync.poll()
        self.assertEqual(self.observed(), [])
        cursor = next(iter(self.sync.cursors.values()))
        self.assertEqual(cursor.session.session_id, "child")
        self.assertTrue(cursor.session.skip)

    def test_same_time_repeated_request_is_distinct_and_prefix_replay_dedupes(self):
        path = self.codex()
        self.append(path, [self.cevent("task_started"), self.message("user", "继续"),
                           self.message("assistant", "第一轮"), self.message("user", "继续"),
                           self.message("assistant", "第二轮")])
        self.sync.poll()
        self.assertEqual([entry["text"] for entry in self.observed()[0]["entries"]],
                         ["继续", "第一轮", "继续", "第二轮"])
        self.assertEqual(len(Cards(self.state / "cards.json").recent(None)[0]["entries"]), 4)

    def test_recent_content_is_bounded_by_count_characters_and_utf8_bytes(self):
        path = self.codex()
        events = [self.cevent("task_started")]
        for index in range(30):
            events.append(self.message("user" if index % 2 == 0 else "assistant",
                                       str(index) + "测" * 5000))
        self.append(path, events)
        self.sync.poll()
        card = self.observed()[0]
        self.assertTrue(card["content_truncated"])
        self.assertLessEqual(len(card["entries"]), 20)
        self.assertLessEqual(sum(len(entry["text"].encode()) for entry in card["entries"]), 32 * 1024)
        self.assertTrue(all(len(entry["text"]) <= 4000 for entry in card["entries"]))
        self.assertTrue(card["entries"][-1]["text"].startswith("29"))

    def test_restart_without_source_retains_last_visible_contents_as_unknown(self):
        path = self.codex()
        self.append(path, [self.cevent("task_started"), self.message("user", "保留需求"),
                           self.message("assistant", "保留回复", phase="commentary")])
        self.sync.poll()
        card = self.observed()[0]
        path.unlink()
        reloaded = Cards(self.state / "cards.json")
        sync = DesktopSync(reloaded, self.state, home=self.root, clock=lambda: self.now)
        sync.poll()
        kept = reloaded.get(card["id"])
        self.assertEqual(kept["state"], "unknown")
        self.assertEqual([entry["text"] for entry in kept["entries"]], ["保留需求", "保留回复"])
        self.assertEqual(kept["content_scope"], "recent")

    def test_visible_progress_refreshes_activity_and_old_hook_cannot_roll_it_back(self):
        path = self.codex()
        self.append(path, [self.cevent("task_started")])
        self.sync.poll()
        self.now += STALE_SECONDS + 1
        self.append(path, [self.message("assistant", "仍在执行检查", phase="commentary")])
        self.sync.poll()
        self.assertEqual(self.observed()[0]["state"], "working")
        self.hook({"agent": "codex", "session_id": "s-1", "cwd": "/wrong",
                   "status": "waiting", "observed_at": self.now - 10})
        self.sync.poll()
        self.assertEqual(self.observed()[0]["state"], "working")
        self.assertEqual(self.observed()[0]["project"], "a")


if __name__ == "__main__":
    unittest.main()
