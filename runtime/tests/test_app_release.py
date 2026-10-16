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
from xiaoyou_runtime.app_release import AppRelease, AppReleaseError, MAX_APK_BYTES, byte_range  # noqa: E402
from xiaoyou_runtime.config import load  # noqa: E402

APK = b"PK\x03\x04" + bytes(range(256)) * 40


def write_apk(folder, data=APK, code=17, name="0.10.2", abi="universal"):
    folder.mkdir(parents=True, exist_ok=True)
    apk = folder / "app-debug.apk"
    apk.write_bytes(data)
    if code is not None:
        (folder / "output-metadata.json").write_text(json.dumps({"elements": [
            {"outputFile": "app-debug.apk", "versionCode": code, "versionName": name,
             "filters": [] if abi == "universal" else [{"filterType": "ABI", "value": abi}]}]}), "utf-8")
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

    def test_abi_variants_keep_default_for_legacy_and_universal_fallback(self):
        release = AppRelease(self.folder / "state")
        arm = write_apk(self.folder / "arm", APK[:100], abi="arm64-v8a")
        universal = write_apk(self.folder / "all")
        intel = write_apk(self.folder / "intel", APK[:200], abi="x86_64")
        release.publish(arm, variants={"universal": universal, "x86_64": intel})
        self.assertEqual(release.info()["size"], 100)
        self.assertEqual(release.info("arm64-v8a")["size"], 100)
        self.assertEqual(release.info("x86_64")["size"], 200)
        self.assertEqual(release.info("armeabi-v7a")["size"], len(APK))
        with release.open("x86_64")[0] as stream:
            self.assertEqual(stream.read(), APK[:200])
        before = release.info()["sha256"]
        with self.assertRaises(AppReleaseError):
            release.publish(arm, variants={"x86_64": write_apk(self.folder / "bad", code=18, abi="x86_64")})
        self.assertEqual(release.info()["sha256"], before)

    def test_pinned_package_rejects_a_new_release_and_open_stream_stays_original(self):
        release = AppRelease(self.folder / "state")
        old = release.publish(write_apk(self.folder / "one"))
        stream, _, _ = release.open(sha256=old["sha256"])
        with stream:
            release.publish(write_apk(self.folder / "two", APK[:400], code=18))
            self.assertEqual(stream.read(), APK)
        with self.assertRaises(AppReleaseError):
            release.open(sha256=old["sha256"])

    def test_publish_variants_from_cli(self):
        path = write_config(self.folder)
        default = write_apk(self.folder / "all")
        arm = write_apk(self.folder / "arm", APK[:100], abi="arm64-v8a")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--config", str(path), "app", "publish", str(default),
                                   "--variant", "arm64-v8a=" + str(arm)]), 0)
        self.assertEqual(AppRelease(load(path, {}).state_dir).info("arm64-v8a")["size"], 100)

    def test_https_hosts_follow_selected_package_without_inheriting_default_url(self):
        release = AppRelease(self.folder / "state")
        default = write_apk(self.folder / "all")
        arm = write_apk(self.folder / "arm", APK[:100], abi="arm64-v8a")
        url = "https://github.com/owner/app/releases/download/v17/default.apk"
        arm_url = "https://github.com/owner/app/releases/download/v17/arm.apk?raw=true"
        release.publish(default, variants={"arm64-v8a": arm}, download_url=url)
        self.assertEqual(release.info()["download_url"], url)
        self.assertEqual(release.info("x86")["download_url"], url)
        self.assertEqual(release.info("arm64-v8a")["download_url"], "")
        release.publish(default, variants={"arm64-v8a": arm}, download_url=url,
                        variant_download_urls={"arm64-v8a": arm_url})
        self.assertEqual(release.info("arm64-v8a")["download_url"], arm_url)
        self.assertEqual(release.info("arm64-v8a")["sha256"], hashlib.sha256(APK[:100]).hexdigest())
        with release.open("arm64-v8a")[0] as stream:
            self.assertEqual(stream.read(), APK[:100])

    def test_rejects_unsafe_hosts_and_unknown_variant_before_changing_publication(self):
        release = AppRelease(self.folder / "state")
        apk = write_apk(self.folder / "out")
        initial = release.publish(apk)
        for url in ("http://example.com/app.apk", "file:///tmp/app.apk", "https:///app.apk",
                    "https://owner:secret@example.com/app.apk", "https://example.com/app.apk#token",
                    "https://example.com/app.apk#", "https://example.com/\napp.apk", "https://example.com:99999/a"):
            with self.subTest(url=url), self.assertRaises(AppReleaseError):
                release.publish(apk, download_url=url)
        with self.assertRaises(AppReleaseError):
            release.publish(apk, variant_download_urls={"arm64-v8a": "https://example.com/arm.apk"})
        self.assertEqual(release.info(), initial)

    def test_cli_supports_default_and_variant_download_urls(self):
        path = write_config(self.folder)
        default = write_apk(self.folder / "all")
        arm = write_apk(self.folder / "arm", APK[:100], abi="arm64-v8a")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--config", str(path), "app", "publish", str(default),
                                   "--download-url", "https://example.com/default.apk",
                                   "--variant", "arm64-v8a=" + str(arm),
                                   "--variant-download-url", "arm64-v8a=https://example.com/arm.apk?raw=true"]), 0)
        release = AppRelease(load(path, {}).state_dir)
        self.assertEqual(release.info()["download_url"], "https://example.com/default.apk")
        self.assertEqual(release.info("arm64-v8a")["download_url"], "https://example.com/arm.apk?raw=true")

    def test_larger_speech_package_and_upper_size_bound(self):
        release = AppRelease(self.folder / "state")
        apk = write_apk(self.folder / "out")
        large_size = 128 * 1024 * 1024 + 1
        with apk.open("r+b") as stream:
            stream.truncate(large_size)
        info = release.publish(apk)
        self.assertEqual(info["size"], large_size)
        digest = hashlib.sha256()
        with release.open()[0] as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
        self.assertEqual(info["sha256"], digest.hexdigest())
        with apk.open("r+b") as stream:
            stream.truncate(MAX_APK_BYTES + 1)
        with self.assertRaises(AppReleaseError):
            release.publish(apk)
        self.assertEqual(release.info(), info)

    def test_single_byte_range_forms_and_invalid_ranges(self):
        for value, expected in (("bytes=3-", (3, 9)), ("bytes=3-5", (3, 5)),
                                ("bytes=-2", (8, 9)), ("bytes=0-99", (0, 9))):
            self.assertEqual(byte_range(value, 10), expected)
        for value in ("bytes=10-", "bytes=9-2", "bytes=-0", "bytes=-", "bytes=0-1,3-4", "junk"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                byte_range(value, 10)


class AppReleaseServerTest(TempDirCase):
    def setUp(self):
        super().setUp()
        self.base, self.config = start_runtime(self, self.folder)

    def get(self, path, token=TOKEN, headers=None):
        request = urllib.request.Request(self.base + path)
        if token:
            request.add_header("Authorization", "Bearer " + token)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
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

    def test_range_resume_and_stale_preconditions(self):
        info = AppRelease(self.config.state_dir).publish(write_apk(self.folder / "out"))
        etag = '"' + info["sha256"] + '"'
        status, headers, body = self.get("/v1/app/apk?sha256=" + info["sha256"],
                                         headers={"Range": "bytes=123-", "If-Range": etag})
        self.assertEqual((status, body), (206, APK[123:]))
        self.assertEqual(headers["Content-Range"], "bytes 123-%d/%d" % (len(APK) - 1, len(APK)))
        self.assertEqual(headers["Accept-Ranges"], "bytes")
        self.assertEqual(self.get("/v1/app/apk", headers={"Range": "bytes=10-20", "If-Range": '"old"'})[2], APK)
        for path, asked, code in (("/v1/app/apk", {"Range": "bytes=999999-"}, 416),
                                  ("/v1/app/apk", {"If-Match": '"old"'}, 412),
                                  ("/v1/app/apk?sha256=" + "0" * 64, {}, 409)):
            with self.subTest(code=code), self.assertRaises(urllib.error.HTTPError) as error:
                self.get(path, headers=asked)
            self.assertEqual(error.exception.code, code)

    def test_http_abi_negotiation_matches_package_checksum_and_auth(self):
        release = AppRelease(self.config.state_dir)
        arm = write_apk(self.folder / "arm", APK[:100], abi="arm64-v8a")
        release.publish(write_apk(self.folder / "all"), variants={"arm64-v8a": arm})
        info = json.loads(self.get("/v1/app?abi=arm64-v8a")[2])
        status, _, body = self.get("/v1/app/apk?abi=arm64-v8a&sha256=" + info["sha256"])
        self.assertEqual((status, body), (200, APK[:100]))
        self.assertEqual(hashlib.sha256(body).hexdigest(), info["sha256"])
        self.assertEqual(self.get("/v1/app/apk")[2], APK)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.get("/v1/app/apk?abi=arm64-v8a", token=None, headers={"Range": "bytes=10-"})
        self.assertEqual(error.exception.code, 401)

    def test_hosted_metadata_stays_authenticated_and_local_legacy_download_still_works(self):
        url = "https://github.com/owner/app/releases/download/v17/app.apk"
        AppRelease(self.config.state_dir).publish(write_apk(self.folder / "out"), download_url=url)
        self.assertEqual(json.loads(self.get("/v1/app")[2])["download_url"], url)
        self.assertEqual(self.get("/v1/app/apk")[2], APK)
        with self.assertRaises(urllib.error.HTTPError) as refused:
            self.get("/v1/app", token=None)
        self.assertEqual(refused.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
