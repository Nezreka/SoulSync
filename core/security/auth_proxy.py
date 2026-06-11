"""Trust an authenticated-user header from a forward-auth proxy.

When SoulSync sits behind an auth proxy (Authelia / Authentik / oauth2-proxy), the
proxy authenticates the user and passes their identity in a header (commonly
``Remote-User``). With ``security.auth_proxy_header`` set to that header name,
SoulSync treats a request carrying it as already-authenticated and lets it past the
launch lock — the proxy is the gatekeeper.

OFF by default (empty header name) → a strict no-op; the launch PIN behaves exactly
as before.

⚠️  SECURITY: only enable this behind a proxy you control that STRIPS any
client-supplied copy of the header. Otherwise a direct client could send
``Remote-User: admin`` and walk straight in. This is why it's opt-in and never on
by default.
"""

from __future__ import annotations

from typing import Callable, Optional


def trusted_proxy_user(get_header: Callable[[str], Optional[str]],
                       header_name: str) -> Optional[str]:
    """Return the authenticated username from the configured proxy header, or None.

    ``get_header`` is a ``request.headers.get``-style callable. ``header_name`` is
    the configured header (e.g. ``Remote-User``); empty/None disables the feature
    (always returns None), so a non-proxy install is unaffected.
    """
    if not header_name:
        return None
    try:
        value = (get_header(header_name) or "").strip()
    except Exception:
        return None
    return value or None


__all__ = ["trusted_proxy_user"]
