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
