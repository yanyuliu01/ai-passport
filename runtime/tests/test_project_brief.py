#!/usr/bin/env python3
"""Tests for project_brief.py. Standard library only; no model is called.

Builds small fake session files in the shape Claude Code and Codex actually
write (confirmed by reading real local files), plus a real tiny git repo, and
checks the digest pulls the right turns and leaves the noise out.
"""

import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_runtime import TempDirCase  # noqa: E402
from xiaoyou_runtime import project_brief as pb  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com",
                        "PATH": "/usr/bin:/bin:/usr/local/bin"})


def _write_jsonl(path: Path, lines) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
                    encoding="utf-8")


class FlattenTextTests(unittest.TestCase):
    def test_plain_string(self):
        self.assertEqual(pb._flatten_text("  你好  "), "你好")

    def test_text_blocks_only(self):
        content = [{"type": "tool_use", "name": "Bash"}, {"type": "text", "text": "一段话"},
                   {"type": "text", "text": "第二段"}]
        self.assertEqual(pb._flatten_text(content), "一段话\n第二段")

    def test_codex_input_and_output_text_blocks(self):
        # Codex 用 input_text（主人）/output_text（模型），不是 Claude 用的 text。
        self.assertEqual(pb._flatten_text([{"type": "input_text", "text": "主人说的"}]), "主人说的")
        self.assertEqual(pb._flatten_text([{"type": "output_text", "text": "模型答的"}]), "模型答的")

    def test_no_text_block(self):
        self.assertEqual(pb._flatten_text([{"type": "tool_use", "name": "Bash"}]), "")

    def test_other_types(self):
        self.assertEqual(pb._flatten_text(None), "")
        self.assertEqual(pb._flatten_text(42), "")


class IsInjectedTests(unittest.TestCase):
    def test_known_wrappers_are_injected(self):
        for text in (
            "<system-reminder>注意事项</system-reminder>",
            "<environment_context>...</environment_context>",
            "<in-app-browser-context source=\"ambient-ui-state\">...",
            "# AGENTS.md instructions for /some/path\n\n...",
        ):
            self.assertTrue(pb._is_injected(text), text)

    def test_real_turns_are_not_injected(self):
        for text in ("用一句话说说你是谁", "看一下这个，本地服务应该硬件和teamhub都有"):
            self.assertFalse(pb._is_injected(text), text)


class GitBriefTests(TempDirCase):
    def test_not_a_repo(self):
        self.assertEqual(pb.git_brief(self.folder), "")

    def test_log_and_status(self):
        _git(self.folder, "init", "-q")
        (self.folder / "a.txt").write_text("1", encoding="utf-8")
        _git(self.folder, "add", "a.txt")
        _git(self.folder, "commit", "-q", "-m", "first commit")
        (self.folder / "b.txt").write_text("2", encoding="utf-8")
        text = pb.git_brief(self.folder)
        self.assertIn("最近的提交", text)
        self.assertIn("first commit", text)
        self.assertIn("工作区状态", text)
        self.assertIn("b.txt", text)


class ClaudeTurnsTests(TempDirCase):
    def test_skips_tool_results_and_meta(self):
        path = self.folder / "session.jsonl"
        _write_jsonl(path, [
            {"type": "user", "isMeta": True, "message": {"content": "系统注入的，不算"}},
            {"type": "user", "toolUseResult": {"x": 1}, "message": {"content": "工具结果，不算"}},
            {"type": "user", "message": {"content": "主人说的第一句"}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"},
                                                            {"type": "text", "text": "模型答的"}]}},
            {"type": "summary", "message": {"content": "不是 user/assistant，不算"}},
        ])
        turns = pb._claude_turns(path, limit=10)
        self.assertEqual(turns, [
            {"role": "user", "text": "主人说的第一句"},
            {"role": "assistant", "text": "模型答的"},
        ])

    def test_skips_system_reminder_without_ismeta_flag(self):
        # 真实数据里见过没带 isMeta 的注入内容：按内容本身的开头再兜底过滤一次。
        path = self.folder / "session.jsonl"
        _write_jsonl(path, [
            {"type": "user", "message": {"content": "<system-reminder>\n背景说明\n</system-reminder>"}},
            {"type": "user", "message": {"content": "主人真的问题"}},
        ])
        turns = pb._claude_turns(path, limit=10)
        self.assertEqual(turns, [{"role": "user", "text": "主人真的问题"}])

    def test_limit_keeps_most_recent(self):
        path = self.folder / "session.jsonl"
        _write_jsonl(path, [{"type": "user", "message": {"content": "第 %d 句" % i}}
                            for i in range(5)])
        turns = pb._claude_turns(path, limit=2)
        self.assertEqual([turn["text"] for turn in turns], ["第 3 句", "第 4 句"])

    def test_missing_file(self):
        self.assertEqual(pb._claude_turns(self.folder / "nope.jsonl", limit=3), [])


