"""Sign in with OIDC only (#1839).

An optional login-mode setting: while it's on, OIDC is the one interactive
way in. Password sign-in, the recovery question, Plex sign-in, a password on
an invite and switching profiles with a password answer 403; a session that
didn't sign in with OIDC ends, on HTTP and on its socket. API keys are
untouched. It only turns on from a session that signed in with OIDC as that
admin, with every usable profile linked. Invites send the person to the
provider and link the account that comes back. SOULSYNC_OIDC_ONLY_OVERRIDE
turns it off from the environment. The provisioning rules count a Plex or
OIDC link as a way in while its sign-in is on.

No provider is ever called: discovery is stubbed and the token / userinfo
endpoints are monkeypatched requests.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-oidconly-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'o.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

import requests  # noqa: E402

from core.security import devices, oidc_only, oidc_session, oidc_signin, session_epoch  # noqa: E402

ISSUER = 'https://id.example.test'
DOC = {
    'issuer': ISSUER,
    'authorization_endpoint': f'{ISSUER}/authorize',
    'token_endpoint': f'{ISSUER}/api/oidc/token',
    'userinfo_endpoint': f'{ISSUER}/api/oidc/userinfo',
}
_GATED = '/api/profiles/me/connections'


def _uid():
    return uuid4().hex[:8]


def _identity(sub):
    return f'{ISSUER}|{sub}'


def _jwt(claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b'=').decode()  # noqa: E731
    return f"{enc({'alg': 'RS256', 'typ': 'JWT'})}.{enc(claims)}.c2ln"


class _Resp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'HTTP {self.status_code}')

    def json(self):
        return self._data


class _Provider:
    def __init__(self, sub, groups=None, username=None):
        self.sub = sub
        self.groups = groups
        self.username = username or f'user-{sub}'
        self.nonce = None

    def post(self, url, data=None, auth=None, timeout=None, headers=None):
        claims = {'iss': ISSUER, 'aud': 'soulsync', 'sub': self.sub, 'exp': time.time() + 300,
                  'iat': time.time(), 'nonce': self.nonce, 'preferred_username': self.username}
        if self.groups is not None:
            claims['groups'] = list(self.groups)
        return _Resp({'access_token': 'at-0', 'id_token': _jwt(claims), 'expires_in': 3600,
                      'refresh_token': 'rt-0', 'token_type': 'Bearer'})

    def get(self, url, timeout=None, headers=None):
        info = {'sub': self.sub, 'preferred_username': self.username}
        if self.groups is not None:
            info['groups'] = list(self.groups)
        return _Resp(info)


def _profiles_snapshot():
    with web_server.get_database()._get_connection() as conn:
        return {r['id']: (r['password_hash'], r['oidc_identity'], r['session_epoch'])
                for r in conn.execute('SELECT id, password_hash, oidc_identity, session_epoch FROM profiles')}


def _profiles_restore(before):
    """the worker's database is shared with every other test module: put the
    profiles back as they were (links, passwords, epochs) and drop the ones
    this test made, so an admin session elsewhere isn't signed out by them"""
    with web_server.get_database()._get_connection() as conn:
        for pid, (pw, ident, epoch) in before.items():
            conn.execute('UPDATE profiles SET password_hash = ?, oidc_identity = ?, session_epoch = ? '
                         'WHERE id = ?', (pw, ident, epoch, pid))
        keep = ','.join(str(int(i)) for i in before) or '0'
        conn.execute(f'DELETE FROM profiles WHERE id NOT IN ({keep})')
        conn.commit()


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    oidc_signin.forget_discovery()
    oidc_session._reset_for_tests()
    devices._reset_for_tests()
    session_epoch._reset_for_tests()
    monkeypatch.setattr(oidc_signin, 'discover', lambda issuer, now=None: DOC)
    monkeypatch.delenv(oidc_only.OVERRIDE_ENV, raising=False)
    oidc_only.reload_override_for_tests()
    before = _profiles_snapshot()
    yield
    _profiles_restore(before)
    oidc_session._reset_for_tests()
    devices._reset_for_tests()
    session_epoch._reset_for_tests()
    monkeypatch.delenv(oidc_only.OVERRIDE_ENV, raising=False)
    oidc_only.reload_override_for_tests()
    # a save in a test must not leak into the next one
    web_server.config_manager.set('security.oidc_only', False)


