"""a jellyfin user per profile, done as a view (#1265).

the api key authenticates every jellyfin call and the user is a parameter,
so a profile's playlists already landed on their chosen jellyfin user. but
the sync got there by assigning client.user_id on the one shared client,
which made every other caller that user until the next sync overwrote it:
with two profiles syncing at once, one's playlist went to the other's
user. JellyfinUserView is the same client with its own user_id (and
library), everything else read from the shared client, nothing on it
changed. hermetic: requests and _make_request are fakes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.jellyfin_client as jc
from core.jellyfin_client import JellyfinClient, JellyfinUserView


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.text = body, status, ''

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


@pytest.fixture()
def jelly(monkeypatch):
    client = JellyfinClient()
    client.base_url = 'http://jf'
    client.api_key = 'key'
    client.user_id = 'admin-uid'
    client.music_library_id = 'lib-admin'
    client._connection_attempted = True
    requests_seen = []
    playlists = {}      # id -> {'name', 'user'}

    def fake_post(url, json=None, params=None, headers=None, timeout=None):
        requests_seen.append(('POST', url, json or params))
        if url.endswith('/Playlists'):
            pid = f"pl-{len(playlists) + 1}"
            playlists[pid] = {'name': json['Name'], 'user': json['UserId']}
            return _Resp({'Id': pid})
        return _Resp({}, 204)
    monkeypatch.setattr(jc.requests, 'post', fake_post)

    # class-level: the view runs the class's methods with itself as self, so
    # an instance-level fake on the base client would never be reached
    def fake_request(self, endpoint, params=None, **kw):
        requests_seen.append(('GET', endpoint, params))
        if endpoint.startswith('/Users/') and endpoint.endswith('/Items') and (params or {}).get('IncludeItemTypes') == 'Playlist':
            uid = endpoint.split('/')[2]
            return {'Items': [{'Id': i, 'Name': p['name']} for i, p in playlists.items() if p['user'] == uid]}
        return {'Items': []}
    monkeypatch.setattr(JellyfinClient, '_make_request', fake_request)
    monkeypatch.setattr(JellyfinClient, '_is_valid_guid', lambda self, x: True)
    return SimpleNamespace(client=client, seen=requests_seen, playlists=playlists)


def _tracks(*ids):
    return [SimpleNamespace(ratingKey=i, title=i, artist='a', album='b') for i in ids]


def test_the_view_has_its_own_user_and_reads_the_rest_from_the_shared_client(jelly):
    view = jelly.client.as_user('kid-uid', 'lib-kids')
    assert isinstance(view, JellyfinUserView) and isinstance(view, JellyfinClient)
    assert view.user_id == 'kid-uid' and view.music_library_id == 'lib-kids' and view.acting_as == 'kid-uid'
    assert view.base_url == 'http://jf' and view.api_key == 'key' and view.ensure_connection() is True
    assert view._album_cache is jelly.client._album_cache
    # the shared client is untouched
    assert jelly.client.user_id == 'admin-uid' and jelly.client.music_library_id == 'lib-admin'


def test_a_view_without_a_library_pick_uses_the_app_library(jelly):
    assert jelly.client.as_user('kid-uid').music_library_id == 'lib-admin'


def test_a_playlist_created_through_the_view_belongs_to_the_user(jelly):
    view = jelly.client.as_user('kid-uid')
    assert view.create_playlist('Chill', _tracks('t1', 't2')) is True
    assert list(jelly.playlists.values()) == [{'name': 'Chill', 'user': 'kid-uid'}]
    adds = [r for r in jelly.seen if r[0] == 'POST' and '/Items' in r[1]]
    assert adds and all(r[2]['UserId'] == 'kid-uid' for r in adds)
    # and the admin's own write is still the admin's
    assert jelly.client.create_playlist('Mine', _tracks('t3')) is True
    assert jelly.playlists['pl-2'] == {'name': 'Mine', 'user': 'admin-uid'}


def test_reads_through_the_view_go_to_the_users_path(jelly):
    view = jelly.client.as_user('kid-uid')
    view.get_playlist_by_name('anything')
    assert any(r[1].startswith('/Users/kid-uid/') for r in jelly.seen if r[0] == 'GET')
    assert not any(r[1].startswith('/Users/admin-uid/') for r in jelly.seen if r[0] == 'GET')


def test_two_views_do_not_share_a_user(jelly):
    a = jelly.client.as_user('a-uid')
    b = jelly.client.as_user('b-uid')
    assert (a.user_id, b.user_id, jelly.client.user_id) == ('a-uid', 'b-uid', 'admin-uid')


# ── the sync ────────────────────────────────────────────────────────────────

def _sync_service(monkeypatch, client, libs):
    from services import sync_service as ss
    svc = ss.PlaylistSyncService.__new__(ss.PlaylistSyncService)
    svc._media_client = lambda name: client if name == 'jellyfin' else None
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'jellyfin')

    class _DB:
        def get_profile_server_library(self, pid):
            return libs.get(pid, {})
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda *a, **k: _DB())
    return svc


def test_sync_acts_as_the_profiles_user_without_touching_the_shared_client(jelly, monkeypatch):
    svc = _sync_service(monkeypatch, jelly.client, {2: {'jellyfin_user_id': 'kid-uid', 'jellyfin_library_id': 'lib-kids'}})
    client, server = svc._get_active_media_client(profile_id=2)
    assert server == 'jellyfin' and isinstance(client, JellyfinUserView)
    assert client.user_id == 'kid-uid' and client.music_library_id == 'lib-kids'
    assert jelly.client.user_id == 'admin-uid', "the shared client was mutated"


def test_a_library_only_pick_still_gets_a_view(jelly, monkeypatch):
    svc = _sync_service(monkeypatch, jelly.client, {2: {'jellyfin_library_id': 'lib-kids'}})
    client, _ = svc._get_active_media_client(profile_id=2)
    assert client.user_id == 'admin-uid' and client.music_library_id == 'lib-kids'


def test_sync_stays_on_the_shared_client_without_a_pick(jelly, monkeypatch):
    svc = _sync_service(monkeypatch, jelly.client, {})
    assert svc._get_active_media_client(profile_id=2)[0] is jelly.client
    assert svc._get_active_media_client(profile_id=None)[0] is jelly.client


def test_two_profiles_syncing_at_once_keep_their_own_jellyfin_user(jelly, monkeypatch):
    """the race the assignment had: profile 3's sync setting the user while
    profile 2's was mid-flight. two views, two users, at the same time."""
    svc = _sync_service(monkeypatch, jelly.client, {2: {'jellyfin_user_id': 'kid-uid'}, 3: {'jellyfin_user_id': 'annie-uid'}})
    two, _ = svc._get_active_media_client(profile_id=2)
    three, _ = svc._get_active_media_client(profile_id=3)
    assert two.create_playlist('Chill', _tracks('t1')) and three.create_playlist('Chill', _tracks('t2'))
    assert sorted(p['user'] for p in jelly.playlists.values()) == ['annie-uid', 'kid-uid']
    assert two.user_id == 'kid-uid'          # untouched by three's sync


