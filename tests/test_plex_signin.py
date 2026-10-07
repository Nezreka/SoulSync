"""Sign in with Plex: the pin flow, who maps to which profile, and the routes.

plex.tv is never called: the pin calls are monkeypatched requests, and the
routes run against stubbed plex_signin seams. Mapping rules (module doc of
core/security/plex_signin.py): no access to this server = no entry; the
server owner = admin; a linked plex user = their profile; anyone else = a new
profile when allowed, request-only unless the admin says otherwise.
"""

from __future__ import annotations

import os
import tempfile
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-plexsignin-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'p.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

from core.security import plex_signin  # noqa: E402
from core.security.plex_signin import PlexAccount  # noqa: E402


def _uid():
    return uuid4().hex[:8]


# -- the pin calls --

class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_start_pin_builds_plex_auth_url(monkeypatch):
    seen = {}

    def fake_post(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=params, headers=headers)
        return _Resp({'id': 4242, 'code': 'abc123'})

    monkeypatch.setattr(plex_signin.requests, 'post', fake_post)
    pin = plex_signin.start_pin('soulsync-test')
    assert pin['id'] == 4242
    assert pin['url'].startswith('https://app.plex.tv/auth#?')
    assert 'clientID=soulsync-test' in pin['url'] and 'code=abc123' in pin['url']
    assert seen['headers']['X-Plex-Client-Identifier'] == 'soulsync-test'
    assert seen['params'] == {'strong': 'true'}


def test_check_pin_returns_the_token_only_once_approved(monkeypatch):
    replies = iter([{'authToken': None}, {'authToken': 'acct-token'}])
    monkeypatch.setattr(plex_signin.requests, 'get', lambda *a, **k: _Resp(next(replies)))
    assert plex_signin.check_pin('soulsync-test', 4242) is None
    assert plex_signin.check_pin('soulsync-test', 4242) == 'acct-token'


# -- who maps to which profile --

def test_no_access_to_this_server_means_no_entry():
    db = web_server.get_database()
    r = plex_signin.sign_in(db, PlexAccount('1', 'stranger', None), owner_account_id='9',
                            allow_create=True, default_can_download=False)
    assert r.profile_id is None and 'access' in r.error


def test_the_server_owner_is_the_admin():
    db = web_server.get_database()
    r = plex_signin.sign_in(db, PlexAccount('9', 'owner', 'srv'), owner_account_id='9',
                            allow_create=False, default_can_download=False)
    assert r.profile_id == 1 and r.error is None


def test_signing_in_again_lands_on_the_same_profile_and_refreshes_the_token():
    db = web_server.get_database()
    plex_id = str(int(uuid4().int % 10**9))
    first = plex_signin.sign_in(db, PlexAccount(plex_id, f'Again{_uid()}', 'old-token'), owner_account_id='9',
                                allow_create=True, default_can_download=False)
    again = plex_signin.sign_in(db, PlexAccount(plex_id, 'renamed', 'new-token'), owner_account_id='9',
                                allow_create=False, default_can_download=False)
    assert again.profile_id == first.profile_id and not again.created
    assert db.get_profile_plex_home_user(first.profile_id)['token'] == 'new-token'


def test_a_home_user_link_is_not_proof_of_who_someone_is():
    """mom links her profile to the kid's home user so playlists land there.
    the kid signing in with plex must NOT become mom: any profile can point
    that link at any unprotected home user, so it proves nothing"""
    db = web_server.get_database()
    kid_id = str(int(uuid4().int % 10**9))
    mom = db.create_profile(name=f'Mom{_uid()}', can_download=True)
    db.set_profile_plex_home_user(mom, kid_id, 'Kid', 'kid-server-token')
    r = plex_signin.sign_in(db, PlexAccount(kid_id, f'Kid{_uid()}', 'kid-token'), owner_account_id='9',
                            allow_create=True, default_can_download=False)
    assert r.profile_id != mom and r.created
    assert not db.get_profile(r.profile_id)['can_download']