@pytest.fixture
def client():
    return web_server.app.test_client()


def _config(monkeypatch, **over):
    values = {'security.require_login': True, 'security.oidc_signin': True,
              'security.oidc_issuer': ISSUER, 'security.oidc_client_id': 'soulsync',
              'security.oidc_client_secret': 's3cret', 'security.plex_signin': False,
              'security.oidc_signin_auto_create': True, 'security.oidc_signin_default_can_download': False,
              'security.oidc_required_group': ''}
    values.update(over)
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, 'get',
                        lambda k, d=None: values[k] if k in values else real_get(k, d))
    import api.login as login_api
    login_api.oidc_start_limiter.reset()
    return values


def _install(monkeypatch, provider):
    monkeypatch.setattr(oidc_signin.requests, 'post', provider.post)
    monkeypatch.setattr(oidc_signin.requests, 'get', provider.get)
    return provider


def _finish(client, location, provider):
    query = parse_qs(urlparse(location).query)
    provider.nonce = query['nonce'][0]
    return client.get(f"/api/auth/oidc/callback?code=c&state={query['state'][0]}")


def _sign_in(client, provider):
    r = client.get('/api/auth/oidc/start')
    assert r.status_code == 302
    done = _finish(client, r.headers['Location'], provider)
    assert done.status_code == 302
    with client.session_transaction() as s:
        return s.get('profile_id'), s.get('device_id')


def _password_session(client, pid):
    """a browser signed in some other way: a device row with no oidc account"""
    db = web_server.get_database()
    device = 'pw-' + _uid()
    assert db.add_profile_device(device, pid, 'test', '127.0.0.1')
    with client.session_transaction() as s:
        s['login_authenticated'] = True
        s['profile_id'] = pid
        s['profile_epoch'] = (db.get_profile(pid) or {}).get('session_epoch', 0)
        s['device_id'] = device
        s['device_owner'] = pid
    return device


def _link_everyone(except_ids=()):
    db = web_server.get_database()
    for p in db.get_all_profiles():
        if p['id'] in except_ids:
            continue
        if not db.profile_sign_in_methods(p['id'])['oidc']:
            db.set_profile_oidc_identity(p['id'], _identity('linked-' + _uid()))


def _admin_oidc_session(client, monkeypatch):
    """the admin signed in with oidc: profile 1 linked, then a full sign-in"""
    sub = 'admin-' + _uid()
    db = web_server.get_database()
    db.set_profile_password(1, 'adminpw1')
    db.set_profile_oidc_identity(1, _identity(sub))
    p = _install(monkeypatch, _Provider(sub, groups=['music']))
    pid, device = _sign_in(client, p)
    assert pid == 1
    return device


# -- turning it on (B1, B9) --

def test_it_turns_on_from_an_oidc_session_with_everyone_linked(client, monkeypatch):
    _config(monkeypatch)
    _admin_oidc_session(client, monkeypatch)
    _link_everyone()
    r = client.post('/api/settings', json={'security': {'oidc_only': True}})
    assert r.status_code == 200, r.get_json()
    assert web_server.config_manager.get('security.oidc_only') is True


def test_a_password_session_cannot_turn_it_on(client, monkeypatch):
    _config(monkeypatch)
    _link_everyone()
    _password_session(client, 1)
    r = client.post('/api/settings', json={'security': {'oidc_only': True}})
    assert r.status_code == 400
    assert 'sign back in with oidc' in r.get_json()['error'].lower()


