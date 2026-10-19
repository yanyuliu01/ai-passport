"""Auto-iteration pipeline: app events, plugins and permissions, a whole run, scheduling.
See docs/development/auto-iteration."""

import json
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from test_runtime import TOKEN, TempDirCase, start_runtime
from xiaoyou_runtime.iterate import plugins as plugins_module
from xiaoyou_runtime.iterate.builtin import planner, sources
from xiaoyou_runtime.iterate.objects import Issue
from xiaoyou_runtime.iterate.pipeline import Runner
from xiaoyou_runtime.iterate.scheduler import Scheduler
from xiaoyou_runtime.iterate.settings import IterateConfigError, parse
from xiaoyou_runtime.iterate.events import EventError, EventStore, EventType, Types, flat
from xiaoyou_runtime.iterate.store import IssueBook, Lock, Busy, Runs
from xiaoyou_runtime.model_gateway import Completion, GatewayError

NOW = 1_800_000_000.0
JAVA = "companion/android/app/src/main/java/com/yanyuliu/pockethub/MainActivity.java"


def event(kind, index, **fields):
    return dict({"id": "e%s%d" % (kind, index), "type": kind, "at": int((NOW - 60 + index) * 1000),
                 "app_version": "0.18.1", "session": "s1"}, **fields)


class FakeGateway:
    def __init__(self, answers):
        self.answers, self.purposes = list(answers), []

    def available(self, purpose):
        return True

    def complete_json(self, purpose, messages, check, attempts=3, max_tokens=1024):
        self.purposes.append((purpose, messages))
        if purpose == "iterate.classify" and not self.answers:
            return {"category": "app_ui"}, Completion(text="", provider="local", model="qwen", locality="local")
        for _ in range(attempts):
            value = self.answers.pop(0)
            if check(value) is None:
                return value, Completion(text="", provider="local", model="qwen", locality="local")
        raise GatewayError("bad")


def good_plan(file=JAVA):
    return {"summary": "崩溃处加空值保护", "problem": "openTask 为空时取卡片会崩",
            "changes": [{"file": file, "intent": "show() 里 openTask 为空时回首页"}],
            "acceptance": ["从通知点进已删除的任务不再闪退"], "out_of_scope": [], "risk": "L1",
            "risk_reason": "只加判断"}


class IterateCase(TempDirCase):
    def setUp(self):
        super().setUp()
        self.state = self.folder / "state"
        self.repo = self.folder / "repo"
        source = self.repo / JAVA
        source.parent.mkdir(parents=True)
        (self.repo / ".git").mkdir()
        source.write_text("\n".join("line %d" % n for n in range(1, 200)) +
                          "\n    private void show(int next) {\n        toast(\"加载失败\");\n    }\n",
                          encoding="utf-8")
        self.settings = parse({"repo_dir": str(self.repo), "trigger": {"min_new_signals": 5}}, self.folder)
        self.config = SimpleNamespace(state_dir=self.state, base_dir=self.folder, host="127.0.0.1",
                                      port=1, token=TOKEN, iterate={}, models=None)

    def write_cards(self, cards):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "cards.json").write_text(json.dumps({"cards": cards}, ensure_ascii=False), encoding="utf-8")

    def runner(self, gateway=None, now=NOW, posted=None, settings=None, leaser=None):
        self.leases = []
        def grant(action, stage, seconds=0):
            self.leases.append((action, stage))
            return {"granted": True}
        return Runner(self.config, settings or self.settings, gateway=gateway or FakeGateway([]), now=now,
                      poster=lambda config, payload: (posted if posted is not None else []).append(payload),
                      leaser=leaser or grant)


