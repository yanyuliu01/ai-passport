"""Artifact persistence, access boundaries, and the authenticated HTTP chain."""

import base64
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))

from xiaoyou_runtime.artifacts import ArtifactError, ArtifactStore, COPY_CHUNK
from xiaoyou_runtime.cards import Cards
from xiaoyou_runtime.server import make_server

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aO1sAAAAASUVORK5CYII=")


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="xiaoyou-artifacts-test-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.workspace = self.folder / "workspace"
        self.workspace.mkdir()
        spec = SimpleNamespace(name="cc", type="echo", workdir=self.workspace,
                               config_dir=None, env_files={})
        self.config = SimpleNamespace(state_dir=self.folder / "state", agents=[spec],
                                      default_agent="cc", base_dir=self.folder,
                                      agent=lambda name: spec if name == "cc" else None,
                                      token="test-token", name="Test", host="127.0.0.1", port=0)
        self.store = ArtifactStore(self.config)
        self.cards = Cards(self.config.state_dir / "cards.json")
        self.card = self.cards.open("default", "Make outputs", agent="cc")
        self.cards.update(self.card["id"], say="Here is the result")

    def test_registration_survives_source_removal_and_restart(self):
        source = self.workspace / "preview.png"
        source.write_bytes(PNG)
        before = self.cards.get("c1")["seq"]
        card = self.store.register_for_card(self.cards, "c1", [{"path": str(source), "name": "方案.png"}])
        meta = card["attachments"][0]
        self.assertEqual((meta["kind"], meta["mime"], meta["width"], meta["height"]),
                         ("image", "image/png", 1, 1))
        self.assertEqual(meta["sha256"], hashlib.sha256(PNG).hexdigest())
        self.assertGreater(card["seq"], before)
        self.assertEqual(card["entries"][-1]["attachments"], card["attachments"])
        source.write_bytes(b"changed")
        source.unlink()
        again = ArtifactStore(self.config)
        cards = Cards(self.config.state_dir / "cards.json")
        self.assertEqual(cards.get("c1")["attachments"], [meta])
        with again.open_content(meta["id"])[0] as opened:
            self.assertEqual(opened.read(), PNG)
        with again.open_content(meta["id"], True)[0] as opened:
            self.assertTrue(opened.read())

    def test_reimport_is_stable_and_copies_are_detached(self):
        source = self.workspace / "report.pdf"
        source.write_bytes(b"%PDF-1.7\nreport")
        first = self.store.register_for_card(self.cards, "c1", [str(source)])
        second = self.store.register_for_card(self.cards, "c1", [str(source)])
        self.assertEqual(first["attachments"][0]["id"], second["attachments"][0]["id"])
        self.assertEqual(len(second["attachments"]), 1)
        first["attachments"][0]["name"] = "mutated"
        first["entries"][-1]["attachments"][0]["name"] = "also mutated"
        actual = self.cards.get("c1")
        self.assertEqual(actual["attachments"][0]["name"], "report.pdf")
        self.assertEqual(actual["entries"][-1]["attachments"][0]["name"], "report.pdf")

    def test_newer_files_survive_when_global_index_exceeds_card_limit(self):
        source = self.workspace / "preview.png"
        source.write_bytes(PNG)
        last = None
        for index in range(257):
            last = self.store.register("c%d" % (index + 1), [{"path": str(source)}], agent="cc")[0]
        source.unlink()
        again = ArtifactStore(self.config)
        with again.open_content(last["id"])[0] as opened:
            self.assertEqual(opened.read(), PNG)

    def test_auto_import_is_only_from_task_inbox_manifest(self):
        outside = self.workspace / "private-report.txt"
        outside.write_text("not an automatic output")
        task_dir = self.store.task_dir("c1")
        (task_dir / "render.png").write_bytes(PNG)
        (task_dir / "manifest.json").write_text(json.dumps({"artifacts": [
            {"path": "render.png"}, {"path": str(outside)}]}))
        metas = self.store.register_task(self.cards, "c1", agent="cc")
        self.assertEqual([item["status"] for item in metas], ["ready", "error"])
        self.assertNotIn(str(self.workspace), json.dumps(metas))
        self.cards.update("c1", say="Finished", attachments=metas)
        self.assertEqual(self.cards.get("c1")["entries"][-1]["attachments"], metas)
        self.assertEqual(self.cards.get("c1")["entries"][0]["attachments"], [])

    def test_unsafe_paths_and_credentials_are_not_exposed(self):
        outside = self.folder / "outside.txt"
        outside.write_text("must stay private")
        hidden = self.workspace / ".env"
        hidden.write_text("private")
        credential = self.workspace / "config.json"
        credential.write_text("private")
        link = self.workspace / "link.txt"
        link.symlink_to(outside)
        directory_link = self.workspace / "linked-dir"
        directory_link.symlink_to(self.folder)
        paths = [str(outside), str(hidden), str(credential), str(link),
                 str(directory_link / "outside.txt"), "../outside.txt"]
        metas = self.store.register("c1", paths, agent="cc")
        self.assertEqual([m["status"] for m in metas], ["error"] * len(paths))
        for meta in metas:
            with self.assertRaises(ArtifactError):
                self.store.open_content(meta["id"])
        self.assertFalse(list(self.store._blobs.iterdir()))
        self.assertNotIn(str(self.folder), json.dumps(metas))

    def test_named_secret_and_runtime_state_excluded_even_in_workspace(self):
        self.config.state_dir = self.workspace / "state"
        store = ArtifactStore(self.config)
        paths = [self.workspace / "api-token.txt", self.workspace / "private.key",
                 self.config.state_dir / "sessions.json"]
        for path in paths:
            path.write_text("private")
        self.assertEqual([m["status"] for m in store.register("c1", [str(p) for p in paths], agent="cc")],
                         ["error", "error", "error"])

    def test_symlink_to_allowed_file_and_fifo_are_not_regular_outputs(self):
        ordinary = self.workspace / "good.txt"
        ordinary.write_text("allowed file")
        link = self.workspace / "alias.txt"
        link.symlink_to(ordinary)
        pipe = self.workspace / "output.pipe"
        os.mkfifo(pipe)
        metas = self.store.register("c1", [str(link), str(pipe)], agent="cc")
        self.assertEqual([m["status"] for m in metas], ["error", "error"])

    def test_task_root_cannot_be_replaced_with_symlink(self):
        directory = self.store.task_dir("c1")
        directory.rmdir()
        directory.symlink_to(self.workspace)
        with self.assertRaises(ArtifactError):
            self.store.task_dir("c1")

    def test_invalid_manifest_fails_before_any_file_is_copied(self):
        source = self.workspace / "report.txt"
        source.write_text("real output")
        with self.assertRaises(ArtifactError):
            self.store.register("c1", [str(source), {"name": "missing path"}], agent="cc")
        self.assertEqual(list(self.store._blobs.iterdir()), [])

    def test_storage_quota_rejects_partial_copy_and_is_per_store(self):
        with patch("xiaoyou_runtime.artifacts.MAX_STORE_BYTES", 7):
            self.store.save_text("c1", "12345")
            with self.assertRaises(ArtifactError):
                self.store.save_text("c1", "1234", name="second.txt")
        self.assertEqual(sum(p.stat().st_size for p in self.store._blobs.iterdir()), 5)
        self.assertFalse(list(self.store._root.glob("copy-*")))

    def test_large_file_stream_reads_are_bounded_and_invalid_thumbnail_is_explicit(self):
        data = b"x" * (COPY_CHUNK * 3 + 12)

        class Bounded(io.BytesIO):
            def read(self, count=-1):
                if count != COPY_CHUNK:
                    raise AssertionError("unbounded source read")
                return super().read(count)

        meta = self.store._snapshot("c1", Bounded(data), "report.bin", "cc")
        with self.store.open_content(meta["id"])[0] as opened:
            self.assertEqual(hashlib.sha256(opened.read()).hexdigest(), meta["sha256"])
        with self.assertRaises(ArtifactError):
            self.store.open_content(meta["id"], True)
        with self.assertRaises(ArtifactError):
            self.store.open_content("../../outside")
        with patch("xiaoyou_runtime.artifacts.MAX_ARTIFACT_BYTES", 5):
            with self.assertRaises(ArtifactError):
                self.store._snapshot("c1", io.BytesIO(b"too long"), "large.txt", "cc")
        self.assertFalse(list(self.store._root.glob("copy-*")))

    def test_full_text_and_new_version_have_separate_stable_ids(self):
        meta = self.store.save_text("c1", "全文" * 9000)
        self.cards.update("c1", say="See full reply", attachments=[meta])
        with self.store.open_content(meta["id"])[0] as opened:
            self.assertEqual(opened.read().decode("utf-8"), "全文" * 9000)
        newer = self.store.save_text("c1", "second version")
        self.assertNotEqual(meta["id"], newer["id"])
        self.assertEqual(self.store.save_text("c1", "second version")["id"], newer["id"])

    def test_malformed_manifest_yields_visible_error_and_not_raw_exception(self):
        (self.store.task_dir("c1") / "manifest.json").write_text("{broken}")
        metas = self.store.register_task(self.cards, "c1", agent="cc")
        self.assertEqual(metas[0]["status"], "error")
        self.assertEqual(metas[0]["name"], "产物清单")

    def test_task_import_io_failure_returns_error_metadata(self):
        with patch.object(self.store, "_roots", side_effect=OSError("disk unavailable")):
            metas = self.store.register_task(self.cards, "c1", agent="cc")
        self.assertEqual(metas[0]["status"], "error")
        self.assertNotIn("disk unavailable", metas[0]["error"])
        with patch.object(self.store, "_snapshot", side_effect=OSError("disk full")):
            with self.assertRaises(ArtifactError):
                self.store.save_text("c1", "must not lose the completed task")

    def start_http(self):
        service = SimpleNamespace(artifacts=self.store, card_store=self.cards)
        server = make_server(self.config, service)
        server.RequestHandlerClass.log_message = lambda *args: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:%d" % server.server_address[1]

    def call(self, base, method, path, body=None, token="test-token"):
        request = urllib.request.Request(base + path, method=method,
                                         data=json.dumps(body).encode() if body is not None else None)
        if token:
            request.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers

    def test_http_registration_download_and_authentication(self):
        source = self.workspace / "preview.png"
        source.write_bytes(PNG)
        base = self.start_http()
        status, body, _ = self.call(base, "POST", "/v1/cards/c1/artifacts", {"artifacts": [str(source)]})
        self.assertEqual(status, 200)
        meta = json.loads(body)["attachments"][0]
        path = "/v1/artifacts/%s/content" % meta["id"]
        for token in (None, "wrong-token"):
            self.assertEqual(self.call(base, "GET", path, token=token)[0], 401)
        status, data, headers = self.call(base, "GET", path)
        self.assertEqual((status, data), (200, PNG))
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["Content-Length"], str(len(PNG)))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(self.call(base, "GET", path.replace("content", "thumbnail"))[0], 200)
        self.assertEqual(self.call(base, "GET", "/v1/artifacts/unknown/content")[0], 404)
        self.assertEqual(self.call(base, "POST", "/v1/cards/c99/artifacts", {"artifacts": []})[0], 404)
        self.assertEqual(self.call(base, "POST", "/v1/cards/c1/artifacts", {"artifacts": []}, token=None)[0], 401)
        self.assertEqual(self.call(base, "POST", "/v1/cards/c1/artifacts", {"artifacts": [], "entry_index": 99})[0], 400)


if __name__ == "__main__":
    unittest.main()