def test_an_oidc_session_for_a_link_that_changed_cannot_turn_it_on(client, monkeypatch):
    _config(monkeypatch)
    _admin_oidc_session(client, monkeypatch)
    _link_everyone()
    web_server.get_database().set_profile_oidc_identity(1, _identity('someone-new'))
    oidc_session._reset_for_tests()
    r = client.post('/api/settings', json={'security': {'oidc_only': True}})
    # #1838 already ends a session whose link moved; either way it isn't on
    assert r.status_code in (400, 401)
    assert not web_server.config_manager.get('security.oidc_only')


def test_it_needs_login_mode_and_a_provider(client, monkeypatch):
    _config(monkeypatch)
    _admin_oidc_session(client, monkeypatch)
    _link_everyone()
    r = client.post('/api/settings', json={'security': {'oidc_only': True, 'require_login': False}})
    assert r.status_code == 400 and 'login mode' in r.get_json()['error']
    r = client.post('/api/settings', json={'security': {'oidc_only': True, 'oidc_issuer': ''}})
    assert r.status_code == 400 and 'provider' in r.get_json()['error']


def test_unlinked_profiles_are_named_and_turned_off_ones_skipped(client, monkeypatch):
    _config(monkeypatch)
    db = web_server.get_database()
    bare = db.create_profile(name='Bare-' + _uid())
    off = db.create_profile(name='Off-' + _uid())
    db.update_profile(off, disabled=1)
    _admin_oidc_session(client, monkeypatch)
    _link_everyone(except_ids=(bare, off))
    r = client.post('/api/settings', json={'security': {'oidc_only': True}})
    body = r.get_json()
    assert r.status_code == 400
    ids = [m['id'] for m in body['unlinked']]
    assert ids == [bare]
    assert 'connect oidc from my account' in body['error'].lower()


def test_an_api_key_cannot_turn_it_on(monkeypatch):
    from core.security.oidc_only import check_security_change
    _config(monkeypatch)
    _link_everyone()
    refusal = check_security_change(web_server.config_manager, {'oidc_only': True},
                                    web_server.get_database(), device_id=None, profile_id=None)
    assert refusal and 'oidc' in refusal['error'].lower()


# -- while it's on --

def _on(monkeypatch, **over):
    return _config(monkeypatch, **{'security.oidc_only': True, **over})


def test_oidc_sign_in_and_login_mode_cannot_be_turned_off_under_it(monkeypatch):
    from core.security.oidc_only import check_security_change
    _on(monkeypatch)
    db = web_server.get_database()
    for change in ({'oidc_signin': False}, {'oidc_client_id': ''}, {'require_login': False}):
        refusal = check_security_change(web_server.config_manager, change, db,
                                        device_id=None, profile_id=None)
        assert refusal and 'turn it off' in refusal['error'].lower(), change


def test_turning_it_off_is_always_allowed(monkeypatch):
    from core.security.oidc_only import check_security_change
    _on(monkeypatch)
    assert check_security_change(web_server.config_manager, {'oidc_only': False},
                                 web_server.get_database(), device_id=None, profile_id=None) is None


@pytest.mark.parametrize('method,path,body', [
    ('post', '/api/auth/login', {'username': 'x', 'password': 'y'}),
    ('get', '/api/auth/recovery-question?username=x', None),
    ('post', '/api/auth/recovery-reset', {'username': 'x', 'answer': 'a', 'new_password': 'abcdef'}),
    ('post', '/api/auth/plex/start', {}),
    ('post', '/api/auth/plex/check', {}),
])
def test_the_other_sign_ins_are_closed(client, monkeypatch, method, path, body):
    _on(monkeypatch, **{'security.plex_signin': True})
    r = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
    assert r.status_code == 403
    assert r.get_json()['oidc_only'] is True


def test_they_open_again_when_it_is_off(client, monkeypatch):
    _config(monkeypatch)
    r = client.post('/api/auth/login', json={'username': 'ghost', 'password': 'x'})
    assert r.status_code == 401


