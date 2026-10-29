"""手机 App 的更新包：这台电脑上只留最新发布的一版，手机经 Runtime 取走后自己装。

  python3 -m xiaoyu_runtime app publish 文件.apk [--note 一句话] [--github [ABI,...]]   发布一版
  python3 -m xiaoyu_runtime app pack speech 目录 [--github]                            发布资源包
  python3 -m xiaoyu_runtime app status                                                现在发布的是哪一版

更新是增量的：安装包只放代码，语音模型这类很少变的大文件放在资源包里。资源包里每个文件
按 SHA-256 存放和提供，手机比对自己装好的，只下载变了的文件。--github 把安装包或资源文件
传到 GitHub Releases，网址写进清单，手机可以选“从网上下载”（同一个文件，同一个校验值）。

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
from typing import Any, BinaryIO, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

MAX_APK_BYTES = 512 * 1024 * 1024
MAX_PACK_FILE_BYTES = 1024 * 1024 * 1024
ABIS = ("arm64-v8a", "armeabi-v7a", "x86", "x86_64", "universal")
PACK_NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
PACK_PATH = re.compile(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*")


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
        self._packs_dir = self._dir / "packs"
        self._packs = self._dir / "packs.json"
        self._lock = threading.Lock()

    # ---- 资源包：按文件校验值增量更新 ----

    def packs(self) -> Dict[str, Any]:
        """现在发布的资源包：{"packs": [{"name", "published_at", "files": [{"path", "sha256", "size", "download_url"}]}]}。"""
        try:
            raw = json.loads(self._packs.read_text("utf-8"))
        except (OSError, ValueError):
            raw = {}
        packs = raw.get("packs") if isinstance(raw, dict) else None
        return {"packs": [dict(pack) for pack in packs] if isinstance(packs, list) else []}

    @staticmethod
    def pack_files(folder: Path) -> List[Tuple[str, Path]]:
        """目录里要进资源包的文件（相对路径, 文件），按路径排好；隐藏文件不算。"""
        folder = Path(folder)
        if not folder.is_dir():
            raise AppReleaseError("资源目录不存在：%s" % folder)
        found = []
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(folder).as_posix()
            if path.is_file() and not any(part.startswith(".") for part in relative.split("/")):
                if not PACK_PATH.fullmatch(relative):
                    raise AppReleaseError("资源文件名只能用字母、数字和 . _ -：%s" % relative)
                found.append((relative, path))
        if not found:
            raise AppReleaseError("资源目录是空的：%s" % folder)
        return found

    def publish_pack(self, name: str, folder: Path,
                     download_urls: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """发布（或更新）一个资源包。没变的文件不重复存；download_urls 按 SHA-256 给网上的地址。"""
        if not PACK_NAME.fullmatch(name or ""):
            raise AppReleaseError("资源包名只能用小写字母、数字和 -，以字母开头")
        urls = {sha: _download_url(url) for sha, url in (download_urls or {}).items()}
        files = []
        with self._lock:
            self._packs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            for relative, path in self.pack_files(folder):
                stored = self._store_pack_file(path)
                files.append(dict(path=relative, download_url=urls.get(stored["sha256"], ""), **stored))
            current = [pack for pack in self.packs()["packs"] if pack.get("name") != name]
            pack = {"name": name, "published_at": round(time.time(), 3), "files": files}
            current.append(pack)
            current.sort(key=lambda item: item["name"])
            self._write_json(self._packs, {"packs": current})
            # 只留现在哪个包都还用得上的文件。
            keep = {item["sha256"] for entry in current for item in entry.get("files", [])}
            for cached in self._packs_dir.glob("*.bin"):
                if re.fullmatch(r"[0-9a-f]{64}\.bin", cached.name) and cached.stem not in keep:
                    try:
                        cached.unlink()
                    except OSError:
                        pass
        return pack

    def _store_pack_file(self, source: Path) -> Dict[str, Any]:
        digest = hashlib.sha256()
        total = 0
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                total += len(chunk)
                digest.update(chunk)
        if total <= 0 or total > MAX_PACK_FILE_BYTES:
            raise AppReleaseError("资源文件是空的或者超过 1 GB：%s" % source)
        sha = digest.hexdigest()
        target = self._packs_dir / (sha + ".bin")
        if not target.is_file() or target.stat().st_size != total:
            handle, temp = tempfile.mkstemp(prefix="pack-", suffix=".part", dir=str(self._packs_dir))
            try:
                with os.fdopen(handle, "wb") as output, source.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1 << 20), b""):
                        output.write(chunk)
                os.replace(temp, target)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        return {"sha256": sha, "size": total}

    def open_pack_file(self, sha256: str) -> Tuple[BinaryIO, int]:
        """资源包里的一个文件；只给现在发布的包里有的。"""
        if not re.fullmatch(r"[0-9a-f]{64}", sha256 or ""):
            raise AppReleaseError("缺少文件校验值")
        listed = any(item.get("sha256") == sha256
                     for pack in self.packs()["packs"] for item in pack.get("files", []))
        path = self._packs_dir / (sha256 + ".bin")
        if not listed or not path.is_file():
            raise AppReleaseError("资源文件已更新，请重新检查更新")
        stream = path.open("rb")
        return stream, os.fstat(stream.fileno()).st_size

    def _write_json(self, target: Path, value: Any) -> None:
        handle, temp = tempfile.mkstemp(prefix="meta-", suffix=".part", dir=str(self._dir))
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            output.write(json.dumps(value, ensure_ascii=False, indent=1))
        os.replace(temp, target)

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


def _github(args: Any) -> Any:
    from . import github_release
    repo = args.github_repo or github_release.repo_from_git(Path(__file__).resolve().parents[2])
    return github_release.Releases(repo, github_release.token())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(config: Any, args: Any) -> int:
    from .github_release import GitHubError
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
            download_url = args.download_url
            if args.github:
                # 先传到 GitHub、拿到地址，再在本机发布：手机看到的清单里网址一定已经能下。
                found = _metadata_beside(Path(args.file))
                code = args.version_code if args.version_code is not None else found.get("version_code")
                name = args.version_name if args.version_name is not None else found.get("version_name")
                packages = dict(variants)
                packages.setdefault(found.get("abi", "universal"), Path(args.file))
                if not isinstance(code, int) or isinstance(code, bool) or code <= 0:
                    raise AppReleaseError("不知道安装包版本号，请提供 Gradle 元数据或 --version-code")
                requested = list(packages) if args.github == "all" else [
                    item.strip() for item in args.github.split(",") if item.strip()]
                for abi, package in packages.items():
                    if abi not in ABIS or not 0 < package.stat().st_size <= MAX_APK_BYTES:
                        raise AppReleaseError("安装包架构或大小不正确：%s" % abi)
                    metadata = _metadata_beside(package)
                    if abi in variants and (metadata.get("version_code") != code
                            or str(metadata.get("version_name") or "") != str(name or "")
                            or metadata.get("abi") != abi):
                        raise AppReleaseError("各架构安装包的版本、架构必须与 Gradle 元数据一致")
                    with package.open("rb") as stream:
                        if stream.read(4) != b"PK\x03\x04":
                            raise AppReleaseError("这不是安卓安装包（apk）")
                if set(requested) - packages.keys():
                    raise AppReleaseError("--github 指定的架构没有对应的安装包")
                github = _github(args)
                tag = "app-%s-%s" % (name or "build", code)
                published = github.release(tag, "Pocket Hub %s" % (name or code),
                                           "Pocket Hub %s（版本号 %s）。%s" % (name or "-", code, args.note[:200]))
                for abi in requested:
                    # A repeated version can contain different bytes. Never reuse
                    # a mutable, same-size asset with an old download's checksum.
                    url = github.upload(published, packages[abi], "pocket-hub-%s-%s-%s.apk" % (
                        name or code, abi, _sha256(packages[abi])[:16]))
                    if packages[abi] == Path(args.file) and abi == found.get("abi", "universal"):
                        download_url = url
                    else:
                        variant_urls[abi] = url
            info = release.publish(Path(args.file), args.note, args.version_code, args.version_name,
                                   variants, download_url, variant_urls)
        except (AppReleaseError, GitHubError, OSError) as error:
            print("没发布成：%s" % error)
            return 1
        print("发布了 App %s（版本号 %d，%.2f MB）。手机连着这台电脑时会提示更新。" % (
            info["version_name"] or "-", info["version_code"], info["size"] / 1048576.0))
        return 0
    if args.action == "pack":
        try:
            urls = {}
            if args.github:
                # 资源文件按校验值起名，放在同一个发布里：没变的文件早就在那儿，不再上传。
                github = _github(args)
                published = github.release("app-packs", "Pocket Hub 资源包",
                                           "Pocket Hub 的资源文件（语音模型等），按 SHA-256 命名，手机只下载变了的。")
                for relative, path in AppRelease.pack_files(Path(args.folder)):
                    sha = _sha256(path)
                    urls[sha] = github.upload(published, path, "%s-%s-%s" % (
                        args.name, sha[:16], relative.replace("/", "-")))
            pack = release.publish_pack(args.name, Path(args.folder), urls)
        except (AppReleaseError, GitHubError, OSError) as error:
            print("没发布成：%s" % error)
            return 1
        print("发布了资源包 %s：%d 个文件，共 %.2f MB%s。手机只会下载变了的文件。" % (
            pack["name"], len(pack["files"]), sum(item["size"] for item in pack["files"]) / 1048576.0,
            "，也传到了 GitHub" if args.github else ""))
        return 0
    info = release.info()
    for pack in release.packs()["packs"]:
        print("资源包 %s：%d 个文件，共 %.2f MB，%s" % (
            pack["name"], len(pack.get("files", [])),
            sum(item.get("size", 0) for item in pack.get("files", [])) / 1048576.0,
            "可以从网上下载" if all(item.get("download_url") for item in pack.get("files", []))
            else "从这台电脑下载"))
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
    hosting = publish.add_mutually_exclusive_group()
    hosting.add_argument("--github", nargs="?", const="all", default="all", metavar="ABI[,ABI]",
                         help="默认把所有提供的安装包传到 GitHub Releases；可限定架构")
    hosting.add_argument("--local-only", dest="github", action="store_const", const="",
                         help="只在本机发布，不上传 GitHub（也用于已有自选下载网址）")
    publish.add_argument("--github-repo", default="", metavar="OWNER/NAME",
                         help="GitHub 仓库；不写就用仓库的 github（或 origin）远端")
    pack = actions.add_parser("pack", help="发布一个资源包（比如语音模型），手机只下载变了的文件")
    pack.add_argument("name", help="资源包名，比如 speech")
    pack.add_argument("folder", help="资源文件所在的目录")
    hosting = pack.add_mutually_exclusive_group()
    hosting.add_argument("--github", action="store_true", default=True, help="把新文件传到 GitHub Releases（默认）")
    hosting.add_argument("--local-only", dest="github", action="store_false", help="只在本机发布资源包")
    pack.add_argument("--github-repo", default="", metavar="OWNER/NAME")
    actions.add_parser("status", help="现在发布的是哪一版")
