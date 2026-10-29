"""把发布的安装包、资源文件传到 GitHub Releases，好让手机“从网上下载”。

传的是这台电脑上发布的同一个文件，所以网上那份和 Runtime 提供的那份校验值一样，手机照常校验。
只上传，不推送代码：发布挂在仓库默认分支上，tag 由这里起。

令牌：先看环境变量 GITHUB_TOKEN / GH_TOKEN，没有就问 git 的凭据助手（就是 git push 到
GitHub 用的那份）。令牌只发给 api.github.com 和 uploads.github.com，不打印、不写盘。
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ASSET = re.compile(r"[A-Za-z0-9._-]{1,200}")
CHUNK = 1 << 20


class GitHubError(Exception):
    pass


def token() -> str:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    try:
        done = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                              capture_output=True, text=True, timeout=30,
                              env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitHubError("没拿到 GitHub 令牌：%s" % error)
    for line in done.stdout.splitlines():
        if line.startswith("password="):
            value = line[len("password="):].strip()
            if value:
                return value
    raise GitHubError("没拿到 GitHub 令牌：设置 GITHUB_TOKEN，或者先让 git 能推送到 GitHub")


def repo_from_git(folder: Path) -> str:
    """仓库的 GitHub 远端（github 优先，其次 origin）是哪个 owner/name。"""
    for remote in ("github", "origin"):
        try:
            url = subprocess.run(["git", "-C", str(folder), "remote", "get-url", remote],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            continue
        match = re.search(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$", url)
        if match:
            return match.group(1)
    raise GitHubError("找不到 GitHub 远端，请用 --github-repo owner/name 指定")


class Releases:
    def __init__(self, repo: str, secret: str, opener: Callable[..., Any] = urllib.request.urlopen,
                 log: Callable[[str], None] = lambda line: print(line, file=sys.stderr)):
        if not REPO.fullmatch(repo or ""):
            raise GitHubError("仓库应该是 owner/name：%s" % repo)
        self._repo, self._token, self._open, self._log = repo, secret, opener, log

    def _call(self, method: str, url: str, body: Any = None, data: Any = None,
              length: Optional[int] = None, timeout: int = 60) -> Any:
        headers = {"Authorization": "Bearer " + self._token, "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "xiaoyu-runtime"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif data is not None:
            headers["Content-Type"] = "application/octet-stream"
            headers["Content-Length"] = str(length)
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self._open(request, timeout=timeout) as response:
                text = response.read().decode("utf-8") or "null"
                return json.loads(text)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            detail = error.read().decode("utf-8", "replace")[:300]
            raise GitHubError("GitHub 返回 %d：%s" % (error.code, detail))
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise GitHubError("连不上 GitHub：%s" % error)

    def release(self, tag: str, title: str, body: str) -> Dict[str, Any]:
        """这个 tag 的发布；没有就建一个（预发布）。"""
        found = self._call("GET", "%s/repos/%s/releases/tags/%s" % (API, self._repo, urllib.parse.quote(tag)))
        if found:
            return found
        made = self._call("POST", "%s/repos/%s/releases" % (API, self._repo),
                          {"tag_name": tag, "name": title, "body": body, "prerelease": True})
        if not made:
            raise GitHubError("没能在 %s 建发布 %s" % (self._repo, tag))
        return made

    def upload(self, release: Dict[str, Any], path: Path, name: str) -> str:
        """传一个文件，返回公开下载地址。同名同大小的已经在了就直接用，不重传。"""
        if not ASSET.fullmatch(name):
            raise GitHubError("文件名不能用在 GitHub 上：%s" % name)
        size = Path(path).stat().st_size
        for asset in release.get("assets") or []:
            if asset.get("name") == name:
                if asset.get("size") == size and asset.get("state") == "uploaded":
                    return asset["browser_download_url"]
                # 上次没传完（或者内容不一样）：删掉重传。
                self._call("DELETE", "%s/repos/%s/releases/assets/%s" % (API, self._repo, asset["id"]))
        self._log("上传到 GitHub：%s（%.1f MB）" % (name, size / 1048576.0))
        with Path(path).open("rb") as stream:
            done = self._call("POST", "%s/repos/%s/releases/%s/assets?name=%s" % (
                UPLOADS, self._repo, release["id"], urllib.parse.quote(name)),
                data=_Progress(stream, size, self._log), length=size, timeout=3600)
        if not done or not done.get("browser_download_url", "").startswith("https://"):
            raise GitHubError("上传 %s 后 GitHub 没给下载地址" % name)
        return done["browser_download_url"]


class _Progress:
    """边读边报进度的文件，给 urllib 当请求体。"""

    def __init__(self, stream: Any, size: int, log: Callable[[str], None]):
        self._stream, self._size, self._log, self._sent, self._shown = stream, size, log, 0, -1

    def read(self, amount: int = CHUNK) -> bytes:
        chunk = self._stream.read(min(amount if amount and amount > 0 else CHUNK, CHUNK))
        self._sent += len(chunk)
        percent = self._sent * 100 // max(1, self._size)
        if percent // 10 != self._shown // 10:
            self._shown = percent
            self._log("  %d%%" % percent)
        return chunk

    def __len__(self) -> int:
        return self._size
