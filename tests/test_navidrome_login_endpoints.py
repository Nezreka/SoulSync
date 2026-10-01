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


# the admin's own playlist account (Cremonies). settings > navidrome saves it
# through the same route as profile 1, and sync then writes as that user.


@pytest.fixture
def as_admin(client):
    with client.session_transaction() as sess:
        sess['profile_id'] = 1
    yield 1
    web_server.get_database().set_profile_navidrome_login(1, None, None)


class _ViewableNavidrome(_FakeNavidrome):
    def as_user(self, username, password):
        return ('view', username, password)


def test_admin_playlist_account_is_what_sync_writes_as(client, as_admin, navidrome, monkeypatch):
    from services.sync_service import navidrome_client_for_profile
    # the lookup opens MusicDatabase() at its default path, which is the app's
    # own db in production. in a mixed test run that path belongs to whichever
    # module set it first, so pin it to the db the endpoint just wrote to
    import database.music_database as mdb
    monkeypatch.setattr(mdb, 'MusicDatabase', lambda *a, **k: web_server.get_database())
    shared = _ViewableNavidrome()
    assert navidrome_client_for_profile(1, shared) is shared   # nothing saved yet
    r = client.post('/api/profiles/me/navidrome-login', json={'username': 'bob', 'password': 'bobpw'})
    assert r.status_code == 200, r.data
    assert client.get('/api/profiles/me/server-library').get_json()['navidrome_username'] == 'bob'
    assert navidrome_client_for_profile(1, shared) == ('view', 'bob', 'bobpw')
    client.delete('/api/profiles/me/navidrome-login')
    assert navidrome_client_for_profile(1, shared) is shared


def test_settings_page_wires_the_playlist_account():
    from pathlib import Path
    root = Path(web_server.__file__).resolve().parent
    html = (root / 'webui' / 'index.html').read_text(encoding='utf-8')
    js = (root / 'webui' / 'static' / 'settings.js').read_text(encoding='utf-8')
    for el in ('navidrome-playlist-username', 'navidrome-playlist-password',
               'navidrome-playlist-status', 'navidrome-playlist-clear'):
        assert f'id="{el}"' in html
        assert f"'{el}'" in js
    for fn in ('saveNavidromePlaylistLogin', 'clearNavidromePlaylistLogin'):
        assert f'onclick="{fn}()"' in html
        assert f'async function {fn}()' in js
    # loaded with the rest of the navidrome fields
    load = js.index("document.getElementById('navidrome-password').value = settings.navidrome")
    assert js.index('loadNavidromePlaylistLogin();', load) - load < 200
    # and never sent with the app config: the main save builds navidrome from
    # its own three fields only
    save = js.index("username: document.getElementById('navidrome-username').value")
    assert 'navidrome-playlist' not in js[save - 400:save + 400]
