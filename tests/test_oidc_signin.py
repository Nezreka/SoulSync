"""Sign in with OIDC: discovery, the token checks, who maps to which profile,
and the routes.

No provider is ever called: discovery, the token endpoint and userinfo are
monkeypatched requests, and the routes run against stubbed seams. Mapping
rules (module doc of core/security/oidc_signin.py): a required group the
account lacks = no entry; a linked account = its profile; anyone else = a new
profile when allowed, request-only unless the admin says otherwise; claims
never make an admin, and email is never identity.
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

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-oidcsignin-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'o.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

from core.security import oidc_signin  # noqa: E402
from core.security.oidc_signin import OidcAccount, OidcError, OidcSettings  # noqa: E402

ISSUER = 'https://id.example.test'
DOC = {
    'issuer': ISSUER,
    'authorization_endpoint': f'{ISSUER}/authorize',
    'token_endpoint': f'{ISSUER}/api/oidc/token',
    'userinfo_endpoint': f'{ISSUER}/api/oidc/userinfo',
}


def _uid():
    return uuid4().hex[:8]


def _settings(**over):
    values = dict(issuer=ISSUER, client_id='soulsync', client_secret='s3cret')
    values.update(over)
    return OidcSettings(**values)


def _jwt(claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b'=').decode()  # noqa: E731
    return f"{enc({'alg': 'RS256', 'typ': 'JWT'})}.{enc(claims)}.c2ln"


def _claims(**over):
    c = {'iss': ISSUER, 'aud': 'soulsync', 'sub': 'user-1', 'exp': time.time() + 300,
         'iat': time.time(), 'nonce': 'n-1', 'preferred_username': 'alice'}
    c.update(over)
    return c


class _Resp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f'HTTP {self.status_code}')

    def json(self):
        return self._data


@pytest.fixture(autouse=True)
def _fresh_discovery():
    oidc_signin.forget_discovery()
    yield
    oidc_signin.forget_discovery()


# -- discovery --

def test_discovery_reads_the_document_and_caches_it(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return _Resp(DOC)

    monkeypatch.setattr(oidc_signin.requests, 'get', fake_get)
    assert oidc_signin.discover(ISSUER + '/')['token_endpoint'] == DOC['token_endpoint']
    oidc_signin.discover(ISSUER)
    assert calls == [f'{ISSUER}/.well-known/openid-configuration']


def test_a_document_for_another_issuer_is_refused(monkeypatch):
    monkeypatch.setattr(oidc_signin.requests, 'get', lambda url, **kw: _Resp({**DOC, 'issuer': 'https://evil.test'}))
    with pytest.raises(OidcError, match="don't match"):
        oidc_signin.discover(ISSUER)


def test_plain_http_is_refused_except_on_loopback(monkeypatch):
    with pytest.raises(OidcError, match='https'):
        oidc_signin.discover('http://id.example.test')
    local = {**DOC, 'issuer': 'http://127.0.0.1:9000', 'token_endpoint': 'http://127.0.0.1:9000/token'}
    monkeypatch.setattr(oidc_signin.requests, 'get', lambda url, **kw: _Resp(local))
    assert oidc_signin.discover('http://127.0.0.1:9000')['issuer'] == 'http://127.0.0.1:9000'


def test_a_token_endpoint_over_http_is_refused(monkeypatch):
    monkeypatch.setattr(oidc_signin.requests, 'get',
                        lambda url, **kw: _Resp({**DOC, 'token_endpoint': 'http://id.example.test/token'}))
    with pytest.raises(OidcError, match='token endpoint'):
        oidc_signin.discover(ISSUER)


# -- starting and coming back --

def test_start_sends_pkce_state_and_nonce():
    url, flow = oidc_signin.start(_settings(), DOC, 'https://media.test/api/auth/oidc/callback')
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert url.startswith(DOC['authorization_endpoint'] + '?')
    assert q['response_type'] == 'code' and q['client_id'] == 'soulsync'
    assert q['state'] == flow.state and q['nonce'] == flow.nonce
    assert q['code_challenge_method'] == 'S256' and q['code_challenge'] and q['code_challenge'] != flow.verifier
    assert q['redirect_uri'] == 'https://media.test/api/auth/oidc/callback'
    assert 's3cret' not in url


def test_the_callback_must_carry_this_browsers_state_in_time():
    _, flow = oidc_signin.start(_settings(), DOC, 'https://m/cb', now=1000.0)
    oidc_signin.check_callback(flow, flow.state, now=1001.0)
    for state, when in (('other', 1001.0), ('', 1001.0), (flow.state, 1000.0 + oidc_signin.FLOW_MAX_AGE + 1)):
        with pytest.raises(OidcError, match='expired'):
            oidc_signin.check_callback(flow, state, now=when)
    with pytest.raises(OidcError):
        oidc_signin.check_callback(None, flow.state)


def test_the_flow_round_trips_through_the_session():
    _, flow = oidc_signin.start(_settings(), DOC, 'https://m/cb', mode='connect', profile_id=4)
    back = oidc_signin.Flow.from_session(json.loads(json.dumps(flow.to_session())))
    assert back == flow
    assert oidc_signin.Flow.from_session({'state': 'x'}) is None


# -- the code exchange --

def test_exchange_uses_basic_auth_by_default(monkeypatch):
    seen = {}

    def fake_post(url, data=None, auth=None, **kw):
        seen.update(url=url, data=data, auth=auth)
        return _Resp({'id_token': 'tok', 'access_token': 'at'})

    monkeypatch.setattr(oidc_signin.requests, 'post', fake_post)
    _, flow = oidc_signin.start(_settings(), DOC, 'https://m/cb')
    oidc_signin.exchange_code(_settings(), DOC, 'code-1', flow)
    assert seen['auth'] == ('soulsync', 's3cret') and 'client_secret' not in seen['data']
    assert seen['data']['code_verifier'] == flow.verifier and seen['data']['code'] == 'code-1'


def test_exchange_posts_the_secret_when_the_provider_only_takes_that(monkeypatch):
    seen = {}

    def fake_post(url, data=None, auth=None, **kw):
        seen.update(data=data, auth=auth)
        return _Resp({'id_token': 'tok'})

    monkeypatch.setattr(oidc_signin.requests, 'post', fake_post)
    doc = {**DOC, 'token_endpoint_auth_methods_supported': ['client_secret_post']}
    _, flow = oidc_signin.start(_settings(), doc, 'https://m/cb')
    oidc_signin.exchange_code(_settings(), doc, 'c', flow)
    assert seen['auth'] is None and seen['data']['client_secret'] == 's3cret'


def test_a_refused_exchange_says_so_without_the_providers_text(monkeypatch):
    monkeypatch.setattr(oidc_signin.requests, 'post',
                        lambda *a, **k: _Resp({'error': 'invalid_grant', 'error_description': '<b>x</b>'}, 400))
    _, flow = oidc_signin.start(_settings(), DOC, 'https://m/cb')
    with pytest.raises(OidcError, match='refused') as e:
        oidc_signin.exchange_code(_settings(), DOC, 'c', flow)
    assert '<b>' not in str(e.value)


# -- the id token --

def test_a_good_token_passes():
    claims = oidc_signin.id_token_claims(_jwt(_claims()), _settings(), DOC, 'n-1')
    assert claims['sub'] == 'user-1'


@pytest.mark.parametrize('bad, msg', [
    ({'iss': 'https://evil.test'}, 'wrong provider'),
    ({'aud': 'someone-else'}, "wasn't meant"),
    ({'aud': ['soulsync', 'other'], 'azp': 'other'}, "wasn't meant"),
    ({'exp': time.time() - 3600}, 'expired'),
    ({'nonce': 'replayed'}, 'expired'),
    ({'sub': ''}, 'who you are'),
])
def test_a_bad_token_is_refused(bad, msg):
    with pytest.raises(OidcError, match=msg):
        oidc_signin.id_token_claims(_jwt(_claims(**bad)), _settings(), DOC, 'n-1')


def test_garbage_is_not_a_token():
    with pytest.raises(OidcError, match='unreadable'):
        oidc_signin.id_token_claims('not.a', _settings(), DOC, 'n-1')


# -- the account --

def test_userinfo_only_counts_for_the_same_subject():
    s = _settings(required_group='music')
    mine = oidc_signin.account_from_claims(_claims(), DOC, s, {'sub': 'user-1', 'groups': ['music']})
    theirs = oidc_signin.account_from_claims(_claims(), DOC, s, {'sub': 'user-2', 'groups': ['music']})
    assert mine.groups == ['music'] and theirs.groups == []


def test_identity_is_issuer_and_subject_never_email():
    a = oidc_signin.account_from_claims(_claims(email='boss@example.test'), DOC, _settings())
    assert a.identity == f'{ISSUER}|user-1' and 'boss@' not in a.identity


def test_userinfo_is_asked_only_when_a_required_group_needs_it():
    assert not oidc_signin.needs_userinfo(_claims(), _settings())
    assert oidc_signin.needs_userinfo(_claims(), _settings(required_group='music'))
    assert not oidc_signin.needs_userinfo(_claims(groups=['music']), _settings(required_group='music'))


# -- who maps to which profile --

def _acct(sub=None, name=None, groups=None):
    return OidcAccount(ISSUER, sub or _uid(), name or f'Oidc{_uid()}', groups or [])


def test_a_new_account_gets_a_request_only_profile_linked_to_it():
    db = web_server.get_database()
    acct = _acct()
    r = oidc_signin.sign_in(db, acct, _settings(), allow_create=True, default_can_download=False)
    assert r.created and r.profile_id
    p = db.get_profile(r.profile_id)
    assert p['name'] == acct.username and not p['is_admin'] and not p['can_download']
    assert db.get_profile_by_oidc_identity(acct.identity)['id'] == r.profile_id


def test_signing_in_again_lands_on_the_same_profile():
    db = web_server.get_database()
    acct = _acct()
    first = oidc_signin.sign_in(db, acct, _settings(), allow_create=True, default_can_download=False)
    renamed = OidcAccount(ISSUER, acct.sub, 'new-name', [])
    again = oidc_signin.sign_in(db, renamed, _settings(), allow_create=False, default_can_download=False)
    assert again.profile_id == first.profile_id and not again.created


def test_the_same_subject_at_another_provider_is_someone_else():
    db = web_server.get_database()
    acct = _acct()
    first = oidc_signin.sign_in(db, acct, _settings(), allow_create=True, default_can_download=False)
    other = OidcAccount('https://other.test', acct.sub, acct.username, [])
    r = oidc_signin.sign_in(db, other, _settings(), allow_create=True, default_can_download=False)
    assert r.profile_id != first.profile_id


def test_a_required_group_keeps_others_out():
    db = web_server.get_database()
    s = _settings(required_group='music')
    assert 'allowed' in oidc_signin.sign_in(db, _acct(groups=['video']), s,
                                            allow_create=True, default_can_download=False).error
    assert oidc_signin.sign_in(db, _acct(groups=['music']), s,
                               allow_create=True, default_can_download=False).profile_id


def test_claims_never_make_an_admin():
    db = web_server.get_database()
    acct = _acct(groups=['admins', 'administrators'])
    r = oidc_signin.sign_in(db, acct, _settings(), allow_create=True, default_can_download=True)
    assert r.profile_id != 1 and not db.get_profile(r.profile_id)['is_admin']


def test_an_admin_connects_their_own_account_and_signs_in_as_admin():
    db = web_server.get_database()
    acct = _acct()
    assert oidc_signin.connect_profile(db, 1, acct).profile_id == 1
    r = oidc_signin.sign_in(db, acct, _settings(), allow_create=False, default_can_download=False)
    assert r.profile_id == 1
    db.set_profile_oidc_identity(1, None)


def test_one_account_one_profile():
    db = web_server.get_database()
    acct = _acct()
    first = oidc_signin.sign_in(db, acct, _settings(), allow_create=True, default_can_download=False)
    other = db.create_profile(name=f'Other{_uid()}')
    r = oidc_signin.connect_profile(db, other, acct)
    assert r.profile_id is None and 'another' in r.error
    assert db.get_profile_by_oidc_identity(acct.identity)['id'] == first.profile_id


def test_a_turned_off_profile_cannot_sign_in():
    db = web_server.get_database()
    acct = _acct()
    pid = db.create_profile(name=f'Off{_uid()}')
    db.set_profile_oidc_identity(pid, acct.identity)
    db.update_profile(pid, disabled=1)
    r = oidc_signin.sign_in(db, acct, _settings(), allow_create=True, default_can_download=False)
    assert r.profile_id is None and 'turned off' in r.error


def test_no_new_profiles_when_the_admin_says_so():
    db = web_server.get_database()
    r = oidc_signin.sign_in(db, _acct(), _settings(), allow_create=False, default_can_download=False)
    assert r.profile_id is None and 'linked' in r.error


def test_a_taken_name_gets_a_number():
    db = web_server.get_database()
    name = f'Taken{_uid()}'
    db.create_profile(name=name)
    r = oidc_signin.sign_in(db, _acct(name=name), _settings(), allow_create=True, default_can_download=True)
    assert db.get_profile(r.profile_id)['name'] == f'{name} 2'


# -- the routes (login mode on, nothing in front of soulsync) --

@pytest.fixture
def client():
    return web_server.app.test_client()


_GATED = '/api/profiles/me/connections'


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


def _stub_provider(monkeypatch, *, sub=None, name=None, groups=None, seen=None):
    """discovery and the token endpoint, answering with an id token for
    whatever nonce the browser's session holds"""
    sub = sub or _uid()
    monkeypatch.setattr(oidc_signin, 'discover', lambda issuer, now=None: DOC)

    def exchange(settings, doc, code, flow):
        if seen is not None:
            seen.append(code)
        claims = _claims(sub=sub, nonce=flow.nonce, preferred_username=name or f'Routed{_uid()}')
        if groups is not None:
            claims['groups'] = groups
        return {'id_token': _jwt(claims), 'access_token': 'at'}

    monkeypatch.setattr(oidc_signin, 'exchange_code', exchange)
    return OidcAccount(ISSUER, sub, name or '', groups or [])


