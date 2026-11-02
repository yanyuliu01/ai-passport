"""Durable bounded image transfer for the authenticated local desktop bridge.

Only the desktop's paired, encrypted peer transport may carry these envelopes;
mesh replication itself must never carry image bytes. The HTTP adapter restricts
all entry points to loopback plus the local mesh bearer.
"""
import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
from pathlib import Path

from ..events.image_inputs import MAX_UPLOAD_BYTES, upload
from ..world.artifacts import ArtifactError

CHUNK = 256 * 1024
MAX_TRANSFERS = 8
MAX_RECEIPTS = 4096
MAX_BYTES = 4 * MAX_UPLOAD_BYTES
STALE_SECONDS = 24 * 60 * 60
ID = re.compile(r"^[0-9a-f]{32}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
CONVERSATION = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


class ImageRelayError(ValueError):
    pass


def _manifest(raw):
    if not isinstance(raw, dict):
        raise ImageRelayError("图片传输格式不正确")
    keys = ("message_id", "source_runtime", "target_runtime", "conversation", "owner_generation",
            "text", "agent", "effort", "model", "attachments")
    value = {key: raw.get(key) for key in keys}
    if not all(isinstance(value[k], str) and ID.fullmatch(value[k]) for k in ("message_id", "source_runtime", "target_runtime")):
        raise ImageRelayError("图片传输身份不正确")
    if not isinstance(value["conversation"], str) or not CONVERSATION.fullmatch(value["conversation"]):
        raise ImageRelayError("图片传输对话不正确")
    if type(value["owner_generation"]) is not int or value["owner_generation"] < 0:
        raise ImageRelayError("图片传输的对话归属不正确")
    if not isinstance(value["text"], str) or len(value["text"]) > 8000:
        raise ImageRelayError("图片传输文字不正确")
    for key in ("agent", "effort", "model"):
        if value[key] is not None and (not isinstance(value[key], str) or len(value[key]) > 128):
            raise ImageRelayError("图片传输选项不正确")
    items = value["attachments"]
    if not isinstance(items, list) or not 1 <= len(items) <= 4:
        raise ImageRelayError("图片数量不正确")
    checked = []
    for item in items:
        if not isinstance(item, dict):
            raise ImageRelayError("图片信息不正确")
        ident, sha, size, mime = (item.get(k) for k in ("id", "sha256", "size", "mime"))
        if (not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", ident)
                or not isinstance(sha, str) or not SHA.fullmatch(sha)
                or type(size) is not int or not 0 < size <= MAX_UPLOAD_BYTES or mime not in MIMES):
            raise ImageRelayError("图片信息不正确")
        checked.append({"id": ident, "sha256": sha, "size": size, "mime": mime})
    if len({item["id"] for item in checked}) != len(checked):
        raise ImageRelayError("图片编号重复")
    value["attachments"] = checked
    return value


def _decode(value):
    if not isinstance(value, str) or not value or len(value) > 350000 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ImageRelayError("图片分块编码不正确")
    try:
        result = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error):
        raise ImageRelayError("图片分块编码不正确") from None
    if not result or len(result) > CHUNK or base64.urlsafe_b64encode(result).decode().rstrip("=") != value:
        raise ImageRelayError("图片分块长度不正确")
    return result


def _open_part(path, writable=False):
    if os.name == "nt":
        from ..windows_files import open_regular, open_rw_regular
        return open_rw_regular(path, MAX_UPLOAD_BYTES) if writable else open_regular(path, MAX_UPLOAD_BYTES)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise OSError("secure no-follow file access is unavailable")
    flags = (os.O_RDWR | os.O_CREAT) if writable else os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags | nofollow, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError("not a regular file")
    return os.fdopen(fd, "r+b" if writable else "rb")


