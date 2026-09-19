"""a library of your own per profile (#1199, Noodlez1232).

a profile the admin switches to "own library" gets its own output folder
and syncs against its own library on the server. rows the scan of that
library writes carry the profile as owner (owner_profile_id); every row
that existed before carries none and is the shared library. the shared
scan and every profile on the shared library behave exactly as before;
an own-library profile downloads into its folder and asks "do i have
this" of its own rows; the admin sees everything.

hermetic: a real MusicDatabase on a temp file, the profile context set
directly, the media server faked at the plexapi-object seam.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.library_scope as scope_mod
from core.library_scope import (
    current_library_scope, library_scope_for_profile, reset_library_scope, set_library_scope,
)
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path, monkeypatch):
    d = MusicDatabase(str(tmp_path / 'lib.db'))
    monkeypatch.setattr('database.music_database.get_database', lambda *a, **k: d)
    scope_mod.invalidate_library_scope_cache()
    yield d
    scope_mod.invalidate_library_scope_cache()


def _as_profile(monkeypatch, pid):
    monkeypatch.setattr('core.profile_context.get_current_profile_id', lambda: pid)
    scope_mod.invalidate_library_scope_cache()


def _seed(db, owner, prefix, n=3):
    """an artist with one album of n tracks, owned by `owner` (None = shared)"""
    ar, al = f"{prefix}-ar", f"{prefix}-al"
    artist = SimpleNamespace(ratingKey=ar, title=f"Artist {prefix}", thumb=None, genres=[], summary='')
    assert db.insert_or_update_media_artist(artist, server_source='plex', owner_profile_id=owner)
    album = SimpleNamespace(ratingKey=al, title=f"Album {prefix}", year=2020, thumb=None, genres=[])
    assert db.insert_or_update_media_album(album, ar, server_source='plex', owner_profile_id=owner)
    for i in range(n):
        t = SimpleNamespace(ratingKey=f"{prefix}-t{i}", title=f"Song {i}", trackNumber=i + 1, duration=100000,
                            parentIndex=1)
        t.media = [SimpleNamespace(parts=[SimpleNamespace(file=f"/m/{prefix}/{i}.flac", size=1000)], bitrate=900)]
        assert db.insert_or_update_media_track(t, al, ar, server_source='plex', owner_profile_id=owner)
    return ar, al


def _owners(db, table='tracks'):
    with db._get_connection() as conn:
        return sorted((r[0], r[1]) for r in conn.execute(f"SELECT id, owner_profile_id FROM {table}").fetchall())


# ── profiles ────────────────────────────────────────────────────────────────

def test_profile_library_defaults_to_shared_and_can_be_switched(db):
    pid = db.create_profile(name='sam')
    assert db.get_profile_library(pid) == {'mode': 'shared', 'root': None}
    assert db.get_profile(pid)['library_mode'] == 'shared'
    assert db.set_profile_library(pid, 'own', '/music/sam')
    assert db.get_profile_library(pid) == {'mode': 'own', 'root': '/music/sam'}
    assert db.get_own_library_profiles() == [{'id': pid, 'name': 'sam', 'root': '/music/sam'}]
    assert db.set_profile_library(pid, 'shared', None)
    assert db.get_profile_library(pid)['mode'] == 'shared' and db.get_own_library_profiles() == []


def test_an_own_library_needs_a_folder_and_the_admin_is_always_shared(db):
    pid = db.create_profile(name='sam')
    assert db.set_profile_library(pid, 'own', '') is False
    assert db.get_profile_library(pid)['mode'] == 'shared'
    db.set_profile_library(1, 'own', '/x')
    assert db.get_profile_library(1) == {'mode': 'shared', 'root': None}


# ── scope ───────────────────────────────────────────────────────────────────

def test_scope_is_none_for_admin_shared_for_a_plain_profile_and_the_id_for_own(db):
    sam = db.create_profile(name='sam')
    kim = db.create_profile(name='kim')
    db.set_profile_library(kim, 'own', '/music/kim')
    assert library_scope_for_profile(1) is None
    assert library_scope_for_profile(None) is None
    assert library_scope_for_profile(sam) == 'shared'
    assert library_scope_for_profile(kim) == kim
    second_admin = db.create_profile(name='boss', is_admin=True)
    assert library_scope_for_profile(second_admin) is None


def test_scope_follows_the_current_profile_and_an_explicit_override_wins(db, monkeypatch):
    kim = db.create_profile(name='kim')
    db.set_profile_library(kim, 'own', '/music/kim')
    _as_profile(monkeypatch, kim)
    assert current_library_scope() == kim
    token = set_library_scope('shared')
    try:
        assert current_library_scope() == 'shared'
        inner = set_library_scope(None)
        assert current_library_scope() is None
        reset_library_scope(inner)
        assert current_library_scope() == 'shared'
    finally:
        reset_library_scope(token)
    assert current_library_scope() == kim


def test_scope_cache_is_dropped_when_the_mode_changes(db, monkeypatch):
    kim = db.create_profile(name='kim')
    _as_profile(monkeypatch, kim)
    assert current_library_scope() == 'shared'
    db.set_profile_library(kim, 'own', '/music/kim')
    assert current_library_scope() == 'shared'          # cached
    scope_mod.invalidate_library_scope_cache()
    assert current_library_scope() == kim


def test_scope_sql():
    assert MusicDatabase._owner_scope_sql(None) == ("1=1", [])
    assert MusicDatabase._owner_scope_sql('shared', 't.owner_profile_id') == ("t.owner_profile_id IS NULL", [])
    assert MusicDatabase._owner_scope_sql(7) == ("owner_profile_id = ?", [7])


# ── the scan writes owners, and never crosses them ─────────────────────────

def test_scans_stamp_rows_with_their_owner(db):
    _seed(db, None, 'shared')
    _seed(db, 2, 'kim')
    assert _owners(db) == [('kim-t0', 2), ('kim-t1', 2), ('kim-t2', 2), ('shared-t0', None), ('shared-t1', None), ('shared-t2', None)]
    assert _owners(db, 'albums') == [('kim-al', 2), ('shared-al', None)]
    assert _owners(db, 'artists') == [('kim-ar', 2), ('shared-ar', None)]


def test_a_profiles_copy_of_an_artist_does_not_rekey_the_shared_one(db):
    """the artist upsert treats a same-name row with a different id as the
    same artist whose server id changed, and REKEYS it (deleting the old
    row). two owners' copies of one artist are two rows on purpose."""
    artist = SimpleNamespace(ratingKey='ar-shared', title='Radiohead', thumb=None, genres=[], summary='')
    db.insert_or_update_media_artist(artist, server_source='plex', owner_profile_id=None)
    kims = SimpleNamespace(ratingKey='ar-kim', title='Radiohead', thumb=None, genres=[], summary='')
    db.insert_or_update_media_artist(kims, server_source='plex', owner_profile_id=2)
    assert _owners(db, 'artists') == [('ar-kim', 2), ('ar-shared', None)]
    # and the same name coming back for the SAME owner with a new id still rekeys
    moved = SimpleNamespace(ratingKey='ar-shared-2', title='Radiohead', thumb=None, genres=[], summary='')
    db.insert_or_update_media_artist(moved, server_source='plex', owner_profile_id=None)
    assert _owners(db, 'artists') == [('ar-kim', 2), ('ar-shared-2', None)]