def _go(client, start='/api/auth/oidc/start'):
    """start, then come back from the provider with this browser's state"""
    r = client.get(start)
    assert r.status_code == 302 and r.headers['Location'].startswith(DOC['authorization_endpoint'])
    state = parse_qs(urlparse(r.headers['Location']).query)['state'][0]
    return client.get(f'/api/auth/oidc/callback?code=code-1&state={state}')


def _last_error(client):
    return client.get('/api/auth/oidc/available').get_json()['error']


def test_available_follows_the_settings(client, monkeypatch):
    _config(monkeypatch, **{'security.oidc_signin': False})
    assert client.get('/api/auth/oidc/available').get_json()['enabled'] is False
    _config(monkeypatch, **{'security.oidc_client_secret': ''})
    assert client.get('/api/auth/oidc/available').get_json()['enabled'] is False
    _config(monkeypatch, **{'security.oidc_button_label': 'Sign in with Pocket ID'})
    body = client.get('/api/auth/oidc/available').get_json()
    assert body['enabled'] is True and body['label'] == 'Sign in with Pocket ID'


def test_turned_off_means_no_flow(client, monkeypatch):
    _config(monkeypatch, **{'security.oidc_signin': False})
    assert client.get('/api/auth/oidc/start').status_code == 403


def test_full_flow_with_nothing_in_front(client, monkeypatch):
    _config(monkeypatch)
    _stub_provider(monkeypatch)
    assert client.get(_GATED).status_code == 401
    done = _go(client)
    assert done.status_code == 302 and done.headers['Location'].endswith('/')
    assert client.get(_GATED).status_code == 200  # signed in for real
    assert _last_error(client) is None


