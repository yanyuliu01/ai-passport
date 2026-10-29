"""tools/ttft_report.py joins phone timing with real Runtime records, read-only."""

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from test_runtime import Scripted, TempDirCase, make_xiaoyu
from xiaoyu_runtime.api.service import Service

TOOL = Path(__file__).resolve().parents[2] / "tools" / "ttft_report.py"
spec = importlib.util.spec_from_file_location("ttft_report", TOOL)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class TtftReportTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.x, store = make_xiaoyu(self.folder, [Scripted("voice")])
        self.addCleanup(self.x.close)
        self.service = Service(self.x, store)
        self.addCleanup(self.service.close)
        self.state = store.state_dir
        self.ids = {}
        for key in ("fast", "slow"):
            self.ids[key] = self.service.submit("hello " + key, client_id=key)["id"]
            self.service.get(self.ids[key], wait=5)

    def write(self, name, text):
        path = Path(self.folder) / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def run_tool(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = report.main(list(args))
        return code, out.getvalue()

    def phone(self, *lines):
        prefix = "10-26 12:00:00.000  1234  1250 I XiaoyuTiming: "
        return self.write("phone.txt", "".join(prefix + line + "\n" for line in lines))

    def test_every_sample_must_pass_and_none_are_dropped(self):
        phone = self.phone("send key=fast t=1000", "final key=fast t=2100 elapsed_ms=1100",
                           "bubble key=fast t=2200 elapsed_ms=1200",
                           "send key=slow t=5000", "bubble key=slow t=8100 elapsed_ms=3100",
                           "send key=lost t=9000")
        log = self.write("stderr.log", "Decide/speak turn=%s decision=tool decision_s=0.640 "
                         "first_body_s=0.910 total_s=1.300 promise_without_commitment=0\n" % self.ids["fast"])
        record = str(Path(self.folder) / "results.jsonl")
        code, text = self.run_tool(phone, "--state", str(self.state), "--runtime-log", log,
                                   "--scenario", "warm", "--record", record, "--note", "app=test")
        self.assertEqual(1, code)
        self.assertIn("Samples: 3  pass: 1  miss: 2  max bubble: 3100 ms", text)
        rows = [json.loads(line) for line in Path(record).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(["PASS", "MISS", "NO BUBBLE"], [row["verdict"] for row in rows])
        self.assertEqual(self.ids["fast"], rows[0]["turn"])
        self.assertEqual(0.91, rows[0]["first_body_s"])
        self.assertEqual(1100, rows[0]["final_ms"])
        self.assertEqual({"warm"}, {row["scenario"] for row in rows})
        # Only ids and times leave the Runtime state, never message text.
        self.assertNotIn("hello", Path(record).read_text(encoding="utf-8"))

    def test_all_fast_samples_pass(self):
        phone = self.phone("send key=fast t=10", "bubble key=fast t=900 elapsed_ms=890")
        code, text = self.run_tool(phone, "--state", str(self.state))
        self.assertEqual(0, code)
        self.assertIn("Result: PASS", text)

    def test_an_error_bubble_is_not_a_response(self):
        message = self.service.get(self.ids["fast"])
        found = report.rows([{"key": "fast", "sent": 0, "final_ms": None, "bubble_ms": 400}],
                            {"fast": dict(message, status="failed", error="boom")}, {})
        self.assertEqual("MISS (error)", found[0]["verdict"])

    def test_a_retry_keeps_the_first_send(self):
        samples = report.phone_samples(["send key=a t=100", "send key=a t=900", "bubble key=a t=1500"])
        self.assertEqual(1400, samples[0]["bubble_ms"])

    def test_a_capture_without_samples_says_so(self):
        code, text = self.run_tool(self.phone("bubble key=old t=5 elapsed_ms=5"))
        self.assertEqual(2, code)
        self.assertIn("NO SAMPLES", text)

    def test_the_state_folder_is_not_changed(self):
        # Stop the Runtime first, so only the tool could change the files.
        self.service.close()
        self.x.close()
        before ={path.name: path.read_bytes() for path in Path(self.state).glob("messages.json*")}
        self.run_tool(self.phone("send key=fast t=1", "bubble key=fast t=2"), "--state", str(self.state))
        after = {path.name: path.read_bytes() for path in Path(self.state).glob("messages.json*")}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