def test_switching_profile_with_a_password_is_closed(client, monkeypatch):
    _on(monkeypatch)
    db = web_server.get_database()
    other = db.create_profile(name='Other-' + _uid())
    db.set_profile_password(other, 'pw12345')
    _admin_oidc_session(client, monkeypatch)
    r = client.post('/api/profiles/select', json={'profile_id': other, 'password': 'pw12345'})
    assert r.status_code == 403 and r.get_json()['oidc_only'] is True


def test_the_login_screen_hears_about_it_and_plex_hides(client, monkeypatch):
    _on(monkeypatch, **{'security.plex_signin': True})
    assert client.get('/api/auth/oidc/available').get_json()['oidc_only'] is True
    assert client.get('/api/auth/plex/available').get_json()['enabled'] is False


def test_api_keys_are_untouched():
    # /api/v1 is the key's credential: no profile gate, no sign-in
    from core.security.session_profile import no_profile_request_is_blocked
    assert no_profile_request_is_blocked('/api/v1/library/artists', 'GET') is False


# -- sessions (B5) --

def test_a_password_session_ends_when_it_is_on(client, monkeypatch):
    _on(monkeypatch)
    db = web_server.get_database()
    pid = db.create_profile(name='PwSess-' + _uid())
    device = _password_session(client, pid)
    assert client.get(_GATED).status_code == 401
    assert db.get_profile_device(device)['revoked_at'] is not None


def test_the_same_session_lives_while_it_is_off(client, monkeypatch):
    _config(monkeypatch)
    pid = web_server.get_database().create_profile(name='PwOk-' + _uid())
    _password_session(client, pid)
    assert client.get(_GATED).status_code == 200


def test_an_oidc_session_is_kept(client, monkeypatch):
    _on(monkeypatch)
    p = _install(monkeypatch, _Provider('keep-' + _uid()))
    _sign_in(client, p)
    assert client.get(_GATED).status_code == 200


def test_a_session_with_no_device_ends_but_the_warmer_stays(client, monkeypatch):
    _on(monkeypatch)
    pid = web_server.get_database().create_profile(name='NoDev-' + _uid())
    with client.session_transaction() as s:
        s['login_authenticated'] = True
        s['profile_id'] = pid
    assert client.get(_GATED).status_code == 401
    with client.session_transaction() as s:
        s['login_authenticated'] = True
        s['profile_id'] = pid
        s['internal_warmup'] = True
    assert client.get(_GATED).status_code == 200


def test_the_socket_check_ends_a_password_session(monkeypatch):
    _on(monkeypatch)
    db = web_server.get_database()
    pid = db.create_profile(name='Sock-' + _uid())
    device = 'pw-' + _uid()
    db.add_profile_device(device, pid, 'test', '127.0.0.1')
    snap = {'profile_id': pid, 'profile_epoch': 0, 'device_id': device}
    assert web_server._ws_snapshot_revoked(snap) is True
    assert web_server._ws_snapshot_revoked({'profile_id': pid, 'profile_epoch': 0, 'device_id': None}) is True


def test_the_socket_check_leaves_it_alone_when_off(monkeypatch):
    _config(monkeypatch)
    db = web_server.get_database()
    pid = db.create_profile(name='SockOff-' + _uid())
    device = 'pw-' + _uid()
    db.add_profile_device(device, pid, 'test', '127.0.0.1')
    assert web_server._ws_snapshot_revoked({'profile_id': pid, 'profile_epoch': 0, 'device_id': device}) is False


def test_the_status_counts_what_it_would_sign_out(client, monkeypatch):
    _config(monkeypatch)
    _admin_oidc_session(client, monkeypatch)
    db = web_server.get_database()
    before = db.count_live_devices_without_oidc()
    db.add_profile_device('pw-' + _uid(), 1, 'test', '127.0.0.1')
    body = client.get('/api/security/oidc-only').get_json()
    assert body['would_sign_out'] == before + 1
    assert body['session_ok'] is True and body['overridden'] is False and body['on'] is False