def test_the_secret_never_reaches_the_browser(client, monkeypatch):
    _config(monkeypatch)
    _stub_provider(monkeypatch)
    r = client.get('/api/auth/oidc/start')
    assert 's3cret' not in r.headers['Location']
    assert 's3cret' not in client.get('/api/auth/oidc/available').get_data(as_text=True)


def test_a_callback_from_another_browser_signs_nobody_in(client, monkeypatch):
    _config(monkeypatch)
    _stub_provider(monkeypatch)
    r = client.get('/api/auth/oidc/start')
    state = parse_qs(urlparse(r.headers['Location']).query)['state'][0]
    stranger = web_server.app.test_client()
    stranger.get(f'/api/auth/oidc/callback?code=code-1&state={state}')
    assert stranger.get(_GATED).status_code == 401
    assert 'expired' in _last_error(stranger)


def test_a_wrong_state_signs_nobody_in(client, monkeypatch):
    _config(monkeypatch)
    _stub_provider(monkeypatch)
    client.get('/api/auth/oidc/start')
    client.get('/api/auth/oidc/callback?code=code-1&state=forged')
    assert client.get(_GATED).status_code == 401
    assert 'expired' in _last_error(client)


def test_a_callback_works_once(client, monkeypatch):
    _config(monkeypatch)
    seen = []
    _stub_provider(monkeypatch, seen=seen)
    r = client.get('/api/auth/oidc/start')
    state = parse_qs(urlparse(r.headers['Location']).query)['state'][0]
    client.get(f'/api/auth/oidc/callback?code=code-1&state={state}')
    client.post('/api/auth/logout')
    client.get(f'/api/auth/oidc/callback?code=code-1&state={state}')
    assert client.get(_GATED).status_code == 401 and seen == ['code-1']


