"""语音识别：把一段录音变成文字。

Runtime 自己不做识别，只负责把 WAV 文件交给配置里选的引擎：

  none          没有配置；语音消息会被拒绝，并说明怎么配
  sense_voice   本机离线识别，用 sherpa-onnx 运行 SenseVoice 模型（需要另外安装，见 README）
  command       运行一条命令，命令的标准输出就是识别结果

收到的录音固定是 16 位单声道 WAV。
"""

import array
import subprocess
import sys
import threading
import wave
from pathlib import Path
from typing import List, Optional

from .config import Config

MIN_SECONDS = 0.2
MAX_SECONDS = 120.0
MIN_RATE = 8000
MAX_RATE = 48000


class SttError(Exception):
    """识别没成功；消息可以直接给用户看。"""


def describe_wav(path: Path) -> float:
    """检查录音格式，返回时长（秒）。格式不对时抛 SttError。"""
    try:
        with wave.open(str(path), "rb") as audio:
            channels, width = audio.getnchannels(), audio.getsampwidth()
            rate, frames = audio.getframerate(), audio.getnframes()
            compression = audio.getcomptype()
    except (wave.Error, EOFError, OSError) as error:
        raise SttError("录音不是合法的 WAV 文件：%s" % error)
    if compression != "NONE" or channels != 1 or width != 2:
        raise SttError("录音应该是 16 位单声道、未压缩的 WAV")
    if not MIN_RATE <= rate <= MAX_RATE:
        raise SttError("录音的采样率应该在 %d 到 %d 之间" % (MIN_RATE, MAX_RATE))
    seconds = frames / float(rate)
    if seconds < MIN_SECONDS:
        raise SttError("录音太短了")
    if seconds > MAX_SECONDS:
        raise SttError("录音太长了（最多 %d 秒）" % MAX_SECONDS)
    return seconds


class Stt:
    name = "none"

    def transcribe(self, wav: Path) -> str:
        raise SttError(
            "Runtime 还没有配置语音识别。在 config.json 里设置 stt（见 README 的“语音”一节）"
        )


class CommandStt(Stt):
    name = "command"

    def __init__(self, command: List[str], cwd: Path, timeout: int):
        self._command = command
        self._cwd = cwd
        self._timeout = timeout

    def transcribe(self, wav: Path) -> str:
        command = [part.replace("{audio}", str(wav)) for part in self._command]
        try:
            done = subprocess.run(
                command, cwd=str(self._cwd), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=self._timeout,
            )
        except FileNotFoundError:
            raise SttError("找不到语音识别命令 %s" % command[0])
        except subprocess.TimeoutExpired:
            raise SttError("语音识别超过 %d 秒没有结果" % self._timeout)
        if done.returncode != 0:
            detail = done.stderr.decode("utf-8", "replace").strip().splitlines()
            raise SttError(
                "语音识别命令退出码 %d：%s" % (done.returncode, detail[-1] if detail else "没有输出")
            )
        return done.stdout.decode("utf-8", "replace").strip()


class SenseVoiceStt(Stt):
    """进程内运行 SenseVoice。模型在第一次识别时加载（几秒），之后常驻。"""

    name = "sense_voice"

    def __init__(self, model_dir: Path, language: str, threads: int):
        self._model_dir = model_dir
        self._language = language
        self._threads = threads
        self._recognizer = None
        self._lock = threading.Lock()

    def _model_file(self) -> Path:
        for name in ("model.int8.onnx", "model.onnx"):
            if (self._model_dir / name).is_file():
                return self._model_dir / name
        raise SttError("在 %s 里找不到 model.int8.onnx 或 model.onnx" % self._model_dir)

    def check(self) -> None:
        """只检查文件和依赖在不在，不加载模型。"""
        self._model_file()
        if not (self._model_dir / "tokens.txt").is_file():
            raise SttError("在 %s 里找不到 tokens.txt" % self._model_dir)
        try:
            import sherpa_onnx  # noqa: F401
        except ImportError:
            raise SttError(
                "没有安装 sherpa-onnx。用运行 Runtime 的同一个 Python 执行："
                "%s -m pip install sherpa-onnx" % sys.executable
            )

    def _load(self):
        if self._recognizer is None:
            self.check()
            import sherpa_onnx

            self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=str(self._model_file()),
                tokens=str(self._model_dir / "tokens.txt"),
                num_threads=self._threads,
                use_itn=True,
                language=self._language,
            )
        return self._recognizer

    def transcribe(self, wav: Path) -> str:
        with wave.open(str(wav), "rb") as audio:
            rate = audio.getframerate()
            samples = array.array("h")
            samples.frombytes(audio.readframes(audio.getnframes()))
        if sys.byteorder == "big":
            samples.byteswap()
        with self._lock:
            try:
                recognizer = self._load()
                stream = recognizer.create_stream()
                stream.accept_waveform(rate, [value / 32768.0 for value in samples])
                recognizer.decode_stream(stream)
                return stream.result.text.strip()
            except SttError:
                raise
            except Exception as error:  # 识别库的错误类型不固定
                raise SttError("语音识别出错：%s: %s" % (type(error).__name__, error))


def create(config: Config) -> Stt:
    if config.stt_engine == "command":
        return CommandStt(config.stt_command, config.base_dir, config.stt_timeout_seconds)
    if config.stt_engine == "sense_voice":
        assert config.stt_model_dir is not None
        return SenseVoiceStt(config.stt_model_dir, config.stt_language, config.stt_threads)
    return Stt()


def check(stt: Stt) -> Optional[str]:
    """启动时的检查：有问题返回说明，没问题返回 None。"""
    if isinstance(stt, SenseVoiceStt):
        try:
            stt.check()
        except SttError as error:
            return str(error)
    return None
