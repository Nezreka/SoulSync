"""a library of your own per profile (#1199, Noodlez1232).

a profile the admin switches to "own library" gets its own output folder
and syncs against its own library on the server. rows the scan of that
library writes carry the profile as owner (owner_profile_id); every row
that existed before carries none and is the shared library. the shared
scan and every profile on the shared library behave exactly as before;
an own-library profile downloads into its folder and asks "do i have
this" of its own rows; the admin is a user of the shared library like
anyone else, so a copy in someone's own library is not the admin's.

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

def test_scope_is_shared_for_the_admin_and_plain_profiles_and_the_id_for_own(db):
    sam = db.create_profile(name='sam')
    kim = db.create_profile(name='kim')
    db.set_profile_library(kim, 'own', '/music/kim')
    assert library_scope_for_profile(1) == 'shared'
    assert library_scope_for_profile(None) == 'shared'
    assert library_scope_for_profile(sam) == 'shared'
    assert library_scope_for_profile(kim) == kim
    second_admin = db.create_profile(name='boss', is_admin=True)
    assert library_scope_for_profile(second_admin) == 'shared'


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
    # the unary + keeps the owner column out of the planner's hands: the
    # scope is a filter on the rows a query's real predicates found, never
    # the index it walks (a plain IS NULL had sqlite walking every row of
    # the library through the owner index, 2ms searches became 18s)
    assert MusicDatabase._owner_scope_sql(None) == ("1=1", [])
    assert MusicDatabase._owner_scope_sql('shared', 't.owner_profile_id') == ("+t.owner_profile_id IS NULL", [])
    assert MusicDatabase._owner_scope_sql(7) == ("+owner_profile_id = ?", [7])


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


def test_the_admin_sees_the_shared_library_and_not_a_profiles_own(db, monkeypatch):
    """kim's own library is hers: it does not show up on the admin's library
    page, and (below) the admin is not told they own her copy"""
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', '/music/kim')
    _seed(db, None, 'shared'); _seed(db, kim, 'kim')
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'plex')
    _as_profile(monkeypatch, 1)
    r = _readers(db)
    assert sorted(r['tracks']) == ['shared-t0', 'shared-t1', 'shared-t2']
    assert r['albums'] == ['shared-al'] and r['artists'] == ['shared-ar'] and r['recent'] == ['shared-al']
    assert r['total'] == 3
    # an explicit "everything" scope is still there for a job that needs it
    token = set_library_scope(None)
    try:
        assert sorted(t.id for t in db.search_tracks(title='Song', artist='')) == \
            ['kim-t0', 'kim-t1', 'kim-t2', 'shared-t0', 'shared-t1', 'shared-t2']
    finally:
        reset_library_scope(token)


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
    # and kim's copy is not the admin's: the admin downloading it gets a copy
    # of their own (two folders, two files, by design)
    _as_profile(monkeypatch, 1)
    assert db.check_track_exists('Song 0', 'Artist kimcopy', confidence_threshold=0.7)[0] is None, \
        "the admin was told they own a track that only exists in kim's library"


def test_candidate_fetchers_are_scoped(db, monkeypatch):
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', '/music/kim')
    _seed(db, None, 'shared'); _seed(db, kim, 'kim')
    _as_profile(monkeypatch, kim)
    assert [t.id for t in db.get_candidate_tracks_for_albums(['shared-al', 'kim-al'])] == ['kim-t0', 'kim-t1', 'kim-t2']
    _as_profile(monkeypatch, 1)
    assert [t.id for t in db.get_candidate_tracks_for_albums(['shared-al', 'kim-al'])] == ['shared-t0', 'shared-t1', 'shared-t2']


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


# ── the owner index and the planner ─────────────────────────────────────────
#
# on boulder's live db (308k tracks) the first cut's bare owner index made
# sqlite walk the whole library through it for every "owner IS NULL" search:
# 2ms became 18s for every profile on the shared library. the index is
# (server_source, owner_profile_id, id) now, which the scan's per-library
# listings read covering, and the scope predicate carries a unary + so the
# planner can never take it as the index to walk.

def _owner_indexes(db):
    with db._get_connection() as conn:
        return sorted(r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE '%owner%'").fetchall())


def test_the_owner_index_is_per_server_and_a_bare_one_from_the_first_cut_is_dropped(tmp_path):
    path = str(tmp_path / 'lib.db')
    db = MusicDatabase(path)
    assert _owner_indexes(db) == ['idx_albums_source_owner', 'idx_artists_source_owner', 'idx_tracks_source_owner']
    # an install that ran the first cut has the bare index; the next start drops it
    with db._get_connection() as conn:
        for t in ('tracks', 'albums', 'artists'):
            conn.execute(f"DROP INDEX idx_{t}_source_owner")
            conn.execute(f"CREATE INDEX idx_{t}_owner ON {t} (owner_profile_id)")
        conn.commit()
    assert _owner_indexes(db) == ['idx_albums_owner', 'idx_artists_owner', 'idx_tracks_owner']
    # migrations run once per path per process: the next start is a new process
    import database.music_database as mdb
    mdb._database_initialized_paths.discard(str(db.database_path))
    mdb._database_initialized_paths.discard(path)
    db = MusicDatabase(path)
    assert _owner_indexes(db) == ['idx_albums_source_owner', 'idx_artists_source_owner', 'idx_tracks_source_owner']


def test_the_scope_is_never_the_index_a_query_walks(db, monkeypatch):
    """every scoped read, under both scopes: the plan never constrains on
    owner_profile_id (the + keeps it a filter on the rows the real
    predicates found), and the scan's per-library listings do use the
    per-server index"""
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', '/music/kim')
    _seed(db, None, 'shared'); _seed(db, kim, 'kim')
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'plex')
    statements = []
    real_connect = db._get_connection

    def traced():
        conn = real_connect()
        conn.set_trace_callback(lambda sql: statements.append(sql))
        return conn
    monkeypatch.setattr(db, '_get_connection', traced)
    for pid in (1, kim):
        _as_profile(monkeypatch, pid)
        _readers(db)
        db.check_track_exists('Song 0', 'Artist shared', confidence_threshold=0.7, server_source='plex')
        db.get_candidate_albums_for_artist('Artist shared', server_source='plex')
        db.get_candidate_tracks_for_albums(['shared-al'])
        db.api_list_albums(search='Album')
    scoped = [s for s in statements if s.lstrip().upper().startswith('SELECT') and 'owner_profile_id' in s]
    assert len(scoped) >= 12, "the scoped reads were not exercised"
    monkeypatch.setattr(db, '_get_connection', real_connect)
    with db._get_connection() as conn:
        for sql in scoped:
            plan = [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall()]
            assert not any('owner_profile_id' in line for line in plan), (sql, plan)
        # the listings a scan makes are "this server, this owner", covering
        plan = [r[3] for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM tracks WHERE server_source = ? AND owner_profile_id IS ?", ('plex', None))]
        assert any('COVERING INDEX idx_tracks_source_owner' in line for line in plan), plan
