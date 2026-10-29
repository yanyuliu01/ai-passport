#!/usr/bin/env python3
"""Synthetic conversation visibility and credential-scrubbing fixtures."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xiaoyu_runtime.events.desktop_content import MAX_MESSAGE_CHARS, message_text, visible_text


class DesktopContentTests(unittest.TestCase):
    def test_extracts_public_text_without_nested_tool_or_thinking_content(self):
        content = [
            {"type": "thinking", "thinking": "private reasoning", "text": "private reasoning"},
            {"type": "reasoning", "text": "private reasoning"},
            {"type": "tool_result", "content": [{"type": "text", "text": "private output"}]},
            {"type": "tool_use", "text": "private args", "input": {"command": "private args"}},
            {"type": "input_text", "text": "  帮我同步任务内容  "},
            {"type": "text", "text": " 已开始处理 "},
            {"type": "output_text", "text": " 验证通过 "},
            {"type": "image", "text": "private image metadata"},
        ]
        self.assertEqual(message_text(content), "帮我同步任务内容\n已开始处理\n验证通过")
        self.assertEqual(message_text("  普通用户内容  "), "普通用户内容")
        self.assertEqual(message_text({"type": "text", "text": "单个文本块"}), "单个文本块")

    def test_malformed_content_is_not_coerced_to_private_text(self):
        for content in (None, 10, True, {"text": "unknown wrapper"},
                        {"type": "tool_result", "text": "private"},
                        {"type": [], "text": "private"},
                        {"type": {"text": "private"}, "text": "private"},
                        {"type": "text", "text": {"password": "private"}},
                        [{"type": "text", "text": 7}, "private", False]):
            with self.subTest(value_type=type(content).__name__):
                self.assertEqual(message_text(content), "")

    def test_ambient_browser_context_preserves_actual_request(self):
        text = """<in-app-browser-context source="ambient-ui-state">
This block is automatically supplied ambient UI state, not the user's request.
https://example.invalid/download?token=synthetic-private
</in-app-browser-context>

