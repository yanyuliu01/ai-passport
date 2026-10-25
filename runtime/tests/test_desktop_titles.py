#!/usr/bin/env python3
"""Synthetic exact-sidebar-title fixtures; never read private desktop data."""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xiaoyou_runtime.events.desktop_titles import DesktopTitles, MAX_METADATA_BYTES, MAX_SESSION_IDS, _Budget


class DesktopTitleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.codex = self.root / ".codex"
        self.claude = self.root / ".claude"
        self.codex.mkdir()
        self.claude.mkdir()
        self.titles = DesktopTitles(self.codex, self.claude)

    def database(self, version=5, named=True):
        path = self.codex / ("state_%s.sqlite" % version)
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT" +
                           (", name TEXT" if named else "") + ")")
        connection.commit()
        return path, connection

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def append(self, path, *records):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record) + "\n")

    def refresh(self, *codex_ids, claude=()):
        self.titles.refresh({"codex": codex_ids, "claude": claude})

    def test_codex_name_is_exact_sidebar_title_not_original_prompt(self):
        path, database = self.database()
        database.execute("INSERT INTO threads VALUES (?, ?, ?)",
                         ("s-1", "first prompt that must never become upstream title", "  同步桌面会话状态  "))
        database.commit()
        database.close()
        self.append(self.codex / "session_index.jsonl", {"id": "s-1", "thread_name": "stale index name"})
        before = path.read_bytes()
        self.refresh("s-1")
        self.assertEqual(self.titles.get("codex", "s-1"), "  同步桌面会话状态  ")
        self.assertEqual(path.read_bytes(), before)

    def test_codex_rename_updates_without_any_transcript_change(self):
        _, database = self.database()
        database.execute("INSERT INTO threads VALUES ('s-1', 'original request', 'first name')")
        database.commit()
        transcript = self.codex / "sessions/rollout-s-1.jsonl"
        self.append(transcript, {"type": "session_meta", "payload": {"id": "s-1"}})
        before = transcript.stat().st_mtime_ns
        self.refresh("s-1")
        self.assertEqual(self.titles.get("codex", "s-1"), "first name")
        database.execute("UPDATE threads SET name='renamed sidebar title' WHERE id='s-1'")
        database.commit()
        database.close()
        self.refresh("s-1")
        self.assertEqual(self.titles.get("codex", "s-1"), "renamed sidebar title")
        self.assertEqual(transcript.stat().st_mtime_ns, before)

    def test_live_wal_updates_are_visible_before_checkpoint(self):
        _, database = self.database()
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("PRAGMA wal_autocheckpoint=0")
        database.execute("INSERT INTO threads VALUES ('s-1', 'request', 'before WAL rename')")
        database.commit()
        self.addCleanup(database.close)
        self.refresh("s-1")
        self.assertEqual(self.titles.get("codex", "s-1"), "before WAL rename")
        database.execute("UPDATE threads SET name='after WAL rename' WHERE id='s-1'")
        database.commit()
        self.assertTrue((self.codex / "state_5.sqlite-wal").exists())
        self.refresh("s-1")
        self.assertEqual(self.titles.get("codex", "s-1"), "after WAL rename")

    def test_readonly_connections_are_closed_and_never_immutable(self):
        _, database = self.database()
        database.execute("INSERT INTO threads VALUES ('s-1', 'request', 'name')")
        database.commit()
        database.close()
        opened, connect = [], sqlite3.connect

        def observe(database_uri, **kwargs):
            self.assertIn("?mode=ro", database_uri)
            self.assertNotIn("immutable", database_uri)
            self.assertTrue(kwargs["uri"])
            connection = connect(database_uri, **kwargs)
            opened.append(connection)
            return connection

        with mock.patch("xiaoyou_runtime.events.desktop_titles.sqlite3.connect", side_effect=observe):
            self.refresh("s-1")
        self.assertEqual(len(opened), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")

    def test_newer_database_version_wins_but_missing_ids_fall_back(self):
        _, old = self.database(version=5)
        old.executemany("INSERT INTO threads VALUES (?, 'request', ?)",
                        [("shared", "old name"), ("old-only", "older available name")])
        old.commit()
        old.close()
        _, new = self.database(version=12)
        new.execute("INSERT INTO threads VALUES ('shared', 'request', 'new schema name')")
        new.execute("ALTER TABLE threads ADD COLUMN next_schema_field TEXT")
        new.commit()
        new.close()
        self.refresh("shared", "old-only")
        self.assertEqual(self.titles.get("codex", "shared"), "new schema name")
        self.assertEqual(self.titles.get("codex", "old-only"), "older available name")

    def test_legacy_title_only_database_does_not_promote_prompt(self):
        _, database = self.database(named=False)
        database.execute("INSERT INTO threads VALUES ('s-1', 'raw first prompt')")
        database.commit()
        database.close()
        self.refresh("s-1")
        self.assertEqual(self.titles.get("codex", "s-1"), "")
        self.append(self.codex / "session_index.jsonl", {"id": "s-1", "thread_name": "desktop index name"})
        self.refresh("s-1")
        self.assertEqual(self.titles.get("codex", "s-1"), "desktop index name")

    def test_latest_session_index_record_and_rename_are_read_without_database(self):
        index = self.codex / "session_index.jsonl"
        self.append(index, {"id": "s-1", "thread_name": "first sidebar name"},
                    {"id": "other", "thread_name": "unrequested name"})
        self.refresh("s-1")
        self.assertEqual(self.titles.get("codex", "s-1"), "first sidebar name")
        self.append(index, {"id": "s-1", "thread_name": "renamed title"})
        self.refresh("s-1")
        self.assertEqual(self.titles.get("codex", "s-1"), "renamed title")
        self.assertEqual(self.titles.get("codex", "other"), "")

    def test_corrupted_database_and_locked_database_use_metadata_fallback(self):
        (self.codex / "state_12.sqlite").write_bytes(b"synthetic invalid sqlite")
        _, database = self.database()
        database.execute("INSERT INTO threads VALUES ('s-1', 'request', 'unreadable db name')")
        database.commit()
        database.execute("BEGIN EXCLUSIVE")
        self.addCleanup(database.close)
        self.append(self.codex / "session_index.jsonl", {"id": "s-1", "thread_name": "available index name"})
        self.refresh("s-1")
        self.assertEqual(self.titles.get("codex", "s-1"), "available index name")

    def test_recognized_legacy_cache_only_and_index_precedence(self):
        self.write(self.codex / ".codex-global-state.json", {
            "thread-titles": {"titles": {"s-1": "cached sidebar name", "s-2": "cached second name"}},
            "model": {"id": "s-3", "title": "unrelated model title"},
            "electron-persisted-atom-state": {"title": "unrelated atom title"},
        })
        self.append(self.codex / "session_index.jsonl", {"id": "s-1", "thread_name": "indexed sidebar name"})
        self.refresh("s-1", "s-2", "s-3")
        self.assertEqual(self.titles.get("codex", "s-1"), "indexed sidebar name")
        self.assertEqual(self.titles.get("codex", "s-2"), "cached second name")
        self.assertEqual(self.titles.get("codex", "s-3"), "")

    def test_claude_index_summary_is_display_title_and_custom_name_wins(self):
        self.write(self.claude / "projects/project/sessions-index.json", {"version": 1, "entries": [
            {"sessionId": "s-1", "summary": "Generated sidebar title", "firstPrompt": "first prompt"},
            {"sessionId": "s-2", "summary": "Generated second title", "customTitle": "  我的任务名称  "},
        ]})
        self.refresh(claude=("s-1", "s-2"))
        self.assertEqual(self.titles.get("claude", "s-1"), "Generated sidebar title")
        self.assertEqual(self.titles.get("claude", "s-2"), "  我的任务名称  ")

    def test_claude_explicit_transcript_rename_wins_over_index_summary(self):
        project = self.claude / "projects/project"
        self.write(project / "sessions-index.json", {"entries": [{
            "sessionId": "s-1", "summary": "stale auto title", "customTitle": "stale custom name"}]})
        self.append(project / "s-1.jsonl",
                    {"type": "custom-title", "sessionId": "s-1", "customTitle": "first rename"},
                    {"type": "user", "sessionId": "s-1", "message": {"content": "latest prompt"}},
                    {"type": "custom-title", "sessionId": "s-1", "customTitle": "last rename"},
                    {"type": "custom-title", "sessionId": "copied-parent", "customTitle": "wrong parent name"})
        self.refresh(claude=("s-1",))
        self.assertEqual(self.titles.get("claude", "s-1"), "last rename")
        self.append(project / "s-1.jsonl", {"type": "custom-title", "sessionId": "s-1", "customTitle": "fresh rename"})
        self.refresh(claude=("s-1",))
        self.assertEqual(self.titles.get("claude", "s-1"), "fresh rename")

    def test_same_uuid_across_agents_is_not_merged_and_unknown_agent_has_no_guess(self):
        self.append(self.codex / "session_index.jsonl", {"id": "same", "thread_name": "Codex sidebar"})
        self.append(self.claude / "projects/project/same.jsonl",
                    {"type": "custom-title", "sessionId": "same", "customTitle": "Claude sidebar"})
        self.titles.refresh({"codex": {"same"}, "claude": {"same"}, "dsh": {"same"}, "future": {"same"}})
        self.assertEqual(self.titles.get("codex", "same"), "Codex sidebar")
        self.assertEqual(self.titles.get("claude", "same"), "Claude sidebar")
        self.assertEqual(self.titles.get("dsh", "same"), "")
        self.assertEqual(self.titles.get("future", "same"), "")

    def test_absent_metadata_removes_cached_title_and_creates_no_files(self):
        index = self.codex / "session_index.jsonl"
        self.append(index, {"id": "s-1", "thread_name": "available title"})
        self.refresh("s-1")
        index.unlink()
        self.refresh("s-1")
        self.assertEqual(self.titles.get("codex", "s-1"), "")
        self.assertEqual(list(self.codex.iterdir()), [])
        self.assertEqual(list(self.claude.iterdir()), [])
        self.titles.refresh({})
        self.assertEqual(self.titles.titles, {})

    def test_malformed_untrusted_metadata_is_not_coerced_or_recursively_extracted(self):
        index = self.codex / "session_index.jsonl"
        self.append(index, [], {"id": ["s-1"], "thread_name": "wrong ID type"},
                    {"id": "s-1", "thread_name": {"title": "private nested title"}},
                    {"id": "s-2", "thread_name": True},
                    {"id": "s-3", "thread_name": "  "})
        self.write(self.codex / ".codex-global-state.json", {"thread-titles": ["untrusted cache"]})
        project = self.claude / "projects/project"
        self.write(project / "sessions-index.json", {"entries": [False,
            {"sessionId": ["s-1"], "summary": "wrong ID type"},
            {"sessionId": "s-1", "summary": {"text": "wrong title type"}},
            {"sessionId": "s-2", "customTitle": 123},
        ]})
        self.append(project / "s-1.jsonl", {"type": "custom-title", "sessionId": "s-1", "customTitle": ["not text"]})
        self.refresh("s-1", "s-2", "s-3", claude=("s-1", "s-2"))
        self.assertEqual(self.titles.titles, {})
        index.write_bytes(b"\xff\xfe malformed\n" + b'{"id":"s-1","thread_name":"valid after invalid"}\n')
        self.refresh("s-1")
        self.assertEqual(self.titles.get("codex", "s-1"), "valid after invalid")

    def test_empty_authoritative_codex_name_does_not_resurrect_old_metadata(self):
        _, older = self.database(version=4)
        older.execute("INSERT INTO threads VALUES ('s-1', 'request', 'older database name')")
        older.commit()
        older.close()
        _, current = self.database()
        current.execute("INSERT INTO threads VALUES ('s-1', 'request', 'current name')")
        current.commit()
        self.append(self.codex / "session_index.jsonl", {"id": "s-1", "thread_name": "stale index name"})
        self.write(self.codex / ".codex-global-state.json", {
            "thread-titles": {"titles": {"s-1": "stale cache name"}}})
        self.refresh("s-1")
        self.assertEqual(self.titles.get("codex", "s-1"), "current name")
        for empty in ("", None, "  "):
            current.execute("UPDATE threads SET name=? WHERE id='s-1'", (empty,))
            current.commit()
            self.refresh("s-1")
            self.assertEqual(self.titles.get("codex", "s-1"), "")
        current.close()

    def test_empty_latest_index_name_blocks_older_index_and_global_cache(self):
        index = self.codex / "session_index.jsonl"
        self.append(index, {"id": "s-1", "thread_name": "old index name"})
        self.write(self.codex / ".codex-global-state.json", {
            "thread-titles": {"titles": {"s-1": "stale cache name"}}})
        self.refresh("s-1")
        self.append(index, {"id": "s-1", "thread_name": ""})
        self.refresh("s-1")
        self.assertEqual(self.titles.get("codex", "s-1"), "")

    def test_empty_latest_claude_custom_title_blocks_stale_index_and_marker(self):
        project = self.claude / "projects/project"
        self.write(project / "sessions-index.json", {"entries": [{
            "sessionId": "s-1", "summary": "stale summary", "customTitle": "stale custom name"}]})
        path = project / "s-1.jsonl"
        self.append(path, {"type": "custom-title", "sessionId": "s-1", "customTitle": "old explicit name"})
        self.refresh(claude=("s-1",))
        self.append(path, {"type": "custom-title", "sessionId": "s-1", "customTitle": ""})
        self.refresh(claude=("s-1",))
        self.assertEqual(self.titles.get("claude", "s-1"), "")

    def test_unchanged_claude_files_use_cache_and_known_project_only(self):
        project = self.claude / "projects/a-known-project"
        self.write(project / "sessions-index.json", {"entries": [{"sessionId": "s-1", "summary": "sidebar summary"}]})
        self.append(project / "s-1.jsonl", {"type": "custom-title", "sessionId": "s-1", "customTitle": "explicit name"})
        self.write(self.claude / "projects/z-unrelated-project/sessions-index.json", {
            "entries": [{"sessionId": "other", "summary": "unrelated sidebar"}]})
        self.refresh(claude=("s-1",))
        with mock.patch("xiaoyou_runtime.events.desktop_titles._json_file", side_effect=AssertionError("unexpected metadata read")), \
             mock.patch("xiaoyou_runtime.events.desktop_titles._records", side_effect=AssertionError("unexpected transcript read")):
            self.refresh(claude=("s-1",))
        self.assertEqual(self.titles.get("claude", "s-1"), "explicit name")

    def test_cached_claude_index_invalidates_on_title_only_file_change(self):
        project = self.claude / "projects/project"
        index = project / "sessions-index.json"
        transcript = project / "s-1.jsonl"
        self.write(index, {"entries": [{"sessionId": "s-1", "summary": "initial summary"}]})
        self.append(transcript, {"type": "user", "sessionId": "s-1", "message": {"content": "initial request"}})
        before = transcript.stat().st_mtime_ns
        self.refresh(claude=("s-1",))
        self.write(index, {"entries": [{"sessionId": "s-1", "summary": "renamed summary"}]})
        self.refresh(claude=("s-1",))
        self.assertEqual(self.titles.get("claude", "s-1"), "renamed summary")
        self.assertEqual(transcript.stat().st_mtime_ns, before)

    def test_refresh_total_file_and_byte_budgets_are_shared_between_adapters(self):
        for number in range(5):
            self.write(self.claude / ("projects/project%s/sessions-index.json" % number), {
                "padding": "x" * 1500, "entries": [{"sessionId": "s-1", "summary": "project title"}]})
        self.append(self.codex / "session_index.jsonl", {"id": "s-codex", "thread_name": "Codex name"})
        budget = _Budget()
        budget.bytes, budget.files = 4000, 2
        with mock.patch("xiaoyou_runtime.events.desktop_titles._Budget", return_value=budget):
            self.refresh("s-codex", claude=("s-1",))
        self.assertGreaterEqual(budget.bytes, 0)
        self.assertEqual(budget.files, 0)
        self.assertEqual(self.titles.get("codex", "s-codex"), "Codex name")

    def test_requested_ids_are_bounded_and_path_traversal_is_ignored(self):
        index = self.codex / "session_index.jsonl"
        self.append(index, *({"id": "s-%s" % i, "thread_name": "title %s" % i}
                            for i in range(MAX_SESSION_IDS + 10)))
        self.titles.refresh({"codex": ["s-%s" % i for i in range(MAX_SESSION_IDS + 10)],
                             "claude": ["../outside", "sub/child", "sub\\child", "..", ".", None]})
        self.assertEqual(len(self.titles.titles), MAX_SESSION_IDS)
        self.assertEqual(self.titles.get("codex", "s-%s" % MAX_SESSION_IDS), "")
        self.titles.refresh({"codex": "not a collection of IDs", "claude": None})
        self.assertEqual(self.titles.titles, {})

    def test_metadata_read_bounds_keep_tail_titles_and_reject_large_json(self):
        index = self.codex / "session_index.jsonl"
        index.write_bytes(b"x" * (MAX_METADATA_BYTES + 100) + b"\n")
        self.append(index, {"id": "s-1", "thread_name": "bounded tail title"})
        self.write(self.codex / ".codex-global-state.json", {
            "oversized": "x" * MAX_METADATA_BYTES,
            "thread-titles": {"titles": {"s-2": "must not be read"}},
        })
        self.refresh("s-1", "s-2")
        self.assertEqual(self.titles.get("codex", "s-1"), "bounded tail title")
        self.assertEqual(self.titles.get("codex", "s-2"), "")


if __name__ == "__main__":
    unittest.main()
