#!/usr/bin/env python3
"""Tests for streamed replies: the DeepSeek stream, the partial reply, and how the
message record carries it to the phone. No model or external network is called."""

import io
import dataclasses
import urllib.request
import json
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
from unittest.mock import patch
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS))

from test_runtime import Scripted, TempDirCase, ToolTalker, make_xiaoyu, write_config  # noqa: E402
from xiaoyu_runtime import config as config_module  # noqa: E402
from xiaoyu_runtime.helpers import dsh_stream  # noqa: E402
from xiaoyu_runtime.loop import xiaoyu as xiaoyu_module  # noqa: E402
from xiaoyu_runtime.helpers.agents import AgentError, Cut, DshAgent, Job, Outcome  # noqa: E402
from xiaoyu_runtime.api.service import Service, PROVIDER_BUSY_REPLIES, _provider_busy_reply  # noqa: E402
from xiaoyu_runtime.api.server import make_server  # noqa: E402
from xiaoyu_runtime.loop.xiaoyu import reply_so_far  # noqa: E402


def sse(*chunks, stop=True):
    """The body of a DeepSeek Messages stream that says the chunks in order."""
    lines = ['event: message_start', 'data: {"type":"message_start","message":{}}', '']
    for chunk in chunks:
        lines += ['event: content_block_delta', 'data: ' + json.dumps(
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": chunk}}), '']
    if stop:
        lines += ['event: message_stop', 'data: {"type":"message_stop"}', '']
    return ("\n".join(lines) + "\n").encode("utf-8")


class FakeOpener:
    """Records each request and answers with the next scripted stream (or error)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request, json.loads(request.data.decode("utf-8"))))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(answer)


def dsh_spec(folder, stream=True):
    return config_module.AgentSpec(
        name="deepseek", type="dsh", description="", aliases=[], speaks=True,
        timeout_seconds=60, command=["dsh"], workdir=folder, model="deepseek-v4-pro",
        config_dir=folder / "home", stream=stream,
        env={"DEEPSEEK_API_KEY": "k"})


class ReplySoFarTests(unittest.TestCase):
    def test_reads_the_reply_while_the_json_is_still_being_written(self):
        self.assertEqual(reply_so_far(""), "")
        self.assertEqual(reply_so_far('{"rep'), "")
        self.assertEqual(reply_so_far('{"reply": "'), "")
        self.assertEqual(reply_so_far('{"reply": "你好，我'), "你好，我")
        self.assertEqual(reply_so_far('{"reply":"第一行\\n第二'), "第一行\n第二")
        self.assertEqual(reply_so_far('{"reply": "说 \\"好\\" 了", "brief": "x'), '说 "好" 了')

    def test_waits_for_a_half_written_escape(self):
        self.assertEqual(reply_so_far('{"reply": "a\\'), "a")
        self.assertEqual(reply_so_far('{"reply": "a\\u4f'), "a")
        self.assertEqual(reply_so_far('{"reply": "a\\u4f60'), "a你")
        # A surrogate pair is shown only once both halves have arrived.
        self.assertEqual(reply_so_far('{"reply": "a\\ud83d'), "a")
        self.assertEqual(reply_so_far('{"reply": "a\\ud83d\\ude00'), "a\U0001F600")

    def test_finds_the_reply_after_other_fields_and_inside_a_fence(self):
        raw = '```json\n{"card": "new", "reply": "好的'
        self.assertEqual(reply_so_far(raw), "好的")


class DshStreamTests(TempDirCase):
    def test_legacy_recap_is_projected_once_without_overwriting_original_session(self):
        from xiaoyu_runtime.loop.voice_tools import Toolbox
        spec = dsh_spec(self.folder)
        old = 'apt-' + 'a' * 32
        folder = dsh_stream.sessions_dir(spec)
        original = [{'role': 'user', 'content': dsh_stream._RECAP_PREFIX +
                     '相关留存原文：旧的时间测试' + dsh_stream._RECAP_INPUT + '查主对话'},
                    {'role': 'assistant', 'content': '我看看'},
                    {'role': 'user', 'content': '再看看？'},
                    {'role': 'assistant', 'content': '这次回错了'}]
        dsh_stream._save(folder, old, original)
        tools = Toolbox([], [], [], lambda _: None, lambda _: None)
        opener = FakeOpener(sse('接着查。'))
        transient = dsh_stream._RECAP_PREFIX + '本次临时背景' + dsh_stream._RECAP_INPUT + '怎么说？'
        out = dsh_stream.run(spec, Job(text=transient, session_text='怎么说？', session_id=old,
                                       system='S', plain=True, tools=tools),
                             {'DEEPSEEK_API_KEY': 'k'}, 'm', lambda _: None, opener)
        sent = opener.requests[0][1]['messages']
        self.assertEqual(sent[0]['content'], '查主对话')
        self.assertNotIn('旧的时间测试', str(sent))
        self.assertIn('本次临时背景', str(sent))
        self.assertNotEqual(out.session_id, old)
        self.assertEqual(dsh_stream._load(folder, old), original)
        saved = dsh_stream._load(folder, out.session_id)
        self.assertEqual(saved[-2]['content'], '怎么说？')
        self.assertNotIn('本次临时背景', str(saved))
        self.assertIn('再看看？', str(saved))

    def test_ambiguous_recap_marker_preserves_input(self):
        value = dsh_stream._RECAP_PREFIX + '背景' + dsh_stream._RECAP_INPUT + '原话' + dsh_stream._RECAP_INPUT + '尾部'
        original = [{'role': 'user', 'content': value}]
        projected, changed = dsh_stream.project_session_history(original)
        self.assertFalse(changed)
        self.assertEqual(projected, original)

    def test_active_window_excludes_old_turns_without_deleting_saved_history(self):
        from xiaoyu_runtime.loop.voice_tools import Toolbox
        spec = dsh_spec(self.folder)
        folder = dsh_stream.sessions_dir(spec)
        session = 'apt-' + 'c' * 32
        original = []
        for i in range(9):
            original.extend([{'role': 'user', 'content': '旧问题%d' % i},
                             {'role': 'assistant', 'content': '旧回答%d' % i}])
        dsh_stream._save(folder, session, original)
        opener = FakeOpener(sse('继续。'))
        out = dsh_stream.run(spec, Job(text='接着说', session_id=session, system='S', plain=True,
                                       tools=Toolbox([], [], [], lambda _: None, lambda _: None)),
                             {'DEEPSEEK_API_KEY': 'k'}, 'm', lambda _: None, opener)
        sent = opener.requests[0][1]['messages']
        self.assertNotIn('旧问题2', str(sent))
        self.assertIn('旧问题3', str(sent))
        self.assertEqual(dsh_stream._load(folder, out.session_id)[:len(original)], original)

    def test_active_window_keeps_explicitly_requested_older_image(self):
        history = []
        for i in range(9):
            user = {'role': 'user', 'content': '问题%d' % i}
            if i == 0:
                user['_image_refs'] = ['a' + '1' * 32]
            history.extend([user, {'role': 'assistant', 'content': '回答'}])
        prefix, recent = dsh_stream.working_history(history, '看看那张图片')
        self.assertEqual(prefix, [])
        self.assertEqual(recent, history)

    def test_active_window_retains_nearby_completed_tool_and_its_owner_request(self):
        history = [{'role': 'user', 'content': '查主对话最早消息'},
                   {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'call1',
                                                       'name': 'read_recent_chat', 'input': {'channel': 'main'}}]},
                   {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'call1', 'content': '最近一页'}]},
                   {'role': 'assistant', 'content': '还可以往前查'}]
        for i in range(7):
            history.extend([{'role': 'user', 'content': '追问%d' % i},
                            {'role': 'assistant', 'content': '回复%d' % i}])
        prefix, recent = dsh_stream.working_history(history, '接着看看')
        self.assertEqual(prefix, [])
        self.assertIn('read_recent_chat', str(recent))
        self.assertIn('查主对话最早消息', str(recent))
        hint = dsh_stream.previous_tool_hint(recent)
        self.assertIn('查主对话最早消息', hint)
        self.assertIn('"channel":"main"', hint)
        sent = dsh_stream.request_messages([{'role': 'user', 'content': '继续'}], hint, 0)
        self.assertIn('最近工具线索', sent[0]['content'])
        self.assertIn('不表示事情已完成', sent[0]['content'])

    def test_working_receipt_is_current_context_and_not_saved_as_user_text(self):
        system = '## 最近实际读取回执\n这是实际读取的事实。\n[{"工具":"read_recent_chat"}]'
        messages = [{'role': 'user', 'content': '怎么说'}]
        outgoing = dsh_stream.request_messages(messages, system, 0)
        self.assertIn('read_recent_chat', outgoing[0]['content'])
        self.assertIn('不能证明结果已送达', outgoing[0]['content'])
        self.assertEqual(messages[0]['content'], '怎么说')

    def test_quote_evidence_is_repeated_next_to_current_input_only(self):
        system = ('## 本轮微信引用\n这是本轮引用。\n'
                  '[{"类型":"text","内容":"19:33，周三","当时回复的主人消息":"刚刚修了一下"}]')
        messages = [{'role': 'user', 'content': '这条回复的是哪句'}]
        outgoing = dsh_stream.request_messages(messages, system, 0)
        self.assertIn('19:33，周三', outgoing[0]['content'])
        self.assertIn('刚刚修了一下', outgoing[0]['content'])
        self.assertEqual(messages[0]['content'], '这条回复的是哪句')

    def test_explicit_other_chat_evidence_reaches_current_input(self):
        system = '## 本轮跨渠道记录\n以下是另一渠道。\n{"渠道":"微信","记录":[{"内容":"今天聊了画画"}]}'
        messages = [{'role': 'user', 'content': '微信刚聊了什么'}]
        outgoing = dsh_stream.request_messages(messages, system, 0)
        self.assertIn('今天聊了画画', outgoing[0]['content'])
        self.assertEqual(messages[0]['content'], '微信刚聊了什么')

    def test_opt_in_clock_diagnostic_records_only_metadata_for_weixin(self):
        spec = dsh_spec(self.folder)
        spec.config_dir.mkdir(parents=True)
        (spec.config_dir / 'xiaoyu-clock-diagnostic.enabled').touch()
        system = '## 本轮上下文\n现在是 2026-11-04 16:06（周三）。'
        opener = FakeOpener(sse('ok'))
        dsh_stream.run(spec, Job(text='private-message-test', system=system, conversation='weixin'),
                       {'DEEPSEEK_API_KEY': 'private-key-test'}, 'deepseek-flash', lambda text: None, opener)
        raw = (spec.config_dir / 'xiaoyu-clock-diagnostic.jsonl').read_text()
        record = json.loads(raw)
        self.assertTrue(record['system_time_complete'])
        self.assertTrue(record['input_time_complete'])
        self.assertTrue(record['time_values_match'])
        self.assertNotIn('private-message-test', raw)
        self.assertNotIn('private-key-test', raw)
        self.assertNotIn('2026-11-04', raw)
        self.assertEqual((spec.config_dir / 'xiaoyu-clock-diagnostic.jsonl').stat().st_mode & 0o777, 0o600)

    def test_current_time_is_sent_beside_input_but_not_saved_in_history(self):
        from xiaoyu_runtime.loop.voice_tools import Toolbox
        spec = dsh_spec(self.folder)
        system = '## 本轮上下文\n现在是 2026-11-04 16:06（周三）。\n其他信息'
        opener = FakeOpener(sse('16:06'))
        tools = Toolbox([], [], [], lambda fields: None, lambda card: None)
        outcome = dsh_stream.run(spec, Job(text='现在几点？', system=system, tools=tools),
                                 {'DEEPSEEK_API_KEY': 'k'}, 'deepseek-flash', lambda text: None, opener)
        sent = opener.requests[0][1]['messages'][-1]['content']
        self.assertIn('当前本地时间 2026-11-04 16:06（周三）', sent)
        saved = dsh_stream._load(dsh_stream.sessions_dir(spec), outcome.session_id)
        self.assertEqual(saved[0]['content'], '现在几点？')

    def test_clock_metadata_does_not_mutate_multimodal_or_other_messages(self):
        image = {'type': 'image', 'source': {'type': 'base64', 'data': 'example'}}
        original = [{'role': 'user', 'content': [{'type': 'text', 'text': '这是什么'}, image]}]
        system = '## 本轮上下文\n现在是 2026-11-04 16:06（周三）。'
        sent = dsh_stream.request_messages(original, system, 0)
        self.assertEqual(len(original[0]['content']), 2)
        self.assertIn('16:06', sent[0]['content'][-1]['text'])
        self.assertIs(dsh_stream.request_messages(original, '没有当前时间', 0), original)

    def test_ephemeral_background_call_does_not_persist_session(self):
        spec = dsh_spec(self.folder)
        with patch.object(dsh_stream, '_save') as save, patch.object(dsh_stream, '_load', return_value=[]) as load:
            outcome = dsh_stream.run(spec, Job(text='background', ephemeral=True, session_id='api-old'),
                                     {'DEEPSEEK_API_KEY': 'k'}, 'deepseek-flash',
                                     lambda text: None, FakeOpener(sse('result')))
        self.assertEqual('result', outcome.text)
        save.assert_not_called()
        self.assertIsNone(load.call_args.args[1])

    def test_interrupted_image_turn_keeps_owner_input_for_the_next_message(self):
        import base64, hashlib
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/aS8AAAAASUVORK5CYII=')
        image = self.folder / 'picture.png'
        image.write_bytes(png)
        def resolve(identity):
            self.assertEqual(identity, 'owner-id')
            return io.BytesIO(png), {'source': 'owner', 'kind': 'image',
                                    'sha256': hashlib.sha256(png).hexdigest()}, 'image/png', len(png)
        cut = Cut()
        opener = FakeOpener(sse('unseen'), sse('answer'))
        def interrupted_open(request, timeout=None):
            result = opener(request, timeout=timeout)
            cut.set()
            return result
        spec = dsh_spec(self.folder)
        first = dsh_stream.run(spec, Job(text='请看这张', images=(str(image),), image_refs=('owner-id',),
                                        image_resolver=resolve, interrupt=cut),
                               {'DEEPSEEK_API_KEY': 'k'}, 'deepseek-flash', lambda text: None, interrupted_open)
        saved = dsh_stream._load(dsh_stream.sessions_dir(spec), first.session_id)
        self.assertEqual(saved[0]['_image_refs'], ['owner-id'])
        self.assertEqual(saved[1]['content'], '（主人发来了新消息，这一轮尚未回复。）')
        dsh_stream.run(spec, Job(text='看看阿尼亚', session_id=first.session_id, image_resolver=resolve),
                       {'DEEPSEEK_API_KEY': 'k'}, 'deepseek-flash', lambda text: None, opener)
        messages = opener.requests[1][1]['messages']
        self.assertEqual(base64.b64decode(messages[0]['content'][1]['source']['data']), png)
        self.assertEqual(messages[1]['content'], '（主人发来了新消息，这一轮尚未回复。）')
        self.assertEqual(messages[-1]['content'], '看看阿尼亚')

    def test_provider_timing_is_correlated_without_prompt_or_key(self):
        from xiaoyu_runtime import timing as turn_timing
        sink = turn_timing.TurnTiming(self.folder)
        client, request = "a" * 32, "b" * 32
        opener = FakeOpener(sse("private-reply"))
        with turn_timing.turn(sink, client, request):
            dsh_stream.run(dsh_spec(self.folder), Job(text="private-prompt"),
                           {"DEEPSEEK_API_KEY": "private-key"}, "deepseek-flash", lambda text: None, opener)
        turn_timing._QUEUE.join()
        data = sink.path.read_text()
        records = [json.loads(line) for line in data.splitlines()]
        self.assertEqual([r["phase"] for r in records], ["model_start", "model_headers",
            "model_first_event", "model_first_text", "model_end"])
        self.assertTrue(all(r["client_id"] == client and r["request_id"] == request for r in records))
        self.assertTrue(all(r["attempt"] == 1 for r in records))
        for secret in ("private-prompt", "private-reply", "private-key"):
            self.assertNotIn(secret, data)

    def test_current_image_uses_authorized_resolver_and_rejects_changed_bytes(self):
        import base64, hashlib
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/aS8AAAAASUVORK5CYII=")
        bad_path = self.folder / "untrusted.png"; bad_path.write_bytes(b"not an image")
        spec = dsh_spec(self.folder)
        meta = {"source": "owner", "kind": "image", "sha256": hashlib.sha256(png).hexdigest()}
        def resolve(identity):
            self.assertEqual(identity, "owner-id")
            return io.BytesIO(png), meta, "image/png", len(png)
        opener = FakeOpener(sse("ok"))
        job = Job(text="look", images=(str(bad_path),), image_refs=("owner-id",), image_resolver=resolve)
        dsh_stream.run(spec, job, {"DEEPSEEK_API_KEY": "k"}, "deepseek-flash", lambda text: None, opener)
        self.assertEqual(base64.b64decode(opener.requests[0][1]["messages"][-1]["content"][1]["source"]["data"]), png)
        meta["sha256"] = "0" * 64
        opener = FakeOpener(sse("must not run"))
        with self.assertRaises(dsh_stream.PermanentRequestError):
            dsh_stream.run(spec, job, {"DEEPSEEK_API_KEY": "k"}, "deepseek-flash", lambda text: None, opener)
        self.assertEqual(opener.requests, [])

    def test_image_reference_requires_resolver(self):
        opener = FakeOpener(sse("must not run"))
        with self.assertRaises(dsh_stream.PermanentRequestError):
            dsh_stream.run(dsh_spec(self.folder), Job(text="look", images=("path",), image_refs=("id",)),
                           {"DEEPSEEK_API_KEY": "k"}, "deepseek-flash", lambda text: None, opener)
        self.assertEqual(opener.requests, [])

    def test_http_status_classification_keeps_retryable_failures_distinct(self):
        for code, error_type in ((401, dsh_stream.PermanentRequestError),
                                 (400, dsh_stream.PermanentRequestError), (429, AgentError), (503, AgentError)):
            with self.subTest(code=code):
                failure = urllib.error.HTTPError("https://fixture.invalid", code, "error", {}, io.BytesIO(b"error"))
                with self.assertRaises(error_type) as caught:
                    dsh_stream.run(dsh_spec(self.folder), Job(text="look"), {"DEEPSEEK_API_KEY": "k"},
                                   "deepseek-flash", lambda text: None, FakeOpener(failure))
                self.assertEqual(isinstance(caught.exception, dsh_stream.PermanentRequestError), code in (400, 401))

    def test_503_is_safe_and_never_resubmitted_through_headless(self):
        failure = urllib.error.HTTPError("https://fixture.invalid", 503, "busy", {},
                                         io.BytesIO(b'{"request_id":"private-id","message":"secret"}'))
        opener = FakeOpener(failure)
        agent = DshAgent(dsh_spec(self.folder), opener=opener)
        with patch.object(agent, "_headless") as headless:
            with self.assertRaises(dsh_stream.ProviderBusy) as caught:
                agent.run(Job(text="hello", plain=True, on_text=lambda text: None))
        self.assertEqual(len(opener.requests), 1)
        headless.assert_not_called()
        self.assertNotIn("private-id", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

    def test_tool_results_do_not_hide_image_from_immediate_followup(self):
        import base64, hashlib
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/aS8AAAAASUVORK5CYII=")
        spec = dsh_spec(self.folder)
        session = "api-" + "a" * 32
        dsh_stream._save(dsh_stream.sessions_dir(spec), session, [
            {"role": "user", "content": "看看", "_image_refs": ["owner-id"]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "task_detail", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]},
            {"role": "assistant", "content": "看到了"}])
        def resolve(identity):
            return io.BytesIO(png), {"source": "owner", "kind": "image", "sha256": hashlib.sha256(png).hexdigest()}, "image/png", len(png)
        opener = FakeOpener(sse("answer"))
        dsh_stream.run(spec, Job(text="右上角写什么？", session_id=session, image_resolver=resolve),
                       {"DEEPSEEK_API_KEY": "k"}, "deepseek-flash", lambda text: None, opener)
        blocks = opener.requests[0][1]["messages"][0]["content"]
        self.assertEqual(base64.b64decode(blocks[1]["source"]["data"]), png)
        opener = FakeOpener(sse("answer"))
        dsh_stream.run(spec, Job(text="换这个咋样", session_id=session, image_resolver=resolve),
                       {"DEEPSEEK_API_KEY": "k"}, "deepseek-flash", lambda text: None, opener)
        blocks = opener.requests[0][1]["messages"][0]["content"]
        self.assertEqual(base64.b64decode(blocks[1]["source"]["data"]), png)

    def test_followup_never_guesses_when_selected_image_cannot_be_restored(self):
        spec = dsh_spec(self.folder)
        session = 'api-' + 'a' * 32
        dsh_stream._save(dsh_stream.sessions_dir(spec), session, [
            {'role': 'user', 'content': '看看', '_image_refs': ['owner-id']},
            {'role': 'assistant', 'content': '看到了'}])
        opener = FakeOpener(sse('must not be sent'))
        def missing(identity):
            raise AgentError('missing')
        with self.assertRaisesRegex(dsh_stream.PermanentRequestError, '重新发送图片'):
            dsh_stream.run(spec, Job(text='换这个咋样', session_id=session, image_resolver=missing),
                           {'DEEPSEEK_API_KEY': 'k'}, 'deepseek-flash', lambda text: None, opener)
        self.assertEqual(opener.requests, [])

    def test_two_followups_keep_image_and_later_omissions_are_explicit(self):
        import base64, hashlib
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/aS8AAAAASUVORK5CYII=")
        image = self.folder / "picture.png"; image.write_bytes(png)
        spec = dsh_spec(self.folder)
        opener = FakeOpener(sse("image"), sse("left"), sse("right"), sse("unrelated"))
        def resolve(identity):
            return io.BytesIO(png), {"source": "owner", "kind": "image", "sha256": hashlib.sha256(png).hexdigest()}, "image/png", len(png)
        first = dsh_stream.run(spec, Job(text="看看", images=(str(image),), image_refs=("owner-id",), image_resolver=resolve),
                               {"DEEPSEEK_API_KEY": "k"}, "deepseek-flash", lambda text: None, opener)
        session = first.session_id
        for text in ("左边呢", "右边呢", "天气怎么样"):
            outcome = dsh_stream.run(spec, Job(text=text, session_id=session, image_resolver=resolve),
                                     {"DEEPSEEK_API_KEY": "k"}, "deepseek-flash", lambda text: None, opener)
            session = outcome.session_id
        self.assertIsInstance(opener.requests[1][1]["messages"][0]["content"], list)
        self.assertIsInstance(opener.requests[2][1]["messages"][0]["content"], list)
        self.assertIn("历史图片未随本次请求提供", opener.requests[3][1]["messages"][0]["content"])
        self.assertNotIn("历史图片未随本次请求提供", str(dsh_stream._load(dsh_stream.sessions_dir(spec), session)))

    def test_image_is_sent_as_native_content_without_persisting_binary(self):
        import base64
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/aS8AAAAASUVORK5CYII=")
        image = self.folder / "picture.png"
        image.write_bytes(png)
        opener = FakeOpener(sse("A white pixel"))
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text="What is this?", images=(str(image),)),
            {"DEEPSEEK_API_KEY": "k"}, "deepseek-flash", lambda text: None, opener)
        blocks = opener.requests[0][1]["messages"][-1]["content"]
        self.assertEqual(blocks[0], {"type": "text", "text": "What is this?"})
        self.assertEqual(base64.b64decode(blocks[1]["source"]["data"]), png)
        self.assertEqual(blocks[1]["source"]["media_type"], "image/png")
        saved = (dsh_stream.sessions_dir(dsh_spec(self.folder)) / (outcome.session_id + ".json")).read_text()
        self.assertNotIn(base64.b64encode(png).decode(), saved)
        self.assertNotIn(str(image), saved)
        self.assertIn("图片未保留", saved)

    def test_followup_restores_previous_image_by_reference_not_path(self):
        import base64
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/aS8AAAAASUVORK5CYII=")
        image = self.folder / "picture.png"; image.write_bytes(png)
        opener = FakeOpener(sse("A pixel"), sse("White"))
        spec = dsh_spec(self.folder)
        def resolve(identity):
            self.assertEqual(identity, "owner-id")
            import hashlib
            return io.BytesIO(png), {"source": "owner", "kind": "image", "sha256": hashlib.sha256(png).hexdigest()}, "image/png", len(png)
        first = dsh_stream.run(spec, Job(text="describe", images=(str(image),), image_refs=("owner-id",), image_resolver=resolve),
            {"DEEPSEEK_API_KEY": "k"}, "deepseek-flash", lambda text: None, opener)
        second = dsh_stream.run(spec, Job(text="what color?", session_id=first.session_id, image_resolver=resolve),
            {"DEEPSEEK_API_KEY": "k"}, "deepseek-flash", lambda text: None, opener)
        earlier = opener.requests[1][1]["messages"][0]["content"]
        self.assertEqual(base64.b64decode(earlier[1]["source"]["data"]), png)
        saved = (dsh_stream.sessions_dir(spec) / (second.session_id + ".json")).read_text()
        self.assertIn("owner-id", saved)
        self.assertNotIn(base64.b64encode(png).decode(), saved)
        self.assertNotIn(str(image), saved)

    def test_later_unrelated_chat_does_not_reupload_previous_image(self):
        import base64
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/aS8AAAAASUVORK5CYII=")
        image = self.folder / "picture.png"
        image.write_bytes(png)
        opener = FakeOpener(sse("A pixel"), sse("White"), sse("Hello"), sse("White"))
        spec = dsh_spec(self.folder)
        def resolve(identity):
            import hashlib
            return io.BytesIO(png), {"source": "owner", "kind": "image", "sha256": hashlib.sha256(png).hexdigest()}, "image/png", len(png)
        first = dsh_stream.run(spec, Job(text="describe", images=(str(image),), image_refs=("owner-id",), image_resolver=resolve),
            {"DEEPSEEK_API_KEY": "k"}, "deepseek-flash", lambda text: None, opener)
        second = dsh_stream.run(spec, Job(text="what color?", session_id=first.session_id, image_resolver=resolve),
            {"DEEPSEEK_API_KEY": "k"}, "deepseek-flash", lambda text: None, opener)
        third = dsh_stream.run(spec, Job(text="hello", session_id=second.session_id, image_resolver=resolve),
            {"DEEPSEEK_API_KEY": "k"}, "deepseek-flash", lambda text: None, opener)
        self.assertTrue(all(isinstance(m["content"], str) for m in opener.requests[2][1]["messages"]))
        dsh_stream.run(spec, Job(text="那张图片是什么颜色？", session_id=third.session_id, image_resolver=resolve),
            {"DEEPSEEK_API_KEY": "k"}, "deepseek-flash", lambda text: None, opener)
        self.assertTrue(any(isinstance(m["content"], list) for m in opener.requests[3][1]["messages"]))

    def test_tool_result_does_not_hide_image_from_immediate_followup(self):
        import base64, hashlib
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/aS8AAAAASUVORK5CYII=")
        spec = dsh_spec(self.folder)
        session = "api-" + "a" * 32
        dsh_stream._save(dsh_stream.sessions_dir(spec), session, [
            {"role": "user", "content": "看看", "_image_refs": ["owner-id"]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "task_detail", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]},
            {"role": "assistant", "content": "看到了"}])
        def resolve(identity):
            return io.BytesIO(png), {"source": "owner", "kind": "image", "sha256": hashlib.sha256(png).hexdigest()}, "image/png", len(png)
        opener = FakeOpener(sse("白色"))
        dsh_stream.run(spec, Job(text="右上角是什么？", session_id=session, image_resolver=resolve),
            {"DEEPSEEK_API_KEY": "k"}, "deepseek-flash", lambda text: None, opener)
        self.assertTrue(any(isinstance(m["content"], list) and any(x.get("type") == "image" for x in m["content"])
                            for m in opener.requests[0][1]["messages"]))

    def test_missing_image_fails_without_calling_provider(self):
        opener = FakeOpener(sse("should not happen"))
        with self.assertRaisesRegex(AgentError, "图片不可读取"):
            dsh_stream.run(dsh_spec(self.folder), Job(text="look", images=(str(self.folder / "missing"),)),
                {"DEEPSEEK_API_KEY": "k"}, "deepseek-flash", lambda text: None, opener)
        self.assertEqual(opener.requests, [])

    def test_preparation_can_expire_the_budget_before_opening_the_provider(self):
        now = [0.0]
        def load(*args):
            now[0] = 1.2
            return []
        with patch.object(dsh_stream.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(dsh_stream, "_load", side_effect=load), patch.object(dsh_stream.urllib.request, "urlopen") as opener:
            with self.assertRaises(AgentError):
                dsh_stream.run(dsh_spec(self.folder), Job(text="test", plain=True, timeout=1),
                               {"DEEPSEEK_API_KEY": "k"}, "m", lambda text: None, opener)
        opener.assert_not_called()

    def test_preparation_leaves_a_fractional_connection_timeout(self):
        now = [0.0]
        def load(*args):
            now[0] = .8
            return []
        with patch.object(dsh_stream.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(dsh_stream, "_load", side_effect=load), patch.object(dsh_stream.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")) as opener:
            with self.assertRaises(AgentError):
                dsh_stream.run(dsh_spec(self.folder), Job(text="test", plain=True, timeout=1),
                               {"DEEPSEEK_API_KEY": "k"}, "m", lambda text: None, opener)
        self.assertAlmostEqual(.2, opener.call_args.kwargs["timeout"])

    def test_request_timing_separates_headers_events_text_and_cache_usage(self):
        now = [0.0]

        class TimedResponse(io.BytesIO):
            def __iter__(self):
                now[0] = 0.3
                yield b'data: {"type":"message_start","message":{"usage":{"input_tokens":20,"cache_read_input_tokens":80,"secret":"NO_LOG"}}}\n'
                now[0] = 1.4
                yield b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"PRIVATE_REPLY"}}\n'
                now[0] = 1.6
                yield b'data: {"type":"message_delta","usage":{"output_tokens":3}}\n'
                now[0] = 1.7
                yield b'data: {"type":"message_stop"}\n'

        def opener(request, timeout):
            now[0] = 0.2
            return TimedResponse()

        log = io.StringIO()
        with patch.object(dsh_stream.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(dsh_stream.sys, "stderr", log):
            dsh_stream.run(dsh_spec(self.folder), Job(text="PRIVATE_REQUEST", plain=True),
                           {"DEEPSEEK_API_KEY": "PRIVATE_KEY"}, "m", lambda text: None, opener)
        record = json.loads(next(line.split("timing=", 1)[1] for line in log.getvalue().splitlines()
                                 if line.startswith("DeepSeek request timing=")))
        self.assertEqual([record[key] for key in
                          ("headers_s", "first_event_s", "first_text_s", "total_s")],
                         [0.2, 0.3, 1.4, 1.7])
        self.assertEqual(record["usage"], {"input_tokens": 20, "cache_read_input_tokens": 80,
                                           "output_tokens": 3})
        self.assertEqual(record["status"], "completed")
        self.assertNotIn("PRIVATE", log.getvalue())
        self.assertNotIn("NO_LOG", log.getvalue())

    def test_failed_request_records_missing_text_and_usage_without_fake_zero(self):
        log = io.StringIO()
        with patch.object(dsh_stream.sys, "stderr", log):
            with self.assertRaises(AgentError):
                dsh_stream.run(dsh_spec(self.folder), Job(text="x", plain=True),
                               {"DEEPSEEK_API_KEY": "k"}, "m", lambda text: None,
                               FakeOpener(urllib.error.URLError("offline")))
        record = json.loads(log.getvalue().split("timing=", 1)[1])
        self.assertEqual(record["status"], "failed")
        self.assertIsNone(record["headers_s"])
        self.assertIsNone(record["first_text_s"])
        self.assertEqual(record["usage"], {})

    def test_silent_response_is_cancelled_without_waiting_for_an_event(self):
        closed = threading.Event()

        class SilentResponse:
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
            def close(self): closed.set()
            def __iter__(self):
                closed.wait(3)
                return iter(())

        start = time.monotonic()
        with self.assertRaisesRegex(AgentError, "超过 1 秒"):
            dsh_stream.run(dsh_spec(self.folder), Job(text="test", plain=True, timeout=1),
                           {"DEEPSEEK_API_KEY": "k"}, "m", lambda text: None,
                           lambda request, timeout: SilentResponse())
        self.assertTrue(closed.is_set())
        self.assertLess(time.monotonic() - start, 2)

    def test_heartbeat_only_stream_cannot_extend_the_total_deadline(self):
        now = [0.0]

        class Heartbeats(io.BytesIO):
            def __iter__(self):
                yield b": keep-alive\n"
                now[0] = 2.1
                yield b": keep-alive\n"

        with patch.object(dsh_stream.time, "monotonic", side_effect=lambda: now[0]):
            with self.assertRaisesRegex(AgentError, "超过 2 秒"):
                dsh_stream.run(dsh_spec(self.folder), Job(text="test", plain=True, timeout=2),
                               {"DEEPSEEK_API_KEY": "k"}, "m", lambda text: None,
                               lambda request, timeout: Heartbeats())

    def test_streams_text_and_keeps_its_own_session(self):
        spec = dsh_spec(self.folder)
        seen = []
        opener = FakeOpener(sse('{"reply": "你', '好", "brief": "hi", "mood": "happy"}'),
                            sse('{"reply": "又见面了", "brief": "b", "mood": "idle"}'))
        job = Job(text="在吗", system="SYSTEM", schema={"type": "object"}, plain=True,
                  on_text=seen.append)
        outcome = dsh_stream.run(spec, job, {"DEEPSEEK_API_KEY": "k"}, "deepseek-v4-pro",
                                 seen.append, opener)
        self.assertEqual(outcome.fields["reply"], "你好")
        self.assertTrue(outcome.session_id.startswith("api-"))
        self.assertEqual(seen, ['{"reply": "你', '{"reply": "你好", "brief": "hi", "mood": "happy"}'])
        request, body = opener.requests[0]
        self.assertEqual(request.full_url, "https://api.deepseek.com/anthropic/v1/messages")
        self.assertEqual(request.get_header("X-api-key"), "k")
        self.assertEqual((body["stream"], body["thinking"], body["model"]),
                         (True, {"type": "disabled"}, "deepseek-v4-pro"))
        self.assertTrue(body["system"].startswith("SYSTEM\n\n请只输出一个 JSON 对象"))
        self.assertEqual(body["messages"], [{"role": "user", "content": "在吗"}])
        # The next turn in the same session carries the earlier one.
        again = dsh_stream.run(spec, Job(text="再说", session_id=outcome.session_id, plain=True,
                                         system="S"), {"DEEPSEEK_API_KEY": "k"}, "m",
                               lambda text: None, opener)
        self.assertEqual(again.session_id, outcome.session_id)
        self.assertEqual([item["role"] for item in opener.requests[1][1]["messages"]],
                         ["user", "assistant", "user"])

    def test_unknown_session_ids_start_fresh(self):
        opener = FakeOpener(sse('{"reply": "嗯"}'))
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text="x", session_id="session-dsh",
                                                            plain=True, system="S"),
                                 {"DEEPSEEK_API_KEY": "k"}, "m", lambda text: None, opener)
        self.assertNotEqual(outcome.session_id, "session-dsh")
        self.assertEqual(len(opener.requests[0][1]["messages"]), 1)

    def test_history_is_trimmed_from_the_oldest_turn(self):
        history = []
        for index in range(30):
            history += [{"role": "user", "content": "q%d" % index},
                        {"role": "assistant", "content": "a%d" % index}]
        kept = dsh_stream.trim(history)
        self.assertEqual(len(kept), dsh_stream.MAX_HISTORY_MESSAGES)
        self.assertEqual(kept[0], {"role": "user", "content": "q10"})

    def test_a_cut_stream_is_an_error(self):
        with self.assertRaises(Exception):
            dsh_stream.run(dsh_spec(self.folder), Job(text="x", plain=True),
                           {"DEEPSEEK_API_KEY": "k"}, "m", lambda text: None,
                           FakeOpener(sse('{"reply": "半', stop=False)))


def tool_sse(text, tool, args, stop_reason="tool_use"):
    """A stream that says text, then calls one tool with args."""
    lines = []
    if text:
        lines += ['data: ' + json.dumps({"type": "content_block_start", "index": 0,
                                         "content_block": {"type": "text", "text": ""}}),
                  'data: ' + json.dumps({"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": text}})]
    lines += ['data: ' + json.dumps({"type": "content_block_start", "index": 1, "content_block": {
                  "type": "tool_use", "id": "tool-1", "name": tool, "input": {}}}),
              'data: ' + json.dumps({"type": "content_block_delta", "index": 1, "delta": {
                  "type": "input_json_delta", "partial_json": json.dumps(args, ensure_ascii=False)}}),
              'data: ' + json.dumps({"type": "message_delta", "delta": {"stop_reason": stop_reason}}),
              'data: {"type":"message_stop"}']
    return ("\n".join(lines) + "\n").encode("utf-8")


class ToolTalkTests(TempDirCase):
    """说的话直接写、做事调工具：一轮里可以说话、调工具、看结果、接着说。"""

    def toolbox(self, problem=None):
        from xiaoyu_runtime.loop.voice_tools import Toolbox
        return Toolbox(["codex"], [], [], lambda fields: problem,
                       lambda card: "c3 · 正在做" if card == "c3" else None)

    def test_clock_context_survives_tool_round_without_entering_saved_history(self):
        spec = dsh_spec(self.folder)
        opener = FakeOpener(tool_sse('', 'task_detail', {'card': 'c3'}), sse('还在做'))
        system = '## 本轮上下文\n现在是 2026-11-04 16:06（周三）。'
        outcome = dsh_stream.run(spec, Job(text='进度呢', system=system, tools=self.toolbox()),
                                 {'DEEPSEEK_API_KEY': 'k'}, 'deepseek-flash', lambda text: None, opener)
        for _, request in opener.requests:
            self.assertIn('当前本地时间 2026-11-04 16:06', request['messages'][0]['content'])
        history = dsh_stream._load(dsh_stream.sessions_dir(spec), outcome.session_id)
        self.assertEqual(history[0]['content'], '进度呢')

    def test_cut_before_first_word_keeps_input_without_fabricated_speech(self):
        cut = Cut()
        opener = FakeOpener(sse('unseen'), sse('answer'))
        def cut_open(request, timeout=None):
            response = opener(request, timeout=timeout)
            cut.set()
            return response
        spec = dsh_spec(self.folder)
        first = dsh_stream.run(spec, Job(text='等我补充', system='S', plain=True,
                                        tools=self.toolbox(), interrupt=cut),
                               {'DEEPSEEK_API_KEY': 'k'}, 'deepseek-flash', lambda text: None, cut_open)
        history = dsh_stream._load(dsh_stream.sessions_dir(spec), first.session_id)
        self.assertEqual(history[-2]['content'], '等我补充')
        self.assertEqual(history[-1]['content'], '（主人发来了新消息，这一轮尚未回复。）')
        dsh_stream.run(spec, Job(text='后半句', system='S', plain=True, tools=self.toolbox(),
                                 session_id=first.session_id),
                       {'DEEPSEEK_API_KEY': 'k'}, 'deepseek-flash', lambda text: None, opener)
        contents = [item['content'] for item in opener.requests[1][1]['messages']]
        self.assertEqual(contents[-3:], ['等我补充', '（主人发来了新消息，这一轮尚未回复。）', '后半句'])

    def test_she_talks_calls_a_tool_and_goes_on(self):
        # 先交出去、还没开口：看了工具结果再说一句，那句话就是这一轮的回复。
        opener = FakeOpener(tool_sse("", "start_task",
                                     {"agent": "codex", "title": "查日志", "task": "看看日志"}),
                            sse("交给 codex 了，有结果我告诉你。"))
        tools = self.toolbox()
        seen = []
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text="查一下", system="S", plain=True,
                                 on_text=seen.append, tools=tools), {"DEEPSEEK_API_KEY": "k"},
                                 "deepseek-v4-pro", seen.append, opener)
        self.assertEqual(outcome.text, "交给 codex 了，有结果我告诉你。")
        self.assertEqual(outcome.fields["action"], {"type": "start", "agent": "codex", "title": "查日志", "task": "看看日志"})
        self.assertEqual(outcome.fields["mood"], "busy")
        first, second = opener.requests[0][1], opener.requests[1][1]
        self.assertIn("tools", first)
        self.assertNotIn("JSON Schema", first.get("system", ""))
        result = second["messages"][-1]["content"][0]
        self.assertEqual((result["type"], result["tool_use_id"]), ("tool_result", "tool-1"))
        self.assertIn("说完就去办", result["content"])
        self.assertEqual(seen[-1], "交给 codex 了，有结果我告诉你。")

    def test_having_said_it_and_handed_it_off_she_does_not_say_it_again(self):
        # 交之前已经跟主人说了：交出去就到此为止，不再问一轮（那一轮多半把同一句话再说一遍）。
        opener = FakeOpener(tool_sse("好，我让 codex 去查。", "start_task",
                                     {"agent": "codex", "title": "查日志", "task": "看看日志"}),
                            sse("好嘞，已经让 codex 去查啦。"))
        seen = []
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text="查一下", system="S", plain=True,
                                 on_text=seen.append, tools=self.toolbox()), {"DEEPSEEK_API_KEY": "k"},
                                 "deepseek-v4-pro", seen.append, opener)
        self.assertEqual(outcome.text, "好，我让 codex 去查。")
        self.assertEqual(outcome.fields["action"]["type"], "start")
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(seen[-1], "好，我让 codex 去查。")
        # 会话里记着工具调用和结果，下一轮她知道这件事已经交出去了。
        saved = dsh_stream._load(dsh_stream.sessions_dir(dsh_spec(self.folder)), outcome.session_id)
        kinds = [block["type"] for item in saved if isinstance(item["content"], list) for block in item["content"]]
        self.assertEqual(kinds[-2:], ["tool_use", "tool_result"])
        # 查看类工具不算交出去：看完接着说。
        opener = FakeOpener(tool_sse("我看一眼。", "task_detail", {"card": "c3"}), sse("还在做。"))
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text="c3 呢", system="S", plain=True,
                                 on_text=print, tools=self.toolbox()), {"DEEPSEEK_API_KEY": "k"},
                                 "deepseek-v4-pro", lambda text: None, opener)
        self.assertEqual(outcome.text, "我看一眼。\n\n---\n\n还在做。")

    def test_read_only_tool_releases_first_bubble_before_reading(self):
        from xiaoyu_runtime.loop.voice_tools import JOIN, Toolbox
        seen = []
        def lookup(card):
            self.assertEqual(seen[-1], "我看一眼。" + JOIN)
            return "c3 · 正在做"
        tools = Toolbox(["codex"], [], [], lambda fields: None, lookup)
        opener = FakeOpener(tool_sse("我看一眼。", "task_detail", {"card": "c3"}), sse("还在做。"))
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text="c3", system="S", plain=True,
                                 on_text=seen.append, tools=tools), {"DEEPSEEK_API_KEY": "k"},
                                 "deepseek-v4-pro", seen.append, opener)
        self.assertEqual(outcome.text, "我看一眼。" + JOIN + "还在做。")

    def test_recent_chat_read_announces_before_tool_even_if_model_is_silent(self):
        from xiaoyu_runtime.loop.voice_tools import JOIN, Toolbox
        seen = []
        def recent(channel, limit):
            self.assertEqual(seen[-1], '我看看' + JOIN)
            return {'渠道': channel, '记录': [{'内容': '真实记录'}]}
        tools = Toolbox([], [], [], lambda fields: None, lambda card: None,
                        recent_chat=recent, announce_reads=True)
        opener = FakeOpener(tool_sse('', 'read_recent_chat', {'channel': 'weixin'}), sse('查到了。'))
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text='看看微信', system='S', plain=True,
                                 on_text=seen.append, tools=tools), {'DEEPSEEK_API_KEY': 'k'},
                                 'deepseek-v4-pro', seen.append, opener)
        self.assertEqual(outcome.text, '我看看' + JOIN + '查到了。')

    def test_background_handoff_announces_attempt_before_selecting_task(self):
        from xiaoyu_runtime.loop.voice_tools import JOIN, Toolbox
        seen = []
        def check(fields):
            self.assertEqual(seen[-1], '我处理一下' + JOIN)
            return None
        tools = Toolbox(['codex'], [], [], check, lambda card: None, announce_reads=True)
        opener = FakeOpener(tool_sse('', 'start_task', {'agent': 'codex', 'title': '查', 'task': '查一下'}))
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text='帮我查', system='S', plain=True,
                                 on_text=seen.append, tools=tools), {'DEEPSEEK_API_KEY': 'k'},
                                 'deepseek-v4-pro', seen.append, opener)
        self.assertEqual(outcome.text, '我处理一下')
        self.assertEqual(tools.action['type'], 'start')

    def test_model_handoff_notice_is_closed_before_selecting_task(self):
        from xiaoyu_runtime.loop.voice_tools import JOIN, Toolbox
        seen = []
        def check(fields):
            self.assertEqual(seen[-1], '我来安排。' + JOIN)
            return None
        tools = Toolbox(['codex'], [], [], check, lambda card: None, announce_reads=True)
        opener = FakeOpener(tool_sse('我来安排。', 'start_task', {'agent': 'codex', 'title': '查', 'task': '查一下'}))
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text='帮我查', system='S', plain=True,
                                 on_text=seen.append, tools=tools), {'DEEPSEEK_API_KEY': 'k'},
                                 'deepseek-v4-pro', seen.append, opener)
        self.assertEqual(outcome.text, '我来安排。')

    def test_handoff_success_claim_is_not_closed_before_selection(self):
        from xiaoyu_runtime.loop.voice_tools import JOIN, Toolbox
        seen = []
        def check(fields):
            self.assertFalse(seen[-1].endswith(JOIN))
            return None
        tools = Toolbox(['codex'], [], [], check, lambda card: None, announce_reads=True)
        opener = FakeOpener(tool_sse('已经安排好了。', 'start_task', {'agent': 'codex', 'title': '查', 'task': '查一下'}))
        dsh_stream.run(dsh_spec(self.folder), Job(text='帮我查', system='S', plain=True,
                       on_text=seen.append, tools=tools), {'DEEPSEEK_API_KEY': 'k'},
                       'deepseek-v4-pro', seen.append, opener)

    def test_handoff_notice_does_not_claim_rejected_task_was_started(self):
        from xiaoyu_runtime.loop.voice_tools import JOIN, Toolbox
        seen = []
        tools = Toolbox(['codex'], [], [], lambda fields: '执行端不可用', lambda card: None, announce_reads=True)
        opener = FakeOpener(tool_sse('', 'start_task', {'agent': 'codex', 'title': '查', 'task': '查一下'}),
                            sse('这次执行端不可用。'))
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text='帮我查', system='S', plain=True,
                                 on_text=seen.append, tools=tools), {'DEEPSEEK_API_KEY': 'k'},
                                 'deepseek-v4-pro', seen.append, opener)
        self.assertEqual(outcome.text, '我处理一下' + JOIN + '这次执行端不可用。')
        self.assertIsNone(tools.action)
        self.assertIn('我处理一下' + JOIN, seen)

    def test_mixed_read_and_handoff_announces_before_either_tool(self):
        from xiaoyu_runtime.loop.voice_tools import JOIN, Toolbox
        seen = []
        def observed(value):
            self.assertEqual(seen[-1], '我处理一下' + JOIN)
            return None
        def read(channel, limit):
            observed(channel)
            return {'渠道': channel, '记录': []}
        tools = Toolbox(['codex'], [], [], observed, lambda card: None, recent_chat=read, announce_reads=True)
        events = []
        for index, name, args in [(1, 'read_recent_chat', {'channel': 'weixin'}),
                                  (2, 'start_task', {'agent': 'codex', 'title': '查', 'task': '查一下'})]:
            events.append({'type': 'content_block_start', 'index': index,
                           'content_block': {'type': 'tool_use', 'id': 'tool-%d' % index, 'name': name, 'input': {}}})
            events.append({'type': 'content_block_delta', 'index': index,
                           'delta': {'type': 'input_json_delta', 'partial_json': json.dumps(args)}})
        events.extend([{'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}}, {'type': 'message_stop'}])
        body = ('\n'.join('data: ' + json.dumps(event) for event in events) + '\n').encode()
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text='查一下', system='S', plain=True,
                                 on_text=seen.append, tools=tools), {'DEEPSEEK_API_KEY': 'k'},
                                 'deepseek-v4-pro', seen.append, FakeOpener(body))
        self.assertEqual(outcome.text, '我处理一下')
        self.assertEqual(tools.action['type'], 'start')

    def test_four_tool_rounds_do_not_make_a_fifth_request(self):
        answer = tool_sse("", "task_detail", {"card": "c3"})
        opener = FakeOpener(*([answer] * 4))
        seen = []
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text="c3", system="S", plain=True,
                                 on_text=seen.append, tools=self.toolbox()), {"DEEPSEEK_API_KEY": "k"},
                                 "deepseek-v4-pro", seen.append, opener)
        self.assertEqual(len(opener.requests), 4)
        self.assertIn("上限", outcome.text)
        self.assertEqual(seen[-1], outcome.text)

    def test_batch_reads_are_bounded_and_repeat_session_query_once(self):
        from xiaoyu_runtime.loop.voice_tools import Toolbox
        seen = []
        def sessions(query):
            seen.append(query)
            return "session result"
        tools = Toolbox(["codex"], [], [], lambda fields: None, lambda card: "task " + card,
                        recall=lambda query: "memory " + query, session_search=sessions)
        body = {"queries": [{"name": "find_sessions", "args": {"query": "project"}},
                            {"name": "task_detail", "args": {"card": "c3"}},
                            {"name": "find_sessions", "args": {"query": "project"}},
                            {"name": "recall", "args": {"query": "x"}}]}
        result, failed = tools.call("read_context", body)
        self.assertFalse(failed)
        self.assertEqual(len(json.loads(result)), 4)
        self.assertEqual(seen, ["project"])
        _, failed = tools.call("read_context", {"queries": [{"name": "start_task", "args": {}}]})
        self.assertTrue(failed)

    def test_a_refused_action_comes_back_in_the_same_turn(self):
        opener = FakeOpener(tool_sse("", "start_task", {"agent": "codex", "title": "x", "task": "y"}),
                            sse("这件事我办不了。"))
        tools = self.toolbox(problem="没有这个帮手")
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text="做", system="S", plain=True,
                                 on_text=print, tools=tools), {"DEEPSEEK_API_KEY": "k"},
                                 "deepseek-v4-pro", lambda text: None, opener)
        result = opener.requests[1][1]["messages"][-1]["content"][0]
        self.assertTrue(result["is_error"])
        self.assertIn("没有这个帮手", result["content"])
        self.assertEqual(outcome.fields["action"], {"type": "none"})

    def test_looking_at_a_task_files_the_reply_under_it(self):
        opener = FakeOpener(tool_sse("我看一眼。", "task_detail", {"card": "c3"}), sse("还在做。"))
        tools = self.toolbox()
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text="c3 呢", system="S", plain=True,
                                 on_text=print, tools=tools), {"DEEPSEEK_API_KEY": "k"},
                                 "deepseek-v4-pro", lambda text: None, opener)
        self.assertEqual(opener.requests[1][1]["messages"][-1]["content"][0]["content"], "c3 · 正在做")
        self.assertEqual((outcome.fields["card"], outcome.fields["action"]["type"]), ("c3", "none"))

    def test_a_promise_without_a_tool_is_spoken_once(self):
        # 说了要去做却没调工具：不再暗中追问，只调用一次模型、只给出这一段话。
        opener = FakeOpener(sse("我这就去把这几天的事拢一拢。"), sse("不该被问到的第二轮。"))
        tools = self.toolbox()
        self.assertTrue(tools.promised("我这就去把这几天的事拢一拢。"))
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text="这几天做了什么", system="S", plain=True,
                                 on_text=print, tools=tools), {"DEEPSEEK_API_KEY": "k"},
                                 "deepseek-v4-pro", lambda text: None, opener)
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(outcome.text, "我这就去把这几天的事拢一拢。")
        self.assertEqual(outcome.fields["action"], {"type": "none"})
        saved = dsh_stream._load(dsh_stream.sessions_dir(dsh_spec(self.folder)), outcome.session_id)
        owner_turns = [item["content"] for item in saved if item["role"] == "user" and isinstance(item["content"], str)]
        self.assertEqual(owner_turns, ["这几天做了什么"])

    def test_tool_sessions_are_separate_and_keep_the_tool_calls(self):
        spec = dsh_spec(self.folder)
        old = "api-" + "1" * 32
        folder = dsh_stream.sessions_dir(spec)
        dsh_stream._save(folder, old, [{"role": "user", "content": "hi"},
                                       {"role": "assistant", "content": '{"reply": "交给 cc 了"}'}])
        opener = FakeOpener(tool_sse("", "task_detail", {"card": "c3"}), sse("还在做。"))
        outcome = dsh_stream.run(spec, Job(text="c3 呢", system="S", plain=True, session_id=old,
                                 on_text=print, tools=self.toolbox()), {"DEEPSEEK_API_KEY": "k"},
                                 "deepseek-v4-pro", lambda text: None, opener)
        # 不接写 JSON 时期的旧会话：新开一个 apt- 会话。
        self.assertTrue(outcome.session_id.startswith("apt-"))
        self.assertEqual(len(opener.requests[0][1]["messages"]), 1)
        saved = dsh_stream._load(folder, outcome.session_id)
        kinds = [block["type"] for item in saved if isinstance(item["content"], list) for block in item["content"]]
        self.assertIn("tool_use", kinds)
        self.assertIn("tool_result", kinds)
        self.assertEqual(saved[-1], {"role": "assistant", "content": "还在做。"})

    def test_saying_she_will_remember_without_remembering_is_counted(self):
        from xiaoyu_runtime.world.memory import OwnerMemory
        from xiaoyu_runtime.loop.voice_tools import Toolbox
        memory = OwnerMemory(self.folder / "owner.md")
        tools = Toolbox(["codex"], [], [], lambda fields: None, lambda card: None, memory)
        self.assertTrue(tools.promised("明白，这个我记下了，后面按这个来。"))
        # 查过一件事不算记住了。
        tools.call("task_detail", {"card": "c3"})
        self.assertTrue(tools.promised("记下了"))
        tools.call("remember", {"note": "少说点"})
        self.assertFalse(tools.promised("记下了"))
        self.assertEqual(memory.notes()[-1][-3:], "少说点")
        # 没有记忆功能时不计这一笔。
        bare = Toolbox(["codex"], [], [], lambda fields: None, lambda card: None)
        self.assertFalse(bare.promised("记下了"))

    def test_she_can_remember_and_forget_through_tools(self):
        opener = FakeOpener(tool_sse("", "remember", {"note": "回复别太长"}), sse("好，记住了。"))
        from xiaoyu_runtime.world.memory import OwnerMemory
        from xiaoyu_runtime.loop.voice_tools import Toolbox
        memory = OwnerMemory(self.folder / "owner.md")
        tools = Toolbox(["codex"], [], [], lambda fields: None, lambda card: None, memory)
        names = [tool["name"] for tool in tools.definitions()]
        self.assertIn("remember", names)
        dsh_stream.run(dsh_spec(self.folder), Job(text="以后少说点", system="S", plain=True,
                       on_text=print, tools=tools), {"DEEPSEEK_API_KEY": "k"},
                       "deepseek-v4-pro", lambda text: None, opener)
        self.assertTrue(memory.notes()[0].endswith("回复别太长"))
        self.assertEqual(len(opener.requests), 2)  # 调了 remember，不再提醒
        self.assertIn("删掉了", tools.call("forget", {"number": 1})[0])
        self.assertEqual(memory.notes(), [])

    def test_cutting_in_stops_the_reply_and_keeps_what_was_said(self):
        cut = threading.Event()
        opener = FakeOpener(sse("我这就去", "把这几天", "的事情拢一拢。"))
        seen = []

        def heard(text):
            seen.append(text)
            cut.set()  # 主人在她说第一块时就插话了

        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text="回顾一下", system="S", plain=True,
                                 on_text=heard, tools=self.toolbox(), interrupt=cut),
                                 {"DEEPSEEK_API_KEY": "k"}, "deepseek-v4-pro", heard, opener)
        self.assertEqual(outcome.text, "我这就去")
        self.assertEqual(len(opener.requests), 1)  # 不再提醒、不再接着说
        self.assertEqual(outcome.fields["action"], {"type": "none"})

    def test_a_cut_that_stops_the_stream_takes_effect(self):
        cut = Cut()
        opener = FakeOpener(sse("我这就去", "把这几天", "的事情拢一拢。"))

        def heard(text):
            cut.set()

        dsh_stream.run(dsh_spec(self.folder), Job(text="回顾一下", system="S", plain=True,
                       on_text=heard, tools=self.toolbox(), interrupt=cut),
                       {"DEEPSEEK_API_KEY": "k"}, "deepseek-v4-pro", heard, opener)
        self.assertTrue(cut.took_effect)

    def test_a_cut_after_the_stream_finished_has_no_effect(self):
        cut = Cut()
        opener = FakeOpener(sse("好的。"))
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text="回顾一下", system="S", plain=True,
                                 on_text=lambda text: None, tools=self.toolbox(), interrupt=cut),
                                 {"DEEPSEEK_API_KEY": "k"}, "deepseek-v4-pro", lambda text: None, opener)
        cut.set()
        self.assertEqual("好的。", outcome.text)
        self.assertFalse(cut.took_effect)

    def test_xiaoyu_interrupt_only_hits_a_reply_in_progress(self):
        xiaoyu, _ = make_xiaoyu(self.folder / "cut", [Scripted("claude")])
        self.addCleanup(xiaoyu.close)
        self.assertFalse(xiaoyu.interrupt("default"))
        cut = threading.Event()
        xiaoyu._speaking["default"] = cut
        self.assertTrue(xiaoyu.interrupt("default"))
        self.assertTrue(cut.is_set())
        self.assertFalse(xiaoyu.interrupt("default"))

    def test_old_json_replies_in_the_history_become_plain_words(self):
        history = [{"role": "user", "content": "hi"},
                   {"role": "assistant", "content": '{"reply": "你好", "brief": "b", "mood": "happy"}'}]
        self.assertEqual(dsh_stream.plain_history(history)[1]["content"], "你好")


class DshAgentStreamTests(TempDirCase):
    def headless(self, said):
        def run(command, **extra):
            self.commands.append(command)
            lines = [json.dumps({"type": "session", "sessionId": "session-dsh"}),
                     json.dumps({"type": "final", "text": said})]
            return subprocess.CompletedProcess(command, 0, "\n".join(lines) + "\n", "")
        return run

    def setUp(self):
        super().setUp()
        self.commands = []

    def test_speaking_with_a_listener_streams(self):
        opener = FakeOpener(sse('{"reply": "流', '式"}'))
        agent = DshAgent(dsh_spec(self.folder), run=self.headless("unused"), opener=opener)
        seen = []
        outcome = agent.run(Job(text="hi", system="S", plain=True, on_text=seen.append))
        self.assertEqual((outcome.fields["reply"], len(seen), self.commands), ("流式", 2, []))

    def test_falls_back_to_dsh_when_the_stream_fails(self):
        opener = FakeOpener(urllib.error.URLError("offline"))
        agent = DshAgent(dsh_spec(self.folder), run=self.headless('{"reply": "老路"}'),
                         opener=opener)
        seen = []
        outcome = agent.run(Job(text="hi", system="S", plain=True, session_id="api-" + "0" * 32,
                                on_text=seen.append))
        self.assertEqual(outcome.fields["reply"], "老路")
        self.assertEqual(seen, [""])
        # dsh never saw the stream's own session id.
        self.assertNotIn("--session-id", self.commands[0])

    def test_old_image_does_not_block_fallback_for_unrelated_text(self):
        spec = dsh_spec(self.folder)
        session = "api-" + "a" * 32
        dsh_stream._save(dsh_stream.sessions_dir(spec), session, [
            {"role": "user", "content": "看图", "_image_refs": ["owner-id"]},
            {"role": "assistant", "content": "看到了"},
            {"role": "user", "content": "左边呢"}, {"role": "assistant", "content": "有字"},
            {"role": "user", "content": "右边呢"}, {"role": "assistant", "content": "也有字"}])
        agent = DshAgent(spec, opener=FakeOpener(urllib.error.URLError("offline")))
        with patch.object(agent, "_headless", return_value=Outcome("fallback", None)) as fallback:
            out = agent.run(Job(text="今天天气怎样", plain=True, session_id=session,
                                image_resolver=lambda _: None, on_text=lambda _: None))
        self.assertEqual(out.text, "fallback")
        fallback.assert_called_once()
        agent = DshAgent(spec, opener=FakeOpener(urllib.error.URLError("offline")))
        with patch.object(agent, "_headless") as fallback:
            with self.assertRaises(AgentError):
                agent.run(Job(text="图片里有什么", plain=True, session_id=session,
                              image_resolver=lambda _: None, on_text=lambda _: None))
        fallback.assert_not_called()

    def test_without_a_listener_or_with_stream_off_it_uses_dsh(self):
        for spec, listener in ((dsh_spec(self.folder), None),
                               (dsh_spec(self.folder, stream=False), print)):
            opener = FakeOpener()
            agent = DshAgent(spec, run=self.headless('{"reply": "dsh"}'), opener=opener)
            outcome = agent.run(Job(text="hi", system="S", plain=True, on_text=listener))
            self.assertEqual((outcome.fields["reply"], opener.requests), ("dsh", []))

    def test_config_turns_streaming_on_for_dsh_by_default(self):
        def load(**extra):
            agent = {"type": "dsh", "command": ["dsh"]}
            agent.update(extra)
            return config_module.load(write_config(self.folder, agents={"ds": agent}), {})
        self.assertTrue(load().agent("ds").stream)
        self.assertFalse(load(stream=False).agent("ds").stream)
        with self.assertRaises(config_module.ConfigError):
            load(stream="yes")
        self.assertIsNone(load().agent("ds").chat_model)
        self.assertEqual(load(chat_model="fast").agent("ds").chat_model, "fast")
        with self.assertRaises(config_module.ConfigError):
            load(chat_model=23)

    def test_chat_model_applies_only_to_plain_turns_and_respects_explicit_model(self):
        spec = dataclasses.replace(dsh_spec(self.folder), chat_model="fast")
        opener = FakeOpener(sse('{"reply":"hi"}'), sse('{"reply":"hi"}'))
        agent = DshAgent(spec, run=self.headless('{"reply":"work"}'), opener=opener)
        agent.run(Job(text="hi", system="S", plain=True, on_text=lambda text: None))
        self.assertEqual(opener.requests[0][1]["model"], "fast")
        agent.run(Job(text="hi", system="S", plain=True, model="explicit", on_text=lambda text: None))
        self.assertEqual(opener.requests[1][1]["model"], "explicit")
        with patch.object(agent, "_headless", return_value=Outcome("work", None)) as headless:
            agent.run(Job(text="work", plain=False))
            self.assertIsNone(headless.call_args[0][0].model)
            self.assertEqual(agent.tier(headless.call_args[0][0])[0], spec.model)

    def test_fallback_uses_only_the_remaining_budget(self):
        now = [0.0]

        def offline(request, timeout):
            now[0] = 7.3
            raise urllib.error.URLError("offline")

        agent = DshAgent(dsh_spec(self.folder), opener=offline)
        with patch.object(dsh_stream.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(agent, "_headless", return_value=Outcome("fallback", None)) as headless:
            agent.run(Job(text="test", plain=True, timeout=10, on_text=lambda text: None))
        self.assertAlmostEqual(headless.call_args[0][0].timeout, 2.7)

    def test_expired_stream_does_not_start_another_full_wait(self):
        now = [0.0]

        def expired(request, timeout):
            now[0] = 10.1
            raise urllib.error.URLError("timeout")

        agent = DshAgent(dsh_spec(self.folder), opener=expired)
        with patch.object(dsh_stream.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(agent, "_headless") as headless:
            with self.assertRaisesRegex(AgentError, "总时限"):
                agent.run(Job(text="test", plain=True, timeout=10, on_text=lambda text: None))
        headless.assert_not_called()

    def test_fractional_fallback_budget_is_never_rounded_up(self):
        now = [0.0]
        def offline(request, timeout):
            now[0] = 9.8
            raise urllib.error.URLError("offline")
        agent = DshAgent(dsh_spec(self.folder), opener=offline)
        with patch.object(dsh_stream.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(agent, "_headless", return_value=Outcome("fallback", None)) as headless:
            agent.run(Job(text="test", plain=True, timeout=10, on_text=lambda text: None))
        self.assertAlmostEqual(.2, headless.call_args[0][0].timeout)

    def test_stale_headless_speech_session_shares_the_original_deadline(self):
        for spent in (7.3, 10.1):
            with self.subTest(spent=spent):
                now, seen = [0.0], []
                def run(command, **kwargs):
                    seen.append(kwargs["timeout"])
                    if len(seen) == 1:
                        now[0] = spent
                        return subprocess.CompletedProcess(command, 1, '{"type":"error","message":"session does not exist"}', "")
                    return subprocess.CompletedProcess(command, 0, '{"type":"final","text":"answer"}', "")
                agent = DshAgent(dsh_spec(self.folder, stream=False), run=run)
                with patch.object(dsh_stream.time, "monotonic", side_effect=lambda: now[0]):
                    if spent > 10:
                        with self.assertRaises(AgentError):
                            agent.run(Job(text="test", plain=True, timeout=10, session_id="old"))
                        self.assertEqual(1, len(seen))
                    else:
                        self.assertEqual("answer", agent.run(Job(text="test", plain=True, timeout=10, session_id="old")).text)
                        self.assertAlmostEqual(2.7, seen[1])


class Streaming(Scripted):
    """Says its reply in pieces through on_text, pausing until told to finish."""

    def __init__(self, name):
        super().__init__(name)
        self.pieces = []
        self.finish = threading.Event()

    def run(self, job):
        if job.on_text is not None and self.pieces:
            raw = ""
            for piece in self.pieces:
                raw += piece
                job.on_text(raw)
                time.sleep(xiaoyu_module.PARTIAL_SECONDS + 0.02)
            self.finish.wait(5)
        return super().run(job)


class ServiceStreamTests(TempDirCase):
    def test_notice_is_visible_while_recent_chat_tool_is_still_running(self):
        started, release = threading.Event(), threading.Event()
        def slow_read(channel, limit):
            started.set()
            if not release.wait(5):
                raise RuntimeError('test read did not resume')
            return {'渠道': channel, '记录': [{'内容': '最近记录'}]}
        opener = FakeOpener(tool_sse('', 'read_recent_chat', {'channel': 'weixin'}), sse('读完了。'))
        agent = DshAgent(dsh_spec(self.folder), opener=opener)
        xiaoyu, store = make_xiaoyu(self.folder, [agent])
        self.addCleanup(xiaoyu.close)
        service = Service(xiaoyu, store)
        self.addCleanup(service.close)
        self.addCleanup(release.set)
        with patch.object(xiaoyu.context, 'recent_chat', side_effect=slow_read):
            item = service.submit('请读取另一边最近消息')
            self.assertTrue(started.wait(5))
            running = service.get(item['id'])
            self.assertEqual(running['status'], 'running')
            self.assertIn('我看看', running['partial'])
            release.set()
            done = service.get(item['id'], wait=5)
            self.assertEqual(done['status'], 'done')
            self.assertIn('读完了', done['reply'])
    def test_busy_fallback_is_varied_stable_and_reaches_failed_conversation(self):
        self.assertGreaterEqual(len(set(PROVIDER_BUSY_REPLIES)), 8)
        self.assertEqual(_provider_busy_reply("turn"), _provider_busy_reply("turn"))
        failure = urllib.error.HTTPError("https://fixture.invalid", 503, "busy", {},
                                         io.BytesIO(b'{"request_id":"private-id"}'))
        opener = FakeOpener(failure)
        agent = DshAgent(dsh_spec(self.folder), opener=opener)
        xiaoyu, store = make_xiaoyu(self.folder, [agent])
        service = Service(xiaoyu, store)
        self.addCleanup(service.close)
        with patch.object(agent, "_headless") as headless:
            item = service.submit("你好")
            result = service.get(item["id"], wait=5)
        self.assertEqual(result["status"], "failed")
        self.assertIn(result["error"], PROVIDER_BUSY_REPLIES)
        self.assertEqual(len(opener.requests), 1)
        headless.assert_not_called()
        self.assertNotIn("private-id", result["error"])
        card = xiaoyu.cards.get(result["card"])
        self.assertEqual(card["entries"][-1]["text"], result["error"])

    def test_busy_after_partial_keeps_existing_bubble_and_adds_fallback(self):
        from xiaoyu_runtime.events.message_parts import parts
        class PartialBusy(ToolTalker):
            def run(self, job):
                job.on_text("先说一句。\n\n---\n\n")
                raise dsh_stream.ProviderBusy("private provider detail")
        agent = PartialBusy()
        agent.type = "dsh"
        xiaoyu, store = make_xiaoyu(self.folder, [agent])
        service = Service(xiaoyu, store)
        self.addCleanup(service.close)
        item = service.submit("你好")
        result = service.get(item["id"], wait=5)
        self.assertEqual(result["status"], "failed")
        blocks = parts(result["error"], True)
        self.assertEqual(blocks[0][0], "先说一句。")
        self.assertIn(blocks[1][0], PROVIDER_BUSY_REPLIES)
        self.assertNotIn("private provider detail", result["error"])

    def test_busy_fallback_is_delivered_once_to_the_originating_weixin_turn(self):
        from xiaoyu_runtime.channels.gateway import Gateway
        from xiaoyu_runtime.channels.store import ChannelStore
        failure = urllib.error.HTTPError("https://fixture.invalid", 503, "busy", {}, io.BytesIO(b"secret"))
        opener = FakeOpener(failure)
        xiaoyu, store = make_xiaoyu(self.folder, [DshAgent(dsh_spec(self.folder), opener=opener)])
        service = Service(xiaoyu, store)
        self.addCleanup(service.close)
        service._owner_writer.conversation_create_weixin()
        channel = ChannelStore(self.folder / "channel.sqlite")
        self.addCleanup(channel.close)
        gateway = Gateway(channel, service, "wx", "bot", "owner")
        gateway.bootstrap_sync("owner", service.sync("weixin", states=["weixin"]))
        gateway.ingest([{"id": "in-1", "sender": "owner", "payload": "你好", "context_token": "token"}], "1")
        self.assertEqual(gateway.submit_pending(), 1)
        request = channel.db.execute("SELECT runtime_id FROM inbox").fetchone()[0]
        result = service.get(request, wait=5)
        self.assertEqual(result["status"], "failed")
        _, epoch, seq = json.loads(channel.cursor("sync:owner"))
        gateway.apply_sync("owner", service.sync("weixin", after=seq, epoch=epoch, states=["weixin"]))
        class Transport:
            def __init__(self): self.sent = []
            def send_text(self, *args):
                self.sent.append(args[-1])
                return {"ret": 0}
        transport = Transport()
        with channel.db:
            channel.db.execute("UPDATE inbox SET received_at=? WHERE message_id='in-1'", (time.time(),))
        self.assertEqual(gateway.send_next(transport, "owner"), "paced")
        with channel.db:
            channel.db.execute("UPDATE inbox SET received_at=0 WHERE message_id='in-1'")
        self.assertEqual(gateway.send_next(transport, "owner"), "sent")
        self.assertEqual(transport.sent, [result["error"]])
        self.assertEqual(gateway.send_next(transport, "owner"), "idle")
        self.assertEqual(len(opener.requests), 1)

    def test_durable_timing_tracks_the_same_client_and_server_ids(self):
        from xiaoyu_runtime import timing as turn_timing
        agent = Streaming("ds")
        agent.pieces = ['{"reply": "private-answer"}']
        agent.script = [{"reply": "private-answer", "brief": "b", "mood": "idle"}]
        xiaoyu, store = make_xiaoyu(self.folder, [agent])
        service = Service(xiaoyu, store)
        self.addCleanup(service.close)
        self.addCleanup(agent.finish.set)
        client = "c" * 32
        message = service.submit("private-question", client_id=client)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not service.get(message["id"])["partial"]:
            time.sleep(.01)
        agent.finish.set()
        while time.monotonic() < deadline and service.get(message["id"])["status"] != "done":
            time.sleep(.01)
        self.assertEqual(service.get(message["id"])["status"], "done")
        # _handle emits its end marker immediately after committing the result.
        while time.monotonic() < deadline:
            turn_timing._QUEUE.join()
            if service._timing.path.exists() and 'runtime_processing_end' in service._timing.path.read_text():
                break
            time.sleep(.01)
        data = service._timing.path.read_text()
        records = [json.loads(line) for line in data.splitlines()]
        self.assertEqual([r["phase"] for r in records], ["runtime_received", "runtime_accepted",
            "runtime_started", "runtime_partial", "runtime_processing_end"])
        self.assertTrue(all(r["client_id"] == client for r in records))
        self.assertTrue(all(r.get("request_id") == message["id"] for r in records[1:]))
        self.assertNotIn("private-question", data)
        self.assertNotIn("private-answer", data)

    def test_the_message_carries_the_reply_while_it_is_written(self):
        agent = Streaming("ds")
        agent.pieces = ['{"reply": "我', '在想', '这件事"', ', "brief": "b"}']
        agent.script = [{"reply": "我在想这件事", "brief": "b", "mood": "idle"}]
        xiaoyu, store = make_xiaoyu(self.folder, [agent])
        service = Service(xiaoyu, store)
        self.addCleanup(service.close)
        self.addCleanup(agent.finish.set)
        message = service.submit("hi")
        seen = []
        rev = message["rev"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (not seen or seen[-1] != "我在想这件事"):
            current = service.get(message["id"], wait=2, rev=rev)
            rev = current["rev"]
            if current["partial"] and (not seen or seen[-1] != current["partial"]):
                seen.append(current["partial"])
        self.assertEqual(seen[0], "我")
        self.assertEqual(seen[-1], "我在想这件事")
        self.assertEqual(service.get(message["id"])["status"], "running")
        agent.finish.set()
        done = service.get(message["id"], wait=5)
        self.assertEqual((done["status"], done["reply"], done["partial"]),
                         ("done", "我在想这件事", ""))
        self.assertEqual(xiaoyu.cards.get(done["card"])["entries"][-1]["text"], "我在想这件事")
        entries = xiaoyu.cards.get(done["card"])["entries"]
        self.assertEqual([entry["id"] for entry in entries], [done["id"] + ":you", done["id"] + ":reply"])
        self.assertTrue(all(entry["scope"] == "main" for entry in entries))

    def test_http_stream_delivers_partial_then_final_and_polling_same_message(self):
        agent = Streaming("ds")
        agent.pieces = ['{"reply": "我', '在想"}']
        agent.script = [{"reply": "我在想", "brief": "b", "mood": "idle"}]
        xiaoyu, store = make_xiaoyu(self.folder, [agent])
        service = Service(xiaoyu, store)
        config = dataclasses.replace(xiaoyu._config, host="127.0.0.1", port=0)
        server = make_server(config, service)
        server.RequestHandlerClass.log_message = lambda *args: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(service.close)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(agent.finish.set)
        message = service.submit("hi", client_id="stream-test")
        base = "http://127.0.0.1:%d" % server.server_address[1]
        request = urllib.request.Request(base + "/v1/messages/" + message["id"] + "/stream",
                                         headers={"Authorization": "Bearer " + config.token})
        snapshots = []
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertIn("text/event-stream", response.headers["Content-Type"])
            while True:
                line = response.readline()
                if not line:
                    break
                if not line.startswith(b"data: "):
                    continue
                snapshot = json.loads(line[6:])
                snapshots.append(snapshot)
                if snapshot["partial"] == "我在想":
                    agent.finish.set()
                if snapshot["status"] in ("done", "failed"):
                    break
        self.assertTrue(any(item["partial"] for item in snapshots))
        self.assertEqual((snapshots[-1]["status"], snapshots[-1]["reply"]), ("done", "我在想"))
        self.assertTrue(all(item["id"] == message["id"] for item in snapshots))
        self.assertEqual(service.get(message["id"]), snapshots[-1])
        self.assertEqual(service.submit("hi", client_id="stream-test")["id"], message["id"])
        for token, expected in (("wrong-token", 401), (config.token, 404)):
            request = urllib.request.Request(base + "/v1/messages/missing/stream",
                                             headers={"Authorization": "Bearer " + token})
            with self.assertRaises(urllib.error.HTTPError) as failure:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(failure.exception.code, expected)

    def test_a_retried_reply_takes_back_what_was_shown(self):
        said = []
        stream = xiaoyu_module.Xiaoyu._streamer(
            "ds", lambda kind, agent, text: said.append((kind, text)))
        stream('{"reply": "要删')
        stream("")
        stream("")
        self.assertEqual(said, [("partial", "要删"), ("partial", "")])


if __name__ == "__main__":
    unittest.main()
