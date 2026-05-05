"""Tests for core/discovery/playlist.py — mirrored playlist discovery worker."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from core.discovery import playlist as dp


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

@dataclass
class _FakeMatch:
    id: str = 'id-1'
    name: str = 'Match Name'
    artists: list = None
    album: str = 'Match Album'
    duration_ms: int = 200000
    image_url: str = ''
    release_date: str = '2024-01-01'

    def __post_init__(self):
        if self.artists is None:
            self.artists = ['Match Artist']


class _FakeSpotifyClient:
    def __init__(self, results=None, authenticated=True):
        self._results = results if results is not None else []
        self._authenticated = authenticated
        self.search_calls = []

    def is_spotify_authenticated(self):
        return self._authenticated

    def search_tracks(self, query, limit=10):
        self.search_calls.append((query, limit))
        return self._results


class _FakeITunesClient:
    def __init__(self, results=None):
        self._results = results if results is not None else []
        self.search_calls = []

    def search_tracks(self, query, limit=10):
        self.search_calls.append((query, limit))
        return self._results


class _FakeMatchingEngine:
    def generate_download_queries(self, t):
        return [f"{t.artists[0]} {t.name}"]


class _FakeAutomationEngine:
    def __init__(self):
        self.events = []

    def emit(self, event_type, data):
        self.events.append((event_type, data))


class _FakeDB:
    def __init__(self, tracks_by_playlist=None, cache_match=None):
        self._tracks = tracks_by_playlist or {}
        self._cache_match = cache_match
        self.extra_data_writes = []
        self.cache_saves = []

    def get_mirrored_playlist_tracks(self, pl_id):
        return self._tracks.get(pl_id, [])

    def get_discovery_cache_match(self, title, artist, source):
        return self._cache_match

    def update_mirrored_track_extra_data(self, track_id, extra_data):
        self.extra_data_writes.append((track_id, extra_data))

    def save_discovery_cache_match(self, title, artist, source, conf, data, raw_t, raw_a):
        self.cache_saves.append((title, artist, source, conf))


class _FakeMetadataCache:
    def get_entity(self, source, kind, entity_id):
        return None


def _build_deps(
    *,
    spotify_results=None,
    spotify_auth=True,
    itunes_results=None,
    discovery_source='spotify',
    cache_match=None,
    tracks_by_playlist=None,
    cancellation_set=None,
    fallback_source='itunes',
    score_result=(None, 0.0, 0),
    auto_progress_log=None,
    activity_log=None,
):
    auto_progress_log = auto_progress_log if auto_progress_log is not None else []
    db = _FakeDB(tracks_by_playlist=tracks_by_playlist or {}, cache_match=cache_match)
    spotify = _FakeSpotifyClient(results=spotify_results or [], authenticated=spotify_auth)
    itunes = _FakeITunesClient(results=itunes_results or [])
    automation = _FakeAutomationEngine()

    deps = dp.PlaylistDiscoveryDeps(
        spotify_client=spotify,
        matching_engine=_FakeMatchingEngine(),
        automation_engine=automation,
        playlist_discovery_cancelled=cancellation_set if cancellation_set is not None else set(),
        pause_enrichment_workers=lambda label: {'paused': True},
        resume_enrichment_workers=lambda state, label: None,
        get_active_discovery_source=lambda: discovery_source,
        get_metadata_fallback_client=lambda: itunes,
        get_metadata_fallback_source=lambda: fallback_source,
        update_automation_progress=lambda *a, **kw: auto_progress_log.append((a, kw)),
        get_database=lambda: db,
        get_discovery_cache_key=lambda title, artist: (title.lower(), artist.lower()),
        validate_discovery_cache_artist=lambda artist, m: True,
        discovery_score_candidates=lambda *args, **kw: score_result,
        get_metadata_cache=lambda: _FakeMetadataCache(),
        build_discovery_wing_it_stub=lambda title, artist, dur: {
            'name': title, 'artists': [artist], 'duration_ms': dur, 'wing_it': True
        },
    )
    deps._db = db
    deps._spotify = spotify
    deps._itunes = itunes
    deps._auto = automation
    deps._auto_progress_log = auto_progress_log
    return deps


def _track(track_id=1, name='Track', artist='Artist', duration_ms=180000, extra_data=None):
    t = {
        'id': track_id,
        'track_name': name,
        'artist_name': artist,
        'duration_ms': duration_ms,
    }
    if extra_data is not None:
        t['extra_data'] = extra_data if isinstance(extra_data, str) else json.dumps(extra_data)
    return t


def _playlist(pl_id='p1', name='My Playlist', source='spotify'):
    return {'id': pl_id, 'name': name, 'source': source}


# ---------------------------------------------------------------------------
# Empty / no work
# ---------------------------------------------------------------------------

def test_no_playlists_runs_clean():
    """Empty playlists list completes without error."""
    deps = _build_deps()
    dp.run_playlist_discovery_worker([], automation_id='auto-1', deps=deps)
    # automation finished call appended
    assert any(kw.get('status') == 'finished' for _, kw in deps._auto_progress_log)


def test_playlist_with_no_tracks_skipped():
    """Playlist with no tracks → continue, no DB writes."""
    deps = _build_deps(tracks_by_playlist={'p1': []})
    dp.run_playlist_discovery_worker([_playlist('p1')], deps=deps)
    assert deps._db.extra_data_writes == []


# ---------------------------------------------------------------------------
# Already-discovered skip logic
# ---------------------------------------------------------------------------

def test_complete_discovery_skipped():
    """Track with discovered=True + complete metadata is skipped."""
    extra = {
        'discovered': True,
        'matched_data': {
            'track_number': 5,
            'album': {'release_date': '2024-01-01', 'id': 'a1'},
        },
    }
    tracks = [_track(track_id=1, extra_data=extra)]
    deps = _build_deps(tracks_by_playlist={'p1': tracks})

    dp.run_playlist_discovery_worker([_playlist('p1')], deps=deps)

    assert deps._db.extra_data_writes == []  # no re-discovery


def test_incomplete_discovery_redone():
    """discovered=True but missing track_number/release_date → re-discover."""
    extra = {
        'discovered': True,
        'matched_data': {'album': {}},  # missing both track_number AND release_date
    }
    tracks = [_track(track_id=1, extra_data=extra)]
    deps = _build_deps(tracks_by_playlist={'p1': tracks})

    dp.run_playlist_discovery_worker([_playlist('p1')], deps=deps)

    # Re-discovered as Wing It (no match in score_result default)
    assert len(deps._db.extra_data_writes) == 1


def test_wing_it_fallback_always_redone():
    """Wing It stub (wing_it_fallback=True) is re-attempted regardless."""
    extra = {'discovered': True, 'wing_it_fallback': True, 'matched_data': {}}
    tracks = [_track(track_id=1, extra_data=extra)]
    deps = _build_deps(tracks_by_playlist={'p1': tracks})

    dp.run_playlist_discovery_worker([_playlist('p1')], deps=deps)

    assert len(deps._db.extra_data_writes) == 1


def test_unmatched_by_user_respected():
    """unmatched_by_user=True → respect user's choice, skip."""
    extra = {'unmatched_by_user': True}
    tracks = [_track(track_id=1, extra_data=extra)]
    deps = _build_deps(tracks_by_playlist={'p1': tracks})

    dp.run_playlist_discovery_worker([_playlist('p1')], deps=deps)

    assert deps._db.extra_data_writes == []


