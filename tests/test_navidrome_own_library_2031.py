"""own libraries on navidrome (#2031).

navidrome keeps more than one library, each with its own users, since 0.58.
an own-library profile on navidrome picks its library from what its own
navidrome login can see, its scans read only that library (musicFolderId)
and map under ``own:<pid>``, its downloads land in its own folder and its
playlists are written as its own navidrome user. the shared library is
untouched, and a navidrome older than 0.58 keeps the shared-folder fallback
with a message saying why.

hermetic: the http session under NavidromeClient is a fake subsonic server
(token auth checked, libraries per user, the calls recorded). nothing goes
over the network and every database is a temp one.
"""

from __future__ import annotations

import hashlib
import os
from types import SimpleNamespace

import pytest

import core.library_scope as scope_mod
import core.navidrome_client as nc
from core.navidrome_client import NavidromeClient, NavidromeUserView
from database.music_database import MusicDatabase


# ── a fake subsonic server ──────────────────────────────────────────────────

class FakeSubsonic:
    """navidrome as seen through the subsonic api: two libraries, the app
    account (an admin) sees both, the friend only his own. on a version
    before 0.58 there are no per-user libraries at all."""

    def __init__(self, version='0.64.2 (abc123)'):
        self.version = version
        self.libraries = {1: 'Music', 2: 'Friend'}
        self.users = {'admin': ('pw', [1, 2]), 'friend': ('fpw', [2])}
        self.artists = {'ar-house': ('House Band', 1), 'ar-friend': ('Friend Band', 2)}
        self.albums = {'al-house': ('House Album', 'ar-house', 1),
                       'al-friend': ('Friend Album', 'ar-friend', 2)}
        self.songs = {'s-house': ('House Song', 'al-house', '/music/House Band/House Album/01 - House Song.flac'),
                      's-friend': ('Friend Song', 'al-friend', '/friend/Friend Band/Friend Album/01 - Friend Song.flac')}
        self.playlists = {}
        self.calls = []          # (endpoint, user, params)

    # transport
    def get(self, url, params=None, timeout=None):
        return self._answer(url, dict(params or {}))

    def post(self, url, data=None, timeout=None):
        return self._answer(url, dict(data or {}))

    def _answer(self, url, params):
        endpoint = url.rsplit('/rest/', 1)[1]
        user = params.pop('u', None)
        token, salt = params.pop('t', None), params.pop('s', None)
        for k in ('v', 'c', 'f'):
            params.pop(k, None)
        self.calls.append((endpoint, user, params))
        known = self.users.get(user)
        if not known or hashlib.md5((known[0] + str(salt)).encode()).hexdigest() != token:
            return _Resp(self._fail(40, 'Wrong username or password'))
        try:
            body = getattr(self, 'ep_' + endpoint)(user, params)
        except _ApiError as e:
            return _Resp(self._fail(e.code, str(e)))
        out = {'status': 'ok', 'version': '1.16.1', 'type': 'navidrome',
               'serverVersion': self.version, 'openSubsonic': True}
        out.update(body)
        return _Resp({'subsonic-response': out})

    def _fail(self, code, message):
        return {'subsonic-response': {'status': 'failed', 'version': '1.16.1',
                                      'error': {'code': code, 'message': message}}}

    # libraries
    def _multi(self):
        parsed = nc.parse_server_version(self.version)
        return bool(parsed and parsed[:2] >= (0, 58))

    def _libs(self, user, params):
        mine = self.users[user][1] if self._multi() else list(self.libraries)
        folder = params.get('musicFolderId')
        if folder is None:
            return set(mine)
        if int(folder) not in mine:
            raise _ApiError(70, 'Library not found or empty')
        return {int(folder)}

    # endpoints
    def ep_ping(self, user, params):
        return {}

    def ep_getMusicFolders(self, user, params):
        libs = sorted(self._libs(user, {}))
        return {'musicFolders': {'musicFolder': [{'id': i, 'name': self.libraries[i]} for i in libs]}}

    def ep_getArtists(self, user, params):
        libs = self._libs(user, params)
        return {'artists': {'index': [{'name': 'X', 'artist': [
            {'id': aid, 'name': name, 'albumCount': 1}
            for aid, (name, lib) in self.artists.items() if lib in libs]}]}}

    def ep_getArtist(self, user, params):
        # navidrome ignores musicFolderId here: the client filters by folder
        libs = self._libs(user, {})
        name, _lib = self.artists[params['id']]
        return {'artist': {'id': params['id'], 'name': name, 'album': [
            self._album(alid) for alid, (_t, ar, lib) in self.albums.items()
            if ar == params['id'] and lib in libs]}}

    def _album(self, alid):
        title, ar, _lib = self.albums[alid]
        return {'id': alid, 'name': title, 'artistId': ar, 'artist': self.artists[ar][0],
                'songCount': sum(1 for s in self.songs.values() if s[1] == alid), 'year': 2020}

    def ep_getAlbumList2(self, user, params):
        libs = self._libs(user, params)
        if int(params.get('offset', 0)):
            return {'albumList2': {'album': []}}
        return {'albumList2': {'album': [self._album(alid) for alid, (_t, _ar, lib) in self.albums.items()
                                         if lib in libs]}}

    def ep_getAlbum(self, user, params):
        alid = params['id']
        if self.albums[alid][2] not in self._libs(user, {}):
            raise _ApiError(70, 'Album not found')
        return {'album': dict(self._album(alid), song=[self._song(sid) for sid, s in self.songs.items()
                                                       if s[1] == alid])}

    def _song(self, sid):
        title, alid, path = self.songs[sid]
        album, ar, _lib = self.albums[alid]
        return {'id': sid, 'title': title, 'album': album, 'albumId': alid, 'artist': self.artists[ar][0],
                'artistId': ar, 'track': 1, 'discNumber': 1, 'duration': 200, 'path': path,
                'suffix': 'flac', 'size': 1000, 'bitRate': 900}

    def ep_search3(self, user, params):
        libs = self._libs(user, params)
        if int(params.get('songOffset', 0)):
            return {'searchResult3': {'song': []}}
        return {'searchResult3': {'song': [self._song(sid) for sid, s in self.songs.items()
                                           if self.albums[s[1]][2] in libs]}}

    def ep_getScanStatus(self, user, params):
        return {'scanStatus': {'scanning': False, 'count': 7}}

    def ep_startScan(self, user, params):
        return {'scanStatus': {'scanning': True}}

    def ep_getPlaylists(self, user, params):
        return {'playlists': {'playlist': [
            {'id': pid, 'name': p['name'], 'owner': p['owner'], 'songCount': len(p['songs']), 'duration': 0}
            for pid, p in self.playlists.items() if user == 'admin' or p['owner'] == user]}}

    def ep_getPlaylist(self, user, params):
        p = self.playlists[params['id']]
        return {'playlist': {'id': params['id'], 'name': p['name'], 'owner': p['owner'],
                             'entry': [self._song(s) for s in p['songs']]}}

    def ep_createPlaylist(self, user, params):
        songs = params.get('songId') or []
        songs = [songs] if isinstance(songs, str) else list(songs)
        visible = {sid for sid, s in self.songs.items() if self.albums[s[1]][2] in self._libs(user, {})}
        if any(s not in visible for s in songs):
            raise _ApiError(70, 'Song not found')
        pid = f"pl{len(self.playlists) + 1}"
        self.playlists[pid] = {'name': params['name'], 'owner': user, 'songs': songs}
        return {'playlist': {'id': pid, 'owner': user}}

    def requests_by(self, user):
        return [(e, p) for e, u, p in self.calls if u == user]


