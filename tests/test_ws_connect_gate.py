"""#852: the socketio handshake bypasses the HTTP launch-PIN/login gate
(before_request doesn't run for it). The connect handler must enforce the same
check, or removing the overlay + opening a socket streams live data unauthenticated."""

from __future__ import annotations

import os
import tempfile

import pytest

from core.security.ws_gate import is_ws_connection_blocked as blocked
from core.security.ws_gate import is_session_revoked as revoked


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


# ── revocation (#1837): HTTP's epoch/device checks apply to sockets too ────
def _r(**kw):
    base = dict(has_profile_session=True, epoch_current=True, device_live=True)
    base.update(kw)
    return revoked(**base)


def test_no_profile_session_is_never_revoked():
    assert _r(has_profile_session=False, epoch_current=False, device_live=False) is False


def test_live_session_is_not_revoked():
    assert _r() is False


def test_stale_epoch_is_revoked():
    assert _r(epoch_current=False) is True


def test_revoked_device_is_revoked():
    assert _r(device_live=False) is True


def test_revoked_blocks_even_when_fully_authenticated():
    assert _b(session_revoked=True) is True
    assert _b(require_login=True, login_authenticated=True, session_revoked=True) is True


def _session_client(pid, epoch, device_id):
    """a fresh client carrying the session cookie, as a replay would."""
    flask_client = web_server.app.test_client()
    with flask_client.session_transaction() as s:
        s['login_authenticated'] = True
        s['profile_id'] = pid
        s['profile_epoch'] = epoch
        s['device_id'] = device_id
    return flask_client


def _profile_with_device(device_id):
    db = web_server.get_database()
    pid = db.create_profile(f'WsRev-{device_id}')
    assert pid
    assert db.add_profile_device(device_id, pid, 'Browser', '127.0.0.1')
    epoch = (db.get_profile(pid) or {}).get('session_epoch', 0)
    return pid, epoch


def test_socket_allowed_when_session_live(monkeypatch):
    _login_on(monkeypatch)
    pid, epoch = _profile_with_device('dev-live-1837')
    sio = web_server.socketio.test_client(
        web_server.app, flask_test_client=_session_client(pid, epoch, 'dev-live-1837'))
    assert sio.is_connected() is True
    sio.disconnect()


def test_socket_rejected_when_device_revoked(monkeypatch):
    # logout / admin sign-out revokes the device row; the replayed cookie
    # still carries login_authenticated, but HTTP would 401 it — so must WS
    _login_on(monkeypatch)
    pid, epoch = _profile_with_device('dev-revoked-1837')
    db = web_server.get_database()
    assert db.revoke_profile_device(pid, 'dev-revoked-1837')
    from core.security.devices import forget
    forget('dev-revoked-1837')  # the revoke endpoint clears the live cache
    sio = web_server.socketio.test_client(
        web_server.app, flask_test_client=_session_client(pid, epoch, 'dev-revoked-1837'))
    assert sio.is_connected() is False


def test_socket_rejected_when_epoch_bumped(monkeypatch):
    # "sign out everywhere": the profile epoch moves past the session's
    _login_on(monkeypatch)
    pid, epoch = _profile_with_device('dev-epoch-1837')
    db = web_server.get_database()
    assert db.bump_profile_session_epoch(pid) is not None
    from core.security.session_epoch import forget
    forget(pid)  # the sign-out-everywhere endpoint clears the epoch cache
    sio = web_server.socketio.test_client(
        web_server.app, flask_test_client=_session_client(pid, epoch, 'dev-epoch-1837'))
    assert sio.is_connected() is False


def test_socket_rejected_when_revoked_even_with_gates_off():
    # revocation is not part of the login/PIN gate: HTTP applies it whenever
    # the session carries a profile, so a revoked socket is refused even on
    # an install with no gate enabled
    pid, epoch = _profile_with_device('dev-nogate-1837')
    db = web_server.get_database()
    assert db.revoke_profile_device(pid, 'dev-nogate-1837')
    from core.security.devices import forget
    forget('dev-nogate-1837')
    sio = web_server.socketio.test_client(
        web_server.app, flask_test_client=_session_client(pid, epoch, 'dev-nogate-1837'))
    assert sio.is_connected() is False


