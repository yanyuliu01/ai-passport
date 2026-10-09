"""固件仓库：这台电脑上留着设备固件的每一版，随时可以指定其中一版推给设备。

设备经蓝牙换固件（见固件的 main/pocket_update_core.h）。镜像不是从这里直接发给设备的：
这里只负责“有哪些版本、现在该是哪一版”，手机 App 取走镜像，再经蓝牙写进设备。

  <state_dir>/firmware/index.json     版本清单、要推给设备的那一版、设备上次报的情况、经过
  <state_dir>/firmware/images/<编号>.bin   每一版的应用镜像，原样留着，不自动清理

一版固件的编号是它的应用镜像 SHA-256 的前 12 位；另外按入库的先后编一个序号，说
“第 7 版”比说十六进制顺口。设备自己报的是 build（固件 ELF 的 SHA-256 前 16 位），
两者都记在清单里，靠 build 认出设备现在跑的是哪一版。

清单每次用的时候都从磁盘读：命令行（入库、指定版本）和正在运行的服务是两个进程，
这样它们看到的是同一份。只用标准库，兼容 Python 3.9。
"""

import hashlib
import json
import os
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# 写在固件里的记号：有它才说明这一版装上之后还能经蓝牙再换下一版。
UPDATE_MARKER = b"pocket-fw-update/1"
ESP_IMAGE_MAGIC = 0xE9
ESP_IMAGE_HEADER_BYTES = 24
APP_DESCRIPTION_OFFSET = 32
APP_DESCRIPTION_MAGIC = 0xABCD5432
CHIP_ESP32C3 = 5
PARTITION_TABLE_OFFSET = 0x8000
PARTITION_TABLE_BYTES = 0xC00
PARTITION_ENTRY = struct.Struct("<2sBBII16sI")
PARTITION_MAGIC = b"\xaa\x50"
MAX_FILE_BYTES = 8 * 1024 * 1024
ID_HEX = 12
BUILD_HEX = 16
# 同一版连着这么多次没装成，就不再自动重推，等人来看。
MAX_ATTEMPTS = 2
LOG_LIMIT = 200
LOCK_STALE_SECONDS = 10.0
DEVICE_EVENTS = ("connected", "progress", "installed", "failed", "unsupported")
GITHUB_API = "https://api.github.com"
POLL_SECONDS = 20.0


class FirmwareError(Exception):
    """这件事没做成；消息可以直接给人看。"""


@dataclass(frozen=True)
class Image:
    """一份应用镜像和从它里面读出来的说明。"""

    data: bytes
    sha256: str
    build: str
    version: str
    project: str
    compiled: str
    idf: str
    # 装上之后还能不能经蓝牙再换下一版
    updatable: bool

    @property
    def id(self) -> str:
        return self.sha256[:ID_HEX]


