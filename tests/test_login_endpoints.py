"""Username/password login endpoints + gate (opt-in login mode)."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-login-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'l.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def _enable_login(monkeypatch):
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, 'get',
                        lambda k, d=None: True if k == 'security.require_login' else real_get(k, d))
    web_server._login_limiter.record_success('127.0.0.1')  # clean slate


_GATED = '/api/profiles/me/connections'   # a normal, non-allowlisted endpoint


def test_login_gate_blocks_then_authenticated_access(client, monkeypatch):
    db = web_server.get_database()
    pid = db.create_profile(name='LoginUser')
    db.set_profile_password(pid, 'secretpw')
    _enable_login(monkeypatch)

    assert client.get(_GATED).status_code == 401                       # not logged in → blocked

    r = client.post('/api/auth/login', json={'username': 'LoginUser', 'password': 'secretpw'})
    assert r.status_code == 200 and r.get_json()['success'] is True

    assert client.get(_GATED).status_code == 200                       # authenticated → in

    assert client.post('/api/auth/logout').get_json()['success'] is True
    assert client.get(_GATED).status_code == 401                       # logged out → blocked again


def test_login_is_case_insensitive_on_username(client, monkeypatch):
    db = web_server.get_database()
    db.set_profile_password(db.create_profile(name='CaseUser'), 'pw')
    _enable_login(monkeypatch)
    assert client.post('/api/auth/login', json={'username': 'caseuser', 'password': 'pw'}).status_code == 200


def test_wrong_password_401_generic(client, monkeypatch):
    db = web_server.get_database()
    db.set_profile_password(db.create_profile(name='WrongPwUser'), 'right')
    _enable_login(monkeypatch)
    r = client.post('/api/auth/login', json={'username': 'WrongPwUser', 'password': 'nope'})
    assert r.status_code == 401
    assert 'username or password' in r.get_json()['error'].lower()     # generic — no name-leak


def test_passwordless_profile_cannot_login(client, monkeypatch):
    db = web_server.get_database()
    db.create_profile(name='NoPwUser')   # no password set
    _enable_login(monkeypatch)
    assert client.post('/api/auth/login', json={'username': 'NoPwUser', 'password': 'x'}).status_code == 401


def test_unknown_user_401(client, monkeypatch):
    _enable_login(monkeypatch)
    assert client.post('/api/auth/login', json={'username': 'ghost', 'password': 'x'}).status_code == 401


def test_cannot_enable_login_without_admin_password(client):
    # admin (1) has no password → enabling login mode is refused (anti-lockout)
    web_server.get_database().set_profile_password(1, '')
    r = client.post('/api/settings', json={'security': {'require_login': True}})
    assert r.status_code == 400
    assert 'password' in r.get_json().get('error', '').lower()


def test_set_password_endpoint(client):
    db = web_server.get_database()
    pid = db.create_profile(name='SetPwTest')
    # admin (default session) can set any profile's login password
    r = client.post(f'/api/profiles/{pid}/set-password', json={'password': 'newpw123'})
    body = r.get_json()
    assert body['success'] is True and body['has_password'] is True
    assert db.verify_profile_password(pid, 'newpw123') is True
    # clearing it
    assert client.post(f'/api/profiles/{pid}/set-password', json={'password': ''}).get_json()['has_password'] is False


def test_profiles_current_signals_login_required(client, monkeypatch):
    _enable_login(monkeypatch)
    body = client.get('/api/profiles/current').get_json()
    assert body.get('login_required') is True   # frontend uses this to show the sign-in screen


def test_pin_gate_unaffected_when_login_off(client, monkeypatch):
    # THE guarantee: with login mode OFF (default) and the launch PIN ON, the PIN
    # gate must STILL enforce — the login feature must not weaken or bypass it.
    real_get = web_server.config_manager.get
    def fake_get(key, default=None):
        if key == 'security.require_login':
            return False                       # login OFF (default)
        if key == 'security.require_pin_on_launch':
            return True                        # PIN ON
        return real_get(key, default)
    monkeypatch.setattr(web_server.config_manager, 'get', fake_get)

    # Unverified session, PIN required → the launch-PIN gate still 401s.
    assert client.get('/api/profiles/me/connections').status_code == 401
    # And /api/profiles/current reports the PIN screen, NOT login.
    body = client.get('/api/profiles/current').get_json()
    assert body.get('login_required') is not True


def test_everything_normal_when_both_off(client, monkeypatch):
    # Default install: login OFF + PIN OFF → no gate at all (today's behavior).
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, 'get',
        lambda k, d=None: False if k in ('security.require_login', 'security.require_pin_on_launch') else real_get(k, d))
    assert client.get('/api/profiles/me/connections').status_code == 200   # reachable, unguarded


def test_recovery_flow_resets_password(client, monkeypatch):
    db = web_server.get_database()
    pid = db.create_profile(name='RecoverMe')
    db.set_profile_password(pid, 'oldpassword')
    db.set_profile_recovery(pid, 'First pet?', 'Rex')
    _enable_login(monkeypatch)

    # forgot-password flow is reachable pre-auth
    q = client.get('/api/auth/recovery-question?username=RecoverMe').get_json()
    assert q['success'] and q['question'] == 'First pet?'

    # wrong answer → 401, password unchanged
    bad = client.post('/api/auth/recovery-reset',
                      json={'username': 'RecoverMe', 'answer': 'Fido', 'new_password': 'newpass1'})
    assert bad.status_code == 401
    assert db.verify_profile_password(pid, 'oldpassword') is True

    # correct answer → password reset + authenticated
    ok = client.post('/api/auth/recovery-reset',
                     json={'username': 'RecoverMe', 'answer': 'rex', 'new_password': 'brandnew1'})
    assert ok.status_code == 200 and ok.get_json()['success'] is True
    assert db.verify_profile_password(pid, 'brandnew1') is True
    assert db.verify_profile_password(pid, 'oldpassword') is False


def test_recovery_question_404_for_unknown(client, monkeypatch):
    _enable_login(monkeypatch)
    assert client.get('/api/auth/recovery-question?username=ghost').status_code == 404


def test_set_recovery_endpoint(client):
    db = web_server.get_database()
    pid = db.create_profile(name='SetRec')
    r = client.post(f'/api/profiles/{pid}/set-recovery', json={'question': 'Q?', 'answer': 'A'})
    assert r.get_json()['has_recovery'] is True
    assert db.verify_profile_recovery_answer(pid, 'a') is True