# ── the users list ──────────────────────────────────────────────────────────

def test_users_with_music_are_found_in_parallel_and_cached(monkeypatch):
    """personal settings waited on one views request per user, in series"""
    import threading
    import time as _time
    client = JellyfinClient()
    client.base_url, client.api_key, client.user_id = 'http://jf', 'key', 'admin'
    client._connection_attempted = True
    calls = []
    lock = threading.Lock()

    def fake_request(self, endpoint, params=None, **kw):
        with lock:
            calls.append(endpoint)
        if endpoint == '/Users':
            return [{'Id': f'u{i}', 'Name': f'User {i}'} for i in range(8)]
        _time.sleep(0.2)
        uid = endpoint.split('/')[2]
        return {'Items': [{'CollectionType': 'music' if uid != 'u3' else 'movies'}]}
    monkeypatch.setattr(JellyfinClient, '_make_request', fake_request)

    start = _time.monotonic()
    users = client.get_available_users()
    elapsed = _time.monotonic() - start
    assert [u['id'] for u in users] == [f'u{i}' for i in range(8) if i != 3]
    assert elapsed < 1.0, f"eight 0.2s views requests took {elapsed:.1f}s: they ran in series"
    assert calls.count('/Users') == 1

    # the second ask is answered from the cache
    assert client.get_available_users() == users
    assert calls.count('/Users') == 1
    client.clear_cache()
    client.get_available_users()
    assert calls.count('/Users') == 2
