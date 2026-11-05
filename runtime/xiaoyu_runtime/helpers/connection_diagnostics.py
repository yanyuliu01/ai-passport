"""Bounded, content-free diagnostics for failed model transport attempts."""
import json
import os
import stat
import threading
import time
from pathlib import Path

_LOCK = threading.Lock()
_MAX_BYTES = 256 * 1024
_EXCEPTIONS = frozenset(('TimeoutError', 'BrokenPipeError', 'ConnectionResetError',
                         'ConnectionAbortedError', 'RemoteDisconnected', 'SSLError',
                         'HTTPException', 'OSError', 'URLError', 'EOFError'))


def record(spec, *, trace_id, conversation, stage, error, reused=None, round_index=1,
           elapsed=0.0, http_status=None, interrupted=False):
    """Never write messages, prompt, provider URL, credentials or error text."""
    if spec.config_dir is None:
        return
    reason = getattr(error, 'reason', error)
    name = type(reason).__name__
    errnum = getattr(reason, 'errno', None)
    entry = {'at': time.time(), 'trace_id': trace_id if isinstance(trace_id, str) and len(trace_id) == 32 else '',
             'conversation': conversation if conversation in ('default', 'weixin') else 'other',
             'stage': stage if stage in ('connect', 'send_request', 'read_headers', 'read_body', 'http', 'unknown') else 'unknown',
             'error_type': name if name in _EXCEPTIONS else 'other',
             'errno': errnum if type(errnum) is int else None,
             'reused_connection': reused if type(reused) is bool else None,
             'round': round_index if type(round_index) is int else None,
             'elapsed_ms': round(max(0.0, elapsed) * 1000),
             'http_status': http_status if type(http_status) is int else None,
             'interrupted': interrupted is True}
    try:
        folder = Path(spec.config_dir) / 'xiaoyu-diagnostics'
        with _LOCK:
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            if folder.is_symlink():
                return
            path = folder / 'deepseek-transport.jsonl'
            if path.is_symlink():
                return
            if path.exists() and path.stat().st_size >= _MAX_BYTES:
                os.replace(path, folder / 'deepseek-transport.jsonl.1')
            fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            try:
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    os.write(fd, (json.dumps(entry, separators=(',', ':')) + '\n').encode('utf-8'))
            finally:
                os.close(fd)
    except (OSError, ValueError, TypeError):
        pass
