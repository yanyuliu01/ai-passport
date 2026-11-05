"""A reply the owner cut into is final text with a mark every client can see."""
import threading
import unittest

from test_runtime import TempDirCase, ToolTalker, make_xiaoyu
from xiaoyu_runtime.delivery import presentation
from xiaoyu_runtime.helpers.agents import Cut, Outcome, stopped_early
from xiaoyu_runtime.api.service import Service


class CutTalker(ToolTalker):
    """Speaks half a sentence; the owner cuts in while it streams."""

    def __init__(self, xiaoyu_ref, effective=True):
        super().__init__("deepseek")
        self.ref = xiaoyu_ref
        self.effective = effective
        self.cut_next = True

    def run(self, job):
        if job.tools is None or not self.cut_next:
            return super().run(job)
        self.cut_next = False
        said = "秋天先从风里来"
        job.on_text(said)
        self.ref[0].interrupt(job.conversation)
        if self.effective:
            stopped_early(job.interrupt)  # What dsh does when it stops reading early.
        return Outcome(said, "t1", job.tools.fields(said))


class WaitingTalker(ToolTalker):
    def __init__(self):
        super().__init__()
        self.entered, self.release = threading.Event(), threading.Event()
        self.cut = None

    def run(self, job):
        if job.tools is not None and self.cut is None:
            self.cut = job.interrupt
            self.entered.set()
            self.release.wait(5)
            if self.cut.is_set():
                stopped_early(self.cut)
            return Outcome('正在回答', 't1', job.tools.fields('正在回答'))
        return super().run(job)


