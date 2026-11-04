"""Connections to ``localhost`` go to the loopback address that answers.

``localhost`` resolves to ``::1`` first and ``127.0.0.1`` second, and Python
tries them in that order. A server listening on IPv4 only (Jellyfin on
Windows, most of the arr stack) refuses ``::1``, and Windows takes about two
seconds to give up on a refused connect before it moves on. Every new
connection to such a server paid that: Server Activity's poll took 2.3s on
Boulder's install, all of it this wait (Nov 4 2026; ``localhost:8096`` took
2,098 ms to connect, ``127.0.0.1:8096`` 0 ms).

``install()`` wraps urllib3's connect (requests and plexapi use it) so a
``localhost`` connect tries IPv4 first, and from then on whichever loopback
address last worked for that port. The URL, Host header and TLS name stay
``localhost``; only the socket's address changes. Every other host is passed
through untouched.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List

_LOOPBACK = ("127.0.0.1", "::1")
_preferred: Dict[int, str] = {}
_install_lock = threading.Lock()
_installed = False


def _order(port: int) -> List[str]:
    first = _preferred.get(port)
    if first is None:
        return list(_LOOPBACK)
    return [first] + [ip for ip in _LOOPBACK if ip != first]


def wrap(create_connection):
    """``create_connection`` with localhost connects ordered as above."""

    def connect(address: Any, *args: Any, **kwargs: Any):
        try:
            host, port = address[0], int(address[1])
        except (TypeError, ValueError, IndexError):
            return create_connection(address, *args, **kwargs)
        if not isinstance(host, str) or host.lower().rstrip(".") != "localhost":
            return create_connection(address, *args, **kwargs)
        last_error: Exception | None = None
        for ip in _order(port):
            try:
                sock = create_connection((ip, port), *args, **kwargs)
            except OSError as exc:
                last_error = exc
                continue
            _preferred[port] = ip
            return sock
        assert last_error is not None
        raise last_error

    connect.__wrapped__ = create_connection
    return connect


def install() -> bool:
    """Wrap urllib3's connect once per process. True when it was installed."""
    global _installed
    with _install_lock:
        if _installed:
            return False
        try:
            from urllib3.util import connection
        except Exception:  # noqa: BLE001 - no urllib3, nothing to speed up
            return False
        connection.create_connection = wrap(connection.create_connection)
        _installed = True
        return True


__all__ = ["install", "wrap"]
