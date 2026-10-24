"""A sign-in with OIDC lasts as long as the provider says (#1838).

The callback records a clock on the device row (which account, when to
renew, the refresh token encrypted). Every request, and every check of the
browser's open socket, renews it past that time server to server; a renewal
that fails, a refusal, a removed group or no refresh token ends the session
by revoking the device. Password and Plex sessions have no clock and are
never touched. Sessions from before the change are ended once at upgrade
for every profile with an OIDC link.

No provider is ever called: discovery is stubbed and the token / userinfo
endpoints are monkeypatched requests.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-oidcsession-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 's.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

import requests  # noqa: E402

from core.security import devices, oidc_session, oidc_signin, session_epoch  # noqa: E402
from core.security.oidc_signin import OidcError, OidcSettings  # noqa: E402

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


def _settings(**over):
    values = dict(issuer=ISSUER, client_id='soulsync', client_secret='s3cret')
    values.update(over)
    return OidcSettings(**values)


def _jwt(claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b'=').decode()  # noqa: E731
    return f"{enc({'alg': 'RS256', 'typ': 'JWT'})}.{enc(claims)}.c2ln"


def _claims(sub, **over):
    c = {'iss': ISSUER, 'aud': 'soulsync', 'sub': sub, 'exp': time.time() + 300, 'iat': time.time()}
    c.update(over)
    return c


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
    """the token endpoint (code and refresh grants) and userinfo"""

    def __init__(self, sub, *, groups=None, rotate=True, give_refresh=True, expires_in=3600,
                 refresh_answer=None, id_token_on_refresh=True, delay=0.0):
        self.sub = sub
        self.groups = groups
        self.rotate = rotate
        self.give_refresh = give_refresh
        self.expires_in = expires_in
        self.refresh_answer = refresh_answer     # None = grant; else (status, body) or an exception
        self.id_token_on_refresh = id_token_on_refresh
        self.delay = delay
        self.refresh_calls = []
        self.userinfo_calls = 0
        self.nonce = None
        self._n = 0
        self._lock = threading.Lock()

    def _id_token(self, **over):
        claims = _claims(self.sub, **over)
        if self.groups is not None:
            claims['groups'] = list(self.groups)
        return _jwt(claims)

    def post(self, url, data=None, auth=None, timeout=None, headers=None):
        assert url == DOC['token_endpoint']
        if data['grant_type'] == 'authorization_code':
            body = {'access_token': 'at-0', 'id_token': self._id_token(nonce=self.nonce),
                    'expires_in': self.expires_in, 'token_type': 'Bearer'}
            if self.give_refresh:
                body['refresh_token'] = 'rt-0'
            return _Resp(body)
        assert data['grant_type'] == 'refresh_token'
        with self._lock:
            self.refresh_calls.append(data['refresh_token'])
            self._n += 1
            n = self._n
        if self.delay:
            time.sleep(self.delay)
        if isinstance(self.refresh_answer, Exception):
            raise self.refresh_answer
        if self.refresh_answer is not None:
            return _Resp(self.refresh_answer[1], self.refresh_answer[0])
        body = {'access_token': f'at-{n}', 'expires_in': self.expires_in, 'token_type': 'Bearer'}
        if self.id_token_on_refresh:
            body['id_token'] = self._id_token()
        if self.rotate:
            body['refresh_token'] = f'rt-{n}'
        return _Resp(body)

    def get(self, url, timeout=None, headers=None):
        assert url == DOC['userinfo_endpoint']
        self.userinfo_calls += 1
        info = {'sub': self.sub}
        if self.groups is not None:
            info['groups'] = list(self.groups)
        return _Resp(info)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    oidc_signin.forget_discovery()
    oidc_session._reset_for_tests()
    devices._reset_for_tests()
    session_epoch._reset_for_tests()
    monkeypatch.setattr(oidc_signin, 'discover', lambda issuer, now=None: DOC)
    yield
    oidc_session._reset_for_tests()


def _install(monkeypatch, provider):
    monkeypatch.setattr(oidc_signin.requests, 'post', provider.post)
    monkeypatch.setattr(oidc_signin.requests, 'get', provider.get)
    return provider


# -- the token lifetime (A2) --

def test_the_access_token_lifetime_counts_first():
    assert oidc_signin.token_expiry({'expires_in': 600}, {'exp': 99}, now=1000) == 1600


def test_the_id_token_expiry_is_the_fallback():
    assert oidc_signin.token_expiry({}, {'exp': 5000}, now=1000) == 5000
    assert oidc_signin.token_expiry({'expires_in': 'soon'}, {'exp': 5000}, now=1000) == 5000


def test_a_tiny_lifetime_is_stretched_to_the_minimum():
    assert oidc_signin.token_expiry({'expires_in': 1}, None, now=1000) == 1000 + oidc_signin.MIN_SESSION
    assert oidc_signin.token_expiry({}, {'exp': 900}, now=1000) == 1000 + oidc_signin.MIN_SESSION


def test_no_lifetime_at_all_is_none():
    assert oidc_signin.token_expiry({}, {}, now=1000) is None
    assert oidc_signin.token_expiry({'expires_in': 0}, None, now=1000) is None


# -- renewing, server to server --

def _identity(sub):
    return f'{ISSUER}|{sub}'


def test_a_renewal_moves_the_clock_and_rotates_the_token(monkeypatch):
    p = _install(monkeypatch, _Provider('u1', expires_in=900))
    got = oidc_signin.refresh(_settings(), DOC, 'rt-0', _identity('u1'), now=1000)
    assert got.expires_at == 1900 and got.refresh_token == 'rt-1'
    assert p.refresh_calls == ['rt-0']


def test_an_unrotated_token_is_kept(monkeypatch):
    _install(monkeypatch, _Provider('u1', rotate=False))
    assert oidc_signin.refresh(_settings(), DOC, 'rt-0', _identity('u1')).refresh_token is None


def test_a_refreshed_id_token_needs_no_nonce(monkeypatch):
    _install(monkeypatch, _Provider('u1'))   # the provider's renewed id token carries none
    oidc_signin.refresh(_settings(), DOC, 'rt-0', _identity('u1'))


def test_a_refusal_ends_it(monkeypatch):
    _install(monkeypatch, _Provider('u1', refresh_answer=(400, {'error': 'invalid_grant'})))
    with pytest.raises(OidcError):
        oidc_signin.refresh(_settings(), DOC, 'rt-0', _identity('u1'))


def test_an_unreachable_provider_ends_it(monkeypatch):
    # A4: fail closed. the issue asks for access to end when renewal fails
    _install(monkeypatch, _Provider('u1', refresh_answer=requests.ConnectionError('down')))
    with pytest.raises(OidcError):
        oidc_signin.refresh(_settings(), DOC, 'rt-0', _identity('u1'))


def test_a_renewal_for_another_account_is_refused(monkeypatch):
    _install(monkeypatch, _Provider('someone-else'))
    with pytest.raises(OidcError):
        oidc_signin.refresh(_settings(), DOC, 'rt-0', _identity('u1'))


def test_a_renewal_for_another_audience_is_refused(monkeypatch):
    p = _install(monkeypatch, _Provider('u1'))
    monkeypatch.setattr(p, '_id_token', lambda **o: _jwt(_claims('u1', aud='another-app')))
    with pytest.raises(OidcError):
        oidc_signin.refresh(_settings(), DOC, 'rt-0', _identity('u1'))


def test_an_identity_from_another_issuer_never_reaches_the_provider(monkeypatch):
    p = _install(monkeypatch, _Provider('u1'))
    with pytest.raises(OidcError):
        oidc_signin.refresh(_settings(), DOC, 'rt-0', 'https://other.test|u1')
    assert p.refresh_calls == []


def test_the_required_group_is_checked_again(monkeypatch):
    _install(monkeypatch, _Provider('u1', groups=['music']))
    oidc_signin.refresh(_settings(required_group='music'), DOC, 'rt-0', _identity('u1'))
    _install(monkeypatch, _Provider('u1', groups=['other']))
    with pytest.raises(OidcError):
        oidc_signin.refresh(_settings(required_group='music'), DOC, 'rt-0', _identity('u1'))


def test_groups_come_from_userinfo_when_the_token_leaves_them_out(monkeypatch):
    p = _install(monkeypatch, _Provider('u1', groups=['music'], id_token_on_refresh=False))
    oidc_signin.refresh(_settings(required_group='music'), DOC, 'rt-0', _identity('u1'))
    assert p.userinfo_calls == 1


def test_userinfo_about_someone_else_does_not_count(monkeypatch):
    p = _install(monkeypatch, _Provider('u1', groups=['music'], id_token_on_refresh=False))
    monkeypatch.setattr(oidc_signin.requests, 'get',
                        lambda url, timeout=None, headers=None: _Resp({'sub': 'x', 'groups': ['music']}))
    with pytest.raises(OidcError):
        oidc_signin.refresh(_settings(required_group='music'), DOC, 'rt-0', _identity('u1'))
    assert p.refresh_calls == ['rt-0']


# -- the routes: login mode on, nothing in front of soulsync --

@pytest.fixture
def client():
    return web_server.app.test_client()


def _config(monkeypatch, **over):
    values = {'security.require_login': True, 'security.oidc_signin': True,
              'security.oidc_issuer': ISSUER, 'security.oidc_client_id': 'soulsync',
              'security.oidc_client_secret': 's3cret',
              'security.oidc_signin_auto_create': True, 'security.oidc_signin_default_can_download': False}
    values.update(over)
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, 'get',
                        lambda k, d=None: values[k] if k in values else real_get(k, d))
    import api.login as login_api
    login_api.oidc_start_limiter.reset()


def _sign_in(client, provider):
    """a full oidc sign-in in this browser; returns (profile id, device id)"""
    r = client.get('/api/auth/oidc/start')
    assert r.status_code == 302
    query = parse_qs(urlparse(r.headers['Location']).query)
    provider.nonce = query['nonce'][0]
    done = client.get(f"/api/auth/oidc/callback?code=c&state={query['state'][0]}")
    assert done.status_code == 302
    with client.session_transaction() as s:
        return s.get('profile_id'), s.get('device_id')


def _expire(device_id):
    """move this browser's renewal time into the past"""
    db = web_server.get_database()
    assert db.set_profile_device_oidc(device_id, expires_at=time.time() - 1, keep_refresh_token=True)
    oidc_session.forget(device_id)


