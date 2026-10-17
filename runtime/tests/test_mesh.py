"""小幽网络（mesh.py）：几台 Runtime 互相同步、转交，不经真的网络。

每台 Runtime 是同一个进程里的一套 Xiaoyou + Service + Mesh；HTTP 调用由一个假的 opener 直接交给
对方的 Mesh.exchange（也检查网络口令），所以拉、推、长轮询、转告都走真的代码。
"""

import dataclasses
import io
import json
import threading
import time
import unittest
import urllib.error

import urllib.request

from test_runtime import TOKEN, Scripted, TempDirCase, free_port, make_xiaoyou, until, write_config

from xiaoyou_runtime import agents as agents_module
from xiaoyou_runtime import config as config_module
from xiaoyou_runtime.mesh import Mesh, local_id, subject_for
from xiaoyou_runtime.server import make_server
from xiaoyou_runtime.service import Service
from xiaoyou_runtime.store import Store
from xiaoyou_runtime.xiaoyou import Xiaoyou

SECRET = "mesh-secret-for-tests-0123456789"


class Network:
    """假的网络：地址 → 那台的 Mesh；down 里的地址连不上。"""

    def __init__(self):
        self.nodes = {}
        self.down = set()
        self.calls = []

    def open(self, request, timeout=None):
        url = request.full_url
        base = url[:url.index("/v1/")]
        self.calls.append(base)
        node = self.nodes.get(base)
        if node is None or base in self.down:
            raise urllib.error.URLError("unreachable %s" % base)
        if not node.authorized(request.get_header("Authorization") or ""):
            raise urllib.error.HTTPError(url, 401, "bad secret", {}, io.BytesIO(b"{}"))
        reply = node.exchange(json.loads(request.data.decode("utf-8")))
        body = json.dumps(reply, ensure_ascii=False).encode("utf-8")

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return Response(body)


class Node:
    def __init__(self, case, network, name, peers=(), advertise=True, agents=None, secret=SECRET):
        folder = case.folder / name
        self.agent = agents[0] if agents else Scripted("claude")
        self.xiaoyou, self.store = make_xiaoyou(folder, agents or [self.agent])
        self.service = Service(self.xiaoyou, self.store)
        self.url = "http://%s.test" % name.lower()
        config = dataclasses.replace(self.xiaoyou._config, name=name, mesh_secret=secret,
                                     mesh_peers=list(peers),
                                     mesh_advertise=[self.url] if advertise else [])
        self.mesh = Mesh(config, self.service, self.xiaoyou, opener=network.open)
        if advertise:
            network.nodes[self.url] = self.mesh
        self.mesh.start()
        case.addCleanup(self.close)

    def close(self):
        self.mesh.close()
        self.service.close()

    def card_from(self, other, card_id):
        """other 那台的一件事在这台的本地卡。"""
        for card in self.xiaoyou.cards.recent(None, 1000, include_desktop=True):
            if card.get("mesh_origin") == other.service.runtime_id and card.get("mesh_id") == card_id:
                return card
        return None

    def done(self, message_id, seconds=10):
        return until(lambda: (self.service.get(message_id) or {}).get("status") in ("done", "failed"), seconds)


class MeshTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.network = Network()
        self.addCleanup(lambda: [mesh.close() for mesh in self.network.nodes.values()])

    def node(self, name, **kwargs):
        return Node(self, self.network, name, **kwargs)

    def test_two_runtimes_share_one_subject_and_each_others_records(self):
        a = self.node("Mac")
        b = self.node("Windows", peers=[a.url])
        self.assertEqual(a.service.info()["subject_id"], subject_for(SECRET))
        self.assertEqual(a.service.info()["subject_id"], b.service.info()["subject_id"])
        self.assertIn("mesh", b.service.info()["capabilities"])
        sent = a.service.submit("在 Mac 上说的一句", client_id="c1")
        self.assertTrue(a.done(sent["id"]))
        card = a.service.get(sent["id"])["card"]
        # 卡可能先以开头的样子到，内容随后跟上。
        self.assertTrue(until(lambda: len((b.card_from(a, card) or {}).get("entries", [])) == 2))
        mirrored = b.card_from(a, card)
        self.assertEqual("Mac", mirrored["origin_name"])
        self.assertEqual(["在 Mac 上说的一句", "re:在 Mac 上说的一句"], [entry["text"] for entry in mirrored["entries"]])
        # 同步接口里：消息编号带着产地，事是这里的本地卡。
        page = b.service.sync("default", 0)
        ids = {message["id"] for message in page["messages"]}
        self.assertIn(local_id(a.service.runtime_id, sent["id"] + ":you"), ids)
        self.assertIn(mirrored["id"], {task["id"] for task in page["tasks"]})
        # 那台的小幽答过的话，这台的小幽下次接话时会被告知。
        self.assertIn(sent["id"], [turn["id"] for turn in b.store.transcript.turns("default")])
        # 反过来也一样（B 连得上 A，A 也从 B 那里知道 B 的地址并连回去）。
        back = b.service.submit("在 Windows 上说的", client_id="c2")
        self.assertTrue(b.done(back["id"]))
        self.assertTrue(until(lambda: a.card_from(b, b.service.get(back["id"])["card"]) is not None))

    def test_records_reach_runtimes_that_only_know_a_relay(self):
        hub = self.node("Hub")
        left = self.node("Left", peers=[hub.url])
        right = self.node("Right", peers=[hub.url])
        sent = left.service.submit("从左边说", client_id="l1")
        self.assertTrue(left.done(sent["id"]))
        card = left.service.get(sent["id"])["card"]
        self.assertTrue(until(lambda: right.card_from(left, card) is not None, 15))
        # Right 从 Hub 那里知道了 Left 的地址，之后直接连 Left。
        self.assertTrue(until(lambda: any(peer["name"] == "Left" for peer in right.mesh.status()["peers"])))

    def test_a_runtime_nobody_can_reach_pushes_its_records(self):
        home = self.node("Home")
        cloud = self.node("Cloud", peers=[home.url], advertise=False)
        sent = cloud.service.submit("云上说的", client_id="x1")
        self.assertTrue(cloud.done(sent["id"]))
        card = cloud.service.get(sent["id"])["card"]
        self.assertTrue(until(lambda: home.card_from(cloud, card) is not None))
        self.assertNotIn("http://cloud.test", self.network.calls)

    def test_a_sentence_in_another_runtimes_task_is_done_there(self):
        a = self.node("Mac")
        b = self.node("Windows", peers=[a.url])
        sent = a.service.submit("开个头", client_id="s1")
        self.assertTrue(a.done(sent["id"]))
        card = a.service.get(sent["id"])["card"]
        self.assertTrue(until(lambda: b.card_from(a, card) is not None))
        alias = b.card_from(a, card)["id"]
        relayed = b.service.submit("接着说一句", client_id="s2", card=alias, pin=True)
        self.assertTrue(b.done(relayed["id"]))
        finished = b.service.get(relayed["id"])
        self.assertEqual("done", finished["status"], finished.get("error"))
        # 那台在自己的那件事里接住了这句话，用的是同一个编号。
        self.assertTrue(until(lambda: any(entry["id"] == relayed["id"] + ":you"
                                          for entry in a.xiaoyou.cards.get(card)["entries"])))
        self.assertIsNotNone(a.service.get(relayed["id"]))
        # 这台的聊天里这句话只有一条（占位被那台的记录替换，编号不变）。
        self.assertTrue(until(lambda: (b.xiaoyou.cards.message(relayed["id"] + ":you") or {}).get("mesh_origin")
                              == a.service.runtime_id))
        page = b.service.sync("default", 0)
        texts = [message["text"] for message in page["messages"] if message["text"] == "接着说一句"]
        self.assertEqual(1, len(texts))
        # 转交出去的占位不会再转给别人。
        self.assertNotIn(relayed["id"] + ":you", {message["id"] for message in b.xiaoyou.cards.own_since(0)[2]})

    def test_relay_to_an_offline_runtime_fails_with_a_reason(self):
        a = self.node("Mac")
        b = self.node("Windows", peers=[a.url])
        sent = a.service.submit("开个头", client_id="o1")
        self.assertTrue(a.done(sent["id"]))
        card = a.service.get(sent["id"])["card"]
        self.assertTrue(until(lambda: b.card_from(a, card) is not None))
        # Mac 关机了：两边都连不上（正在等的那次交换先收尾），过了一阵子。
        self.network.down.update({a.url, b.url})
        a.mesh.close()
        time.sleep(1.5)
        clock = [10 ** 10]
        b.mesh._clock = lambda: clock[0]
        relayed = b.service.submit("Mac 关机了", client_id="o2", card=b.card_from(a, card)["id"], pin=True)
        self.assertTrue(b.done(relayed["id"]))
        self.assertIn("离线", b.service.get(relayed["id"])["error"])

    def test_cancel_and_approval_answers_go_to_the_task_owner(self):
        worker = Scripted("claude")
        a = self.node("Mac", agents=[worker])
        b = self.node("Windows", peers=[a.url])
        card = a.xiaoyou.record.task_open("default", "长任务", "working", "claude")
        approval = a.xiaoyou.approvals.ask(card["id"], "default", "claude", "claude · Bash", "rm -rf build")
        self.assertTrue(until(lambda: b.card_from(a, card["id"]) is not None))
        mirrored = local_id(a.service.runtime_id, approval)
        self.assertTrue(until(lambda: any(item["id"] == mirrored for item in b.xiaoyou.approvals.pending())))
        self.assertTrue(b.service.approve(mirrored, "allow"))
        self.assertTrue(until(lambda: (a.xiaoyou.approvals.get(approval) or {}).get("status") == "allowed"))
        self.assertTrue(until(lambda: not any(item["id"] == mirrored for item in b.xiaoyou.approvals.pending())))
        b.service.cancel(b.card_from(a, card["id"])["id"])
        self.assertTrue(until(lambda: a.xiaoyou.cards.get(card["id"])["state"] == "cancelled"))
        self.assertTrue(until(lambda: b.card_from(a, card["id"])["state"] == "cancelled"))

    def test_xiaoyou_hands_work_to_a_helper_on_another_runtime(self):
        a = self.node("Mac", agents=[Scripted("claude"), Scripted("codex", speaks=False)])
        b = self.node("Windows", peers=[a.url])
        self.assertTrue(until(lambda: b.xiaoyou.has("codex@Mac"), 35) or self._refresh(b, "codex@Mac"))
        sent = b.service.submit("看看日志", client_id="d1", agent="codex@Mac")
        self.assertTrue(b.done(sent["id"]))
        card = b.service.get(sent["id"])["card"]
        self.assertTrue(until(lambda: b.xiaoyou.cards.get(card)["state"] == "done", 15))
        finished = b.xiaoyou.cards.get(card)
        self.assertEqual({"runtime": a.service.runtime_id, "name": "Mac"}, finished["delegated_to"])
        self.assertIn("看看日志", finished["entries"][-1]["text"])
        # Mac 替这件事开的卡记着替谁做；Windows 不再另外显示它。
        worker = [item for item in a.xiaoyou.cards.recent(None, 100) if item.get("delegated_from")]
        self.assertEqual(1, len(worker))
        self.assertEqual(b.service.runtime_id, worker[0]["delegated_from"]["runtime"])
        self.assertIsNone(b.card_from(a, worker[0]["id"]))

    def test_continuing_another_runtimes_desktop_session_happens_on_that_runtime(self):
        heir = Scripted("cc", kind="claude_code")
        a = self.node("Mac", agents=[Scripted("claude"), heir])
        b = self.node("Windows", peers=[a.url])
        desktop = a.xiaoyou.record.observed_open("__desktop__", "改同步方案", "done", "claude")
        a.xiaoyou.record.observed(desktop["id"], monitoring_only=True, source="desktop", session_id="s-mac",
                                  desktop_cwd=str(self.folder), desktop_status="idle", entries=[
                                      {"id": "e1", "role": "you", "text": "先出方案", "at": 1.0}])
        self.assertTrue(until(lambda: b.card_from(a, desktop["id"]) is not None))
        mirrored = b.card_from(a, desktop["id"])
        self.assertTrue(b.mesh.amend(mirrored, "照方案改代码", continue_desktop=True))
        # Mac 自己开一件「接着：…」，交给它能接 Claude 会话的帮手，在那个会话里接着做。
        self.assertTrue(until(lambda: any(card["title"].startswith("接着：") and card["agent"] == "cc"
                                          for card in a.xiaoyou.cards.recent(None, 100)), 10))
        self.assertTrue(until(lambda: heir.jobs and heir.jobs[-1].session_id == "s-mac", 10))
        self.assertFalse(any(card["title"].startswith("接着：") for card in b.xiaoyou.cards.recent(None, 100)
                             if not card.get("mesh_origin")))

    def _refresh(self, node, name):
        node.mesh.sweep()
        return until(lambda: node.xiaoyou.has(name))

    def test_wrong_secret_is_refused(self):
        a = self.node("Mac")
        b = self.node("Intruder", peers=[a.url], secret="another-secret-0123456789abc")
        self.assertTrue(until(lambda: any(peer["error"] or peer["runtime_id"] for peer in b.mesh.status()["peers"])
                              or b.mesh.status()["unresolved"], 3))
        self.assertEqual([], a.mesh.status()["peers"])

    def test_replicas_and_local_cards_survive_a_restart(self):
        a = self.node("Mac")
        b = self.node("Windows", peers=[a.url])
        sent = a.service.submit("记住这件事", client_id="r1")
        self.assertTrue(a.done(sent["id"]))
        card = a.service.get(sent["id"])["card"]
        self.assertTrue(until(lambda: b.card_from(a, card) is not None))
        alias = b.card_from(a, card)["id"]
        b.close()
        del self.network.nodes[b.url]
        again = self.node("Windows", peers=[a.url])
        self.assertEqual(alias, again.card_from(a, card)["id"])
        self.assertEqual(b.service.runtime_id, again.service.runtime_id)
        # 重启后不会把别人的事标成“Runtime 重启了，没做完”。
        self.assertNotEqual("failed", again.card_from(a, card)["state"])


