"""CORS support for the JSON API (browser extensions, cross-origin clients).

Beckid: Firefox does not apply host-permission CORS bypass to extension
page fetches the way Chrome does, so the SoulSync Companion extension's
``fetch()`` calls from its popup/options pages are subject to normal CORS
and fail with ``NetworkError`` against a server that sends no CORS headers.
Verified Sep 2026 against Firefox 156 with a local extension + stub server:
even a background-context fetch with the host permission granted is
rejected; the same fetch succeeds once the server answers preflights and
sends ``Access-Control-Allow-Origin``.

Pure decision helpers (no Flask) so the behavior is unit-testable without
standing up the app. ``web_server.py`` wires them into a ``before_request``
preflight handler (registered BEFORE the login/launch-PIN gates — a
preflight carries no credentials and returns no data, so it must not be
gated; the real request still goes through auth) and an ``after_request``
response header hook.

Security: CORS alone grants nothing. Every ``/api/*`` route still enforces
its own auth (API key / session). ``Access-Control-Allow-Origin: '*'`` is
used, matching several existing endpoints; browsers forbid combining ``*``
with credentialed requests, so session cookies cannot be leveraged
cross-origin. An arbitrary website still cannot call the API without the
user's key.
"""

from __future__ import annotations

# The extension's fetch() calls send these; the preflight must allow them.
_ALLOWED_HEADERS = "Authorization, Content-Type, X-Requested-With"

# Everything the API serves today, plus room for growth.
_ALLOWED_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"

# Cache a successful preflight for a day so browsers don't re-OPTIONS every call.
_MAX_AGE = "86400"


def is_api_path(path: str | None) -> bool:
    """True for paths that belong to the JSON API surface."""
    return bool(path) and path.startswith("/api/")


def preflight_headers(path: str | None, method: str | None) -> dict[str, str] | None:
    """CORS headers for an OPTIONS preflight to an API path, else None.

    Returns None for non-OPTIONS requests and non-API paths so the caller
    leaves those requests alone.
    """
    if (method or "").upper() != "OPTIONS":
        return None
    if not is_api_path(path):
        return None
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": _ALLOWED_METHODS,
        "Access-Control-Allow-Headers": _ALLOWED_HEADERS,
        "Access-Control-Max-Age": _MAX_AGE,
    }


def response_headers(path: str | None) -> dict[str, str]:
    """CORS headers to stamp on an API response (empty dict for non-API)."""
    if not is_api_path(path):
        return {}
    return {"Access-Control-Allow-Origin": "*"}


__all__ = ["is_api_path", "preflight_headers", "response_headers"]
