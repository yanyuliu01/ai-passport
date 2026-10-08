"""收消息、排队、一条一条交给后端，并让调用方能等到结果。

语音消息多一步：先把录音交给语音识别，得到文字后和打字的消息走同一条路。

所有消息排成一队、由一个工作线程顺序处理：同一个对话里的两句话不会同时去续同一个
会话。验证阶段只有一个用户，这样最简单也最不容易出错。
"""

import queue
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Dict, Optional

from .backends import Backend, BackendError
from .store import Store
from .stt import Stt, SttError, describe_wav

MAX_TEXT_CHARS = 8000
MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_KEPT_MESSAGES = 200
MAX_NAME_CHARS = 64


class RequestError(Exception):
    """调用方给的内容不对；消息可以直接返回给调用方。"""


def _name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_NAME_CHARS:
        raise RequestError("%s 应该是 1 到 %d 个字符的字符串" % (what, MAX_NAME_CHARS))
    if any(ch.isspace() or ch in "/\\" or ord(ch) < 0x20 for ch in value):
        raise RequestError("%s 不能包含空白或斜杠" % what)
    return value


class Service:
    def __init__(self, backend: Backend, store: Store, stt: Optional[Stt] = None):
        self._backend = backend
        self._store = store
        self._stt = stt if stt is not None else Stt()
        self._audio: Dict[str, Any] = {}  # 消息编号 → 还没识别的录音文件
        self._changed = threading.Condition()
        self._messages = OrderedDict()  # type: OrderedDict[str, Dict[str, Any]]
        self._by_client_id: Dict[str, str] = {}
        self._queue = queue.Queue()  # type: queue.Queue[Optional[str]]
        self._worker = threading.Thread(target=self._work, name="xiaoyou-worker", daemon=True)
        self._worker.start()

    def submit(self, text: Any, conversation: Any = "default", client_id: Any = None) -> Dict[str, Any]:
        """登记一条消息并立刻返回；client_id 相同的重复提交返回同一条，不会重做。"""
        if not isinstance(text, str) or not text.strip():
            raise RequestError("text 不能为空")
        if len(text) > MAX_TEXT_CHARS:
            raise RequestError("text 不能超过 %d 个字符" % MAX_TEXT_CHARS)
        return self._enqueue("text", text, None, conversation, client_id)

    def submit_voice(self, audio: Any, conversation: Any = "default",
                     client_id: Any = None) -> Dict[str, Any]:
        """登记一条语音消息：audio 是 16 位单声道 WAV 的全部字节。识别在排队处理时进行。"""
        if not isinstance(audio, (bytes, bytearray)) or not audio:
            raise RequestError("录音是空的")
        if len(audio) > MAX_AUDIO_BYTES:
            raise RequestError("录音不能超过 %d MB" % (MAX_AUDIO_BYTES // (1024 * 1024)))
        if self._stt.name == "none":
            raise RequestError(
                "Runtime 还没有配置语音识别。在 config.json 里设置 stt（见 README 的“语音”一节）"
            )
        return self._enqueue("voice", "", bytes(audio), conversation, client_id)

    def _enqueue(self, kind: str, text: str, audio: Optional[bytes], conversation: Any,
                 client_id: Any) -> Dict[str, Any]:
        conversation = _name(conversation, "conversation")
        if client_id is not None:
            client_id = _name(client_id, "client_id")
        with self._changed:
            if client_id is not None and client_id in self._by_client_id:
                return dict(self._messages[self._by_client_id[client_id]])
            message_id = uuid.uuid4().hex
            if audio is not None:
                # 先落盘再登记：格式不对的录音直接拒绝，不进队列。
                path = self._store.voice_file(message_id)
                path.write_bytes(audio)
                try:
                    describe_wav(path)
                except SttError as error:
                    path.unlink()
                    raise RequestError(str(error))
                self._audio[message_id] = path
            message = {
                "id": message_id,
                "client_id": client_id,
                "conversation": conversation,
                "kind": kind,
                "status": "queued",
                "text": text,
                "reply": None,
                "brief": None,
                "mood": "busy",
                "error": None,
                "created_at": time.time(),
                "finished_at": None,
            }
            self._messages[message["id"]] = message
            if client_id is not None:
                self._by_client_id[client_id] = message["id"]
            self._trim()
            snapshot = dict(message)
        self._queue.put(snapshot["id"])
        return snapshot

    def get(self, message_id: str, wait: float = 0.0) -> Optional[Dict[str, Any]]:
        """查一条消息；wait 大于 0 时最多等这么多秒，直到它处理完。"""
        deadline = time.monotonic() + max(0.0, wait)
        with self._changed:
            while True:
                message = self._messages.get(message_id)
                if message is None:
                    return None
                remaining = deadline - time.monotonic()
                if message["status"] in ("done", "failed") or remaining <= 0:
                    return dict(message)
                self._changed.wait(remaining)

    def reset(self, conversation: Any) -> bool:
        """忘掉一个对话的会话编号：下一句话会开一个新会话。"""
        return self._store.forget(_name(conversation, "conversation"))

    def close(self) -> None:
        self._queue.put(None)
        self._worker.join(timeout=5)

    def _trim(self) -> None:
        # 只丢已经处理完的旧消息，还在排队的不能丢。
        for message_id in list(self._messages):
            if len(self._messages) <= MAX_KEPT_MESSAGES:
                break
            old = self._messages[message_id]
            if old["status"] in ("done", "failed"):
                del self._messages[message_id]
                if old["client_id"] is not None:
                    self._by_client_id.pop(old["client_id"], None)

    def _update(self, message_id: str, **fields: Any) -> None:
        with self._changed:
            message = self._messages.get(message_id)
            if message is not None:
                message.update(fields)
            self._changed.notify_all()

    def _work(self) -> None:
        while True:
            message_id = self._queue.get()
            if message_id is None:
                return
            with self._changed:
                message = self._messages.get(message_id)
                if message is None:
                    continue
                text, conversation = message["text"], message["conversation"]
                audio = self._audio.pop(message_id, None)
            if audio is not None:
                self._update(message_id, status="transcribing")
                try:
                    text = self._stt.transcribe(audio)
                    if not text:
                        raise SttError("没听清，再说一次吧")
                except SttError as error:
                    self._update(
                        message_id, status="failed", error=str(error), mood="oops",
                        finished_at=time.time(),
                    )
                    continue
                except Exception as error:
                    self._update(
                        message_id, status="failed", mood="oops", finished_at=time.time(),
                        error="语音识别出错：%s: %s" % (type(error).__name__, error),
                    )
                    continue
                finally:
                    try:
                        audio.unlink()
                    except OSError:
                        pass
                text = text[:MAX_TEXT_CHARS]
            self._update(message_id, status="running", text=text)
            try:
                turn = self._backend.turn(text, self._store.session(conversation))
                if turn.session_id:
                    self._store.remember(conversation, turn.session_id)
                self._update(
                    message_id, status="done", reply=turn.reply, brief=turn.brief,
                    mood=turn.mood, finished_at=time.time(),
                )
            except BackendError as error:
                self._update(
                    message_id, status="failed", error=str(error), mood="oops",
                    finished_at=time.time(),
                )
            except Exception as error:  # 工作线程不能死：记下来，继续处理下一条
                self._update(
                    message_id, status="failed", mood="oops", finished_at=time.time(),
                    error="Runtime 内部出错：%s: %s" % (type(error).__name__, error),
                )