class MeshConfigTests(TempDirCase):
    def load(self, env=None, **overrides):
        return config_module.load(write_config(self.folder, **overrides), env or {})

    def test_mesh_is_off_without_a_secret_and_needs_a_long_one(self):
        self.assertIsNone(self.load().mesh_secret)
        with self.assertRaises(config_module.ConfigError):
            self.load(mesh={"secret": "short"})
        with self.assertRaises(config_module.ConfigError):
            self.load(mesh={"peers": ["http://other.test"]})
        with self.assertRaises(config_module.ConfigError):
            self.load(mesh={"secret": SECRET, "peers": ["other.test"]})

    def test_peers_secret_and_advertised_address_come_from_config_env_or_the_listen_address(self):
        (self.folder / "mesh.key").parent.mkdir(parents=True, exist_ok=True)
        (self.folder / "mesh.key").write_text(SECRET + "\n", encoding="utf-8")
        loaded = self.load(mesh={"secret_file": "mesh.key", "peers": ["https://home.test/"]},
                           server={"host": "100.1.2.3", "port": 9000, "token": TOKEN})
        self.assertEqual((SECRET, ["https://home.test"], ["http://100.1.2.3:9000"]),
                         (loaded.mesh_secret, loaded.mesh_peers, loaded.mesh_advertise))
        # 只监听本机：不对外说地址（只往外连）。
        self.assertEqual([], self.load(mesh={"secret": SECRET}).mesh_advertise)
        loaded = self.load({"XIAOYOU_MESH_SECRET": SECRET, "XIAOYOU_MESH_PEERS": "http://a.test, https://b.test",
                            "XIAOYOU_MESH_ADVERTISE": "https://me.test"})
        self.assertEqual((["http://a.test", "https://b.test"], ["https://me.test"]),
                         (loaded.mesh_peers, loaded.mesh_advertise))

    def test_desktop_sync_can_be_turned_on_in_the_config(self):
        self.assertFalse(self.load().desktop_sync)
        self.assertTrue(self.load(desktop_sync=True).desktop_sync)
        loaded = self.load(desktop_sync={"enabled": True, "projects": ["work"]})
        self.assertEqual((True, [(self.folder / "work").resolve()]), (loaded.desktop_sync, loaded.desktop_projects))


