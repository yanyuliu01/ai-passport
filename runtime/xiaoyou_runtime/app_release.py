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
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional, Tuple

MAX_APK_BYTES = 128 * 1024 * 1024


class AppReleaseError(Exception):
    pass


def _metadata_beside(apk: Path) -> Dict[str, Any]:
    """Gradle 在 apk 旁边写的 output-metadata.json 里的版本号；没有就是空的。"""
    try:
        raw = json.loads((apk.parent / "output-metadata.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    for element in raw.get("elements") or []:
        if isinstance(element, dict) and element.get("outputFile") == apk.name:
            return {"version_code": element.get("versionCode"), "version_name": element.get("versionName")}
    return {}


class AppRelease:
    def __init__(self, state_dir: Any):
        self._dir = Path(state_dir).resolve() / "app-release"
        self._apk = self._dir / "app.apk"
        self._meta = self._dir / "release.json"
        self._lock = threading.Lock()

    def info(self) -> Dict[str, Any]:
        """发布的那一版；没有时 available 为 False。"""
        try:
            meta = json.loads(self._meta.read_text("utf-8"))
        except (OSError, ValueError):
            meta = None
        if not isinstance(meta, dict) or not self._apk.is_file():
            return {"available": False}
        return dict(meta, available=True)

    def publish(self, source: Path, note: str = "", version_code: Optional[int] = None,
                version_name: Optional[str] = None) -> Dict[str, Any]:
        source = Path(source)
        try:
            size = source.stat().st_size
        except OSError as error:
            raise AppReleaseError("读不到安装包：%s" % error)
        if size <= 0 or size > MAX_APK_BYTES:
            raise AppReleaseError("安装包是空的或者超过 128 MB")
        found = _metadata_beside(source)
        code = version_code if version_code is not None else found.get("version_code")
        name = version_name if version_name is not None else found.get("version_name")
        if not isinstance(code, int) or isinstance(code, bool) or code <= 0:
            raise AppReleaseError("不知道这个安装包的版本号：旁边没有 output-metadata.json，请用 --version-code 指定")
        digest = hashlib.sha256()
        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            handle, temp = tempfile.mkstemp(prefix="app-", suffix=".part", dir=str(self._dir))
            try:
                with os.fdopen(handle, "wb") as output, source.open("rb") as stream:
                    head = stream.read(4)
                    if head != b"PK\x03\x04":
                        raise AppReleaseError("这不是安卓安装包（apk）")
                    output.write(head)
                    digest.update(head)
                    while True:
                        chunk = stream.read(1 << 20)
                        if not chunk:
                            break
                        output.write(chunk)
                        digest.update(chunk)
                os.replace(temp, self._apk)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            meta = {
                "version_code": code, "version_name": str(name or ""), "sha256": digest.hexdigest(),
                "size": size, "published_at": round(time.time(), 3), "note": note[:200],
            }
            text = json.dumps(meta, ensure_ascii=False, indent=1)
            handle, temp = tempfile.mkstemp(prefix="release-", suffix=".part", dir=str(self._dir))
            with os.fdopen(handle, "w", encoding="utf-8") as output:
                output.write(text)
            os.replace(temp, self._meta)
        return dict(meta, available=True)

    def open(self) -> Tuple[BinaryIO, Dict[str, Any], int]:
        info = self.info()
        if not info.get("available"):
            raise AppReleaseError("这台电脑上还没有发布 App 更新")
        stream = self._apk.open("rb")
        return stream, info, os.fstat(stream.fileno()).st_size


def run(config: Any, args: Any) -> int:
    release = AppRelease(config.state_dir)
    if args.action == "publish":
        try:
            info = release.publish(Path(args.file), args.note, args.version_code, args.version_name)
        except AppReleaseError as error:
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
    actions.add_parser("status", help="现在发布的是哪一版")