class _ApiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def server(monkeypatch):
    fake = FakeSubsonic()
    monkeypatch.setattr(nc, '_http', lambda: fake)
    # the version module state is process-wide: start every test unknown
    monkeypatch.setattr(nc, '_server_version', None)
    monkeypatch.setattr(nc, '_server_version_loaded', False)
    return fake


@pytest.fixture()
def db(tmp_path, monkeypatch):
    d = MusicDatabase(str(tmp_path / 'music.db'))
    import database.music_database as md
    monkeypatch.setattr(md, 'get_database', lambda *a, **k: d)
    # only the bare MusicDatabase() the navidrome client opens: get_database's
    # own MusicDatabase(path) must stay real, or its per-thread cache keeps
    # this temp db for every later test in the process
    real = md.MusicDatabase
    monkeypatch.setattr(md, 'MusicDatabase', lambda *a, **k: d if not a and not k else real(*a, **k))
    scope_mod.invalidate_library_scope_cache()
    yield d
    scope_mod.invalidate_library_scope_cache()


@pytest.fixture()
def navidrome_active(monkeypatch):
    from core.settings import config_manager
    monkeypatch.setattr(config_manager, 'get_active_media_server', lambda: 'navidrome')
    monkeypatch.setattr(config_manager, 'get_navidrome_config',
                        lambda: {'base_url': 'http://navidrome:4533', 'username': 'admin', 'password': 'pw'})