def _text(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()


def _image_length(blob: bytes, start: int) -> int:
    """从 start 开始的那份 ESP 镜像有多长；顺带核对校验和与末尾的 SHA-256。"""
    header = blob[start:start + ESP_IMAGE_HEADER_BYTES]
    if len(header) < ESP_IMAGE_HEADER_BYTES or header[0] != ESP_IMAGE_MAGIC:
        raise FirmwareError("这不是 ESP 的固件镜像")
    segments = header[1]
    if not 1 <= segments <= 16:
        raise FirmwareError("镜像头里的段数不对（%d）" % segments)
    position = start + ESP_IMAGE_HEADER_BYTES
    checksum = 0xEF
    for _ in range(segments):
        if position + 8 > len(blob):
            raise FirmwareError("镜像不完整：段头没读全")
        length = struct.unpack_from("<I", blob, position + 4)[0]
        position += 8
        if length > len(blob) - position:
            raise FirmwareError("镜像不完整：缺 %d 字节" % (length - (len(blob) - position)))
        # 所有段的数据按字节异或：先 8 字节一组算，剩下的零头再逐个算。
        whole = length - length % 8
        folded = 0
        for (word,) in struct.iter_unpack("<Q", blob[position:position + whole]):
            folded ^= word
        for shift in range(0, 64, 8):
            checksum ^= (folded >> shift) & 0xFF
        for byte in blob[position + whole:position + length]:
            checksum ^= byte
        position += length
    # 校验和是补齐到 16 字节后的最后一个字节。
    end = start + ((position - start + 16) & ~15)
    if end > len(blob):
        raise FirmwareError("镜像不完整：缺校验和")
    if blob[end - 1] != checksum:
        raise FirmwareError("镜像的校验和对不上，文件可能坏了")
    if header[23] == 1:
        digest = blob[end:end + 32]
        if len(digest) != 32 or digest != hashlib.sha256(blob[start:end]).digest():
            raise FirmwareError("镜像末尾的 SHA-256 对不上，文件可能坏了")
        end += 32
    return end - start


def _is_app(blob: bytes, start: int = 0) -> bool:
    offset = start + APP_DESCRIPTION_OFFSET
    return (len(blob) >= offset + 4
            and struct.unpack_from("<I", blob, offset)[0] == APP_DESCRIPTION_MAGIC)


def _app_offset_in_merged(blob: bytes) -> int:
    """合并镜像（从 0x0 写入的那种）里应用镜像的位置：读它自带的分区表。"""
    table = blob[PARTITION_TABLE_OFFSET:PARTITION_TABLE_OFFSET + PARTITION_TABLE_BYTES]
    apps: List[Tuple[int, int]] = []
    for cursor in range(0, len(table) - PARTITION_ENTRY.size + 1, PARTITION_ENTRY.size):
        magic, kind, subtype, offset, _size, _label, _flags = PARTITION_ENTRY.unpack_from(
            table, cursor)
        if magic != PARTITION_MAGIC:
            break
        if kind == 0:
            apps.append((subtype, offset))
    if not apps:
        raise FirmwareError("这个文件里没有分区表，也不是应用镜像")
    # 先找第一个 OTA 槽位，再找 factory，都没有就用第一个应用分区。
    for wanted in (0x10, 0x00):
        for subtype, offset in apps:
            if subtype == wanted:
                return offset
    return apps[0][1]


def parse_image(blob: bytes) -> Image:
    """认出一个固件文件：应用镜像，或者含引导程序和分区表的合并镜像（取出其中的应用）。"""
    if not blob:
        raise FirmwareError("文件是空的")
    if len(blob) > MAX_FILE_BYTES:
        raise FirmwareError("文件超过 8 MB，不是这台设备的固件")
    start = 0
    if not _is_app(blob):
        start = _app_offset_in_merged(blob)
        if not _is_app(blob, start):
            raise FirmwareError("分区表指的位置（0x%x）上不是应用镜像" % start)
    length = _image_length(blob, start)
    app = bytes(blob[start:start + length])
    chip = struct.unpack_from("<H", app, 12)[0]
    if chip != CHIP_ESP32C3:
        raise FirmwareError("这份固件不是给 ESP32-C3 的（芯片编号 %d）" % chip)
    description = app[APP_DESCRIPTION_OFFSET:APP_DESCRIPTION_OFFSET + 176]
    if len(description) < 176:
        raise FirmwareError("镜像太短，读不到版本信息")
    return Image(
        data=app,
        sha256=hashlib.sha256(app).hexdigest(),
        build=description[144:176].hex()[:BUILD_HEX],
        version=_text(description[16:48]),
        project=_text(description[48:80]),
        compiled=("%s %s" % (_text(description[96:112]), _text(description[80:96]))).strip(),
        idf=_text(description[112:144]),
        updatable=UPDATE_MARKER in app,
    )


class FirmwareStore:
    """版本清单和镜像文件。所有改动都是“读清单、改、整份写回”，并且用一个锁文件隔开进程。"""

    def __init__(self, state_dir: Path, clock: Callable[[], float] = time.time):
        self._dir = Path(state_dir) / "firmware"
        self._index = self._dir / "index.json"
        self._images = self._dir / "images"
        self._lock_file = self._dir / "index.lock"
        self._lock = threading.Lock()
        self._clock = clock

    # ---- 清单的读写 ----

    def _read(self) -> Dict[str, Any]:
        try:
            raw = json.loads(self._index.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {}
        except (OSError, json.JSONDecodeError) as error:
            # 清单坏了不能当成“一个版本都没有”：那样下一次写入会把历史盖掉。
            raise FirmwareError("读不了固件清单 %s：%s" % (self._index, error))
        if not isinstance(raw, dict):
            raise FirmwareError("固件清单 %s 的格式不对" % self._index)
        raw.setdefault("rev", 0)
        raw.setdefault("seq", 0)
        raw.setdefault("versions", [])
        raw.setdefault("target", None)
        raw.setdefault("device", None)
        raw.setdefault("log", [])
        return raw

    def _write(self, data: Dict[str, Any]) -> None:
        data["rev"] = int(data.get("rev", 0)) + 1
        del data["log"][:-LOG_LIMIT]
        self._dir.mkdir(parents=True, exist_ok=True)
        temporary = self._index.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        os.replace(str(temporary), str(self._index))

    def _change(self, edit: Callable[[Dict[str, Any]], Any]) -> Any:
        """在进程间的锁里读清单、交给 edit 改、写回；edit 返回 False 表示没改，不用写。"""
        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            deadline = time.monotonic() + LOCK_STALE_SECONDS
            while True:
                try:
                    os.close(os.open(str(self._lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                    break
                except FileExistsError:
                    # 上一个拿锁的进程中途死掉会把锁文件留下：过了时限就当它没了。
                    try:
                        stale = time.time() - self._lock_file.stat().st_mtime > LOCK_STALE_SECONDS
                    except OSError:
                        stale = False
                    if stale or time.monotonic() > deadline:
                        try:
                            self._lock_file.unlink()
                        except OSError:
                            pass
                        continue
                    time.sleep(0.05)
            try:
                data = self._read()
                result = edit(data)
                if result is not False:
                    self._write(data)
                return result
            finally:
                try:
                    self._lock_file.unlink()
                except OSError:
                    pass

    def _note(self, data: Dict[str, Any], event: str, version_id: str, detail: str = "") -> None:
        data["log"].append({"at": self._clock(), "event": event, "id": version_id,
                            "detail": detail})

    # ---- 查 ----

    def rev(self) -> int:
        return int(self._read()["rev"])

    def snapshot(self) -> Dict[str, Any]:
        """整份清单：版本从新到旧，设备那一项里补上它现在跑的是第几版。"""
        data = self._read()
        versions = sorted(data["versions"], key=lambda item: item["seq"], reverse=True)
        device = dict(data["device"]) if isinstance(data["device"], dict) else None
        if device is not None:
            running = self._by_build(data, device.get("build", ""))
            device["id"] = running["id"] if running else ""
            device["seq"] = running["seq"] if running else 0
        return {"rev": data["rev"], "versions": versions, "target": data["target"],
                "device": device, "log": data["log"][-20:]}

    def target(self) -> Dict[str, Any]:
        """现在该推给设备的那一版，写成一层的对象（手机 App 的解析器只认一层）。没有时 id 为空。"""
        data = self._read()
        target = data["target"] if isinstance(data["target"], dict) else {}
        return {
            "rev": data["rev"], "id": target.get("id", ""), "seq": target.get("seq", 0),
            "build": target.get("build", ""), "size": target.get("size", 0),
            "sha256": target.get("sha256", ""), "note": target.get("note", ""),
            "reason": target.get("reason", ""), "attempts": target.get("attempts", 0),
        }

    def wait_for_change(self, rev: int, seconds: float) -> None:
        """清单的版本号还是 rev 就等着，最多 seconds 秒。"""
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            try:
                if self.rev() != rev:
                    return
            except FirmwareError:
                return
            time.sleep(0.5)

    @staticmethod
    def _by_build(data: Dict[str, Any], build: str) -> Optional[Dict[str, Any]]:
        if not build:
            return None
        matches = [item for item in data["versions"] if item.get("build") == build]
        return max(matches, key=lambda item: item["seq"]) if matches else None

    def _resolve(self, data: Dict[str, Any], ref: str) -> Dict[str, Any]:
        """按人说的话找到一版：序号（7 或 #7）、编号的开头、latest、current、previous。"""
        versions = data["versions"]
        if not versions:
            raise FirmwareError("仓库里还没有固件；先用 firmware add 或 firmware fetch 入库")
        ref = str(ref).strip()
        device = data["device"] if isinstance(data["device"], dict) else {}
        if ref in ("latest", "最新"):
            return max(versions, key=lambda item: item["seq"])
        if ref in ("current", "previous", "当前", "上一版"):
            wanted = device.get("build" if ref in ("current", "当前") else "prev", "")
            found = self._by_build(data, wanted)
            if found is None:
                raise FirmwareError(
                    "不知道设备%s是哪一版：它还没报过，或者那一版不在仓库里"
                    % ("现在跑的" if ref in ("current", "当前") else "另一个槽位里的"))
            return found
        number = ref[1:] if ref.startswith("#") else ref
        if number.isdigit() and len(number) < 6:
            for item in versions:
                if item["seq"] == int(number):
                    return item
            raise FirmwareError("没有第 %s 版" % number)
        matches = [item for item in versions if item["id"].startswith(ref.lower())]
        if len(matches) == 1:
            return matches[0]
        raise FirmwareError("编号 %s %s" % (ref, "对上了不止一版，多写几位" if matches else "没有对上任何一版"))

    def find(self, ref: str) -> Dict[str, Any]:
        return dict(self._resolve(self._read(), ref))

    def image(self, version_id: str) -> bytes:
        """一版的应用镜像；读出来再核对一遍，文件被动过就不给。"""
        entry = self.find(version_id)
        try:
            blob = (self._images / (entry["id"] + ".bin")).read_bytes()
        except OSError as error:
            raise FirmwareError("第 %d 版的镜像文件读不了：%s" % (entry["seq"], error))
        if hashlib.sha256(blob).hexdigest() != entry["sha256"]:
            raise FirmwareError("第 %d 版的镜像文件和入库时不一样了，不能用" % entry["seq"])
        return blob

    # ---- 改 ----

    def add(self, blob: bytes, source: str = "", note: str = "") -> Dict[str, Any]:
        """入库一个固件文件；同一份镜像再入库一次只更新备注，不占新的序号。"""
        image = parse_image(blob)
        self._images.mkdir(parents=True, exist_ok=True)
        path = self._images / (image.id + ".bin")
        if not path.exists() or path.read_bytes() != image.data:
            temporary = path.with_suffix(".bin.tmp")
            temporary.write_bytes(image.data)
            os.replace(str(temporary), str(path))

        def edit(data: Dict[str, Any]) -> Dict[str, Any]:
            for item in data["versions"]:
                if item["sha256"] == image.sha256:
                    if note:
                        item["note"] = note
                    return dict(item, existed=True)
            data["seq"] = int(data["seq"]) + 1
            entry = {
                "id": image.id, "seq": data["seq"], "sha256": image.sha256,
                "size": len(image.data), "build": image.build, "version": image.version,
                "project": image.project, "compiled": image.compiled, "idf": image.idf,
                "updatable": image.updatable, "added_at": self._clock(), "source": source,
                "note": note,
            }
            data["versions"].append(entry)
            self._note(data, "added", image.id, source)
            return dict(entry, existed=False)

        return self._change(edit)

    def set_target(self, ref: str, reason: str = "push", force: bool = False) -> Dict[str, Any]:
        """指定要推给设备的那一版。装上后不能再经蓝牙换的版本默认不让推。"""

        def edit(data: Dict[str, Any]) -> Dict[str, Any]:
            entry = self._resolve(data, ref)
            if not entry.get("updatable") and not force:
                raise FirmwareError(
                    "第 %d 版装上之后不能再经蓝牙换固件（它是加这个功能之前的版本），"
                    "换上去就只能插线刷回来。确实要这样做就加 --force" % entry["seq"])
            data["target"] = {
                "id": entry["id"], "seq": entry["seq"], "build": entry["build"],
                "size": entry["size"], "sha256": entry["sha256"], "note": entry.get("note", ""),
                "reason": reason, "requested_at": self._clock(), "attempts": 0,
            }
            self._note(data, "requested", entry["id"], reason)
            return dict(entry)

        return self._change(edit)

    def clear_target(self) -> bool:
        def edit(data: Dict[str, Any]) -> Any:
            if not isinstance(data["target"], dict):
                return False
            self._note(data, "cancelled", data["target"]["id"])
            data["target"] = None
            return True

        return bool(self._change(edit))

    def remove(self, ref: str) -> Dict[str, Any]:
        """从仓库里删掉一版（正要推给设备的那一版不能删）。"""

        def edit(data: Dict[str, Any]) -> Dict[str, Any]:
            entry = self._resolve(data, ref)
            if isinstance(data["target"], dict) and data["target"]["id"] == entry["id"]:
                raise FirmwareError("第 %d 版正等着推给设备；先 firmware cancel" % entry["seq"])
            data["versions"] = [item for item in data["versions"] if item["id"] != entry["id"]]
            self._note(data, "removed", entry["id"])
            return dict(entry)

        entry = self._change(edit)
        try:
            (self._images / (entry["id"] + ".bin")).unlink()
        except OSError:
            pass
        return entry

    def report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """手机 App 转告设备的情况：连上了、传到哪了、装好了、没装成。返回 target()。"""
        if not isinstance(payload, dict):
            raise FirmwareError("请求体应该是一个对象")
        event = payload.get("event", "connected")
        if event not in DEVICE_EVENTS:
            raise FirmwareError("event 只能是 %s 之一" % "、".join(DEVICE_EVENTS))

        def text(key: str, limit: int) -> str:
            value = payload.get(key, "")
            return value[:limit] if isinstance(value, str) else ""

        def number(key: str) -> int:
            value = payload.get(key, 0)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

        def edit(data: Dict[str, Any]) -> None:
            device = data["device"] if isinstance(data["device"], dict) else {}
            target = data["target"] if isinstance(data["target"], dict) else None
            device["seen_at"] = self._clock()
            device["event"] = event
            for key, limit in (("name", 40), ("build", BUILD_HEX), ("prev", BUILD_HEX),
                               ("ver", 32), ("slot", 16), ("state", 16)):
                if key in payload:
                    device[key] = text(key, limit)
            if "max" in payload:
                device["max"] = number("max")
            device["detail"] = text("detail", 200)
            if event == "progress":
                device["progress"] = {"id": text("id", ID_HEX), "sent": number("sent"),
                                      "size": number("size")}
            else:
                device.pop("progress", None)
            data["device"] = device
            if target is None:
                return
            arrived = device.get("build") == target["build"] and device.get("state") == "valid"
            if event == "installed" or (event == "connected" and arrived):
                if arrived:
                    self._note(data, "installed", target["id"], target.get("reason", ""))
                    data["target"] = None
            elif event == "failed" and text("id", ID_HEX) in ("", target["id"]):
                target["attempts"] = int(target.get("attempts", 0)) + 1
                self._note(data, "failed", target["id"], device["detail"])
                if target["attempts"] >= MAX_ATTEMPTS:
                    # 不再自动重推：反复换不上去多半是这一版本身有问题。
                    self._note(data, "gave_up", target["id"],
                               "连着 %d 次没装成" % target["attempts"])
                    data["target"] = None
            elif event == "unsupported":
                self._note(data, "gave_up", target["id"], "设备上的固件不能经蓝牙换")
                data["target"] = None

        self._change(edit)
        return self.target()


# ---- 从 GitHub 取构建好的固件 ----

def _github(url: str, opener: Callable[..., Any], token: Optional[str], accept: str,
            timeout: float) -> bytes:
    request = urllib.request.Request(url)
    request.add_header("Accept", accept)
    request.add_header("User-Agent", "xiaoyou-runtime")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with opener(request, timeout=timeout) as response:
            return response.read(MAX_FILE_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code in (403, 429):
            raise FirmwareError("GitHub 暂时不让查了（%d，多半是没登录时的次数限制）；"
                                "设置环境变量 GITHUB_TOKEN 可以放宽" % error.code)
        raise FirmwareError("GitHub 返回 %d：%s" % (error.code, url))
    except (urllib.error.URLError, OSError) as error:
        raise FirmwareError("连不上 GitHub：%s" % error)


def find_release(repo: str, asset: str, tag_prefix: str, tag: Optional[str] = None,
                 commit: Optional[str] = None, opener: Callable[..., Any] = urllib.request.urlopen,
                 token: Optional[str] = None) -> Optional[Dict[str, str]]:
    """在仓库的发布里找一个带固件的：指定标签、指定提交，或者最新的一个。找不到返回 None。"""
    raw = _github("%s/repos/%s/releases?per_page=30" % (GITHUB_API, repo), opener, token,
                  "application/vnd.github+json", 30)
    try:
        releases = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise FirmwareError("GitHub 返回的内容看不懂")
    if not isinstance(releases, list):
        raise FirmwareError("GitHub 没有返回发布列表（仓库 %s 存在吗？）" % repo)
    for release in releases:
        if not isinstance(release, dict):
            continue
        name = str(release.get("tag_name", ""))
        if tag is not None:
            if name != tag:
                continue
        elif not name.startswith(tag_prefix):
            continue
        built_from = str(release.get("target_commitish", ""))
        if commit is not None and not (built_from and built_from.startswith(commit)):
            continue
        urls = {item.get("name"): item.get("browser_download_url")
                for item in release.get("assets", []) if isinstance(item, dict)}
        if urls.get(asset):
            return {"tag": name, "commit": built_from, "url": urls[asset],
                    "sha256_url": urls.get(asset + ".sha256") or ""}
    return None


def fetch_release(repo: str, asset: str, tag_prefix: str, tag: Optional[str] = None,
                  commit: Optional[str] = None, wait_seconds: float = 0,
                  opener: Callable[..., Any] = urllib.request.urlopen,
                  token: Optional[str] = None, sleep: Callable[[float], None] = time.sleep,
                  say: Callable[[str], None] = lambda message: None) -> Tuple[bytes, Dict[str, str]]:
    """取回一个构建好的固件。wait_seconds 大于 0 时，还没构建出来就隔一会儿再看，直到超时。"""
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        found = find_release(repo, asset, tag_prefix, tag, commit, opener, token)
        if found is not None:
            break
        if time.monotonic() >= deadline:
            what = "标签 %s" % tag if tag else ("提交 %s 的构建" % commit[:10] if commit else "固件")
            raise FirmwareError("%s 里没找到%s%s" % (
                repo, what, "（等了 %d 秒）" % wait_seconds if wait_seconds else ""))
        say("还没构建出来，过一会儿再看……")
        sleep(min(POLL_SECONDS, max(1.0, deadline - time.monotonic())))
    say("下载 %s ……" % found["tag"])
    blob = _github(found["url"], opener, token, "application/octet-stream", 120)
    if len(blob) > MAX_FILE_BYTES:
        raise FirmwareError("下载到的文件超过 8 MB，不像是这台设备的固件")
    if found["sha256_url"]:
        expected = _github(found["sha256_url"], opener, token, "application/octet-stream",
                           30).decode("ascii", "replace").split()
        if not expected or hashlib.sha256(blob).hexdigest() != expected[0].lower():
            raise FirmwareError("下载到的 %s 和发布里写的 SHA-256 对不上" % found["tag"])
    return blob, found


def build_locally(command: List[str], source_dir: Path, output: Path, timeout: int = 1800,
                  run: Callable[..., Any] = subprocess.run) -> bytes:
    """在这台电脑上构建固件（配置里的 firmware.build_command），返回构建出的文件。"""
    if not command:
        raise FirmwareError("配置里没有 firmware.build_command：这台电脑不会自己构建固件，"
                            "用 firmware fetch 取 GitHub 上构建好的")
    try:
        done = run(command, cwd=str(source_dir), stdout=subprocess.PIPE,
                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                   timeout=timeout)
    except FileNotFoundError:
        raise FirmwareError("找不到构建命令 %s" % command[0])
    except subprocess.TimeoutExpired:
        raise FirmwareError("构建超过 %d 秒还没结束，已经停止" % timeout)
    except OSError as error:
        raise FirmwareError("构建没启动起来：%s" % error)
    if done.returncode != 0:
        tail = "\n".join((done.stdout or "").strip().splitlines()[-15:])
        raise FirmwareError("构建失败（退出码 %d）：\n%s" % (done.returncode, tail))
    try:
        return output.read_bytes()
    except OSError as error:
        raise FirmwareError("构建完了，但读不到 %s：%s" % (output, error))


def head_commit(source_dir: Path, run: Callable[..., Any] = subprocess.run) -> str:
    """源码目录现在所在的提交。"""
    try:
        done = run(["git", "-C", str(source_dir), "rev-parse", "HEAD"], stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise FirmwareError("问不到 %s 现在在哪个提交：%s" % (source_dir, error))
    commit = (done.stdout or "").strip()
    if done.returncode != 0 or len(commit) < 7:
        raise FirmwareError("%s 不是一个 git 仓库" % source_dir)
    return commit