class ClaudeSessionsTests(TempDirCase):
    def test_finds_sessions_for_escaped_repo_path(self):
        repo = Path("/Users/weihaoliu/ai-passport")
        home = self.folder
        project_dir = home / ".claude" / "projects" / str(repo).replace("/", "-")
        _write_jsonl(project_dir / "s1.jsonl", [{"type": "user", "message": {"content": "你好"}}])
        sessions = pb.claude_sessions(repo, now=time.time(), home=home)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["turns"], [{"role": "user", "text": "你好"}])

    def test_no_project_dir(self):
        self.assertEqual(pb.claude_sessions(Path("/nowhere"), now=time.time(), home=self.folder), [])

    def test_stale_session_excluded(self):
        repo = Path("/Users/weihaoliu/ai-passport")
        home = self.folder
        project_dir = home / ".claude" / "projects" / str(repo).replace("/", "-")
        path = project_dir / "old.jsonl"
        _write_jsonl(path, [{"type": "user", "message": {"content": "很久以前说的"}}])
        now = time.time()
        import os
        old = now - (pb.RECENT_DAYS + 1) * 86400
        os.utime(path, (old, old))
        self.assertEqual(pb.claude_sessions(repo, now=now, home=home), [])

    def test_caps_session_count(self):
        repo = Path("/Users/weihaoliu/ai-passport")
        home = self.folder
        project_dir = home / ".claude" / "projects" / str(repo).replace("/", "-")
        now = time.time()
        import os
        for i in range(pb.MAX_SESSIONS + 2):
            path = project_dir / ("s%d.jsonl" % i)
            _write_jsonl(path, [{"type": "user", "message": {"content": "第 %d 个会话" % i}}])
            os.utime(path, (now - i, now - i))
        sessions = pb.claude_sessions(repo, now=now, home=home)
        self.assertEqual(len(sessions), pb.MAX_SESSIONS)


class LatestClaudeSessionTests(TempDirCase):
    def test_picks_the_most_recent_one(self):
        repo = Path("/Users/weihaoliu/ai-passport")
        home = self.folder
        project_dir = home / ".claude" / "projects" / str(repo).replace("/", "-")
        now = time.time()
        import os
        _write_jsonl(project_dir / "old.jsonl", [{"type": "user", "message": {"content": "旧的"}}])
        os.utime(project_dir / "old.jsonl", (now - 100, now - 100))
        _write_jsonl(project_dir / "new.jsonl", [{"type": "user", "message": {"content": "新的"}}])
        os.utime(project_dir / "new.jsonl", (now, now))
        session = pb.latest_claude_session(repo, now=now, home=home)
        self.assertEqual(session["session_id"], "new")
        self.assertEqual(session["turns"], [{"role": "user", "text": "新的"}])

    def test_excludes_a_given_session_id(self):
        repo = Path("/Users/weihaoliu/ai-passport")
        home = self.folder
        project_dir = home / ".claude" / "projects" / str(repo).replace("/", "-")
        _write_jsonl(project_dir / "self.jsonl", [{"type": "user", "message": {"content": "自己刚留的"}}])
        self.assertIsNone(pb.latest_claude_session(repo, now=time.time(), home=home, exclude="self"))

    def test_none_when_no_sessions(self):
        self.assertIsNone(pb.latest_claude_session(Path("/nowhere"), now=time.time(), home=self.folder))


class RecapTextTests(unittest.TestCase):
    def test_generated_recaps_do_not_quote_other_recaps(self):
        legacy = "（主人最近（刚才）在这个项目上聊过下面这些，当时用的不是这套会话——）\n主人：old context"
        generated = pb.recap_text({"when": 1, "turns": [{"role": "user", "text": legacy},
            {"role": "user", "text": "new request"}]}, now=2)
        self.assertNotIn("old context", generated)
        self.assertIn("new request", generated)
        self.assertTrue(pb._is_injected(generated))
        self.assertTrue(pb._is_injected(legacy))
        self.assertFalse(pb._is_injected("主人最近在忙什么？"))

    def test_formats_turns_with_a_timestamp_and_instruction(self):
        now = time.time()
        session = {"when": now, "turns": [{"role": "user", "text": "我们聊到一半"},
                                          {"role": "assistant", "text": "已经查完日志了"}]}
        text = pb.recap_text(session, now=now)
        self.assertIn("接着它的话往下做", text)
        self.assertIn("主人：我们聊到一半", text)
        self.assertIn("回答：已经查完日志了", text)