def test_cancelling_at_the_provider_says_so(client, monkeypatch):
    _config(monkeypatch)
    _stub_provider(monkeypatch)
    r = client.get('/api/auth/oidc/start')
    state = parse_qs(urlparse(r.headers['Location']).query)['state'][0]
    client.get(f'/api/auth/oidc/callback?error=access_denied&error_description=%3Cb%3Ex&state={state}')
    assert client.get(_GATED).status_code == 401
    assert _last_error(client) == 'Sign-in was cancelled.'


def test_a_required_group_is_enforced_on_the_route(client, monkeypatch):
    _config(monkeypatch, **{'security.oidc_required_group': 'music'})
    _stub_provider(monkeypatch, groups=['video'])
    _go(client)
    assert client.get(_GATED).status_code == 401
    assert 'allowed' in _last_error(client)


def test_starts_are_rate_limited(client, monkeypatch):
    _config(monkeypatch)
    _stub_provider(monkeypatch)
    codes = [client.get('/api/auth/oidc/start').status_code for _ in range(10)]
    assert codes[:8] == [302] * 8
    last = client.get('/api/auth/oidc/start')
    assert last.headers['Location'].endswith('/') and 'Too many' in _last_error(client)


def _sign_in_as(client, pid):
    with client.session_transaction() as s:
        s['login_authenticated'] = True
        s['profile_id'] = pid
        s['profile_epoch'] = (web_server.get_database().get_profile(pid) or {}).get('session_epoch', 0)


