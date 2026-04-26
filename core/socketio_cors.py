"""Socket.IO CORS allow-list resolution + rejection logging.

Three concerns lifted out of `web_server.py`:

- :func:`resolve_cors_origins` — read the user's
  ``security.cors_origins`` config setting (string, list, or unset) and
  return what to hand to Flask-SocketIO's ``cors_allowed_origins``
  parameter: an empty list (same-origin only), the literal ``'*'``
  (wildcard, opt-in), or a list of explicit origin URLs.

- :func:`will_reject` — predict whether engineio's CORS check will
  reject a request, given the resolved allow-list, the request's
  ``Origin`` header, and the request's ``Host`` header. Used to log a
  helpful warning *before* engineio silently 403s a WebSocket upgrade.
  (Without this, the user just sees a half-broken UI with no live
  updates and nothing in the logs explaining why.)

- :class:`RejectionLogger` — threadsafe dedup wrapper around the warning
  emitter. Each unique origin is logged once per process so a malicious
  site repeatedly hammering the WS endpoint can't spam logs.

Pure logic, no Flask app dependency. Web_server.py imports these and
wires them into the SocketIO init + a Flask ``before_request`` hook.
"""

from __future__ import annotations

import threading
from typing import Any, List, Optional, Set, Union


# What ``cors_allowed_origins`` accepts and what we hand to Flask-SocketIO:
#
# - ``None`` → engineio's same-origin default. engineio computes the
#   allowed origin list from the request itself: ``scheme://HTTP_HOST``
#   plus ``X-Forwarded-Proto://X-Forwarded-Host`` when those headers are
#   present. Reverse proxies that set X-Forwarded-Host (Nginx with
#   ``proxy_set_header X-Forwarded-Host`` — and Caddy/Traefik by default)
#   work transparently. THE SECURE DEFAULT.
#
# - ``'*'`` → allow any origin. Insecure; opt-in only.
#
# - ``[origin, ...]`` → explicit allow-list. For setups whose Origin
#   matches neither the backend's Host nor any forwarded header.
#
# IMPORTANT: do NOT use ``[]``. In engineio that means "disable CORS
# handling entirely" (server.py:202: ``if cors_allowed_origins != []:``)
# which is identical to the ``'*'`` wildcard from a security standpoint.
ResolvedOrigins = Union[List[str], str, None]


def resolve_cors_origins(config_manager: Any) -> ResolvedOrigins:
    """Resolve the configured Socket.IO allow-list.

    Reads ``security.cors_origins`` from ``config_manager`` and normalizes
    whatever shape the user typed (or didn't) into one of three values:

    - ``None`` (the secure default). Hand to Flask-SocketIO and engineio
      enforces same-origin, with automatic support for X-Forwarded-Host
      so reverse-proxy users don't need to configure anything.
    - ``'*'`` — literal wildcard. Allows any origin. Insecure; opt-in.
    - ``[origin, ...]`` — list of explicit origin URLs. For users behind
      a proxy that doesn't send the forwarded headers OR for custom
      contexts (Electron wrappers, browser extensions).

    Accepts the config value as either a string (comma OR newline
    separated, since the settings UI is a textarea) or a list. Anything
    else falls back to ``None`` — the secure default.
    """
    raw = config_manager.get('security.cors_origins', None) if config_manager else None
    if raw is None:
        return None
    if isinstance(raw, str):
        if not raw.strip():
            return None
        parts = [p.strip() for p in raw.replace('\n', ',').split(',')]
    elif isinstance(raw, (list, tuple)):
        parts = [str(p).strip() for p in raw]
    else:
        return None
    parts = [p for p in parts if p]
    if not parts:
        return None
    if any(p == '*' for p in parts):
        return '*'
    return parts


def will_reject(
    allowed: ResolvedOrigins,
    origin: str,
    host: str,
    forwarded_host: str = '',
) -> bool:
    """Predict whether engineio's CORS check will reject this request.

    Mirrors engineio's allow-list / same-origin logic so callers can log
    a helpful warning *before* the rejection happens. Returns ``True``
    when the request will be rejected.

    Same-origin check: ``Origin``'s ``host[:port]`` portion matches the
    request's ``Host`` header OR the ``X-Forwarded-Host`` header. Engineio
    checks both when ``cors_allowed_origins`` is ``None``; we mirror that
    so reverse-proxy users with proper proxy headers don't trigger
    spurious "rejected" log lines.
    """
    if allowed == '*':
        return False
    if isinstance(allowed, list) and origin in allowed:
        return False
    # Origin is "scheme://host[:port][/path]"; pull just host[:port].
    origin_host = origin.split('://', 1)[-1].split('/', 1)[0]
    if host and origin_host == host:
        return False
    if forwarded_host and origin_host == forwarded_host.split(',')[0].strip():
        return False
    return True


class RejectionLogger:
    """Threadsafe dedup wrapper that logs each rejected origin only once.

    Engineio silently 403s WebSocket upgrades from disallowed origins.
    Without a log line the user sees a half-broken UI (no live progress,
    no toasts) and has no idea what's wrong. This class watches incoming
    requests via :meth:`maybe_log` and emits a clear warning the first
    time each unique origin appears, telling the user where to add it.

    Bounded by the number of unique origins ever attempted; cleared on
    process restart. The dedup is intentional — a malicious site
    hammering the endpoint shouldn't be able to spam logs.
    """

    def __init__(self, logger: Any):
        self._logger = logger
        self._seen: Set[str] = set()
        self._lock = threading.Lock()

    def maybe_log(
        self,
        allowed: ResolvedOrigins,
        origin: Optional[str],
        host: str,
        forwarded_host: str = '',
    ) -> bool:
        """Log a rejection warning if applicable, deduped.

        Returns ``True`` if a warning was emitted this call. Designed to
        be safe to call from a Flask ``before_request`` hook on every
        Socket.IO request — it short-circuits early on requests that
        won't be rejected (no Origin header, allowed origin, same-origin
        match against either Host or X-Forwarded-Host).
        """
        if not origin:
            return False  # Non-browser clients (curl, server-to-server)
        if not will_reject(allowed, origin, host, forwarded_host):
            return False
        with self._lock:
            if origin in self._seen:
                return False
            self._seen.add(origin)
        self._logger.warning(
            f"[Socket.IO] Rejecting WebSocket connection from origin '{origin}' "
            f"(request Host='{host}'). If this is your reverse-proxy or custom "
            f"domain, add it to Settings → Security → Allowed WebSocket Origins."
        )
        return True

    def reset_for_tests(self) -> None:
        """Clear the dedup cache. Test-only."""
        with self._lock:
            self._seen.clear()


def log_startup_status(allowed: ResolvedOrigins, logger: Any) -> None:
    """Emit a one-shot startup log line describing the resolved policy.

    - For ``'*'`` (wildcard) → warning, since it's a security risk.
    - For a non-empty list → info, so the user can confirm their config
      took effect.
    - For ``None`` (same-origin default) → silent. That's the default;
      nothing noteworthy.
    """
    if allowed == '*':
        logger.warning(
            "[Socket.IO] cors_allowed_origins is set to '*' — any website can open "
            "a WebSocket to this instance. Set Settings → Security → Allowed Origins "
            "to a specific list (or leave empty for same-origin only) to lock this down."
        )
    elif allowed:
        logger.info(f"[Socket.IO] Allowed cross-origin connections from: {allowed}")
