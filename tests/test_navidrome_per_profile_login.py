"""a navidrome login per profile (#1265): playlists a profile syncs land on
their own navidrome user.

subsonic writes playlists as whoever authenticated and has no admin
impersonation, so every playlist soulsync wrote belonged to the app account.
now a profile can save its own login; the sync then acts through
NavidromeUserView, the shared client bound to that user, and name lookups
only ever return the caller's own playlists so two profiles' same-named
playlists cannot stomp each other.

everything here is hermetic: the transport is a fake _make_request on the
shared client that records who each request went out as. the same flows
were run once against a real navidrome (0.59) with a second user and
behaved the same.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.navidrome_client import NavidromeClient, NavidromeUserView


# ── a fake navidrome ────────────────────────────────────────────────────────

class FakeNavidrome:
    """playlists per owner, answering the subsonic calls the sync makes.
    records (endpoint, user) for every request."""

    def __init__(self):
        self.playlists = {}      # id -> {name, owner, songs}
        self.calls = []
        self._n = 0
        self.refuse = set()      # users whose login is wrong

    def request(self, endpoint, params=None, as_user=None, timeout=None):
        user = (as_user or ('admin', 'pw'))[0]
        self.calls.append((endpoint, user))
        if user in self.refuse:
            return None
        params = params or {}
        if endpoint == 'ping':
            return {'status': 'ok'}
        if endpoint == 'getPlaylists':
            visible = [p for p in self.playlists.values() if user == 'admin' or p['owner'] == user]
            return {'status': 'ok', 'playlists': {'playlist': [
                {'id': i, 'name': p['name'], 'owner': p['owner'], 'songCount': len(p['songs']), 'duration': 0}
                for i, p in self.playlists.items() if p in visible]}}
        if endpoint == 'getPlaylist':
            p = self.playlists[params['id']]
            return {'status': 'ok', 'playlist': {'id': params['id'], 'name': p['name'],
                                                 'entry': [{'id': s, 'title': s} for s in p['songs']]}}
        if endpoint == 'createPlaylist':
            songs = list(params.get('songId') or [])
            pid = params.get('playlistId')
            if pid:
                if self.playlists[pid]['owner'] != user and user != 'admin':
                    return None
                self.playlists[pid]['songs'] = songs
            else:
                self._n += 1
                pid = f"pl{self._n}"
                self.playlists[pid] = {'name': params['name'], 'owner': user, 'songs': songs}
            return {'status': 'ok', 'playlist': {'id': pid, 'owner': self.playlists[pid]['owner']}}
        if endpoint == 'updatePlaylist':
            p = self.playlists[params['playlistId']]
            if p['owner'] != user and user != 'admin':
                return None
            for i in sorted(params.get('songIndexToRemove') or [], reverse=True):
                p['songs'].pop(i)
            p['songs'].extend(params.get('songIdToAdd') or [])
            return {'status': 'ok'}
        if endpoint == 'deletePlaylist':
            self.playlists.pop(params['id'], None)
            return {'status': 'ok'}
        if endpoint == 'getScanStatus':
            return {'status': 'ok', 'scanStatus': {'scanning': False, 'count': 3}}
        if endpoint == 'search3':
            if params.get('songOffset', 0):
                return {'status': 'ok', 'searchResult3': {'song': []}}
            return {'status': 'ok', 'searchResult3': {'song': [{'id': s, 'title': s} for s in ('s1', 's2', 's3')]}}
        raise AssertionError(f"unexpected {endpoint}")

    def owners_of(self, name):
        return sorted((p['owner'], p['songs']) for p in self.playlists.values() if p['name'] == name)


def _tracks(*ids):
    return [SimpleNamespace(ratingKey=i, title=i) for i in ids]


@pytest.fixture()
def nav(monkeypatch):
    server = FakeNavidrome()
    client = NavidromeClient()
    client.base_url = 'http://navidrome'
    client.username = 'admin'
    client.password = 'pw'
    client._connection_attempted = True
    monkeypatch.setattr(client, '_make_request', server.request)
    # the write validator resolves tracks against the app db; the songs it
    # asks for are the fake's
    import core.library.navidrome_identity as ident
    monkeypatch.setattr(ident, 'resolve_tracks', lambda tracks, songs, db: list(tracks))
    monkeypatch.setattr(ident, 'get_database', lambda: None, raising=False)
    return SimpleNamespace(server=server, client=client)


# ── the view ────────────────────────────────────────────────────────────────

def test_the_view_is_a_client_that_requests_as_its_user(nav):
    view = nav.client.as_user('bob', 'bobpw')
    assert isinstance(view, NavidromeClient) and isinstance(view, NavidromeUserView)
    view._make_request('ping')
    assert nav.server.calls[-1] == ('ping', 'bob')
    assert view.acting_as == 'bob' and view.username == 'bob' and view.password == 'bobpw'
    # the shared client is untouched and still asks as itself
    assert nav.client.username == 'admin'
    nav.client._make_request('ping')
    assert nav.server.calls[-1] == ('ping', 'admin')


def test_the_view_reads_shared_state_and_delegates_connection(nav):
    view = nav.client.as_user('bob', 'bobpw')
    assert view.base_url == 'http://navidrome'
    assert view._album_cache is nav.client._album_cache
    assert view.ensure_connection() is True


def test_a_playlist_written_through_the_view_belongs_to_the_user(nav):
    view = nav.client.as_user('bob', 'bobpw')
    assert view.update_playlist('Chill', _tracks('s1', 's2')) is True
    assert nav.server.owners_of('Chill') == [('bob', ['s1', 's2'])]
    assert all(user == 'bob' for _, user in nav.server.calls)


def test_same_named_playlists_of_two_users_do_not_collide(nav):
    bob = nav.client.as_user('bob', 'bobpw')
    assert bob.update_playlist('Chill', _tracks('s1', 's2'))
    assert nav.client.update_playlist('Chill', _tracks('s3'))
    assert nav.server.owners_of('Chill') == [('admin', ['s3']), ('bob', ['s1', 's2'])]
    # each side edits only its own on reconcile and append
    assert bob.reconcile_playlist('Chill', _tracks('s1', 's2', 's3'))
    assert nav.client.append_to_playlist('Chill', _tracks('s3', 's1'))
    assert nav.server.owners_of('Chill') == [('admin', ['s3', 's1']), ('bob', ['s1', 's2', 's3'])]
    # and a replace-mode sync by the admin does not delete bob's as a "duplicate"
    assert nav.client.update_playlist('Chill', _tracks('s2'))
    assert nav.server.owners_of('Chill') == [('admin', ['s2']), ('bob', ['s1', 's2', 's3'])]


def test_name_lookups_are_owner_scoped_but_get_all_is_not(nav):
    bob = nav.client.as_user('bob', 'bobpw')
    bob.update_playlist('Chill', _tracks('s1'))
    nav.client.update_playlist('Chill', _tracks('s2'))
    assert [p.owner for p in bob.get_playlists_by_name('Chill')] == ['bob']
    assert [p.owner for p in nav.client.get_playlists_by_name('Chill')] == ['admin']
    assert nav.client.get_playlist_by_name('Chill').owner == 'admin'
    assert bob.get_playlist_by_name('Chill').owner == 'bob'
    # the server playlists page lists what the account can see
    assert sorted(p.owner for p in nav.client.get_all_playlists()) == ['admin', 'bob']


def test_a_playlist_with_no_owner_field_is_still_found(nav):
    """older servers: no owner in getPlaylists. the old behaviour holds."""
    nav.server.playlists['x'] = {'name': 'Old', 'owner': None, 'songs': []}
    assert nav.client.get_playlist_by_name('Old') is not None


def test_owner_comparison_ignores_case(nav):
    nav.client.username = 'Admin'
    nav.server.playlists['x'] = {'name': 'Mine', 'owner': 'admin', 'songs': []}
    assert nav.client.get_playlist_by_name('Mine') is not None


def test_verify_user_login(nav):
    assert nav.client.verify_user_login('bob', 'bobpw') == (True, None)
    nav.server.refuse.add('bob')
    ok, err = nav.client.verify_user_login('bob', 'bad')
    assert ok is False and err
    assert nav.client.verify_user_login('', 'x')[0] is False


# ── the db ──────────────────────────────────────────────────────────────────

def test_profile_login_roundtrip_is_encrypted_and_clearable(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / 'm.db'))
    pid = db.create_profile(name='bob')
    assert db.get_profile_navidrome_login(pid) is None
    assert db.set_profile_navidrome_login(pid, 'bob', 'bobpw')
    assert db.get_profile_navidrome_login(pid) == ('bob', 'bobpw')
    with db._get_connection() as conn:
        raw = conn.execute("SELECT navidrome_password FROM profiles WHERE id = ?", (pid,)).fetchone()[0]
    assert raw != 'bobpw' and raw.startswith('gAAAAA')
    assert db.get_profile_server_library(pid)['navidrome_username'] == 'bob'
    assert 'bobpw' not in str(db.get_profile_server_library(pid))
    assert db.set_profile_navidrome_login(pid, None, None)
    assert db.get_profile_navidrome_login(pid) is None
    assert db.get_profile_server_library(pid)['navidrome_username'] is None


def test_a_username_without_a_password_is_refused(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / 'm.db'))
    pid = db.create_profile(name='bob')
    assert db.set_profile_navidrome_login(pid, 'bob', '') is False
    assert db.get_profile_navidrome_login(pid) is None


# ── the sync ────────────────────────────────────────────────────────────────

def _sync_service(monkeypatch, client, login):
    from services import sync_service as ss
    svc = ss.PlaylistSyncService.__new__(ss.PlaylistSyncService)
    svc._media_client = lambda name: client if name == 'navidrome' else None
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'navidrome')

    class _DB:
        def get_profile_navidrome_login(self, pid):
            return login.get(pid)
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda *a, **k: _DB())
    return svc


def test_sync_acts_as_the_profiles_user_when_it_has_a_login(nav, monkeypatch):
    svc = _sync_service(monkeypatch, nav.client, {2: ('bob', 'bobpw')})
    client, server = svc._get_active_media_client(profile_id=2)
    assert server == 'navidrome' and isinstance(client, NavidromeUserView) and client.acting_as == 'bob'
    assert client.update_playlist('Chill', _tracks('s1'))
    assert nav.server.owners_of('Chill') == [('bob', ['s1'])]


def test_sync_stays_on_the_app_account_without_a_login(nav, monkeypatch):
    svc = _sync_service(monkeypatch, nav.client, {})
    client, _ = svc._get_active_media_client(profile_id=2)
    assert client is nav.client
    client, _ = svc._get_active_media_client(profile_id=None)
    assert client is nav.client


def test_a_db_failure_reading_the_login_falls_back_to_the_app_account(nav, monkeypatch):
    svc = _sync_service(monkeypatch, nav.client, {})

    class _Boom:
        def get_profile_navidrome_login(self, pid):
            raise RuntimeError('db locked')
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda *a, **k: _Boom())
    client, _ = svc._get_active_media_client(profile_id=2)
    assert client is nav.client


def test_reconcile_or_replace_still_treats_the_view_as_navidrome(nav, monkeypatch):
    """the navidrome branch (no destructive fallback) has to hold for the view"""
    from services import sync_service as ss
    svc = ss.PlaylistSyncService.__new__(ss.PlaylistSyncService)
    view = nav.client.as_user('bob', 'bobpw')
    monkeypatch.setattr(view.__class__, 'reconcile_playlist', lambda self, n, t: False, raising=False)
    called = []
    monkeypatch.setattr(view.__class__, 'update_playlist', lambda self, n, t: called.append(n) or True, raising=False)
    assert svc._reconcile_or_replace(view, 'Chill', _tracks('s1')) is False
    assert called == [], "a failed navidrome reconcile must not fall back to a destructive replace"