# ---------------------------------------------------------------------------
# Cache hit short-circuit
# ---------------------------------------------------------------------------

def test_cache_hit_short_circuits():
    """Discovery cache hit writes extra_data and skips search."""
    cached = {'name': 'Cached Match', 'artists': ['CA'], 'confidence': 0.9}
    tracks = [_track(track_id=1)]
    deps = _build_deps(tracks_by_playlist={'p1': tracks}, cache_match=cached)

    dp.run_playlist_discovery_worker([_playlist('p1')], deps=deps)

    assert len(deps._db.extra_data_writes) == 1
    track_id, extra = deps._db.extra_data_writes[0]
    assert extra['discovered'] is True
    assert extra['matched_data'] == cached
    assert deps._spotify.search_calls == []  # no live search


# ---------------------------------------------------------------------------
# Live search match
# ---------------------------------------------------------------------------

def test_match_above_threshold_writes_extra_data():
    """High-confidence match writes matched_data + saves to discovery cache."""
    match = _FakeMatch()
    tracks = [_track(track_id=1)]
    deps = _build_deps(
        tracks_by_playlist={'p1': tracks},
        spotify_results=[match],
        score_result=(match, 0.92, 0),
    )

    dp.run_playlist_discovery_worker([_playlist('p1')], deps=deps)

    assert len(deps._db.extra_data_writes) == 1
    _, extra = deps._db.extra_data_writes[0]
    assert extra['discovered'] is True
    assert extra['source'] == 'spotify'
    assert extra['confidence'] == 0.92
    assert deps._db.cache_saves  # saved to cache