def test_connecting_from_my_account_then_signing_in(client, monkeypatch):
    _config(monkeypatch)
    db = web_server.get_database()
    pid = db.create_profile(name=f'Conn{_uid()}')
    db.set_profile_password(pid, 'secretpw')
    _sign_in_as(client, pid)
    acct = _stub_provider(monkeypatch)
    assert client.get('/api/profiles/me/oidc').get_json()['connected'] is False
    _go(client, '/api/profiles/me/oidc-connect/start')
    status = client.get('/api/profiles/me/oidc').get_json()
    assert status['connected'] is True and status['last'] == {'connected': True, 'error': None}
    assert db.get_profile_by_oidc_identity(acct.identity)['id'] == pid

    other = web_server.app.test_client()
    _go(other)
    assert other.get(_GATED).status_code == 200
    assert other.get('/api/profiles/current').get_json()['profile']['id'] == pid


def test_connect_needs_a_signed_in_profile(client, monkeypatch):
    _config(monkeypatch)
    _stub_provider(monkeypatch)
    assert client.get('/api/profiles/me/oidc-connect/start').status_code == 401


def test_disconnect_never_strands_a_profile(client, monkeypatch):
    _config(monkeypatch)
    db = web_server.get_database()
    acct = _stub_provider(monkeypatch)
    _go(client)  # an oidc-made profile: no password, no plex
    pid = db.get_profile_by_oidc_identity(acct.identity)['id']
    r = client.delete('/api/profiles/me/oidc')
    assert r.status_code == 400 and 'only way' in r.get_json()['error']
    db.set_profile_password(pid, 'secretpw')
    assert client.delete('/api/profiles/me/oidc').get_json()['success'] is True
    assert db.get_profile_by_oidc_identity(acct.identity) is None


def test_password_login_still_works(client, monkeypatch):
    _config(monkeypatch)
    db = web_server.get_database()
    name = f'Pw{_uid()}'
    pid = db.create_profile(name=name)
    db.set_profile_password(pid, 'secretpw')
    web_server._login_limiter.reset()
    r = client.post('/api/auth/login', json={'username': name, 'password': 'secretpw'})
    assert r.status_code == 200 and client.get(_GATED).status_code == 200


