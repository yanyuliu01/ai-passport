#!/usr/bin/env python3
"""Tests for streamed replies: the DeepSeek stream, the partial reply, and how the
message record carries it to the phone. No model or network is called."""

import io
import json
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS))

from test_runtime import Scripted, TempDirCase, make_xiaoyou, write_config  # noqa: E402
from xiaoyou_runtime import config as config_module  # noqa: E402
from xiaoyou_runtime import dsh_stream  # noqa: E402
from xiaoyou_runtime import xiaoyou as xiaoyou_module  # noqa: E402
from xiaoyou_runtime.agents import DshAgent, Job, Outcome  # noqa: E402
from xiaoyou_runtime.service import Service  # noqa: E402
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
