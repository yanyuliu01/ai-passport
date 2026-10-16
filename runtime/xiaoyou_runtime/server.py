"""HTTP 接口：手机 App（或任何客户端）通过它和小幽说话。

  GET  /healthz                         不需要令牌，只说明服务活着
  GET  /v1/agents                       小幽在这台 Runtime 上能用的代理
  GET  /v1/usage?refresh=1              订阅额度还剩多少，每个帮手用的模型和档位；refresh 现问一次
  POST /v1/messages                     {"text", "conversation"?, "client_id"?, "agent"?, "card"?, "pin"?,
                                         "effort"?, "model"?} → 202 + 消息
  POST /v1/voice?conversation=&client_id=&agent=&card=&pin=&effort=&model=
                                        请求体是 16 位单声道 WAV → 202 + 消息
  GET  /v1/messages/<id>?wait=<秒>&rev=<n>  查结果；wait 最多 60 秒，处理完会提前返回；
                                        带 rev 时记录一有变化就返回；回复还在生成时
                                        partial 是已经生成的那部分（流式）
  GET  /v1/feed?conversation=&after=<序号>&wait=<秒>&usage=<rev>
                                        之后变过的卡，和现在的用量；没有变化时最多等 wait 秒
  GET  /v1/cards?conversation=          最近的卡
  POST /v1/cards/<编号>/artifacts       {"artifacts":[{"path","name"?}], "entry_index"?} 登记本地产物
  GET  /v1/artifacts/<编号>/content     按附件编号取得已保存的文件快照
  GET  /v1/artifacts/<编号>/thumbnail   取得图片缩略图（不可用时返回 404）
  POST /v1/cards/<编号>/cancel          取消一件事
  POST /v1/approvals/<编号>             {"decision": "allow" 或 "deny"} 回答一个授权
  POST /v1/conversations/<名字>/reset   让这个对话从头开始
  POST /v1/conversations/<名字>/history {"turns":[{"id","text","reply","at"?}]} 带来别处的对话

设备固件（这台电脑上留着每一版；手机 App 取走镜像，经蓝牙写进设备）：

  GET  /v1/firmware                     版本清单、要推给设备的那一版、设备上次报的情况
  GET  /v1/firmware/target?wait=&rev=   要推给设备的那一版（一层的对象，没有时 id 为空）；
                                        带 rev 时清单一有变化就返回
  GET  /v1/firmware/<编号>/image         这一版的应用镜像（二进制）
  POST /v1/firmware?note=&push=1        请求体是固件文件（应用镜像或合并镜像）→ 入库
  POST /v1/firmware/target              {"id": 编号、序号、latest、previous，或 null 取消, "force"?}
  POST /v1/firmware/device              手机转告设备的情况 {"event","build","state",…} → 同 target

手机 App 更新（这台电脑上只留最新发布的一版，用 `python3 -m xiaoyou_runtime app publish` 发布）：

  GET  /v1/app                          发布的那一版 {"available","version_code","version_name","sha256","size",
                                         "published_at","note"}
  GET  /v1/app/apk                      安装包本身

除 /healthz 外都要带 Authorization: Bearer <令牌>。
"""

import gzip
import hmac
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, quote, urlsplit

from . import __version__
from .app_release import AppRelease, AppReleaseError, byte_range
from .artifacts import ArtifactError, ArtifactStore, COPY_CHUNK
from .config import Config
from .firmware import MAX_FILE_BYTES, FirmwareError, FirmwareStore

# 比这小的回答不值得压。
GZIP_MIN_BYTES = 2048
from .service import MAX_AUDIO_BYTES, RequestError, Service

MAX_BODY_BYTES = 64 * 1024
MAX_HISTORY_BODY_BYTES = 1024 * 1024
MAX_WAIT_SECONDS = 60.0


