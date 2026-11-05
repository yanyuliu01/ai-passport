"""Coordinate a channel host's reservation and local QR binding.

This internal component does not expose HTTP endpoints or start itself. The
caller must authenticate the local computer before allowing a binding.
"""
import threading
from pathlib import Path

from .authority_client import AuthorityUnavailable
from .controller import Controller
from .host_grant import HostGrant


class ChannelSession:
    def __init__(self, service, *, grant=None, controller=None, timeout=5):
        self.service = service
        self.grant = grant or HostGrant(service)
        self.controller = controller or Controller(service, service._store.state_dir,
                                                   host_granted=self.grant.allowed)
        self.timeout = timeout
        self._lock = threading.RLock()
        self._operation = None
        self._phase = 'unbound'
        self._attempt = None
        self._rescan = False
        self._needs_recovery = (Path(service._store.state_dir) / 'channels' / 'weixin').exists()

    @property
    def needs_recovery(self):
        return self._needs_recovery

    def status(self):
        """A local view only; never publish QR contents or account credentials."""
        with self._lock:
            phase = self._phase
            state = self.controller.status().get('state')
            if phase == 'active':
                visible = 'connected' if state == 'connected' else 'unreachable'
            elif phase in ('binding', 'activating'):
                visible = 'binding'
            elif phase in ('unbound', 'reserved') and not self._needs_recovery:
                visible = 'unbound'
            else:
                visible = 'unreachable'
            return {'state': visible, 'local': phase in ('active', 'binding', 'activating'),
                    'reason': 'service' if visible == 'unreachable' else None}

    def public_status(self):
        """A bounded, nonblocking observation for the shared sync path."""
        if not self._lock.acquire(blocking=False):
            return {'state': 'unknown'}
        try:
            if not self.controller._lock.acquire(blocking=False):
                return {'state': 'unknown'}
            try:
                value = self.status()
                return {'state': value['state'] if value['local'] else 'unknown'}
            finally:
                self.controller._lock.release()
        finally:
            self._lock.release()

    def retry_delivery(self, block_id, operation, expected_revision, *, confirm_unknown=False):
        with self._lock:
            if self._phase != 'active':
                raise ValueError('微信连接暂不可用')
            return self.controller.retry_delivery(block_id, operation, expected_revision, confirm_unknown=confirm_unknown)

    def _command(self, name, *args):
        if self._operation is not None and self._operation != name:
            raise AuthorityUnavailable('微信渠道操作仍在核对')
        if self._operation is None:
            getattr(self.grant, name)(*args)
            self._operation = name
        result = self.grant.resolve(timeout=self.timeout)
        status = result.get('status')
        if status in ('queued', 'unknown'):
            raise AuthorityUnavailable('微信渠道操作仍在核对，请稍后重试')
        self._operation = None
        if status != 'applied':
            raise AuthorityUnavailable('微信渠道负责人未确认')
        return result

    def begin(self):
        with self._lock:
            if self._needs_recovery or self._phase in ('recovering', 'recover_activating'):
                self.recover()
            if self._phase in ('revoking', 'releasing'):
                self._release_reservation()
            if self._phase not in ('unbound', 'reserved', 'active'):
                raise ValueError('微信连接操作正在进行')
            self._rescan = self._phase == 'active'
            if self._phase == 'unbound':
                prepare = getattr(self.service, 'prepare_local_channel_authority', None)
                if prepare:
                    prepare()
                self._command('reserve')
                self._phase = 'reserved'
            if not self._rescan:
                self.grant.prepare_host()
            attempt = self.controller.begin()
            self._attempt = attempt['id']
            self._phase = 'binding'
            return attempt

    def poll(self, attempt, verify_code=None):
        with self._lock:
            if attempt != self._attempt or self._phase not in ('binding', 'activating'):
                raise ValueError('扫码请求已失效')
            needs_poll = self._phase == 'binding'
        # A held upstream poll must not prevent the local user cancelling.
        result = self.controller.poll(attempt, verify_code) if needs_poll else {'state': 'confirmed'}
        with self._lock:
            if attempt != self._attempt or self._phase not in ('binding', 'activating'):
                raise ValueError('扫码请求已失效')
            if self._phase == 'binding':
                if result.get('state') != 'confirmed':
                    return result
                if self._rescan:
                    self._phase, self._attempt = 'active', None
                    return result
                # Credentials and worker are already durable/started. Do not
                # repeat QR confirmation if the authority response is delayed.
                self._phase = 'activating'
            self._command('activate')
            self._phase, self._attempt = 'active', None
            return {'state': 'confirmed'}

    def recover(self):
        """Reconfirm durable authority before reusing any saved credentials."""
        with self._lock:
            if self._phase not in ('unbound', 'recovering', 'recover_activating'):
                raise ValueError('微信连接操作正在进行')
            if self._phase != 'recover_activating':
                self._phase = 'recovering'
                prepare = getattr(self.service, 'prepare_local_channel_authority', None)
                if prepare:
                    prepare()
                if not self.grant.client.recovery_safe():
                    prior = self.grant.reconcile(timeout=self.timeout)
                    if prior.get('status') not in ('clear', 'applied', 'refused'):
                        raise AuthorityUnavailable('上次微信渠道操作仍在核对')
                result = self._command('recover')
                state = result.get('channel_authority') or {}
                if state.get('state') == 'unbound':
                    self._phase = 'unbound'
                    self._needs_recovery = False
                    return {'state': 'unbound'}
                if state.get('host') != self.service.runtime_id:
                    self._phase = 'external'
                    self._needs_recovery = False
                    return {'state': 'external'}
                if state.get('state') == 'revoking':
                    self._phase = 'revoking'
                    self._needs_recovery = False
                    return {'state': 'revoking'}
                if state.get('state') not in ('reserved', 'active'):
                    raise AuthorityUnavailable('微信渠道状态需要核对')
                # Once a grant is installed, repeating recover would attempt a
                # second acquisition. Keep failures closed for reconciliation.
                self._phase = 'recovery_blocked'
                self.grant.prepare_host()
                if not self.controller.resume():
                    if state['state'] == 'reserved':
                        self._phase = 'reserved'
                        self._needs_recovery = False
                        return {'state': 'reserved'}
                    self._phase = 'credentials_missing'
                    raise AuthorityUnavailable('微信连接凭据不可用，需要核对')
                if state['state'] == 'active':
                    self._phase = 'active'
                    self._needs_recovery = False
                    return {'state': 'active'}
                self._phase = 'recover_activating'
            self._command('activate')
            self._phase = 'active'
            self._needs_recovery = False
            return {'state': 'active'}

    def cancel(self, attempt):
        with self._lock:
            if self._phase == 'binding':
                if attempt != self._attempt:
                    return False
                self.controller.cancel(attempt)
                self._attempt = None
                if self._rescan:
                    self._phase = 'active'
                    return True
                self._phase = 'reserved'
            if self._phase in ('reserved', 'revoking', 'releasing'):
                self._release_reservation()
                return True
            return False

    def _release_reservation(self):
        if self._phase in ('reserved', 'revoking'):
            self._phase = 'revoking'
            self._command('begin_release')
            self._phase = 'releasing'
        if self._phase == 'releasing':
            self._command('release', self.controller)
            if not self.controller.close(timeout=self.timeout):
                raise AuthorityUnavailable('微信连接仍在停止')
            self.controller = Controller(self.service, self.service._store.state_dir,
                                         host_granted=self.grant.allowed)
            self._phase = 'unbound'

    def shutdown(self, timeout=0):
        """Stop locally while retaining the durable reservation for recovery."""
        with self._lock:
            if self._phase == 'unbound' and self._operation is None:
                return True
            return self.grant.shutdown(self.controller, timeout=timeout)
