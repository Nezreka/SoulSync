"""Regression coverage for own-library isolation across real database and service paths."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from core.library_scope import (invalidate_library_scope_cache, library_artist_id,
                                reset_library_scope, set_library_scope)
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path, monkeypatch):
    d = MusicDatabase(str(tmp_path / 'library.db'))
    monkeypatch.setattr('database.music_database.get_database', lambda *a, **k: d)
    monkeypatch.setattr('core.database_update_worker.get_database', lambda *a, **k: d)
    invalidate_library_scope_cache()
    yield d
    invalidate_library_scope_cache()


def seed(db, owner, prefix, server='plex', artist_id=None):
    aid = artist_id or prefix + '-ar'
    artist = NS(ratingKey=aid, title='Same Artist', genres=[], thumb=None, summary='')
    album = NS(ratingKey=prefix+'-al', title=prefix+' Album', year=2020, genres=[], thumb=None)
    track = NS(ratingKey=prefix+'-t', title='Same Song', trackNumber=1, parentIndex=1,
               duration=100000, media=[NS(parts=[NS(file='/server/'+prefix+'.flac', size=1000)], bitrate=900)])
    assert db.insert_or_update_media_artist(artist, server_source=server, owner_profile_id=owner)
    assert db.insert_or_update_media_album(album, aid, server_source=server, owner_profile_id=owner)
    assert db.insert_or_update_media_track(track, album.ratingKey, aid, server_source=server, owner_profile_id=owner)
    album.tracks = lambda: [track]
    artist.albums = lambda: [album]
    return artist


@pytest.mark.parametrize('own_first', [False, True])
def test_jellyfin_shared_artist_has_independent_parents_and_survives_shared_refresh(db, own_first):
    pid = db.create_profile(name='Own')
    entries = [(None, 'shared'), (pid, 'own')]
    for owner, prefix in reversed(entries) if own_first else entries:
        seed(db, owner, prefix, 'jellyfin', 'native-artist')
    token = set_library_scope(pid)
    try:
        artists = db.search_artists('Same Artist', server_source='jellyfin')
        assert [a.id for a in artists] == [library_artist_id('native-artist', 'jellyfin', pid)]
        assert [a['id'] for a in db.get_artist_full_detail(artists[0].id)['albums']] == ['own-al']
    finally:
        reset_library_scope(token)
    db.clear_server_data('jellyfin', owner_profile_id=None)
    assert db.get_all_track_ids_for_server('jellyfin', owner_profile_id=pid) == {'own-t'}
    assert db.get_all_album_ids_for_server('jellyfin', owner_profile_id=pid) == {'own-al'}


def test_legacy_jellyfin_parent_migration_preserves_metadata_and_children(db):
    owners = [db.create_profile(name=n) for n in ('One', 'Two')]
    seed(db, None, 'shared', 'jellyfin', 'native-artist')
    for pid in owners:
        seed(db, pid, str(pid), 'jellyfin', 'native-artist')
    with db._get_connection() as conn:
        # Reproduce the rows the original four commits wrote: every album
        # referenced the server-global artist even when its owner differed.
        for pid in owners:
            local = library_artist_id('native-artist', 'jellyfin', pid)
            for table in ('albums', 'tracks'):
                conn.execute(f'UPDATE {table} SET artist_id = ? WHERE owner_profile_id = ?', ('native-artist', pid))
            conn.execute('DELETE FROM artists WHERE id = ?', (local,))
        conn.execute("UPDATE artists SET spotify_artist_id = 'sp-artist' WHERE id = 'native-artist'")
        conn.commit()
    import database.music_database as module
    module._database_initialized_paths.discard(str(db.database_path))
    healed = MusicDatabase(str(db.database_path))
    with healed._get_connection() as conn:
        rows = conn.execute('SELECT id, spotify_artist_id FROM artists').fetchall()
        assert {r[0] for r in rows} == {'native-artist', *(library_artist_id('native-artist', 'jellyfin', p) for p in owners)}
        assert {r[1] for r in rows} == {'sp-artist'}
        healed._repair_own_jellyfin_artist_ids(conn.cursor())  # idempotent
        conn.commit()
    healed.clear_server_data('jellyfin')
    for pid in owners:
        assert healed.get_all_track_ids_for_server('jellyfin', owner_profile_id=pid) == {f'{pid}-t'}


@pytest.mark.parametrize('pid_scope', ['shared', 'own'])
def test_both_artist_detail_views_and_source_lookup_stay_in_library(db, pid_scope):
    from core.artist_source_lookup import find_library_artist_for_source
    pid = db.create_profile(name='Own')
    seed(db, None, 'shared'); seed(db, pid, 'own')
    with db._get_connection() as conn:
        conn.execute("UPDATE artists SET spotify_artist_id = 'source-artist'")
        conn.commit()
    prefix = pid_scope
    token = set_library_scope(pid if prefix == 'own' else 'shared')
    try:
        assert find_library_artist_for_source(db, 'spotify', 'source-artist') == prefix+'-ar'
        for artist_key in (prefix+'-ar', 'source-artist'):
            result = db.get_artist_full_detail(artist_key)
            assert result['success'] and [a['id'] for a in result['albums']] == [prefix+'-al']
        result = db.get_artist_discography(prefix+'-ar')
        assert result['artist']['track_count'] == 1
        releases = sum(result['owned_releases'].values(), [])
        assert [a['id'] for a in releases] == [prefix+'-al']
        other = 'shared' if prefix == 'own' else 'own'
        assert not db.get_artist_full_detail(other+'-ar')['success']
        assert not db.get_artist_discography(other+'-ar')['success']
    finally:
        reset_library_scope(token)


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('owns_copy', [False, True])
def test_real_sync_matcher_never_uses_shared_track(db, monkeypatch, cached, owns_copy):
    from services.sync_service import PlaylistSyncService
    pid = db.create_profile(name='Own')
    db.set_profile_library(pid, 'own', '/own')
    seed(db, None, 'shared', 'jellyfin')
    if owns_copy:
        seed(db, pid, 'own', 'jellyfin')
    if cached:
        db.save_sync_match_cache('source-id', 'Same Song', 'Same Artist', 'jellyfin', 'shared-t', 'Same Song', 1.0)
    class DatabaseFactory(MusicDatabase):
        def __new__(cls, *a, **k):
            return db
    monkeypatch.setattr('database.music_database.MusicDatabase', DatabaseFactory)
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'jellyfin')
    svc = PlaylistSyncService(spotify_client=MagicMock(), download_orchestrator=MagicMock(), media_server_engine=MagicMock())
    svc._get_active_media_client = lambda: (NS(is_connected=lambda: True), 'jellyfin')
    svc._cancelled = False
    token = set_library_scope(pid)
    try:
        match, confidence = asyncio.run(svc._find_track_in_media_server(
            NS(id='source-id', name='Same Song', artists=['Same Artist'], album=None), candidate_pool={}))
        assert (match.id if match else None) == ('own-t' if owns_copy else None)
    finally:
        reset_library_scope(token)


def test_playlist_folder_check_reads_explicit_batch_owner(db, monkeypatch, tmp_path):
    from core.downloads import playlist_folder as pf
    shared = tmp_path / 'shared'; shared.mkdir()
    own = tmp_path / 'own'; own.mkdir()
    pid = db.create_profile(name='Own'); db.set_profile_library(pid, 'own', str(own))
    monkeypatch.setattr(pf, '_get_config_manager', lambda: NS(get=lambda k, d=None: str(shared) if k == 'soulseek.transfer_path' else d))
    monkeypatch.setattr(pf, 'get_file_path_from_template', lambda *a: ('My Playlist', 'Same Artist - Same Song'))
    track = {'name': 'Same Song', 'artists': ['Same Artist']}
    shared_file = shared / 'My Playlist' / 'Same Artist - Same Song.flac'
    shared_file.parent.mkdir(); shared_file.write_bytes(b'shared')
    assert pf.track_exists_in_playlist_folder_from_track_data('My Playlist', track, profile_id=1)
    assert not pf.track_exists_in_playlist_folder_from_track_data('My Playlist', track, profile_id=pid)
    own_file = own / 'My Playlist' / shared_file.name
    own_file.parent.mkdir(); own_file.write_bytes(b'own')
    assert pf.track_exists_in_playlist_folder_from_track_data('My Playlist', track, profile_id=pid)


@pytest.mark.parametrize('own_file_exists', [False, True])
def test_materialized_playlist_only_resolves_inside_owners_mount(db, monkeypatch, tmp_path, own_file_exists):
    from core.playlists import materialize_service as ms
    own = tmp_path / 'own'; own.mkdir()
    shared = tmp_path / 'shared'; shared.mkdir()
    suffix = 'Same Artist/Same Album/Same Song.flac'
    own_file = own / suffix
    if own_file_exists:
        own_file.parent.mkdir(parents=True); own_file.write_bytes(b'own')
    shared_file = shared / suffix; shared_file.parent.mkdir(parents=True); shared_file.write_bytes(b'shared')
    pid = db.create_profile(name='Own'); db.set_profile_library(pid, 'own', str(own))
    seed(db, pid, 'own')
    with db._get_connection() as conn:
        conn.execute('UPDATE tracks SET file_path = ? WHERE id = ?', ('/media-server-only/kim/' + suffix, 'own-t'))
        conn.commit()
    monkeypatch.setattr(db, 'get_mirrored_playlist_tracks', lambda *a: [{'track_name': 'Same Song', 'artist_name': 'Same Artist'}])
    monkeypatch.setattr(ms, 'rebuild_playlist_folder', lambda root, name, paths, mode, **kw: paths)
    cfg = NS(get=lambda k, d=None: {'soulseek.transfer_path': str(shared), 'playlists.materialize_path': str(tmp_path/'Playlists'), 'library.music_paths': []}.get(k, d))
    _, paths = ms._rebuild_one_from_db(db, cfg, {'id': 1, 'profile_id': pid, 'name': 'My Playlist'})
    assert paths == ([str(own_file)] if own_file_exists else [])


@pytest.mark.parametrize('server', ['navidrome', 'soulsync', 'plex', 'jellyfin'])
def test_own_mode_save_requires_a_supported_server(db, monkeypatch, tmp_path, server):
    from flask import Flask
    import api.user_profiles as up
    pid = db.create_profile(name='Own'); root = tmp_path / 'own'; root.mkdir()
    monkeypatch.setattr(up, 'get_database', lambda: db)
    monkeypatch.setattr(up, 'get_current_profile_id', lambda: 1)
    monkeypatch.setattr(up, 'config_manager', NS(get=lambda k, d=None: d, get_active_media_server=lambda: server))
    with Flask(__name__).test_request_context(json={'library_mode': 'own', 'library_root': str(root)}):
        response = up.update_profile(pid)
        status = response[1] if isinstance(response, tuple) else response.status_code
        assert status == (200 if server in ('plex', 'jellyfin') else 400)
        assert db.get_profile_library(pid)['mode'] == ('own' if status == 200 else 'shared')


def test_jellyfin_client_uses_native_artist_ids_at_server_boundary(monkeypatch):
    from core.jellyfin_client import JellyfinClient
    client = JellyfinClient.__new__(JellyfinClient)
    client._artist_cache = {}; client._album_cache = {}
    client.user_id = 'user'; client.music_library_id = 'own-library'
    client.ensure_connection = lambda: True
    calls = []
    client._make_request = lambda url, *a: calls.append(url) or {'Id': 'native', 'Name': 'Artist'}
    client._fetch_all_items = lambda params: calls.append(params) or []
    local = library_artist_id('native', 'jellyfin', 2)
    assert client.get_artist_by_id(local).ratingKey == 'native'
    assert client.get_albums_for_artist_verified(local) == ([], True)
    assert calls[0] == '/Users/user/Items/native'
    assert calls[1]['ArtistIds'] == 'native' and calls[1]['ParentId'] == 'own-library'

class ScanClient:
    def __init__(self, artists):
        self.artists = artists
        self.last_fetch_failed = False
    def ensure_connection(self):
        return True
    def get_all_artists(self):
        self.last_fetch_failed = False
        return self.artists
    def get_all_artist_ids(self):
        self.last_fetch_failed = False
        return {a.ratingKey for a in self.artists}
    def get_all_album_ids(self):
        self.last_fetch_failed = False
        return {al.ratingKey for a in self.artists for al in a.albums()}
    def clear_cache(self):
        pass


def test_jellyfin_worker_keeps_local_ids_for_stale_detection_and_failed_listings(db):
    from core.database_update_worker import DatabaseUpdateWorker
    pid = db.create_profile(name='Own')
    seed(db, None, 'shared', 'jellyfin', 'native-artist')
    artist = seed(db, pid, 'own', 'jellyfin', 'native-artist')
    client = ScanClient([artist])
    worker = DatabaseUpdateWorker(client, server_type='jellyfin', force_sequential=True, owner_profile_id=pid)
    worker.run_deep_scan()
    assert db.get_all_track_ids_for_server('jellyfin', owner_profile_id=pid) == {'own-t'}
    # A transient failed artist listing must protect its local child IDs.
    def failed_albums():
        raise RuntimeError('server unavailable')
    artist.albums = failed_albums
    client.get_all_album_ids = lambda: {'own-al'}
    worker = DatabaseUpdateWorker(client, server_type='jellyfin', force_sequential=True, owner_profile_id=pid)
    worker.run_deep_scan()
    assert db.get_all_track_ids_for_server('jellyfin', owner_profile_id=pid) == {'own-t'}
    assert db.get_all_track_ids_for_server('jellyfin') == {'shared-t'}
    # Verified removal on a later scan removes only this library's parent.
    client.artists = []
    client.get_all_album_ids = lambda: set()
    client.get_all_artists()
    worker = DatabaseUpdateWorker(client, server_type='jellyfin', force_sequential=True, owner_profile_id=pid)
    worker.run_deep_scan()
    assert db.get_all_track_ids_for_server('jellyfin', owner_profile_id=pid) == set()
    assert db.get_all_track_ids_for_server('jellyfin') == {'shared-t'}


def test_strict_path_resolution_rejects_existing_other_root_and_traversal(tmp_path):
    from core.library.path_resolver import resolve_library_file_path
    own = tmp_path / 'own'; own.mkdir()
    other = tmp_path / 'other'; other.mkdir()
    foreign = other / 'song.flac'; foreign.write_bytes(b'foreign')
    assert resolve_library_file_path(str(foreign), library_root=str(own)) is None
    assert resolve_library_file_path('../other/song.flac', library_root=str(own)) is None
    local = own / 'song.flac'; local.write_bytes(b'own')
    assert resolve_library_file_path(str(local), library_root=str(own)) == str(local)

@pytest.mark.parametrize('server', ['navidrome', 'soulsync'])
def test_switching_to_unsupported_server_uses_shared_scope_and_destination(db, monkeypatch, tmp_path, server):
    from core.imports.paths import library_root_for_profile
    from core.library_scope import library_scope_for_profile
    pid = db.create_profile(name='Own'); db.set_profile_library(pid, 'own', str(tmp_path))
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'plex')
    assert library_scope_for_profile(pid) == pid
    assert library_root_for_profile(pid) == str(tmp_path)
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: server)
    assert library_scope_for_profile(pid) == 'shared'
    assert library_root_for_profile(pid) is None
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'jellyfin')
    assert library_scope_for_profile(pid) == pid


def test_old_manual_match_cannot_suppress_download_in_new_own_library(db):
    from core.library.manual_library_match import match_is_live
    pid = db.create_profile(name='Own')
    seed(db, None, 'shared')
    old_match = {'library_track_id': 'shared-t', 'library_file_path': '/server/shared.flac'}
    token = set_library_scope(pid)
    try:
        assert db.api_get_tracks_by_ids(['shared-t']) == []
        assert db.find_track_id_by_file_path('/server/shared.flac') is None
        assert not match_is_live(db, old_match)
        seed(db, pid, 'own')
        assert match_is_live(db, {'library_track_id': 'own-t'})
    finally:
        reset_library_scope(token)


def test_batch_materialization_respects_owner_root(db, tmp_path):
    from core.playlists.materialize_service import collect_batch_real_paths
    own = tmp_path / 'own'; own.mkdir()
    shared = tmp_path / 'shared'; shared.mkdir()
    (shared / 'song.flac').write_bytes(b'shared')
    pid = db.create_profile(name='Own'); db.set_profile_library(pid, 'own', str(own))
    cfg = NS(get=lambda k, d=None: str(shared) if k == 'soulseek.transfer_path' else d)
    batch = {'profile_id': pid, 'analysis_results': [{'found': True, 'matched_file_path': '/server/song.flac'}]}
    assert collect_batch_real_paths(batch, {}, config_manager=cfg) == []
    (own / 'song.flac').write_bytes(b'own')
    assert collect_batch_real_paths(batch, {}, config_manager=cfg) == [str(own/'song.flac')]


def test_overlapping_server_selections_cannot_reparent_another_owners_records(db):
    pid = db.create_profile(name='Own')
    shared = seed(db, None, 'shared', 'jellyfin', 'native-artist')
    own_artist = NS(ratingKey='native-artist', title='Same Artist', genres=[], thumb=None, summary='')
    assert db.insert_or_update_media_artist(own_artist, server_source='jellyfin', owner_profile_id=pid)
    shared_album = shared.albums()[0]
    assert not db.insert_or_update_media_album(shared_album, 'native-artist', server_source='jellyfin', owner_profile_id=pid)
    assert not db.insert_or_update_media_track(shared_album.tracks()[0], 'shared-al', 'native-artist', server_source='jellyfin', owner_profile_id=pid)
    db.set_profile_library(pid, 'shared', None)
    assert db.get_all_track_ids_for_server('jellyfin') == {'shared-t'}
