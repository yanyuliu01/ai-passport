"""Relay publication keeps Runtime metadata behind verified uploads."""
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('publish_app_relay',ROOT/'tools/publish_app_relay.py')
publisher=importlib.util.module_from_spec(spec);spec.loader.exec_module(publisher)

class PublisherTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.settings=self.root/'settings.json';self.settings.write_text(json.dumps({'host':'owner@relay.example',
   'directory':'/srv/releases','base_url':'https://150.158.176.23/v1/releases/apps','key':'/private/key','known_hosts':'/private/known'}))
  self.abis=['universal','arm64-v8a','armeabi-v7a','x86','x86_64']
  for abi in self.abis:(self.root/('app-'+abi+'-debug.apk')).write_bytes(b'PK\x03\x04'+abi.encode())
 def metadata(self,path):
  return {'abi':path.name[len('app-'):-len('-debug.apk')],'version_code':88,'version_name':'0.39.1'}
 def test_runtime_only_publishes_after_remote_hashes_match(self):
  calls=[];release=Mock();release.publish.return_value={'version_code':88,'version_name':'0.39.1'}
  def run(command,**kw):
   calls.append(command[-1]);out=b''
   if command[-1].startswith('sha256sum '):out=(re.search('[a-f0-9]{64}',command[-1]).group()+' file\n').encode()
   return subprocess.CompletedProcess(command,0,stdout=out,stderr=b'')
  with patch.object(publisher,'verify_download'),patch.object(publisher,'_metadata_beside',side_effect=self.metadata),patch.object(publisher.subprocess,'run',side_effect=run),patch.object(publisher,'load',return_value=SimpleNamespace(state_dir=self.root)),patch.object(publisher,'AppRelease',return_value=release):
   publisher.publish(self.root/'config',self.root,self.settings,'Update')
  self.assertEqual(sum(c.startswith('sha256sum ') for c in calls),5)
  values=release.publish.call_args.kwargs
  self.assertTrue(values['download_url'].startswith('https://150.158.176.23/v1/releases/apps/'))
  self.assertEqual(len(values['variant_download_urls']),4)
 def test_checksum_failure_preserves_current_runtime_release(self):
  release=Mock()
  def run(command,**kw):return subprocess.CompletedProcess(command,0,stdout=b'wrong file\n' if command[-1].startswith('sha256sum ') else b'',stderr=b'')
  with patch.object(publisher,'verify_download'),patch.object(publisher,'_metadata_beside',side_effect=self.metadata),patch.object(publisher.subprocess,'run',side_effect=run),patch.object(publisher,'AppRelease',return_value=release):
   with self.assertRaisesRegex(ValueError,'checksum mismatch'):publisher.publish(self.root/'config',self.root,self.settings,'Update')
  release.publish.assert_not_called()

if __name__=='__main__':unittest.main()