def test_a_profiles_copy_of_an_album_does_not_rekey_the_shared_one(db):
    ar_s, al_s = _seed(db, None, 'shared', n=1)
    album = SimpleNamespace(ratingKey='al-kim', title='Album shared', year=2020, thumb=None, genres=[])
    db.insert_or_update_media_album(album, ar_s, server_source='plex', owner_profile_id=2)
    assert _owners(db, 'albums') == [('al-kim', 2), ('shared-al', None)]


def test_duplicate_artist_merge_stays_within_an_owner(db):
    _seed(db, None, 'shared')
    _seed(db, 2, 'kim')
    with db._get_connection() as conn:
        conn.execute("UPDATE artists SET name = 'Same' ")
        conn.commit()
    db.merge_duplicate_artists()
    assert _owners(db, 'artists') == [('kim-ar', 2), ('shared-ar', None)]


def test_id_fetchers_and_the_full_refresh_wipe_are_per_owner(db):
    _seed(db, None, 'shared')
    _seed(db, 2, 'kim')
    assert db.get_all_track_ids_for_server('plex') == {'shared-t0', 'shared-t1', 'shared-t2'}
    assert db.get_all_track_ids_for_server('plex', owner_profile_id=2) == {'kim-t0', 'kim-t1', 'kim-t2'}
    assert db.get_all_artist_ids_for_server('plex') == {'shared-ar'}
    assert db.get_all_album_ids_for_server('plex', owner_profile_id=2) == {'kim-al'}
    assert db.get_statistics_for_server('plex', owner_profile_id=2)['tracks'] == 3
    assert db.get_statistics_for_server('plex')['tracks'] == 6        # no owner filter by default
    db.clear_server_data('plex')                                        # the shared full refresh
    assert _owners(db) == [('kim-t0', 2), ('kim-t1', 2), ('kim-t2', 2)]


