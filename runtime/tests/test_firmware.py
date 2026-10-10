#!/usr/bin/env python3
"""Tests for the firmware library kept by the Xiaoyou runtime. Standard library only.

The images here are built by hand in the ESP application image format (header,
segments, checksum, appended SHA-256, application description), so these tests
prove that the runtime reads that format and keeps the library straight. They
do not prove that a device accepts an image: that is the firmware's job, and
needs a device.
"""

import contextlib
import hashlib
import io
import json
import struct
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_runtime import TOKEN, TempDirCase, start_runtime, write_config  # noqa: E402
from xiaoyou_runtime import config as config_module  # noqa: E402
from xiaoyou_runtime import firmware as firmware_module  # noqa: E402
from xiaoyou_runtime import firmware_cli  # noqa: E402
from xiaoyou_runtime.__main__ import main  # noqa: E402
from xiaoyou_runtime.firmware import FirmwareError, FirmwareStore, parse_image  # noqa: E402


def make_app(version="v1", seed=1, updatable=True, chip=5, payload=3000, hashed=True):
    """A small but well-formed ESP application image."""
    elf = hashlib.sha256(("elf-%s-%d" % (version, seed)).encode()).digest()
    description = struct.pack("<II8x", 0xABCD5432, 0)
    description += version.encode().ljust(32, b"\x00")
    description += b"FoloToy-AI-Passport".ljust(32, b"\x00")
    description += b"16:22:27".ljust(16, b"\x00") + b"Oct  9 2026".ljust(16, b"\x00")
    description += b"v5.5.3".ljust(32, b"\x00") + elf
    description = description.ljust(256, b"\x00")
    body = bytes((index * 31 + seed) % 251 for index in range(payload))
    if updatable:
        body += b"\x00" + firmware_module.UPDATE_MARKER + b"\x00"
    segments = [description + body, bytes((index + seed) % 7 for index in range(701))]
    header = bytearray(24)
    header[0] = 0xE9
    header[1] = len(segments)
    struct.pack_into("<H", header, 12, chip)
    header[23] = 1 if hashed else 0
    image = bytes(header)
    checksum = 0xEF
    for index, data in enumerate(segments):
        image += struct.pack("<II", 0x3C000020 + index * 0x10000, len(data)) + data
        for byte in data:
            checksum ^= byte
    image += b"\x00" * (15 - len(image) % 16) + bytes([checksum])
    if hashed:
        image += hashlib.sha256(image).digest()
    return image


def make_merged(app, offset=0x20000, subtype=0x10, label=b"ota_0"):
    """Bootloader area, partition table and the application, the way merge-bin lays them out."""
    table = struct.pack("<2sBBII16sI", b"\xaa\x50", 1, 2, 0x9000, 0x6000, b"nvs".ljust(16, b"\x00"), 0)
    table += struct.pack("<2sBBII16sI", b"\xaa\x50", 0, subtype, offset, 0x3F0000,
                         label.ljust(16, b"\x00"), 0)
    table += b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(table).digest()
    blob = bytearray(b"\xff" * offset)
    blob[0:4] = b"\xe9\x03\x02\x20"  # a bootloader starts with the same magic byte
    blob[0x8000:0x8000 + len(table)] = table
    return bytes(blob) + app