class CodexSessionTests(TempDirCase):
    def test_filters_by_cwd(self):
        path = self.folder / "rollout.jsonl"
        _write_jsonl(path, [
            {"type": "session_meta", "payload": {"cwd": "/somewhere/else"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": "问个事"}]}},
        ])
        cwd, turns = pb._codex_session(path, "/Users/weihaoliu/ai-passport", limit=5)
        self.assertEqual(cwd, "/somewhere/else")
        self.assertEqual(turns, [])

    def test_matching_cwd_collects_turns(self):
        # 真实的 Codex rollout 用 input_text/output_text，不是 Claude 用的 text——
        # 这俩曾经不被认，_codex_session 对真实数据总是摘不出任何一轮。
        path = self.folder / "rollout.jsonl"
        repo = "/Users/weihaoliu/ai-passport"
        _write_jsonl(path, [
            {"type": "session_meta", "payload": {"cwd": repo}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": "问个事"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                  "content": [{"type": "output_text", "text": "答了"}]}},
            {"type": "event_msg", "payload": {"type": "task_started"}},
        ])
        cwd, turns = pb._codex_session(path, repo, limit=5)
        self.assertEqual(cwd, repo)
        self.assertEqual(turns, [{"role": "user", "text": "问个事"},
                                 {"role": "assistant", "text": "答了"}])

    def test_injected_context_is_skipped(self):
        # Codex 会把 AGENTS.md 内容当成一条 user 消息塞进去，那不是主人真的说的话。
        path = self.folder / "rollout.jsonl"
        repo = "/Users/weihaoliu/ai-passport"
        _write_jsonl(path, [
            {"type": "session_meta", "payload": {"cwd": repo}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "# AGENTS.md instructions for " + repo + "\n\n..."}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": "真的问题"}]}},
        ])
        cwd, turns = pb._codex_session(path, repo, limit=5)
        self.assertEqual(turns, [{"role": "user", "text": "真的问题"}])


class CodexSessionsTests(TempDirCase):
    def test_finds_matching_and_skips_other_repos(self):
        repo = Path("/Users/weihaoliu/ai-passport")
        home = self.folder
        day_dir = home / ".codex" / "sessions" / "2026" / "10" / "10"
        _write_jsonl(day_dir / "rollout-a.jsonl", [
            {"type": "session_meta", "payload": {"cwd": str(repo)}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": "这是这个项目的"}]}},
        ])
        _write_jsonl(day_dir / "rollout-b.jsonl", [
            {"type": "session_meta", "payload": {"cwd": "/some/other/repo"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": "别的项目"}]}},
        ])
        sessions = pb.codex_sessions(repo, now=time.time(), home=home)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["turns"], [{"role": "user", "text": "这是这个项目的"}])

    def test_no_sessions_dir(self):
        self.assertEqual(pb.codex_sessions(Path("/x"), now=time.time(), home=self.folder), [])


class BriefTests(TempDirCase):
    def test_combines_git_and_both_providers(self):
        repo = self.folder / "repo"
        repo.mkdir()
        _git(repo, "init", "-q")
        (repo / "a.txt").write_text("1", encoding="utf-8")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-q", "-m", "init repo")
        home = self.folder / "home"
        escaped = str(repo).replace("/", "-")
        _write_jsonl(home / ".claude" / "projects" / escaped / "s.jsonl",
                     [{"type": "user", "message": {"content": "问小幽的事"}}])
        _write_jsonl(home / ".codex" / "sessions" / "2026" / "10" / "10" / "rollout-x.jsonl", [
            {"type": "session_meta", "payload": {"cwd": str(repo)}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": "问 codex 的事"}]}},
        ])
        text = pb.brief(repo, now=time.time(), home=home)
        self.assertIn("init repo", text)
        self.assertIn("问小幽的事", text)
        self.assertIn("问 codex 的事", text)

    def test_empty_everything(self):
        repo = self.folder / "nothing"
        repo.mkdir()
        text = pb.brief(repo, now=time.time(), home=self.folder / "home")
        self.assertEqual(text, "这台电脑上还没有这个项目的改动或会话记录。")


if __name__ == "__main__":
    unittest.main()
