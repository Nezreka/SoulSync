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
from core.automation.handlers._pipeline_shared import run_sync_and_wishlist
from core.automation.handlers.playlist_pipeline import auto_playlist_pipeline
from core.automation.handlers.refresh_mirrored import auto_refresh_mirrored
from core.automation.handlers.sync_playlist import auto_sync_playlist
from core.playlists.sources.bootstrap import build_playlist_source_registry


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
    # Build a default registry from whatever clients the test passed.
    # The refresh_mirrored handler reads from deps.playlist_source_registry
    # exclusively, so the registry must mirror the passed clients to
    # preserve the pre-refactor test behavior.
    _spotify = overrides.get('spotify_client')
    _tidal = overrides.get('tidal_client')
    _get_deezer = overrides.get('get_deezer_client', lambda: None)
    _parse_youtube = overrides.get('parse_youtube_playlist', lambda url: None)
    _registry = build_playlist_source_registry(
        spotify_client_getter=lambda: _spotify,
        tidal_client_getter=lambda: _tidal,
        qobuz_client_getter=lambda: None,
        deezer_client_getter=_get_deezer,
        youtube_parser=_parse_youtube,
    )

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
        run_playlist_organize_download=lambda **k: {'status': 'skipped'},
        missing_download_executor=None,
        load_sync_status_file=lambda: {},
        get_deezer_client=lambda: None,
        parse_youtube_playlist=lambda url: None,
        playlist_source_registry=_registry,
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
    # Adapter-side projection reads metadata fields off the playlist
    # object (the real ``core.spotify_client.Playlist`` dataclass).
    # Provide minimal defaults so the stub stays a one-liner at call
    # sites that only care about tracks.
    id: str = 'spot-id'
    name: str = 'My Spot'
    description: Optional[str] = ''
    owner: str = 'me'
    public: bool = True
    collaborative: bool = False
    total_tracks: int = 0


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

    def test_spotify_public_missing_source_url_is_reported_as_error(self):
        db = _StubDB(playlists=[
            {'id': 1, 'name': 'No URL', 'source': 'spotify_public', 'source_playlist_id': 'hash'},
        ])
        progress = []
        deps = _build_deps(
            get_database=lambda: db,
            update_progress=lambda *a, **k: progress.append(k),
        )

        result = auto_refresh_mirrored({'playlist_id': '1'}, deps)

        assert result['status'] == 'completed'
        assert result['refreshed'] == '0'
        assert result['errors'] == '1'
        assert db.mirror_calls == []
        assert any(p.get('log_type') == 'error' and 'missing its original source URL' in p.get('log_line', '') for p in progress)

    def test_tidal_not_authenticated_emits_skip_not_error(self):
        """Soft-skip preserves the legacy log_type='skip' contract — the
        run still counts as completed with 0 errors so the automation
        doesn't surface a Tidal-down condition as a refresh failure."""
        class _UnauthedTidal:
            def is_authenticated(self):
                return False

        db = _StubDB(playlists=[
            {'id': 7, 'name': 'My Tidal', 'source': 'tidal',
             'source_playlist_id': 'tid-id', 'profile_id': 1},
        ])
        progress = []
        deps = _build_deps(
            get_database=lambda: db,
            tidal_client=_UnauthedTidal(),
            update_progress=lambda *a, **k: progress.append(k),
        )

        result = auto_refresh_mirrored({'playlist_id': '7'}, deps)

        assert result['status'] == 'completed'
        assert result['refreshed'] == '0'
        assert result['errors'] == '0'  # skip, not error
        assert db.mirror_calls == []
        assert any(
            p.get('log_type') == 'skip' and 'Tidal not authenticated' in p.get('log_line', '')
            for p in progress
        )

    def test_deezer_refresh_writes_plain_tracks_no_matched_data(self):
        class _StubDeezer:
            def is_authenticated(self):
                return True

            def get_user_playlists(self):
                return []

            def get_playlist(self, playlist_id):
                return {
                    'id': playlist_id,
                    'name': 'Deez',
                    'description': '',
                    'track_count': 1,
                    'image_url': '',
                    'owner': '',
                    'tracks': [{
                        'id': 'dz1',
                        'name': 'Track',
                        'artists': ['Deez Artist'],
                        'album': 'Deez Album',
                        'duration_ms': 200_000,
                    }],
                }

        db = _StubDB(playlists=[
            {'id': 9, 'name': 'Deez Mix', 'source': 'deezer',
             'source_playlist_id': 'dz-id', 'profile_id': 1},
        ])
        deps = _build_deps(
            get_database=lambda: db,
            get_deezer_client=lambda: _StubDeezer(),
        )

        result = auto_refresh_mirrored({'playlist_id': '9'}, deps)

        assert result['status'] == 'completed'
        assert result['refreshed'] == '1'
        assert len(db.mirror_calls) == 1
        call = db.mirror_calls[0]
        assert call['source'] == 'deezer'
        assert len(call['tracks']) == 1
        # Deezer tracks don't carry discovery state — no extra_data.
        assert 'extra_data' not in call['tracks'][0]
        assert call['tracks'][0]['source_track_id'] == 'dz1'

    def test_youtube_refresh_reads_url_from_description(self):
        """URL-backed sources store the hash in source_playlist_id and
        the canonical URL in description. The handler has to pull the
        URL out before passing to the adapter."""
        parsed_calls = []

        def _fake_parser(url):
            parsed_calls.append(url)
            return {
                'id': 'yt_pl',
                'name': 'YT Mix',
                'url': url,
                'track_count': 1,
                'tracks': [{
                    'id': 'vid1',
                    'name': 'Track',
                    'artists': ['Channel'],
                    'duration_ms': 240_000,
                }],
            }

        db = _StubDB(playlists=[
            {
                'id': 11,
                'name': 'YT Mix',
                'source': 'youtube',
                'source_playlist_id': 'hashhash',
                'description': 'https://youtube.com/playlist?list=yt_pl',
                'profile_id': 1,
            },
        ])
        deps = _build_deps(
            get_database=lambda: db,
            parse_youtube_playlist=_fake_parser,
        )

        result = auto_refresh_mirrored({'playlist_id': '11'}, deps)

        assert result['refreshed'] == '1'
        # Parser was called with the URL from description, not the hash.
        assert parsed_calls == ['https://youtube.com/playlist?list=yt_pl']
        assert db.mirror_calls[0]['tracks'][0]['source_track_id'] == 'vid1'

    def test_listenbrainz_refresh_runs_discovery_and_writes_matched_data(self):
        """End-to-end: LB cached playlist → adapter projects to
        NormalizedTrack with needs_discovery=True → refresh_mirrored
        calls source.discover_tracks → matched_data lands in
        extra_data on the mirror DB row."""
        from core.playlists.sources.bootstrap import build_playlist_source_registry
        from core.playlists.sources.listenbrainz import ListenBrainzPlaylistSource

        class _StubLBManager:
            def get_cached_playlists(self, playlist_type):
                if playlist_type == 'created_for_user':
                    return [{
                        'playlist_mbid': 'lb-1',
                        'title': 'LB Weekly',
                        'creator': 'ListenBrainz',
                        'track_count': 1,
                        'annotation': {},
                        'last_updated': '2026-05-26',
                    }]
                return []

            def get_playlist_type(self, mbid):
                return 'created_for_user' if mbid == 'lb-1' else ''

            def get_cached_tracks(self, mbid):
                if mbid == 'lb-1':
                    return [{
                        'track_name': 'MB Song',
                        'artist_name': 'MB Artist',
                        'album_name': 'MB Album',
                        'duration_ms': 240_000,
                        'recording_mbid': 'rec-1',
                        'release_mbid': 'rel-1',
                        'album_cover_url': '',
                        'additional_metadata': {},
                    }]
                return []

            def update_all_playlists(self):
                pass

            def refresh_playlist(self, mbid):
                # Adapter now calls the targeted refresh instead of
                # the legacy ``update_all_playlists``. Trivial stub —
                # the test only cares about the discovery + commit
                # paths downstream.
                return {'success': True, 'result': 'skipped',
                        'playlist_mbid': mbid}

        discovery_calls = []

        def fake_discover(track_dicts):
            discovery_calls.append(list(track_dicts))
            return [{
                'id': 'sp-matched',
                'name': 'Matched',
                'artists': ['Spotify Artist'],
                'album': {'name': 'Spotify Album'},
                'duration_ms': 240_000,
                'image_url': 'art',
                'source': 'spotify',
                '_provider': 'spotify',
                '_confidence': 0.93,
            }]

        # Build a registry with the LB adapter pre-wired with discovery.
        # The default _build_deps registry won't have discovery wired,
        # so we override it for this test.
        registry = build_playlist_source_registry(
            spotify_client_getter=lambda: None,
            tidal_client_getter=lambda: None,
            qobuz_client_getter=lambda: None,
            deezer_client_getter=lambda: None,
            listenbrainz_manager_getter=lambda: _StubLBManager(),
            discover_callable=fake_discover,
        )

        db = _StubDB(playlists=[
            {
                'id': 33,
                'name': 'LB Weekly',
                'source': 'listenbrainz',
                'source_playlist_id': 'lb-1',
                'profile_id': 1,
            },
        ])
        deps = _build_deps(get_database=lambda: db)
        # Replace the default registry with the one wired up above.
        # AutomationDeps is a frozen dataclass-like — re-assign via
        # object.__setattr__ since it's a plain dataclass.
        object.__setattr__(deps, 'playlist_source_registry', registry)

        result = auto_refresh_mirrored({'playlist_id': '33'}, deps)

        assert result['status'] == 'completed'
        assert result['refreshed'] == '1'
        # discover_tracks ran once, with the single MB track.
        assert len(discovery_calls) == 1
        assert discovery_calls[0][0]['track_name'] == 'MB Song'
        assert discovery_calls[0][0]['artist_name'] == 'MB Artist'

        # Mirror DB row carries the matched_data extra_data.
        call = db.mirror_calls[0]
        assert call['source'] == 'listenbrainz'
        assert len(call['tracks']) == 1
        track = call['tracks'][0]
        assert track['source_track_id'] == 'sp-matched'
        extra = json.loads(track['extra_data'])
        assert extra['discovered'] is True
        assert extra['provider'] == 'spotify'
        assert extra['matched_data']['id'] == 'sp-matched'

    def test_skip_discovery_flag_bypasses_matcher(self):
        """``skip_discovery=True`` (set by the pipeline runner) must
        prevent ``_maybe_discover`` from invoking the matching engine
        on LB / Last.fm tracks. Pipeline's Phase 2 runs the discovery
        worker with proper progress emission — running it during
        refresh too blocks the UI for minutes with no updates."""
        from core.playlists.sources.bootstrap import build_playlist_source_registry

        class _StubLBManager:
            def get_cached_playlists(self, playlist_type):
                if playlist_type == 'created_for_user':
                    return [{
                        'playlist_mbid': 'lb-skip',
                        'title': 'LB Weekly',
                        'creator': 'ListenBrainz',
                        'track_count': 1,
                        'annotation': {},
                        'last_updated': '2026-05-26',
                    }]
                return []

            def get_playlist_type(self, mbid):
                return 'created_for_user' if mbid == 'lb-skip' else ''

            def get_cached_tracks(self, mbid):
                if mbid == 'lb-skip':
                    return [{
                        'track_name': 'MB Song',
                        'artist_name': 'MB Artist',
                        'album_name': 'MB Album',
                        'duration_ms': 240_000,
                        'recording_mbid': 'rec-skip',
                        'release_mbid': '',
                        'album_cover_url': '',
                        'additional_metadata': {},
                    }]
                return []

            def refresh_playlist(self, mbid):
                # Test only cares that discovery is skipped, not the
                # refresh path itself — keep this trivial.
                return {'success': True, 'result': 'skipped'}

        discovery_calls = []

        def fake_discover(track_dicts):
            discovery_calls.append(list(track_dicts))
            return []  # never returned because we expect skip

        registry = build_playlist_source_registry(
            spotify_client_getter=lambda: None,
            tidal_client_getter=lambda: None,
            qobuz_client_getter=lambda: None,
            deezer_client_getter=lambda: None,
            listenbrainz_manager_getter=lambda: _StubLBManager(),
            discover_callable=fake_discover,
        )

        db = _StubDB(playlists=[
            {
                'id': 77,
                'name': 'LB Weekly',
                'source': 'listenbrainz',
                'source_playlist_id': 'lb-skip',
                'profile_id': 1,
            },
        ])
        deps = _build_deps(get_database=lambda: db)
        object.__setattr__(deps, 'playlist_source_registry', registry)

        # Pipeline-style call: include the skip_discovery flag.
        result = auto_refresh_mirrored(
            {'playlist_id': '77', 'skip_discovery': True}, deps,
        )

        assert result['status'] == 'completed'
        assert result['refreshed'] == '1'
        # CRITICAL: the matcher was NOT invoked.
        assert discovery_calls == []

        # The mirror row still landed — just without matched_data.
        # Phase 2 of the pipeline picks it up from needs_discovery.
        call = db.mirror_calls[0]
        assert len(call['tracks']) == 1
        # No discovery → no matched_data, and the source_track_id
        # falls back to the recording_mbid the LB adapter projects.
        track = call['tracks'][0]
        assert track['source_track_id'] == 'rec-skip'

    def test_spotify_public_uses_authed_spotify_when_signed_in(self):
        """The handler-level fallback chain: when Spotify is authed
        AND the public URL is a playlist URL, prefer the authed API so
        the mirror gets album-art-bearing matched_data instead of the
        bare scraper output."""
        track = _StubSpotifyTrack(
            id='auth-trk', name='From Auth', artists=['Artist'],
            album='Album', duration_ms=200_000, image_url='img',
        )
        spotify = _StubSpotifyClient(_StubSpotifyPlaylist(
            tracks=[track], id='auth-pid', name='Auth',
        ))

        db = _StubDB(playlists=[
            {
                'id': 22,
                'name': 'Pub',
                'source': 'spotify_public',
                'source_playlist_id': 'hash',
                'description': 'https://open.spotify.com/playlist/abc123def456',
                'profile_id': 1,
            },
        ])
        deps = _build_deps(
            get_database=lambda: db,
            spotify_client=spotify,
        )

        result = auto_refresh_mirrored({'playlist_id': '22'}, deps)

        assert result['refreshed'] == '1'
        call = db.mirror_calls[0]
        # Track came from the authed Spotify path → carries matched_data.
        extra = json.loads(call['tracks'][0]['extra_data'])
        assert extra['discovered'] is True
        assert extra['provider'] == 'spotify'
        assert extra['matched_data']['id'] == 'auth-trk'


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

    def test_organize_by_playlist_passes_skip_wishlist_add(self):
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
            playlists=[{'id': 1, 'name': 'P', 'organize_by_playlist': True}],
            playlist_tracks={1: [discovered_track]},
        )
        sync_calls: List[tuple] = []
        deps = _build_deps(
            get_database=lambda: db,
            run_sync_task=lambda *a, **k: sync_calls.append((a, k)),
        )
        auto_sync_playlist({'playlist_id': '1'}, deps)
        for _ in range(50):
            if sync_calls:
                break
            import time
            time.sleep(0.01)
        assert sync_calls
        assert sync_calls[0][1].get('skip_wishlist_add') is True

    def test_unchanged_since_last_sync_returns_skipped(self):
        discovered_track = {
            'source_track_id': 'spot-1',
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
            'auto_mirror_1': {
                'tracks_hash': expected_hash,
                'mirror_tracks_hash': expected_hash,
                'matched_tracks': 1,
            }
        }

        deps = _build_deps(
            get_database=lambda: db,
            load_sync_status_file=lambda: sync_statuses,
        )
        result = auto_sync_playlist({'playlist_id': '1'}, deps)
        assert result['status'] == 'skipped'
        assert 'unchanged' in result['reason']

    def test_playlist_changed_event_bypasses_unchanged_skip(self):
        discovered_track = {
            'source_track_id': 'spot-1',
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
        import hashlib
        expected_hash = hashlib.md5('spot-1'.encode()).hexdigest()
        sync_statuses = {
            'auto_mirror_1': {
                'tracks_hash': expected_hash,
                'mirror_tracks_hash': expected_hash,
                'matched_tracks': 1,
            }
        }
        sync_calls: List[tuple] = []
        deps = _build_deps(
            get_database=lambda: db,
            load_sync_status_file=lambda: sync_statuses,
            run_sync_task=lambda *a, **k: sync_calls.append((a, k)),
        )
        result = auto_sync_playlist(
            {'playlist_id': '1', '_event_data': {'added': '1', 'playlist_id': '1'}},
            deps,
        )
        assert result['status'] == 'started'
        import time
        for _ in range(50):
            if sync_calls:
                break
            time.sleep(0.01)
        assert len(sync_calls) == 1

    def test_new_mirror_row_with_skipped_track_bypasses_unchanged_skip(self):
        """New playlist row without discovery must not reuse the old tracks_hash skip."""
        discovered_track = {
            'source_track_id': 'spot-1',
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
            playlist_tracks={1: [discovered_track, {}]},  # second row not syncable
        )
        import hashlib
        old_hash = hashlib.md5('spot-1'.encode()).hexdigest()
        new_mirror_hash = hashlib.md5('spot-1,spot-new'.encode()).hexdigest()
        sync_statuses = {
            'auto_mirror_1': {
                'tracks_hash': old_hash,
                'mirror_tracks_hash': old_hash,
                'matched_tracks': 1,
            }
        }
        # Give the new mirror row a source id so mirror hash changes.
        db.playlist_tracks[1][1]['source_track_id'] = 'spot-new'

        sync_calls: List[tuple] = []
        deps = _build_deps(
            get_database=lambda: db,
            load_sync_status_file=lambda: sync_statuses,
            run_sync_task=lambda *a, **k: sync_calls.append((a, k)),
        )
        result = auto_sync_playlist({'playlist_id': '1'}, deps)
        assert result['status'] == 'started'
        assert result['skipped_tracks'] == '1'
        import time
        for _ in range(50):
            if sync_calls:
                break
            time.sleep(0.01)
        assert len(sync_calls) == 1
        assert new_mirror_hash != old_hash


# ─── playlist_pipeline ───────────────────────────────────────────────


class TestPlaylistPipeline:
    def test_pipeline_skips_when_shared_lock_is_already_running(self):
        deps = _build_deps()
        deps.state.set_pipeline_running(True)

        result = auto_playlist_pipeline({'all': True}, deps)

        assert result == {
            'status': 'skipped',
            'reason': 'playlist_pipeline is already running',
            '_manages_own_progress': True,
        }
        assert deps.state.pipeline_running is True

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

    def test_shared_sync_tail_counts_background_sync_errors(self):
        progress = []
        sync_states = {
            'auto_mirror_1': {'status': 'error', 'error': 'media server unavailable'},
        }
        deps = _build_deps(
            get_sync_states=lambda: sync_states,
            update_progress=lambda *a, **k: progress.append(k),
        )

        result = run_sync_and_wishlist(
            deps,
            'auto-1',
            [{'id': 1, 'name': 'Broken'}],
            sync_one_fn=lambda _pl: {'status': 'started'},
            sync_id_for_fn=lambda _pl: 'auto_mirror_1',
            skip_wishlist=True,
        )

        assert result['errors'] == 1
        assert result['synced'] == 0
        assert any(p.get('log_type') == 'error' and 'media server unavailable' in p.get('log_line', '') for p in progress)
