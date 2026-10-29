"""One serialized model lane per conversation, shared by all wake sources.

Queues contain references to already saved requests or callbacks that recheck
their saved source before doing work. This scheduler never replays an effect.
"""

import sys
import threading
from collections import deque


class ConversationLoop:
    def __init__(self, changed, handle, parallel=4):
        # The same lock guards Service admission and owner handoff; a queued
        # wake cannot be mistaken for an idle conversation during handoff.
        self._changed, self._handle = changed, handle
        self.lanes, self.busy = {}, {}
        self._slots = threading.Semaphore(parallel)
        self._closed = False

    def enqueue(self, conversation, item):
        with self._changed:
            if self._closed:
                return False
            lane = self.lanes.setdefault(conversation, deque())
            lane.append(item)
            if conversation not in self.busy:
                worker = threading.Thread(target=self._drain, args=(conversation,),
                                          name="xiaoyu-%s" % conversation, daemon=True)
                self.busy[conversation] = worker
                try:
                    worker.start()
                except Exception:
                    self.busy.pop(conversation, None)
                    lane.pop()
                    if not lane:
                        self.lanes.pop(conversation, None)
                    raise
            return True

    def stop(self):
        with self._changed:
            self._closed = True
            return list(self.busy.values())

    def _drain(self, conversation):
        with self._slots:
            while True:
                with self._changed:
                    lane = self.lanes.get(conversation)
                    if self._closed or not lane:
                        self.lanes.pop(conversation, None)
                        self.busy.pop(conversation, None)
                        self._changed.notify_all()
                        return
                    item = lane.popleft()
                try:
                    if isinstance(item, str):
                        self._handle(item)
                    else:
                        item()
                except Exception as error:
                    sys.stderr.write("Conversation loop work failed: %s: %s\n" % (type(error).__name__, error))