@pytest.fixture()
def shared_client(server, db, navidrome_active):
    """the app's navidrome client, connected for real against the fake, with
    the shared library's folder (1) picked in settings."""
    db.set_preference('navidrome_music_folder_id', '1')
    db.set_preference('navidrome_music_folder', 'Music')
    client = NavidromeClient()
    assert client.ensure_connection()
    assert client.music_folder_id == '1'
    return client


@pytest.fixture()
def friend(db, tmp_path):
    """a profile with an own library, its own navidrome login and its pick."""
    root = tmp_path / 'libraries' / 'friend'
    root.mkdir(parents=True)
    pid = db.create_profile(name='Friend')
    assert db.set_profile_library(pid, 'own', str(root))
    db.set_profile_navidrome_login(pid, 'friend', 'fpw')
    db.set_profile_server_library(pid, 'navidrome', library_id='2')
    scope_mod.invalidate_library_scope_cache()
    return SimpleNamespace(id=pid, root=str(root))


def _engine(client):
    return SimpleNamespace(client=lambda name: client if name == 'navidrome' else None)


# ── the version gate ────────────────────────────────────────────────────────

@pytest.mark.parametrize('text,parsed,ok', [
    ('0.58.0 (fc4e5a4)', (0, 58, 0), True),
    ('v0.64.2', (0, 64, 2), True),
    ('1.0', (1, 0, 0), True),
    ('0.57.1 (abc)', (0, 57, 1), False),
    ('0.49.3', (0, 49, 3), False),
    ('', None, False),
    ('dev', None, False),
])
def test_server_versions(text, parsed, ok):
    assert nc.parse_server_version(text) == parsed
    assert nc.multi_library_supported(text) is ok


def test_connecting_learns_the_version_and_turns_own_libraries_on(shared_client, server, db):
    assert nc.known_server_version() == '0.64.2 (abc123)'
    # kept in the db, so a restart with navidrome down still knows it
    assert db.get_preference('navidrome_server_version') == '0.64.2 (abc123)'
    assert scope_mod.own_libraries_supported_on('navidrome') is True
    assert scope_mod.own_library_unsupported_reason('navidrome') is None


def test_a_remembered_version_answers_before_navidrome_does(server, db, navidrome_active):
    db.set_preference('navidrome_server_version', '0.60.0')
    assert scope_mod.own_library_supported() is True


def test_an_old_navidrome_keeps_the_shared_fallback_with_a_reason(server, db, navidrome_active, friend):
    server.version = '0.57.0 (old)'
    client = NavidromeClient()
    assert client.ensure_connection()
    reason = scope_mod.own_library_unsupported_reason('navidrome')
    assert '0.58' in reason and '0.57.0' in reason
    assert scope_mod.library_scope_for_profile(friend.id) == 'shared'
    from core.imports.paths import library_root_for_profile
    assert library_root_for_profile(friend.id, announce=False) is None
    import api.database_admin as da
    monkey = SimpleNamespace(get_database=lambda: db)
    da_get = da.get_database
    try:
        da.get_database = monkey.get_database
        assert da._own_library_scan_clients('navidrome') == []
    finally:
        da.get_database = da_get


def test_an_unknown_version_is_not_enough(server, db, navidrome_active):
    reason = scope_mod.own_library_unsupported_reason('navidrome')
    assert reason and 'has not reached Navidrome' in reason
    assert scope_mod.own_library_supported() is False