def test_the_count_skips_devices_not_used_lately(monkeypatch):
    # a row outlives its session (cleared cookie, sign out everywhere), so a
    # device last used before the window isn't counted; revoked ones never are
    _config(monkeypatch)
    db = web_server.get_database()
    before = db.count_live_devices_without_oidc(7)
    recent, stale, gone = ('pw-' + _uid() for _ in range(3))
    for device in (recent, stale, gone):
        db.add_profile_device(device, 1, 'test', '127.0.0.1')
    with db._get_connection() as conn:
        conn.execute("UPDATE profile_devices SET last_seen = datetime('now', '-8 days') WHERE id = ?", (stale,))
        conn.commit()
    db.revoke_profile_device(1, gone)
    try:
        assert db.count_live_devices_without_oidc(7) == before + 1
        assert db.count_live_devices_without_oidc(30) == before + 2
    finally:
        for device in (recent, stale):
            db.revoke_profile_device(1, device)


# -- making profiles (B6) and passwords (B4, B10) --

def test_a_profile_made_by_hand_is_refused(client, monkeypatch):
    _on(monkeypatch)
    _admin_oidc_session(client, monkeypatch)
    r = client.post('/api/profiles', json={'name': 'Hand-' + _uid(), 'password': 'abcdef1'})
    assert r.status_code == 400 and 'invite' in r.get_json()['error']


def test_the_admin_password_never_comes_off(client, monkeypatch):
    _config(monkeypatch)
    _admin_oidc_session(client, monkeypatch)
    r = client.post('/api/profiles/1/set-password', json={'password': ''})
    assert r.status_code == 400
    assert web_server.get_database().profile_has_password(1)


def test_an_oidc_linked_member_can_drop_their_password(client, monkeypatch):
    _config(monkeypatch)
    db = web_server.get_database()
    pid = db.create_profile(name='Drop-' + _uid())
    db.set_profile_password(pid, 'abcdef1')
    db.set_profile_oidc_identity(pid, _identity('drop-' + _uid()))
    _admin_oidc_session(client, monkeypatch)
    r = client.post(f'/api/profiles/{pid}/set-password', json={'password': ''})
    assert r.status_code == 200 and r.get_json()['has_password'] is False


def test_under_it_nobody_disconnects_oidc(client, monkeypatch):
    _on(monkeypatch)
    _admin_oidc_session(client, monkeypatch)
    r = client.delete('/api/profiles/me/oidc')
    assert r.status_code == 400 and 'oidc only' in r.get_json()['error'].lower()


def test_login_mode_turns_on_with_an_oidc_made_member(client, monkeypatch):
    # #1839's provisioning item: an oidc-linked member with no password
    # used to block login mode
    from core.security.oidc_only import check_security_change
    _config(monkeypatch, **{'security.require_login': False})
    db = web_server.get_database()
    for p in db.get_all_profiles():
        if p['id'] != 1 and not db.profile_sign_in_methods(p['id'])['password']:
            db.set_profile_oidc_identity(p['id'], _identity('m-' + _uid()))
    db.set_profile_password(1, 'adminpw1')
    assert check_security_change(web_server.config_manager, {'require_login': True}, db,
                                 device_id=None, profile_id=None) is None


def test_login_mode_still_refuses_a_member_with_no_way_in(monkeypatch):
    from core.security.oidc_only import check_security_change
    _config(monkeypatch, **{'security.require_login': False})
    db = web_server.get_database()
    bare = db.create_profile(name='NoWay-' + _uid())
    refusal = check_security_change(web_server.config_manager, {'require_login': True}, db,
                                    device_id=None, profile_id=None)
    assert refusal and bare in [m['id'] for m in refusal['stranded']]