def _stored(device_id):
    with web_server.get_database()._get_connection() as conn:
        return dict(conn.execute('SELECT * FROM profile_devices WHERE id = ?', (device_id,)).fetchone())


def test_a_sign_in_records_the_clock(client, monkeypatch):
    _config(monkeypatch)
    sub = _uid()
    p = _install(monkeypatch, _Provider(sub, expires_in=900))
    before = time.time()
    _, device = _sign_in(client, p)
    row = _stored(device)
    assert row['oidc_identity'] == _identity(sub)
    assert before + 900 <= row['oidc_expires_at'] <= time.time() + 900
    # encrypted at rest, never the plain token
    assert row['oidc_refresh_token'] and row['oidc_refresh_token'] != 'rt-0'
    assert web_server.get_database().get_profile_device_oidc(device)['refresh_token'] == 'rt-0'


def test_the_tokens_never_reach_the_cookie(client, monkeypatch):
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid()))
    _sign_in(client, p)
    with client.session_transaction() as s:
        flat = json.dumps(dict(s), default=str)
    assert 'rt-0' not in flat and 'at-0' not in flat


def test_before_expiry_nothing_is_asked(client, monkeypatch):
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid()))
    _sign_in(client, p)
    for _ in range(3):
        assert client.get(_GATED).status_code == 200
    assert p.refresh_calls == []