def test_an_upgrade_seen_on_connect_drops_the_cached_scopes(server, db, navidrome_active, friend):
    server.version = '0.57.0'
    NavidromeClient().ensure_connection()
    assert scope_mod.library_scope_for_profile(friend.id) == 'shared'
    server.version = '0.58.1'
    NavidromeClient().ensure_connection()
    # no 30s wait: the version flip invalidated the cache
    assert scope_mod.library_scope_for_profile(friend.id) == friend.id


# ── the scan reads only the profile's library ──────────────────────────────

def test_the_own_library_scan_client_reads_only_their_library_as_them(shared_client, server, db, friend, monkeypatch):
    import api.database_admin as da
    monkeypatch.setattr(da, 'get_database', lambda: db)
    monkeypatch.setattr(da, 'media_server_engine', _engine(shared_client))

    [(prof, view)] = da._own_library_scan_clients('navidrome')
    assert prof['id'] == friend.id
    assert isinstance(view, NavidromeUserView) and view.acting_as == 'friend'
    assert view.music_folder_id == '2'

    server.calls.clear()
    assert [a.title for a in view.get_all_artists()] == ['Friend Band']
    assert view.get_all_album_ids() == {'al-friend'}
    assert [a.title for a in view.get_recently_added_albums()] == ['Friend Album']
    assert [a['id'] for a in view.get_album_census()] == ['al-friend']
    albums = view.get_albums_for_artist('ar-friend')
    assert [a.title for a in albums] == ['Friend Album']
    # every listing went out as the friend, on library 2
    for endpoint, user, params in server.calls:
        assert user == 'friend'
        if endpoint in ('getArtists', 'getAlbumList2'):
            assert params.get('musicFolderId') == '2'

    # the shared client is untouched: its folder, its caches, its account
    assert shared_client.music_folder_id == '1'
    assert shared_client.username == 'admin'
    assert 'ar-friend' not in shared_client._album_cache
    assert shared_client._folder_album_ids is None
    assert [a.title for a in shared_client.get_all_artists()] == ['House Band']


def test_the_own_scan_runs_under_their_owner_and_maps_own_pid(shared_client, server, db, friend, monkeypatch):
    """the scan phase runs one worker per own library with the profile as
    owner and the scope set, so every mapping it writes is own:<pid>."""
    import api.database_admin as da
    from core.library2.media_server_sync import scan_library_id
    monkeypatch.setattr(da, 'get_database', lambda: db)
    monkeypatch.setattr(da, 'media_server_engine', _engine(shared_client))
    monkeypatch.setattr(da, '_db_update_phase_callback', lambda *a: None)
    seen = []

    class _Worker:
        def __init__(self, media_client, full_refresh, server_type, force_sequential, owner_profile_id):
            self.client, self.owner = media_client, owner_profile_id
            self.processed_artists = self.processed_tracks = 0

        def connect_callback(self, *a):
            pass

        def run_deep_scan(self):
            seen.append((self.owner, scan_library_id(), self.client.music_folder_id,
                         [a.title for a in self.client.get_all_artists()]))

    monkeypatch.setattr(da, 'DatabaseUpdateWorker', _Worker)
    da._run_own_library_scans('navidrome', deep=True)
    assert seen == [(friend.id, f'own:{friend.id}', '2', ['Friend Band'])]
    # and the scope is back to the shared scan's after
    assert scan_library_id() == ''