class InterruptedReplyTests(TempDirCase):
    def start(self, effective=True):
        ref = [None]
        self.voice = CutTalker(ref, effective)
        self.x, self.store = make_xiaoyu(self.folder, [self.voice])
        ref[0] = self.x
        self.service = Service(self.x, self.store)
        self.addCleanup(self.service.close)

    def turn(self, text):
        sent = self.service.submit(text)
        done = self.service.get(sent["id"], wait=5)
        self.assertEqual("done", done["status"])
        return done, self.x.cards.message(sent["id"] + ":reply")

    def test_cut_reply_is_marked_on_every_projection(self):
        self.start()
        done, reply = self.turn("写一段秋天的散文")
        self.assertEqual("秋天先从风里来", reply["text"])
        self.assertEqual("final", reply["status"])
        self.assertIs(True, reply["interrupted"])
        self.assertIs(True, done["interrupted"])
        view = presentation.message(reply)["presentation"]
        self.assertIs(True, view["interrupted"])
        self.assertTrue(view["complete"])
        self.assertEqual("已打断", view["status_label"])
        streamed = presentation.response(done)
        self.assertIs(True, streamed["interrupted"])
        self.assertEqual("已打断", streamed["status_label"])

    def test_next_reply_is_not_marked(self):
        self.start()
        self.turn("写一段秋天的散文")
        done, reply = self.turn("好的")
        self.assertNotIn("interrupted", reply)
        self.assertNotIn("interrupted", done)
        self.assertNotIn("interrupted", presentation.message(reply)["presentation"])

    def test_cut_after_the_reply_was_complete_is_not_marked(self):
        self.start(effective=False)
        done, reply = self.turn("写一段秋天的散文")
        self.assertNotIn("interrupted", reply)
        self.assertNotIn("interrupted", done)
        self.assertEqual("已回复", presentation.message(reply)["presentation"]["status_label"])

    def test_mark_survives_a_restart(self):
        self.start()
        done, _ = self.turn("写一段秋天的散文")
        self.service.close()
        self.x.close()
        x, _ = make_xiaoyu(self.folder, [ToolTalker()])
        self.addCleanup(x.close)
        self.assertIs(True, x.cards.message(done["id"] + ":reply")["interrupted"])

    def test_unmarked_revision_is_unchanged(self):
        record = {"id": "t:reply", "status": "final", "text": "hi", "author": {"kind": "xiaoyou"}}
        before = presentation.message(record)["presentation"]["input_revision"]
        marked = presentation.message(dict(record, interrupted=True))["presentation"]
        self.assertNotEqual(before, marked["input_revision"])
        self.assertEqual(before, presentation.message(dict(record, interrupted=False))["presentation"]["input_revision"])

    def test_streaming_records_are_never_marked(self):
        view = presentation.message({"id": "t:reply", "status": "streaming", "partial": "hi",
                                     "interrupted": True, "author": {"kind": "xiaoyou"}})["presentation"]
        self.assertNotIn("interrupted", view)

    def test_cut_only_reports_effect_when_marked(self):
        cut = Cut()
        cut.set()
        self.assertFalse(cut.took_effect)
        stopped_early(cut)
        self.assertTrue(cut.took_effect)
        stopped_early(None)  # Plain events and missing interrupts are ignored.

    def test_natural_followup_interrupts_the_previous_reply_in_app_and_weixin(self):
        for conversation in ('default', 'weixin'):
            with self.subTest(conversation=conversation):
                voice = WaitingTalker()
                x, store = make_xiaoyu(self.folder / conversation, [voice])
                service = Service(x, store)
                self.addCleanup(x.close)
                self.addCleanup(service.close)
                self.addCleanup(voice.release.set)
                if conversation == 'weixin':
                    service._owner_writer.conversation_create_weixin()
                args = {'conversation': conversation, '_channel_authorized': conversation == 'weixin'}
                first = service.submit('先看看', **args)
                self.assertTrue(voice.entered.wait(3))
                self.assertFalse(voice.cut.is_set(), 'a newly accepted turn must not interrupt itself')
                second = service.submit('还有一件事', **args)
                self.assertTrue(voice.cut.is_set())
                self.assertEqual(service.get(second['id'])['status'], 'queued')
                voice.release.set()
                self.assertEqual(service.get(first['id'], wait=5)['status'], 'done')
                self.assertEqual(service.get(second['id'], wait=5)['status'], 'done')
                self.assertTrue(service.get(first['id']).get('interrupted', False))

    def test_retry_of_same_client_message_does_not_interrupt_its_reply(self):
        voice = WaitingTalker()
        x, store = make_xiaoyu(self.folder / 'retry', [voice])
        service = Service(x, store)
        self.addCleanup(x.close)
        self.addCleanup(service.close)
        self.addCleanup(voice.release.set)
        first = service.submit('先看看', client_id='same-input')
        self.assertTrue(voice.entered.wait(3))
        duplicate = service.submit('先看看', client_id='same-input')
        self.assertEqual(first['id'], duplicate['id'])
        self.assertFalse(voice.cut.is_set())
        voice.release.set()
        self.assertEqual(service.get(first['id'], wait=5)['status'], 'done')

    def test_explicit_stop_still_interrupts_and_preserves_partial_reply(self):
        voice = WaitingTalker()
        x, store = make_xiaoyu(self.folder / 'explicit', [voice])
        service = Service(x, store)
        self.addCleanup(x.close)
        self.addCleanup(service.close)
        self.addCleanup(voice.release.set)
        first = service.submit('先看看')
        self.assertTrue(voice.entered.wait(3))
        second = service.submit('停一下，我补充一句')
        self.assertTrue(voice.cut.is_set())
        voice.release.set()
        done = service.get(first['id'], wait=5)
        self.assertTrue(done.get('interrupted'))
        self.assertEqual(done['reply'], '正在回答')
        self.assertEqual(service.get(second['id'], wait=5)['status'], 'done')

    def test_background_speech_remains_preemptible(self):
        self.start()
        with self.x.speaking_turn('default') as background:
            self.assertTrue(self.x.interrupt('default'))
            self.assertTrue(background.is_set())

    def test_empty_interruption_sentinel_is_not_published_as_a_bubble(self):
        base = {'id': 't:reply', 'conversation': 'weixin', 'status': 'final', 'text': '……',
                'author': {'kind': 'xiaoyou', 'name': 'xiaoyou'}, 'interrupted': True}
        view = presentation.message(base)['presentation']
        self.assertEqual(view['blocks'], [])
        self.assertEqual(view['plain'], '')
        self.assertTrue(view['interrupted'])
        self.assertEqual(presentation.response({'id': 't', 'conversation': 'weixin', 'status': 'done', 'reply': '……',
                                                'interrupted': True})['blocks'], [])
        self.assertEqual(presentation.message(dict(base, text='说了一半'))['presentation']['plain'], '说了一半')
        self.assertEqual(presentation.message(dict(base, interrupted=False))['presentation']['plain'], '……')


if __name__ == "__main__":
    unittest.main()
