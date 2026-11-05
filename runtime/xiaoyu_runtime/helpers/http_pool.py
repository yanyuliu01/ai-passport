"""Small, bounded keep-alive pool for the official streaming model endpoint.

Only the official direct HTTPS endpoint uses this transport. Custom endpoints,
system proxy configurations and injected test openers retain urllib's existing
behaviour. A POST is never replayed: a stale or broken connection fails normally.
"""

import http.client
import select
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_HOST = "api.deepseek.com"
_MAX_IDLE = 4
_IDLE_SECONDS = 45.0
_DRAIN_SECONDS = 0.05
_DRAIN_LIMIT = 32768


class AmbiguousSendError(urllib.error.URLError):
    """A POST may have reached its destination; do not submit it again."""

    def __init__(self, reason, *, stage='unknown', reused=None):
        super().__init__(reason)
        self.stage = stage
        self.reused = reused


def _same_origin(url, origin):
    try:
        parsed = urllib.parse.urlsplit(url)
        default = 443 if parsed.scheme == "https" else 80
        expected_default = 443 if origin[0] == "https" else 80
        return (parsed.scheme == origin[0] and parsed.hostname == origin[1]
                and (parsed.port or default) == (origin[2] or expected_default)
                and parsed.username is None and parsed.password is None)
    except ValueError:
        return False


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin):
        self._origin = origin

    def redirect_request(self, request, fp, code, message, headers, newurl):
        if not _same_origin(newurl, self._origin):
            raise urllib.error.HTTPError(request.full_url, code, "redirect origin changed", headers, fp)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def direct_official(request):
    """Return true only where changing urllib's HTTP implementation is safe."""
    try:
        url = urllib.parse.urlsplit(request.full_url)
        if (url.scheme != "https" or url.hostname != _HOST or url.port not in (None, 443)
                or url.username is not None or url.password is not None
                or request.get_method() != "POST" or request.has_header("Proxy-authorization")
                or request.has_header("Connection")):
            return False
        proxies = urllib.request.getproxies()
        return not proxies.get("https") or urllib.request.proxy_bypass(url.hostname)
    except (ValueError, OSError):
        return False


class _Lease:
    """Own one response and its connection until closed or cancelled."""

    def __init__(self, pool, origin, connection, response, deadline, reused=False):
        self._pool, self._origin, self._connection = pool, origin, connection
        self._response, self._deadline = response, deadline
        self._guard = threading.Lock()
        self._closed = False
        self._cancelled = False
        self._reused = reused

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.close(reusable=kind is None)

    def __iter__(self):
        return self

    def __next__(self):
        try:
            line = self._response.readline()
        except (OSError, http.client.HTTPException) as error:
            raise AmbiguousSendError(error, stage='read_body', reused=self._reused) from error
        if not line:
            raise StopIteration
        return line

    def read(self, *args):
        try:
            return self._response.read(*args)
        finally:
            # Error responses are not pooled. HTTPError.read() is normally the
            # last operation the caller performs on its attached file object.
            if self._response.status >= 300:
                self.close(reusable=False)

    def __getattr__(self, name):
        return getattr(self._response, name)

    def cancel(self):
        """Unblock a stalled read without touching any subsequently leased socket."""
        with self._guard:
            if self._closed:
                return
            self._cancelled = True
            sock = self._connection.sock
            if sock is None:
                # HTTPConnection detaches the socket for Connection: close;
                # its HTTPResponse still owns the underlying socket.
                raw = getattr(getattr(self._response, "fp", None), "raw", None)
                sock = getattr(raw, "_sock", None)
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def close(self, reusable=True):
        with self._guard:
            if self._closed:
                return
            self._closed = True
            connection, response = self._connection, self._response
            try:
                # A message_stop SSE event can precede the last blank line and
                # HTTP chunk terminator. Drain only a small, bounded trailer;
                # never wait for a server that leaves its stream open.
                if (reusable and not self._cancelled and not response.will_close and not response.isclosed()
                        and connection.sock is not None):
                    wait = min(_DRAIN_SECONDS, self._deadline - time.monotonic())
                    if wait > 0:
                        connection.sock.settimeout(wait)
                        remaining = _DRAIN_LIMIT
                        while remaining > 0 and not response.isclosed():
                            # Python 3.9 readline() with Content-Length zero
                            # returns b'' without closing its file wrapper.
                            if response.length == 0:
                                response.read()
                                break
                            line = response.readline(min(remaining, 8192))
                            if not line:
                                break
                            remaining -= len(line)
                clean = (reusable and not self._cancelled and not response.will_close
                         and response.isclosed() and connection.sock is not None)
            except (OSError, ValueError, http.client.HTTPException):
                clean = False
            finally:
                response.close()
            if clean:
                self._pool._release(self._origin, connection)
            else:
                connection.close()


