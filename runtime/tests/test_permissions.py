"""R4.3（Runtime 部分）：主机身份、每台设备的凭据、权限权威和敏感操作、地址范围（设计第 6 节）。"""

import dataclasses
import json
import secrets
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_mesh import Network, SECRET  # noqa: E402
from test_runtime import TOKEN, Scripted, TempDirCase, make_xiaoyu, start_runtime, until  # noqa: E402
from xiaoyu_runtime.world import identity  # noqa: E402
from xiaoyu_runtime.world.devices import Devices, hash_secret, new_credential  # noqa: E402
from xiaoyu_runtime.delivery.mesh import Mesh, MeshError, scope_of  # noqa: E402
from xiaoyu_runtime.api.service import Service  # noqa: E402


class IdentityTests(TempDirCase):
    def test_a_damaged_identity_refuses_to_start_instead_of_changing(self):
        first = identity.runtime_id(self.folder)
        self.assertEqual(identity.runtime_id(self.folder), first)  # 不变
        (self.folder / identity.FILE).write_text("坏了", encoding="utf-8")
        with self.assertRaises(identity.IdentityError):
            identity.runtime_id(self.folder)
        self.assertEqual((self.folder / identity.FILE).read_text(encoding="utf-8"), "坏了")  # 没被悄悄换掉

    def test_the_proof_checks_out_only_with_the_same_credential(self):
        key = hash_secret("s" * 64)
        proof = identity.host_proof(key, "h" * 32, "n" * 16)
        self.assertEqual(proof, identity.host_proof(key, "h" * 32, "n" * 16))
        self.assertNotEqual(proof, identity.host_proof(hash_secret("t" * 64), "h" * 32, "n" * 16))
        self.assertNotEqual(proof, identity.host_proof(key, "g" * 32, "n" * 16))
        self.assertRegex(identity.fingerprint("h" * 32), r"^([0-9a-f]{4} ){5}[0-9a-f]{4}$")


class DeviceTests(TempDirCase):
    def test_issue_verify_revoke_and_revocation_is_final(self):
        devices = Devices(self.folder / "devices.json")
        device_id, credential, secret_hash = new_credential()
        devices.add(device_id, "手机", "phone", secret_hash)
        self.assertEqual(devices.verify(credential)["name"], "手机")
        self.assertIsNone(devices.verify(credential[:-1] + ("0" if credential[-1] != "0" else "1")))
        self.assertEqual(devices.add(device_id, "手机", "phone", secret_hash)["id"], device_id)  # 重复提交
        with self.assertRaises(ValueError):
            devices.add(device_id, "别的", "phone", hash_secret("x"))
        old = devices.record(device_id)
        devices.revoke(device_id)
        self.assertIsNone(devices.verify(credential))
        self.assertFalse(devices.merge(old))  # 迟到的旧记录不能让它复活
        self.assertIsNone(Devices(self.folder / "devices.json").verify(credential))  # 落了盘

    def test_address_scopes(self):
        self.assertEqual(scope_of("http://127.0.0.1:8765"), "loopback")
        self.assertEqual(scope_of("http://192.168.1.5:8765"), "lan")
        self.assertEqual(scope_of("http://100.101.102.103:8765"), "private")
        self.assertEqual(scope_of("http://mac.tail1234.ts.net:8765"), "private")
        self.assertEqual(scope_of("https://mac.tail1234.ts.net"), "public")
        self.assertEqual(scope_of("https://example.com"), "public")


class Node:
    def __init__(self, case, network, name, peers=(), home=False, advertise=None):
        folder = case.folder / name
        self.agent = Scripted("claude")
        self.xiaoyu, self.store = make_xiaoyu(folder, [self.agent])
        self.service = Service(self.xiaoyu, self.store)
        self.url = "http://%s.test" % name.lower()
        config = dataclasses.replace(self.xiaoyu._config, name=name, mesh_secret=SECRET,
                                     mesh_peers=list(peers), mesh_advertise=advertise or [self.url],
                                     mesh_home=home)
        self.mesh = Mesh(config, self.service, self.xiaoyu, opener=network.open)
        network.nodes[self.url] = self.mesh
        self.mesh.start()
        case.addCleanup(self.close)

    @property
    def id(self):
        return self.service.runtime_id

    def close(self):
        self.mesh.close()
        self.service.close()


class PermissionMeshTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.network = Network()
        self.mac = Node(self, self.network, "Mac", home=True,
                        advertise=["http://mac.test", "http://10.0.0.5:8765"])
        self.win = Node(self, self.network, "Windows", peers=["http://mac.test"])
        self.assertTrue(until(lambda: self.win.service.authority() == self.mac.id, 15))

    def offline(self, node):
        self.network.down.add(node.url)
        for other in (self.mac, self.win):
            peer = other.mesh._peers.get(node.id)
            if peer is not None:
                peer.seen = peer.heard = 0

    def test_the_mac_is_the_authority_even_after_a_handoff(self):
        self.assertEqual(self.mac.service.authority(), self.mac.id)
        self.assertEqual(self.mac.service.handoff("default", "Windows")["status"], "applied")
        self.assertTrue(until(lambda: self.win.service.owner("default")["owner"] == self.win.id, 15))
        self.assertEqual(self.win.service.authority(), self.mac.id)

    def test_a_device_paired_on_windows_is_recorded_by_the_mac(self):
        result = self.win.service.device_add("我的手机", "phone")
        self.assertEqual(result["status"], "queued")
        credential = result["credential"]
        self.assertTrue(until(lambda: self.mac.service.devices.verify(credential) is not None, 15))
        self.assertTrue(until(lambda: self.win.service.devices.verify(credential) is not None, 15))
        # 密钥只在发起配对的那台出现过：网络里传的只有哈希。
        secret = credential.rsplit(".", 1)[1]
        for folder in (self.mac.store.state_dir, self.win.store.state_dir):
            for path in folder.rglob("*"):
                if path.is_file() and path.stat().st_size < 4_000_000:
                    self.assertNotIn(secret, path.read_text(encoding="utf-8", errors="ignore"), str(path))
        # 在 Mac 上撤销：两台都不再认。
        device_id = credential.split(".")[1]
        self.assertEqual(self.mac.service.device_revoke(device_id)["status"], "applied")
        self.assertTrue(until(lambda: self.win.service.devices.verify(credential) is None, 15))

    def test_sensitive_operations_need_the_mac_online(self):
        self.offline(self.mac)
        problem = self.win.service.permission_problem()
        self.assertIn("不在线", problem)
        self.assertEqual(self.win.service.device_add("新手机")["status"], "refused")
        self.assertEqual(self.win.service.handoff("default", "Mac")["status"], "refused")
        # Mac 自己不受影响。
        self.assertIsNone(self.mac.service.permission_problem())

    def test_a_removed_computer_can_no_longer_exchange_records(self):
        self.assertEqual(self.mac.service.member_remove("Windows")["status"], "applied")
        with self.assertRaises(MeshError):
            self.mac.mesh.exchange({"from": self.win.mesh.info(), "cursors": {}})
        self.assertTrue(until(lambda: self.win.id in self.mac.mesh.status()["removed"], 5))
        self.assertNotIn(self.win.id, [peer["runtime_id"] for peer in self.mac.mesh.status()["peers"]])

    def test_manual_routes_stay_first_and_carry_their_scope(self):
        self.assertTrue(until(lambda: len(self.win.mesh._peers.get(self.mac.id).urls if self.mac.id in self.win.mesh._peers else []) == 2, 15))
        peer = next(item for item in self.win.mesh.status()["peers"] if item["runtime_id"] == self.mac.id)
        self.assertEqual(peer["routes"][0], {"url": "http://mac.test", "scope": "public", "manual": True})
        self.assertEqual(peer["routes"][1], {"url": "http://10.0.0.5:8765", "scope": "lan", "manual": False})


class DeviceHttpTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.base, self.config = start_runtime(self, self.folder)

    def call(self, method, path, body=None, token=TOKEN):
        request = urllib.request.Request(self.base + path, method=method,
                                         data=json.dumps(body).encode() if body is not None else None)
        request.add_header("Authorization", "Bearer " + token)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def test_a_device_credential_works_proves_the_host_and_stops_after_revocation(self):
        status, paired = self.call("POST", "/v1/devices", {"name": "测试手机", "kind": "phone"})
        self.assertEqual((status, paired["status"]), (200, "applied"))
        credential = paired["credential"]
        nonce = secrets.token_hex(16)
        status, info = self.call("GET", "/v1/info?nonce=" + nonce, token=credential)
        self.assertEqual(status, 200)
        key = hash_secret(credential.rsplit(".", 1)[1])
        self.assertEqual(info["host"]["proof"], identity.host_proof(key, info["runtime_id"], nonce))
        self.assertEqual(info["host"]["fingerprint"], identity.fingerprint(info["runtime_id"]))
        # 旧的共享令牌照常可用，也能拿到证明（用它自己的哈希）。
        status, legacy = self.call("GET", "/v1/info?nonce=" + nonce)
        self.assertEqual(legacy["host"]["proof"], identity.host_proof(hash_secret(TOKEN), info["runtime_id"], nonce))
        self.assertEqual(self.call("GET", "/v1/devices")[1]["devices"][0]["name"], "测试手机")
        self.assertNotIn("hash", json.dumps(self.call("GET", "/v1/devices")[1]))
        status, _ = self.call("POST", "/v1/devices/%s/revoke" % credential.split(".")[1], {})
        self.assertEqual(status, 200)
        self.assertEqual(self.call("GET", "/v1/info", token=credential)[0], 401)


if __name__ == "__main__":
    unittest.main()