def test_a_real_scan_maps_their_song_under_own_pid_and_leaves_the_house_alone(shared_client, server, db, friend, monkeypatch):
    """the whole phase, real worker and real catalogue: the friend's imported
    song gets an own:<pid> navidrome mapping; the house song is not read."""
    import api.database_admin as da
    from tests.support.catalogue_seed import seed_library_track
    monkeypatch.setattr(da, 'get_database', lambda: db)
    monkeypatch.setattr(da, 'media_server_engine', _engine(shared_client))
    monkeypatch.setattr(da, '_db_update_phase_callback', lambda *a: None)
    monkeypatch.setattr(da, '_db_update_artist_callback', lambda *a: None)
    monkeypatch.setattr('core.database_update_worker.get_database', lambda *a, **k: db, raising=False)
    path = os.path.join(friend.root, 'Friend Band', 'Friend Album', '01 - Friend Song.flac')
    os.makedirs(os.path.dirname(path))
    with open(path, 'wb') as f:
        f.write(b'audio')
    server.songs['s-friend'] = ('Friend Song', 'al-friend', path)
    # what the admin's save does (library_config_changed): the friend's
    # folder becomes a library, so files under it are the friend's
    from core.library2 import library_roots
    library_roots.sync_library_roots(db)
    with db._get_connection() as conn:
        seed_library_track(conn, artist='Friend Band', album='Friend Album', title='Friend Song',
                           file_path=path, server_source='soulsync',
                           artist_server_id='x-ar', album_server_id='x-al', track_server_id='x-tr')
        conn.commit()

    da._run_own_library_scans('navidrome', deep=True)

    with db._get_connection() as conn:
        maps = {tuple(r) for r in conn.execute(
            "SELECT entity_type, server_library_id, server_id FROM lib2_media_server_mappings "
            "WHERE server_source='navidrome'")}
    assert ('track', f'own:{friend.id}', 's-friend') in maps
    assert not any(lib == '' for _e, lib, _s in maps)
    assert not any(sid.startswith(('s-house', 'al-house', 'ar-house')) for _e, _l, sid in maps)
    # the scan's listings went out as the friend on library 2 (filtered on
    # the folder: other code in the process may use the shared client)
    listings = [(u, p) for e, u, p in server.calls if e == 'getArtists' and p.get('musicFolderId') == '2']
    assert listings and all(u == 'friend' for u, _p in listings)
    assert not any(e == 'getArtists' and u == 'friend' and p.get('musicFolderId') != '2'
                   for e, u, p in server.calls)


def test_no_pick_or_the_shared_folder_skips_the_scan(shared_client, server, db, friend, monkeypatch):
    import api.database_admin as da
    monkeypatch.setattr(da, 'get_database', lambda: db)
    monkeypatch.setattr(da, 'media_server_engine', _engine(shared_client))
    db.set_profile_server_library(friend.id, 'navidrome', library_id='1')
    assert da._own_library_scan_clients('navidrome') == []
    db.set_profile_server_library(friend.id, 'navidrome', library_id=None)
    assert da._own_library_scan_clients('navidrome') == []


def test_without_a_login_the_app_account_scans_their_folder(shared_client, server, db, friend, monkeypatch):
    import api.database_admin as da
    monkeypatch.setattr(da, 'get_database', lambda: db)
    monkeypatch.setattr(da, 'media_server_engine', _engine(shared_client))
    db.set_profile_navidrome_login(friend.id, None, None)
    [(_prof, view)] = da._own_library_scan_clients('navidrome')
    assert view.acting_as is None and view.username == 'admin'
    server.calls.clear()
    assert [a.title for a in view.get_all_artists()] == ['Friend Band']
    assert server.calls[-1][1] == 'admin' and server.calls[-1][2]['musicFolderId'] == '2'


def test_the_post_scan_repair_reads_the_whole_server_as_the_app_account(shared_client, server, db, friend, monkeypatch):
    """the identity repair merges ids it no longer sees; read as the friend
    it would see one library and call every house song gone."""
    import core.library.navidrome_identity as ident
    view = shared_client.as_user('friend', 'fpw', music_folder_id='2')
    monkeypatch.setattr(ident, 'has_same_path_duplicates', lambda _db: True)
    seen = {}
    monkeypatch.setattr(ident, 'repair_rekeyed_tracks', lambda _db, songs: seen.update(songs) or 0)
    assert view.post_scan(db) is True
    assert set(seen) == {'s-house', 's-friend'}
    assert {u for e, u, _p in server.calls if e == 'search3'} == {'admin'}


# ── downloads land in their folder ──────────────────────────────────────────

