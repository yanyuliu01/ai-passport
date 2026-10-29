"""Persisted helper defaults do not mutate an already dispatched agent."""
import json
import os
from unittest.mock import patch

from test_runtime import Scripted, TempDirCase, make_xiaoyu, start_runtime
from test_helpers import HttpTests
from xiaoyu_runtime.api.service import Service, RequestError
from xiaoyu_runtime.helpers.model_preferences import Preferences, options


class ModelPreferenceTests(TempDirCase):
    def test_switch_is_durable_and_old_agent_keeps_its_model(self):
        old = Scripted("codex", kind="codex", model="expensive")
        x, store = make_xiaoyu(self.folder, [old])
        service = Service(x, store)
        self.addCleanup(x.close)
        self.addCleanup(service.close)
        self.assertEqual(service.helper_model("codex", {"model": "gpt-6.1-sol"})["model"], "gpt-6.1-sol")
        self.assertEqual(old.spec.model, "expensive")
        self.assertEqual(x.agents()[0].spec.model, "gpt-6.1-sol")
        self.assertEqual(service.agents()[0]["configured_model"], "gpt-6.1-sol")
        x2, _ = make_xiaoyu(self.folder, [Scripted("codex", kind="codex", model="expensive")])
        self.addCleanup(x2.close)
        self.assertEqual(x2.agents()[0].spec.model, "gpt-6.1-sol")
        for bad in (None, "", "--unsafe", "model\ncommand", 123, "a" * 129):
            with self.assertRaises(RequestError):
                service.helper_model("codex", {"model": bad})
        self.assertEqual(Preferences(x._config.state_dir).values, {"codex": "gpt-6.1-sol"})
        with patch.object(x.model_preferences, "save", side_effect=OSError("disk full")):
            from xiaoyu_runtime.api.service import UnavailableError
            with self.assertRaises(UnavailableError):
                service.helper_model("codex", {"model": "another-model"})
        self.assertEqual(x.agents()[0].spec.model, "gpt-6.1-sol")

    def test_catalog_excludes_hidden_models_and_does_not_select_astra(self):
        (self.folder / "models_cache.json").write_text(json.dumps({"models": [
            {"slug": "gpt-6.1-sol", "visibility": "list"},
            {"slug": "private", "visibility": "hide"}]}))
        spec = Scripted("codex", kind="codex").spec
        with patch.dict(os.environ, {"CODEX_HOME": str(self.folder)}):
            self.assertEqual(options(spec), ["gpt-6.1-sol"])


class ModelHttpTests(HttpTests):
    def test_model_settings_http_and_missing_helper(self):
        base, _ = start_runtime(self, self.folder)
        self.call(base, "POST", "/v1/helpers", {"name": "codex", "harness": "codex", "access": "login"})
        status, _ = self.call(base, "POST", "/v1/helpers/codex/model", {"model": "gpt-6.1-sol"})
        self.assertEqual(status, 200)
        _, result = self.call(base, "GET", "/v1/helpers/options")
        self.assertEqual(next(row for row in result["model_settings"] if row["name"] == "codex")["model"], "gpt-6.1-sol")
        status, _ = self.call(base, "POST", "/v1/helpers/missing/model", {"model": "gpt-6.1-sol"})
        self.assertEqual(status, 400)
