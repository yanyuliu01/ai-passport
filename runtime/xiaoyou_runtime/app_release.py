"""手机 App 的更新包：这台电脑上只留最新发布的一版，手机经 Runtime 取走后自己装。

  python3 -m xiaoyou_runtime app publish 文件.apk [--note 一句话]   发布一版
  python3 -m xiaoyou_runtime app status                           现在发布的是哪一版

文件旁边有 Gradle 写的 output-metadata.json 时，版本号从那里读；也可以用 --version-code、
--version-name 指定。手机看到版本号更大（或同一版本号但安装包不同、发布得比它装得晚）时提示更新，
真正安装要主人在手机上点系统的确认。
"""

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional, Tuple
from urllib.parse import urlsplit

MAX_APK_BYTES = 512 * 1024 * 1024
ABIS = ("arm64-v8a", "armeabi-v7a", "x86", "x86_64", "universal")


class AppReleaseError(Exception):
    pass


def _download_url(value: str) -> str:
    """Only public HTTPS URLs, without embedded credentials or fragments."""
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if (len(value) > 4096 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
                or parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment or "#" in value
                or parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError("unsafe URL")
    except ValueError as error:
        raise AppReleaseError("下载地址必须是无用户名、密码或片段的 HTTPS 网址") from error
    return value


def byte_range(value: str, size: int) -> Tuple[int, int]:
    """A single HTTP byte range; reject malformed/multiple/unsatisfiable ranges."""
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value) if len(value) <= 128 else None
    if not match or not any(match.groups()) or size <= 0:
        raise ValueError("invalid byte range")
    left, right = match.groups()
    if not left:
        suffix = int(right)
        if suffix <= 0:
            raise ValueError("invalid suffix")
        return max(0, size - suffix), size - 1
    start = int(left)
    end = min(int(right), size - 1) if right else size - 1
    if start >= size or end < start:
        raise ValueError("unsatisfiable byte range")
    return start, end


