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


# ---------------------------------------------------------------- trusted proxy list
from types import SimpleNamespace  # noqa: E402

from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

from core.security.auth_proxy import (  # noqa: E402
    address_is_trusted,
    parse_trusted_proxies,
    peer_address,
    request_proxy_user,
    setup_problem,
)


def _cfg(header='Remote-User', trusted='10.0.0.0/24'):
    values = {'security.auth_proxy_header': header, 'security.auth_proxy_trusted': trusted}
    return lambda key, default=None: values.get(key, default)


def _req(addr, headers=None, environ=None):
    env = {'REMOTE_ADDR': addr}
    env.update(environ or {})
    return SimpleNamespace(headers=dict(headers or {'Remote-User': 'alice'}), environ=env)


def test_parse_ips_ranges_and_junk():
    nets, bad = parse_trusted_proxies(' 172.18.0.2,\n10.0.0.0/8 ; fd00::/8  nope ')
    assert [str(n) for n in nets] == ['172.18.0.2/32', '10.0.0.0/8', 'fd00::/8']
    assert bad == ['nope']
    assert parse_trusted_proxies(['192.168.1.1', '']) [0][0].num_addresses == 1
    assert parse_trusted_proxies('') == ([], [])
    assert parse_trusted_proxies(None) == ([], [])


def test_header_counts_only_from_a_trusted_peer():
    assert request_proxy_user(_req('10.0.0.9'), _cfg()) == 'alice'
    assert request_proxy_user(_req('10.0.1.9'), _cfg()) is None
    assert request_proxy_user(_req(''), _cfg()) is None
    assert request_proxy_user(_req('garbage'), _cfg()) is None


def test_empty_trusted_list_ignores_the_header():
    assert request_proxy_user(_req('127.0.0.1'), _cfg(trusted='')) is None
    assert request_proxy_user(_req('127.0.0.1'), _cfg(trusted=None)) is None


def test_header_off_is_a_no_op():
    assert request_proxy_user(_req('10.0.0.9'), _cfg(header='')) is None
    assert request_proxy_user(_req('10.0.0.9'), None) is None


def test_ipv4_mapped_peer_matches_its_ipv4_range():
    assert address_is_trusted('::ffff:10.0.0.9', parse_trusted_proxies('10.0.0.0/24')[0])
    assert not address_is_trusted('::ffff:10.0.1.9', parse_trusted_proxies('10.0.0.0/24')[0])


def test_peer_is_the_connection_not_x_forwarded_for():
    # reverse-proxy mode: ProxyFix rewrites REMOTE_ADDR from X-Forwarded-For and
    # keeps the real peer aside; the check must use the real one
    seen = {}

    def app(environ, start_response):
        seen.update(environ)
        start_response('200 OK', [])
        return [b'']

    wrapped = ProxyFix(app, x_for=1)
    wrapped({'REMOTE_ADDR': '192.168.1.50', 'HTTP_X_FORWARDED_FOR': '10.0.0.9',
             'REQUEST_METHOD': 'GET', 'wsgi.url_scheme': 'http',
             'SERVER_NAME': 'x', 'SERVER_PORT': '80'}, lambda *a: None)
    assert seen['REMOTE_ADDR'] == '10.0.0.9'
    assert peer_address(seen) == '192.168.1.50'
    req = SimpleNamespace(headers={'Remote-User': 'admin'}, environ=seen)
    assert request_proxy_user(req, _cfg()) is None
    # and a request that really came from the proxy still works through ProxyFix
    wrapped({'REMOTE_ADDR': '10.0.0.2', 'HTTP_X_FORWARDED_FOR': '203.0.113.7',
             'REQUEST_METHOD': 'GET', 'wsgi.url_scheme': 'http',
             'SERVER_NAME': 'x', 'SERVER_PORT': '80'}, lambda *a: None)
    req = SimpleNamespace(headers={'Remote-User': 'alice'}, environ=seen)
    assert request_proxy_user(req, _cfg()) == 'alice'


def test_setup_problem_tells_a_header_only_install():
    assert setup_problem(_cfg(header='')) is None
    assert setup_problem(_cfg()) is None
    msg = setup_problem(_cfg(trusted=''))
    assert 'ignores the header' in msg and 'Remote-User' in msg
    assert 'nope' in setup_problem(_cfg(trusted='10.0.0.1, nope'))