def test_past_expiry_it_renews_and_carries_on(client, monkeypatch):
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid(), expires_in=900))
    _, device = _sign_in(client, p)
    _expire(device)
    assert client.get(_GATED).status_code == 200
    assert p.refresh_calls == ['rt-0']
    got = web_server.get_database().get_profile_device_oidc(device)
    assert got['refresh_token'] == 'rt-1' and got['expires_at'] > time.time() + 800
    assert client.get(_GATED).status_code == 200
    assert p.refresh_calls == ['rt-0']   # renewed once, not per request


def test_a_refused_renewal_signs_the_browser_out(client, monkeypatch):
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid()))
    pid, device = _sign_in(client, p)
    p.refresh_answer = (400, {'error': 'invalid_grant'})
    _expire(device)
    r = client.get(_GATED)
    assert r.status_code == 401 and r.get_json().get('signed_out') is True
    assert _stored(device)['revoked_at'] is not None   # the device is revoked, not just this cookie
    assert client.get(_GATED).status_code == 401


def test_an_unreachable_provider_signs_the_browser_out(client, monkeypatch):
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid()))
    _, device = _sign_in(client, p)
    p.refresh_answer = requests.Timeout('slow')
    _expire(device)
    assert client.get(_GATED).status_code == 401


def test_leaving_the_required_group_signs_the_browser_out(client, monkeypatch):
    _config(monkeypatch, **{'security.oidc_required_group': 'music'})
    p = _install(monkeypatch, _Provider(_uid(), groups=['music']))
    _, device = _sign_in(client, p)
    assert client.get(_GATED).status_code == 200
    p.groups = ['someone-removed-me']
    _expire(device)
    assert client.get(_GATED).status_code == 401