def _metadata_beside(apk: Path) -> Dict[str, Any]:
    """Gradle 在 apk 旁边写的 output-metadata.json 里的版本号；没有就是空的。"""
    try:
        raw = json.loads((apk.parent / "output-metadata.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    for element in raw.get("elements") or []:
        if isinstance(element, dict) and element.get("outputFile") == apk.name:
            abi = next((item.get("value") for item in element.get("filters", [])
                        if item.get("filterType") == "ABI"), "universal")
            return {"version_code": element.get("versionCode"), "version_name": element.get("versionName"),
                    "abi": abi}
    return {}


class AppRelease:
    def __init__(self, state_dir: Any):
        self._dir = Path(state_dir).resolve() / "app-release"
        self._apk = self._dir / "app.apk"
        self._meta = self._dir / "release.json"
        self._lock = threading.Lock()

    def info(self, abi: str = "") -> Dict[str, Any]:
        """发布的那一版；没有时 available 为 False。"""
        try:
            meta = json.loads(self._meta.read_text("utf-8"))
        except (OSError, ValueError):
            meta = None
        if not isinstance(meta, dict) or not self._apk.is_file():
            return {"available": False}
        if abi and abi != meta.get("abi", "universal"):
            variants = meta.get("variants", {})
            selected = variants.get(abi) or variants.get("universal")
            if selected:
                # A variant without its own mirror must not inherit the default
                # package's URL: its bytes and checksum may be different.
                return {**meta, "download_url": "", **selected, "available": True}
            if meta.get("abi", "universal") != "universal":
                return {"available": False}
        return dict(meta, available=True)

    def publish(self, source: Path, note: str = "", version_code: Optional[int] = None,
                version_name: Optional[str] = None,
                variants: Optional[Dict[str, Path]] = None, download_url: str = "",
                variant_download_urls: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        download_url = _download_url(download_url)
        variant_urls = {abi: _download_url(url) for abi, url in (variant_download_urls or {}).items()}
        if variant_urls.keys() - (variants or {}).keys():
            raise AppReleaseError("各架构下载地址必须对应 --variant 提供的安装包")
        source = Path(source)
        try:
            size = source.stat().st_size
        except OSError as error:
            raise AppReleaseError("读不到安装包：%s" % error)
        if size <= 0 or size > MAX_APK_BYTES:
            raise AppReleaseError("安装包是空的或者超过 512 MB")
        found = _metadata_beside(source)
        code = version_code if version_code is not None else found.get("version_code")
        name = version_name if version_name is not None else found.get("version_name")
        if not isinstance(code, int) or isinstance(code, bool) or code <= 0:
            raise AppReleaseError("不知道这个安装包的版本号：旁边没有 output-metadata.json，请用 --version-code 指定")
        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            previous = self.info()
            built = {}
            for abi, apk in (variants or {}).items():
                if abi not in ABIS:
                    raise AppReleaseError("未知的处理器架构：%s" % abi)
                metadata = _metadata_beside(Path(apk))
                if (metadata.get("version_code") != code
                        or str(metadata.get("version_name") or "") != str(name or "")
                        or metadata.get("abi") != abi):
                    raise AppReleaseError("各架构安装包的版本、架构必须与 Gradle 元数据一致")
                built[abi] = dict(self._copy(Path(apk)), abi=abi, download_url=variant_urls.get(abi, ""))
            default = dict(self._copy(source), abi=found.get("abi", "universal"), download_url=download_url)
            # Keep the legacy filename for a running older Runtime. New readers open
            # immutable, hash-named files so a publish never mixes metadata and bytes.
            handle, temp = tempfile.mkstemp(prefix="app-", suffix=".part", dir=str(self._dir))
            os.close(handle)
            os.unlink(temp)
            try:
                os.link(self._dir / (default["sha256"] + ".apk"), temp)
                os.replace(temp, self._apk)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            meta = {
                "version_code": code, "version_name": str(name or ""), **default,
                "published_at": round(time.time(), 3), "note": note[:200], "variants": built,
            }
            text = json.dumps(meta, ensure_ascii=False, indent=1)
            handle, temp = tempfile.mkstemp(prefix="release-", suffix=".part", dir=str(self._dir))
            with os.fdopen(handle, "w", encoding="utf-8") as output:
                output.write(text)
            os.replace(temp, self._meta)
            # Bound the cache to the current and previous publication. An already
            # open stream remains valid even when an older pathname is removed.
            keep = {item.get("sha256") for manifest in (meta, previous)
                    for item in [manifest, *manifest.get("variants", {}).values()]}
            for cached in self._dir.glob("*.apk"):
                if re.fullmatch(r"[0-9a-f]{64}\.apk", cached.name) and cached.stem not in keep:
                    try:
                        cached.unlink()
                    except OSError:
                        pass
        return dict(meta, available=True)

    def _copy(self, source: Path) -> Dict[str, Any]:
        digest = hashlib.sha256()
        handle, temp = tempfile.mkstemp(prefix="app-", suffix=".part", dir=str(self._dir))
        try:
            total = 0
            with os.fdopen(handle, "wb") as output, source.open("rb") as stream:
                if stream.read(4) != b"PK\x03\x04":
                    raise AppReleaseError("这不是安卓安装包（apk）")
                stream.seek(0)
                while True:
                    chunk = stream.read(1 << 20)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_APK_BYTES:
                        raise AppReleaseError("安装包超过 512 MB")
                    output.write(chunk)
                    digest.update(chunk)
            sha = digest.hexdigest()
            os.replace(temp, self._dir / (sha + ".apk"))
            return {"sha256": sha, "size": total}
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def open(self, abi: str = "", sha256: str = "") -> Tuple[BinaryIO, Dict[str, Any], int]:
        info = self.info(abi)
        if not info.get("available"):
            raise AppReleaseError("这台电脑上还没有发布 App 更新")
        if sha256 and sha256 != info["sha256"]:
            raise AppReleaseError("安装包已更新，请重新检查更新")
        immutable = self._dir / (info["sha256"] + ".apk")
        stream = (immutable if immutable.is_file() else self._apk).open("rb")
        return stream, info, os.fstat(stream.fileno()).st_size


def run(config: Any, args: Any) -> int:
    release = AppRelease(config.state_dir)
    if args.action == "publish":
        try:
            variants = {}
            for item in args.variant:
                abi, separator, filename = item.partition("=")
                if not separator or not filename or abi in variants:
                    raise AppReleaseError("--variant 应为不重复的 架构=安装包路径")
                variants[abi] = Path(filename)
            variant_urls = {}
            for item in args.variant_download_url:
                abi, separator, url = item.partition("=")
                if not separator or not url or abi in variant_urls:
                    raise AppReleaseError("--variant-download-url 应为不重复的 架构=HTTPS网址")
                variant_urls[abi] = url
            info = release.publish(Path(args.file), args.note, args.version_code, args.version_name,
                                   variants, args.download_url, variant_urls)
        except (AppReleaseError, OSError) as error:
            print("没发布成：%s" % error)
            return 1
        print("发布了 App %s（版本号 %d，%.2f MB）。手机连着这台电脑时会提示更新。" % (
            info["version_name"] or "-", info["version_code"], info["size"] / 1048576.0))
        return 0
    info = release.info()
    if not info.get("available"):
        print("还没有发布 App 更新。")
        return 0
    print("现在发布的是 App %s（版本号 %d，%.2f MB），发布于 %s%s" % (
        info.get("version_name") or "-", info.get("version_code") or 0, (info.get("size") or 0) / 1048576.0,
        time.strftime("%Y-%m-%d %H:%M", time.localtime(info.get("published_at") or 0)),
        "，备注：" + info["note"] if info.get("note") else ""))
    return 0


def add_arguments(subparsers: Any) -> None:
    parser = subparsers.add_parser("app", help="手机 App 的更新包：发布、查看")
    actions = parser.add_subparsers(dest="action", metavar="动作")
    publish = actions.add_parser("publish", help="发布一版安装包，手机会提示更新")
    publish.add_argument("file")
    publish.add_argument("--note", default="", help="给这一版写一句说明，手机上会显示")
    publish.add_argument("--version-code", type=int, help="版本号（整数）；不写就读 apk 旁边的 output-metadata.json")
    publish.add_argument("--version-name", help="显示用的版本名")
    publish.add_argument("--variant", action="append", default=[], metavar="ABI=APK",
                         help="同版其他架构的包，可重复；使用 Gradle 输出的元数据")
    publish.add_argument("--download-url", default="", metavar="HTTPS_URL",
                         help="默认安装包的公开 HTTPS 下载网址；不填则从本机 Runtime 下载")
    publish.add_argument("--variant-download-url", action="append", default=[], metavar="ABI=HTTPS_URL",
                         help="--variant 安装包对应的公开 HTTPS 下载网址，可重复")
    actions.add_parser("status", help="现在发布的是哪一版")
