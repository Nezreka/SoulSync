"""WebUI SPA routing helpers."""

from __future__ import annotations

EXACT_EXCLUDED_PATHS = {"/callback", "/status"}
PREFIX_EXCLUDED_PATHS = (
    "/api",
    "/auth",
    "/callback/",
    "/deezer/",
    "/socket.io",
    "/static",
    "/stream",
    "/tidal/",
)


def should_serve_webui_spa(pathname: str) -> bool:
    """Return True when a request path should fall through to the SPA."""
    normalized = pathname.rstrip("/") or "/"
    if normalized in EXACT_EXCLUDED_PATHS:
        return False
    return not normalized.startswith(PREFIX_EXCLUDED_PATHS)
