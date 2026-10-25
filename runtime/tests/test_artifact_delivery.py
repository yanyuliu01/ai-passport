"""End-to-end task result delivery using stand-in agents, without model calls."""

import dataclasses
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))

from xiaoyou_runtime.helpers.agents import Agent, Outcome
from xiaoyou_runtime.world.cards import MAX_ENTRY_CHARS, Cards
from xiaoyou_runtime.config import AgentSpec, load
from xiaoyou_runtime.world.store import Store
from xiaoyou_runtime.loop.xiaoyou import Xiaoyou


class OutputAgent(Agent):
    """Captures the job and produces only fixture files in its assigned inbox."""
    def __init__(self, reply, files=None, wrapped=False, speaks=True):
        super().__init__(AgentSpec(name="fixture", type="dsh", description="Test fixture",
                                   aliases=[], speaks=speaks, timeout_seconds=5))
        self.reply = reply
        self.files = files or {}
        self.wrapped = wrapped
        self.jobs = []

    def run(self, job):
        self.jobs.append(job)
        if self.files:
            found = re.search(r"inside this task's directory: (.+?)\. Write manifest.json", job.system)
            if found is None:
                raise AssertionError("task did not provide its artifact inbox")
            directory = Path(found.group(1))
            manifest = []
            for filename, data in self.files.items():
                (directory / filename).write_bytes(data)
                manifest.append({"path": filename, "name": filename})
            (directory / "manifest.json").write_text(json.dumps({"artifacts": manifest}))
        fields = {"reply": self.reply, "brief": "完成", "mood": "happy"}
        return Outcome(json.dumps(fields, ensure_ascii=False) if self.wrapped else self.reply,
                       "fixture-session", None if self.wrapped else fields)


class ArtifactDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="xiaoyou-delivery-test-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name).resolve()
        (self.folder / "persona.txt").write_text("Test persona")
        self.path = self.folder / "config.json"
        self.path.write_text(json.dumps({"server": {"host": "127.0.0.1", "token": "fixture-token-for-host-tests"},
                                         "backend": "echo"}))

    def runtime(self, agent):
        config = dataclasses.replace(load(self.path, {}), agents=[agent.spec],
                                     default_agent=agent.name, voice_agent=agent.name,
                                     usage_enabled=False)
        store = Store(config.state_dir)
        runtime = Xiaoyou(config, [agent], store)
        self.addCleanup(runtime.close)
        return runtime, config

    def deliver(self, runtime):
        turn = runtime.hear("Make the design", "default", "fixture-turn", asked="fixture")
        card = runtime.settle(turn.card, 5)
        self.assertEqual(card["state"], "done")
        return card

    def read(self, runtime, attachment):
        with runtime.artifacts.open_content(attachment["id"])[0] as stream:
            return stream.read()

    def test_long_reply_preserves_original_and_marks_display_truncation(self):
        full = "# 设计原稿\n\n```text\nkeep markdown untouched\n```\n" + "完整段落\n" * 2500 + "原文结尾"
        agent = OutputAgent(full)
        runtime, _ = self.runtime(agent)
        card = self.deliver(runtime)
        final = card["entries"][-1]
        self.assertLessEqual(len(final["text"]), MAX_ENTRY_CHARS)
        self.assertIn("内容较长，完整内容见附件", final["text"])
        text = next(item for item in card["attachments"] if item["name"] == "完整方案与回复.txt")
        self.assertEqual(self.read(runtime, text).decode("utf-8"), full)
        self.assertEqual(final["attachments"], card["attachments"])
        self.assertEqual(card["entries"][1].get("attachments", []), [])

    def test_json_reply_wrapper_is_parsed_before_display_and_full_text_snapshot(self):
        full = "这是正文\n" * 2000
        runtime, _ = self.runtime(OutputAgent(full, wrapped=True))
        card = self.deliver(runtime)
        final = card["entries"][-1]
        self.assertTrue(final["text"].startswith("这是正文"))
        self.assertNotIn('"reply":', final["text"])
        self.assertNotIn('"brief":', final["text"])
        self.assertEqual(self.read(runtime, card["attachments"][0]).decode("utf-8"), full)

    def test_short_json_reply_remains_normal_text_without_unneeded_file(self):
        runtime, _ = self.runtime(OutputAgent("界面方案已经完成", wrapped=True))
        card = self.deliver(runtime)
        self.assertEqual(card["entries"][-1]["text"], "界面方案已经完成")
        self.assertEqual(card["attachments"], [])

    def test_task_manifest_binds_final_entry_and_survives_runtime_restart(self):
        from test_artifacts import PNG
        files = {"preview.png": PNG, "design.pdf": b"%PDF-1.7\nfixture design"}
        agent = OutputAgent("请看图片和设计文档", files=files)
        runtime, config = self.runtime(agent)
        card = self.deliver(runtime)
        self.assertIn("manifest.json", agent.jobs[0].system)
        self.assertEqual({a["name"] for a in card["attachments"]}, set(files))
        self.assertEqual(card["entries"][-1]["attachments"], card["attachments"])
        self.assertEqual(card["entries"][1].get("attachments", []), [])
        for attachment in card["attachments"]:
            self.assertEqual(self.read(runtime, attachment), files[attachment["name"]])
        inbox = runtime.artifacts.task_dir(card["id"])
        for file in inbox.iterdir():
            file.unlink()
        runtime.close()
        restarted = Xiaoyou(config, [OutputAgent("unused")], Store(config.state_dir))
        self.addCleanup(restarted.close)
        restored = restarted.cards.get(card["id"])
        self.assertEqual(restored["attachments"], card["attachments"])
        self.assertEqual(restored["entries"][-1]["attachments"], card["attachments"])
        for attachment in restored["attachments"]:
            self.assertEqual(self.read(restarted, attachment), files[attachment["name"]])

    def test_non_speaking_helper_keeps_manifest_outputs_on_final_entry(self):
        agent = OutputAgent("File ready", {"result.txt": b"result"}, speaks=False)
        runtime, _ = self.runtime(agent)
        card = self.deliver(runtime)
        self.assertEqual(self.read(runtime, card["attachments"][0]), b"result")
        self.assertEqual(card["entries"][-1]["attachments"], card["attachments"])

    def test_non_speaking_json_reply_fallback_does_not_show_wrapper(self):
        runtime, _ = self.runtime(OutputAgent("只显示正文", wrapped=True, speaks=False))
        card = self.deliver(runtime)
        self.assertEqual(card["entries"][-1]["text"], "只显示正文")

    def test_raw_short_result_and_main_summary_have_separate_persistent_identities(self):
        runtime, config = self.runtime(OutputAgent("原始结论与证据", speaks=False))
        card = self.deliver(runtime)
        original = next(entry for entry in card["entries"] if entry.get("kind") == "worker_result")
        summary = card["entries"][-1]
        self.assertEqual((original["text"], original["scope"], original["author"]),
                         ("原始结论与证据", "task", "fixture"))
        self.assertEqual((summary["kind"], summary["scope"]), ("summary", "main"))
        self.assertNotEqual(original["id"], summary["id"])
        self.assertTrue(card["result_ready"])
        self.assertEqual(card["summary_partial"], "")
        restored = Cards(config.state_dir / "cards.json").get(card["id"])
        self.assertEqual(restored["entries"], card["entries"])
        self.assertTrue(restored["result_ready"])

    def test_old_summary_cannot_close_a_new_task_run(self):
        from xiaoyou_runtime.helpers.tasks import Task
        agent = OutputAgent("unused")
        runtime, _ = self.runtime(agent)
        card = runtime.cards.open("default", "task", "working", "fixture")
        old = Task(card=card["id"], conversation="default", agent=agent, text="old")
        runtime.cards.update(card["id"], run_id="new-run")
        runtime._close(old, "old summary", None, "happy", "done", [])
        runtime._finished(old, Outcome("old raw result", None), None)
        current = runtime.cards.get(card["id"])
        self.assertEqual(current["state"], "working")
        self.assertEqual(current["entries"], [])

    def test_original_is_available_before_streamed_summary_finishes(self):
        import threading
        from test_runtime import Scripted, make_xiaoyou, until
        from xiaoyou_runtime.api.service import Service
        ready, finish = threading.Event(), threading.Event()
        class Voice(Scripted):
            def run(self, job):
                if "原始结果" in job.text:
                    self.assertion = job.on_text is not None
                    job.on_text('{"reply":"总结开头')
                    ready.set(); finish.wait(5)
                    return Outcome('summary', None, {"reply": "summary", "brief": "done", "mood": "happy"})
                return super().run(job)
        voice, helper = Voice("ds"), Scripted("worker", speaks=False)
        runtime, _ = make_xiaoyou(self.folder / "relay", [voice, helper])
        service = Service(runtime, runtime._store)
        self.addCleanup(service.close); self.addCleanup(finish.set)
        message = service.get(service.submit("@worker read evidence")["id"], wait=5)
        self.assertTrue(ready.wait(5))
        card = runtime.cards.get(message["card"])
        self.assertTrue(voice.assertion)
        self.assertEqual(card["summary_partial"], "总结开头")
        self.assertTrue(card["result_ready"])
        self.assertTrue(any(entry.get("kind") == "worker_result" for entry in card["entries"]))
        finish.set()
        self.assertTrue(until(lambda: runtime.cards.get(message["card"])["state"] == "done", 5))
        self.assertEqual(runtime.cards.get(message["card"])["entries"][-1]["kind"], "summary")


if __name__ == "__main__":
    unittest.main()
