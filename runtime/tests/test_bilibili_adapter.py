"""Bounded anonymous content extraction, independent of the live service."""

import io
import json
import tempfile
import time
import urllib.error
import unittest
from pathlib import Path

from xiaoyu_runtime.entertainment import bilibili
from xiaoyu_runtime.entertainment import frames
from xiaoyu_runtime.entertainment.budget import Budget, Limited, MIN_INTERVAL, MAX_DAILY, RESTRICTION_COOLDOWN
from xiaoyu_runtime.entertainment.schedule import claim, defer, status as schedule_status

try:
    import PIL  # noqa: F401
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class Response:
    def __init__(self, value=b"", headers=None):
        self.value = value
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size):
        return self.value[:size]


def response(value):
    return Response(json.dumps(value).encode())


class BilibiliAdapterTest(unittest.TestCase):
    def test_extract_video_and_return_only_bounded_metadata(self):
        calls = []
        def opener(url):
            calls.append(url)
            return response({"code": 0, "data": {"bvid": "BV1GJ411x7h7", "title": "a",
                "desc": "x" * 2000, "owner": {"name": "creator"}, "duration": 60,
                "unexpected_private_field": "not included"}})
        result = bilibili.inspect("看看 BV1GJ411x7h7", opener)
        self.assertEqual(1, len(calls))
        self.assertEqual("video_metadata", result["source"])
        self.assertEqual(1200, len(result["items"][0]["description"]))
        self.assertNotIn("unexpected_private_field", result["items"][0])
        self.assertIn("未读取字幕", result["coverage"])

    def test_discovery_is_bounded_and_explicitly_not_personalized(self):
        def opener(url):
            self.assertIn("popular?ps=8", url)
            return response({"code": 0, "data": {"list": [
                {"bvid": "BV1GJ411x7h7", "title": str(i)} for i in range(20)]}})
        result = bilibili.inspect("找点有趣的", opener)
        self.assertEqual(8, len(result["items"]))
        self.assertIn("并非个性化", result["coverage"])

    def test_discovery_can_read_fewer_candidates_without_reading_video(self):
        calls = []
        def opener(url):
            calls.append(url)
            return response({"code": 0, "data": {"list": [
                {"bvid": "BV1GJ411x7h7", "title": str(i)} for i in range(8)]}})
        with tempfile.TemporaryDirectory() as folder:
            result = bilibili.process("找点有趣的", Path(folder), opener=opener, max_items=2, read_video=False)
        data = json.loads(result.split('\n', 1)[1])
        self.assertEqual(2, len(data['items']))
        self.assertNotIn('sampled_video', data)
        self.assertEqual(1, len(calls))

    def test_short_link_redirect_must_be_official_video(self):
        def opener(url):
            self.assertEqual("https://b23.tv/abcd", url)
            raise urllib.error.HTTPError(url, 302, "Found", {"Location": "https://www.bilibili.com/video/BV1GJ411x7h7?tracking=1"}, io.BytesIO())
        self.assertEqual("BV1GJ411x7h7", bilibili.video_id("https://b23.tv/abcd", opener))
        def bad(url):
            raise urllib.error.HTTPError(url, 302, "Found", {"Location": "https://evil.invalid/BV1GJ411x7h7"}, io.BytesIO())
        with self.assertRaises(bilibili.Unavailable):
            bilibili.video_id("https://b23.tv/abcd", bad)

    def test_restriction_stops_without_retry(self):
        calls = []
        def opener(url):
            calls.append(url)
            raise urllib.error.HTTPError(url, 412, "blocked", {}, io.BytesIO())
        with self.assertRaisesRegex(bilibili.Unavailable, "停止请求"):
            bilibili.inspect("发现", opener)
        self.assertEqual(1, len(calls))

    def test_rejects_oversized_and_bad_payloads(self):
        for value in (Response(b"x" * (bilibili.MAX_RESPONSE + 1)), response({"code": -412, "data": {}})):
            with self.assertRaises(bilibili.Unavailable):
                bilibili.inspect("发现", lambda url: value)

    def test_subtitles_are_read_without_claiming_to_watch(self):
        calls = []
        def opener(url):
            calls.append(url)
            if "/view?" in url:
                return response({"code": 0, "data": {"bvid": "BV1GJ411x7h7", "cid": 123, "duration": 90}})
            if "/player/" in url:
                return response({"code": 0, "data": {"subtitle": {"subtitles": [
                    {"lan": "ai-zh", "subtitle_url": "//aisubtitle.hdslb.com/a.json"}]}}})
            return response({"body": [{"from": 0, "to": 2, "content": "字幕文本"}]})
        result = bilibili.inspect("BV1GJ411x7h7", opener)
        self.assertEqual(3, len(calls))
        self.assertEqual("subtitle", result["source"])
        self.assertEqual("字幕文本", result["transcript"])
        self.assertIn("AI 生成", result["coverage"])
        self.assertIn("未读取视频画面或音频", result["coverage"])

    def test_subtitle_url_must_not_access_untrusted_host(self):
        calls = []
        def opener(url):
            calls.append(url)
            if "/view?" in url:
                return response({"code": 0, "data": {"bvid": "BV1GJ411x7h7", "cid": 123}})
            return response({"code": 0, "data": {"subtitle": {"subtitles": [
                {"subtitle_url": "http://localhost/private"}]}}})
        result = bilibili.inspect("BV1GJ411x7h7", opener)
        self.assertEqual(2, len(calls))
        self.assertEqual("video_metadata", result["source"])
        self.assertIn("不受支持", result["subtitle_status"])

    def test_subtitle_outlasting_video_is_not_used(self):
        def opener(url):
            if "/view?" in url:
                return response({"code": 0, "data": {"bvid": "BV1GJ411x7h7", "cid": 123,
                    "duration": 90, "pages": [{"cid": 123, "duration": 90}]}})
            if "/player/" in url:
                return response({"code": 0, "data": {"bvid": "BV1GJ411x7h7", "cid": 123,
                    "subtitle": {"subtitles": [{"lan": "ai-zh", "subtitle_url": "//aisubtitle.hdslb.com/a.json"}]}}})
            return response({"body": [{"from": 0.28, "to": 1.9, "content": "开头"},
                                       {"from": 421.94, "to": 423.02, "content": "不属于这个视频"}]})
        result = bilibili.inspect("BV1GJ411x7h7", opener)
        self.assertNotIn("transcript", result)
        self.assertEqual("video_metadata", result["source"])
        self.assertIn("不符", result["subtitle_status"])

    def test_player_identity_must_match_the_requested_video(self):
        calls = []
        def opener(url):
            calls.append(url)
            if "/view?" in url:
                return response({"code": 0, "data": {"bvid": "BV1GJ411x7h7", "cid": 123, "duration": 90}})
            return response({"code": 0, "data": {"bvid": "BV1GJ411x7h7", "cid": 456,
                "subtitle": {"subtitles": [{"subtitle_url": "//aisubtitle.hdslb.com/a.json"}]}}})
        result = bilibili.inspect("BV1GJ411x7h7", opener)
        self.assertEqual(2, len(calls))
        self.assertNotIn("transcript", result)
        self.assertIn("不符", result["subtitle_status"])

    @unittest.skipUnless(HAS_PIL, "Pillow not installed")
    def test_empty_preview_index_uses_platform_binary_timeline(self):
        import struct
        from PIL import Image
        stream = io.BytesIO()
        Image.new("RGB", (20, 20), "red").save(stream, format="PNG")
        def opener(url):
            if "/videoshot?" in url:
                return response({"code": 0, "data": {"image": ["//i0.hdslb.com/a.png"],
                    "pvdata": "//boss.hdslb.com/timeline.bin", "index": [], "img_x_len": 2,
                    "img_y_len": 2, "img_x_size": 10, "img_y_size": 10}})
            if url.endswith("timeline.bin"):
                return Response(struct.pack(">4H", 0, 3, 6, 9))
            return Response(stream.getvalue())
        sheet = frames.contact_sheet("BV1GJ411x7h7", 123, opener)
        self.assertEqual([0, 3, 6, 9], sheet["timestamps_seconds"])

    def test_preview_timeline_rejects_untrusted_hosts_and_restrictions(self):
        def metadata(pvdata):
            return response({"code": 0, "data": {"image": ["//i0.hdslb.com/a.png"],
                "pvdata": pvdata, "index": [], "img_x_len": 1, "img_y_len": 1,
                "img_x_size": 10, "img_y_size": 10}})
        calls = []
        def untrusted(url):
            calls.append(url)
            return metadata("https://example.org/private")
        with self.assertRaisesRegex(bilibili.Unavailable, "不受支持"):
            frames.contact_sheet("BV1GJ411x7h7", 123, untrusted)
        self.assertEqual(1, len(calls))
        calls.clear()
        def restricted(url):
            calls.append(url)
            if "/videoshot?" in url:
                return metadata("//boss.hdslb.com/timeline.bin")
            raise urllib.error.HTTPError(url, 412, "blocked", {}, io.BytesIO())
        with self.assertRaises(bilibili.Unavailable) as error:
            frames.contact_sheet("BV1GJ411x7h7", 123, restricted)
        self.assertTrue(error.exception.restricted)
        self.assertEqual(2, len(calls))

    @unittest.skipUnless(HAS_PIL, "Pillow not installed")
    def test_storyboard_samples_real_timestamps(self):
        from PIL import Image
        stream = io.BytesIO()
        Image.new("RGB", (20, 20), "red").save(stream, format="PNG")
        def opener(url):
            if "/videoshot?" in url:
                return response({"code": 0, "data": {"image": ["//i0.hdslb.com/a.png"],
                    "index": [0, 2, 5, 9], "img_x_len": 2, "img_y_len": 2,
                    "img_x_size": 10, "img_y_size": 10}})
            return Response(stream.getvalue())
        sheet = frames.contact_sheet("BV1GJ411x7h7", 123, opener)
        self.assertEqual([0, 2, 5, 9], sheet["timestamps_seconds"])
        self.assertEqual("PNG", Image.open(io.BytesIO(sheet["image"])).format)
        self.assertIn("抽样静帧", sheet["coverage"])

    @unittest.skipUnless(HAS_PIL, "Pillow not installed")
    def test_storyboard_refuses_untrusted_image_host(self):
        def opener(url):
            if "/videoshot?" in url:
                return response({"code": 0, "data": {"image": ["//evil.invalid/a.png"],
                    "index": [0], "img_x_len": 1, "img_y_len": 1,
                    "img_x_size": 10, "img_y_size": 10}})
            self.fail("must not access unexpected host")
        with self.assertRaisesRegex(bilibili.Unavailable, "不受支持"):
            frames.contact_sheet("BV1GJ411x7h7", 123, opener)

    def test_budget_is_persistent_and_restriction_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "budget.sqlite3"
            budget = Budget(path)
            now = 10 * 86400 + 100
            budget.claim(now)
            with self.assertRaises(Limited):
                Budget(path).claim(now + 1)
            budget.restricted(now + 1)
            with self.assertRaises(Limited):
                Budget(path).claim(now + MIN_INTERVAL + 1)
            Budget(path).claim(now + RESTRICTION_COOLDOWN + 2)

    def test_user_selected_daily_and_interval_budget(self):
        self.assertEqual(600, MIN_INTERVAL)
        self.assertEqual(12, MAX_DAILY)
        with tempfile.TemporaryDirectory() as folder:
            budget = Budget(Path(folder) / "budget.sqlite3")
            now = 20 * 86400 + 100
            for index in range(MAX_DAILY):
                budget.claim(now + index * MIN_INTERVAL)
            with self.assertRaisesRegex(Limited, "额度"):
                budget.claim(now + MAX_DAILY * MIN_INTERVAL)

    def test_budget_status_is_read_only_and_exposes_real_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'budget.sqlite3'
            budget = Budget(path)
            now = 20 * 86400 + 100
            initial = budget.status(now)
            self.assertEqual(12, initial['remaining_today'])
            self.assertEqual(0, initial['wait_seconds'])
            self.assertFalse(path.exists())
            budget.claim(now)
            status = budget.status(now + 1)
            self.assertEqual(11, status['remaining_today'])
            self.assertEqual(599, status['wait_seconds'])
            budget.restricted(now + 1)
            self.assertTrue(budget.status(now + 2)['platform_restricted'])
            self.assertGreater(budget.status(now + 2)['wait_seconds'], MIN_INTERVAL)

    def test_daily_discovery_claim_survives_restart_and_respects_quiet_hours(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "schedule.sqlite3"
            today = time.localtime()
            at_noon = time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 12, 0, 0, 0, 0, -1))
            self.assertFalse(claim(path, at_noon - 9 * 3600))
            self.assertTrue(claim(path, at_noon))
            self.assertFalse(claim(path, at_noon + 60))
            self.assertTrue(claim(path, at_noon + 86400))

    def test_deferred_opportunity_is_persistent_and_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'schedule.sqlite3'
            today = time.localtime()
            noon = time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 12, 0, 0, 0, 0, -1))
            self.assertEqual(3, schedule_status(path, noon)['deferrals_remaining'])
            self.assertTrue(claim(path, noon))
            for turn in range(3):
                now = noon + turn * 600
                self.assertTrue(defer(path, 10, now))
                self.assertEqual(2 - turn, schedule_status(path, now)['deferrals_remaining'])
                self.assertEqual(600, schedule_status(path, now)['scheduled_in_seconds'])
                self.assertFalse(claim(path, now + 599))
                self.assertTrue(claim(path, now + 600))
            self.assertFalse(defer(path, 10, noon + 1800))

    @unittest.skipUnless(HAS_PIL, "Pillow not installed")
    def test_request_saves_frame_artifact_and_obeys_cooldown(self):
        from PIL import Image
        image = io.BytesIO()
        Image.new("RGB", (20, 20), "red").save(image, format="PNG")
        calls = []
        def opener(url):
            calls.append(url)
            if "/view?" in url:
                return response({"code": 0, "data": {"bvid": "BV1GJ411x7h7", "cid": 123}})
            if "/player/v2?" in url:
                return response({"code": 0, "data": {"subtitle": {"subtitles": []}}})
            if "/videoshot?" in url:
                return response({"code": 0, "data": {"image": ["//i0.hdslb.com/a.png"],
                    "index": [0, 3, 6, 9], "img_x_len": 2, "img_y_len": 2,
                    "img_x_size": 10, "img_y_size": 10}})
            return Response(image.getvalue())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            task = root / "task"
            task.mkdir()
            result = bilibili.process("BV1GJ411x7h7", root / "state", task, opener)
            self.assertIn("visual_samples", result)
            manifest = json.loads((task / "manifest.json").read_text())
            self.assertTrue((task / manifest["artifacts"][0]["path"]).is_file())
            before = len(calls)
            self.assertIn("冷却期", bilibili.process("BV1GJ411x7h7", root / "state", task, opener))
            self.assertEqual(before, len(calls))


if __name__ == "__main__":
    unittest.main()
