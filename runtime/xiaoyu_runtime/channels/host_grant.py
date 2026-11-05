"""A local, event-driven channel grant tied to the enrolled mesh authority.

Grants are deliberately not restored from disk. Restart recovery requires a
clean operation journal and a fresh confirmation from the authority.
"""
import threading

from .authority_client import AuthorityClient, AuthorityUnavailable
from .host_lock import HostLock


class HostGrant:
    def __init__(self, service, client=None, host_lock=None):
        self.service = service
        self.client = client or AuthorityClient(service)
        self._host_lock = host_lock or HostLock(service._store.state_dir / 'channels' / 'weixin' / 'grant.lock')
        self._lock = threading.RLock()
        self._grant = None
        self._pending = None
        self._revoking = False
        self._release_authorized = False
        self._closing = False
        self._closed = False
        self._reconciling = False

    def allowed(self):
        """Cheap local check used by the worker; never performs network I/O."""
        with self._lock:
            grant = dict(self._grant) if self._grant is not None else None
            revoking = self._revoking or self._closing or self._closed
        if grant is None or revoking:
            return False
        try:
            mesh, authority = self.client._authority()
            return (authority == grant['authority'] and grant['host'] == self.service.runtime_id
                    and self.client._scope(mesh, authority) == grant.get('_scope'))
        except (AuthorityUnavailable, ValueError):
            return False

    def _start(self, action, call, *, controller=None):
        marker = {'action': action, 'request': None, 'controller': controller}
        with self._lock:
            if self._closing or self._closed:
                raise AuthorityUnavailable('微信渠道正在停止')
            if self._pending is not None or self._reconciling:
                raise AuthorityUnavailable('微信渠道操作仍在等待核对')
            if action in ('reserve', 'recover') and self._grant is not None:
                raise AuthorityUnavailable('微信渠道已有负责人申请')
            if action not in ('reserve', 'recover') and self._grant is None:
                raise AuthorityUnavailable('微信渠道负责人尚未确认')
            if action == 'activate' and self._revoking:
                raise AuthorityUnavailable('微信渠道正在断开')
            if action == 'release' and not self._release_authorized:
                raise AuthorityUnavailable('微信渠道撤销尚未确认')
            if action in ('reserve', 'recover'):
                if not self._host_lock.acquire():
                    raise AuthorityUnavailable('这台电脑已有微信渠道授权进程')
                if action == 'recover' and not self.client.recovery_safe():
                    self._host_lock.release()
                    raise AuthorityUnavailable('上次微信渠道操作需要先核对，暂不能自动恢复')
            if action == 'begin_release':
                self._revoking = True  # Fence workers before any remote request.
            generation = self._grant['generation'] if self._grant else None
            self._pending = marker
        try:
            request = call(generation)
        except BaseException:
            with self._lock:
                if self._pending is marker:
                    self._pending = None
                    if action in ('reserve', 'recover'):
                        self._host_lock.release()
            raise
        with self._lock:
            if self._pending is not marker:
                raise AuthorityUnavailable('微信渠道操作已变化')
            marker['request'] = request
        return dict(request)

    def reserve(self):
        return self._start('reserve', lambda _: self.client.reserve())

    def prepare_host(self):
        """Attach the verified local grant to the channel's catalog entry."""
        with self._lock:
            if not self.allowed() or self._grant is None:
                raise AuthorityUnavailable('微信渠道负责人尚未确认')
            grant = {key: self._grant[key] for key in ('authority', 'generation', 'state', 'host')}
            result = self.service._system_writer.conversation_claim_weixin(grant)
            if not self.allowed():
                raise AuthorityUnavailable('微信渠道负责人已变化，需要核对')
            return result

    def recover(self):
        """Reconfirm authority state after restart before reusing credentials."""
        if not self.client.recovery_safe():
            raise AuthorityUnavailable('上次微信渠道操作需要先核对，暂不能自动恢复')
        return self._start('recover', lambda _: self.client.view())

    def reconcile(self, timeout=0):
        """Check a retained operation before allowing restart recovery."""
        with self._lock:
            if self._closing or self._closed or self._grant is not None or self._pending is not None or self._reconciling:
                raise AuthorityUnavailable('微信渠道操作仍在等待核对')
            if not self._host_lock.acquire():
                raise AuthorityUnavailable('这台电脑已有微信渠道授权进程')
            self._reconciling = True
        try:
            return self.client.reconcile_pending(timeout)
        finally:
            with self._lock:
                self._reconciling = False

    def activate(self):
        return self._start('activate', self.client.activate)

    def begin_release(self):
        return self._start('begin_release', self.client.begin_release)

    def release(self, controller):
        return self._start('release', lambda generation: self.client.release(controller, generation), controller=controller)

    def resolve(self, timeout=0):
        with self._lock:
            marker = self._pending
            if marker is None or marker['request'] is None:
                raise AuthorityUnavailable('没有可核对的微信渠道操作')
            request, action = marker['request'], marker['action']
            grant = dict(self._grant) if self._grant else None
        if action == 'release':
            result = self.client.finish_release(request, marker['controller'], grant['generation'], timeout)
        else:
            result = self.client.wait(request, timeout) if request.get('status') == 'queued' else request
        if result.get('status') in ('queued', 'unknown'):
            return dict(result)  # Preserve the original identity; never retry a mutation automatically.
        with self._lock:
            if self._pending is not marker:
                raise AuthorityUnavailable('微信渠道操作已变化')
            if result.get('status') != 'applied':
                self._pending = None
                if action in ('reserve', 'recover'):
                    self._host_lock.release()
                return dict(result)
            state = result.get('channel_authority')
            if action == 'recover':
                mesh, authority = self.client._authority()
                scope = self.client._scope(mesh, authority)
                if request.get('status') == 'queued' and any(request.get(key) != value for key, value in scope.items()):
                    raise AuthorityUnavailable('微信渠道来源版本已变化，需要核对')
                if (not isinstance(state, dict) or result.get('authority') != authority
                        or state.get('authority') != authority or type(state.get('generation')) is not int
                        or state['generation'] < 0 or state.get('state') not in ('unbound', 'reserved', 'active', 'revoking')
                        or (state['state'] == 'unbound' and state.get('host') is not None)
                        or (state['state'] != 'unbound' and
                            (state['generation'] < 1 or not isinstance(state.get('host'), str) or not state['host']))):
                    raise AuthorityUnavailable('微信渠道负责人结果不匹配，需要核对')
                if state.get('host') == self.service.runtime_id:
                    self._grant = dict(state, _scope=scope)
                    self._revoking = state['state'] == 'revoking'
                    self._release_authorized = self._revoking
                else:
                    self._host_lock.release()
                self._pending = None
                return dict(result)
            expected = {'reserve': 'reserved', 'activate': 'active',
                        'begin_release': 'revoking', 'release': 'unbound'}[action]
            mesh, authority = self.client._authority()
            scope = self.client._scope(mesh, authority)
            if request.get('status') == 'queued' and any(request.get(key) != value for key, value in scope.items()):
                raise AuthorityUnavailable('微信渠道来源版本已变化，需要核对')
            if (not isinstance(state, dict) or result.get('authority') != authority
                    or state.get('authority') != authority or state.get('state') != expected
                    or type(state.get('generation')) is not int or state['generation'] < 1
                    or (action != 'release' and state.get('host') != self.service.runtime_id)
                    or (action == 'release' and state.get('host') is not None)
                    or (grant is not None and state['generation'] != grant['generation'])):
                raise AuthorityUnavailable('微信渠道负责人结果不匹配，需要核对')
            if action == 'release':
                self._grant = None
                self._release_authorized = False
                self._host_lock.release()
            else:
                self._grant = dict(state, _scope=scope)
                if action == 'reserve':
                    self._revoking = False
                elif action == 'begin_release':
                    self._release_authorized = True
            self._pending = None
            return dict(result)

    def shutdown(self, controller, timeout=0):
        """Stop locally without releasing the network reservation.

        Unresolved mutations stay in the journal; a new process cannot
        recover until their outcomes have been reconciled.
        """
        with self._lock:
            if self._closed:
                return True
            self._closing = True
            self._revoking = True
            if self._reconciling or (self._pending is not None and self._pending['request'] is None):
                return False
        if not controller.close(timeout=timeout):
            return False
        with self._lock:
            self._grant = None
            self._pending = None
            self._closed = True
            self._host_lock.release()
            return True