def test_a_failed_connect_reports_to_my_account_not_the_login_screen(client, monkeypatch):
    _config(monkeypatch)
    db = web_server.get_database()
    acct = _stub_provider(monkeypatch)
    _go(web_server.app.test_client())  # someone else's profile already has this account
    pid = db.create_profile(name=f'Late{_uid()}')
    db.set_profile_password(pid, 'secretpw')
    _sign_in_as(client, pid)
    done = _go(client, '/api/profiles/me/oidc-connect/start')
    assert done.headers['Location'].endswith('/?oidc=connect')
    status = client.get('/api/profiles/me/oidc').get_json()
    assert status['connected'] is False and 'another' in status['last']['error']
    assert _last_error(client) is None
    assert db.get_profile_by_oidc_identity(acct.identity)['id'] != pid


# -- the provider's logo in my account --

@pytest.mark.parametrize('url, ok', [
    ('https://cdn.example.test/pocket-id.png', True),
    ('http://cdn.example.test/pocket-id.png', False),
    ('javascript:alert(1)', False),
    ('data:image/png;base64,AAAA', False),
    ('https://cdn.example.test/a.png" onerror="x', False),
    ('', False),
])
def test_only_a_plain_https_icon_gets_through(url, ok):
    assert oidc_signin.safe_icon_url(url) == (url if ok else '')


def test_my_account_hands_over_the_icon(client, monkeypatch):
    _config(monkeypatch, **{'security.oidc_icon_url': 'https://cdn.example.test/pocket-id.png'})
    db = web_server.get_database()
    pid = db.create_profile(name=f'Icon{_uid()}')
    _sign_in_as(client, pid)
    assert client.get('/api/profiles/me/oidc').get_json()['icon'] == 'https://cdn.example.test/pocket-id.png'
    _config(monkeypatch, **{'security.oidc_icon_url': 'javascript:alert(1)'})
    assert client.get('/api/profiles/me/oidc').get_json()['icon'] == ''


# -- what a new oidc profile starts with (signup_defaults) --

def _pages_of(profile):
    pages = profile['allowed_pages']
    return json.loads(pages) if isinstance(pages, str) else pages


def test_a_new_oidc_profile_without_settings_starts_like_a_plex_one():
    from core.security.signup_defaults import SIGNUP_PAGES
    db = web_server.get_database()
    r = oidc_signin.sign_in(db, _acct(), _settings(), allow_create=True, default_can_download=False)
    p = db.get_profile(r.profile_id)
    assert set(_pages_of(p)) == set(SIGNUP_PAGES) and p['home_page'] == 'discover'
    assert p['allowed_sides'] == 'music' and p['request_limit'] == 0


def test_a_new_oidc_profile_gets_the_sign_up_access():
    from core.security.signup_defaults import SIGNUP_PAGES, SIGNUP_VIDEO_PAGES, signup_access
    db = web_server.get_database()
    values = {'security.signup_sides': 'both', 'security.signup_request_limit': 5}
    access = signup_access(lambda k, d=None: values.get(k, d))
    r = oidc_signin.sign_in(db, _acct(), _settings(), allow_create=True, default_can_download=False,
                            access=access)
    p = db.get_profile(r.profile_id)
    assert p['allowed_sides'] == 'both' and p['request_limit'] == 5 and p['request_limit_days'] == 7
    assert set(_pages_of(p)) == set(SIGNUP_PAGES + SIGNUP_VIDEO_PAGES)
    assert not p['is_admin'] and not p['can_download']


def test_the_oidc_route_passes_the_sign_up_settings(client, monkeypatch):
    _config(monkeypatch, **{'security.signup_sides': 'video', 'security.signup_request_limit': 3,
                            'security.signup_request_limit_days': 30})
    acct = _stub_provider(monkeypatch)
    done = _go(client)
    assert done.status_code == 302 and _last_error(client) is None
    db = web_server.get_database()
    p = db.get_profile(db.get_profile_by_oidc_identity(acct.identity)['id'])
    assert p['allowed_sides'] == 'video' and p['home_page'] == 'video-discover'
    assert p['request_limit'] == 3 and p['request_limit_days'] == 30
