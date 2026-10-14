#!/usr/bin/env python3
"""Tests for the phone App update package kept by the Xiaoyou runtime. Standard library only.

They prove that a published APK is served back byte for byte with its version
and checksum. They do not prove that a phone installs it: Android asks the
owner to confirm, and that needs a phone.
"""

import contextlib
import hashlib
import io
import json
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_runtime import TOKEN, TempDirCase, start_runtime, write_config  # noqa: E402
from xiaoyou_runtime.__main__ import main  # noqa: E402
from xiaoyou_runtime.app_release import AppRelease, AppReleaseError  # noqa: E402

APK = b"PK\x03\x04" + bytes(range(256)) * 40


def write_apk(folder, data=APK, code=17, name="0.10.2"):
    folder.mkdir(parents=True, exist_ok=True)
    apk = folder / "app-debug.apk"
    apk.write_bytes(data)
    if code is not None:
        (folder / "output-metadata.json").write_text(json.dumps({"elements": [
            {"outputFile": "app-debug.apk", "versionCode": code, "versionName": name}]}), "utf-8")
    return apk


class AppReleaseTest(TempDirCase):
    def test_publish_reads_gradle_metadata(self):
        release = AppRelease(self.folder / "state")
        self.assertEqual(release.info(), {"available": False})
        info = release.publish(write_apk(self.folder / "out"), "动画修好了")
        self.assertEqual((info["version_code"], info["version_name"], info["note"]), (17, "0.10.2", "动画修好了"))
        self.assertEqual(info["sha256"], hashlib.sha256(APK).hexdigest())
        self.assertEqual(release.info()["size"], len(APK))
        stream, _, size = release.open()
        with stream:
            self.assertEqual(stream.read(), APK)
        self.assertEqual(size, len(APK))

    def test_rejects_unknown_version_and_non_apk(self):
        release = AppRelease(self.folder / "state")
        with self.assertRaises(AppReleaseError):
            release.publish(write_apk(self.folder / "a", code=None))
        self.assertEqual(release.publish(write_apk(self.folder / "a", code=None), version_code=3)["version_code"], 3)
        with self.assertRaises(AppReleaseError):
            release.publish(write_apk(self.folder / "b", data=b"not a zip"))
        self.assertEqual(release.info()["version_code"], 3)

    def test_command_line_publish_and_status(self):
        path = write_config(self.folder)
        apk = write_apk(self.folder / "out")
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(main(["--config", str(path), "app", "publish", str(apk), "--note", "试试"]), 0)
            self.assertEqual(main(["--config", str(path), "app", "status"]), 0)
        self.assertIn("版本号 17", printed.getvalue())
        self.assertIn("试试", printed.getvalue())


class AppReleaseServerTest(TempDirCase):
    def setUp(self):
        super().setUp()
        self.base, self.config = start_runtime(self, self.folder)

    def get(self, path, token=TOKEN):
        request = urllib.request.Request(self.base + path)
        if token:
            request.add_header("Authorization", "Bearer " + token)
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.headers, response.read()

    def test_serves_the_published_apk(self):
        with self.assertRaises(urllib.error.HTTPError) as missing:
            self.get("/v1/app/apk")
        self.assertEqual(missing.exception.code, 404)
        self.assertFalse(json.loads(self.get("/v1/app")[2])["available"])
        AppRelease(self.config.state_dir).publish(write_apk(self.folder / "out"))
        info = json.loads(self.get("/v1/app")[2])
        self.assertEqual((info["available"], info["version_code"]), (True, 17))
        status, headers, body = self.get("/v1/app/apk")
        self.assertEqual((status, body), (200, APK))
        self.assertEqual(headers["Content-Type"], "application/vnd.android.package-archive")

    def test_needs_the_token(self):
        with self.assertRaises(urllib.error.HTTPError) as refused:
            self.get("/v1/app", token=None)
        self.assertEqual(refused.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
