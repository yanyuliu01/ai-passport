"""Create a bounded contact sheet from Bilibili's optional video previews.

The resulting images are samples, not continuous video. No login or video
download is performed. Callers must pass the returned PNG to a vision model.
"""

import io
import math
import struct
import urllib.error
import urllib.parse
from .network import BV, Unavailable, get_json as _json, open_anonymous as _open

MAX_SHEET_BYTES = 4 * 1024 * 1024
MAX_SHEETS = 4
MAX_PIXELS = 16_000_000
FRAME_COUNT = 8
MAX_TIMELINE_BYTES = 40_000


def _url(url):
    parsed = urllib.parse.urlsplit("https:" + url if url.startswith("//") else url)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        raise Unavailable("预览图地址不受支持") from None
    if (parsed.scheme != "https" or not host.endswith(".hdslb.com") or parsed.username
            or parsed.password or port not in (None, 443)):
        raise Unavailable("预览图地址不受支持")
    return parsed.geturl()


def contact_sheet(bvid, cid, opener=_open):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        raise Unavailable("缺少图片处理组件") from None
    if not isinstance(cid, int) or isinstance(cid, bool) or cid <= 0:
        raise Unavailable("视频画面索引不可用")
    if not isinstance(bvid, str) or not BV.fullmatch(bvid):
        raise Unavailable("视频编号无效")
    data = _json("https://api.bilibili.com/x/player/videoshot?index=1&bvid=" + bvid + "&cid=" + str(cid), opener)
    urls, index = data.get("image"), data.get("index")
    if index is None:
        index = []
    cols, rows, width, height = (data.get(k) for k in ("img_x_len", "img_y_len", "img_x_size", "img_y_size"))
    if (not isinstance(urls, list) or not urls or not isinstance(index, list)
            or len(urls) > MAX_SHEETS or any(not isinstance(url, str) for url in urls)
            or any(not isinstance(x, int) or isinstance(x, bool) or not 0 < x <= 50 for x in (cols, rows))
            or any(not isinstance(x, int) or isinstance(x, bool) or not 0 < x <= 512 for x in (width, height))
            or cols * width * rows * height > MAX_PIXELS):
        raise Unavailable("平台未提供可用的画面预览")
    if not index and isinstance(data.get("pvdata"), str):
        try:
            with opener(_url(data["pvdata"])) as response:
                timeline = response.read(MAX_TIMELINE_BYTES + 1)
            if not timeline or len(timeline) > MAX_TIMELINE_BYTES or len(timeline) % 2:
                raise Unavailable("画面预览时间索引无效")
            index = list(struct.unpack(">%dH" % (len(timeline) // 2), timeline))
        except urllib.error.HTTPError as error:
            raise Unavailable("画面预览时间索引不可用", restricted=error.code in (403, 412, 429)) from None
        except (urllib.error.URLError, OSError, TimeoutError):
            raise Unavailable("画面预览时间索引不可用") from None
    if (not index or len(index) > len(urls) * cols * rows
            or any(not isinstance(i, (int, float)) or isinstance(i, bool) or not math.isfinite(i) or i < 0 for i in index)
            or any(a > b for a, b in zip(index, index[1:]))):
        raise Unavailable("平台未提供可用的画面预览")
    count = min(FRAME_COUNT, len(index))
    positions = [round(i * (len(index) - 1) / (count - 1)) for i in range(count)] if count > 1 else [0]
    sheets = {}
    output = Image.new("RGB", (width * 2, (height + 24) * ((len(positions) + 1) // 2)), "white")
    draw = ImageDraw.Draw(output)
    timestamps = []
    for n, position in enumerate(positions):
        sheet_no, slot = divmod(position, cols * rows)
        if sheet_no not in sheets:
            try:
                with opener(_url(urls[sheet_no])) as response:
                    raw = response.read(MAX_SHEET_BYTES + 1)
                if len(raw) > MAX_SHEET_BYTES:
                    raise Unavailable("画面预览过大")
                img = Image.open(io.BytesIO(raw))
                if img.width != cols * width or img.height != rows * height or img.width * img.height > MAX_PIXELS:
                    raise Unavailable("画面预览尺寸不符")
                img.load()
                sheets[sheet_no] = img.convert("RGB")
            except Unavailable:
                raise
            except urllib.error.HTTPError as error:
                raise Unavailable("画面预览无法读取", restricted=error.code in (403, 412, 429)) from None
            except Exception:
                raise Unavailable("画面预览无法读取") from None
        x, y = (slot % cols) * width, (slot // cols) * height
        out_x, out_y = (n % 2) * width, (n // 2) * (height + 24)
        output.paste(sheets[sheet_no].crop((x, y, x + width, y + height)), (out_x, out_y))
        second = int(index[position])
        timestamps.append(second)
        draw.text((out_x + 4, out_y + height + 3), "%02d:%02d" % divmod(second, 60), fill="black")
    stream = io.BytesIO()
    output.save(stream, format="PNG")
    return {"image": stream.getvalue(), "timestamps_seconds": timestamps,
            "coverage": "这些是视频预览中的抽样静帧，不是连续画面；可能错过动作和细节。"}