class EventsTest(IterateCase):
    def test_legacy_and_envelope_events_are_cleaned_and_deduplicated(self):
        store = EventStore(self.state, origin="mac")
        result = store.accept({"events": [
            event("crash", 1, exception="NullPointerException", frames=["a"] * 20, text="私密内容"),
            {"id": "e2", "at": int(NOW * 1000), "type": "screen", "session": "s1",
             "client": {"kind": "desktop", "id": "pc-1", "version": "0.1", "platform": "macos"},
             "fields": {"name": "chat", "ms": 1200, "typed": "不该收"}},
            {"id": "bad id!", "type": "crash", "at": 1},
            {"id": "x", "type": "keystroke", "at": 1},
            {"id": "y", "type": "screen", "at": 1, "client": {"kind": "fridge"}},
        ]}, received_at=NOW)
        self.assertEqual((result["accepted"], result["rejected"], result["duplicates"]), (2, 3, 0))
        events, cursor = store.read_after(None)
        self.assertEqual([e["type"] for e in events], ["crash", "screen"])
        crash, screen = events
        self.assertEqual(crash["client"], {"kind": "android", "version": "0.18.1"})
        self.assertNotIn("text", crash["fields"])          # unregistered fields are dropped
        self.assertEqual(len(crash["fields"]["frames"]), 8)
        self.assertEqual(screen["client"]["kind"], "desktop")
        self.assertNotIn("typed", screen["fields"])
        self.assertEqual((crash["origin"], crash["at"]), ("mac", NOW - 59))
        self.assertEqual(flat(crash)["app_version"], "0.18.1")
        again = store.accept({"events": [event("crash", 1, exception="x")]}, received_at=NOW)
        self.assertEqual((again["accepted"], again["duplicates"]), (0, 1))
        self.assertEqual(store.ingest(events + [dict(screen, id="e3")], origin="win", received_at=NOW), 1)
        more, _ = store.read_after(cursor)
        self.assertEqual([(e["id"], e["origin"]) for e in more], [("e3", "mac")])
        with self.assertRaises(EventError):
            store.accept({"events": [event("screen", n) for n in range(201)]})

    def test_types_can_be_added_and_retention_is_per_type(self):
        types = Types([EventType("voice_retry", {"count": int}, "usage", 1)])
        store = EventStore(self.state, types)
        store.accept({"events": [
            {"id": "v1", "type": "voice_retry", "at": int((NOW - 2 * 86400) * 1000), "count": 2},
            {"id": "s1", "type": "screen", "at": int((NOW - 2 * 86400) * 1000), "name": "home"}]},
            received_at=NOW - 2 * 86400)
        self.assertEqual(store.prune(NOW), 1)
        self.assertEqual([e["id"] for e in store.all()], ["s1"])
        self.assertEqual(store.delete(kind="screen"), 1)
        self.assertEqual(store.all(), [])
        self.assertIn("voice_retry", [t["name"] for t in types.describe()["types"]])

    def test_http_endpoints_and_info_declaration(self):
        url, loaded = start_runtime(self, self.folder / "rt")
        def post(path, body):
            request = urllib.request.Request(url + path, method="POST", data=json.dumps(body).encode("utf-8"),
                                             headers={"Authorization": "Bearer " + TOKEN,
                                                      "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read())
        self.assertEqual(post("/v1/events", {"events": [event("error", 1, screen="chat", message="发送失败")]}),
                         {"accepted": 1, "rejected": 0, "duplicates": 0})
        self.assertEqual(post("/v1/app/events", {"events": [event("error", 1)]})["duplicates"], 1)
        self.assertTrue(post("/v1/iterate/lease", {"action": "acquire", "stage": "detect"})["granted"])
        request = urllib.request.Request(url + "/v1/info", headers={"Authorization": "Bearer " + TOKEN})
        with urllib.request.urlopen(request, timeout=5) as response:
            info = json.loads(response.read())
        self.assertIn("events_v1", info["capabilities"])
        self.assertIn("crash", [t["name"] for t in info["events"]["types"]])
        self.assertIn("collect", info["iterate"]["stages"])
        self.assertEqual(EventStore(loaded.state_dir).all()[0]["fields"]["message"], "发送失败")


class MeshEventsTest(IterateCase):
    def test_events_reach_every_computer_and_leases_exclude_each_other(self):
        from test_mesh import Network, Node
        from test_runtime import until
        network = Network()
        self.addCleanup(lambda: [mesh.close() for mesh in network.nodes.values()])
        a = Node(self, network, "Mac")
        b = Node(self, network, "Windows", peers=[a.url])
        stored = EventStore(a.xiaoyou._config.state_dir, origin=a.service.runtime_id).accept(
            {"events": [event("crash", 1, exception="Boom")]})
        a.mesh.publish_events(stored["events"])
        b_store = EventStore(b.xiaoyou._config.state_dir)
        self.assertTrue(until(lambda: len(b_store.all()) == 1))
        self.assertEqual(b_store.all()[0]["origin"], a.service.runtime_id)

        self.assertTrue(a.mesh.lease("acquire", "detect", 3600)["granted"])
        self.assertTrue(until(lambda: any(item["holder"] == a.service.runtime_id for item in b.mesh.leases())))
        refused = b.mesh.lease("acquire", "detect", 3600)
        self.assertFalse(refused["granted"])
        self.assertEqual(refused["holder_name"], "Mac")
        self.assertTrue(a.mesh.lease("renew", "plan", 3600)["granted"])
        a.mesh.lease("release", "done", 60)
        self.assertTrue(until(lambda: b.mesh.lease("acquire", "detect", 3600)["granted"]))


class PluginTest(IterateCase):
    def test_context_enforces_declared_permissions(self):
        plugin = plugins_module.Plugin(name="p", kind="detector", run=lambda ctx, s: [])
        ctx = plugins_module.Context(plugin, self.settings, self.state)
        for name in ("state_dir", "repo", "gateway"):
            with self.assertRaises(plugins_module.PermissionDenied):
                getattr(ctx, name)
        self.assertFalse(ctx.model_available("x"))

    def test_a_plugin_dir_can_add_and_replace_plugins(self):
        folder = self.folder / "plugins"
        folder.mkdir()
        (folder / "mine.py").write_text(
            "PLUGINS = [{'name': 'crash', 'kind': 'detector', 'run': lambda ctx, s: [], 'description': 'mine'},\n"
            "           {'name': 'extra', 'kind': 'source', 'run': lambda ctx, c: ([], c)}]\n", encoding="utf-8")
        (folder / "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
        registry = plugins_module.builtin_registry([folder])
        self.assertEqual(registry.get("detector", "crash").description, "mine")
        registry.get("source", "extra")
        self.assertTrue(any("boom" in error for error in registry.errors))
        with self.assertRaises(plugins_module.PluginError):
            plugins_module.Registry().add_module(SimpleNamespace(PLUGINS=[
                {"name": "x", "kind": "source", "run": print, "permissions": ["root"]}]), "t")

    def test_settings_are_validated(self):
        for raw in ({"pipeline": {"nope": []}}, {"pipeline": {"source": "one"}}, {"areas": ["web"]},
                    {"trigger": {"interval_hours": 0}}, {"votes": 9}):
            with self.assertRaises(IterateConfigError):
                parse(raw, self.folder)


class PlannerTest(IterateCase):
    def issue(self, **hints):
        return Issue(fingerprint="crash:1", detector="crash", area="app", title="App 崩溃", severity=5,
                     count=1, first_seen=NOW, last_seen=NOW, hints=hints)

    def test_context_comes_from_stack_frames_and_terms(self):
        found = planner.gather_context(self.repo, self.settings, self.issue(
            frames=["com.yanyuliu.pockethub.MainActivity.show(MainActivity.java:200)"]))
        self.assertEqual(found[0]["file"], JAVA)
        self.assertIn("private void show", found[0]["snippets"][0])
        by_text = planner.gather_context(self.repo, self.settings, self.issue(message="加载失败"))
        self.assertIn("加载失败", by_text[0]["snippets"][0])
        self.assertEqual(planner.gather_context(self.repo, self.settings, self.issue(message="不存在的词")), [])

    def test_plans_are_checked_and_risk_has_a_floor(self):
        files = [JAVA]
        self.assertIsNone(planner.check_plan(good_plan(), files, self.settings))
        self.assertIn("不在相关代码里", planner.check_plan(good_plan("Other.java"), files, self.settings))
        too_many = dict(good_plan(), changes=[good_plan()["changes"][0]] * 4)
        self.assertIn("1 到 3", planner.check_plan(too_many, files, self.settings))
        self.assertEqual(planner.risk_floor(["x/RuntimeClient.java"]), "L2")
        self.assertEqual(planner.risk_floor(["x/AppUpdate.java"]), "L3")
        self.assertEqual(planner.risk_floor([JAVA]), "L1")


class PipelineTest(IterateCase):
    def seed(self):
        EventStore(self.state).accept({"events": [
            event("crash", 1, exception="java.lang.NullPointerException: card",
                  frames=["com.yanyuliu.pockethub.MainActivity.show(MainActivity.java:200)"]),
        ] + [event("rage_tap", n, screen="home", region="2,3", count=5) for n in range(2, 6)]},
            received_at=NOW)
        self.write_cards([
            {"id": "c1", "state": "failed", "agent": "tailor", "brief": "模型 claude-x 不可用",
             "finished_at": NOW - 100, "entries": [
                 {"role": "xiaoyou", "text": "好的", "at": NOW - 120},
                 {"role": "you", "text": "首页头像又偏了", "at": NOW - 110, "id": "m1"},
                 {"role": "you", "text": "谢谢", "at": NOW - 105, "id": "m2"}]},
            {"id": "c2", "monitoring_only": True, "state": "failed", "finished_at": NOW - 90,
             "entries": [{"role": "you", "text": "又不对", "at": NOW - 95}]},
        ])

    def test_a_run_collects_detects_picks_one_issue_and_plans_it(self):
        self.seed()
        posted, gateway = [], FakeGateway([])
        gateway.answers = [{"summary": "x"}, good_plan()]
        gateway.available = lambda purpose: purpose == "iterate.plan"  # chat uses keywords here
        run = self.runner(gateway, posted=posted).run(force=True)
        self.assertEqual(run["state"], "planned", run.get("error"))
        self.assertEqual(run["stages"]["collect"]["app_events"]["signals"], 5)
        self.assertEqual(run["stages"]["collect"]["chat_feedback"]["signals"], 1)  # 谢谢 and desktop cards skipped
        self.assertEqual(run["issue"]["detector"], "crash")  # severity 5 wins, one crash is enough
        self.assertEqual(run["plan"]["model"], "local/qwen")
        plans = [messages for purpose, messages in gateway.purposes if purpose == "iterate.plan"]
        self.assertIn("MainActivity.java", plans[0][1]["content"])
        book = IssueBook(self.state)
        self.assertEqual(book.issues[run["issue"]["fingerprint"]].status, "planned")
        areas = {issue.detector: issue.area for issue in book.issues.values()}
        self.assertEqual(areas["task_failures"], "agent")
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["origin"], "iterate")
        self.assertIn("崩溃处加空值保护", posted[0]["text"])
        folder = Runs(self.state).folder / run["id"]
        self.assertTrue((folder / "plan.json").exists() and (folder / "report.md").exists())

        # Next round: the crash is already planned, so the rage taps (4 times) are picked.
        gateway = FakeGateway([dict(good_plan(), summary="首页点击区域变大")])
        second = self.runner(gateway, now=NOW + 60).run(force=True)
        self.assertEqual(second["issue"]["detector"], "rage_tap")
        self.assertEqual(second["stages"]["collect"]["app_events"]["signals"], 0)  # cursor advanced

    def test_without_force_it_waits_for_enough_new_signals(self):
        self.seed()
        Runs(self.state).save_state({"last_run_at": NOW - 60, "pending_signals": 0})
        settings = parse({"repo_dir": str(self.repo), "trigger": {"min_new_signals": 10}}, self.folder)
        waiting = self.runner(now=NOW, settings=settings).run()
        self.assertEqual(waiting["state"], "waiting")
        self.assertEqual(Runs(self.state).state()["pending_signals"], 8)  # 5 app, 1 chat, 2 failed cards
        # 8 >= 5 with the default test settings: the following run is due without new signals.
        due = self.runner(FakeGateway([good_plan()]), now=NOW + 1).run()
        self.assertEqual(due["state"], "planned")

    def test_dry_run_keeps_no_state_and_no_model_means_no_plan(self):
        self.seed()

        class NoModel(FakeGateway):
            def available(self, purpose):
                return False

        posted = []
        run = self.runner(NoModel([]), posted=posted).run(force=True, dry_run=True)
        self.assertEqual(run["state"], "triaged")
        self.assertIn("可用的模型", run["stages"]["plan"]["skipped"])
        self.assertEqual(Runs(self.state).cursors(), {})
        self.assertEqual(IssueBook(self.state).issues, {})
        self.assertEqual(posted, [])

    def test_a_broken_source_does_not_stop_the_run(self):
        self.seed()
        registry = plugins_module.builtin_registry()
        registry.add_module(SimpleNamespace(PLUGINS=[{
            "name": "app_events", "kind": "source", "run": lambda ctx, cursor: 1 / 0}]), "test")
        run = Runner(self.config, self.settings, registry, FakeGateway([good_plan()]), NOW,
                     poster=lambda *a: None).run(force=True)
        self.assertIn("ZeroDivisionError", run["stages"]["collect"]["app_events"]["error"])
        self.assertNotEqual(run["state"], "failed")

    def test_another_computer_holding_the_lease_stops_the_run(self):
        self.seed()
        refuse = lambda action, stage, seconds=0: {"granted": False, "holder_name": "Windows", "stage": "plan"}
        run = self.runner(leaser=refuse).run(force=True)
        self.assertEqual(run["state"], "elsewhere")
        self.assertIn("Windows", run["report"])
        self.assertEqual(IssueBook(self.state).issues, {})  # nothing analysed here

    def test_the_lease_is_renewed_per_stage_and_released(self):
        self.seed()
        runner = self.runner(FakeGateway([good_plan()]))
        runner.gateway.available = lambda purpose: purpose == "iterate.plan"
        self.assertEqual(runner.run(force=True)["state"], "planned")
        self.assertEqual(self.leases, [("acquire", "detect"), ("renew", "plan"), ("release", "done")])

    def test_a_computer_without_the_repository_leaves_planning_to_others(self):
        self.seed()
        (self.repo / ".git").rmdir()
        run = self.runner(FakeGateway([good_plan()])).run(force=True)
        self.assertEqual(run["state"], "triaged")
        self.assertIn("留给能写方案的电脑", run["stages"]["plan"]["skipped"])

    def test_only_one_run_at_a_time(self):
        with Lock(self.state):
            with self.assertRaises(Busy):
                self.runner().run(force=True)


class ChatFeedbackTest(IterateCase):
    def test_model_votes_decide_the_category(self):
        self.write_cards([{"id": "c1", "entries": [{"role": "you", "text": "按钮点了没反应", "at": NOW - 5}]}])
        settings = parse({"votes": 3}, self.folder)
        gateway = FakeGateway([{"category": "app_bug"}, {"category": "app_ui"}, {"category": "app_bug"}])
        plugin = plugins_module.builtin_registry().get("source", "chat_feedback")
        ctx = plugins_module.Context(plugin, settings, self.state, gateway, now=NOW)
        signals, cursor = sources.chat_feedback(ctx, None)
        self.assertEqual(signals[0].data["category"], "app_bug")
        self.assertAlmostEqual(signals[0].data["confidence"], 0.67)
        self.assertEqual(cursor, {"at": NOW - 5})
        self.assertEqual(sources.heuristic_category("小幽你又派错人了"), "agent_behavior")


class SchedulerTest(IterateCase):
    def test_runs_only_when_enabled_and_idle(self):
        launched = []
        cards = SimpleNamespace(items=[], recent=lambda conversation, limit: cards.items)
        launch = lambda command: launched.append(command) or SimpleNamespace(poll=lambda: 0)
        scheduler = Scheduler(Path("config.json"), parse({}, self.folder), cards, launch)
        self.assertFalse(scheduler.tick())
        scheduler = Scheduler(Path("config.json"), parse({"enabled": True}, self.folder), cards, launch)
        cards.items = [{"state": "working"}]
        self.assertFalse(scheduler.tick())
        cards.items = [{"state": "working", "monitoring_only": True}, {"state": "done"}]
        self.assertTrue(scheduler.tick())
        self.assertEqual(launched[0][-2:], ["iterate", "run"])