def test_their_downloads_land_in_their_folder(shared_client, db, friend, tmp_path, monkeypatch):
    import core.imports.paths as paths
    shared = tmp_path / 'Transfer'
    shared.mkdir()
    cfg = SimpleNamespace(get=lambda k, d=None: str(shared) if k == 'soulseek.transfer_path' else d,
                          get_active_media_server=lambda: 'navidrome')
    monkeypatch.setattr(paths, '_get_config_manager', lambda: cfg)
    assert scope_mod.library_scope_for_profile(friend.id) == friend.id
    assert paths.transfer_root_for_context({'profile_id': friend.id}) == paths.config_root_path(friend.root)
    # everyone else still lands in the shared folder
    plain = db.create_profile(name='Plain')
    assert paths.transfer_root_for_context({'profile_id': plain}) == paths.config_root_path(str(shared))
    assert paths.transfer_root_for_context({}) == paths.config_root_path(str(shared))


def test_on_an_old_navidrome_their_downloads_fall_back_to_shared(server, db, navidrome_active, friend, tmp_path, monkeypatch):
    import core.imports.paths as paths
    server.version = '0.55.0'
    NavidromeClient().ensure_connection()
    shared = tmp_path / 'Transfer'
    shared.mkdir()
    cfg = SimpleNamespace(get=lambda k, d=None: str(shared) if k == 'soulseek.transfer_path' else d,
                          get_active_media_server=lambda: 'navidrome')
    monkeypatch.setattr(paths, '_get_config_manager', lambda: cfg)
    paths.reset_own_library_fallback_notifications()
    assert paths.transfer_root_for_context({'profile_id': friend.id}) == paths.config_root_path(str(shared))


# ── playlists go to their navidrome user, in their library ─────────────────

def test_their_playlist_client_is_their_user_in_their_library(shared_client, db, friend):
    from services.sync_service import client_for_profile, navidrome_client_for_profile
    view = navidrome_client_for_profile(friend.id, shared_client)
    assert view.acting_as == 'friend' and view.music_folder_id == '2'
    # the generic facade (server playlists page, sync names) agrees
    generic = client_for_profile(friend.id, shared_client, 'navidrome')
    assert generic.acting_as == 'friend' and generic.music_folder_id == '2'
    # a shared profile with a login keeps today's view: the shared folder
    plain = db.create_profile(name='Plain')
    db.set_profile_navidrome_login(plain, 'friend', 'fpw')
    assert navidrome_client_for_profile(plain, shared_client).music_folder_id == '1'


def test_their_playlist_is_written_as_them_with_their_songs(shared_client, server, db, friend, monkeypatch):
    import core.library.navidrome_identity as ident
    monkeypatch.setattr(ident, 'resolve_tracks',
                        lambda tracks, songs, _db, skipped=None, ambiguous=None: list(tracks))
    from services.sync_service import navidrome_client_for_profile
    view = navidrome_client_for_profile(friend.id, shared_client)
    found = view.search_tracks('Friend Song', 'Friend Band')
    assert [t.id for t in found] == ['s-friend']
    assert server.calls[-1][1] == 'friend' and server.calls[-1][2]['musicFolderId'] == '2'
    assert view.create_playlist('Mine', [SimpleNamespace(ratingKey='s-friend', id='s-friend')])
    [(pid, pl)] = server.playlists.items()
    assert pl == {'name': 'Mine', 'owner': 'friend', 'songs': ['s-friend']}
    # the app account's playlists are not theirs
    assert shared_client.get_playlist_by_name('Mine') is None


def test_a_library_view_without_a_login_acts_as_nobody(shared_client):
    view = shared_client.as_user(None, None, music_folder_id='2')
    assert view.acting_as is None
    assert view.username == 'admin' and view.music_folder_id == '2'


# ── the profile settings endpoints ─────────────────────────────────────────

@pytest.fixture()
def api(db, shared_client, monkeypatch, tmp_path):
    from flask import Flask
    import api.user_profiles as up
    shared = tmp_path / 'Transfer'
    shared.mkdir()
    monkeypatch.setattr(up, 'config_manager', SimpleNamespace(
        get=lambda k, d=None: str(shared) if k == 'soulseek.transfer_path' else d,
        get_active_media_server=lambda: 'navidrome'))
    monkeypatch.setattr(up, 'get_database', lambda: db)
    monkeypatch.setattr(up, '_media_server_engine', lambda: _engine(shared_client))
    state = SimpleNamespace(pid=1)
    monkeypatch.setattr(up, 'get_current_profile_id', lambda: state.pid)
    app = Flask(__name__)

    def call(fn, *args, json=None, method='GET'):
        with app.test_request_context(json=json, method=method):
            resp = fn(*args)
        if isinstance(resp, tuple):
            return resp[1], resp[0].get_json()
        return resp.status_code, resp.get_json()
    return SimpleNamespace(up=up, call=call, state=state)