def test_turning_plex_sign_in_off_cannot_strand_a_plex_member(monkeypatch):
    from core.security.oidc_only import check_security_change
    _config(monkeypatch, **{'security.plex_signin': True})
    db = web_server.get_database()
    pid = db.create_profile(name='PlexOnly-' + _uid())
    db.set_profile_plex_account(pid, 'plex-' + _uid())
    refusal = check_security_change(web_server.config_manager, {'plex_signin': False}, db,
                                    device_id=None, profile_id=None)
    assert refusal and pid in [m['id'] for m in refusal['stranded']]
    assert check_security_change(web_server.config_manager, {'plex_signin': True}, db,
                                 device_id=None, profile_id=None) is None


def test_turning_oidc_sign_in_off_cannot_strand_an_oidc_member(monkeypatch):
    from core.security.oidc_only import check_security_change
    _config(monkeypatch)
    db = web_server.get_database()
    pid = db.create_profile(name='OidcOnly-' + _uid())
    db.set_profile_oidc_identity(pid, _identity('oo-' + _uid()))
    refusal = check_security_change(web_server.config_manager, {'oidc_signin': False}, db,
                                    device_id=None, profile_id=None)
    assert refusal and pid in [m['id'] for m in refusal['stranded']]


# -- invites (B3) --

def _invite(client, monkeypatch):
    _admin_oidc_session(client, monkeypatch)
    r = client.post('/api/profiles/invites', json={'preset': {'allowed_sides': 'both'}})
    assert r.status_code == 201
    token = r.get_json()['token']
    client.post('/api/auth/logout')
    with client.session_transaction() as s:
        s.clear()
    return token


def _invite_row(token):
    import hashlib
    return web_server.get_database().get_profile_invite_by_hash(hashlib.sha256(token.encode()).hexdigest())


def test_the_invite_page_asks_for_no_password(client, monkeypatch):
    _on(monkeypatch)
    token = _invite(client, monkeypatch)
    body = client.get(f'/api/invite/{token}').get_json()
    assert body['oidc'] is True and body['password_required'] is False


def test_an_invite_links_the_account_that_comes_back(client, monkeypatch):
    _on(monkeypatch)
    token = _invite(client, monkeypatch)
    name = 'Inv-' + _uid()
    r = client.post(f'/api/invite/{token}/accept', json={'name': name})
    assert r.status_code == 200 and r.get_json()['oidc'] is True
    assert _invite_row(token)['used_at'] is None          # nothing spent yet
    sub = 'new-' + _uid()
    p = _install(monkeypatch, _Provider(sub))
    done = _finish(client, r.get_json()['url'], p)
    assert done.status_code == 302 and '/invite/' not in done.headers['Location']
    db = web_server.get_database()
    prof = db.get_profile_by_oidc_identity(_identity(sub))
    assert prof and prof['name'] == name
    assert db.get_profile(prof['id'])['allowed_sides'] == 'both'
    assert _invite_row(token)['used_at'] is not None
    with client.session_transaction() as s:
        assert s['profile_id'] == prof['id']
        device = s['device_id']
    assert db.get_profile_device_oidc(device)['identity'] == _identity(sub)
    assert client.get(_GATED).status_code == 200          # an oidc session, kept under oidc-only


def test_an_account_that_already_has_a_profile_leaves_the_invite_unspent(client, monkeypatch):
    _on(monkeypatch)
    sub = 'taken-' + _uid()
    db = web_server.get_database()
    owner = db.create_profile(name='Owner-' + _uid())
    db.set_profile_oidc_identity(owner, _identity(sub))
    token = _invite(client, monkeypatch)
    r = client.post(f'/api/invite/{token}/accept', json={'name': 'Dup-' + _uid()})
    done = _finish(client, r.get_json()['url'], _install(monkeypatch, _Provider(sub)))
    assert done.status_code == 302 and f'/invite/{token}' in done.headers['Location']
    assert _invite_row(token)['used_at'] is None
    assert 'already has' in client.get(f'/api/invite/{token}').get_json()['error']


