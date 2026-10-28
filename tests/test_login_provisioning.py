"""No-gaps invariant: while login mode is on, every profile must have a login
password. Pure policy seam + endpoint enforcement at every write-point (create,
clear, enable-login)."""

from __future__ import annotations

import os
import tempfile

import pytest

from core.security.login_provisioning import (
    SignInWays, create_refusal, has_a_way_in, members_without_a_way_in,
    profiles_without_oidc, removing_oidc_strands, removing_password_strands)


def _methods(table):
    return lambda pid: table.get(pid, {})


PW = {'password': True, 'plex': False, 'oidc': False}
OIDC = {'password': False, 'plex': False, 'oidc': True}
PLEX = {'password': False, 'plex': True, 'oidc': False}
NONE = {'password': False, 'plex': False, 'oidc': False}


# ── pure policy ─────────────────────────────────────────────────────────────
def test_members_without_a_way_in_flags_only_members_with_none():
    profiles = [
        {'id': 1, 'name': 'Admin', 'is_admin': True},    # admin: own anti-lockout
        {'id': 2, 'name': 'HasPw', 'is_admin': False},
        {'id': 3, 'name': 'NoWay', 'is_admin': False},
    ]
    out = members_without_a_way_in(profiles, _methods({1: NONE, 2: PW, 3: NONE}), SignInWays())
    assert out == [{'id': 3, 'name': 'NoWay'}]


def test_members_without_a_way_in_empty_when_all_set():
    assert members_without_a_way_in([{'id': 2}], _methods({2: PW}), SignInWays()) == []
    assert members_without_a_way_in(None, _methods({}), SignInWays()) == []


def test_an_oidc_or_plex_link_counts_only_while_its_sign_in_is_on():
    # #1839: an oidc-made member used to block login mode for having no password
    assert has_a_way_in(OIDC, SignInWays(oidc=True)) is True
    assert has_a_way_in(OIDC, SignInWays(oidc=False)) is False
    assert has_a_way_in(PLEX, SignInWays(plex=True)) is True
    assert has_a_way_in(PLEX, SignInWays(plex=False)) is False


def test_under_oidc_only_a_password_is_no_way_in():
    assert has_a_way_in(PW, SignInWays(password=False, oidc=True)) is False


def test_profiles_without_oidc_counts_the_admin_and_skips_turned_off():
    profiles = [{'id': 1, 'name': 'Admin'}, {'id': 2, 'name': 'Off', 'disabled': True},
                {'id': 3, 'name': 'Linked'}, {'id': 4, 'name': 'Bare'}]
    out = profiles_without_oidc(profiles, _methods({1: PW, 2: NONE, 3: OIDC, 4: PW}))
    assert out == [{'id': 1, 'name': 'Admin'}, {'id': 4, 'name': 'Bare'}]


def test_create_refusal():
    assert create_refusal(True, has_password=False)
    assert create_refusal(True, has_password=True) is None
    assert create_refusal(False, has_password=False) is None
    assert create_refusal(True, has_password=False, is_admin=True) is None
    # oidc-only: made by hand it could never sign in, password or not
    assert 'invite' in create_refusal(True, has_password=True, oidc_only=True)


def test_removing_password_strands():
    assert removing_password_strands(False) is False
    assert removing_password_strands(True) is True          # nothing else known: refuse
    ways = SignInWays(oidc=True)
    both = {'password': True, 'plex': False, 'oidc': True}
    assert removing_password_strands(True, profile_id=5, methods=both, ways=ways) is False
    assert removing_password_strands(True, profile_id=5, methods=PW, ways=ways) is True
    # the admin's is the way back in: it never comes off in login mode
    assert removing_password_strands(True, profile_id=1, methods=both, ways=ways) is True


def test_removing_oidc_strands():
    both = {'password': True, 'plex': False, 'oidc': True}
    assert removing_oidc_strands(False, methods=OIDC, ways=SignInWays(oidc=True)) is False
    assert removing_oidc_strands(True, methods=both, ways=SignInWays(oidc=True)) is False
    assert removing_oidc_strands(True, methods=OIDC, ways=SignInWays(oidc=True)) is True
    # a plex link whose sign-in is off is no way in (the old guard counted it)
    plex_oidc = {'password': False, 'plex': True, 'oidc': True}
    assert removing_oidc_strands(True, methods=plex_oidc, ways=SignInWays(plex=False, oidc=True)) is True
    # oidc-only: never
    assert removing_oidc_strands(True, methods=both, ways=SignInWays(password=False, oidc=True)) is True


# ── endpoint enforcement ────────────────────────────────────────────────────
_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-prov-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'p.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'
web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def _login_on(monkeypatch, on=True):
    real = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, 'get',
                        lambda k, d=None: on if k == 'security.require_login' else real(k, d))


def _auth(c):
    # Turning login mode on activates the HTTP gate — authenticate the session as
    # admin so the request reaches the endpoint (we're testing the endpoint logic).
    with c.session_transaction() as sess:
        sess['login_authenticated'] = True
        sess['profile_id'] = 1


def test_create_without_password_blocked_when_login_on(monkeypatch, client):
    _login_on(monkeypatch, True); _auth(client)
    r = client.post('/api/profiles', json={'name': 'NoPwMember'})
    assert r.status_code == 400
    assert 'login' in r.get_json()['error'].lower()


def test_create_with_password_succeeds_when_login_on(monkeypatch, client):
    _login_on(monkeypatch, True); _auth(client)
    r = client.post('/api/profiles', json={'name': 'PwMember', 'password': 'secret9'})
    assert r.status_code == 200 and r.get_json()['success'] is True
    pid = r.get_json()['profile_id']
    assert web_server.get_database().verify_profile_password(pid, 'secret9') is True


def test_create_without_password_fine_when_login_off(monkeypatch, client):
    _login_on(monkeypatch, False)
    r = client.post('/api/profiles', json={'name': 'PinOnlyMember'})
    assert r.status_code == 200 and r.get_json()['success'] is True   # no friction when off


def test_clear_password_blocked_when_login_on(monkeypatch, client):
    db = web_server.get_database()
    r = client.post('/api/profiles', json={'name': 'Clearable', 'password': 'x12345'})
    pid = r.get_json()['profile_id']
    _login_on(monkeypatch, True); _auth(client)
    r2 = client.post(f'/api/profiles/{pid}/set-password', json={'password': ''})
    assert r2.status_code == 400 and 'login mode' in r2.get_json()['error'].lower()
    assert db.verify_profile_password(pid, 'x12345') is True          # still set
