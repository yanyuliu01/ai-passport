"""Image acceptance, durable identity, and native vision dispatch (no real model)."""
import dataclasses
import io
import json
import os
import sys
import tempfile
import time
import unittest
import threading
import urllib.request
import urllib.error
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from PIL import Image
except ImportError:
    Image = None
from xiaoyu_runtime.helpers.agents import Agent, AgentSpec, Job, Outcome, CodexAgent, AgentError
from xiaoyu_runtime.world.artifacts import ArtifactError, ArtifactStore
from xiaoyu_runtime.events.image_inputs import upload, resolve
from xiaoyu_runtime.config import load
from xiaoyu_runtime.world.store import Store
from xiaoyu_runtime.loop.xiaoyu import Xiaoyu
from xiaoyu_runtime.api.service import Service, ConflictError, RequestError

class Vision(Agent):
    def __init__(self):
        super().__init__(AgentSpec(name="vision", type="codex", description="fixture",
            aliases=[], speaks=False, timeout_seconds=5))
        self.jobs = []
    def run(self, job):
        self.jobs.append(job)
        return Outcome("The fixture image is red", "fixture-session")

class DshVision(Agent):
    def __init__(self, action=None):
        super().__init__(AgentSpec(name="deepseek", type="dsh", description="fixture", aliases=[],
            speaks=True, timeout_seconds=5, stream=True, model="deepseek-flash", chat_model="deepseek-flash"))
        self.jobs = []
        self.action = action or {"type": "none"}
        self.session = "fixture-session"
    def talks_with_tools(self):
        return True
    def run(self, job):
        self.jobs.append(job)
        return Outcome("I see a red image", self.session, {"reply": "I see a red image",
            "card": "new", "mood": "happy", "action": self.action})

