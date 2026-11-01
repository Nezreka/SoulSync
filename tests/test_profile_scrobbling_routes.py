"""Phonk_Ben_67 through the real app: a profile signs into last.fm for
scrobbling and turns scrobbling to its own listenbrainz on. the last.fm
callback is a browser redirect, so it only takes the state handed out to the
same profile (no one can link their last.fm to someone else's profile).
"""

from __future__ import annotations

import os
import tempfile
from urllib.parse import parse_qs, unquote, urlparse

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-scrobble-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'scrobble_routes.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def db():
    return web_server.get_database()


@pytest.fixture(autouse=True)
def _lastfm_app(monkeypatch):
    import api.user_profiles as up
    import core.lastfm_client as lfm
    values = {'lastfm.api_key': 'key', 'lastfm.api_secret': 'secret'}
    real_get = up.config_manager.get
    monkeypatch.setattr(up.config_manager, 'get', lambda k, d=None: values.get(k, real_get(k, d)))
    monkeypatch.setattr(lfm.LastFMClient, 'get_session',
                        lambda self, token: {'key': f'session-{token}', 'name': 'kidfm'} if token == 'good' else None)


def _as(client, pid):
    with client.session_transaction() as sess:
        sess['profile_id'] = pid


def _state(client):
    url = client.get('/api/profiles/me/lastfm/scrobble/auth-url').get_json()['url']
    cb = unquote(parse_qs(urlparse(url).query)['cb'][0])
    return parse_qs(urlparse(cb).query)['state'][0]


def test_signing_in_stores_the_session_for_that_profile(client, db):
    kid = db.create_profile(name=f'kid_{os.urandom(3).hex()}')
    _as(client, kid)
    state = _state(client)
    r = client.get(f'/api/profiles/me/lastfm/scrobble/callback?state={state}&token=good')
    assert r.status_code == 200
    assert db.get_profile_scrobbling(kid)['lastfm'] == {'connected': True, 'user': 'kidfm'}
    # the state is single use
    again = client.get(f'/api/profiles/me/lastfm/scrobble/callback?state={state}&token=good')
    assert again.status_code == 400


def test_a_state_from_another_profile_is_refused(client, db):
    kid = db.create_profile(name=f'kid_{os.urandom(3).hex()}')
    other = db.create_profile(name=f'other_{os.urandom(3).hex()}')
    _as(client, other)
    state = _state(client)
    _as(client, kid)
    r = client.get(f'/api/profiles/me/lastfm/scrobble/callback?state={state}&token=good')
    assert r.status_code == 400
    assert db.get_profile_scrobbling(kid)['lastfm']['connected'] is False
    assert db.get_profile_scrobbling(other)['lastfm']['connected'] is False


def test_the_admin_scrobbles_from_settings(client):
    _as(client, 1)
    assert client.get('/api/profiles/me/scrobbling').get_json()['managed_in_settings'] is True
    assert client.get('/api/profiles/me/lastfm/scrobble/auth-url').status_code == 400


def test_listenbrainz_scrobbling_toggles(client, db):
    kid = db.create_profile(name=f'kid_{os.urandom(3).hex()}')
    _as(client, kid)
    assert client.post('/api/profiles/me/listenbrainz/scrobble', json={'enabled': True}).status_code == 400
    db.set_profile_listenbrainz(kid, 'tok', '', 'kid')
    on = client.post('/api/profiles/me/listenbrainz/scrobble', json={'enabled': True}).get_json()
    assert on['listenbrainz'] == {'enabled': True, 'available': True}
    off = client.post('/api/profiles/me/listenbrainz/scrobble', json={'enabled': False}).get_json()
    assert off['listenbrainz']['enabled'] is False