class ConnectionPool:
    """A bounded pool; each active stream has an exclusive HTTP connection."""

    def __init__(self):
        self._lock = threading.Lock()
        self._idle = {}

    @staticmethod
    def _usable(connection):
        sock = connection.sock
        if sock is None:
            return False
        try:
            # Idle readability may be EOF, TLS close-notify or unexpected
            # trailing data. Either way, discard rather than misroute a reply.
            if isinstance(sock, ssl.SSLSocket) and sock.pending():
                return False
            return not select.select([sock], [], [], 0)[0]
        except (OSError, ValueError):
            return False

    def _take(self, origin):
        now = time.monotonic()
        with self._lock:
            entries = self._idle.get(origin, [])
            while entries:
                last, connection = entries.pop()
                if now - last <= _IDLE_SECONDS and self._usable(connection):
                    return connection
                connection.close()
        return None

    def _release(self, origin, connection):
        with self._lock:
            entries = self._idle.setdefault(origin, [])
            if len(entries) < _MAX_IDLE:
                entries.append((time.monotonic(), connection))
                return
        connection.close()

    def open(self, request, timeout):
        deadline = time.monotonic() + timeout
        url = urllib.parse.urlsplit(request.full_url)
        origin = (url.scheme, url.hostname, url.port)
        connection = self._take(origin)
        reused = connection is not None
        if connection is None:
            if url.scheme == "https":
                connection = http.client.HTTPSConnection(url.hostname, url.port, timeout=timeout,
                                                          context=ssl.create_default_context())
            elif url.scheme == "http":  # Local HTTP/1.1 fixture tests only.
                connection = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout)
            else:
                raise urllib.error.URLError("unsupported streaming protocol")
        else:
            connection.timeout = timeout
            connection.sock.settimeout(timeout)
        path = urllib.parse.urlunsplit(("", "", url.path or "/", url.query, ""))
        headers = dict(request.header_items())
        headers.setdefault("User-agent", "Python-urllib/" + urllib.request.__version__)
        try:
            if connection.sock is None:
                connection.connect()
        except (OSError, http.client.HTTPException) as error:
            connection.close()
            # A failed connection has not sent any request bytes.
            raise urllib.error.URLError(error) from error
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            connection.close()
            raise urllib.error.URLError("streaming request deadline expired before send")
        connection.sock.settimeout(remaining)
        timer_guard = threading.Lock()
        active = [True]
        expired = threading.Event()

        def expire_headers():
            # This guard ends timer ownership before handing the socket to the
            # response lease. A late timer cannot kill a later pooled turn.
            with timer_guard:
                if not active[0]:
                    return
                expired.set()
                sock = connection.sock
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        timer = threading.Timer(remaining, expire_headers)
        timer.daemon = True
        timer.start()
        stage = 'send_request'
        try:
            connection.request(request.get_method(), path, body=request.data, headers=headers)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or expired.is_set():
                raise socket.timeout("streaming request deadline expired")
            connection.sock.settimeout(remaining)
            stage = 'read_headers'
            response = connection.getresponse()
            if expired.is_set():
                response.close()
                raise socket.timeout("streaming request deadline expired")
        except (OSError, http.client.HTTPException) as error:
            connection.close()
            # Once writing starts, neither a broken pipe nor a lost response
            # proves that the server did not process this request.
            raise AmbiguousSendError(error, stage=stage, reused=reused) from error
        finally:
            with timer_guard:
                active[0] = False
            timer.cancel()
        lease = _Lease(self, origin, connection, response, deadline, reused=reused)
        if response.status >= 300:
            location = response.getheader("Location")
            if response.status in (301, 302, 303, 307, 308) and location:
                redirect = urllib.parse.urljoin(request.full_url, location)
                if _same_origin(redirect, origin):
                    handler = _SameOriginRedirect(origin)
                    next_request = handler.redirect_request(request, lease, response.status,
                                                            response.reason, response.headers, redirect)
                    lease.close(reusable=False)
                    if next_request is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise urllib.error.URLError("streaming request deadline expired")
                        try:
                            return urllib.request.build_opener(handler).open(next_request, timeout=remaining)
                        except urllib.error.URLError as error:
                            raise AmbiguousSendError(error) from error
            # Never replay a POST or send its credentials to a different
            # origin. Preserve urllib's HTTPError interface on failure.
            raise urllib.error.HTTPError(request.full_url, response.status, response.reason,
                                         response.headers, lease)
        return lease


_POOL = ConnectionPool()


def open_direct(request, timeout):
    return _POOL.open(request, timeout)