# ── reads answer for the caller's library ──────────────────────────────────

def _readers(db):
    return {
        'tracks': [t.id for t in db.search_tracks(title='Song', artist='')],
        'albums': [a.id for a in db.search_albums(title='Album', artist='')],
        'artists': [a.id for a in db.search_artists('Artist')],
        'grid': [a['name'] for a in db.get_library_artists()['artists']] if db.get_library_artists() else [],
        'recent': [a['id'] for a in db.api_get_recently_added('albums')],
        'total': db.get_statistics_for_server()['tracks'],
    }


def test_the_admin_sees_every_library(db, monkeypatch):
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', '/music/kim')
    _seed(db, None, 'shared'); _seed(db, kim, 'kim')
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'plex')
    _as_profile(monkeypatch, 1)
    r = _readers(db)
    assert sorted(r['tracks']) == ['kim-t0', 'kim-t1', 'kim-t2', 'shared-t0', 'shared-t1', 'shared-t2']
    assert sorted(r['albums']) == ['kim-al', 'shared-al'] and sorted(r['artists']) == ['kim-ar', 'shared-ar']
    assert r['total'] == 6


def test_a_shared_profile_sees_the_shared_library_only(db, monkeypatch):
    sam = db.create_profile(name='sam')
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', '/music/kim')
    _seed(db, None, 'shared'); _seed(db, kim, 'kim')
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'plex')
    _as_profile(monkeypatch, sam)
    r = _readers(db)
    assert sorted(r['tracks']) == ['shared-t0', 'shared-t1', 'shared-t2']
    assert r['albums'] == ['shared-al'] and r['artists'] == ['shared-ar'] and r['recent'] == ['shared-al']
    assert r['total'] == 3


def test_an_own_library_profile_sees_its_own_rows_only(db, monkeypatch):
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', '/music/kim')
    _seed(db, None, 'shared'); _seed(db, kim, 'kim')
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'plex')
    _as_profile(monkeypatch, kim)
    r = _readers(db)
    assert sorted(r['tracks']) == ['kim-t0', 'kim-t1', 'kim-t2']
    assert r['albums'] == ['kim-al'] and r['artists'] == ['kim-ar'] and r['recent'] == ['kim-al']
    assert r['total'] == 3


