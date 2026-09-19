"""/api/profiles/me/plex-home-user(s): a profile links itself to a plex home
user (#1265). real app, real http; the plex client is a fake on the engine."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-plexhome-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'plexhome.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


class _FakePlex:
    def __init__(self):
        self.minted = []

    def list_home_users(self):
        return [{'id': '1', 'title': 'Kids', 'protected': False, 'restricted': True},
                {'id': '2', 'title': 'Annie', 'protected': True, 'restricted': True}]

    def mint_home_user_server_token(self, user_id, pin=None):
        self.minted.append((user_id, pin))
        if user_id == '1':
            return 'srvtoken-Kids', 'Kids', None
        if user_id == '2' and pin == '1234':
            return 'srvtoken-Annie', 'Annie', None
        if user_id == '2':
            return None, 'Annie', 'Plex refused the PIN for that user'
        return None, None, 'That Plex Home user was not found'


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def as_kid(client):
    db = web_server.get_database()
    pid = db.create_profile(name=f'kid_{os.urandom(3).hex()}')
    with client.session_transaction() as sess:
        sess['profile_id'] = pid
    return pid


@pytest.fixture
def plex(monkeypatch):
    import api.user_profiles as up
    fake = _FakePlex()

    class _Engine:
        def client(self, name):
            return fake if name == 'plex' else None
    monkeypatch.setattr(up, '_media_server_engine', lambda: _Engine())
    return fake


def test_home_users_are_listed_without_secrets(client, as_kid, plex):
    body = client.get('/api/profiles/me/plex-home-users').get_json()
    assert body['success'] and [u['title'] for u in body['users']] == ['Kids', 'Annie']
    assert 'token' not in str(body)


def test_linking_stores_the_token_and_shows_the_title_only(client, as_kid, plex):
    r = client.post('/api/profiles/me/plex-home-user', json={'user_id': '1'})
    assert r.status_code == 200 and r.get_json() == {'success': True, 'title': 'Kids'}, r.data
    assert web_server.get_database().get_profile_plex_home_user(as_kid)['token'] == 'srvtoken-Kids'
    libs = client.get('/api/profiles/me/server-library').get_json()
    assert (libs['plex_home_user_id'], libs['plex_home_user_title']) == ('1', 'Kids')
    assert 'srvtoken' not in str(libs)


def test_a_wrong_pin_is_refused_and_nothing_is_saved(client, as_kid, plex):
    r = client.post('/api/profiles/me/plex-home-user', json={'user_id': '2', 'pin': '0000'})
    assert r.status_code == 400 and 'PIN' in r.get_json()['error']
    assert web_server.get_database().get_profile_plex_home_user(as_kid) is None
    assert client.post('/api/profiles/me/plex-home-user', json={'user_id': '2', 'pin': '1234'}).status_code == 200
    assert plex.minted[-1] == ('2', '1234')


def test_missing_user_is_a_400_and_no_plex_is_a_503(client, as_kid, plex, monkeypatch):
    assert client.post('/api/profiles/me/plex-home-user', json={}).status_code == 400
    import api.user_profiles as up
    monkeypatch.setattr(up, '_media_server_engine', lambda: None)
    assert client.post('/api/profiles/me/plex-home-user', json={'user_id': '1'}).status_code == 503
    assert client.get('/api/profiles/me/plex-home-users').status_code == 503


def test_unlink_clears_the_link(client, as_kid, plex):
    client.post('/api/profiles/me/plex-home-user', json={'user_id': '1'})
    assert client.delete('/api/profiles/me/plex-home-user').get_json()['success']
    assert web_server.get_database().get_profile_plex_home_user(as_kid) is None


def test_the_link_is_per_profile(client, as_kid, plex):
    client.post('/api/profiles/me/plex-home-user', json={'user_id': '1'})
    assert web_server.get_database().get_profile_plex_home_user(1) is None
