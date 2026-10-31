import unittest

from test_runtime import TempDirCase, write_config, config_module, usage_module, codex_limits


class SubscriptionUsageTest(TempDirCase):
    def test_weekly_only_snapshot_clears_old_short_window_and_survives_restart(self):
        config = config_module.load(write_config(self.folder, agents={"codex": {"type": "codex"}}), {})
        meter = usage_module.Usage(config, enabled=True, clock=lambda: 1900000000, fetchers={})
        meter.observe("codex", "codex", codex_limits(90))
        weekly = {"rateLimits": {"planType": "prolite", "primary": {
            "usedPercent": 12, "windowDurationMins": 10080, "resetsAt": 2000500000},
            "secondary": None}}
        meter.observe("codex", "codex", weekly)
        windows = meter.snapshot()["accounts"][0]["windows"]
        self.assertEqual(windows[0], {"kind": "5h", "left": None, "resets_at": None, "unavailable": True})
        self.assertEqual(windows[1]["left"], 88)
        self.assertEqual(meter.snapshot()["accounts"][0]["state"], "ok")
        meter.fetchers["codex"] = lambda spec: ({}, None, "offline")
        meter.refresh()
        self.assertEqual(meter.snapshot()["accounts"][0]["windows"], windows)
        restored = usage_module.Usage(config, enabled=False, clock=lambda: 1900000000)
        self.assertEqual(restored.snapshot()["accounts"][0]["windows"], windows)
        meter.observe("codex", "codex", codex_limits(30))
        self.assertEqual(meter.snapshot()["accounts"][0]["windows"][0]["left"], 70)

    def test_incomplete_snapshot_does_not_claim_short_window_unavailable(self):
        partial = {"rateLimits": {"primary": {"usedPercent": 20, "windowDurationMins": 10080}}}
        windows, _ = usage_module.codex_windows(partial)
        self.assertNotIn("5h", windows)


if __name__ == "__main__":
    unittest.main()
