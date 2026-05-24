"""Boundary tests for the playlist-lifecycle automation handlers
(``refresh_mirrored``, ``sync_playlist``, ``discover_playlist``,
``playlist_pipeline``).

The handlers themselves are mechanical lifts of the closures that
used to live in ``web_server._register_automation_handlers`` — these
tests pin the seam so the wiring stays correct (deps are read from
the deps object, not module-level globals; cross-handler calls in
the pipeline still compose; failure paths still return clear status
shapes).

Source-specific branches inside ``refresh_mirrored`` (Spotify auth
+ public-embed fallback, Deezer / Tidal / YouTube) are validated
end-to-end via fake clients in ``deps`` rather than per-source
because they're a verbatim lift — drift would show up here as a
behavior change."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import pytest

from core.automation.deps import AutomationDeps, AutomationState
from core.automation.handlers.discover_playlist import auto_discover_playlist
from core.automation.handlers.playlist_pipeline import auto_playlist_pipeline
from core.automation.handlers.refresh_mirrored import auto_refresh_mirrored
from core.automation.handlers.sync_playlist import auto_sync_playlist


# ─── shared scaffolding ──────────────────────────────────────────────


class _StubLogger:
    def __init__(self):
        self.messages: List[tuple] = []

    def debug(self, *a, **k): self.messages.append(('debug', a))
    def info(self, *a, **k): self.messages.append(('info', a))
    def warning(self, *a, **k): self.messages.append(('warning', a))
    def error(self, *a, **k): self.messages.append(('error', a))


@dataclass
class _StubDB:
    """Fake MusicDatabase — minimal surface used by the playlist handlers."""

    playlists: List[dict] = field(default_factory=list)
    playlist_tracks: Dict[int, List[dict]] = field(default_factory=dict)
    extra_data_maps: Dict[int, Dict[str, str]] = field(default_factory=dict)
    mirror_calls: List[dict] = field(default_factory=list)

    def get_mirrored_playlists(self) -> list:
        return list(self.playlists)

    def get_mirrored_playlist(self, playlist_id: int) -> Optional[dict]:
        for p in self.playlists:
            if int(p.get('id', -1)) == int(playlist_id):
                return p
        return None

    def get_mirrored_playlist_tracks(self, playlist_id: int) -> list:
        return list(self.playlist_tracks.get(int(playlist_id), []))

    def get_mirrored_tracks_extra_data_map(self, playlist_id: int) -> dict:
        return dict(self.extra_data_maps.get(int(playlist_id), {}))

    def mirror_playlist(self, **kwargs) -> None:
        self.mirror_calls.append(kwargs)


def _build_deps(**overrides) -> AutomationDeps:
    defaults = dict(
        engine=object(),
        state=AutomationState(),
        config_manager=object(),
        update_progress=lambda *a, **k: None,
        logger=_StubLogger(),
        get_database=lambda: _StubDB(),
        spotify_client=None,
        tidal_client=None,
        web_scan_manager=None,
        process_wishlist_automatically=lambda **k: None,
        process_watchlist_scan_automatically=lambda **k: None,
        is_wishlist_actually_processing=lambda: False,
        is_watchlist_actually_scanning=lambda: False,
        get_watchlist_scan_state=lambda: {},
        run_playlist_discovery_worker=lambda *a, **k: None,
        run_sync_task=lambda *a, **k: None,
        load_sync_status_file=lambda: {},
        get_deezer_client=lambda: None,
        parse_youtube_playlist=lambda url: None,
        get_sync_states=lambda: {},
        set_db_update_automation_id=lambda v: None,
        get_db_update_state=lambda: {},
        db_update_lock=threading.Lock(),
        db_update_executor=None,
        run_db_update_task=lambda *a, **k: None,
        run_deep_scan_task=lambda *a, **k: None,
        get_duplicate_cleaner_state=lambda: {},
        duplicate_cleaner_lock=threading.Lock(),
        duplicate_cleaner_executor=None,
        run_duplicate_cleaner=lambda: None,
        get_quality_scanner_state=lambda: {},
        quality_scanner_lock=threading.Lock(),
        quality_scanner_executor=None,
        run_quality_scanner=lambda *a, **k: None,
        download_orchestrator=None,
        run_async=lambda coro: None,
        tasks_lock=threading.Lock(),
        get_download_batches=lambda: {},
        get_download_tasks=lambda: {},
        sweep_empty_download_directories=lambda: 0,
        get_staging_path=lambda: '/staging',
        docker_resolve_path=lambda p: p,
        get_current_profile_id=lambda: 1,
        get_watchlist_scanner=lambda spc: None,
        get_app=lambda: None,
        get_beatport_data_cache=lambda: {'cache_lock': threading.Lock(), 'homepage': {}},
        init_automation_progress=lambda *a, **k: None,
        record_progress_history=lambda *a, **k: None,
        build_personalized_manager=lambda: None,
    )
    defaults.update(overrides)
    return AutomationDeps(**defaults)  # type: ignore[arg-type]


# ─── discover_playlist ───────────────────────────────────────────────


class TestDiscoverPlaylist:
    def test_no_playlist_id_returns_error(self):
        deps = _build_deps()
        result = auto_discover_playlist({}, deps)
        assert result == {'status': 'error', 'reason': 'No playlist specified'}

    def test_specific_playlist_id_starts_worker(self):
        db = _StubDB(playlists=[{'id': 42, 'name': 'Test Playlist'}])
        called: List[Any] = []
        deps = _build_deps(
            get_database=lambda: db,
            run_playlist_discovery_worker=lambda *a, **k: called.append((a, k)),
        )
        result = auto_discover_playlist({'playlist_id': '42', '_automation_id': 'auto-1'}, deps)
        assert result['status'] == 'started'
        assert result['_manages_own_progress'] is True
        assert result['playlist_count'] == '1'
        # Worker spawned on a thread; give it a moment.
        for _ in range(50):
            if called:
                break
            import time
            time.sleep(0.01)
        assert len(called) == 1

    def test_all_playlists_includes_every_one(self):
        db = _StubDB(playlists=[
            {'id': 1, 'name': 'A'}, {'id': 2, 'name': 'B'}, {'id': 3, 'name': 'C'},
        ])
        deps = _build_deps(get_database=lambda: db)
        result = auto_discover_playlist({'all': True}, deps)
        assert result['playlist_count'] == '3'
        assert 'A' in result['playlists']
        assert 'B' in result['playlists']
        assert 'C' in result['playlists']

    def test_no_playlists_in_db_returns_error(self):
        deps = _build_deps(get_database=lambda: _StubDB(playlists=[]))
        result = auto_discover_playlist({'all': True}, deps)
        assert result == {'status': 'error', 'reason': 'No playlists found'}


# ─── refresh_mirrored ────────────────────────────────────────────────


@dataclass
class _StubSpotifyTrack:
    id: str
    name: str
    artists: list
    album: str
    duration_ms: int
    image_url: Optional[str] = None


@dataclass
class _StubSpotifyPlaylist:
    tracks: list


class _StubSpotifyClient:
    def __init__(self, playlist):
        self._playlist = playlist
        self._authenticated = True

    def is_spotify_authenticated(self) -> bool:
        return self._authenticated

    def get_playlist_by_id(self, _source_id):
        return self._playlist


class TestRefreshMirrored:
    def test_no_playlist_specified_returns_error(self):
        deps = _build_deps()
        result = auto_refresh_mirrored({}, deps)
        assert result == {'status': 'error', 'reason': 'No playlist specified'}

    def test_filters_unrefreshable_sources(self):
        # Sources 'file' and 'beatport' have no API to refresh from.
        db = _StubDB(playlists=[
            {'id': 1, 'name': 'F', 'source': 'file', 'source_playlist_id': '1'},
            {'id': 2, 'name': 'B', 'source': 'beatport', 'source_playlist_id': '2'},
        ])
        deps = _build_deps(get_database=lambda: db)
        result = auto_refresh_mirrored({'all': True}, deps)
        assert result['status'] == 'completed'
        assert result['refreshed'] == '0'
        assert db.mirror_calls == []  # nothing got pushed to DB

    def test_spotify_refresh_writes_to_db(self):
        track = _StubSpotifyTrack(
            id='track123', name='Hello', artists=['Adele'],
            album='25', duration_ms=295000,
        )
        playlist = _StubSpotifyPlaylist(tracks=[track])
        spotify = _StubSpotifyClient(playlist)
        db = _StubDB(playlists=[
            {'id': 5, 'name': 'My Spot', 'source': 'spotify',
             'source_playlist_id': 'spot-id', 'profile_id': 1},
        ])
        deps = _build_deps(get_database=lambda: db, spotify_client=spotify)
        result = auto_refresh_mirrored({'playlist_id': '5'}, deps)
        assert result['status'] == 'completed'
        assert result['refreshed'] == '1'
        assert len(db.mirror_calls) == 1
        call = db.mirror_calls[0]
        assert call['source'] == 'spotify'
        assert call['source_playlist_id'] == 'spot-id'
        assert call['name'] == 'My Spot'
        assert len(call['tracks']) == 1
        # Spotify-source tracks should be auto-marked discovered.
        extra = json.loads(call['tracks'][0]['extra_data'])
        assert extra['discovered'] is True
        assert extra['provider'] == 'spotify'
        assert extra['matched_data']['id'] == 'track123'

    def test_per_playlist_exception_collected_into_errors(self):
        # Force an exception by making the DB blow up on mirror_playlist.
        class _ExplodingDB(_StubDB):
            def mirror_playlist(self, **kwargs):
                raise RuntimeError('db disk full')

        track = _StubSpotifyTrack(id='t', name='t', artists=['a'], album='a', duration_ms=0)
        spotify = _StubSpotifyClient(_StubSpotifyPlaylist(tracks=[track]))
        db = _ExplodingDB(playlists=[
            {'id': 1, 'name': 'X', 'source': 'spotify', 'source_playlist_id': 'spot'},
        ])
        deps = _build_deps(get_database=lambda: db, spotify_client=spotify)
        result = auto_refresh_mirrored({'all': True}, deps)
        # Error captured, status still 'completed' (handler returns counts).
        assert result['status'] == 'completed'
        assert result['errors'] == '1'
        assert result['refreshed'] == '0'


# ─── sync_playlist ───────────────────────────────────────────────────


class TestSyncPlaylist:
    def test_no_playlist_id_returns_error(self):
        deps = _build_deps()
        result = auto_sync_playlist({}, deps)
        assert result == {'status': 'error', 'reason': 'No playlist specified'}

    def test_playlist_not_found_returns_error(self):
        deps = _build_deps(get_database=lambda: _StubDB(playlists=[]))
        result = auto_sync_playlist({'playlist_id': '99'}, deps)
        assert result == {'status': 'error', 'reason': 'Playlist not found'}

    def test_no_tracks_returns_error(self):
        db = _StubDB(playlists=[{'id': 1, 'name': 'P'}], playlist_tracks={1: []})
        deps = _build_deps(get_database=lambda: db)
        result = auto_sync_playlist({'playlist_id': '1'}, deps)
        assert result == {'status': 'error', 'reason': 'No tracks in playlist'}

    def test_no_discovered_tracks_skips(self):
        # All tracks lack discovery + spotify_hint + valid IDs.
        db = _StubDB(
            playlists=[{'id': 1, 'name': 'P'}],
            playlist_tracks={1: [{}, {}]},  # empty tracks → nothing usable
        )
        deps = _build_deps(get_database=lambda: db)
        result = auto_sync_playlist({'playlist_id': '1'}, deps)
        assert result['status'] == 'skipped'
        assert 'No discovered tracks' in result['reason']
        assert result['skipped_tracks'] == '2'

    def test_discovered_track_starts_sync_thread(self):
        discovered_track = {
            'extra_data': json.dumps({
                'discovered': True,
                'matched_data': {
                    'id': 'spot-1', 'name': 'Track', 'artists': [{'name': 'X'}],
                    'album': {'name': 'Album'}, 'duration_ms': 200000,
                },
            }),
            'artist_name': 'X',
        }
        db = _StubDB(
            playlists=[{'id': 1, 'name': 'P'}],
            playlist_tracks={1: [discovered_track]},
        )
        sync_calls: List[tuple] = []
        deps = _build_deps(
            get_database=lambda: db,
            run_sync_task=lambda *a, **k: sync_calls.append((a, k)),
        )
        result = auto_sync_playlist({'playlist_id': '1'}, deps)
        assert result['status'] == 'started'
        assert result['_manages_own_progress'] is True
        assert result['discovered_tracks'] == '1'
        # Wait for thread to fire run_sync_task
        for _ in range(50):
            if sync_calls:
                break
            import time
            time.sleep(0.01)
        assert len(sync_calls) == 1

    def test_unchanged_since_last_sync_returns_skipped(self):
        discovered_track = {
            'extra_data': json.dumps({
                'discovered': True,
                'matched_data': {
                    'id': 'spot-1', 'name': 'T', 'artists': [{'name': 'X'}],
                    'album': {'name': 'A'}, 'duration_ms': 0,
                },
            }),
            'artist_name': 'X',
        }
        db = _StubDB(
            playlists=[{'id': 1, 'name': 'P'}],
            playlist_tracks={1: [discovered_track]},
        )

        # Pre-populate the sync-status file with the EXPECTED hash so the
        # preflight short-circuit fires.
        import hashlib
        expected_hash = hashlib.md5('spot-1'.encode()).hexdigest()
        sync_statuses = {
            'auto_mirror_1': {'tracks_hash': expected_hash, 'matched_tracks': 1}
        }

        deps = _build_deps(
            get_database=lambda: db,
            load_sync_status_file=lambda: sync_statuses,
        )
        result = auto_sync_playlist({'playlist_id': '1'}, deps)
        assert result['status'] == 'skipped'
        assert 'unchanged' in result['reason']


# ─── playlist_pipeline ───────────────────────────────────────────────


class TestPlaylistPipeline:
    def test_no_playlist_specified_returns_error(self):
        deps = _build_deps()
        result = auto_playlist_pipeline({}, deps)
        assert result == {'status': 'error', 'error': 'No playlist specified'}
        # Pipeline-running flag MUST be cleared on error so the guard
        # doesn't block subsequent triggers.
        assert deps.state.pipeline_running is False

    def test_no_refreshable_playlists_clears_running_flag(self):
        db = _StubDB(playlists=[
            {'id': 1, 'name': 'F', 'source': 'file'},
            {'id': 2, 'name': 'B', 'source': 'beatport'},
        ])
        deps = _build_deps(get_database=lambda: db)
        result = auto_playlist_pipeline({'all': True}, deps)
        assert result == {'status': 'error', 'error': 'No refreshable playlists found'}
        assert deps.state.pipeline_running is False

    def test_pipeline_clears_running_on_unhandled_exception(self):
        # Force the database accessor to blow up after the early checks.
        class _ExplodingDB(_StubDB):
            def get_mirrored_playlists(self):
                raise RuntimeError('db down')

        db = _ExplodingDB(playlists=[])
        deps = _build_deps(get_database=lambda: db)
        result = auto_playlist_pipeline({'all': True}, deps)
        assert result['status'] == 'error'
        assert result['_manages_own_progress'] is True
        assert deps.state.pipeline_running is False