class ImageRelay:
    def __init__(self, mesh):
        self.mesh = mesh
        self.service = mesh._service
        self._lock = threading.RLock()
        self.root = Path(mesh._config.state_dir).resolve() / "mesh" / "image-relays"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink() or self.root.resolve() != self.root:
            raise ImageRelayError("图片传输目录不安全")

    def pending(self, target):
        if not isinstance(target, str) or not ID.fullmatch(target):
            raise ImageRelayError("目标电脑不正确")
        result = []
        for item in self.service.relayed():
            if item.get("relay_to") != target or not item.get("attachments") or not item.get("routed"):
                continue
            view = self.mesh.owner_of(item["conversation"])
            if view.get("unresolved"):
                continue
            if view.get("owner") != target or view.get("generation") != item.get("owner_generation"):
                # The original destination may already have accepted the turn.
                # Never replay it at the new owner without reconciliation.
                with self.mesh._lock:
                    self.mesh._relays.pop(item["id"], None)
                self.service.relay_done(item["id"], False, "对话负责人已变化，原图片消息是否已处理需要先核对", unknown=True)
                continue
            envelope = {"message_id": item["id"], "source_runtime": self.mesh.runtime_id,
                "target_runtime": target, "conversation": item["conversation"],
                "owner_generation": item.get("owner_generation"), "text": item.get("text"),
                "agent": item.get("asked"), "effort": item.get("effort"), "model": item.get("model"),
                "attachments": item["attachments"]}
            result.append(_manifest(envelope))
        return {"transfers": result}

    def _outgoing(self, body):
        message_id, target = body.get("message_id"), body.get("target_runtime")
        return next((x for x in self.pending(target)["transfers"] if x["message_id"] == message_id), None)

    def read(self, body):
        if not isinstance(body, dict):
            raise ImageRelayError("图片请求不正确")
        envelope = self._outgoing(body)
        if envelope is None:
            raise ImageRelayError("没有等待发送的图片")
        index, offset = body.get("index"), body.get("offset")
        if type(index) is not int or not 0 <= index < len(envelope["attachments"]) or type(offset) is not int or offset < 0:
            raise ImageRelayError("图片分块位置不正确")
        item = envelope["attachments"][index]
        if offset >= item["size"]:
            raise ImageRelayError("图片分块位置不正确")
        with self.service._write_lock:
            current = self.service.get(envelope["message_id"])
            if current is None or current.get("status") != "running":
                raise ImageRelayError("原图片消息已停止")
            if not current.get("_image_transfer_started"):
                # Persist before returning bytes: after a crash we must not
                # treat this delivery as certainly unstarted.
                self.service._update(envelope["message_id"], strict=True, _image_transfer_started=True)
            with self.service.artifacts.open_content(item["id"])[0] as stream:
                stream.seek(offset)
                data = stream.read(min(CHUNK, item["size"] - offset))
        if not data:
            raise ImageRelayError("原图片不可读取")
        return {"offset": offset, "next": offset + len(data), "size": item["size"],
                "data": base64.urlsafe_b64encode(data).decode().rstrip("=")}

    def _owner(self, envelope):
        if envelope["target_runtime"] != self.mesh.runtime_id or envelope["source_runtime"] == self.mesh.runtime_id:
            raise ImageRelayError("图片传输目标不正确")
        if self.service.devices.removed(envelope["source_runtime"]):
            raise ImageRelayError("发送电脑已移除")
        view = self.mesh.owner_of(envelope["conversation"])
        if view.get("unresolved") or view.get("owner") != self.mesh.runtime_id or view.get("generation") != envelope["owner_generation"]:
            raise ImageRelayError("对话负责人已变化，请等待同步后重试")

    def _folder(self, envelope):
        # The directory key binds the sender as well as the message id.
        key = hashlib.sha256((envelope["source_runtime"] + ":" + envelope["message_id"]).encode()).hexdigest()
        return self.root / key

    def _stored(self, folder):
        try:
            if folder.is_symlink() or folder.resolve() != folder:
                raise OSError("unsafe transfer directory")
            with _open_part(folder / "manifest.json") as stream:
                raw = stream.read(128 * 1024 + 1)
            if len(raw) > 128 * 1024:
                raise ValueError("manifest too large")
            return json.loads(raw.decode("utf-8"))
        except (OSError, ValueError):
            raise ImageRelayError("图片传输记录不可用") from None

    def _clean_stale(self):
        """Discard only inactive partial uploads; source retains the request."""
        now = time.time()
        for folder in self.root.iterdir():
            if folder.is_symlink() or not folder.is_dir():
                continue
            try:
                try:
                    stored = self._stored(folder)
                except ImageRelayError:
                    stored = {}
                if stored.get("finished"):
                    # A crash after the durable receipt but before unlink must
                    # not retain up to 32 MiB of redundant staged image data.
                    for part in folder.glob("*.part"):
                        try:
                            part.unlink()
                        except OSError:
                            pass
                    continue  # durable idempotency receipt
                last = max([folder.stat().st_mtime] + [p.stat().st_mtime for p in folder.iterdir() if p.is_file()])
                if now - last > STALE_SECONDS:
                    shutil.rmtree(folder)
            except (OSError, ValueError):
                continue

    @staticmethod
    def _save(folder, value):
        fd, name = tempfile.mkstemp(prefix="manifest-", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(value, output, ensure_ascii=False, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, folder / "manifest.json")
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def receive(self, body):
        envelope = _manifest(body)
        index, offset = body.get("index"), body.get("offset")
        if type(index) is not int or not 0 <= index < len(envelope["attachments"]) or type(offset) is not int or offset < 0:
            raise ImageRelayError("图片分块位置不正确")
        data = _decode(body.get("data"))
        size = envelope["attachments"][index]["size"]
        if offset + len(data) > size:
            raise ImageRelayError("图片分块超过声明大小")
        with self._lock:
            self._owner(envelope)
            folder = self._folder(envelope)
            if not folder.exists():
                self._clean_stale()
                active = []
                receipts = 0
                for p in self.root.iterdir():
                    if not p.is_dir() or p.is_symlink():
                        continue
                    try:
                        finished = self._stored(p).get("finished")
                    except ImageRelayError:
                        finished = False
                    if not finished:
                        active.append(p)
                    else:
                        receipts += 1
                if len(active) >= MAX_TRANSFERS:
                    raise ImageRelayError("图片传输队列已满")
                if receipts >= MAX_RECEIPTS:
                    raise ImageRelayError("图片传输回执已满，请先整理传输记录")
                folder.mkdir(mode=0o700)
                self._save(folder, {"envelope": envelope, "finished": False})
            if folder.is_symlink() or folder.resolve() != folder:
                raise ImageRelayError("图片传输目录不安全")
            if not (folder / "manifest.json").exists() and not list(folder.iterdir()):
                # A crash after mkdir but before the manifest rename is safe to
                # resume: no part has been acknowledged or written yet.
                self._save(folder, {"envelope": envelope, "finished": False})
            stored = self._stored(folder)
            if stored.get("envelope") != envelope:
                raise ImageRelayError("图片传输编号与原请求冲突")
            if stored.get("finished"):
                return {"next": size}
            path = folder / (str(index) + ".part")
            with _open_part(path, writable=True) as stream:
                stream.seek(0, os.SEEK_END)
                length = stream.tell()
                if length > size or offset > length:
                    raise ImageRelayError("图片分块不连续")
                stream.seek(offset)
                overlap = min(length - offset, len(data))
                if stream.read(overlap) != data[:overlap]:
                    raise ImageRelayError("重复图片分块内容不一致")
                if overlap < len(data):
                    stream.seek(length)
                    stream.write(data[overlap:])
                    stream.flush()
                    os.fsync(stream.fileno())
                return {"next": offset + len(data)}

    def finish(self, body):
        envelope = _manifest(body)
        with self._lock:
            folder = self._folder(envelope)
            stored = self._stored(folder)
            if stored.get("envelope") != envelope:
                raise ImageRelayError("图片传输编号与原请求冲突")
            if stored.get("finished"):
                return {"message_id": envelope["message_id"], "status": "accepted"}
            # If a crash happened after durable message acceptance but before
            # writing our transfer receipt, recover the same message even if
            # ownership has subsequently moved. Never submit it a second time.
            prior = self.service.get(envelope["message_id"])
            if prior is not None:
                prior_images = [(x.get("id"), x.get("sha256")) for x in prior.get("attachments", [])]
                requested = [(x["id"], x["sha256"]) for x in envelope["attachments"]]
                if (prior.get("conversation") != envelope["conversation"] or prior.get("text") != envelope["text"]
                        or prior.get("asked") != envelope["agent"] or prior.get("effort") != envelope["effort"]
                        or prior.get("model") != envelope["model"] or prior_images != requested):
                    raise ImageRelayError("图片消息编号与已有消息冲突")
                stored["finished"] = True
                self._save(folder, stored)
                return {"message_id": envelope["message_id"], "status": "accepted"}
            self._owner(envelope)
            ids = []
            for index, item in enumerate(envelope["attachments"]):
                path = folder / (str(index) + ".part")
                try:
                    with _open_part(path) as stream:
                        data = stream.read(MAX_UPLOAD_BYTES + 1)
                except OSError:
                    raise ImageRelayError("图片尚未传完整") from None
                if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise ImageRelayError("图片尚未传完整或校验失败")
                meta = upload(self.service.artifacts, data)
                if meta["id"] != item["id"] or meta["mime"] != item["mime"]:
                    raise ImageRelayError("图片身份校验失败")
                ids.append(meta["id"])
            with self.service._write_lock, self.service._changed:
                self._owner(envelope)
                prior = self.service.get(envelope["message_id"])
                if prior is not None:
                    prior_images = [(x.get("id"), x.get("sha256")) for x in prior.get("attachments", [])]
                    requested = [(x["id"], x["sha256"]) for x in envelope["attachments"]]
                    if (prior.get("conversation") != envelope["conversation"] or prior.get("text") != envelope["text"]
                            or prior.get("asked") != envelope["agent"] or prior.get("effort") != envelope["effort"]
                            or prior.get("model") != envelope["model"] or prior_images != requested):
                        raise ImageRelayError("图片消息编号与已有消息冲突")
                result = self.service.submit(envelope["text"], envelope["conversation"], client_id=envelope["message_id"],
                    agent=envelope["agent"], effort=envelope["effort"], model=envelope["model"], hop=1,
                    mesh={"message_id": envelope["message_id"], "routed_generation": envelope["owner_generation"]},
                    image_ids=ids, _trusted_mesh_images=True)
            if result["id"] != envelope["message_id"]:
                raise ImageRelayError("图片消息编号冲突")
            stored["finished"] = True
            self._save(folder, stored)
            for index in range(len(ids)):
                try:
                    (folder / (str(index) + ".part")).unlink()
                except FileNotFoundError:
                    pass
            return {"message_id": envelope["message_id"], "status": "accepted"}

    def progress(self, body):
        if not isinstance(body, dict):
            raise ImageRelayError("图片回复状态不正确")
        envelope = self._outgoing(body)
        rev, text = body.get("rev"), body.get("partial")
        if envelope is None or type(rev) is not int or rev < 0 or not isinstance(text, str) or len(text) > 32000:
            raise ImageRelayError("图片回复状态不正确")
        with self.service._write_lock:
            with self.service._changed:
                message = self.service._messages.get(envelope["message_id"])
                if not message or message.get("status") != "running":
                    return {"status": "finished"}
                if rev <= message.get("_image_remote_rev", -1):
                    return {"status": "recorded"}
                first = message.get("first_partial_at") or (time.time() if text else None)
            self.service._update(envelope["message_id"], strict=True, _image_remote_rev=rev,
                                 partial=text, first_partial_at=first, stage="图片已送达，等待回复")
        return {"status": "recorded"}

    def unavailable(self, body):
        if not isinstance(body, dict) or body.get("reason") not in ("unsupported", "unverified", "retrying"):
            raise ImageRelayError("图片传输状态不正确")
        envelope = self._outgoing(body)
        if envelope is None:
            raise ImageRelayError("没有等待发送的图片")
        stage = {"unsupported": "对方版本不支持图片传输，请升级后重试",
                 "unverified": "正在等待对方安全身份验证",
                 "retrying": "图片传输暂未完成，正在重试"}[body["reason"]]
        current = self.service.get(envelope["message_id"])
        if current and current.get("stage") != stage:
            self.service._update(envelope["message_id"], stage=stage)
        return {"status": "recorded"}

    def expire_unstarted(self, seconds=90):
        """Bound waits when an old local desktop cannot start the bridge."""
        now = time.time()
        with self.service._write_lock:
            for message in self.service.relayed():
                if (not message.get("attachments") or not message.get("routed")
                        or message.get("_image_transfer_started") or not message.get("processing_started_at")
                        or now - message["processing_started_at"] < seconds):
                    continue
                with self.mesh._lock:
                    self.mesh._relays.pop(message["id"], None)
                self.service.relay_done(message["id"], False,
                    "图片尚未发出：对方或本机桌面不支持安全图片传输，请升级并重新发送")

    def ack(self, body):
        if not isinstance(body, dict):
            raise ImageRelayError("图片确认不正确")
        envelope = self._outgoing(body)
        if envelope is None:
            prior = self.service.get(body.get("message_id")) if isinstance(body.get("message_id"), str) else None
            if prior and prior.get("relay_to") == body.get("target_runtime") and prior.get("status") == body.get("status") and prior.get("status") in ("done", "failed"):
                return {"status": "recorded"}
        if envelope is None or body.get("status") not in ("done", "failed"):
            raise ImageRelayError("图片确认与原请求不匹配")
        text = body.get("reply") if body["status"] == "done" else body.get("error")
        if text is not None and (not isinstance(text, str) or len(text) > 32000):
            raise ImageRelayError("图片确认结果不正确")
        self.mesh._finish_relay(envelope["message_id"], body["status"] == "done", text or "")
        return {"status": "recorded"}