def test_never_an_admin_profile_except_through_the_owner_check():
    db = web_server.get_database()
    plex_id = str(int(uuid4().int % 10**9))
    boss = db.create_profile(name=f'Boss{_uid()}', is_admin=True)
    db.set_profile_plex_account(boss, plex_id)
    r = plex_signin.sign_in(db, PlexAccount(plex_id, 'boss', 'tok'), owner_account_id='9',
                            allow_create=True, default_can_download=False)
    assert r.profile_id is None and 'admin' in r.error


def test_a_turned_off_profile_cannot_sign_in():
    db = web_server.get_database()
    plex_id = str(int(uuid4().int % 10**9))
    pid = db.create_profile(name=f'Off{_uid()}')
    db.set_profile_plex_account(pid, plex_id)
    db.update_profile(pid, disabled=1)
    r = plex_signin.sign_in(db, PlexAccount(plex_id, 'off', 'tok'), owner_account_id='9',
                            allow_create=True, default_can_download=False)
    assert r.profile_id is None and 'turned off' in r.error


def test_a_new_plex_user_gets_a_request_only_profile_linked_to_them():
    db = web_server.get_database()
    plex_id = str(int(uuid4().int % 10**9))
    name = f'Friend{_uid()}'
    r = plex_signin.sign_in(db, PlexAccount(plex_id, name, 'friend-token'), owner_account_id='9',
                            allow_create=True, default_can_download=False)
    assert r.created and r.profile_id
    profile = db.get_profile(r.profile_id)
    assert profile['name'] == name and not profile['is_admin'] and not profile['can_download']
    assert db.get_profile_plex_home_user(r.profile_id) == {'id': plex_id, 'title': name, 'token': 'friend-token'}
    assert db.get_profile_by_plex_account(plex_id)['id'] == r.profile_id


def test_a_taken_name_gets_a_number():
    db = web_server.get_database()
    name = f'Taken{_uid()}'
    db.create_profile(name=name)
    r = plex_signin.sign_in(db, PlexAccount(str(int(uuid4().int % 10**9)), name, 'tok'), owner_account_id='9',
                            allow_create=True, default_can_download=True)
    assert db.get_profile(r.profile_id)['name'] == f'{name} 2'
    assert db.get_profile(r.profile_id)['can_download']


def test_no_new_profiles_when_the_admin_says_so():
    db = web_server.get_database()
    r = plex_signin.sign_in(db, PlexAccount(str(int(uuid4().int % 10**9)), 'newbie', 'tok'),
                            owner_account_id='9', allow_create=False, default_can_download=False)
    assert r.profile_id is None and 'linked' in r.error


# -- the routes --

@pytest.fixture
def client():
    return web_server.app.test_client()


def _config(monkeypatch, **over):
    values = {'security.require_login': True, 'security.plex_signin': True,
              'security.plex_signin_auto_create': True, 'security.plex_signin_default_can_download': False}
    values.update(over)
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, 'get',
                        lambda k, d=None: values[k] if k in values else real_get(k, d))
    web_server._login_limiter.reset()
    import api.login as login_api
    login_api.plex_start_limiter.reset()
    monkeypatch.setattr(login_api, '_PLEX_CHECK_MIN_INTERVAL', 0)


def _stub_plex(monkeypatch, *, approve_after=1, account=None):
    calls = {'checks': 0}
    monkeypatch.setattr(plex_signin, 'client_identifier', lambda cm: 'soulsync-test')
    monkeypatch.setattr(plex_signin, 'start_pin', lambda cid, forward_url='': {'id': 77, 'url': 'https://app.plex.tv/auth#?x'})

    def check(cid, pin_id):
        calls['checks'] += 1
        assert pin_id == 77
        return 'acct-token' if calls['checks'] > approve_after else None

    monkeypatch.setattr(plex_signin, 'check_pin', check)
    monkeypatch.setattr(plex_signin, 'server_machine_id', lambda plex: 'machine-1')
    monkeypatch.setattr(plex_signin, 'owner_account_id', lambda plex: '9')
    acct = account or PlexAccount(str(int(uuid4().int % 10**9)), f'Routed{_uid()}', 'route-token')
    monkeypatch.setattr(plex_signin, 'resolve_account', lambda token, machine: acct)
    return acct


_GATED = '/api/profiles/me/connections'


