"""Runtime 开着时，隔一会儿看看要不要跑一轮自动迭代。

这里只判断"现在方便不方便"（iterate.enabled、主人那边有没有正在做的事），真正跑一轮放在
单独的子进程里（python3 -m xiaoyou_runtime iterate run）：它崩了、卡住了都不影响 Runtime，
也不占发消息的路。信号够不够、时间到没到由那一轮自己判断。
"""

import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Optional

CHECK_SECONDS = 15 * 60
RUN_TIMEOUT_SECONDS = 3600
BUSY_STATES = ("working", "waiting")


def idle(cards: Any) -> bool:
    for card in cards.recent(None, 50):
        if card.get("state") in BUSY_STATES and not card.get("monitoring_only"):
            return False
    return True


class Scheduler:
    def __init__(self, config_path: Path, settings: Any, cards: Any,
                 launch: Optional[Callable[[list], Any]] = None, interval: float = CHECK_SECONDS):
        self._command = [sys.executable, "-m", "xiaoyou_runtime", "--config", str(config_path),
                         "iterate", "run"]
        self._settings, self._cards = settings, cards
        self._launch = launch or self._spawn
        self._interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._child: Optional[subprocess.Popen] = None

    def _spawn(self, command: list) -> subprocess.Popen:
        return subprocess.Popen(command, cwd=str(Path(__file__).resolve().parents[2]),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def tick(self) -> bool:
        """该跑就起一个子进程；返回这次有没有起。"""
        if not self._settings.enabled:
            return False
        if self._child is not None and self._child.poll() is None:
            return False  # 上一轮还在跑
        if self._settings.only_when_idle and not idle(self._cards):
            return False
        self._child = self._launch(self._command)
        return True

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.tick()
            except Exception as error:  # 调度出错不影响 Runtime
                print("自动迭代没能启动：%s" % error, file=sys.stderr)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="iterate", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
