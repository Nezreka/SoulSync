"""own library per profile (#1199): the scans, the wishlist cleanup, the
sync and the download analysis all act for the right library."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.library_scope as scope_mod
from core.database_update_worker import DatabaseUpdateWorker
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path, monkeypatch):
    d = MusicDatabase(str(tmp_path / 'lib.db'))
    monkeypatch.setattr('database.music_database.get_database', lambda *a, **k: d)
    monkeypatch.setattr('core.database_update_worker.get_database', lambda path=None: d)
    scope_mod.invalidate_library_scope_cache()
    yield d
    scope_mod.invalidate_library_scope_cache()


# ── a plex-shaped server with two libraries ────────────────────────────────

class _Track:
    def __init__(self, k, n):
        self.ratingKey, self.title, self.trackNumber, self.duration, self.parentIndex = k, f"Song {n}", n + 1, 100000, 1
        self.media = [SimpleNamespace(parts=[SimpleNamespace(file=f"/m/{k}.flac", size=1000)], bitrate=900)]


class _Album:
    def __init__(self, k, tracks):
        self.ratingKey, self.title, self.year, self.thumb, self.genres = k, f"Album {k}", 2020, None, []
        self._t = tracks

    def tracks(self):
        return [_Track(t, n) for n, t in enumerate(self._t)]


class _Artist:
    def __init__(self, k, albums):
        self.ratingKey, self.title, self.thumb, self.genres, self.summary = k, f"Artist {k}", None, [], ''
        self._a = albums

    def albums(self):
        return [_Album(a, t) for a, t in self._a.items()]


class _Client:
    def __init__(self, artists):
        self._artists, self.last_fetch_failed = artists, False

    def ensure_connection(self):
        return True

    def get_all_artists(self):
        return list(self._artists)


def _lib(prefix, tracks_per_album=2):
    return [_Artist(f"{prefix}-ar", {f"{prefix}-al": [f"{prefix}-t{i}" for i in range(tracks_per_album)]})]


def _rows(db):
    with db._get_connection() as conn:
        return sorted((r[0], r[1]) for r in conn.execute("SELECT id, owner_profile_id FROM tracks").fetchall())


def test_a_profiles_deep_scan_stamps_its_rows_and_leaves_the_shared_library_alone(db, tmp_path):
    kim = db.create_profile(name='kim')
    DatabaseUpdateWorker(_Client(_lib('shared')), str(tmp_path / 'lib.db'), server_type='plex', force_sequential=True).run_deep_scan()
    DatabaseUpdateWorker(_Client(_lib('kim')), str(tmp_path / 'lib.db'), server_type='plex', force_sequential=True,
                         owner_profile_id=kim).run_deep_scan()
    assert _rows(db) == [('kim-t0', kim), ('kim-t1', kim), ('shared-t0', None), ('shared-t1', None)]
    # kim's library loses a track; her deep scan removes hers and nobody else's
    w = DatabaseUpdateWorker(_Client([_Artist('kim-ar', {'kim-al': ['kim-t0']})]), str(tmp_path / 'lib.db'),
                             server_type='plex', force_sequential=True, owner_profile_id=kim)
    w.run_deep_scan()
    assert _rows(db) == [('kim-t0', kim), ('shared-t0', None), ('shared-t1', None)]
    # and the shared deep scan never counts kim's rows as stale
    DatabaseUpdateWorker(_Client(_lib('shared')), str(tmp_path / 'lib.db'), server_type='plex', force_sequential=True).run_deep_scan()
    assert _rows(db) == [('kim-t0', kim), ('shared-t0', None), ('shared-t1', None)]


def test_a_shared_full_refresh_does_not_wipe_a_profiles_library(db, tmp_path):
    kim = db.create_profile(name='kim')
    DatabaseUpdateWorker(_Client(_lib('kim')), str(tmp_path / 'lib.db'), server_type='plex', force_sequential=True,
                         owner_profile_id=kim).run_deep_scan()
    DatabaseUpdateWorker(_Client(_lib('shared')), str(tmp_path / 'lib.db'), full_refresh=True, server_type='plex',
                         force_sequential=True).run()
    assert _rows(db) == [('kim-t0', kim), ('kim-t1', kim), ('shared-t0', None), ('shared-t1', None)]


# ── the scan launcher builds one client per own-library profile ───────────

def test_own_library_scan_clients_for_jellyfin_and_plex(db, monkeypatch):
    import api.database_admin as da
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', '/music/kim')
    sam = db.create_profile(name='sam'); db.set_profile_library(sam, 'own', '/music/sam')     # no library picked
    db.set_profile_server_library(kim, 'jellyfin', library_id='lib-kim', user_id='kim-uid')
    db.set_profile_server_library(kim, 'plex', library_id='Kids Music')
    monkeypatch.setattr(da, 'get_database', lambda: db)

    class _Jelly:
        user_id = 'admin-uid'

        def as_user(self, user_id, library_id=None):
            return SimpleNamespace(kind='jellyfin-view', user_id=user_id, library_id=library_id)

    class _PlexView:
        def __init__(self, base, server, title):
            self.title, self.picked = title, None

        def set_music_library_by_name(self, name):
            self.picked = name
            return name == 'Kids Music'

    class _Plex:
        server = object()

        def ensure_connection(self):
            return True

        def as_home_user(self, token, title=''):
            return None
    import core.plex_client as pc
    monkeypatch.setattr(pc, 'PlexUserView', _PlexView)
    monkeypatch.setattr(da, 'media_server_engine', SimpleNamespace(client=lambda n: {'jellyfin': _Jelly(), 'plex': _Plex()}[n]))

    jf = da._own_library_scan_clients('jellyfin')
    assert [(p['name'], c.user_id, c.library_id) for p, c in jf] == [('kim', 'kim-uid', 'lib-kim')]   # sam skipped: no pick
    px = da._own_library_scan_clients('plex')
    assert [(p['name'], c.picked) for p, c in px] == [('kim', 'Kids Music')]
    assert da._own_library_scan_clients('navidrome') == []


def test_own_library_scans_run_as_the_shared_scans_final_phase(db, monkeypatch, tmp_path):
    import api.database_admin as da
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', '/music/kim')
    monkeypatch.setattr(da, 'get_database', lambda: db)
    monkeypatch.setattr(da, '_own_library_scan_clients', lambda server_type: [({'id': kim, 'name': 'kim'}, _Client(_lib('kim')))])
    phases = []
    monkeypatch.setattr(da, '_db_update_phase_callback', lambda p: phases.append(p))
    monkeypatch.setattr(da, '_db_update_artist_callback', lambda *a: None)
    monkeypatch.setattr(da, '_reconcile_after_scan', lambda w: phases.append('reconcile'))
    hook = da._post_scan_hook_with_own_libraries('plex', deep=True)
    hook(SimpleNamespace())
    assert phases[0] == "Scanning kim's library..." and phases[-1] == 'reconcile'
    assert _rows(db) == [('kim-t0', kim), ('kim-t1', kim)]


# ── wishlist cleanup asks through the wishlist owner's library ─────────────

def test_wishlist_cleanup_does_not_clear_an_own_library_profiles_entry_for_the_admins_track(db, monkeypatch, tmp_path):
    from core.wishlist import processing as wp
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', '/music/kim')
    DatabaseUpdateWorker(_Client(_lib('shared')), str(tmp_path / 'lib.db'), server_type='plex', force_sequential=True).run_deep_scan()
    # kim wants "Song 0 by Artist shared-ar", which only the ADMIN owns
    track = {'id': 'sp1', 'spotify_track_id': 'sp1', 'name': 'Song 0', 'artists': [{'name': 'Artist shared-ar'}], 'album': {}}
    # sanity: the admin's own check does see it, so a wrongly scoped cleanup would clear it
    monkeypatch.setattr('core.profile_context.get_current_profile_id', lambda: 1)
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'plex')
    assert db.check_track_exists('Song 0', 'Artist shared-ar', confidence_threshold=0.7, server_source='plex')[0] is not None
    removed = []

    class _WL:
        def get_wishlist_tracks_for_download(self, profile_id=1):
            return [track] if profile_id == kim else []

        def mark_track_download_result(self, tid, success, **kw):
            removed.append(tid)
            return True
    profiles = SimpleNamespace(get_all_profiles=lambda: [{'id': 1}, {'id': kim}])
    monkeypatch.setattr('core.library.manual_library_match.get_match_for_track', lambda *a, **k: None)
    monkeypatch.setattr('core.profile_context.get_current_profile_id', lambda: 1)    # the job runs as admin
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'plex')
    n = wp.remove_tracks_already_in_library(_WL(), profiles, db, 'plex')
    assert n == 0 and removed == [], "kim's wishlist entry was cleared because the admin owns the track"
    # once kim's own library has it, it clears
    DatabaseUpdateWorker(_Client([_Artist('kim-ar', {'kim-al': ['kim-t0']})]), str(tmp_path / 'lib.db'),
                         server_type='plex', force_sequential=True, owner_profile_id=kim).run_deep_scan()
    with db._get_connection() as conn:
        conn.execute("UPDATE artists SET name = 'Artist shared-ar', name_norm = NULL, name_key = NULL WHERE id = 'kim-ar'")
        conn.commit()
    db.ensure_norm_backfilled() if hasattr(db, 'ensure_norm_backfilled') else None
    scope_mod.invalidate_library_scope_cache()
    n = wp.remove_tracks_already_in_library(_WL(), profiles, db, 'plex')
    assert n == 1 and removed == ['sp1']


# ── the sync and the download analysis carry the scope ─────────────────────

def test_the_sync_runs_inside_the_profiles_library_scope(db, monkeypatch):
    import asyncio
    from services import sync_service as ss
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', '/music/kim')
    seen = {}
    svc = ss.PlaylistSyncService.__new__(ss.PlaylistSyncService)

    async def fake_inner(self, playlist, download_missing, profile_id, sync_mode):
        seen['scope'] = scope_mod.current_library_scope()
        return 'done'
    monkeypatch.setattr(ss.PlaylistSyncService, '_sync_playlist', fake_inner)
    monkeypatch.setattr('core.profile_context.get_current_profile_id', lambda: 1)
    assert asyncio.run(svc.sync_playlist(SimpleNamespace(name='p', tracks=[]), profile_id=kim)) == 'done'
    assert seen['scope'] == kim
    assert scope_mod.current_library_scope() == 'shared'              # reset after: the admin's own scope


def test_the_download_analysis_runs_inside_the_batch_owners_scope(db, monkeypatch):
    from core.downloads import master
    from core.runtime_state import download_batches
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', '/music/kim')
    download_batches['b1'] = {'profile_id': kim}
    seen = {}
    monkeypatch.setattr(master, '_run_full_missing_tracks_process',
                        lambda *a, **k: seen.setdefault('scope', scope_mod.current_library_scope()))
    monkeypatch.setattr('core.profile_context.get_current_profile_id', lambda: 1)
    try:
        master.run_full_missing_tracks_process('b1', 'pl', [], SimpleNamespace())
    finally:
        download_batches.pop('b1', None)
    assert seen['scope'] == kim


# ── background jobs that act for a profile carry its scope ─────────────────
#
# these run on threads with no request: the scheduled watchlist scan walks
# every profile's artists in one list, the label phase wishlists for one
# profile, a playlist folder is rebuilt from batch completion, and the
# own-library scan runs on the shared scan's thread. each one answers
# "do i have this" through the right library, not the thread's default.

def _scan_state():
    return {'cancel_requested': False, 'tracks_found_this_scan': 0, 'tracks_added_this_scan': 0,
            'recent_wishlist_additions': [], 'scan_track_events': [], 'results': [], 'summary': {}}


def test_the_watchlist_scan_asks_each_artists_owner_library(db, monkeypatch):
    from datetime import datetime
    from core.watchlist_scanner import WatchlistScanner
    from database.music_database import WatchlistArtist
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', '/music/kim')
    sam = db.create_profile(name='sam')
    monkeypatch.setattr('core.profile_context.get_current_profile_id', lambda: 1)    # the scan thread runs as admin
    scanner = WatchlistScanner.__new__(WatchlistScanner)
    scanner._database = db
    monkeypatch.setattr(scanner, '_watchlist_source_priority', lambda: [])
    monkeypatch.setattr(scanner, '_get_lookback_period_setting', lambda: '30')
    seen = []

    def discography(artist, since):
        # the "is this missing" checks run right here, inside the artist's scope
        seen.append((artist.artist_name, scope_mod.current_library_scope()))
        return None
    monkeypatch.setattr(scanner, 'get_artist_discography_for_watchlist', discography)

    def artist(n, pid):
        return WatchlistArtist(id=n, spotify_artist_id=f"sp{n}", artist_name=f"A{n}", date_added=datetime.now(), profile_id=pid)
    results = scanner.scan_watchlist_artists([artist(1, kim), artist(2, sam), artist(3, 1)], profile_id=1,
                                             scan_state=_scan_state(), apply_global_overrides=False)
    assert seen == [('A1', kim), ('A2', 'shared'), ('A3', 'shared')]
    assert len(results) == 3 and not any(r.success for r in results)
    assert scope_mod.current_library_scope() == 'shared'         # nothing leaks past the loop


def test_the_label_phase_asks_the_wishlisting_profiles_library(db, monkeypatch):
    from core.automation.handlers import scan_watchlist_labels as labels
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', '/music/kim')
    monkeypatch.setattr('core.profile_context.get_current_profile_id', lambda: 1)
    seen = {}
    monkeypatch.setattr(labels, 'build_default_seams', lambda **kw: {})
    monkeypatch.setattr(labels, 'run_label_watchlist_scan',
                        lambda **kw: seen.setdefault('scope', scope_mod.current_library_scope()) and 'ran')
    fake_db = SimpleNamespace(get_watchlist_labels=lambda: [{'id': 1, 'name': 'Warp'}])
    labels.run_label_scan_phase(_scan_state(), database=fake_db, get_deezer=None, profile_id=kim)
    assert seen['scope'] == kim
    assert scope_mod.current_library_scope() == 'shared'


def test_a_playlist_folder_is_rebuilt_from_its_owners_library(db, monkeypatch, tmp_path):
    from core.playlists import materialize_service as ms
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', '/music/kim')
    monkeypatch.setattr('core.profile_context.get_current_profile_id', lambda: 1)    # a batch completion thread
    seen = []
    fake_db = SimpleNamespace(
        get_mirrored_playlist_tracks=lambda pid: [{'track_name': 'Song 0', 'artist_name': 'Artist kim'}],
        check_track_exists=lambda *a, **k: (seen.append(scope_mod.current_library_scope()), (None, 0.0))[1])
    cfg = SimpleNamespace(get=lambda k, d=None: str(tmp_path / 'Playlists') if k == 'playlists.materialize_path' else d)
    monkeypatch.setattr(ms, 'rebuild_playlist_folder', lambda *a, **k: SimpleNamespace(), raising=False)
    try:
        ms._rebuild_one_from_db(fake_db, cfg, {'id': 9, 'name': 'Mix', 'profile_id': kim})
    except Exception:
        pass                                    # the folder build past the matching is not the point here
    assert seen == [kim]
    assert scope_mod.current_library_scope() == 'shared'


def test_the_own_library_scan_runs_inside_the_profiles_scope(db, monkeypatch):
    import api.database_admin as da
    kim = db.create_profile(name='kim'); db.set_profile_library(kim, 'own', '/music/kim')
    monkeypatch.setattr('core.profile_context.get_current_profile_id', lambda: 1)
    monkeypatch.setattr(da, '_own_library_scan_clients', lambda server_type: [({'id': kim, 'name': 'kim'}, object())])
    monkeypatch.setattr(da, '_db_update_phase_callback', lambda p: None)
    monkeypatch.setattr(da, '_db_update_artist_callback', lambda *a: None)
    seen = {}

    class _Worker:
        def __init__(self, **kw):
            seen['owner'] = kw['owner_profile_id']
            self.processed_artists = self.processed_tracks = 0

        def connect_callback(self, *a):
            pass

        def run(self):
            seen['scope'] = scope_mod.current_library_scope()
    monkeypatch.setattr(da, 'DatabaseUpdateWorker', _Worker)
    da._run_own_library_scans('plex', deep=False)
    assert seen == {'owner': kim, 'scope': kim}
    assert scope_mod.current_library_scope() == 'shared'
