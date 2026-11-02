"""Routing must not choose two owners when mesh ownership is unknown."""
from dataclasses import replace
import json
from unittest import mock

from test_mesh import Network, Node
from test_runtime import TempDirCase, make_xiaoyu, Scripted, write_config, until
from xiaoyu_runtime import config as config_module
from xiaoyu_runtime.api.service import Service
from xiaoyu_runtime.loop.effects import EffectFenceError


class OwnerFencingFaultTests(TempDirCase):
    def test_strict_ownership_is_opt_in_and_validated(self):
        path = write_config(self.folder)
        self.assertFalse(config_module.load(path, {}).mesh_require_owner)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["mesh"] = {"require_owner": True}
        path.write_text(json.dumps(raw), encoding="utf-8")
        self.assertTrue(config_module.load(path, {}).mesh_require_owner)
        raw["mesh"]["require_owner"] = "yes"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(config_module.ConfigError):
            config_module.load(path, {})

    def test_two_new_mesh_members_wait_for_owner_and_then_route_once(self):
        network = Network()
        mac = Node(self, network, "Mac")
        windows = Node(self, network, "Windows")
        for node in (mac, windows):
            node.mesh._config = replace(node.mesh._config, mesh_require_owner=True)
        messages = [{"id": "m1", "conversation": "default", "hop": 0, "pin": False},
                    {"id": "m2", "conversation": "default", "hop": 0, "pin": False}]
        for node, msg in zip((mac, windows), messages):
            self.assertIsNone(node.mesh.owner_of("default")["owner"])
            self.assertFalse(node.service._route(msg))
            with self.assertRaises(EffectFenceError):
                node.service._effect_owner("default")
        known = {"owner": mac.service.runtime_id, "generation": 1, "name": "Mac",
                 "online": True, "conflict": False}
        with mock.patch.object(mac.mesh, "owner_of", return_value=known):
            self.assertTrue(mac.service._route(messages[0]))
            self.assertNotIn("relay_to", messages[0])
        with mock.patch.object(windows.mesh, "owner_of", return_value=known):
            self.assertTrue(windows.service._route(messages[1]))
            self.assertEqual(messages[1]["relay_to"], mac.service.runtime_id)

    def test_conflicting_owners_cannot_run_local_or_forward(self):
        node = Node(self, Network(), "Mac")
        node.mesh._config = replace(node.mesh._config, mesh_require_owner=True)
        view = {"owner": node.service.runtime_id, "generation": 1, "conflict": True}
        msg = {"id": "m", "conversation": "default", "hop": 0, "pin": False}
        with mock.patch.object(node.mesh, "owner_of", return_value=view):
            self.assertFalse(node.service._route(msg))

    def test_explicit_local_runtime_without_mesh_still_processes(self):
        xiaoyu, store = make_xiaoyu(self.folder, [Scripted("claude")])
        service = Service(xiaoyu, store)
        self.addCleanup(service.close)
        msg = {"id": "m", "conversation": "default", "hop": 0, "pin": False}
        self.assertTrue(service._route(msg))

    def test_parked_turn_resumes_at_mac_after_owner_record_arrives(self):
        network = Network()
        mac = Node(self, network, "Mac")
        windows = Node(self, network, "Windows", peers=[mac.url])
        for node in (mac, windows):
            node.mesh._config = replace(node.mesh._config, mesh_require_owner=True)
        sent = windows.service.submit("one parked turn", client_id="owner-fence-test")
        self.assertTrue(until(lambda: sent["id"] in windows.service._parked))
        self.assertEqual(windows.agent.started, [])
        mac.mesh._publish_owner("default", mac.service.runtime_id, 1)
        self.assertTrue(until(lambda: windows.done(sent["id"]), 15))
        result = windows.service.get(sent["id"])
        self.assertEqual(result["status"], "done", result.get("error"))
        self.assertEqual(windows.agent.started, [])
        self.assertEqual(mac.agent.started.count("one parked turn"), 1)