def test_match_below_threshold_falls_back_to_wing_it():
    """No high-confidence match → Wing It stub written."""
    match = _FakeMatch()
    tracks = [_track(track_id=1)]
    deps = _build_deps(
        tracks_by_playlist={'p1': tracks},
        spotify_results=[match],
        score_result=(match, 0.5, 0),  # below 0.7 threshold
    )

    dp.run_playlist_discovery_worker([_playlist('p1')], deps=deps)

    assert len(deps._db.extra_data_writes) == 1
    _, extra = deps._db.extra_data_writes[0]
    assert extra['source'] == 'wing_it_fallback'
    assert extra['wing_it_fallback'] is True


# ---------------------------------------------------------------------------
# iTunes fallback
# ---------------------------------------------------------------------------

def test_itunes_fallback_when_spotify_unauthenticated():
    """spotify unauthenticated → iTunes used."""
    match = _FakeMatch()
    tracks = [_track(track_id=1)]
    deps = _build_deps(
        tracks_by_playlist={'p1': tracks},
        spotify_auth=False,
        discovery_source='itunes',
        itunes_results=[match],
        score_result=(match, 0.95, 0),
    )

    dp.run_playlist_discovery_worker([_playlist('p1')], deps=deps)

    assert deps._itunes.search_calls
    assert deps._spotify.search_calls == []


def test_neither_provider_available_returns_error():
    """Spotify not authenticated AND iTunes raises → automation marked error, return."""
    def raising_fallback():
        raise RuntimeError("no fallback")
    tracks = [_track(track_id=1)]
    deps = _build_deps(
        tracks_by_playlist={'p1': tracks},
        spotify_auth=False,
    )
    deps.get_metadata_fallback_client = raising_fallback

    dp.run_playlist_discovery_worker([_playlist('p1')], automation_id='a1', deps=deps)

    # No discovery occurred; automation marked error
    assert deps._db.extra_data_writes == []
    assert any(kw.get('status') == 'error' for _, kw in deps._auto_progress_log)


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def test_cancellation_aborts_loop():
    """automation_id in cancellation set → finish + return."""
    tracks = [_track(track_id=1), _track(track_id=2)]
    cancel_set = {'auto-stop'}
    deps = _build_deps(
        tracks_by_playlist={'p1': tracks},
        cancellation_set=cancel_set,
    )

    dp.run_playlist_discovery_worker([_playlist('p1')], automation_id='auto-stop', deps=deps)

    # Cancelled before any track processed; cancel_set drained
    assert 'auto-stop' not in cancel_set


# ---------------------------------------------------------------------------
# Completion event emission
# ---------------------------------------------------------------------------

def test_discovery_completed_event_emitted():
    """At least one discovered track → automation_engine.emit('discovery_completed')."""
    match = _FakeMatch()
    tracks = [_track(track_id=1)]
    deps = _build_deps(
        tracks_by_playlist={'p1': tracks},
        spotify_results=[match],
        score_result=(match, 0.92, 0),
    )

    dp.run_playlist_discovery_worker([_playlist('p1')], deps=deps)

    events = deps._auto.events
    assert any(name == 'discovery_completed' for name, _ in events)


def test_no_event_when_nothing_discovered():
    """Zero discovered → no discovery_completed event."""
    extra = {
        'discovered': True,
        'matched_data': {
            'track_number': 5,
            'album': {'release_date': '2024-01-01', 'id': 'a1'},
        },
    }
    tracks = [_track(track_id=1, extra_data=extra)]
    deps = _build_deps(tracks_by_playlist={'p1': tracks})

    dp.run_playlist_discovery_worker([_playlist('p1')], deps=deps)

    assert deps._auto.events == []


# ---------------------------------------------------------------------------
# Multi-playlist
# ---------------------------------------------------------------------------

def test_multi_playlist_aggregates_grand_total():
    """Multiple playlists → grand_total counted across all."""
    match = _FakeMatch()
    tracks_p1 = [_track(track_id=1)]
    tracks_p2 = [_track(track_id=2), _track(track_id=3)]
    deps = _build_deps(
        tracks_by_playlist={'p1': tracks_p1, 'p2': tracks_p2},
        spotify_results=[match],
        score_result=(match, 0.92, 0),
    )

    dp.run_playlist_discovery_worker([_playlist('p1'), _playlist('p2')], deps=deps)

    # All 3 tracks discovered → 3 extra_data writes
    assert len(deps._db.extra_data_writes) == 3
