"""One monotonic deadline shared by provider preparation and retry attempts."""

import time

from ..errors import AgentError


class DeadlineExceeded(AgentError):
    """Only deadline expiry, distinct from an uncertain tool/effect failure."""


class Deadline:
    def __init__(self, seconds, *, clock=None):
        self._clock = clock or time.monotonic
        self._ends = self._clock() + seconds

    def remaining(self):
        remaining = self._ends - self._clock()
        if remaining <= 0:
            raise DeadlineExceeded("回复已达到总时限，不再等待或重试")
        return remaining
