"""Opt-in reverse-proxy mode must be a STRICT no-op when off (default), so a
direct/LAN install is byte-for-byte unchanged, and only harden when enabled."""

from __future__ import annotations

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from core.security.reverse_proxy import apply_reverse_proxy_mode, CONFIG_KEY


def _cfg(value):
    """A config_manager.get-style callable returning `value` for the proxy key."""
    return lambda key, default=None: value if key == CONFIG_KEY else default


def test_off_by_default_is_a_strict_noop():
    app = Flask(__name__)

    enabled = apply_reverse_proxy_mode(app, _cfg(False))  # default/off

    assert enabled is False
    assert not isinstance(app.wsgi_app, ProxyFix)            # NOT wrapped
    # Flask defaults untouched — cookie not forced Secure, no SameSite override
    assert app.config.get('SESSION_COOKIE_SECURE') in (None, False)
    assert app.config.get('SESSION_COOKIE_SAMESITE') is None


def test_missing_key_is_also_a_noop():
    app = Flask(__name__)
    assert apply_reverse_proxy_mode(app, lambda key, default=None: default) is False
    assert not isinstance(app.wsgi_app, ProxyFix)


def test_on_wraps_proxyfix_and_secures_cookie():
    app = Flask(__name__)

    enabled = apply_reverse_proxy_mode(app, _cfg(True))

    assert enabled is True
    assert isinstance(app.wsgi_app, ProxyFix)               # forwarded headers trusted
    assert app.config['SESSION_COOKIE_SECURE'] is True      # cookie HTTPS-only
    assert app.config['SESSION_COOKIE_SAMESITE'] == 'Lax'


def test_failure_falls_back_to_noop():
    # A config_get that raises must not break startup — treated as off.
    app = Flask(__name__)
    def boom(key, default=None):
        raise RuntimeError('config exploded')
    assert apply_reverse_proxy_mode(app, boom) is False
    assert not isinstance(app.wsgi_app, ProxyFix)
