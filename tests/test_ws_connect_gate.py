"""#852: the socketio handshake bypasses the HTTP launch-PIN/login gate
(before_request doesn't run for it). The connect handler must enforce the same
check, or removing the overlay + opening a socket streams live data unauthenticated."""

from __future__ import annotations

import os
import tempfile

import pytest

from core.security.ws_gate import is_ws_connection_blocked as blocked


# ── pure gate logic ────────────────────────────────────────────────────────
def _b(**kw):
    base = dict(require_login=False, login_authenticated=False,
               require_pin=False, pin_verified=False, proxy_authed=False)
    base.update(kw)
    return blocked(**base)


def test_nothing_on_allows():
    assert _b() is False


def test_login_on_unauth_blocks():
    assert _b(require_login=True) is True


def test_login_on_authed_allows():
    assert _b(require_login=True, login_authenticated=True) is False


def test_pin_on_unverified_blocks():
    assert _b(require_pin=True) is True


def test_pin_on_verified_allows():
    assert _b(require_pin=True, pin_verified=True) is False


def test_pin_on_proxy_authed_allows():
    assert _b(require_pin=True, proxy_authed=True) is False


def test_login_takes_precedence_over_pin():
    # login on + unauth -> blocked even if the PIN was verified
    assert _b(require_login=True, require_pin=True, pin_verified=True, proxy_authed=True) is True


# ── integration: real socketio connect via the gate ────────────────────────
_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-wsgate-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'w.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'
web_server = pytest.importorskip('web_server')


def _pin_on(monkeypatch):
    real = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, 'get',
                        lambda k, d=None: True if k == 'security.require_pin_on_launch' else real(k, d))


def test_socket_rejected_when_gate_on_and_unauthenticated(monkeypatch):
    _pin_on(monkeypatch)
    flask_client = web_server.app.test_client()  # no launch_pin_verified
    sio = web_server.socketio.test_client(web_server.app, flask_test_client=flask_client)
    assert sio.is_connected() is False           # the #852 hole, now closed


def test_socket_allowed_when_gate_off():
    flask_client = web_server.app.test_client()
    sio = web_server.socketio.test_client(web_server.app, flask_test_client=flask_client)
    assert sio.is_connected() is True


def test_socket_allowed_when_pin_verified(monkeypatch):
    _pin_on(monkeypatch)
    flask_client = web_server.app.test_client()
    with flask_client.session_transaction() as s:
        s['launch_pin_verified'] = True
    sio = web_server.socketio.test_client(web_server.app, flask_test_client=flask_client)
    assert sio.is_connected() is True


# ── login mode (the "Sign in to SoulSync" path — #852 report was this one) ──
def _login_on(monkeypatch):
    real = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, 'get',
                        lambda k, d=None: True if k == 'security.require_login' else real(k, d))


def test_socket_rejected_when_login_required_and_unauthenticated(monkeypatch):
    _login_on(monkeypatch)
    flask_client = web_server.app.test_client()          # no login_authenticated
    sio = web_server.socketio.test_client(web_server.app, flask_test_client=flask_client)
    assert sio.is_connected() is False                   # login overlay can't be bypassed via WS


def test_socket_allowed_when_login_authenticated(monkeypatch):
    _login_on(monkeypatch)
    flask_client = web_server.app.test_client()
    with flask_client.session_transaction() as s:
        s['login_authenticated'] = True
    sio = web_server.socketio.test_client(web_server.app, flask_test_client=flask_client)
    assert sio.is_connected() is True