def test_no_refresh_token_means_signed_out_at_expiry(client, monkeypatch):
    # A1: nothing to renew with, so the user signs in again (usually one click)
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid(), give_refresh=False))
    _, device = _sign_in(client, p)
    assert _stored(device)['oidc_refresh_token'] is None
    assert client.get(_GATED).status_code == 200
    _expire(device)
    assert client.get(_GATED).status_code == 401
    assert p.refresh_calls == []


def test_a_token_that_no_longer_decrypts_ends_the_session(client, monkeypatch):
    # an .encryption_key that changed hands the ciphertext back; it must
    # never be sent to the provider as if it were the token
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid()))
    _, device = _sign_in(client, p)
    monkeypatch.setattr(web_server.config_manager, '_decrypt_value', lambda v: v)
    _expire(device)
    assert client.get(_GATED).status_code == 401
    assert p.refresh_calls == []


def test_two_requests_at_expiry_renew_once(client, monkeypatch):
    # the rotation race: the second must not spend the token the first rotated
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid(), delay=0.3))
    _, device = _sign_in(client, p)
    _expire(device)
    db = web_server.get_database()
    results = []
    go = threading.Barrier(4)

    def hit():
        go.wait()
        results.append(oidc_session.check_device(device, db, web_server.config_manager))

    threads = [threading.Thread(target=hit) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [True] * 4
    assert p.refresh_calls == ['rt-0']


def test_two_requests_at_a_refused_expiry_ask_once(client, monkeypatch):
    # the waiter must see the device the first request already ended, not
    # send the same dead refresh token to the provider a second time
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid(), delay=0.3))
    _, device = _sign_in(client, p)
    p.refresh_answer = (400, {'error': 'invalid_grant'})
    _expire(device)
    db = web_server.get_database()
    results = []
    go = threading.Barrier(4)

    def hit():
        go.wait()
        results.append(oidc_session.check_device(device, db, web_server.config_manager))

    threads = [threading.Thread(target=hit) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [False] * 4
    assert p.refresh_calls == ['rt-0']
    assert _stored(device)['revoked_at'] is not None


def test_turning_oidc_off_ends_its_sessions(client, monkeypatch):
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid()))
    _sign_in(client, p)
    _config(monkeypatch, **{'security.oidc_signin': False})
    assert client.get(_GATED).status_code == 401


def test_disconnecting_the_account_ends_its_sessions(client, monkeypatch):
    _config(monkeypatch)
    sub = _uid()
    p = _install(monkeypatch, _Provider(sub))
    pid, device = _sign_in(client, p)
    other = web_server.app.test_client()
    _sign_in(other, p)
    db = web_server.get_database()
    db.set_profile_password(pid, 'secretpw')
    assert client.delete('/api/profiles/me/oidc').get_json()['success'] is True
    oidc_session._reset_for_tests()
    assert other.get(_GATED).status_code == 401


def test_a_read_error_keeps_the_session(client, monkeypatch):
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid()))
    _, device = _sign_in(client, p)
    _expire(device)

    def broken(_):
        raise RuntimeError('database is locked')

    monkeypatch.setattr(type(web_server.get_database()), 'get_profile_device_oidc', lambda self, d: broken(d))
    assert client.get(_GATED).status_code == 200
    assert p.refresh_calls == []


def test_a_sign_in_whose_clock_cant_be_saved_is_not_kept(client, monkeypatch):
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid()))
    monkeypatch.setattr(type(web_server.get_database()), 'set_profile_device_oidc',
                        lambda self, *a, **k: False)
    pid, device = _sign_in(client, p)
    assert pid is None and device is None
    assert client.get(_GATED).status_code == 401
    assert client.get('/api/auth/oidc/available').get_json()['error'] == 'Sign-in failed. Try again.'


def test_a_sign_in_without_any_lifetime_is_not_kept(client, monkeypatch):
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid()))
    monkeypatch.setattr(oidc_signin, 'token_expiry', lambda *a, **k: None)
    pid, _ = _sign_in(client, p)
    assert pid is None
    assert client.get(_GATED).status_code == 401


def test_a_password_session_is_never_touched(client, monkeypatch):
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid()))
    db = web_server.get_database()
    name = f'Pw{_uid()}'
    pid = db.create_profile(name=name)
    db.set_profile_password(pid, 'secretpw')
    r = client.post('/api/auth/login', json={'username': name, 'password': 'secretpw'})
    assert r.get_json()['success'] is True
    with client.session_transaction() as s:
        device = s['device_id']
    assert _stored(device)['oidc_identity'] is None
    # even with oidc turned off, a password session has no clock to end
    _config(monkeypatch, **{'security.oidc_signin': False})
    assert client.get(_GATED).status_code == 200
    assert p.refresh_calls == []