def test_the_required_group_still_applies(client, monkeypatch):
    _on(monkeypatch, **{'security.oidc_required_group': 'music'})
    token = _invite(client, monkeypatch)
    r = client.post(f'/api/invite/{token}/accept', json={'name': 'Grp-' + _uid()})
    _finish(client, r.get_json()['url'], _install(monkeypatch, _Provider('g-' + _uid(), groups=['other'])))
    assert _invite_row(token)['used_at'] is None


def test_cancelling_at_the_provider_leaves_it_usable(client, monkeypatch):
    _on(monkeypatch)
    token = _invite(client, monkeypatch)
    r = client.post(f'/api/invite/{token}/accept', json={'name': 'Cxl-' + _uid()})
    state = parse_qs(urlparse(r.get_json()['url']).query)['state'][0]
    done = client.get(f'/api/auth/oidc/callback?error=access_denied&state={state}')
    assert f'/invite/{token}' in done.headers['Location']
    assert _invite_row(token)['used_at'] is None


def test_invites_are_unchanged_while_it_is_off(client, monkeypatch):
    _config(monkeypatch)
    token = _invite(client, monkeypatch)
    body = client.get(f'/api/invite/{token}').get_json()
    assert body['oidc'] is False and body['password_required'] is True
    r = client.post(f'/api/invite/{token}/accept', json={'name': 'Plain-' + _uid(), 'password': 'abcdef1'})
    assert r.status_code == 201


def test_the_invite_details_never_reach_the_cookie(client, monkeypatch):
    _on(monkeypatch)
    token = _invite(client, monkeypatch)
    client.post(f'/api/invite/{token}/accept', json={'name': 'Ck-' + _uid(), 'pin': '4321'})
    with client.session_transaction() as s:
        flat = json.dumps(dict(s), default=str)
    assert token not in flat and 'pbkdf2' not in flat


# -- the environment override (B7) --

def test_the_override_opens_the_other_sign_ins(client, monkeypatch):
    _on(monkeypatch)
    monkeypatch.setenv(oidc_only.OVERRIDE_ENV, 'off')
    oidc_only.reload_override_for_tests()
    assert oidc_only.is_on(web_server.config_manager) is False
    assert oidc_only.stored_on(web_server.config_manager) is True
    assert client.post('/api/auth/login', json={'username': 'ghost', 'password': 'x'}).status_code == 401


def test_the_override_is_read_at_start_only(monkeypatch):
    monkeypatch.setenv(oidc_only.OVERRIDE_ENV, 'off')
    assert oidc_only.overridden() is False                 # not re-read until a restart
    assert oidc_only.reload_override_for_tests() is True


def test_any_other_value_is_not_an_override(monkeypatch):
    monkeypatch.setenv(oidc_only.OVERRIDE_ENV, 'on')
    assert oidc_only.reload_override_for_tests() is False


def test_the_override_is_logged_at_start(monkeypatch, caplog):
    import logging
    _on(monkeypatch)
    monkeypatch.setenv(oidc_only.OVERRIDE_ENV, 'off')
    oidc_only.reload_override_for_tests()
    with caplog.at_level(logging.WARNING):
        oidc_only.log_start_state(web_server.config_manager)
    assert any(oidc_only.OVERRIDE_ENV in r.getMessage() for r in caplog.records)


def test_the_status_says_overridden(client, monkeypatch):
    _on(monkeypatch)
    _admin_oidc_session(client, monkeypatch)
    monkeypatch.setenv(oidc_only.OVERRIDE_ENV, 'off')
    oidc_only.reload_override_for_tests()
    body = client.get('/api/security/oidc-only').get_json()
    assert body['overridden'] is True and body['stored'] is True and body['on'] is False


def test_members_cannot_read_the_status(client, monkeypatch):
    _config(monkeypatch)
    db = web_server.get_database()
    pid = db.create_profile(name='Mem-' + _uid())
    _password_session(client, pid)
    assert client.get('/api/security/oidc-only').status_code in (401, 403)