## My request:
我看只同步了任务状态？能不能把任务内容也同步？"""
        self.assertEqual(visible_text(text), "我看只同步了任务状态？能不能把任务内容也同步？")

    def test_mixed_context_and_visible_text_preserves_both_public_parts(self):
        text = ("请检查当前任务。\n<environment_context>private path"
                "<permissions instructions>private permissions</permissions>"
                "</environment_context>\n处理完成。")
        self.assertEqual(visible_text(text), "请检查当前任务。\n\n处理完成。")
        self.assertEqual(visible_text("开始<external_codex_apps_open_page />完成"), "开始完成")

    def test_context_only_and_agents_instruction_only_are_skipped(self):
        contexts = ("environment_context", "in-app-browser-context", "skills_instructions",
                    "app-context", "user_instructions", "system-reminder", "permissions",
                    "permissions_instructions", "external_codex_apps_open_page",
                    "codex_apps_client_time_context", "codex_apps_open_page_instructions",
                    "send_user_message_question_reply")
        for name in contexts:
            with self.subTest(name=name):
                self.assertEqual(visible_text(f"<{name}>private context</{name}>"), "")
        self.assertEqual(visible_text("# AGENTS.md instructions for /example\n"
                                     "<INSTRUCTIONS>private rules</INSTRUCTIONS>"), "")
        self.assertEqual(visible_text("# AGENTS.md instructions for /example\nprivate rules"), "")
        self.assertEqual(visible_text("# AGENTS.zh_CN.md instructions for /example\n"
                                     "<INSTRUCTIONS>private rules</INSTRUCTIONS>\n请修改页面"),
                         "请修改页面")

    def test_malformed_context_fails_closed_and_real_request_marker_survives(self):
        for text in ("<environment_context>private unfinished",
                     "<environment_context private unfinished",
                     "<environment_context>private</permissions>still private",
                     "<environment_context><app-context>private</environment_context>private"):
            with self.subTest(text=text):
                self.assertEqual(visible_text(text), "")
        self.assertEqual(visible_text("可见部分\n<environment_context>private unfinished"), "可见部分")
        self.assertEqual(visible_text("<environment_context>private unfinished\n"
                                     "## My request:\n请同步内容"), "请同步内容")
        self.assertEqual(visible_text("<environment_context>\n## My request:\n"
                                     "private injected request\n</environment_context>\n公开请求"),
                         "公开请求")

    def test_redacts_credentials_in_json_environment_and_headers(self):
        text = ("OPENAI_API_KEY=synthetic-openai\n"
                "export GITHUB_TOKEN='synthetic-github'\n"
                "DATABASE_PASSWORD=synthetic-db\n"
                'payload = {"password": "synthetic password", "accessToken": "synthetic-access", '
                '"client_secret":"synthetic-secret", "name":"公开名称"}\n'
                "Authorization: Bearer synthetic-bearer\n"
                "api_key: \"synthetic-key\"\n"
                "AWS_ACCESS_KEY_ID=synthetic-access-id\n"
                "AWS_SECRET_ACCESS_KEY=synthetic-aws-secret")
        result = visible_text(text)
        self.assertNotIn("synthetic", result)
        self.assertIn('"password": "[REDACTED]"', result)
        self.assertIn('"name":"公开名称"', result)
        self.assertIn("Authorization: Bearer [REDACTED]", result)
        self.assertEqual(visible_text('password="unfinished secret'), 'password="[REDACTED]"')
        self.assertEqual(visible_text("password=synthetic-prefix\n"
                                     "# AGENTS.md instructions for /example\nprivate rules"),
                         "password=[REDACTED]")

    def test_redacts_signed_query_and_url_password_preserving_query_shape(self):
        text = ("https://example.invalid/?project=public&token=synthetic-token&limit=5\n"
                "https://example.invalid/?X-Amz-Signature=synthetic-signature#anchor\n"
                "https://user:synthetic-password@example.invalid/public")
        result = visible_text(text)
        self.assertNotIn("synthetic", result)
        self.assertIn("?project=public&token=[REDACTED]&limit=5", result)
        self.assertIn("?X-Amz-Signature=[REDACTED]#anchor", result)
        self.assertIn("https://[REDACTED]@example.invalid/public", result)

    def test_private_keys_and_common_raw_key_formats_are_redacted_before_truncation(self):
        # These delimiters and explicitly non-key payloads are synthetic. Build
        # them at runtime so the repository's real-key scanner stays strict.
        delimiter = "-" * 5
        begin = delimiter + "BEGIN " + "RSA PRIVATE KEY" + delimiter
        end = delimiter + "END " + "RSA PRIVATE KEY" + delimiter
        unfinished = delimiter + "BEGIN " + "OPENSSH PRIVATE KEY" + delimiter
        text = ("开始\n" + begin + "\n"
                + "synthetic-private-key\n" * 300
                + end + "\n结束\n"
                + "sk-" + "s" * 30 + "\n" + "ghp_" + "g" * 30)
        result = visible_text(text)
        self.assertNotIn("synthetic", result)
        self.assertNotIn("sss", result)
        self.assertNotIn("ggg", result)
        self.assertIn("结束", result)
        self.assertIn("[REDACTED PRIVATE KEY]", result)
        self.assertEqual(visible_text(unfinished + "\nsynthetic unfinished non-key payload"),
                         "[REDACTED PRIVATE KEY]")

    def test_chinese_code_and_newlines_are_preserved_while_controls_are_normalized(self):
        text = "请检查代码：\r\n```python\r\n\tprint('<widget>中文</widget>')\r\n```\r\n\x00\x1b[31m完成\x1b[0m\u202e"
        self.assertEqual(visible_text(text),
                         "请检查代码：\n```python\n    print('<widget>中文</widget>')\n```\n完成")
        self.assertEqual(visible_text("Discuss the token budget and password reset feature."),
                         "Discuss the token budget and password reset feature.")

    def test_messages_are_bounded_without_breaking_unicode(self):
        self.assertEqual(visible_text("中" * MAX_MESSAGE_CHARS), "中" * MAX_MESSAGE_CHARS)
        result = visible_text("中" * (MAX_MESSAGE_CHARS + 100))
        self.assertEqual(len(result), MAX_MESSAGE_CHARS)
        self.assertTrue(result.endswith("…"))
        result.encode("utf-8")
        self.assertEqual(visible_text("\n\t\0"), "")
        self.assertEqual(visible_text(None), "")


if __name__ == "__main__":
    unittest.main()