def make_server(config: Config, service: Service,
                firmware: Optional[FirmwareStore] = None,
                artifacts: Optional[ArtifactStore] = None) -> ThreadingHTTPServer:
    expected = ("Bearer " + config.token).encode("utf-8")
    if firmware is None:
        firmware = FirmwareStore(config.state_dir)
    if artifacts is None:
        artifacts = getattr(service, "artifacts", None) or ArtifactStore(config)
    release = AppRelease(config.state_dir)
    default_spec = config.agent(config.default_agent)
    default_type = default_spec.type if default_spec is not None else ""

    class Handler(BaseHTTPRequestHandler):
        server_version = "xiaoyou-runtime/" + __version__
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

        def _send(self, status: int, body: Dict[str, Any]) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            # 手机常经中继连过来，整份卡片有几百 KB：对方说收得了 gzip 就压一下。
            zipped = (len(data) > GZIP_MIN_BYTES
                      and "gzip" in self.headers.get("Accept-Encoding", "").lower())
            if zipped:
                data = gzip.compress(data, 6)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if zipped:
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _fail(self, status: int, message: str) -> None:
            # 出错后不再复用这条连接：请求体可能还没读完。
            self.close_connection = True
            self._send(status, {"error": message})

        def _authorized(self) -> bool:
            given = self.headers.get("Authorization", "").encode("utf-8")
            if hmac.compare_digest(given, expected):
                return True
            self._fail(401, "令牌不对或者没带令牌")
            return False

        def _body(self, limit: int = MAX_BODY_BYTES) -> Tuple[bool, Any]:
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._fail(411, "需要 Content-Length")
                return False, None
            if length < 0 or length > limit:
                self._fail(413, "请求体太大")
                return False, None
            try:
                return True, json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._fail(400, "请求体不是合法的 JSON")
                return False, None

        def _wait_and_rev(self, query: Dict[str, Any]) -> Optional[Tuple[float, Optional[int]]]:
            """读 ?wait=&rev=；写得不对时回 400 并返回 None。"""
            try:
                wait = float(query.get("wait", ["0"])[0])
            except ValueError:
                self._fail(400, "wait 应该是秒数")
                return None
            if not 0 <= wait <= MAX_WAIT_SECONDS:  # 同时挡掉 NaN
                self._fail(400, "wait 应该在 0 到 %d 之间" % MAX_WAIT_SECONDS)
                return None
            rev = None
            if "rev" in query:
                try:
                    rev = int(query["rev"][0])
                except ValueError:
                    self._fail(400, "rev 应该是整数")
                    return None
            return wait, rev

        def _message_stream(self, message_id: str, query: Dict[str, Any]) -> None:
            asked = self._wait_and_rev(query)
            if asked is None:
                return
            after = asked[1]
            if service.get(message_id) is None:
                self._fail(404, "没有这条消息（Runtime 重启后旧消息查不到）")
                return
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while True:
                    current = service.get(message_id, wait=15 if after is not None else 0, rev=after)
                    if current is None:
                        # A restart/eviction does not authorize the client to resubmit the job.
                        event = {"id": message_id, "status": "failed", "error": "消息记录已不可用，请查看任务"}
                        self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
                        self.wfile.flush()
                        return
                    terminal = current["status"] in ("done", "failed")
                    if current["rev"] != after or terminal:
                        data = json.dumps(current, ensure_ascii=False)
                        self.wfile.write(("id: %d\nevent: message\ndata: %s\n\n" % (current["rev"], data)).encode("utf-8"))
                    else:
                        self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    if terminal:
                        return
                    after = current["rev"]
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def _firmware_get(self, parts: Any, query: Dict[str, Any]) -> None:
            try:
                if len(parts) == 2:
                    self._send(200, firmware.snapshot())
                elif parts[2:] == ["target"]:
                    asked = self._wait_and_rev(query)
                    if asked is None:
                        return
                    wait, rev = asked
                    if rev is not None and wait > 0:
                        firmware.wait_for_change(rev, wait)
                    self._send(200, firmware.target())
                elif len(parts) == 4 and parts[3] == "image":
                    data = firmware.image(parts[2])
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._fail(404, "没有这个地址")
            except FirmwareError as error:
                self._fail(404 if len(parts) == 4 else 500, str(error))

        def _artifact_get(self, artifact_id: str, thumbnail: bool) -> None:
            try:
                stream, meta, mime, size = artifacts.open_content(artifact_id, thumbnail)
            except ArtifactError as error:
                self._fail(404, str(error))
                return
            with stream:
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(size))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "private, no-store")
                self.send_header("ETag", '"' + meta["sha256"] + '"')
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(meta["name"], safe=""))
                self.end_headers()
                while True:
                    chunk = stream.read(COPY_CHUNK)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        def _app_apk(self, query: Dict[str, Any]) -> None:
            try:
                stream, info, size = release.open(query.get("abi", [""])[0], query.get("sha256", [""])[0])
            except (AppReleaseError, OSError) as error:
                self._fail(409 if query.get("sha256") else 404, str(error))
                return
            with stream:
                etag = '"' + info["sha256"] + '"'
                if self.headers.get("If-Match", etag) not in (etag, "*"):
                    self._fail(412, "安装包已更新，请重新检查更新")
                    return
                asked = self.headers.get("Range", "")
                partial = bool(asked and self.headers.get("If-Range", etag) == etag)
                start, end = 0, size - 1
                if partial:
                    try:
                        start, end = byte_range(asked, size)
                    except ValueError:
                        self.send_response(416)
                        self.send_header("Content-Range", "bytes */%d" % size)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                self.send_response(206 if partial else 200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Accept-Ranges", "bytes")
                if partial:
                    self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
                self.send_header("Cache-Control", "private, no-store")
                self.send_header("ETag", etag)
                self.end_headers()
                stream.seek(start)
                remaining = end - start + 1
                try:
                    while remaining:
                        chunk = stream.read(min(COPY_CHUNK, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return

        def _firmware_post(self, parts: Any, query: Dict[str, Any]) -> None:
            try:
                if len(parts) == 2:
                    try:
                        length = int(self.headers.get("Content-Length", ""))
                    except ValueError:
                        self._fail(411, "需要 Content-Length")
                        return
                    if length <= 0 or length > MAX_FILE_BYTES:
                        self._fail(413, "固件文件是空的或者超过 8 MB")
                        return
                    blob = self.rfile.read(length)
                    if len(blob) != length:
                        self._fail(400, "固件文件没有传完")
                        return
                    entry = firmware.add(blob, query.get("source", ["upload"])[0][:80],
                                         query.get("note", [""])[0][:200])
                    if query.get("push", ["0"])[0] in ("1", "true"):
                        firmware.set_target(entry["id"], "push")
                    self._send(201, entry)
                    return
                ok, body = self._body()
                if not ok:
                    return
                if not isinstance(body, dict):
                    raise FirmwareError("请求体应该是一个对象")
                if parts[2:] == ["target"]:
                    wanted = body.get("id")
                    if wanted is None:
                        firmware.clear_target()
                    elif isinstance(wanted, (str, int)) and not isinstance(wanted, bool):
                        firmware.set_target(str(wanted), str(body.get("reason") or "push")[:40],
                                            body.get("force") is True)
                    else:
                        raise FirmwareError("id 应该是编号、序号、latest、previous，或者 null")
                    self._send(200, firmware.target())
                elif parts[2:] == ["device"]:
                    self._send(200, firmware.report(body))
                else:
                    self._fail(404, "没有这个地址")
            except FirmwareError as error:
                self._fail(400, str(error))

        def do_GET(self) -> None:
            url = urlsplit(self.path)
            parts = [part for part in url.path.split("/") if part]
            if parts == ["healthz"]:
                # backend 是给旧版手机 App 看的：默认代理的类型。
                self._send(200, {
                    "ok": True, "version": __version__, "backend": default_type,
                    "name": config.name,
                })
                return
            if not self._authorized():
                return
            query = parse_qs(url.query)
            if len(parts) == 4 and parts[:2] == ["v1", "artifacts"] and parts[3] in ("content", "thumbnail"):
                self._artifact_get(parts[2], parts[3] == "thumbnail")
                return
            if parts == ["v1", "app"]:
                self._send(200, release.info(query.get("abi", [""])[0]))
                return
            if parts == ["v1", "app", "apk"]:
                self._app_apk(query)
                return
            if parts == ["v1", "agents"]:
                self._send(200, {"default": config.default_agent, "agents": service.agents()})
                return
            if parts == ["v1", "usage"]:
                self._send(200, service.usage(query.get("refresh", [""])[0] in ("1", "true")))
                return
            if parts == ["v1", "cards"]:
                try:
                    cards = service.cards(query.get("conversation", ["default"])[0])
                except RequestError as error:
                    self._fail(400, str(error))
                    return
                self._send(200, {"cards": cards})
                return
            if parts == ["v1", "feed"]:
                asked = self._wait_and_rev(query)
                if asked is None:
                    return
                try:
                    after = int(query.get("after", ["0"])[0])
                except ValueError:
                    self._fail(400, "after 应该是整数")
                    return
                try:
                    seen = int(query["usage"][0]) if "usage" in query else None
                except ValueError:
                    self._fail(400, "usage 应该是整数")
                    return
                try:
                    feed = service.feed(query.get("conversation", ["default"])[0], after, asked[0],
                                        seen)
                except RequestError as error:
                    self._fail(400, str(error))
                    return
                self._send(200, feed)
                return
            if parts[:2] == ["v1", "firmware"]:
                self._firmware_get(parts, query)
                return
            if len(parts) == 4 and parts[:2] == ["v1", "messages"] and parts[3] == "stream":
                self._message_stream(parts[2], query)
                return
            if len(parts) == 3 and parts[:2] == ["v1", "messages"]:
                asked = self._wait_and_rev(query)
                if asked is None:
                    return
                wait, rev = asked
                message = service.get(parts[2], wait, rev)
                if message is None:
                    self._fail(404, "没有这条消息（Runtime 重启后旧消息查不到）")
                else:
                    self._send(200, message)
                return
            self._fail(404, "没有这个地址")

        def _voice(self, query: Dict[str, Any]) -> None:
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._fail(411, "需要 Content-Length")
                return
            if length <= 0 or length > MAX_AUDIO_BYTES:
                self._fail(413, "录音是空的或者太大")
                return
            audio = self.rfile.read(length)
            if len(audio) != length:
                self._fail(400, "录音没有传完")
                return
            try:
                message = service.submit_voice(
                    audio, query.get("conversation", ["default"])[0],
                    query.get("client_id", [None])[0], query.get("agent", [None])[0],
                    query.get("card", [None])[0],
                    query.get("pin", [""])[0] in ("1", "true"),
                    query.get("effort", [None])[0], query.get("model", [None])[0],
                )
            except RequestError as error:
                self._fail(400, str(error))
                return
            self._send(202, message)

        def do_POST(self) -> None:
            url = urlsplit(self.path)
            parts = [part for part in url.path.split("/") if part]
            if not self._authorized():
                return
            if parts == ["v1", "voice"]:
                self._voice(parse_qs(url.query))
                return
            if parts[:2] == ["v1", "firmware"]:
                self._firmware_post(parts, parse_qs(url.query))
                return
            history = (len(parts) == 4 and parts[:2] == ["v1", "conversations"]
                       and parts[3] == "history")
            ok, body = self._body(MAX_HISTORY_BODY_BYTES if history else MAX_BODY_BYTES)
            if not ok:
                return
            try:
                if history:
                    if not isinstance(body, dict):
                        raise RequestError("请求体应该是一个对象")
                    self._send(200, {"accepted": service.share(parts[2], body.get("turns"))})
                    return
                if parts == ["v1", "messages"]:
                    if not isinstance(body, dict):
                        raise RequestError("请求体应该是一个对象")
                    message = service.submit(
                        body.get("text"), body.get("conversation", "default"),
                        body.get("client_id"), body.get("agent"), body.get("hop", 0),
                        body.get("card"), body.get("pin") is True,
                        body.get("effort"), body.get("model"),
                    )
                    self._send(202, message)
                    return
                if len(parts) == 3 and parts[:2] == ["v1", "approvals"]:
                    if not isinstance(body, dict):
                        raise RequestError("请求体应该是一个对象")
                    answered = service.approve(parts[2], body.get("decision"))
                    if answered is None:
                        self._fail(404, "没有这个授权（那件事可能已经结束了）")
                    elif not answered:
                        self._fail(409, "这个授权已经回答过了")
                    else:
                        self._send(200, {"id": parts[2], "decision": body["decision"]})
                    return
                if len(parts) == 4 and parts[:2] == ["v1", "cards"] and parts[3] == "cancel":
                    card = service.cancel(parts[2])
                    if card is None:
                        self._fail(404, "没有这件事")
                    else:
                        self._send(200, card)
                    return
                if len(parts) == 4 and parts[:2] == ["v1", "cards"] and parts[3] == "artifacts":
                    if not isinstance(body, dict):
                        raise RequestError("请求体应该是一个对象")
                    cards = getattr(service, "card_store", None)
                    if cards is None:
                        cards = service._xiaoyou.cards
                    if cards.get(parts[2]) is None:
                        self._fail(404, "没有这件事")
                        return
                    try:
                        card = artifacts.register_for_card(cards, parts[2], body.get("artifacts"),
                                                           body.get("entry_index"))
                    except ArtifactError as error:
                        self._fail(400, str(error))
                        return
                    self._send(200, card)
                    return
                if len(parts) == 4 and parts[:2] == ["v1", "conversations"] and parts[3] == "reset":
                    self._send(200, {"reset": service.reset(parts[2])})
                    return
            except RequestError as error:
                self._fail(400, str(error))
                return
            self._fail(404, "没有这个地址")

    class Server(ThreadingHTTPServer):
        def handle_error(self, request: Any, client_address: Any) -> None:
            # 客户端自己断开连接（手机切后台、关掉空闲连接）很常见，不是故障：
            # 记一行就够了，不打印整段调用栈。其他异常照常打印。
            error = sys.exc_info()[1]
            if isinstance(error, (ConnectionError, TimeoutError)):
                sys.stderr.write("%s 断开了连接（%s）\n" % (client_address[0], type(error).__name__))
                return
            super().handle_error(request, client_address)

    server = Server((config.host, config.port), Handler)
    server.daemon_threads = True
    # 监听的是 Tailscale 之类的地址时，本机再开一个只给自己用的口子：Tailscale Funnel
    # 这类本机转发进不去自己的 Tailscale 地址，只能转给 127.0.0.1。本机地址外面连不到。
    server.loopback = None
    if config.host not in ("127.0.0.1", "localhost", "::1", "", "0.0.0.0", "::"):
        try:
            server.loopback = Server(("127.0.0.1", config.port), Handler)
            server.loopback.daemon_threads = True
        except OSError as error:
            sys.stderr.write("本机 127.0.0.1:%d 没开成：%s\n" % (config.port, error))
    return server