def test_connected_socket_dropped_when_revoked(monkeypatch):
    _login_on(monkeypatch)
    pid, epoch = _profile_with_device('dev-drop-1837')
    pid2, epoch2 = _profile_with_device('dev-keep-1837')
    dropped = web_server.socketio.test_client(
        web_server.app, flask_test_client=_session_client(pid, epoch, 'dev-drop-1837'))
    kept = web_server.socketio.test_client(
        web_server.app, flask_test_client=_session_client(pid2, epoch2, 'dev-keep-1837'))
    assert dropped.is_connected() is True and kept.is_connected() is True

    db = web_server.get_database()
    assert db.revoke_profile_device(pid, 'dev-drop-1837')
    from core.security.devices import forget
    forget('dev-drop-1837')
    web_server._disconnect_revoked_sockets()

    assert dropped.is_connected() is False   # delivery stops at revocation
    assert kept.is_connected() is True       # other sessions are untouched
    kept.disconnect()


def test_connected_socket_dropped_when_epoch_bumped(monkeypatch):
    # the sweep path for "sign out everywhere", not just device revocation
    _login_on(monkeypatch)
    pid, epoch = _profile_with_device('dev-epoch-drop-1837')
    sio = web_server.socketio.test_client(
        web_server.app, flask_test_client=_session_client(pid, epoch, 'dev-epoch-drop-1837'))
    assert sio.is_connected() is True

    db = web_server.get_database()
    assert db.bump_profile_session_epoch(pid) is not None
    from core.security.session_epoch import forget
    forget(pid)
    web_server._disconnect_revoked_sockets()
    assert sio.is_connected() is False


def test_socket_without_device_id_relies_on_epoch_only(monkeypatch):
    # pre-device sessions carry no device_id: the device check is a no-op
    # for them (same as HTTP), the epoch check still revokes them
    _login_on(monkeypatch)
    db = web_server.get_database()
    pid = db.create_profile('WsRev-nodev-1837')
    assert pid
    epoch = (db.get_profile(pid) or {}).get('session_epoch', 0)
    flask_client = web_server.app.test_client()
    with flask_client.session_transaction() as s:
        s['login_authenticated'] = True
        s['profile_id'] = pid
        s['profile_epoch'] = epoch
    sio = web_server.socketio.test_client(web_server.app, flask_test_client=flask_client)
    assert sio.is_connected() is True

    assert db.bump_profile_session_epoch(pid) is not None
    from core.security.session_epoch import forget
    forget(pid)
    web_server._disconnect_revoked_sockets()
    assert sio.is_connected() is False


def test_revocation_check_fails_open_on_db_error(monkeypatch):
    # the revocation check is auxiliary to the gate: a broken db must read
    # as "not revoked" (mirrors HTTP's fail-open helpers), never throw into
    # the gate and never become a new way to block a sessionless handshake
    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(web_server, 'get_database', _boom)
    with web_server.app.test_request_context('/socket.io/'):
        from flask import session as _session
        _session['profile_id'] = 1
        _session['profile_epoch'] = 0
        _session['device_id'] = 'dev-broken-1837'
        assert web_server._ws_current_session_revoked() is False
        assert web_server._ws_connection_blocked() is False


def test_ws_gate_without_any_session_context_stays_open_when_gates_off():
    # no request context at all: pre-#1837 the gate returned False here
    # without ever touching the session; revocation must not change that
    assert web_server._ws_connection_blocked() is False


def test_socket_rejected_replaying_cookie_after_real_logout(monkeypatch):
    # end-to-end, the issue's exact report: sign in through the real login
    # endpoint, connect, sign out through the real logout endpoint, then
    # replay the pre-logout cookie at the socket
    _login_on(monkeypatch)
    db = web_server.get_database()
    pid = db.create_profile('WsRev-e2e-1837')
    assert pid
    db.set_profile_password(pid, 'hunter2-1837')
    client = web_server.app.test_client()
    res = client.post('/api/auth/login',
                      json={'username': 'WsRev-e2e-1837', 'password': 'hunter2-1837'})
    assert res.status_code == 200
    cookie = client.get_cookie('soulsync_session')
    assert cookie is not None
    sio = web_server.socketio.test_client(web_server.app, flask_test_client=client)
    assert sio.is_connected() is True
    sio.disconnect()
    assert client.post('/api/auth/logout').status_code == 200

    replay = web_server.app.test_client()
    replay.set_cookie('soulsync_session', cookie.value)
    sio2 = web_server.socketio.test_client(web_server.app, flask_test_client=replay)
    assert sio2.is_connected() is False