def test_available_follows_the_setting(client, monkeypatch):
    _config(monkeypatch, **{'security.plex_signin': False})
    assert client.get('/api/auth/plex/available').get_json()['enabled'] is False
    _config(monkeypatch)
    assert client.get('/api/auth/plex/available').get_json()['enabled'] is True


def test_turned_off_means_no_flow(client, monkeypatch):
    _config(monkeypatch, **{'security.plex_signin': False})
    assert client.post('/api/auth/plex/start').status_code == 403
    assert client.post('/api/auth/plex/check').status_code == 403


def test_full_flow_pending_then_signed_in(client, monkeypatch):
    _config(monkeypatch)
    acct = _stub_plex(monkeypatch, approve_after=1)
    assert client.get(_GATED).status_code == 401

    start = client.post('/api/auth/plex/start')
    assert start.status_code == 200 and start.get_json()['url'].startswith('https://app.plex.tv/')

    pending = client.post('/api/auth/plex/check').get_json()
    assert pending == {'success': True, 'pending': True}
    assert client.get(_GATED).status_code == 401

    done = client.post('/api/auth/plex/check').get_json()
    assert done['success'] and done['pending'] is False and done['created'] is True
    assert done['profile']['name'] == acct.username
    assert client.get(_GATED).status_code == 200  # signed in for real


def test_the_pin_belongs_to_the_browser_that_started_it(client, monkeypatch):
    _config(monkeypatch)
    _stub_plex(monkeypatch, approve_after=0)
    client.post('/api/auth/plex/start')
    stranger = web_server.app.test_client()
    resp = stranger.post('/api/auth/plex/check')
    assert resp.status_code == 400
    assert stranger.get(_GATED).status_code == 401


def test_an_account_without_access_is_turned_away(client, monkeypatch):
    _config(monkeypatch)
    _stub_plex(monkeypatch, approve_after=0, account=PlexAccount('123', 'outsider', None))
    client.post('/api/auth/plex/start')
    resp = client.post('/api/auth/plex/check')
    assert resp.status_code == 403
    assert client.get(_GATED).status_code == 401


def test_password_login_still_works_after_the_refactor(client, monkeypatch):
    _config(monkeypatch)
    db = web_server.get_database()
    name = f'Pw{_uid()}'
    pid = db.create_profile(name=name)
    db.set_profile_password(pid, 'secretpw')
    r = client.post('/api/auth/login', json={'username': name, 'password': 'secretpw'})
    assert r.status_code == 200
    assert client.get(_GATED).status_code == 200


def test_starts_are_rate_limited_even_when_they_succeed(client, monkeypatch):
    _config(monkeypatch)
    _stub_plex(monkeypatch)
    codes = [client.post('/api/auth/plex/start').status_code for _ in range(10)]
    assert codes[:8] == [200] * 8
    assert codes[8] == 429


def test_polling_faster_than_a_second_never_reaches_plex(client, monkeypatch):
    _config(monkeypatch)
    import api.login as login_api
    monkeypatch.setattr(login_api, '_PLEX_CHECK_MIN_INTERVAL', 60)
    calls = []
    _stub_plex(monkeypatch)
    monkeypatch.setattr(plex_signin, 'check_pin', lambda cid, pin: calls.append(pin))
    client.post('/api/auth/plex/start')
    client.post('/api/auth/plex/check')
    assert client.post('/api/auth/plex/check').get_json() == {'success': True, 'pending': True}
    assert len(calls) == 1


def test_a_blip_from_plex_keeps_waiting_an_expired_pin_starts_over(client, monkeypatch):
    import requests
    _config(monkeypatch)
    _stub_plex(monkeypatch)
    client.post('/api/auth/plex/start')

    def blip(cid, pin):
        raise requests.ConnectionError('plex.tv hiccup')
    monkeypatch.setattr(plex_signin, 'check_pin', blip)
    assert client.post('/api/auth/plex/check').get_json() == {'success': True, 'pending': True}

    class _Gone:
        status_code = 404

    def expired(cid, pin):
        raise requests.HTTPError('404', response=_Gone())
    monkeypatch.setattr(plex_signin, 'check_pin', expired)
    r = client.post('/api/auth/plex/check')
    assert r.status_code == 410 and 'expired' in r.get_json()['error']
    assert client.post('/api/auth/plex/check').status_code == 400  # the pin is gone
