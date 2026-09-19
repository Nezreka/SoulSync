"""/api/profiles/me/navidrome-login: a profile saves its own navidrome
login (#1265). the login is pinged as that user before it is stored, so a
typo is refused here and not found out by a failing sync. real app, real
http; the navidrome client is a fake on the engine."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-ndlogin-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'ndlogin.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


class _FakeNavidrome:
    def __init__(self):
        self.good = {('bob', 'bobpw')}

    def verify_user_login(self, username, password):
        return ((username, password) in self.good, None if (username, password) in self.good else 'Wrong username or password')


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def as_bob(client):
    db = web_server.get_database()
    pid = db.create_profile(name=f'bob_{os.urandom(3).hex()}')
    with client.session_transaction() as sess:
        sess['profile_id'] = pid
    return pid


@pytest.fixture
def navidrome(monkeypatch):
    import api.user_profiles as up
    fake = _FakeNavidrome()

    class _Engine:
        def client(self, name):
            return fake if name == 'navidrome' else None
    monkeypatch.setattr(up, '_media_server_engine', lambda: _Engine())
    return fake


def test_a_good_login_is_saved_and_shown_by_username_only(client, as_bob, navidrome):
    r = client.post('/api/profiles/me/navidrome-login', json={'username': 'bob', 'password': 'bobpw'})
    assert r.status_code == 200 and r.get_json()['success'], r.data
    assert web_server.get_database().get_profile_navidrome_login(as_bob) == ('bob', 'bobpw')
    libs = client.get('/api/profiles/me/server-library').get_json()
    assert libs['navidrome_username'] == 'bob'
    assert 'bobpw' not in libs.values()


def test_a_wrong_login_is_refused_and_nothing_is_saved(client, as_bob, navidrome):
    r = client.post('/api/profiles/me/navidrome-login', json={'username': 'bob', 'password': 'nope'})
    assert r.status_code == 400
    assert 'refused' in r.get_json()['error']
    assert web_server.get_database().get_profile_navidrome_login(as_bob) is None


def test_missing_fields_are_a_400(client, as_bob, navidrome):
    assert client.post('/api/profiles/me/navidrome-login', json={'username': 'bob'}).status_code == 400


def test_no_navidrome_client_is_a_503(client, as_bob, monkeypatch):
    import api.user_profiles as up
    monkeypatch.setattr(up, '_media_server_engine', lambda: None)
    assert client.post('/api/profiles/me/navidrome-login', json={'username': 'bob', 'password': 'x'}).status_code == 503


def test_delete_clears_the_login(client, as_bob, navidrome):
    client.post('/api/profiles/me/navidrome-login', json={'username': 'bob', 'password': 'bobpw'})
    assert client.delete('/api/profiles/me/navidrome-login').get_json()['success']
    assert web_server.get_database().get_profile_navidrome_login(as_bob) is None


def test_the_login_is_per_profile(client, as_bob, navidrome):
    client.post('/api/profiles/me/navidrome-login', json={'username': 'bob', 'password': 'bobpw'})
    assert web_server.get_database().get_profile_navidrome_login(1) is None
