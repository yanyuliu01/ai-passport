"""Orchestrate one locally hosted channel after an external ownership grant.

The caller must implement authenticated local access and durable multi-node
ownership arbitration. This class is not automatically enabled or exposed.
"""
import threading
import time
import re
from pathlib import Path

from .binding import Binding
from .credentials import Credentials
from .gateway import Gateway
from .host_lock import HostLock
from .store import ChannelStore
from .weixin import WeixinClient
from .worker import ChannelWorker


class Controller:
    def __init__(self, service, state_dir, *, host_granted, client_factory=None):
        if not callable(host_granted):
            raise ValueError('channel ownership authority is required')
        self.service = service
        self.root = Path(state_dir) / 'channels' / 'weixin'
        self.credentials = Credentials(self.root / 'credentials.json')
        self._host_lock = HostLock(self.root / 'host.lock')
        self._host_granted = host_granted
        self._client_factory = client_factory or (lambda **kw: WeixinClient(**kw))
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._closing = False
        self._closed = False
        self._inflight = 0
        self._resume_after_cancel = False
        self._binding = None
        self._worker = None
        self._state = 'unbound'
        self._reason = None
        self._delivery_reconciliation_required = False

    def _granted(self):
        # No timeout, failed lookup or unknown authority counts as a grant.
        if self._host_granted() is not True:
            raise ValueError('微信渠道负责人尚未确认')

    def status(self):
        with self._lock:
            return {'state': self._state, 'reason': self._reason,
                    'delivery_reconciliation_required': self._delivery_reconciliation_required}

    def retry_delivery(self, block_id, operation, expected_revision, *, confirm_unknown=False):
        """Record one explicitly requested retry while the host grant is held."""
        prefix = self.service.runtime_id + '/'
        if (not isinstance(block_id, str) or len(block_id) > 512 or not block_id.startswith(prefix)
                or not re.fullmatch(r'[A-Za-z0-9._:-]+#(?:[0-9]{1,8}|media:a[0-9a-f]{32})', block_id[len(prefix):])):
            raise ValueError('微信回复编号无效')
        with self._condition:
            if self._closing or self._worker is None:
                raise ValueError('微信连接暂不可用')
            self._granted()
            self._inflight += 1
        try:
            store = ChannelStore(self.root / 'channel.sqlite')
            try:
                return store.retry_once('weixin', block_id, operation, expected_revision, confirm_unknown=confirm_unknown)
            finally:
                store.close()
        finally:
            self._finish_operation()

    def resume(self):
        """Resume saved credentials only after the caller grants exclusive ownership."""
        self._granted()
        with self._lock:
            if self._closing:
                raise ValueError('微信连接正在停止')
            if self._worker is not None or self._binding is not None:
                raise ValueError('微信连接已启动或正在扫码')
            if not self._host_lock.acquire():
                raise ValueError('这台电脑已有微信渠道进程在运行')
            try:
                credentials = self.credentials.load()
                if not credentials:
                    self._host_lock.release()
                    return False
                channel = self.service._owner_writer.conversation_create_weixin()
                if channel.get('owner') != self.service.runtime_id:
                    raise ValueError('微信渠道不属于这台电脑')
                self._granted()
                self._launch(credentials)
                self._state, self._reason = 'connecting', None
                return True
            except BaseException:
                # A partially started worker can still have network calls in
                # flight. Keep its lock until close() confirms it stopped.
                if self._worker is None:
                    self._host_lock.release()
                raise

    def begin(self):
        self._granted()
        with self._lock:
            if self._closing:
                raise ValueError('微信连接正在停止')
            if self._worker is not None and not self._worker.stop(timeout=0):
                raise ValueError('微信连接正在停止，请稍后重试')
            self._worker = None
            if not self._host_lock.acquire():
                raise ValueError('这台电脑已有微信渠道进程在运行')
            try:
                prior = self.credentials.load()
                self._resume_after_cancel = False
                self._binding = Binding(self._client_factory(), expected_owner=prior['owner'] if prior else None)
                self._state, self._reason = 'binding', None
                binding = self._binding
                self._inflight += 1
            except BaseException:
                if not self._inflight and self._binding is None and self._worker is None:
                    self._host_lock.release()
                raise
        try:
            result = binding.start()
            with self._lock:
                if self._binding is not binding:
                    raise ValueError('扫码请求已取消')
                self._granted()
                return result
        except BaseException:
            with self._lock:
                if self._binding is binding:
                    self._binding = None
                    self._state, self._reason = 'unbound', None
            raise
        finally:
            self._finish_operation()

    def _finish_operation(self):
        with self._condition:
            self._inflight -= 1
            if not self._inflight and self._resume_after_cancel and not self._closing:
                self._resume_cancelled_binding()
            if not self._inflight and self._binding is None and self._worker is None and not self._closing:
                self._host_lock.release()
            self._condition.notify_all()

    def _resume_cancelled_binding(self):
        """Restart the prior account only after the cancelled QR call returns."""
        self._resume_after_cancel = False
        try:
            self._granted()
            credentials = self.credentials.load()
            if credentials is None:
                self._state, self._reason = 'unbound', None
                return
            self._launch(credentials)
            self._state, self._reason = 'connecting', None
        except Exception:
            self._state, self._reason = 'unreachable', 'ownership_unavailable'

    def cancel(self, attempt_id):
        with self._lock:
            if self._binding and self._binding.cancel(attempt_id):
                self._binding = None
                try:
                    self._resume_after_cancel = self.credentials.load() is not None
                except ValueError:
                    self._resume_after_cancel = False
                    self._state, self._reason = 'unreachable', 'credentials_unavailable'
                else:
                    self._state, self._reason = ('connecting', None) if self._resume_after_cancel else ('unbound', None)
                if not self._inflight:
                    if self._resume_after_cancel:
                        self._resume_cancelled_binding()
                    if self._worker is None:
                        self._host_lock.release()
                return True
            return False

    def poll(self, attempt_id, verify_code=None):
        with self._lock:
            if self._closing:
                raise ValueError('微信连接正在停止')
            binding = self._binding
            if binding is None:
                raise ValueError('扫码请求已失效')
            self._inflight += 1
        try:
            return self._poll(binding, attempt_id, verify_code)
        finally:
            self._finish_operation()

    def _poll(self, binding, attempt_id, verify_code):
        result = binding.poll(attempt_id, verify_code)
        with self._lock:
            if self._binding is not binding:
                raise ValueError('扫码请求已取消')
            if result['state'] != 'confirmed':
                return result
            self._granted()
            # Create the channel through the existing record writer. A failure
            # or changed owner stops before credentials or workers are used.
            channel = self.service._owner_writer.conversation_create_weixin()
            if channel.get('owner') != self.service.runtime_id:
                raise ValueError('微信渠道不属于这台电脑')
            self._granted()
            binding.persist_credentials(attempt_id, self.credentials.save)
            self._granted()
            credentials = self.credentials.load()
            self._launch(credentials)
            self._binding = None
            self._state, self._reason = 'connecting', None
            return {'state': 'confirmed'}

    def _launch(self, credentials):
        if not credentials:
            raise ValueError('微信凭据不可用')
        account, owner = credentials['bot'], credentials['owner']
        database = self.root / 'channel.sqlite'
        # Factories run inside their owning threads; sqlite connections are not shared.
        gateway = lambda: Gateway(ChannelStore(database), self.service, 'weixin', account, owner)
        transport = lambda: self._client_factory(token=credentials['token'], base=credentials['base'])
        self._worker = ChannelWorker(gateway, transport, self.service, 'weixin',
                                     authorized=self._host_granted, status=self._changed)
        self._worker.start()

    def _changed(self, state):
        with self._lock:
            # The callback carries fixed state labels only, never server errors.
            if state == 'connected':
                self._state, self._reason = 'connected', None
            elif state == 'delivery_reconciliation_required':
                # A later healthy input poll must not hide an unresolved
                # status projection. It remains sticky until controller restart.
                self._delivery_reconciliation_required = True
            elif state in ('input_unavailable', 'output_unavailable', 'input_paused', 'ownership_unavailable'):
                self._state, self._reason = 'unreachable', state

    def prepare_release(self, timeout=0):
        """Stop and drain the host before acknowledging an ownership release.

        Keep the local process lock held. The caller must confirm durable
        revocation with the authority before calling close(). A timeout never
        proves that a network effect stopped.
        """
        deadline = time.monotonic() + max(0, timeout)
        with self._condition:
            if self._closed:
                return False
            self._closing = True
            self._resume_after_cancel = False
            # A newly created controller cannot attest that the host stopped
            # while another process still holds this host's exclusive lock.
            if not self._host_lock.acquire():
                return False
            if self._state != 'unbound':
                self._state, self._reason = 'unreachable', 'ownership_unavailable'
            self._binding = None
            worker = self._worker
        stopped = worker is None or worker.stop(timeout=max(0, deadline - time.monotonic()))
        with self._condition:
            while self._inflight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            if not stopped:
                return False
            database = self.root / 'channel.sqlite'
            if database.exists():
                # A prior process can die after starting a send. Hold the host
                # lock and record the ambiguity before reporting it stopped.
                store = ChannelStore(database, timeout=0)
                try:
                    store.recover_interrupted('weixin')
                finally:
                    store.close()
            return True

    def close(self, timeout=0):
        with self._lock:
            if self._closed:
                return True
        if not self.prepare_release(timeout):
            return False
        with self._lock:
            self._worker = None
            self._host_lock.release()
            self._closed = True
            return True
