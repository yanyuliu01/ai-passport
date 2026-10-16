#!/usr/bin/env python3
"""Resource packs (incremental app updates) and GitHub Releases uploads. Standard library only.

Large files that rarely change (speech models) leave the APK and are published per file by
SHA-256, so a phone downloads only what changed. They prove the Runtime side: storage,
serving with resume, and uploading the same bytes to GitHub. They do not prove a phone
downloads or uses them.
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
from unittest import mock

RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_app_release import write_apk  # noqa: E402
from test_runtime import TOKEN, TempDirCase, start_runtime, write_config  # noqa: E402
from xiaoyou_runtime import app_release, github_release  # noqa: E402
from xiaoyou_runtime.__main__ import main  # noqa: E402
from xiaoyou_runtime.app_release import AppRelease, AppReleaseError  # noqa: E402

MODEL = bytes(range(256)) * 64
TOKENS = "a 1\nb 2\n".encode("utf-8")


def write_pack(folder, model=MODEL, tokens=TOKENS):
    (folder / "sense-voice").mkdir(parents=True, exist_ok=True)
    (folder / "sense-voice" / "model.int8.onnx").write_bytes(model)
    (folder / "sense-voice" / "tokens.txt").write_bytes(tokens)
    (folder / ".DS_Store").write_bytes(b"ignored")
    return folder


class PackTest(TempDirCase):
    def test_files_are_stored_once_by_checksum_and_unchanged_files_keep_their_ids(self):
        release = AppRelease(self.folder / "state")
        self.assertEqual({"packs": []}, release.packs())
        first = release.publish_pack("speech", write_pack(self.folder / "v1"))
        self.assertEqual(["sense-voice/model.int8.onnx", "sense-voice/tokens.txt"],
                         [item["path"] for item in first["files"]])
        self.assertEqual(hashlib.sha256(MODEL).hexdigest(), first["files"][0]["sha256"])
        # A new version that only changes the tokens: the model keeps its checksum, so a phone
        # that has it downloads only the small file.
        second = release.publish_pack("speech", write_pack(self.folder / "v2", tokens=b"c 3\n"))
        self.assertEqual(first["files"][0]["sha256"], second["files"][0]["sha256"])
        self.assertNotEqual(first["files"][1]["sha256"], second["files"][1]["sha256"])
        stored = sorted(path.name for path in (self.folder / "state" / "app-release" / "packs").glob("*.bin"))
        self.assertEqual(2, len(stored))  # the old tokens file is gone
        stream, size = release.open_pack_file(second["files"][0]["sha256"])
        with stream:
            self.assertEqual((MODEL, len(MODEL)), (stream.read(), size))
        with self.assertRaises(AppReleaseError):
            release.open_pack_file(first["files"][1]["sha256"])

    def test_rejects_bad_names_empty_folders_and_unsafe_urls(self):
        release = AppRelease(self.folder / "state")
        for name in ("", "Speech", "1x", "a/b"):
            with self.subTest(name=name), self.assertRaises(AppReleaseError):
                release.publish_pack(name, write_pack(self.folder / "p"))
        (self.folder / "empty").mkdir()
        with self.assertRaises(AppReleaseError):
            release.publish_pack("speech", self.folder / "empty")
        with self.assertRaises(AppReleaseError):
            release.publish_pack("speech", write_pack(self.folder / "p"),
                                 {hashlib.sha256(MODEL).hexdigest(): "http://example.com/m"})
        self.assertEqual({"packs": []}, release.packs())

    def test_command_line_pack_and_status(self):
        path = write_config(self.folder)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(0, main(["--config", str(path), "app", "pack", "speech",
                                      str(write_pack(self.folder / "models"))]))
            self.assertEqual(0, main(["--config", str(path), "app", "status"]))
        self.assertIn("资源包 speech：2 个文件", printed.getvalue())
        self.assertIn("从这台电脑下载", printed.getvalue())


class PackHttpTest(TempDirCase):
    def setUp(self):
        super().setUp()
        self.base, self.config = start_runtime(self, self.folder)

    def get(self, path, headers=None, token=TOKEN):
        request = urllib.request.Request(self.base + path)
        if token:
            request.add_header("Authorization", "Bearer " + token)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.headers, response.read()

    def test_manifest_and_files_are_served_with_resume(self):
        self.assertEqual({"packs": []}, json.loads(self.get("/v1/app/packs")[2]))
        pack = AppRelease(self.config.state_dir).publish_pack("speech", write_pack(self.folder / "m"))
        listed = json.loads(self.get("/v1/app/packs")[2])["packs"][0]
        self.assertEqual(pack["files"], listed["files"])
        sha = pack["files"][0]["sha256"]
        status, headers, body = self.get("/v1/app/file?sha256=" + sha)
        self.assertEqual((200, MODEL, '"%s"' % sha), (status, body, headers["ETag"]))
        status, headers, body = self.get("/v1/app/file?sha256=" + sha,
                                         {"Range": "bytes=100-", "If-Range": '"%s"' % sha})
        self.assertEqual((206, MODEL[100:]), (status, body))
        for path, code in (("/v1/app/file?sha256=" + "0" * 64, 409), ("/v1/app/file", 404)):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as error:
                self.get(path)
            self.assertEqual(code, error.exception.code)
        with self.assertRaises(urllib.error.HTTPError) as refused:
            self.get("/v1/app/packs", token=None)
        self.assertEqual(401, refused.exception.code)


class FakeGitHub:
    """Stands in for api.github.com and uploads.github.com."""

    def __init__(self):
        self.releases, self.uploads, self.deleted, self.headers = {}, [], [], []

    def __call__(self, request, timeout=None):
        self.headers.append(dict(request.header_items()))
        url, method = request.full_url, request.get_method()
        if "/releases/tags/" in url:
            tag = url.rsplit("/", 1)[1]
            if tag not in self.releases:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"{}"))
            return self.reply(self.releases[tag])
        if method == "POST" and url.endswith("/releases"):
            body = json.loads(request.data)
            made = {"id": len(self.releases) + 1, "tag_name": body["tag_name"], "assets": []}
            self.releases[body["tag_name"]] = made
            return self.reply(made)
        if method == "POST" and "/assets?name=" in url:
            name = url.split("name=", 1)[1]
            data = request.data.read(1 << 30) if hasattr(request.data, "read") else request.data
            self.uploads.append((name, data))
            asset = {"id": 100 + len(self.uploads), "name": name, "size": len(data), "state": "uploaded",
                     "browser_download_url": "https://github.com/o/r/releases/download/t/" + name}
            for release in self.releases.values():
                if "/releases/%d/" % release["id"] in url:
                    release["assets"].append(asset)
            return self.reply(asset)
        if method == "DELETE":
            self.deleted.append(url)
            return self.reply(None)
        raise AssertionError("unexpected %s %s" % (method, url))

    @staticmethod
    def reply(value):
        return io.BytesIO(json.dumps(value).encode("utf-8"))


class GitHubTest(TempDirCase):
    def releases(self, fake):
        return github_release.Releases("o/r", "secret-token", opener=fake, log=lambda line: None)

    def test_same_file_is_not_uploaded_twice_and_a_broken_one_is_replaced(self):
        fake = FakeGitHub()
        github = self.releases(fake)
        path = self.folder / "model.bin"
        path.write_bytes(MODEL)
        published = github.release("app-packs", "packs", "body")
        url = github.upload(published, path, "speech-abc-model.bin")
        self.assertEqual("https://github.com/o/r/releases/download/t/speech-abc-model.bin", url)
        self.assertEqual(MODEL, fake.uploads[0][1])
        again = github.upload(github.release("app-packs", "packs", "body"), path, "speech-abc-model.bin")
        self.assertEqual((url, 1), (again, len(fake.uploads)))
        # A half-uploaded asset under that name is deleted and uploaded again.
        fake.releases["app-packs"]["assets"][0].update(size=5, state="starter")
        github.upload(fake.releases["app-packs"], path, "speech-abc-model.bin")
        self.assertEqual((1, 2), (len(fake.deleted), len(fake.uploads)))
        self.assertTrue(all(headers.get("Authorization") == "Bearer secret-token" for headers in fake.headers))
        with self.assertRaises(github_release.GitHubError):
            github.upload(published, path, "bad name with spaces")
        with self.assertRaises(github_release.GitHubError):
            github_release.Releases("not a repo", "t")

    def test_token_comes_from_the_environment_or_git(self):
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "from-env"}):
            self.assertEqual("from-env", github_release.token())
        done = mock.Mock(stdout="protocol=https\nhost=github.com\nusername=x\npassword=from-git\n")
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch("subprocess.run", return_value=done):
            self.assertEqual("from-git", github_release.token())
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch("subprocess.run", return_value=mock.Mock(stdout="")):
            with self.assertRaises(github_release.GitHubError):
                github_release.token()

    def test_publish_and_pack_put_the_uploaded_addresses_into_the_manifest(self):
        path = write_config(self.folder)
        fake = FakeGitHub()
        github = self.releases(fake)
        out = self.folder / "out"
        default = write_apk(out / "u")
        arm = write_apk(out / "a", b"PK\x03\x04arm", abi="arm64-v8a")
        printed = io.StringIO()
        with mock.patch.object(app_release, "_github", return_value=github), contextlib.redirect_stdout(printed):
            self.assertEqual(0, main(["--config", str(path), "app", "publish", str(default),
                                      "--variant", "arm64-v8a=%s" % arm, "--github"]))
            self.assertEqual(0, main(["--config", str(path), "app", "pack", "speech",
                                      str(write_pack(self.folder / "m")), "--github"]))
            # Publishing the same pack again uploads nothing new.
            self.assertEqual(0, main(["--config", str(path), "app", "pack", "speech",
                                      str(write_pack(self.folder / "m")), "--github"]))
        self.assertIn("app-0.10.2-17", fake.releases)
        self.assertEqual(["pocket-hub-0.10.2-arm64-v8a.apk"],
                         [name for name, _ in fake.uploads if name.endswith(".apk")])
        release = AppRelease(self.folder / "state")
        self.assertTrue(release.info("arm64-v8a")["download_url"].endswith("pocket-hub-0.10.2-arm64-v8a.apk"))
        self.assertEqual("", release.info()["download_url"])  # universal stays on the Runtime
        files = release.packs()["packs"][0]["files"]
        self.assertTrue(all(item["download_url"].startswith("https://github.com/") for item in files))
        self.assertEqual(3, len(fake.uploads))  # one APK and two pack files, nothing uploaded twice
        self.assertIn("也传到了 GitHub", printed.getvalue())

    def test_a_failed_upload_publishes_nothing(self):
        path = write_config(self.folder)
        broken = mock.Mock()
        broken.release.side_effect = github_release.GitHubError("GitHub 返回 401：bad credentials")
        printed = io.StringIO()
        with mock.patch.object(app_release, "_github", return_value=broken), contextlib.redirect_stdout(printed):
            self.assertEqual(1, main(["--config", str(path), "app", "publish",
                                      str(write_apk(self.folder / "out")), "--github", "universal"]))
        self.assertIn("没发布成", printed.getvalue())
        self.assertFalse(AppRelease(self.folder / "state").info()["available"])


if __name__ == "__main__":
    unittest.main()
