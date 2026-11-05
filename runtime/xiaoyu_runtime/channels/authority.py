"""Durable single-host reservation; transport and authentication are external.

Only the configured, stable network authority may open this ledger. A host
must stop its worker and account for in-flight network calls before it can
release a reservation. In particular, offline or a timeout is not a release.
This module is not exposed over HTTP or mesh by itself.
"""
import json
import re
import threading
from pathlib import Path

from .host_lock import HostLock
from ..world.world import durable_write


def _identity(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) is not None


class ChannelAuthority:
    def __init__(self, path, authority, *, local_runtime):
        if not _identity(authority) or authority != local_runtime:
            raise ValueError('stable channel authority required')
        self.path = Path(path)
        self.authority = authority
        self._lock = threading.RLock()
        self._closed = False
        self._process_lock = HostLock(self.path.with_suffix(self.path.suffix + '.lock'))
        if not self._process_lock.acquire():
            raise ValueError('channel authority already running')
        try:
            try:
                state = json.loads(self.path.read_text('utf-8'))
            except FileNotFoundError:
                state = {'authority': authority, 'generation': 0, 'state': 'unbound', 'host': None}
                durable_write(self.path, state)
            if (not isinstance(state, dict) or set(state) != {'authority', 'generation', 'state', 'host'}
                    or state['authority'] != authority or type(state['generation']) is not int
                    or state['generation'] < 0 or state['state'] not in ('unbound', 'reserved', 'active', 'revoking')
                    or (state['state'] == 'unbound' and state['host'] is not None)
                    or (state['state'] != 'unbound' and
                        (state['generation'] < 1 or not _identity(state['host'])))):
                raise ValueError('invalid channel authority ledger')
            self._state = state
        except BaseException:
            self._process_lock.release()
            raise

    def _save(self, state):
        durable_write(self.path, state)
        self._state = state

    def view(self):
        with self._lock:
            self._check_open()
            return dict(self._state)

    def reserve(self, host):
        """Reserve for the authenticated requesting host, never steal a binding."""
        if not _identity(host):
            raise ValueError('invalid channel host')
        with self._lock:
            self._check_open()
            state = self._state
            if state['state'] != 'unbound':
                if state['host'] == host and state['state'] == 'reserved':
                    return dict(state)
                raise ValueError('channel already reserved or bound')
            updated = dict(state, state='reserved', host=host, generation=state['generation'] + 1)
            self._save(updated)
            return dict(updated)

    def activate(self, host, generation):
        with self._lock:
            self._check(host, generation)
            if self._state['state'] == 'active':
                return self.view()
            if self._state['state'] != 'reserved':
                raise ValueError('channel reservation is being revoked')
            updated = dict(self._state, state='active')
            self._save(updated)
            return dict(updated)

    def begin_release(self, host, generation):
        """Fence new starts before asking the old host to stop its worker."""
        with self._lock:
            self._check(host, generation)
            if self._state['state'] == 'revoking':
                return self.view()
            updated = dict(self._state, state='revoking')
            self._save(updated)
            return dict(updated)

    def release(self, host, generation, *, worker_stopped):
        """Accept only an authenticated host's completed stop acknowledgement."""
        if worker_stopped is not True:
            raise ValueError('channel worker has not stopped')
        with self._lock:
            self._check(host, generation)
            if self._state['state'] != 'revoking':
                raise ValueError('channel revocation has not started')
            updated = dict(self._state, state='unbound', host=None)
            self._save(updated)
            return dict(updated)

    def _check(self, host, generation):
        self._check_open()
        if (not _identity(host) or type(generation) is not int or self._state['state'] == 'unbound'
                or self._state['host'] != host or self._state['generation'] != generation):
            raise ValueError('channel reservation changed')

    def _check_open(self):
        if self._closed:
            raise ValueError('channel authority is closed')

    def close(self):
        with self._lock:
            self._closed = True
            self._process_lock.release()
