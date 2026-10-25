"""设备凭据和移出的电脑（R4.3，见 docs/development/runtime-refactor-design 第 6 节）。

每台设备（手机、电脑客户端）配对时拿到自己的凭据 dev.<编号>.<密钥>。密钥在发起配对的那台
Runtime 上生成、只给设备一次；各台只存它的哈希。名单由权限权威（Mac）记下，随小幽网络同步，
只认权威写的；撤销也一样，撤销是终态。旧的共享令牌（server.token）在过渡期照常可用。

移出的电脑也记在这里：权威把一台电脑移出后，各台拒绝和它交换记录。
"""

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .store import _write

CREDENTIAL = re.compile(r"^dev\.([0-9a-f]{16})\.([0-9a-f]{64})$")
KINDS = ("phone", "desktop", "device")
ID = re.compile(r"^[0-9a-f]{16}$")
RUNTIME = re.compile(r"^[0-9a-f]{32}$")


def hash_secret(secret: str) -> str:
    return hashlib.sha256(("xiaoyou-device\0" + secret).encode("utf-8")).hexdigest()


def new_credential() -> Tuple[str, str, str]:
    """(设备编号, 凭据, 密钥哈希)。凭据只在这里出现一次。"""
    device_id, secret = secrets.token_hex(8), secrets.token_hex(32)
    return device_id, "dev.%s.%s" % (device_id, secret), hash_secret(secret)


def _device(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not ID.match(item["id"]) \
            or not isinstance(item.get("hash"), str) or not re.fullmatch(r"[0-9a-f]{64}", item["hash"]):
        return None
    rev = item.get("rev")
    return {"id": item["id"], "name": str(item.get("name") or "")[:40],
            "kind": item.get("kind") if item.get("kind") in KINDS else "device",
            "hash": item["hash"], "rev": rev if isinstance(rev, int) and not isinstance(rev, bool) else 0,
            "created_at": float(item.get("created_at") or 0),
            "revoked_at": float(item["revoked_at"]) if isinstance(item.get("revoked_at"), (int, float)) else None}


class Devices:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._devices: Dict[str, Dict[str, Any]] = {}
        self._removed: Dict[str, Dict[str, Any]] = {}  # 移出的电脑：runtime_id → {"at", "name"}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        for item in raw.get("devices", []) if isinstance(raw.get("devices"), list) else []:
            device = _device(item)
            if device is not None:
                self._devices[device["id"]] = device
        for key, item in (raw.get("removed") or {}).items() if isinstance(raw.get("removed"), dict) else []:
            if RUNTIME.match(key) and isinstance(item, dict):
                self._removed[key] = {"at": float(item.get("at") or 0), "name": str(item.get("name") or "")[:40]}

    def _save(self) -> None:
        _write(self._path, {"devices": list(self._devices.values()), "removed": self._removed}, 2)

    # ---- 权威上：记下、撤销 ----

    def add(self, device_id: str, name: str, kind: str, secret_hash: str) -> Dict[str, Any]:
        device = _device({"id": device_id, "name": name, "kind": kind, "hash": secret_hash, "rev": 1,
                          "created_at": time.time()})
        if device is None:
            raise ValueError("设备编号或凭据哈希不对")
        with self._lock:
            known = self._devices.get(device_id)
            if known is not None:
                if known["hash"] != secret_hash:
                    raise ValueError("这个设备编号已经用过了")
                return dict(known)  # 同一个请求重复提交：同一个结果
            self._devices[device_id] = device
            self._save()
            return dict(device)

    def revoke(self, device_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            device = self._devices.get(device_id)
            if device is None:
                return None
            if device["revoked_at"] is None:
                device.update(revoked_at=time.time(), rev=device["rev"] + 1)
                self._save()
            return dict(device)

    def remove_member(self, runtime_id: str, name: str = "") -> Dict[str, Any]:
        if not RUNTIME.match(runtime_id):
            raise ValueError("电脑编号不对")
        with self._lock:
            item = self._removed.setdefault(runtime_id, {"at": time.time(), "name": name[:40]})
            self._save()
            return dict(item, runtime_id=runtime_id)

    # ---- 别的电脑上：照着权威的记录改 ----

    def merge(self, item: Any) -> bool:
        device = _device(item)
        if device is None:
            return False
        with self._lock:
            known = self._devices.get(device["id"])
            if known is not None and (known["rev"] >= device["rev"] or known["revoked_at"] is not None):
                return False  # 撤销是终态：迟到的旧记录不能让它复活
            self._devices[device["id"]] = device
            self._save()
            return True

    def merge_removed(self, runtime_id: str, item: Any) -> bool:
        if not RUNTIME.match(runtime_id) or not isinstance(item, dict):
            return False
        with self._lock:
            if runtime_id in self._removed:
                return False
            self._removed[runtime_id] = {"at": float(item.get("at") or 0), "name": str(item.get("name") or "")[:40]}
            self._save()
            return True

    # ---- 读 ----

    def verify(self, credential: str) -> Optional[Dict[str, Any]]:
        """凭据对、没撤销：返回那台设备（不含哈希）；否则 None。"""
        match = CREDENTIAL.match(credential or "")
        if match is None:
            return None
        with self._lock:
            device = self._devices.get(match.group(1))
        if device is None or device["revoked_at"] is not None:
            return None
        if not hmac.compare_digest(device["hash"], hash_secret(match.group(2))):
            return None
        return self.view(device)

    def key_hash(self, credential: str) -> Optional[str]:
        match = CREDENTIAL.match(credential or "")
        return hash_secret(match.group(2)) if match else None

    def record(self, device_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            device = self._devices.get(device_id)
            return dict(device) if device is not None else None

    @staticmethod
    def view(device: Dict[str, Any]) -> Dict[str, Any]:
        return {key: device[key] for key in ("id", "name", "kind", "created_at", "revoked_at")}

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [self.view(device) for device in sorted(self._devices.values(), key=lambda d: d["created_at"])]

    def removed(self, runtime_id: str) -> bool:
        with self._lock:
            return runtime_id in self._removed

    def removed_members(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self._removed.items()}
