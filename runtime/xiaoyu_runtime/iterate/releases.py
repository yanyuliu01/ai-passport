"""App 发布的时间线：哪一版什么时候发布的（见 docs/development/auto-iteration 的"外层循环"）。

改版前后的使用行为和平时不一样：新版刚装上会四处点点看看，有新版没装时会反复去设置里看更新。
检测器把这两段时间的行为单独拿出来，不当成"用不明白"的证据；版本方案也会拿到这条时间线。

现有的 app_release 只留最新一版的清单，所以这里从发布目录里每个安装包的落盘时间重建历史，
再用 release.json 校准最新那一版。只读，不改发布目录。
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# 新版发布后这么久算"上手期"
SETTLING_SECONDS = 3 * 3600
# 落盘时间相差这么近的安装包算同一次发布（一次发布有几个架构的包）
SAME_RELEASE_SECONDS = 600


def timeline(state_dir: Path) -> List[Dict[str, Any]]:
    """[{"at": 发布时间, "version": 版本名或空}]，按时间排序。"""
    folder = Path(state_dir) / "app-release"
    stamps = sorted(path.stat().st_mtime for path in folder.glob("*.apk")) if folder.is_dir() else []
    releases: List[Dict[str, Any]] = []
    for stamp in stamps:
        if releases and stamp - releases[-1]["at"] < SAME_RELEASE_SECONDS:
            continue
        releases.append({"at": stamp, "version": ""})
    try:
        latest = json.loads((folder / "release.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        latest = {}
    published = latest.get("published_at") if isinstance(latest, dict) else None
    if isinstance(published, (int, float)):
        near = [item for item in releases if abs(item["at"] - published) < SAME_RELEASE_SECONDS]
        if near:
            near[0].update(at=float(published), version=str(latest.get("version_name") or ""))
        else:
            releases.append({"at": float(published), "version": str(latest.get("version_name") or "")})
            releases.sort(key=lambda item: item["at"])
    return releases


def phase(at: float, version: str, releases: List[Dict[str, Any]]) -> Optional[str]:
    """这一刻的使用属于哪段特殊时期：settling（新版刚发布的上手期）、updating（有更新的版本还没装），平时是 None。"""
    if not releases:
        return None
    before = [item for item in releases if item["at"] <= at]
    if not before:
        return None
    newest = before[-1]
    if newest["version"] and version and newest["version"] != version:
        return "updating"
    known = [item for item in before if item["version"] == version] if version else []
    started = known[-1]["at"] if known else newest["at"]
    if at - started < SETTLING_SECONDS:
        return "settling"
    if not newest["version"] and newest["at"] > started:
        return "updating"
    return None


def describe(releases: List[Dict[str, Any]]) -> List[str]:
    import time
    return ["%s 发布%s" % (time.strftime("%m-%d %H:%M", time.localtime(item["at"])),
                         (" " + item["version"]) if item["version"] else "了一版 App")
            for item in releases]
