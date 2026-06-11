"""Forward-auth proxy header trust (Tier 3): OFF by default → no-op; when the
operator configures a header, a request carrying it is treated as authenticated."""

from __future__ import annotations

from core.security.auth_proxy import trusted_proxy_user


def _headers(d):
    return lambda name: d.get(name)


def test_off_when_no_header_configured():
    # empty header name → feature disabled → always None (direct install unaffected)
    assert trusted_proxy_user(_headers({'Remote-User': 'alice'}), '') is None
    assert trusted_proxy_user(_headers({'Remote-User': 'alice'}), None) is None


def test_returns_user_when_header_present():
    assert trusted_proxy_user(_headers({'Remote-User': 'alice'}), 'Remote-User') == 'alice'


def test_none_when_configured_header_absent_or_blank():
    assert trusted_proxy_user(_headers({}), 'Remote-User') is None
    assert trusted_proxy_user(_headers({'Remote-User': '   '}), 'Remote-User') is None


def test_get_header_exception_is_safe():
    def boom(_name):
        raise RuntimeError('header lookup blew up')
    assert trusted_proxy_user(boom, 'Remote-User') is None