@unittest.skipIf(Image is None, "Image acceptance requires requirements-images.txt (Pillow)")
class ImageInputsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root/'persona.txt').write_text('Fixture persona',encoding='utf-8')
        config = self.root/'config.json'; config.write_text(json.dumps({'backend':'echo','server':{'host':'127.0.0.1','token':'image-fixture-token-for-tests'}}))
        self.agent = Vision()
        self.config = dataclasses.replace(load(config, {}), agents=[self.agent.spec],
            default_agent='vision', voice_agent='vision', usage_enabled=False, port=0)
        self.store = ArtifactStore(self.config)
        image=Image.new('RGB',(12,8),'red'); out=io.BytesIO(); image.save(out,'PNG')
        self.png=out.getvalue()
    def runtime(self):
        disk=Store(self.config.state_dir)
        runtime=Xiaoyu(self.config,[self.agent],disk); self.addCleanup(runtime.close)
        service=Service(runtime,disk); self.addCleanup(service.close)
        return runtime,service
    def test_native_voice_sees_image_and_preserves_attachment(self):
        voice = DshVision()
        self.config = dataclasses.replace(self.config, agents=[voice.spec, self.agent.spec],
                                          default_agent=voice.name, voice_agent=voice.name)
        disk = Store(self.config.state_dir)
        runtime = Xiaoyu(self.config, [voice, self.agent], disk); self.addCleanup(runtime.close)
        service = Service(runtime, disk); self.addCleanup(service.close)
        item = upload(runtime.artifacts, self.png)
        msg = service.submit("what is this?", image_ids=[item["id"]])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            stored = service.get(msg["id"])
            if stored["status"] in ("done", "failed"):
                break
            time.sleep(.01)
        self.assertEqual(stored["status"], "done", stored)
        self.assertEqual(len(voice.jobs), 1)
        self.assertEqual(Path(voice.jobs[0].images[0]).read_bytes(), self.png)
        self.assertEqual(voice.jobs[0].image_refs, (item["id"],))
        self.assertEqual(self.agent.jobs, [])
        card = runtime.cards.get(stored["card"])
        owner = next(entry for entry in card["entries"] if entry["role"] == "you")
        self.assertEqual(owner["attachments"][0]["id"], item["id"])

    def test_short_followup_reuses_immediately_previous_owner_image_in_app_and_weixin(self):
        for conversation in ('default', 'weixin'):
            with self.subTest(conversation=conversation), tempfile.TemporaryDirectory() as folder:
                voice = DshVision()
                config = dataclasses.replace(self.config, state_dir=Path(folder),
                    agents=[voice.spec, self.agent.spec], default_agent=voice.name, voice_agent=voice.name)
                disk = Store(config.state_dir)
                runtime = Xiaoyu(config, [voice, self.agent], disk)
                service = Service(runtime, disk)
                try:
                    if conversation == 'weixin':
                        service._owner_writer.conversation_create_weixin()
                    image = upload(runtime.artifacts, self.png)
                    args = {'conversation': conversation, '_channel_authorized': conversation == 'weixin'}
                    first = service.submit('', image_ids=[image['id']], **args)
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline and service.get(first['id'])['status'] not in ('done', 'failed'):
                        time.sleep(.01)
                    self.assertEqual(service.get(first['id'])['status'], 'done')
                    self.assertEqual(runtime._recent_owner_image_reference('这个任务咋样', conversation), [])
                    self.assertEqual(runtime._recent_owner_image_reference('换这个咋样', 'other-chat'), [])
                    second = service.submit('换这个咋样', **args)
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline and service.get(second['id'])['status'] not in ('done', 'failed'):
                        time.sleep(.01)
                    self.assertEqual(service.get(second['id'])['status'], 'done')
                    self.assertEqual(len(voice.jobs), 2)
                    self.assertEqual(Path(voice.jobs[1].images[0]).read_bytes(), self.png)
                    card = runtime.cards.get(service.get(second['id'])['card'])
                    owner = next(entry for entry in card['entries'] if entry.get('id') == second['id'] + ':you')
                    self.assertEqual(owner.get('attachments', []), [])
                finally:
                    service.close(); runtime.close()

    def test_followup_with_missing_original_image_fails_instead_of_guessing(self):
        voice = DshVision()
        self.config = dataclasses.replace(self.config, agents=[voice.spec, self.agent.spec],
                                          default_agent=voice.name, voice_agent=voice.name)
        disk = Store(self.config.state_dir)
        runtime = Xiaoyu(self.config, [voice, self.agent], disk); self.addCleanup(runtime.close)
        service = Service(runtime, disk); self.addCleanup(service.close)
        image = upload(runtime.artifacts, self.png)
        first = service.submit('', image_ids=[image['id']])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and service.get(first['id'])['status'] not in ('done', 'failed'):
            time.sleep(.01)
        self.assertEqual(service.get(first['id'])['status'], 'done')
        (runtime.artifacts._blobs / image['sha256']).unlink()
        second = service.submit('换这个咋样')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and service.get(second['id'])['status'] not in ('done', 'failed'):
            time.sleep(.01)
        self.assertEqual(service.get(second['id'])['status'], 'failed')
        self.assertIn('重新发送图片', service.get(second['id'])['error'])
        self.assertEqual(len(voice.jobs), 1)

    def test_initial_image_resolver_limits_access_to_current_input(self):
        class ResolvingVision(DshVision):
            def run(inner, job):
                inner.jobs.append(job)
                stream, _, _, _ = job.image_resolver(inner.expected)
                with stream:
                    inner.image = stream.read()
                try:
                    job.image_resolver(inner.other)
                except AgentError:
                    inner.denied = True
                else:
                    inner.denied = False
                return Outcome("red", inner.session, {"reply": "red", "card": "new",
                    "mood": "happy", "action": {"type": "none"}})

        voice = ResolvingVision()
        self.config = dataclasses.replace(self.config, agents=[voice.spec, self.agent.spec],
                                          default_agent=voice.name, voice_agent=voice.name)
        disk = Store(self.config.state_dir)
        runtime = Xiaoyu(self.config, [voice, self.agent], disk); self.addCleanup(runtime.close)
        service = Service(runtime, disk); self.addCleanup(service.close)
        original = upload(runtime.artifacts, self.png)
        out = io.BytesIO(); Image.new("RGB", (12, 8), "blue").save(out, "PNG")
        other = upload(runtime.artifacts, out.getvalue())
        voice.expected, voice.other = original["id"], other["id"]
        message = service.submit("what is this?", image_ids=[original["id"]])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            saved = service.get(message["id"])
            if saved["status"] in ("done", "failed"):
                break
            time.sleep(.01)
        self.assertEqual(saved["status"], "done", saved)
        self.assertEqual(voice.image, self.png)
        self.assertTrue(voice.denied)

    def test_failed_native_vision_retains_original_image_after_restart(self):
        class UnavailableVision(DshVision):
            def run(self, job):
                raise AgentError("vision service unavailable")

        voice = UnavailableVision()
        self.config = dataclasses.replace(self.config, agents=[voice.spec, self.agent.spec],
                                          default_agent=voice.name, voice_agent=voice.name)
        disk = Store(self.config.state_dir)
        runtime = Xiaoyu(self.config, [voice, self.agent], disk)
        service = Service(runtime, disk)
        try:
            item = upload(runtime.artifacts, self.png)
            message = service.submit("what is this?", image_ids=[item["id"]])
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                saved = service.get(message["id"])
                if saved["status"] in ("done", "failed"):
                    break
                time.sleep(.01)
            self.assertEqual(saved["status"], "failed")
            card_id = saved["card"]
            card = runtime.cards.get(card_id)
            owner = next(entry for entry in card["entries"] if entry.get("id") == message["id"] + ":you")
            self.assertEqual([image["id"] for image in owner["attachments"]], [item["id"]])
        finally:
            service.close()
            runtime.close()

        restored_disk = Store(self.config.state_dir)
        restored = Xiaoyu(self.config, [voice, self.agent], restored_disk)
        self.addCleanup(restored.close)
        card = restored.cards.get(card_id)
        owner = next(entry for entry in card["entries"] if entry.get("id") == message["id"] + ":you")
        self.assertEqual([image["id"] for image in owner["attachments"]], [item["id"]])
        stream, _, _, _ = restored.artifacts.open_content(item["id"])
        with stream:
            self.assertEqual(stream.read(), self.png)

    def test_permanent_vision_error_does_not_retry_same_request(self):
        from xiaoyu_runtime.helpers.dsh_stream import PermanentRequestError
        class InvalidVision(DshVision):
            def run(self, job):
                self.jobs.append(job)
                raise PermanentRequestError("DeepSeek 流式接口返回 401")
        voice = InvalidVision()
        self.config = dataclasses.replace(self.config, agents=[voice.spec, self.agent.spec],
                                          default_agent=voice.name, voice_agent=voice.name)
        disk = Store(self.config.state_dir)
        runtime = Xiaoyu(self.config, [voice, self.agent], disk); self.addCleanup(runtime.close)
        service = Service(runtime, disk); self.addCleanup(service.close)
        item = upload(runtime.artifacts, self.png)
        msg = service.submit("what is this?", image_ids=[item["id"]])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = service.get(msg["id"])
            if current["status"] in ("done", "failed"):
                break
            time.sleep(.01)
        self.assertEqual(current["status"], "failed")
        self.assertEqual(len(voice.jobs), 1)
        self.assertEqual(len(current["attachments"]), 1)

    def test_restart_repairs_image_if_interrupted_before_attachment_write(self):
        voice = DshVision()
        self.config = dataclasses.replace(self.config, agents=[voice.spec, self.agent.spec],
                                          default_agent=voice.name, voice_agent=voice.name)
        disk = Store(self.config.state_dir)
        runtime = Xiaoyu(self.config, [voice, self.agent], disk)
        service = Service(runtime, disk)
        item = upload(runtime.artifacts, self.png)
        with mock.patch.object(service, "_line_up"):
            message = service.submit("what is this?", image_ids=[item["id"]])
        service._update(message["id"], status="running")
        card = runtime._writer.task_open("default", "what is this?", "failed")
        runtime._writer.exchange(card["id"], said="what is this?", say="failed",
                                 turn_id=message["id"])
        service.close()
        runtime.close()

        disk = Store(self.config.state_dir)
        restored = Xiaoyu(self.config, [voice, self.agent], disk)
        recovered = Service(restored, disk)
        self.addCleanup(restored.close)
        self.addCleanup(recovered.close)
        card = restored.cards.get(card["id"])
        owner = next(entry for entry in card["entries"] if entry.get("id") == message["id"] + ":you")
        self.assertEqual([image["id"] for image in owner["attachments"]], [item["id"]])
        self.assertEqual(recovered.get(message["id"])["status"], "failed")

    def test_native_voice_handoff_keeps_original_image(self):
        voice = DshVision({"type": "start", "agent": "vision", "title": "inspect", "task": "inspect image"})
        self.config = dataclasses.replace(self.config, agents=[voice.spec, self.agent.spec],
                                          default_agent=voice.name, voice_agent=voice.name)
        disk = Store(self.config.state_dir)
        runtime = Xiaoyu(self.config, [voice, self.agent], disk); self.addCleanup(runtime.close)
        service = Service(runtime, disk); self.addCleanup(service.close)
        item = upload(runtime.artifacts, self.png)
        msg = service.submit("inspect this", image_ids=[item["id"]])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not self.agent.jobs:
            time.sleep(.01)
        self.assertEqual(len(self.agent.jobs), 1, service.get(msg["id"]))
        self.assertEqual(Path(self.agent.jobs[0].images[0]).read_bytes(), self.png)

    def test_followup_handoff_uses_only_referenced_conversation_image(self):
        from xiaoyu_runtime.helpers import dsh_stream
        voice = DshVision({"type": "start", "agent": "vision", "title": "inspect",
                           "task": "inspect earlier picture", "include_images": True})
        voice.spec = dataclasses.replace(voice.spec, config_dir=self.root / "dsh")
        voice.session = "apt-" + "a" * 32
        self.config = dataclasses.replace(self.config, agents=[voice.spec, self.agent.spec],
                                          default_agent=voice.name, voice_agent=voice.name)
        disk = Store(self.config.state_dir)
        runtime = Xiaoyu(self.config, [voice, self.agent], disk); self.addCleanup(runtime.close)
        service = Service(runtime, disk); self.addCleanup(service.close)
        item = upload(runtime.artifacts, self.png)
        old = runtime._writer.task_open("default", "old picture")
        runtime._writer.exchange(old["id"], said="picture", say="seen", turn_id="old")
        runtime._writer.attach(old["id"], [item], 0)
        with self.assertRaises(Exception):
            runtime._resolve_conversation_image("other", item["id"])
        disk.remember("default", voice.name, voice.session)
        dsh_stream._save(dsh_stream.sessions_dir(voice.spec), voice.session,
                         [{"role": "user", "content": "picture", "_image_refs": [item["id"]]},
                          {"role": "assistant", "content": "seen"}])
        msg = service.submit("have the assistant inspect the earlier picture")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not self.agent.jobs:
            time.sleep(.01)
        self.assertEqual(len(self.agent.jobs), 1, service.get(msg["id"]))
        self.assertEqual(Path(self.agent.jobs[0].images[0]).read_bytes(), self.png)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not service.get(msg["id"]).get("card"):
            time.sleep(.01)
        card = runtime.cards.get(service.get(msg["id"])["card"])
        owner = next(entry for entry in card["entries"] if entry["role"] == "you")
        self.assertFalse(owner.get("attachments"))
        named = service.submit("@vision 看看刚才的图片")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(self.agent.jobs) < 2:
            time.sleep(.01)
        self.assertEqual(len(self.agent.jobs), 2, service.get(named["id"]))
        self.assertEqual(Path(self.agent.jobs[1].images[0]).read_bytes(), self.png)

    def test_followup_missing_image_does_not_start_helper(self):
        voice = DshVision({"type": "start", "agent": "vision", "title": "inspect",
                           "task": "inspect earlier picture", "include_images": True})
        self.config = dataclasses.replace(self.config, agents=[voice.spec, self.agent.spec],
                                          default_agent=voice.name, voice_agent=voice.name)
        disk = Store(self.config.state_dir)
        runtime = Xiaoyu(self.config, [voice, self.agent], disk); self.addCleanup(runtime.close)
        service = Service(runtime, disk); self.addCleanup(service.close)
        msg = service.submit("inspect previous image")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and service.get(msg["id"])["status"] not in ("done", "failed"):
            time.sleep(.01)
        self.assertEqual(self.agent.jobs, [])
        self.assertIn("图片", service.get(msg["id"])["reply"])

    def test_upload_is_immutable_deduplicated_and_downloadable_after_restart(self):
        first=upload(self.store,self.png); second=upload(self.store,self.png)
        self.assertEqual(first['id'],second['id'])
        restored=ArtifactStore(self.config)
        stream,meta,mime,size=restored.open_content(first['id'])
        with stream:self.assertEqual(stream.read(),self.png)
        self.assertEqual(mime,'image/png'); self.assertEqual(meta['source'],'owner')
        self.assertNotIn('path',meta); self.assertEqual(resolve(restored,[first['id']]),[meta])
    def test_rejects_corrupt_large_duplicate_and_non_owner_images(self):
        for data in (b'',b'not an image',self.png[:24]):
            with self.assertRaises(ArtifactError):upload(self.store,data)
        item=upload(self.store,self.png)
        for ids in ([item['id']]*2,{},[{}],['../../config.json'],[item['id']]*5):
            with self.assertRaises(ArtifactError):resolve(self.store,ids)
        other=self.store._snapshot('fixture',io.BytesIO(self.png),'reply.png','runtime')
        with self.assertRaises(ArtifactError):resolve(self.store,[other['id']])
    def test_message_keeps_image_ids_and_deduplicates_before_dispatch(self):
        runtime,service=self.runtime(); item=upload(runtime.artifacts,self.png)
        message=service.submit('',client_id='one',image_ids=[item['id']])
        again=service.submit('',client_id='one',image_ids=[item['id']])
        self.assertEqual(message['id'],again['id'])
        with self.assertRaises(ConflictError):service.submit('different',client_id='one',image_ids=[item['id']])
        deadline=time.monotonic()+4
        while time.monotonic()<deadline and not self.agent.jobs:time.sleep(.01)
        self.assertEqual(len(self.agent.jobs),1)
        self.assertEqual(len(self.agent.jobs[0].images),1)
        self.assertEqual(Path(self.agent.jobs[0].images[0]).read_bytes(),self.png)
        stored=service.get(message['id'])
        self.assertEqual(stored['attachments'][0]['id'],item['id'])
        card=runtime.cards.get(stored['card'])
        owner=next(entry for entry in card['entries'] if entry['role']=='you')
        self.assertEqual(owner['attachments'][0]['id'],item['id'])
    def test_unsupported_routes_fail_before_acceptance(self):
        runtime,service=self.runtime();item=upload(runtime.artifacts,self.png)
        for extra in ({'card':'c1'},{'pin':True},{'agent':'missing'}):
            with self.assertRaises(RequestError):service.submit('image',image_ids=[item['id']],**extra)
    def test_authenticated_http_upload_send_and_content(self):
        from xiaoyu_runtime.api.server import make_server
        runtime, service = self.runtime()
        server = make_server(self.config, service, artifacts=runtime.artifacts)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        address = 'http://127.0.0.1:' + str(server.server_port)
        def request(path, data=None, token=True):
            headers = {'Authorization': 'Bearer ' + self.config.token} if token else {}
            req = urllib.request.Request(address + path, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=4) as response:
                return response.status, response.read()
        with self.assertRaises(urllib.error.HTTPError) as error:
            request('/v1/images', self.png, False)
        self.assertEqual(error.exception.code, 401)
        error.exception.close()
        status, body = request('/v1/images', self.png)
        self.assertEqual(status, 201); image = json.loads(body)
        status, body = request('/v1/messages', json.dumps({'image_ids':[image['id']], 'client_id':'http-image'}).encode())
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(body)['attachments'][0]['id'], image['id'])
        status, body = request('/v1/artifacts/' + image['id'] + '/content')
        self.assertEqual(body, self.png)

    def test_codex_exec_uses_native_image_flag(self):
        agent=CodexAgent(self.agent.spec)
        command=agent.command(Job('look',images=('fixture.png',)),Path('last.txt'))
        self.assertIn('--image',command)
        self.assertEqual(command[command.index('--image')+1],'fixture.png')

if __name__=='__main__':unittest.main()