# -- sessions from before the change (A6) --

def _run_upgrade(db):
    with db._get_connection() as conn:
        cur = conn.cursor()
        db._add_device_oidc_session(cur)
        conn.commit()


def test_upgrade_signs_out_oidc_linked_profiles_once(monkeypatch):
    db = web_server.get_database()
    linked = db.create_profile(name=f'Old{_uid()}')
    db.set_profile_oidc_identity(linked, _identity(_uid()))
    plain = db.create_profile(name=f'Plain{_uid()}')
    with db._get_connection() as conn:
        conn.execute("DELETE FROM metadata WHERE key = 'oidc_session_lifetime_v1'")
        conn.commit()
    epochs = lambda: (db.get_profile(linked)['session_epoch'], db.get_profile(plain)['session_epoch'])  # noqa: E731
    linked_before, plain_before = epochs()
    _run_upgrade(db)
    assert epochs() == (linked_before + 1, plain_before)
    _run_upgrade(db)   # once only: a restart signs nobody out again
    assert epochs() == (linked_before + 1, plain_before)
    with db._get_connection() as conn:
        assert conn.execute("SELECT 1 FROM schema_migrations WHERE name = 'oidc_session_lifetime_v1'").fetchone()


def test_an_old_session_is_signed_out_then_a_fresh_sign_in_works(client, monkeypatch):
    _config(monkeypatch)
    sub = _uid()
    p = _install(monkeypatch, _Provider(sub))
    pid, _ = _sign_in(client, p)
    db = web_server.get_database()
    # an old session: signed in before the change, so its device row has no clock
    old = web_server.app.test_client()
    with old.session_transaction() as s:
        s['login_authenticated'] = True
        s['profile_id'] = pid
        s['profile_epoch'] = db.get_profile(pid)['session_epoch']
    assert old.get(_GATED).status_code == 200
    with db._get_connection() as conn:
        conn.execute("DELETE FROM metadata WHERE key = 'oidc_session_lifetime_v1'")
        conn.commit()
    _run_upgrade(db)
    session_epoch._reset_for_tests()
    assert old.get(_GATED).status_code == 401
    fresh = web_server.app.test_client()
    assert _sign_in(fresh, p)[0] == pid
    assert fresh.get(_GATED).status_code == 200


# -- the open socket runs the same clock (#1841's checks) --

def _socket(flask_client):
    return web_server.socketio.test_client(web_server.app, flask_test_client=flask_client)


def test_a_failed_renewal_drops_an_open_socket_with_no_http_request(client, monkeypatch):
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid()))
    _, device = _sign_in(client, p)
    sio = _socket(client)
    assert sio.is_connected() is True
    p.refresh_answer = (400, {'error': 'invalid_grant'})
    _expire(device)
    web_server._disconnect_revoked_sockets()   # the 2s sweep; no HTTP request in between
    assert sio.is_connected() is False
    assert _stored(device)['revoked_at'] is not None


def test_a_good_renewal_keeps_the_socket(client, monkeypatch):
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid()))
    _, device = _sign_in(client, p)
    sio = _socket(client)
    _expire(device)
    web_server._disconnect_revoked_sockets()
    assert sio.is_connected() is True
    assert p.refresh_calls == ['rt-0']
    sio.disconnect()


def test_an_expired_session_that_cant_renew_cannot_open_a_socket(client, monkeypatch):
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid(), give_refresh=False))
    _, device = _sign_in(client, p)
    _expire(device)
    assert _socket(client).is_connected() is False


def test_a_password_socket_is_untouched_by_the_sweep(client, monkeypatch):
    _config(monkeypatch)
    p = _install(monkeypatch, _Provider(_uid()))
    db = web_server.get_database()
    name = f'PwWs{_uid()}'
    pid = db.create_profile(name=name)
    db.set_profile_password(pid, 'secretpw')
    assert client.post('/api/auth/login', json={'username': name, 'password': 'secretpw'}).get_json()['success']
    sio = _socket(client)
    _config(monkeypatch, **{'security.oidc_signin': False})
    web_server._disconnect_revoked_sockets()
    assert sio.is_connected() is True
    assert p.refresh_calls == []
    sio.disconnect()
