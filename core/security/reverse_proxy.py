"""Opt-in reverse-proxy mode.

Default OFF. When off this is a strict no-op: the Flask app is left exactly as it
was, ``X-Forwarded-*`` headers are NOT trusted (so a direct client can't spoof its
IP/scheme), and the session cookie keeps Flask's defaults. So a normal direct /
LAN install is byte-for-byte unchanged.

Only when the operator explicitly sets ``security.trust_reverse_proxy: true`` —
they're running behind nginx / Caddy / Traefik that terminates TLS — do we:
  - trust the proxy's ``X-Forwarded-For/Proto/Host/Port`` (correct client IP,
    HTTPS detection, redirects), and
  - mark the session cookie ``Secure`` (HTTPS-only) + ``SameSite=Lax``.

Gated this way the security/UX change is scoped strictly to people who turned it
on; everyone else is untouched.
"""

from __future__ import annotations

CONFIG_KEY = "security.trust_reverse_proxy"


def apply_reverse_proxy_mode(app, config_get) -> bool:
    """Apply reverse-proxy hardening to ``app`` iff the operator enabled it.

    ``config_get`` is a ``config_manager.get``-style callable ``(key, default)``.
    Returns True if proxy mode was enabled, False (no-op) otherwise. Never raises
    out — a failure to enable falls back to the safe no-op behaviour.
    """
    try:
        if not config_get(CONFIG_KEY, False):
            return False
        from werkzeug.middleware.proxy_fix import ProxyFix
        # Trust exactly one proxy hop for each forwarded header.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
        app.config["SESSION_COOKIE_SECURE"] = True
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

        # Security headers — registered ONLY in proxy mode (so a direct/LAN install
        # gets none of them). Conservative set that won't break a same-origin app:
        # nosniff, clickjacking protection, and HSTS (safe: only honoured over the
        # HTTPS the proxy terminates). No CSP here — it needs per-deployment tuning
        # and is better added at the proxy. setdefault() so we never clobber a
        # header the proxy already set.
        @app.after_request
        def _security_headers(response):
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
            return response

        return True
    except Exception:
        # If anything goes wrong, behave like off — never break startup over this.
        return False


__all__ = ["apply_reverse_proxy_mode", "CONFIG_KEY"]