def test_do_i_have_this_answers_per_library(db, monkeypatch):
    """the check every sync, wishlist cleanup and artist page runs"""
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', '/music/kim')
    _seed(db, None, 'shared')                # the shared library has "Song 0" by Artist shared
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'plex')
    _as_profile(monkeypatch, 1)
    assert db.check_track_exists('Song 0', 'Artist shared', confidence_threshold=0.7)[0] is not None
    _as_profile(monkeypatch, kim)
    assert db.check_track_exists('Song 0', 'Artist shared', confidence_threshold=0.7)[0] is None, \
        "an own-library profile was told it owns the admin's track"
    _seed(db, kim, 'kimcopy')
    assert db.check_track_exists('Song 0', 'Artist kimcopy', confidence_threshold=0.7)[0] is not None


def test_candidate_fetchers_are_scoped(db, monkeypatch):
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', '/music/kim')
    _seed(db, None, 'shared'); _seed(db, kim, 'kim')
    _as_profile(monkeypatch, kim)
    assert [t.id for t in db.get_candidate_tracks_for_albums(['shared-al', 'kim-al'])] == ['kim-t0', 'kim-t1', 'kim-t2']
    _as_profile(monkeypatch, 1)
    assert len(db.get_candidate_tracks_for_albums(['shared-al', 'kim-al'])) == 6


# ── downloads land in the profile's folder ────────────────────────────────

def test_transfer_root_follows_the_batch_owner(db, monkeypatch, tmp_path):
    from core.imports import paths
    from core.runtime_state import download_batches
    monkeypatch.setattr(paths, '_get_config_manager', lambda: SimpleNamespace(get=lambda k, d=None: str(tmp_path / 'Transfer')))
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', str(tmp_path / 'kim'))
    sam = db.create_profile(name='sam')
    download_batches['b-kim'] = {'profile_id': kim}
    download_batches['b-sam'] = {'profile_id': sam}
    download_batches['b-admin'] = {'profile_id': 1}
    try:
        assert paths.transfer_root_for_context({'batch_id': 'b-kim'}) == str(tmp_path / 'kim')
        assert paths.transfer_root_for_context({'batch_id': 'b-sam'}) == str(tmp_path / 'Transfer')
        assert paths.transfer_root_for_context({'batch_id': 'b-admin'}) == str(tmp_path / 'Transfer')
        assert paths.transfer_root_for_context({}) == str(tmp_path / 'Transfer')
        assert paths.transfer_root_for_context({'profile_id': kim}) == str(tmp_path / 'kim')       # a page import
        assert paths.transfer_root_for_context({'batch_id': 'gone'}) == str(tmp_path / 'Transfer')
    finally:
        for b in ('b-kim', 'b-sam', 'b-admin'):
            download_batches.pop(b, None)


def test_the_final_path_builder_uses_the_profiles_root(db, monkeypatch, tmp_path):
    from core.imports import paths
    monkeypatch.setattr(paths, '_get_config_manager', lambda: SimpleNamespace(
        get=lambda k, d=None: str(tmp_path / 'Transfer') if k == 'soulseek.transfer_path' else d))
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', str(tmp_path / 'kim'))
    context = {'profile_id': kim, 'spotify_artist': {'name': 'Artist'}, 'spotify_album': {'name': 'Album'},
               'original_search_result': {'title': 'Song', 'artist': 'Artist', 'album': 'Album'}, 'is_album_download': True}
    album_info = {'album_name': 'Album', 'track_number': 1, 'clean_track_name': 'Song', 'disc_number': 1}
    dest, _ = paths.build_final_path_for_track(context, {'name': 'Artist'}, album_info, '.flac', create_dirs=False)
    assert str(dest).startswith(str(tmp_path / 'kim')), dest
    context.pop('profile_id')
    dest, _ = paths.build_final_path_for_track(context, {'name': 'Artist'}, album_info, '.flac', create_dirs=False)
    assert str(dest).startswith(str(tmp_path / 'Transfer')), dest
