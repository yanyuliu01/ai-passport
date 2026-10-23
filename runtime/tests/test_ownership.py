"""R4.2：对话负责人的绑定和写入隔离（设计第 6 节）。

纯函数部分直接测规则；网络部分用 test_mesh.py 的假网络，两台都在同一个进程里，走真的代码。
"""

import dataclasses
import json
import sys
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_mesh import Network, SECRET  # noqa: E402
from test_runtime import Scripted, TempDirCase, make_xiaoyou, until  # noqa: E402
from xiaoyou_runtime import ownership  # noqa: E402
from xiaoyou_runtime.mesh import Mesh  # noqa: E402
from xiaoyou_runtime.service import Service  # noqa: E402

MAC, WIN, OTHER = "a" * 32, "b" * 32, "c" * 32


def bind(conversation, owner, generation, by, oseq=1):
    return dict(ownership.binding(conversation, owner, generation, by), oseq=oseq)


class RuleTests(unittest.TestCase):
    def test_nothing_bound_means_nobody_in_particular(self):
        self.assertIsNone(ownership.resolve("default", [])["owner"])
        self.assertEqual(ownership.resolve("side-x", [], creator=WIN)["owner"], WIN)
        # 没记创建者的旧旁聊：跟主对话同一个负责人。
        self.assertEqual(ownership.resolve("side-x", [], main_owner=MAC)["owner"], MAC)

    def test_only_the_current_owner_writes_the_next_generation(self):
        records = [bind("default", MAC, 1, MAC), bind("default", OTHER, 2, WIN),  # WIN 不是第 1 代负责人：不算
                   bind("default", WIN, 2, MAC), bind("default", MAC, 3, WIN)]
        view = ownership.resolve("default", records)
        self.assertEqual((view["owner"], view["generation"], view["conflict"]), (MAC, 3, False))
        side = [bind("side-x", MAC, 1, MAC)]  # 创建者是 WIN，MAC 自己写的第 1 代不算
        self.assertEqual(ownership.resolve("side-x", side, creator=WIN)["owner"], WIN)
        side.append(bind("side-x", MAC, 1, WIN))
        self.assertEqual(ownership.resolve("side-x", side, creator=WIN)["owner"], MAC)

    def test_two_home_claims_pick_the_smaller_id_and_report_a_conflict(self):
        view = ownership.resolve("default", [bind("default", WIN, 1, WIN), bind("default", MAC, 1, MAC)])
        self.assertEqual((view["owner"], view["conflict"]), (MAC, True))

    def test_a_computer_that_handed_away_is_fenced_after_that_point(self):
        records = [bind("default", MAC, 1, MAC, oseq=5), bind("default", WIN, 2, MAC, oseq=9)]
        self.assertEqual(ownership.handed_away(MAC, "default", records, WIN), 9)
        self.assertIsNone(ownership.handed_away(MAC, "default", records, MAC))  # 又交回来了
        self.assertIsNone(ownership.handed_away(WIN, "default", records, WIN))


class Node:
    def __init__(self, case, network, name, peers=(), home=False):
        folder = case.folder / name
        self.agent = Scripted("claude")
        self.xiaoyou, self.store = make_xiaoyou(folder, [self.agent])
        self.service = Service(self.xiaoyou, self.store)
        self.url = "http://%s.test" % name.lower()
        config = dataclasses.replace(self.xiaoyou._config, name=name, mesh_secret=SECRET,
                                     mesh_peers=list(peers), mesh_advertise=[self.url], mesh_home=home)
        self.mesh = Mesh(config, self.service, self.xiaoyou, opener=network.open)
        network.nodes[self.url] = self.mesh
        self.mesh.start()
        case.addCleanup(self.close)

    @property
    def id(self):
        return self.service.runtime_id

    def close(self):
        self.mesh.close()
        self.service.close()

    def get(self, message_id):
        return self.service.get(message_id) or {}

    def has_card_from(self, other, card_id):
        return any(card.get("mesh_origin") == other.id and card.get("mesh_id") == card_id
                   for card in self.xiaoyou.cards.recent(None, 1000, include_desktop=True))


class OwnershipMeshTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.network = Network()
        self.mac = Node(self, self.network, "Mac", home=True)
        self.win = Node(self, self.network, "Windows", peers=[self.mac.url])
        self.assertTrue(until(lambda: self.win.service.owner("default")["owner"] == self.mac.id, 15))

    def offline(self, node):
        """node 断网很久了：连不上，别人也认它离线。"""
        self.network.down.add(node.url)
        for other in (self.mac, self.win):
            peer = other.mesh._peers.get(node.id)
            if peer is not None:
                peer.seen = peer.heard = 0

    def test_the_main_conversation_is_answered_by_its_owner_only(self):
        sent = self.win.service.submit("在 Windows 上说的一句", client_id="w1")
        self.assertTrue(until(lambda: self.win.get(sent["id"]).get("status") == "done", 20))
        self.assertEqual(self.win.get(sent["id"])["reply"], "re:在 Windows 上说的一句")
        self.assertIn("在 Windows 上说的一句", self.mac.agent.texts)
        self.assertEqual(self.win.agent.texts, [])  # Windows 没替 Mac 答
        self.assertEqual(self.mac.service.owner("default")["generation"], 1)

    def test_an_unreachable_owner_means_the_message_waits_with_its_id(self):
        self.offline(self.mac)
        sent = self.win.service.submit("Mac 合盖时说的", client_id="w2")
        self.assertTrue(until(lambda: "连不上" in (self.win.get(sent["id"]).get("stage") or ""), 10))
        waiting = self.win.get(sent["id"])
        self.assertEqual((waiting["status"], waiting["operation_state"]), ("queued", "pending"))
        self.assertEqual(self.win.agent.texts, [])
        # 同一个 client_id 重试还是这一句，不会变成第二句。
        self.assertEqual(self.win.service.submit("Mac 合盖时说的", client_id="w2")["id"], sent["id"])
        # Mac 回来了：按原编号交过去，由 Mac 答。
        self.network.down.discard(self.mac.url)
        self.assertTrue(until(lambda: self.win.get(sent["id"]).get("status") == "done", 30))
        self.assertIn("Mac 合盖时说的", self.mac.agent.texts)
        self.assertEqual(self.win.agent.texts, [])

    def test_a_handoff_moves_answering_and_fences_late_writes(self):
        before = self.mac.service.submit("交之前说的", client_id="m1")
        self.assertTrue(until(lambda: self.mac.get(before["id"]).get("status") == "done", 10))
        early = self.mac.get(before["id"])["card"]
        result = self.mac.service.handoff("default", "Windows")
        self.assertEqual((result["status"], result["generation"]), ("applied", 2))
        self.assertTrue(until(lambda: self.win.service.owner("default")["owner"] == self.win.id, 15))
        # 现在在 Mac 上说的，转给 Windows 答。
        after = self.mac.service.submit("交之后说的", client_id="m2")
        self.assertTrue(until(lambda: self.mac.get(after["id"]).get("status") == "done", 20))
        self.assertTrue(any(text.endswith("交之后说的") for text in self.win.agent.texts))
        self.assertFalse(any(text.endswith("交之后说的") for text in self.mac.agent.texts))
        # 交之前 Mac 写的照收；交出去以后 Mac 还硬写的回复，Windows 不收。
        self.assertTrue(until(lambda: self.win.has_card_from(self.mac, early), 15))
        stale = self.mac.xiaoyou.intake.writer("xiaoyou").task_open("default", "交出去以后硬写的", "done")
        self.mac.xiaoyou.intake.writer("xiaoyou").exchange(stale["id"], said=None, say="不该被收下",
                                                           turn_id="t-stale")
        # Mac 之后写的电脑会话卡不受负责人限制：它到了，说明比它早的硬写的那张也已经送到过。
        marker = self.mac.xiaoyou.intake.writer("desktop", actor="test").observed_open(
            "__desktop__", "之后的电脑会话", "unknown", "claude")
        self.assertTrue(until(lambda: self.win.has_card_from(self.mac, marker["id"]), 15))
        self.assertFalse(self.win.has_card_from(self.mac, stale["id"]))

    def test_a_busy_conversation_is_not_handed_off(self):
        busy = self.mac.xiaoyou.cards.open("default", "还在做的事", "working", "claude")
        result = self.mac.service.handoff("default", "Windows")
        self.assertEqual(result["status"], "refused")
        self.assertIn("还在做的事", result["reason"])
        self.assertEqual(self.mac.service.owner("default")["owner"], self.mac.id)
        self.mac.xiaoyou.cards.update(busy["id"], state="done")
        self.assertEqual(self.mac.service.handoff("default", "Windows")["status"], "applied")

    def test_an_offline_target_is_not_handed_to(self):
        self.offline(self.win)
        result = self.mac.service.handoff("default", "Windows")
        self.assertEqual(result["status"], "refused")
        self.assertEqual(self.mac.service.owner("default")["owner"], self.mac.id)

    def test_a_handoff_asked_elsewhere_goes_to_the_owner(self):
        self.assertEqual(self.win.service.handoff("default", "Windows")["status"], "queued")
        self.assertTrue(until(lambda: self.mac.service.owner("default")["owner"] == self.win.id, 15))

    def test_a_forwarded_message_is_not_bounced_back(self):
        # 转来的一方说“第 5 代归你”，这台只知道第 1 代归 Mac：先等绑定同步过来，不转回去。
        message_id = uuid.uuid4().hex
        self.win.service.submit("转过来的一句", client_id=message_id,
                                mesh={"message_id": message_id, "routed_generation": 5})
        self.assertTrue(until(lambda: message_id in self.win.service._parked, 10))
        self.assertEqual(self.win.get(message_id)["status"], "queued")
        self.assertNotIn("转过来的一句", self.mac.agent.texts)
        self.assertEqual(self.win.agent.texts, [])

    def test_side_conversations_belong_to_their_creator(self):
        side = self.win.xiaoyou.intake.writer("owner").conversation_create()
        self.assertEqual(side["owner"], self.win.id)
        self.win.mesh.shares.scan(time.time())  # 旁聊目录随共享记录同步（平时 30 秒一次）
        self.assertTrue(until(lambda: self.mac.service.owner(side["id"])["owner"] == self.win.id, 20))
        sent = self.mac.service.submit("在旁聊里说", conversation=side["id"], client_id="s1")
        self.assertTrue(until(lambda: self.mac.get(sent["id"]).get("status") == "done", 20))
        self.assertTrue(any(text.endswith("在旁聊里说") for text in self.win.agent.texts))
        self.assertFalse(any(text.endswith("在旁聊里说") for text in self.mac.agent.texts))


if __name__ == "__main__":
    unittest.main()