class MeshHttpTests(TempDirCase):
    """两台真的在本机端口上跑的 Runtime，经真的 HTTP 交换。"""

    def runtime(self, name, peers=()):
        port = free_port()
        url = "http://127.0.0.1:%d" % port
        loaded = config_module.load(write_config(self.folder / name), {
            "XIAOYOU_PORT": str(port), "XIAOYOU_NAME": name, "XIAOYOU_MESH_SECRET": SECRET,
            "XIAOYOU_MESH_PEERS": ",".join(peers), "XIAOYOU_MESH_ADVERTISE": url})
        store = Store(loaded.state_dir, legacy_agent=loaded.default_agent)
        xiaoyou = Xiaoyou(loaded, [agents_module.create(spec) for spec in loaded.agents], store)
        service = Service(xiaoyou, store)
        mesh = Mesh(loaded, service, xiaoyou)
        server = make_server(loaded, service)
        server.RequestHandlerClass.log_message = lambda *args: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        mesh.start()
        self.addCleanup(service.close)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(mesh.close)
        return url, service, xiaoyou

    def call(self, method, url, body=None, token=TOKEN):
        request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                         method=method)
        if token:
            request.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read() or b"{}")

    def test_two_runtimes_sync_over_http_and_the_exchange_needs_the_mesh_secret(self):
        home, home_service, _ = self.runtime("Home")
        laptop, _, laptop_xiaoyou = self.runtime("Laptop", peers=[home])
        self.assertEqual(401, self.call("POST", home + "/v1/mesh/exchange", {}, token=None)[0])
        self.assertEqual(401, self.call("POST", home + "/v1/mesh/exchange", {}, token=TOKEN)[0])
        self.assertEqual(401, self.call("GET", home + "/v1/mesh", token=SECRET)[0])
        sent = home_service.submit("家里说的", client_id="h1")
        self.assertTrue(until(lambda: home_service.get(sent["id"])["status"] == "done"))
        card = home_service.get(sent["id"])["card"]
        self.assertTrue(until(lambda: any(item.get("mesh_id") == card and len(item["entries"]) == 2
                                          for item in laptop_xiaoyou.cards.recent(None, 100)), 10))
        status, mesh = self.call("GET", laptop + "/v1/mesh")
        self.assertEqual(200, status)
        self.assertEqual(["Home"], [peer["name"] for peer in mesh["peers"]])
        self.assertTrue(mesh["peers"][0]["online"])
        status, info = self.call("GET", laptop + "/v1/info")
        self.assertIn("mesh", info["capabilities"])
        self.assertEqual(subject_for(SECRET), info["subject_id"])


if __name__ == "__main__":
    unittest.main()