class ImageTests(unittest.TestCase):
    def test_an_application_image_is_read(self):
        app = make_app("403725c")
        image = parse_image(app)
        self.assertEqual(image.data, app)
        self.assertEqual(image.sha256, hashlib.sha256(app).hexdigest())
        self.assertEqual(image.id, image.sha256[:12])
        self.assertEqual(image.version, "403725c")
        self.assertEqual(image.project, "FoloToy-AI-Passport")
        self.assertEqual(image.compiled, "Oct  9 2026 16:22:27")
        self.assertEqual(image.idf, "v5.5.3")
        self.assertEqual(image.build, hashlib.sha256(b"elf-403725c-1").hexdigest()[:16])
        self.assertTrue(image.updatable)
        self.assertFalse(parse_image(make_app(updatable=False)).updatable)

    def test_the_application_is_taken_out_of_a_merged_image(self):
        app = make_app("merged")
        for merged in (make_merged(app), make_merged(app, 0x10000, 0x00, b"factory"),
                       make_merged(app) + b"\xff" * 4096):
            image = parse_image(merged)
            self.assertEqual(image.data, app)
            self.assertEqual(image.version, "merged")

    def test_an_image_without_the_appended_hash_is_still_checked_by_its_checksum(self):
        app = make_app(hashed=False)
        self.assertEqual(parse_image(app).data, app)
        broken = bytearray(app)
        broken[400] ^= 0x10
        with self.assertRaisesRegex(FirmwareError, "校验和"):
            parse_image(bytes(broken))

    def test_damaged_or_foreign_files_are_refused(self):
        app = make_app()
        flipped = bytearray(app)
        flipped[len(app) // 2] ^= 1
        with self.assertRaisesRegex(FirmwareError, "校验和"):
            parse_image(bytes(flipped))
        hash_flipped = bytearray(app)
        hash_flipped[-1] ^= 1
        with self.assertRaisesRegex(FirmwareError, "SHA-256"):
            parse_image(bytes(hash_flipped))
        with self.assertRaisesRegex(FirmwareError, "不完整"):
            parse_image(app[:-200])
        with self.assertRaisesRegex(FirmwareError, "空的"):
            parse_image(b"")
        with self.assertRaisesRegex(FirmwareError, "分区表"):
            parse_image(b"not firmware at all" * 50)
        with self.assertRaisesRegex(FirmwareError, "8 MB"):
            parse_image(b"\x00" * (8 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(FirmwareError, "ESP32-C3"):
            parse_image(make_app(chip=9))
        # A partition table that points at something that is not an application.
        with self.assertRaisesRegex(FirmwareError, "不是应用镜像"):
            parse_image(make_merged(b"\xe9" + b"\x00" * 600))


class StoreTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.now = 1000.0
        self.store = FirmwareStore(self.folder, clock=lambda: self.now)

    def add(self, version, **options):
        self.now += 10
        note = options.pop("note", "")
        return self.store.add(make_app(version, **options), "test", note)

    def test_every_version_is_kept_and_numbered_in_order(self):
        first = self.add("v1", note="first")
        second = self.add("v2")
        self.assertEqual((first["seq"], second["seq"]), (1, 2))
        self.assertFalse(first["existed"])
        self.assertNotEqual(first["id"], second["id"])
        # The same image again keeps its number; a new note replaces the old one.
        again = self.store.add(make_merged(make_app("v1")), "merged", "renamed")
        self.assertTrue(again["existed"])
        self.assertEqual((again["seq"], again["note"]), (1, "renamed"))
        snapshot = self.store.snapshot()
        self.assertEqual([item["seq"] for item in snapshot["versions"]], [2, 1])
        self.assertEqual(self.store.image(first["id"]), make_app("v1"))
        self.assertEqual(sorted(path.name for path in (self.folder / "firmware" / "images").iterdir()),
                         sorted([first["id"] + ".bin", second["id"] + ".bin"]))

    def test_a_version_is_found_the_ways_people_name_it(self):
        first = self.add("v1")
        second = self.add("v2")
        for ref in ("1", "#1", first["id"], first["id"][:5], first["id"][:5].upper()):
            self.assertEqual(self.store.find(ref)["id"], first["id"], ref)
        self.assertEqual(self.store.find("latest")["id"], second["id"])
        for ref, why in (("9", "没有第 9 版"), ("zz", "没有对上"), ("", "不止一版")):
            with self.assertRaisesRegex(FirmwareError, why):
                self.store.find(ref)
        # What the device runs and what its other slot holds, once it has said so.
        with self.assertRaisesRegex(FirmwareError, "还没报过"):
            self.store.find("previous")
        self.store.report({"event": "connected", "build": second["build"], "prev": first["build"],
                           "state": "valid"})
        self.assertEqual(self.store.find("current")["id"], second["id"])
        self.assertEqual(self.store.find("previous")["id"], first["id"])
        self.assertEqual(self.store.snapshot()["device"]["seq"], 2)
        with self.assertRaisesRegex(FirmwareError, "还没有固件"):
            FirmwareStore(self.folder / "empty").find("latest")

    def test_pushing_a_version_and_the_device_arriving_on_it(self):
        first = self.add("v1")
        second = self.add("v2")
        self.assertEqual(self.store.target()["id"], "")
        self.store.set_target("2")
        target = self.store.target()
        self.assertEqual((target["id"], target["seq"], target["size"], target["sha256"]),
                         (second["id"], 2, second["size"], second["sha256"]))
        # The phone says where the device is: still on the old one, then sending.
        answer = self.store.report({"event": "connected", "build": first["build"],
                                    "state": "valid", "name": "Claude-AB12CD"})
        self.assertEqual(answer["id"], second["id"])
        self.store.report({"event": "progress", "build": first["build"], "state": "valid",
                           "id": second["id"], "sent": 4096, "size": second["size"]})
        self.assertEqual(self.store.snapshot()["device"]["progress"]["sent"], 4096)
        # Restarted into the new one but not confirmed yet: still the target.
        self.store.report({"event": "connected", "build": second["build"], "state": "pending"})
        self.assertEqual(self.store.target()["id"], second["id"])
        answer = self.store.report({"event": "installed", "build": second["build"],
                                    "state": "valid", "prev": first["build"]})
        self.assertEqual(answer["id"], "")
        snapshot = self.store.snapshot()
        self.assertIsNone(snapshot["target"])
        self.assertNotIn("progress", snapshot["device"])
        self.assertEqual([item["event"] for item in snapshot["log"]],
                         ["added", "added", "requested", "installed"])
        # Going back is the same thing with another name.
        self.store.set_target("previous", "restore")
        self.assertEqual(self.store.target()["id"], first["id"])
        # A device that turns up already on the wanted version settles it too.
        self.store.report({"event": "connected", "build": first["build"], "state": "valid"})
        self.assertEqual(self.store.target()["id"], "")

    def test_a_version_that_will_not_install_is_not_pushed_forever(self):
        first = self.add("v1")
        second = self.add("v2")
        self.store.set_target(second["id"])
        self.store.report({"event": "failed", "build": first["build"], "state": "valid",
                           "id": second["id"], "detail": "rolled back"})
        target = self.store.target()
        self.assertEqual((target["id"], target["attempts"]), (second["id"], 1))
        # A failure reported for some other version does not count against this one.
        self.store.report({"event": "failed", "build": first["build"], "id": first["id"]})
        self.assertEqual(self.store.target()["attempts"], 1)
        self.store.report({"event": "failed", "build": first["build"], "state": "valid",
                           "id": second["id"], "detail": "sha256"})
        self.assertEqual(self.store.target()["id"], "")
        self.assertEqual(self.store.snapshot()["log"][-1]["event"], "gave_up")
        # Nothing was deleted: it can be asked for again by hand.
        self.store.set_target(second["id"])
        self.assertEqual(self.store.target()["attempts"], 0)
        # Firmware from before this feature cannot take an image at all.
        self.store.report({"event": "unsupported", "build": "", "detail": "unknown command"})
        self.assertEqual(self.store.target()["id"], "")

    def test_a_version_that_cannot_be_replaced_afterwards_needs_force(self):
        self.add("new")
        old = self.add("old", updatable=False)
        with self.assertRaisesRegex(FirmwareError, "插线"):
            self.store.set_target(old["id"])
        self.assertEqual(self.store.target()["id"], "")
        self.store.set_target(old["id"], force=True)
        self.assertEqual(self.store.target()["id"], old["id"])

    def test_cancel_and_remove(self):
        first = self.add("v1")
        second = self.add("v2")
        self.assertFalse(self.store.clear_target())
        self.store.set_target("1")
        with self.assertRaisesRegex(FirmwareError, "cancel"):
            self.store.remove("1")
        self.assertTrue(self.store.clear_target())
        self.store.remove("1")
        self.assertEqual([item["id"] for item in self.store.snapshot()["versions"]], [second["id"]])
        self.assertFalse((self.folder / "firmware" / "images" / (first["id"] + ".bin")).exists())
        # Numbers are never reused, so "version 1" never comes to mean something else.
        self.assertEqual(self.add("v3")["seq"], 3)

    def test_the_latest_thing_that_went_wrong_is_offered_as_a_notice(self):
        first = self.add("v1")
        self.assertEqual((self.store.target()["notice"], self.store.target()["notice_at"]), ("", 0))
        self.now = 5000.0
        self.store.note("fetch_failed", "abc", "构建失败了")
        target = self.store.target()
        self.assertEqual((target["notice"], target["notice_at"]), ("构建失败了", 5000))
        self.assertEqual(self.store.snapshot()["log"][-1]["event"], "fetch_failed")
        # Anything that happens afterwards retires it.
        self.store.set_target(first["id"])
        self.assertEqual(self.store.target()["notice"], "")
        self.store.report({"event": "failed", "build": "x", "id": first["id"], "detail": "sha256"})
        self.assertEqual(self.store.target()["notice"], "sha256")

    def test_a_tampered_or_missing_image_is_not_handed_out(self):
        first = self.add("v1")
        path = self.folder / "firmware" / "images" / (first["id"] + ".bin")
        path.write_bytes(path.read_bytes()[:-1] + b"\x00")
        with self.assertRaisesRegex(FirmwareError, "不一样"):
            self.store.image(first["id"])
        path.unlink()
        with self.assertRaisesRegex(FirmwareError, "读不了"):
            self.store.image(first["id"])

    def test_bad_reports_are_refused_and_odd_fields_ignored(self):
        self.add("v1")
        for bad in (None, [], "x"):
            with self.assertRaises(FirmwareError):
                self.store.report(bad)
        with self.assertRaisesRegex(FirmwareError, "event"):
            self.store.report({"event": "exploded"})
        self.store.report({"event": "connected", "build": 7, "max": -1, "name": "n" * 100,
                           "sent": True})
        device = self.store.snapshot()["device"]
        self.assertEqual((device["build"], device["max"], len(device["name"])), ("", 0, 40))

    def test_the_device_says_how_much_memory_it_has_left(self):
        self.add("v1")
        self.store.report({"event": "connected", "build": "b" * 16, "state": "valid",
                           "heap": 41232, "low": 28764})
        device = self.store.snapshot()["device"]
        self.assertEqual((device["heap"], device["low"]), (41232, 28764))
        # 老固件不报这两项：记录里就没有，不写成 0。
        self.store.report({"event": "connected", "build": "c" * 16, "heap": "lots"})
        self.assertEqual(self.store.snapshot()["device"]["heap"], 0)
        other = FirmwareStore(self.folder / "elsewhere")
        other.report({"event": "connected", "build": "d" * 16})
        self.assertNotIn("heap", other.snapshot()["device"])

    def test_another_process_sees_the_same_library(self):
        first = self.add("v1")
        other = FirmwareStore(self.folder)
        self.assertEqual(other.find("1")["id"], first["id"])
        other.set_target("1")
        self.assertEqual(self.store.target()["id"], first["id"])
        # The revision moves with every change; a waiter is released by one.
        rev = self.store.rev()
        started = time.monotonic()
        timer = threading.Timer(0.3, other.clear_target)
        timer.start()
        self.addCleanup(timer.cancel)
        self.store.wait_for_change(rev, 5)
        self.assertLess(time.monotonic() - started, 4)
        self.assertNotEqual(self.store.rev(), rev)
        started = time.monotonic()
        self.store.wait_for_change(self.store.rev(), 0.6)
        self.assertGreaterEqual(time.monotonic() - started, 0.5)

    def test_a_lock_left_behind_by_a_dead_process_does_not_block_forever(self):
        self.add("v1")
        lock = self.folder / "firmware" / "index.lock"
        lock.write_text("", encoding="utf-8")
        import os
        stale = time.time() - 60
        os.utime(str(lock), (stale, stale))
        self.store.set_target("1")
        self.assertEqual(self.store.target()["seq"], 1)
        self.assertFalse(lock.exists())

    def test_a_broken_index_is_reported_not_overwritten(self):
        self.add("v1")
        index = self.folder / "firmware" / "index.json"
        index.write_text("{ not json", encoding="utf-8")
        with self.assertRaisesRegex(FirmwareError, "清单"):
            self.store.snapshot()
        with self.assertRaisesRegex(FirmwareError, "清单"):
            self.add("v2")
        self.assertEqual(index.read_text(encoding="utf-8"), "{ not json")


class FakeGitHub:
    """Answers the two kinds of request the fetcher makes: the release list and downloads."""

    def __init__(self, releases, files):
        self.releases = releases
        self.files = files
        self.calls = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.calls.append((url, request.get_header("Authorization")))
        if url.endswith("/releases?per_page=30"):
            return contextlib.closing(io.BytesIO(json.dumps(self.releases).encode()))
        if url in self.files:
            return contextlib.closing(io.BytesIO(self.files[url]))
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))


def release(tag, commit, blob, digest=None, asset="FoloToy-AI-Passport-full.bin"):
    base = "https://example.invalid/%s/" % tag
    entry = {"tag_name": tag, "target_commitish": commit, "assets": [
        {"name": asset, "browser_download_url": base + asset},
        {"name": asset + ".sha256", "browser_download_url": base + asset + ".sha256"},
    ]}
    files = {
        base + asset: blob,
        base + asset + ".sha256": ("%s  %s\n" % (
            digest or hashlib.sha256(blob).hexdigest(), asset)).encode(),
    }
    return entry, files


def no_git(repo, tag_prefix):
    """Stands in for remote_tags on a computer without git: the fetcher asks the API."""
    return None


class FetchTests(unittest.TestCase):
    ARGS = ("me/repo", "FoloToy-AI-Passport-full.bin", "firmware-build-")

    def setUp(self):
        self.old = make_merged(make_app("old"))
        self.new = make_merged(make_app("new"))
        newer, newer_files = release("firmware-build-3", "c" * 40, self.new)
        older, older_files = release("firmware-build-2", "b" * 40, self.old)
        app, _ = release("companion-build-9", "c" * 40, b"apk")
        self.releases = [app, newer, older]
        self.files = dict(newer_files, **older_files)

    def test_the_newest_firmware_release_is_taken(self):
        github = FakeGitHub(self.releases, self.files)
        blob, found = firmware_module.fetch_release(*self.ARGS, opener=github, token="t0ken", tags=no_git)
        self.assertEqual(blob, self.new)
        self.assertEqual((found["tag"], found["commit"]), ("firmware-build-3", "c" * 40))
        self.assertEqual(github.calls[0],
                         ("https://api.github.com/repos/me/repo/releases?per_page=30", "Bearer t0ken"))

    def test_a_tag_or_a_commit_picks_one_build(self):
        github = FakeGitHub(self.releases, self.files)
        blob, found = firmware_module.fetch_release(*self.ARGS, tag="firmware-build-2", opener=github, tags=no_git)
        self.assertEqual((blob, found["tag"]), (self.old, "firmware-build-2"))
        blob, found = firmware_module.fetch_release(*self.ARGS, commit="b" * 10, opener=github, tags=no_git)
        self.assertEqual(found["tag"], "firmware-build-2")
        with self.assertRaisesRegex(FirmwareError, "没找到"):
            firmware_module.fetch_release(*self.ARGS, commit="d" * 40, opener=github, tags=no_git)
        with self.assertRaisesRegex(FirmwareError, "标签"):
            firmware_module.fetch_release(*self.ARGS, tag="firmware-build-99", opener=github, tags=no_git)

    def test_waiting_for_a_build_that_is_not_there_yet(self):
        naps = []
        said = []

        class Later(FakeGitHub):
            def __call__(inner, request, timeout=None):
                if request.full_url.endswith("/releases?per_page=30") and len(naps) < 2:
                    inner.calls.append((request.full_url, None))
                    return contextlib.closing(io.BytesIO(b"[]"))
                return FakeGitHub.__call__(inner, request, timeout)

        github = Later(self.releases, self.files)
        blob, found = firmware_module.fetch_release(
            *self.ARGS, commit="c" * 40, wait_seconds=300, opener=github,
            sleep=naps.append, say=said.append, tags=no_git)
        self.assertEqual((blob, found["tag"]), (self.new, "firmware-build-3"))
        self.assertEqual(len(naps), 2)
        self.assertTrue(all(0 < nap <= firmware_module.POLL_SECONDS for nap in naps))
        self.assertIn("下载 firmware-build-3 ……", said)

    def test_a_build_that_already_failed_is_not_waited_for(self):
        naps = []
        runs = {"workflow_runs": [
            {"name": "Companion app", "status": "completed", "conclusion": "failure",
             "html_url": "https://example.invalid/app"},
            {"name": "Pocket firmware", "status": "completed", "conclusion": "failure",
             "html_url": "https://example.invalid/run/7"},
        ]}

        def github(request, timeout=None):
            url = request.full_url
            if "/actions/runs?head_sha=" + "d" * 40 in url:
                return contextlib.closing(io.BytesIO(json.dumps(runs).encode()))
            return contextlib.closing(io.BytesIO(b"[]"))

        with self.assertRaisesRegex(FirmwareError, "构建失败了：https://example.invalid/run/7"):
            firmware_module.fetch_release(*self.ARGS, commit="d" * 40, wait_seconds=600,
                                          opener=github, sleep=naps.append, tags=no_git)
        self.assertEqual(naps, [])
        # Still running, or only other workflows failed: keep waiting.
        runs["workflow_runs"][1].update(status="in_progress", conclusion=None)
        self.assertIsNone(firmware_module.failed_build("me/repo", "d" * 40, opener=github))

    def test_a_download_that_does_not_match_its_published_hash_is_refused(self):
        bad, files = release("firmware-build-4", "e" * 40, self.new, digest="0" * 64)
        with self.assertRaisesRegex(FirmwareError, "对不上"):
            firmware_module.fetch_release(*self.ARGS, opener=FakeGitHub([bad], files),
                                          tags=no_git)

    def test_github_problems_are_explained(self):
        def limited(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 403, "rate limit", {}, io.BytesIO(b""))

        def offline(request, timeout=None):
            raise urllib.error.URLError("no route")

        with self.assertRaisesRegex(FirmwareError, "GITHUB_TOKEN"):
            firmware_module.fetch_release(*self.ARGS, opener=limited, tags=no_git)
        with self.assertRaisesRegex(FirmwareError, "连不上"):
            firmware_module.fetch_release(*self.ARGS, opener=offline, tags=no_git)
        with self.assertRaisesRegex(FirmwareError, "发布列表"):
            firmware_module.fetch_release(
                *self.ARGS, tags=no_git, opener=lambda request, timeout=None: contextlib.closing(
                    io.BytesIO(b'{"message":"Not Found"}')))


    # ---- finding builds through git, without the rate-limited API ----

    WEB = "https://github.com/me/repo/releases/download/"

    def web_files(self, tag, blob, digest=True):
        asset = self.ARGS[1]
        files = {self.WEB + tag + "/" + asset: blob}
        if digest:
            files[self.WEB + tag + "/" + asset + ".sha256"] = (
                "%s  %s\n" % (hashlib.sha256(blob).hexdigest(), asset)).encode()
        return files

    def test_git_lists_the_builds_and_the_api_is_not_asked(self):
        listed = {"firmware-build-9": "9" * 40, "firmware-build-10": "a" * 40,
                  "firmware-build-2": "b" * 40}
        files = dict(self.web_files("firmware-build-10", self.new),
                     **self.web_files("firmware-build-2", self.old))
        github = FakeGitHub([], files)
        tags = lambda repo, prefix: dict(listed)

        blob, found = firmware_module.fetch_release(*self.ARGS, opener=github, token="t0ken",
                                                    tags=tags)
        # The newest is the highest build number, not the last in spelling order.
        self.assertEqual((blob, found["tag"], found["commit"]),
                         (self.new, "firmware-build-10", "a" * 40))
        blob, found = firmware_module.fetch_release(*self.ARGS, commit="b" * 12, opener=github,
                                                    tags=tags)
        self.assertEqual((blob, found["tag"]), (self.old, "firmware-build-2"))
        blob, found = firmware_module.fetch_release(*self.ARGS, tag="firmware-build-2",
                                                    opener=github, tags=tags)
        self.assertEqual(blob, self.old)
        with self.assertRaisesRegex(FirmwareError, "没找到"):
            firmware_module.fetch_release(*self.ARGS, commit="d" * 40, opener=github, tags=tags)
        # A tag without files (build 9) is "not there", not a crash.
        with self.assertRaisesRegex(FirmwareError, "没找到"):
            firmware_module.fetch_release(*self.ARGS, tag="firmware-build-9", opener=github,
                                          tags=tags)
        # Nothing went to the API, and the token never left for the download hosts.
        self.assertTrue(all(url.startswith(self.WEB) and auth is None
                            for url, auth in github.calls))

    def test_waiting_through_git_asks_the_api_only_now_and_then(self):
        naps = []
        listed = {}
        files = self.web_files("firmware-build-11", self.new, digest=False)
        github = FakeGitHub([], {})
        runs = []

        def opener(request, timeout=None):
            if "/actions/runs?head_sha=" in request.full_url:
                runs.append(request.full_url)
                return contextlib.closing(io.BytesIO(b'{"workflow_runs": []}'))
            return github(request, timeout)

        def nap(seconds):
            naps.append(seconds)
            if len(naps) == 3:
                listed["firmware-build-11"] = "c" * 40   # the tag appears first ...
            if len(naps) == 5:
                github.files.update(files)               # ... and its files a little later

        blob, found = firmware_module.fetch_release(
            *self.ARGS, commit="c" * 40, wait_seconds=900, opener=opener, sleep=nap,
            tags=lambda repo, prefix: dict(listed))
        self.assertEqual((blob, found["tag"]), (self.new, "firmware-build-11"))
        self.assertEqual(len(naps), 5)
        # Five rounds of waiting, one question to the API (the clock barely moved in this test).
        self.assertEqual(len(runs), 1)
        self.assertFalse(any("api.github.com/repos/me/repo/releases" in url
                             for url, _ in github.calls))

    def test_remote_tags_reads_what_git_prints(self):
        seen = {}

        def run(command, **options):
            seen["command"] = command
            seen["prompt"] = options["env"].get("GIT_TERMINAL_PROMPT")

            class Done:
                returncode = 0
                stdout = ("%s\trefs/tags/firmware-build-1\n" % ("1" * 40)
                          + "%s\trefs/tags/firmware-build-2\n" % ("0" * 40)
                          + "%s\trefs/tags/firmware-build-2^{}\n" % ("2" * 40)
                          + "garbage\n")
            return Done

        tags = firmware_module.remote_tags("me/repo", "firmware-build-", run=run)
        # An annotated tag counts as the commit it points to.
        self.assertEqual(tags, {"firmware-build-1": "1" * 40, "firmware-build-2": "2" * 40})
        self.assertEqual(seen["command"], ["git", "ls-remote", "--tags",
                                           "https://github.com/me/repo.git", "firmware-build-*"])
        self.assertEqual(seen["prompt"], "0")

        def missing(command, **options):
            raise FileNotFoundError("git")

        def refused(command, **options):
            class Done:
                returncode = 128
                stdout = ""
            return Done

        self.assertIsNone(firmware_module.remote_tags("me/repo", "firmware-build-", run=missing))
        self.assertIsNone(firmware_module.remote_tags("me/repo", "firmware-build-", run=refused))


class BuildTests(TempDirCase):
    def test_a_local_build_runs_the_configured_command(self):
        output = self.folder / "build" / "app.bin"
        script = ("import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.parent.mkdir(exist_ok=True); "
                  "p.write_bytes(b'built')")
        blob = firmware_module.build_locally(
            [sys.executable, "-c", script, str(output)], self.folder, output)
        self.assertEqual(blob, b"built")
        with self.assertRaisesRegex(FirmwareError, "build_command"):
            firmware_module.build_locally([], self.folder, output)
        with self.assertRaisesRegex(FirmwareError, "退出码 3"):
            firmware_module.build_locally(
                [sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"], self.folder, output)
        with self.assertRaisesRegex(FirmwareError, "找不到构建命令"):
            firmware_module.build_locally(["no-such-build-tool-xyz"], self.folder, output)
        with self.assertRaisesRegex(FirmwareError, "读不到"):
            firmware_module.build_locally([sys.executable, "-c", "pass"], self.folder,
                                          self.folder / "missing.bin")
        with self.assertRaisesRegex(FirmwareError, "git"):
            firmware_module.head_commit(self.folder)


class ConfigTests(TempDirCase):
    def test_the_firmware_section(self):
        loaded = config_module.load(write_config(self.folder), {})
        self.assertIsNone(loaded.firmware_repo)
        self.assertEqual(loaded.firmware_asset, "FoloToy-AI-Passport-full.bin")
        self.assertEqual(loaded.firmware_build_command, [])
        loaded = config_module.load(write_config(self.folder, firmware={
            "repo": "yanyuliu01/ai-passport", "source_dir": "src",
            "build_command": ["make", "firmware"], "build_output": "out/app.bin",
        }), {})
        self.assertEqual(loaded.firmware_repo, "yanyuliu01/ai-passport")
        self.assertEqual(loaded.firmware_source_dir, (self.folder / "src").resolve())
        self.assertEqual(loaded.firmware_build_output, "out/app.bin")
        for bad in ({"repo": "just-a-name"}, {"repo": "https://github.com/a/b"}, {"asset": " "},
                    {"build_command": ["make"]}, {"build_command": "make"},
                    {"build_command": ["make"], "source_dir": "src", "build_output": ""}):
            with self.assertRaises(config_module.ConfigError, msg=bad):
                config_module.load(write_config(self.folder, firmware=bad), {})


class HttpTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.base, self.config = start_runtime(self, self.folder)
        self.store = FirmwareStore(self.config.state_dir)

    def call(self, method, path, body=None, token=TOKEN, raw=None, binary=False):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if token:
            request.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = response.read()
                return response.status, payload if binary else json.loads(payload)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_the_library_needs_the_token(self):
        for method, path in (("GET", "/v1/firmware"), ("GET", "/v1/firmware/target"),
                             ("POST", "/v1/firmware/device")):
            status, _ = self.call(method, path, body={} if method == "POST" else None, token=None)
            self.assertEqual(status, 401, path)

    def test_upload_list_download_and_push(self):
        app = make_app("http")
        status, body = self.call("GET", "/v1/firmware")
        self.assertEqual((status, body["versions"], body["target"], body["device"]),
                         (200, [], None, None))
        status, entry = self.call("POST", "/v1/firmware?note=from%20test", raw=make_merged(app))
        self.assertEqual((status, entry["seq"], entry["note"], entry["size"]),
                         (201, 1, "from test", len(app)))
        status, body = self.call("GET", "/v1/firmware")
        self.assertEqual([item["id"] for item in body["versions"]], [entry["id"]])
        status, blob = self.call("GET", "/v1/firmware/%s/image" % entry["id"], binary=True)
        self.assertEqual((status, blob), (200, app))
        status, body = self.call("GET", "/v1/firmware/ffffffffffff/image")
        self.assertEqual(status, 404)

        # The phone's view: one flat object, empty id when there is nothing to do.
        status, target = self.call("GET", "/v1/firmware/target")
        self.assertEqual((status, target["id"]), (200, ""))
        self.assertTrue(all(not isinstance(value, (dict, list)) for value in target.values()))
        status, target = self.call("POST", "/v1/firmware/target", {"id": "latest"})
        self.assertEqual((status, target["id"], target["sha256"]),
                         (200, entry["id"], hashlib.sha256(app).hexdigest()))
        status, answer = self.call("POST", "/v1/firmware/device",
                                   {"event": "installed", "build": entry["build"], "state": "valid"})
        self.assertEqual((status, answer["id"]), (200, ""))
        status, body = self.call("GET", "/v1/firmware")
        self.assertEqual((body["device"]["seq"], body["target"]), (1, None))
        # Asking by number, and taking the request back.
        status, target = self.call("POST", "/v1/firmware/target", {"id": 1, "reason": "restore"})
        self.assertEqual((target["id"], target["reason"]), (entry["id"], "restore"))
        status, target = self.call("POST", "/v1/firmware/target", {"id": None})
        self.assertEqual((status, target["id"]), (200, ""))

    def test_upload_and_push_in_one_request(self):
        status, entry = self.call("POST", "/v1/firmware?push=1", raw=make_app("one"))
        self.assertEqual(status, 201)
        self.assertEqual(self.store.target()["id"], entry["id"])

    def test_waiting_for_the_target_to_change(self):
        entry = self.store.add(make_app("wait"))
        rev = self.store.rev()
        timer = threading.Timer(0.3, lambda: self.store.set_target(entry["id"]))
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        status, target = self.call("GET", "/v1/firmware/target?wait=10&rev=%d" % rev)
        self.assertEqual((status, target["id"]), (200, entry["id"]))
        self.assertLess(time.monotonic() - started, 8)
        self.assertGreater(target["rev"], rev)
        # A stale rev answers at once.
        started = time.monotonic()
        self.call("GET", "/v1/firmware/target?wait=10&rev=%d" % rev)
        self.assertLess(time.monotonic() - started, 2)

    def test_bad_requests_are_refused_with_a_reason(self):
        self.store.add(make_app("old", updatable=False))
        cases = (
            ("POST", "/v1/firmware", b"junk" * 100, "分区表"),
            ("POST", "/v1/firmware/target", json.dumps({"id": "nope"}).encode(), "没有对上"),
            ("POST", "/v1/firmware/target", json.dumps({"id": True}).encode(), "id"),
            ("POST", "/v1/firmware/target", json.dumps({"id": 1}).encode(), "插线"),
            ("POST", "/v1/firmware/target", b"[1]", "对象"),
            ("POST", "/v1/firmware/device", json.dumps({"event": "x"}).encode(), "event"),
        )
        for method, path, raw, why in cases:
            status, body = self.call(method, path, raw=raw)
            self.assertEqual(status, 400, path)
            self.assertIn(why, body["error"])
        self.assertEqual(self.call("GET", "/v1/firmware/target?wait=x")[0], 400)
        self.assertEqual(self.call("GET", "/v1/firmware/target?rev=x")[0], 400)
        self.assertEqual(self.call("GET", "/v1/firmware/a/b/c")[0], 404)
        self.assertEqual(self.call("POST", "/v1/firmware/nothing", {})[0], 404)
        self.assertEqual(self.call("POST", "/v1/firmware", raw=b"")[0], 413)
        # Forcing is explicit.
        status, target = self.call("POST", "/v1/firmware/target", {"id": 1, "force": True})
        self.assertEqual((status, target["seq"]), (200, 1))


class CommandLineTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.config_path = write_config(self.folder, firmware={"repo": "me/repo"})
        self.store = FirmwareStore(self.folder / "state")

    def run_cli(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--config", str(self.config_path), "firmware", *arguments])
        return code, out.getvalue(), err.getvalue()

    def test_add_list_push_status_cancel_remove(self):
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("还没有固件", out)
        first = self.folder / "first.bin"
        first.write_bytes(make_merged(make_app("aaa1111")))
        second = self.folder / "second.bin"
        second.write_bytes(make_app("bbb2222"))
        code, out, _ = self.run_cli("add", str(first), "--note", "顶栏改细")
        self.assertEqual(code, 0)
        self.assertIn("已入库 第 1 版", out)
        code, out, _ = self.run_cli("add", str(second), "--push")
        self.assertIn("已入库 第 2 版", out)
        self.assertEqual(self.store.target()["seq"], 2)
        code, out, _ = self.run_cli("add", str(first))
        self.assertIn("已经有这一版了", out)

        self.store.report({"event": "progress", "build": self.store.find("1")["build"],
                           "state": "valid", "ver": "aaa1111", "sent": 50, "size": 200})
        code, out, _ = self.run_cli("list")
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("→") and "第 2 版" in lines[0] and "bbb2222" in lines[0])
        self.assertTrue(lines[1].startswith("●") and "顶栏改细" in lines[1])
        code, out, _ = self.run_cli("status")
        self.assertIn("设备：第 1 版", out)
        self.assertIn("正在传：25%", out)
        self.assertIn("要推的：第 2 版", out)

        code, out, _ = self.run_cli("cancel")
        self.assertIn("不推了", out)
        code, out, _ = self.run_cli("restore", "1")
        self.assertEqual((code, self.store.target()["reason"]), (0, "restore"))
        code, out, err = self.run_cli("remove", "1")
        self.assertEqual(code, 1)
        self.assertIn("cancel", err)
        self.run_cli("cancel")
        code, out, _ = self.run_cli("remove", "#1")
        self.assertEqual(code, 0)
        self.assertEqual([item["seq"] for item in self.store.snapshot()["versions"]], [2])

    def test_mistakes_are_explained(self):
        code, _, err = self.run_cli("add", str(self.folder / "missing.bin"))
        self.assertEqual(code, 1)
        self.assertIn("读不了", err)
        junk = self.folder / "junk.bin"
        junk.write_bytes(b"\x00" * 100)
        code, _, err = self.run_cli("add", str(junk))
        self.assertEqual(code, 1)
        code, _, err = self.run_cli("push", "latest")
        self.assertIn("还没有固件", err)
        code, _, err = self.run_cli("build")
        self.assertIn("source_dir", err)
        code, _, err = self.run_cli("fetch", "--commit", "HEAD")
        self.assertIn("source_dir", err)

    def test_fetch_stores_what_github_built(self):
        merged = make_merged(make_app("ccc3333"))
        entry, files = release("firmware-build-7", "c" * 40, merged)
        loaded = config_module.load(self.config_path, {})
        said = []

        class Args:
            action = "fetch"
            tag = None
            repo = None
            commit = "c" * 40
            wait = 0
            note = "from CI"
            push = True
            detach = False

        code = firmware_cli.run(loaded, self.config_path, Args, [], out=said.append,
                                opener=FakeGitHub([entry], files), tags=no_git)
        self.assertEqual(code, 0)
        stored = self.store.find("latest")
        self.assertEqual((stored["version"], stored["note"]), ("ccc3333", "from CI"))
        self.assertEqual(stored["source"], "github:firmware-build-7@cccccccccc")
        self.assertEqual(self.store.target()["id"], stored["id"])

        # Waiting for a build that never appears leaves a note where the phone will see it.
        Args.commit = "d" * 12
        Args.wait = 1
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = firmware_cli.run(loaded, self.config_path, Args, [], out=said.append,
                                    opener=FakeGitHub([entry], files), tags=no_git)
        self.assertEqual(code, 1)
        self.assertIn("没找到", err.getvalue())
        self.assertIn("没找到", self.store.target()["notice"])

    def test_the_server_command_line_is_unchanged(self):
        # No subcommand still means "run the service"; --check proves parsing got that far.
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--config", str(self.config_path), "--check"])
        self.assertEqual(code, 0)
        self.assertIn("配置没问题", out.getvalue())


if __name__ == "__main__":
    unittest.main()
