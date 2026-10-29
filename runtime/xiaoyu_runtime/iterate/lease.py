"""谁来跑自动迭代：能力声明和租约（见 docs/development/auto-iteration 的"无中心"）。

能力：这台电脑能做哪几步，写在 /v1/info 的 iterate.stages 里。
  collect、detect   总能做（只读本机的事件和记录）
  plan              有这个仓库，而且模型网关里 iterate.version_plan 或 iterate.plan 有可用的候选
  build             有 JDK 和 Android SDK（JAVA_HOME / ANDROID_HOME，或者 iterate.toolchain 指的目录）

租约：一轮要跑之前，向本机 Runtime 申请（POST /v1/iterate/lease）；Runtime 经 mesh 和别的电脑比较，
别的电脑持有没过期的租约就不给。持有者每到一步续一次，跑完放手；它关机了，租约过期后别的电脑可以接手。
Runtime 没开（或者这台没加入网络）时没有可比的，直接当作拿到。
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_TOOLCHAIN = Path("~/.local/share/ai-passport-toolchain").expanduser()
LEASE_SECONDS = 2 * 3600


def _has_android(settings: Any) -> bool:
    folder = getattr(settings, "toolchain", None) or DEFAULT_TOOLCHAIN
    if os.environ.get("JAVA_HOME") and os.environ.get("ANDROID_HOME"):
        return True
    return Path(folder, "android-sdk").is_dir() and any(Path(folder, "jdk").glob("*")) \
        if Path(folder, "jdk").is_dir() else False


def capabilities(settings: Any, gateway: Any = None) -> Dict[str, Any]:
    stages: List[str] = ["collect", "detect"]
    repo = Path(settings.repo_dir)
    has_repo = (repo / ".git").exists()
    if has_repo and gateway is not None and (gateway.available("iterate.version_plan")
                                             or gateway.available("iterate.plan")):
        stages.append("plan")
    if has_repo and _has_android(settings):
        stages.append("build")
    return {"enabled": bool(settings.enabled), "stages": stages}


class LeaseClient:
    """迭代子进程用：向本机 Runtime 申请、续约、放手。"""

    def __init__(self, config: Any, opener: Any = urllib.request.urlopen):
        host = config.host if config.host not in ("0.0.0.0", "::", "") else "127.0.0.1"
        self._url = "http://%s:%d/v1/iterate/lease" % (host, config.port)
        self._token = config.token
        self._open = opener

    def __call__(self, action: str, stage: str, seconds: float = LEASE_SECONDS) -> Dict[str, Any]:
        body = json.dumps({"action": action, "stage": stage, "seconds": seconds}).encode("utf-8")
        request = urllib.request.Request(self._url, data=body, method="POST", headers={
            "Authorization": "Bearer " + self._token, "Content-Type": "application/json"})
        try:
            with self._open(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as error:
            # Runtime 没开或者是旧版本：没有别的电脑可比，当作拿到。
            return {"granted": True, "local": True, "note": str(error)[:120]}


def local_lease(action: str, stage: str, seconds: float = LEASE_SECONDS,
                mesh: Optional[Any] = None, runtime_id: str = "") -> Dict[str, Any]:
    """Runtime 里处理一次申请：加入了网络就交给 mesh 比较，否则直接给。"""
    if action not in ("acquire", "renew", "release"):
        raise ValueError("action 只能是 acquire、renew、release")
    if not isinstance(stage, str) or not stage or len(stage) > 32:
        raise ValueError("stage 要写这一步的名字")
    if mesh is None:
        return {"granted": True, "holder": runtime_id, "local": True}
    return mesh.lease(action, stage, seconds)
