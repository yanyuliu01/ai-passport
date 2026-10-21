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
from xiaoyou_runtime.agents import Agent, AgentSpec, Job, Outcome, CodexAgent
from xiaoyou_runtime.artifacts import ArtifactError, ArtifactStore
from xiaoyou_runtime.image_inputs import upload, resolve
from xiaoyou_runtime.config import load
from xiaoyou_runtime.store import Store
from xiaoyou_runtime.xiaoyou import Xiaoyou
from xiaoyou_runtime.service import Service, ConflictError, RequestError

class Vision(Agent):
    def __init__(self):
        super().__init__(AgentSpec(name="vision", type="codex", description="fixture",
            aliases=[], speaks=False, timeout_seconds=5))
        self.jobs = []
    def run(self, job):
        self.jobs.append(job)
        return Outcome("The fixture image is red", "fixture-session")

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
        runtime=Xiaoyou(self.config,[self.agent],disk); self.addCleanup(runtime.close)
        service=Service(runtime,disk); self.addCleanup(service.close)
        return runtime,service
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
        from xiaoyou_runtime.server import make_server
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
