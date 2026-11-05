"""Optional channel worker; owner selection and credentials are external.

Two connections keep a long inbound poll from delaying a committed reply.
This module is not automatically started or exposed by Runtime.
"""
import json
import threading
import time
from .weixin import WeixinPausedError


class ChannelWorker:
    def __init__(self, gateway_factory, transport_factory, service, destination, *, authorized, status=None):
        if not callable(authorized):
            raise ValueError('channel ownership authority is required')
        self._gateway_factory = gateway_factory
        self._transport_factory = transport_factory
        self._service = service
        self._destination = destination
        self._status = status or (lambda _: None)
        self._authorized = authorized
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._threads = []
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if any(thread.is_alive() for thread in self._threads):
                raise RuntimeError('channel worker already running or stopping')
            self._stop.clear()
            self._ready.clear()
            self._threads = [threading.Thread(target=self._input, daemon=True, name='channel-input'),
                             threading.Thread(target=self._output, daemon=True, name='channel-output')]
            try:
                for thread in self._threads:
                    thread.start()
            except BaseException:
                # Starting the second thread can fail after the first has
                # begun. The caller retains the host lock until stop succeeds.
                self._stop.set()
                self._ready.set()
                raise

    def stop(self, timeout=1):
        self._stop.set()
        deadline = time.monotonic() + max(0, timeout)
        for thread in self._threads:
            if thread.ident is not None:
                thread.join(timeout=max(0, deadline - time.monotonic()))
        # An in-flight network request may still finish. Callers must not
        # delete credentials or start another worker until this returns True.
        return not any(thread.is_alive() for thread in self._threads)

    def _emit(self, state):
        try:
            self._status(state)
        except Exception:
            pass

    def _allowed(self):
        try:
            allowed = self._authorized() is True
        except Exception:
            allowed = False
        if not allowed:
            self._emit('ownership_unavailable')
            self._stop.set()
        return allowed

    def _input(self):
        gateway = None
        delay = 1
        try:
            gateway = self._gateway_factory()
            transport = self._transport_factory()
            # Establish the sync cut before accepting new channel input. If a
            # reply completes before an initial snapshot, it could be skipped.
            while not self._stop.is_set() and not self._ready.wait(0.5):
                pass
            while not self._stop.is_set():
                try:
                    if not self._allowed():
                        break
                    started = time.monotonic()
                    count = gateway.poll_once(transport, cancelled=lambda: self._stop.is_set() or not self._allowed())
                    if not self._stop.is_set():
                        self._emit('input_unavailable' if getattr(gateway, 'intake_problem', False) else 'connected')
                    if not count and time.monotonic() - started < 0.5:
                        # Some failures return an empty long-poll immediately.
                        # Avoid a tight loop; normal held polls pay no delay.
                        self._stop.wait(1)
                    delay = 1
                except WeixinPausedError:
                    self._emit('input_paused')
                    self._stop.wait(3600)
                    delay = 1
                except Exception:
                    # Do not log remote error text: it may contain credentials.
                    self._emit('input_unavailable')
                    self._stop.wait(delay)
                    delay = min(delay * 2, 60)
        except Exception:
            self._emit('input_unavailable')
        finally:
            if gateway is not None:
                gateway.store.close()

    def _output(self):
        gateway = None
        delay = 1
        recovered = False
        try:
            gateway = self._gateway_factory()
            transport = self._transport_factory()
            while not self._stop.is_set():
                try:
                    if not self._allowed():
                        break
                    if not recovered:
                        # The caller must provide exclusive ownership before
                        # starting the worker. A prior process may have died
                        # after sending but before recording the result.
                        gateway.store.recover_interrupted(self._destination)
                        recover_shares = getattr(gateway.store, 'recover_proactive_shares', None)
                        if callable(recover_shares):
                            recover_shares()
                        recovered = True
                    saved = gateway.store.cursor('sync:' + self._destination)
                    if not saved:
                        snapshot = self._service.sync('weixin', states=['weixin'])
                        if not snapshot.get('seq'):
                            raise ValueError('channel sync boundary unavailable')
                        gateway.bootstrap_sync(self._destination, snapshot)
                        continue
                    runtime, epoch, seq = json.loads(saved)
                    if not seq:
                        raise ValueError('channel sync boundary unavailable')
                    if runtime != self._service.runtime_id:
                        raise ValueError('channel sync source changed')
                    self._ready.set()
                    if gateway.flush_delivery(self._destination):
                        self._emit('delivery_reconciliation_required')
                    while not self._stop.is_set():
                        if not self._allowed():
                            break
                        state = gateway.send_next(transport, self._destination)
                        if state == 'paced':
                            # Keep the wait cancellable and re-check authority;
                            # it must not delay input polling on the other thread.
                            self._stop.wait(min(0.5, max(0.01, gateway.pacing_delay(self._destination))))
                            continue
                        if gateway.flush_delivery(self._destination):
                            self._emit('delivery_reconciliation_required')
                        if state == 'idle':
                            break
                        if state not in ('sent', 'cancelled'):
                            self._emit('delivery_' + state)
                            break
                    if self._stop.is_set():
                        break
                    # Shares have a separate durable journal and cannot hold up
                    # normal replies if their delivery fails or is uncertain.
                    send_share = getattr(gateway, 'send_proactive_share', None)
                    config = getattr(getattr(self._service, '_xiaoyu', None), '_config', None)
                    if callable(send_share) and getattr(config, 'bilibili_weixin_shares', False) and self._allowed():
                        send_link = getattr(gateway, 'send_proactive_link', None)
                        link_state = send_link(transport, self._destination) if callable(send_link) else 'idle'
                        if link_state == 'idle' and not self._stop.is_set() and self._allowed():
                            state = send_share(transport, self._destination)
                            if state == 'sent' and callable(send_link) and not self._stop.is_set() and self._allowed():
                                send_link(transport, self._destination)
                    started = time.monotonic()
                    snapshot = self._service.sync('weixin', after=seq, epoch=epoch,
                                                  wait=10, states=['weixin'])
                    if self._stop.is_set():
                        break
                    gateway.apply_sync(self._destination, snapshot)
                    if gateway.flush_delivery(self._destination):
                        self._emit('delivery_reconciliation_required')
                    if snapshot.get('seq') == seq and time.monotonic() - started < 0.5:
                        self._stop.wait(1)
                    delay = 1
                except Exception:
                    self._emit('output_unavailable')
                    self._stop.wait(delay)
                    delay = min(delay * 2, 60)
        except Exception:
            self._emit('output_unavailable')
        finally:
            if gateway is not None:
                gateway.store.close()