def test_the_library_list_is_what_their_login_sees(api, friend, server):
    api.state.pid = friend.id
    status, body = api.call(api.up.list_profile_navidrome_libraries)
    assert status == 200 and body['own_library'] and body['supported']
    assert body['folders'] == [{'id': '2', 'name': 'Friend', 'shared': False}]
    assert body['selected'] == '2'
    assert server.calls[-1][:2] == ('getMusicFolders', 'friend')


def test_a_shared_profile_gets_no_picker(api, db):
    api.state.pid = db.create_profile(name='Plain')
    status, body = api.call(api.up.list_profile_navidrome_libraries)
    assert status == 200 and body['own_library'] is False and body['folders'] == []


def test_the_pick_is_checked_against_their_login(api, friend, db):
    api.state.pid = friend.id
    save = api.up.save_profile_server_library
    status, body = api.call(save, json={'server_type': 'navidrome', 'library_id': '1'}, method='POST')
    assert status == 400 and 'no such library' in body['error']
    status, body = api.call(save, json={'server_type': 'navidrome', 'library_id': '2'}, method='POST')
    assert status == 200 and body['success']
    assert db.get_profile_server_library(friend.id)['navidrome_library_id'] == '2'


def test_the_shared_library_cannot_be_their_own(api, friend, db, server):
    server.users['friend'] = ('fpw', [1, 2])
    api.state.pid = friend.id
    status, body = api.call(api.up.save_profile_server_library,
                            json={'server_type': 'navidrome', 'library_id': '1'}, method='POST')
    assert status == 400 and 'shared library' in body['error']


def test_the_picker_says_why_on_an_old_navidrome(api, friend, server):
    server.version = '0.56.0'
    NavidromeClient().ensure_connection()
    api.state.pid = friend.id
    status, body = api.call(api.up.list_profile_navidrome_libraries)
    assert status == 200 and body['supported'] is False and body['folders'] == []
    assert '0.58' in body['message']


def test_the_admin_can_turn_on_an_own_library_on_navidrome(api, db, tmp_path):
    root = tmp_path / 'kim'
    root.mkdir()
    kim = db.create_profile(name='Kim')
    status, body = api.call(api.up.update_profile, kim, json={'library_mode': 'own', 'library_root': str(root)},
                            method='PUT')
    assert status == 200 and body['success'], body
    assert db.get_profile_library(kim)['mode'] == 'own'
    _s, listing = api.call(api.up.list_profiles)
    assert listing['own_library_supported'] is True and listing['own_library_unsupported_reason'] is None


def test_turning_it_on_needs_the_shared_folder_picked(api, db, tmp_path):
    db.set_preference('navidrome_music_folder_id', '')
    root = tmp_path / 'kim'
    root.mkdir()
    kim = db.create_profile(name='Kim')
    status, body = api.call(api.up.update_profile, kim, json={'library_mode': 'own', 'library_root': str(root)},
                            method='PUT')
    assert status == 400 and "shared library's music folder" in body['error']
    assert db.get_profile_library(kim)['mode'] == 'shared'


def test_on_an_old_navidrome_the_admin_is_told_why(api, db, server, tmp_path):
    server.version = '0.57.2'
    NavidromeClient().ensure_connection()
    root = tmp_path / 'kim'
    root.mkdir()
    kim = db.create_profile(name='Kim')
    status, body = api.call(api.up.update_profile, kim, json={'library_mode': 'own', 'library_root': str(root)},
                            method='PUT')
    assert status == 400 and '0.58' in body['error'] and '0.57.2' in body['error']
    _s, listing = api.call(api.up.list_profiles)
    assert listing['own_library_supported'] is False
    assert '0.58' in listing['own_library_unsupported_reason']
