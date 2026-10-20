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

from test_runtime import Scripted, TempDirCase, make_xiaoyou, write_config  # noqa: E402
from xiaoyou_runtime import config as config_module  # noqa: E402
from xiaoyou_runtime import dsh_stream  # noqa: E402
from xiaoyou_runtime import xiaoyou as xiaoyou_module  # noqa: E402
from xiaoyou_runtime.agents import AgentError, DshAgent, Job, Outcome  # noqa: E402
from xiaoyou_runtime.service import Service  # noqa: E402
from xiaoyou_runtime.server import make_server  # noqa: E402
from xiaoyou_runtime.xiaoyou import reply_so_far  # noqa: E402


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
        from xiaoyou_runtime.voice_tools import Toolbox
        return Toolbox(["codex"], [], [], lambda fields: problem,
                       lambda card: "c3 · 正在做" if card == "c3" else None)

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

    def test_a_promise_without_a_tool_gets_one_quiet_reminder(self):
        opener = FakeOpener(sse("我这就去把这几天的事拢一拢。"),
                            tool_sse("说得对，现在补上。", "start_task", {"agent": "codex", "title": "回顾", "task": "整理这几天做的事"}),
                            sse("交给 codex 了。"))
        tools = self.toolbox()
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text="这几天做了什么", system="S", plain=True,
                                 on_text=print, tools=tools), {"DEEPSEEK_API_KEY": "k"},
                                 "deepseek-v4-pro", lambda text: None, opener)
        reminder = opener.requests[1][1]["messages"][-1]["content"]
        self.assertIn("没调对应的工具", reminder)
        self.assertEqual(outcome.fields["action"]["type"], "start")
        # 补调了工具就到此为止：不再问一轮，也不把回应提醒的话给主人。
        self.assertEqual(outcome.text, "我这就去把这几天的事拢一拢。")
        self.assertEqual(len(opener.requests), 2)
        # 只提醒一次：说了不做第二次就照实给出去。
        opener = FakeOpener(sse("我这就去看看。"), sse("我去看一眼。"))
        outcome = dsh_stream.run(dsh_spec(self.folder), Job(text="看看", system="S", plain=True,
                                 on_text=print, tools=self.toolbox()), {"DEEPSEEK_API_KEY": "k"},
                                 "deepseek-v4-pro", lambda text: None, opener)
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(outcome.fields["action"], {"type": "none"})

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

    def test_saying_she_will_remember_without_remembering_gets_a_reminder(self):
        from xiaoyou_runtime.memory import OwnerMemory
        from xiaoyou_runtime.voice_tools import Toolbox
        memory = OwnerMemory(self.folder / "owner.md")
        tools = Toolbox(["codex"], [], [], lambda fields: None, lambda card: None, memory)
        self.assertTrue(tools.promised("明白，这个我记下了，后面按这个来。"))
        # 查过一件事不算记住了。
        tools.call("task_detail", {"card": "c3"})
        self.assertTrue(tools.promised("记下了"))
        tools.call("remember", {"note": "少说点"})
        self.assertFalse(tools.promised("记下了"))
        self.assertEqual(memory.notes()[-1][-3:], "少说点")
        # 没有记忆功能时不拿这个提醒她。
        bare = Toolbox(["codex"], [], [], lambda fields: None, lambda card: None)
        self.assertFalse(bare.promised("记下了"))

    def test_she_can_remember_and_forget_through_tools(self):
        opener = FakeOpener(tool_sse("", "remember", {"note": "回复别太长"}), sse("好，记住了。"))
        from xiaoyou_runtime.memory import OwnerMemory
        from xiaoyou_runtime.voice_tools import Toolbox
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

    def test_xiaoyou_interrupt_only_hits_a_reply_in_progress(self):
        xiaoyou, _ = make_xiaoyou(self.folder / "cut", [Scripted("claude")])
        self.addCleanup(xiaoyou.close)
        self.assertFalse(xiaoyou.interrupt("default"))
        cut = threading.Event()
        xiaoyou._speaking["default"] = cut
        self.assertTrue(xiaoyou.interrupt("default"))
        self.assertTrue(cut.is_set())
        self.assertFalse(xiaoyou.interrupt("default"))

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
        self.assertEqual(headless.call_args[0][0].timeout, 2)

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
                time.sleep(xiaoyou_module.PARTIAL_SECONDS + 0.02)
            self.finish.wait(5)
        return super().run(job)


class ServiceStreamTests(TempDirCase):
    def test_the_message_carries_the_reply_while_it_is_written(self):
        agent = Streaming("ds")
        agent.pieces = ['{"reply": "我', '在想', '这件事"', ', "brief": "b"}']
        agent.script = [{"reply": "我在想这件事", "brief": "b", "mood": "idle"}]
        xiaoyou, store = make_xiaoyou(self.folder, [agent])
        service = Service(xiaoyou, store)
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
        self.assertEqual(xiaoyou.cards.get(done["card"])["entries"][-1]["text"], "我在想这件事")
        entries = xiaoyou.cards.get(done["card"])["entries"]
        self.assertEqual([entry["id"] for entry in entries], [done["id"] + ":you", done["id"] + ":reply"])
        self.assertTrue(all(entry["scope"] == "main" for entry in entries))

    def test_http_stream_delivers_partial_then_final_and_polling_same_message(self):
        agent = Streaming("ds")
        agent.pieces = ['{"reply": "我', '在想"}']
        agent.script = [{"reply": "我在想", "brief": "b", "mood": "idle"}]
        xiaoyou, store = make_xiaoyou(self.folder, [agent])
        service = Service(xiaoyou, store)
        config = dataclasses.replace(xiaoyou._config, host="127.0.0.1", port=0)
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
        stream = xiaoyou_module.Xiaoyou._streamer(
            "ds", lambda kind, agent, text: said.append((kind, text)))
        stream('{"reply": "要删')
        stream("")
        stream("")
        self.assertEqual(said, [("partial", "要删"), ("partial", "")])


if __name__ == "__main__":
    unittest.main()
