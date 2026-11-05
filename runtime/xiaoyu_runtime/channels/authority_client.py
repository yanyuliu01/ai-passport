"""Event-driven channel authority requests over the existing Runtime mesh.

The caller retains queued command identities and must reconcile an uncertain
result before issuing another operation. This module does not bind an account
or enable the channel on its own.
"""
import time
import uuid
import re

from .authority_journal import AuthorityJournal
from .authority import _identity


class AuthorityUnavailable(ValueError):
    pass


def _valid_state(value, authority):
    return (isinstance(value, dict) and set(value) == {'authority', 'generation', 'state', 'host'}
            and value.get('authority') == authority and type(value.get('generation')) is int
            and value['generation'] >= 0 and value.get('state') in ('unbound', 'reserved', 'active', 'revoking')
            and ((value['state'] == 'unbound' and value.get('host') is None)
                 or (value['state'] != 'unbound' and value['generation'] >= 1 and _identity(value.get('host')))))


class AuthorityClient:
    def __init__(self, service, journal=None):
        self.service = service
        self.journal = journal if journal is not None else AuthorityJournal(
            service._store.state_dir / 'channels' / 'weixin' / 'authority-operation.json')
        self._reconcile_view = None

    def recovery_safe(self):
        # A fresh view is safe only when no earlier mutation can arrive later.
        try:
            return self.journal.pending() is None
        except (OSError, ValueError):
            return False

    def reconcile_pending(self, timeout=0):
        """Check the saved operation identity without resubmitting it."""
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 <= timeout <= 25:
            raise ValueError('微信渠道操作等待时间无效')
        pending = self.journal.pending()
        if pending is None:
            return {'status': 'clear'}
        mesh, authority = self._authority()
        if authority != pending['authority']:
            raise AuthorityUnavailable('微信渠道权威已变化，原操作需要核对')
        scope = self._scope(mesh, authority)
        if any(pending[key] != scope[key] for key in scope):
            raise AuthorityUnavailable('微信渠道来源版本已变化，原操作需要核对')
        if authority != self.service.runtime_id:
            deadline = time.monotonic() + timeout
            outcome = self.wait({'status': 'queued', 'authority': authority,
                                 'command_id': pending['id'], 'action': pending['action'], **scope}, timeout)
            if outcome.get('status') != 'unknown':
                return outcome
            # The authority has durably acknowledged that the old command is
            # uncertain and will not execute it again. Reuse one read-only
            # view request while waiting; never resend the mutation.
            if self._reconcile_view is None or self._reconcile_view[0] != pending['id']:
                self._reconcile_view = (pending['id'], self.view())
            request = self._reconcile_view[1]
            remaining = max(0, deadline - time.monotonic())
            view = self.wait(request, remaining) if request.get('status') == 'queued' else request
            if view.get('status') != 'applied':
                if view.get('status') != 'queued':
                    self._reconcile_view = None
                return outcome
            self._reconcile_view = None
            state = view.get('channel_authority')
            if not _valid_state(state, authority):
                raise AuthorityUnavailable('微信渠道权威状态不可用')
            return self._reconcile_state(pending, authority, state)
        # A local call has no delayed mesh command. Once the old process is
        # gone, its durable authority state can resolve whether it applied.
        result = self.service.channel_authority_command(self.service.runtime_id, {'action': 'view'})
        state = result.get('channel_authority')
        if not _valid_state(state, authority):
            raise AuthorityUnavailable('微信渠道权威状态不可用')
        return self._reconcile_state(pending, authority, state)

    def _reconcile_state(self, pending, authority, state):
        action, generation = pending['action'], pending['generation']
        status, host, current = state.get('state'), state.get('host'), state.get('generation')
        local = host == self.service.runtime_id
        if action == 'reserve':
            applied = status == 'reserved' and local and type(current) is int and current > 0
            refused = status == 'unbound' and host is None
        else:
            same = type(current) is int and current == generation
            applied = same and ((action == 'activate' and status == 'active' and local)
                                or (action == 'begin_release' and status == 'revoking' and local)
                                or (action == 'release' and status == 'unbound' and host is None))
            refused = same and local and ((action == 'activate' and status == 'reserved')
                                          or (action == 'begin_release' and status in ('reserved', 'active'))
                                          or (action == 'release' and status == 'revoking'))
        if not applied and not refused:
            return {'status': 'unknown', 'authority': authority, 'command_id': pending['id']}
        self.journal.finish(pending['id'])
        return {'status': 'applied' if applied else 'refused', 'authority': authority,
                'channel_authority': dict(state)}

    def _authority(self):
        mesh = self.service.mesh
        if mesh is None:
            raise AuthorityUnavailable('微信渠道权威不可用')
        main = mesh.owner_of('default')
        authority = mesh.authority()
        if not authority or main.get('conflict') or main.get('unresolved'):
            raise AuthorityUnavailable('微信渠道权威尚未确认')
        return mesh, authority

    def _scope(self, mesh, authority):
        host_epoch = self.service.card_store.epoch
        authority_epoch = (host_epoch if authority == self.service.runtime_id
                           else mesh._replicas.applied_position(authority)[0])
        scope = {'subject': self.service.subject_id, 'host_epoch': host_epoch, 'authority_epoch': authority_epoch}
        if not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{32}', value) for value in scope.values()):
            raise AuthorityUnavailable('微信渠道来源版本尚未确认')
        return scope

    def _submit(self, args):
        mesh, authority = self._authority()
        scope = self._scope(mesh, authority)
        mutation = args.get('action') != 'view'
        command = uuid.uuid4().hex if mutation else None
        if authority != self.service.runtime_id:
            problem = mesh._reachable(authority)
            if problem:
                raise AuthorityUnavailable('微信渠道权威不可用，请等负责的电脑上线')
        if mutation:
            self.journal.begin({'id': command, 'authority': authority,
                                'action': args.get('action'), 'generation': args.get('generation'), **scope})
        if authority == self.service.runtime_id:
            from ..api.service import RequestError
            try:
                result = self.service.channel_authority_command(self.service.runtime_id, args)
            except RequestError:
                # The local authority explicitly refused without applying.
                if mutation:
                    self.journal.finish(command)
                raise
            if mutation:
                self.journal.finish(command)
            return {'status': 'applied', 'authority': authority, **result}
        if mutation:
            command = mesh.command(authority, 'weixin_authority', args, command_id=command)
        else:
            command = mesh.command(authority, 'weixin_authority', args)
        return {'status': 'queued', 'authority': authority, 'command_id': command, 'action': args.get('action'), **scope}

    def _settle(self, command, action):
        if action == 'view':
            return
        pending = self.journal.pending()
        if pending is not None:
            if pending['id'] != command or pending['action'] != action:
                raise AuthorityUnavailable('微信渠道操作编号不匹配，需要核对')
            self.journal.finish(command)

    def view(self):
        return self._submit({'action': 'view'})

    def reserve(self):
        return self._submit({'action': 'reserve'})

    def activate(self, generation):
        return self._submit({'action': 'activate', 'generation': generation})

    def begin_release(self, generation):
        return self._submit({'action': 'begin_release', 'generation': generation})

    def release(self, controller, generation):
        # Never ask the authority to free a host while a QR or send is in flight.
        if not controller.prepare_release(timeout=0):
            raise AuthorityUnavailable('微信连接还在停止，请稍后核对')
        return self._submit({'action': 'release', 'generation': generation, 'worker_stopped': True})

    def finish_release(self, request, controller, generation, timeout=0):
        """Release the local lock only after the authority confirms this generation."""
        result = self.wait(request, timeout) if request.get('status') == 'queued' else request
        if result.get('status') != 'applied':
            return result
        state = result.get('channel_authority')
        if (not isinstance(state, dict) or state.get('state') != 'unbound' or state.get('host') is not None
                or type(generation) is not int or state.get('generation') != generation
                or state.get('authority') != result.get('authority')):
            raise AuthorityUnavailable('微信渠道释放结果不匹配，保留本机连接锁')
        if not controller.close(timeout=0):
            raise AuthorityUnavailable('微信连接尚未完全停止，保留本机连接锁')
        return result

    def wait(self, request, timeout=0):
        if (not isinstance(request, dict) or request.get('status') != 'queued'
                or not isinstance(request.get('authority'), str) or not isinstance(request.get('command_id'), str)
                or request.get('action') not in ('view', 'reserve', 'activate', 'begin_release', 'release')
                or isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 <= timeout <= 25):
            raise ValueError('微信渠道操作格式无效')
        mesh, authority = self._authority()
        if request['authority'] != authority:
            raise AuthorityUnavailable('微信渠道权威已变化，原操作需要核对')
        scope = self._scope(mesh, authority)
        if any(request.get(key) != scope[key] for key in scope):
            raise AuthorityUnavailable('微信渠道来源版本已变化，原操作需要核对')
        key, deadline = request['command_id'], time.monotonic() + timeout
        with mesh._changed:
            while True:
                if self._scope(mesh, authority) != scope:
                    raise AuthorityUnavailable('微信渠道来源版本已变化，原操作需要核对')
                raw = mesh._replicas.applied_record(authority, 'result', key + ':accepted', epoch=scope['authority_epoch'])
                if raw is not None:
                    main = mesh.owner_of('default')
                    if mesh.authority() != authority or main.get('conflict') or main.get('unresolved'):
                        raise AuthorityUnavailable('微信渠道权威已变化，原操作需要核对')
                    if (raw.get('id') != key or raw.get('from') != authority
                            or raw.get('to') != self.service.runtime_id or raw.get('phase') != 'accepted'):
                        raise AuthorityUnavailable('微信渠道操作结果来源不匹配')
                    if raw.get('unknown') is True:
                        return {'status': 'unknown', 'authority': authority, 'command_id': key}
                    if raw.get('ok') is not True:
                        self._settle(key, request['action'])
                        return {'status': 'refused', 'authority': authority, 'reason': '微信渠道操作未被接受'}
                    result = raw.get('channel_authority')
                    if not _valid_state(result, authority):
                        raise AuthorityUnavailable('微信渠道操作结果格式不匹配')
                    self._settle(key, request['action'])
                    return {'status': 'applied', 'authority': authority, 'channel_authority': dict(result)}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return dict(request)
                mesh._changed.wait(remaining)
