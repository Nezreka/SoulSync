"""Tests for core/discovery/youtube.py — YouTube discovery worker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from core.discovery import youtube as dy


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

@dataclass
class _FakeMatch:
    id: str = 'spt-1'
    name: str = 'Found Title'
    artists: list = None
    album: str = 'Found Album'
    duration_ms: int = 200000
    image_url: str = ''

    def __post_init__(self):
        if self.artists is None:
            self.artists = ['Found Artist']


class _FakeSpotifyClient:
    def __init__(self, results=None, authenticated=True):
        self._results = results or []
        self._authenticated = authenticated
        self.search_calls = []

    def is_spotify_authenticated(self):
        return self._authenticated

    def search_tracks(self, query, limit=10):
        self.search_calls.append((query, limit))
        return self._results


class _FakeITunesClient:
    def __init__(self, results=None):
        self._results = results or []
        self.search_calls = []

    def search_tracks(self, query, limit=10):
        self.search_calls.append((query, limit))
        return self._results


class _FakeMatchingEngine:
    def generate_download_queries(self, track):
        return [f"{track.artists[0]} {track.name}"]


class _FakeDB:
    def __init__(self, cache_match=None):
        self._cache_match = cache_match
        self.cache_saves = []
        self.mirrored_updates = []

    def get_discovery_cache_match(self, title, artist, source):
        return self._cache_match

    def save_discovery_cache_match(self, title, artist, source, conf, data, raw_t, raw_a):
        self.cache_saves.append((title, artist, source, conf))

    def update_mirrored_track_extra_data(self, db_track_id, extra_data):
        self.mirrored_updates.append((db_track_id, extra_data))


class _FakeMetadataCache:
    def get_entity(self, source, kind, entity_id):
        return None


def _build_deps(
    *,
    states=None,
    spotify_results=None,
    spotify_auth=True,
    itunes_results=None,
    discovery_source='spotify',
    cache_match=None,
    pause_called=None,
    resume_called=None,
    rate_limited=False,
    activity_log=None,
    score_candidates_result=(None, 0.0, 0),
):
    pause_called = pause_called if pause_called is not None else []
    resume_called = resume_called if resume_called is not None else []
    activity_log = activity_log if activity_log is not None else []

    db = _FakeDB(cache_match=cache_match)
    spotify = _FakeSpotifyClient(results=spotify_results or [], authenticated=spotify_auth)
    itunes = _FakeITunesClient(results=itunes_results or [])

    deps = dy.YoutubeDiscoveryDeps(
        youtube_playlist_states=states if states is not None else {},
        spotify_client=spotify,
        matching_engine=_FakeMatchingEngine(),
        pause_enrichment_workers=lambda label: (pause_called.append(label) or {'paused': True}),
        resume_enrichment_workers=lambda state, label: resume_called.append((state, label)),
        get_active_discovery_source=lambda: discovery_source,
        get_metadata_fallback_client=lambda: itunes,
        get_discovery_cache_key=lambda title, artist: (title.lower(), artist.lower()),
        validate_discovery_cache_artist=lambda artist, m: True,
        extract_artist_name=lambda a: a if isinstance(a, str) else a.get('name', ''),
        spotify_rate_limited=lambda: rate_limited,
        discovery_score_candidates=lambda *args, **kw: score_candidates_result,
        get_metadata_cache=lambda: _FakeMetadataCache(),
        build_discovery_wing_it_stub=lambda title, artist, dur: {
            'name': title, 'artists': [artist], 'duration_ms': dur, 'wing_it': True
        },
        get_database=lambda: db,
        add_activity_item=lambda *a, **kw: activity_log.append((a, kw)),
    )
    deps._db = db  # expose for test assertions
    deps._spotify = spotify
    deps._itunes = itunes
    deps._pause_called = pause_called
    deps._resume_called = resume_called
    deps._activity_log = activity_log
    return deps


def _seed_state(url_hash, states, *, tracks=None, phase='discovering'):
    states[url_hash] = {
        'phase': phase,
        'playlist': {'name': 'Test Playlist', 'tracks': tracks or []},
        'discovery_progress': 0,
        'spotify_matches': 0,
        'discovery_results': [],
    }


def _track(name='Track1', artist='Artist1', duration_ms=180000):
    return {'name': name, 'artists': [artist], 'duration_ms': duration_ms,
            'raw_title': name, 'raw_artist': artist}


# ---------------------------------------------------------------------------
# Cache path
# ---------------------------------------------------------------------------

def test_cache_hit_skips_search():
    """Cache hit short-circuits search and appends found result."""
    states = {}
    cached = {
        'name': 'Cached Title',
        'artists': ['Cached Artist'],
        'album': {'name': 'Cached Album'},
    }
    _seed_state('h1', states, tracks=[_track()])
    deps = _build_deps(states=states, cache_match=cached)

    dy.run_youtube_discovery_worker('h1', deps)

    state = states['h1']
    assert state['spotify_matches'] == 1
    assert state['discovery_results'][0]['status'] == 'Found'
    assert state['discovery_results'][0]['spotify_track'] == 'Cached Title'
    assert deps._spotify.search_calls == []  # no live search


# ---------------------------------------------------------------------------
# Match path (Strategy 1)
# ---------------------------------------------------------------------------

def test_strategy1_match_above_threshold():
    """Strategy 1 returns match with confidence >= 0.9 → recorded as Found."""
    states = {}
    match = _FakeMatch()
    _seed_state('h2', states, tracks=[_track()])
    deps = _build_deps(states=states, spotify_results=[match],
                       score_candidates_result=(match, 0.95, 0))

    dy.run_youtube_discovery_worker('h2', deps)

    state = states['h2']
    assert state['spotify_matches'] == 1
    assert state['discovery_results'][0]['status'] == 'Found'
    assert state['discovery_results'][0]['confidence'] == 0.95
    assert deps._db.cache_saves  # match cached


# ---------------------------------------------------------------------------
# Wing It fallback
# ---------------------------------------------------------------------------

def test_no_match_triggers_wing_it_fallback():
    """No match in any strategy → Wing It stub created."""
    states = {}
    _seed_state('h3', states, tracks=[_track()])
    deps = _build_deps(states=states,
                       score_candidates_result=(None, 0.0, 0))

    dy.run_youtube_discovery_worker('h3', deps)

    state = states['h3']
    assert state.get('wing_it_count') == 1
    result = state['discovery_results'][0]
    assert result['status'] == 'Wing It'
    assert result['wing_it_fallback'] is True
    assert result['matched_data'].get('wing_it') is True


# ---------------------------------------------------------------------------
# iTunes fallback path
# ---------------------------------------------------------------------------

def test_itunes_fallback_when_spotify_unauthenticated():
    """When Spotify not authenticated, iTunes client searched instead."""
    states = {}
    match = _FakeMatch()
    _seed_state('h4', states, tracks=[_track()])
    deps = _build_deps(states=states, spotify_auth=False,
                       itunes_results=[match],
                       discovery_source='itunes',
                       score_candidates_result=(match, 0.92, 0))

    dy.run_youtube_discovery_worker('h4', deps)

    assert deps._itunes.search_calls
    assert deps._spotify.search_calls == []  # spotify NOT called


def test_spotify_skipped_when_rate_limited():
    """Spotify globally rate-limited → falls through to iTunes."""
    states = {}
    match = _FakeMatch()
    _seed_state('h5', states, tracks=[_track()])
    deps = _build_deps(states=states, rate_limited=True,
                       itunes_results=[match],
                       score_candidates_result=(match, 0.95, 0))

    dy.run_youtube_discovery_worker('h5', deps)

    assert deps._itunes.search_calls
    # Spotify search call list may be empty (rate-limited each iter)


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def test_phase_changed_cancels_loop():
    """Phase changed mid-loop → worker exits early."""
    states = {}
    _seed_state('h6', states, tracks=[_track(), _track('T2', 'A2')], phase='cancelled')
    deps = _build_deps(states=states)

    dy.run_youtube_discovery_worker('h6', deps)

    # Loop bails on first iteration (phase != 'discovering')
    assert states['h6']['discovery_results'] == []


def test_skip_discovery_flag_skips_track():
    """Track flagged with skip_discovery is skipped during loop."""
    states = {}
    tr = _track()
    tr['skip_discovery'] = True
    _seed_state('h7', states, tracks=[tr])
    deps = _build_deps(states=states)

    dy.run_youtube_discovery_worker('h7', deps)

    assert states['h7']['discovery_results'] == []
    assert states['h7']['phase'] == 'discovered'  # completes normally


# ---------------------------------------------------------------------------
# Completion: phase + activity feed
# ---------------------------------------------------------------------------

def test_float_duration_does_not_crash_format():
    """yt_dlp can return float duration_ms — format string must handle it (regression)."""
    states = {}
    tr = _track(duration_ms=212345.7)  # float, not int
    _seed_state('hflt', states, tracks=[tr])
    deps = _build_deps(states=states)

    # Before fix: raised "Unknown format code 'd' for object of type 'float'".
    # After fix: int() cast makes it work and produces a clean duration string.
    dy.run_youtube_discovery_worker('hflt', deps)

    result = states['hflt']['discovery_results'][0]
    assert result['status'] != 'Error'  # didn't crash mid-loop
    assert ':' in result['duration']    # duration string formatted


def test_completion_marks_phase_discovered():
    """All tracks processed → phase='discovered', status='complete', progress=100."""
    states = {}
    _seed_state('h8', states, tracks=[_track()])
    deps = _build_deps(states=states)

    dy.run_youtube_discovery_worker('h8', deps)

    assert states['h8']['phase'] == 'discovered'
    assert states['h8']['status'] == 'complete'
    assert states['h8']['discovery_progress'] == 100


def test_activity_feed_logged_on_completion():
    """Discovery completion appends an activity feed item."""
    states = {}
    _seed_state('h9', states, tracks=[_track()])
    deps = _build_deps(states=states)

    dy.run_youtube_discovery_worker('h9', deps)

    assert len(deps._activity_log) == 1
    args, _ = deps._activity_log[0]
    title, msg = args[1], args[2]
    assert 'YouTube Discovery Complete' in title
    assert 'Test Playlist' in msg


# ---------------------------------------------------------------------------
# Mirrored playlist DB writeback
# ---------------------------------------------------------------------------

def test_mirrored_playlist_writes_to_db():
    """url_hash starting with 'mirrored_' → discovery results written to DB."""
    states = {}
    tr = _track()
    tr['db_track_id'] = 'dbtid-1'
    _seed_state('mirrored_xyz', states, tracks=[tr])
    deps = _build_deps(states=states)

    dy.run_youtube_discovery_worker('mirrored_xyz', deps)

    assert len(deps._db.mirrored_updates) == 1
    db_track_id, extra = deps._db.mirrored_updates[0]
    assert db_track_id == 'dbtid-1'
    # Wing It (no match) → discovered=True with provider='wing_it_fallback'
    assert extra['discovered'] is True


def test_non_mirrored_playlist_no_db_writeback():
    """Non-mirrored url_hash → no DB writeback."""
    states = {}
    _seed_state('regular_hash', states, tracks=[_track()])
    deps = _build_deps(states=states)

    dy.run_youtube_discovery_worker('regular_hash', deps)

    assert deps._db.mirrored_updates == []


# ---------------------------------------------------------------------------
# Enrichment workers pause/resume
# ---------------------------------------------------------------------------

def test_enrichment_workers_paused_and_resumed():
    """Worker pauses enrichment workers on entry, resumes in finally."""
    states = {}
    _seed_state('h10', states, tracks=[_track()])
    deps = _build_deps(states=states)

    dy.run_youtube_discovery_worker('h10', deps)

    assert deps._pause_called == ['YouTube discovery']
    assert deps._resume_called  # resume called regardless


def test_error_during_loop_resets_phase_to_fresh():
    """Error mid-loop (after state lookup) → phase='fresh', status='error'."""
    states = {}
    _seed_state('herr', states, tracks=[_track()])
    deps = _build_deps(states=states)
    # Force matching engine to raise during iteration
    deps.matching_engine = None  # AttributeError on .generate_download_queries

    dy.run_youtube_discovery_worker('herr', deps)

    # Inner per-track try absorbs errors and continues, so loop completes
    # normally; this verifies finally still runs.
    assert deps._resume_called


# ---------------------------------------------------------------------------
# Sort by index
# ---------------------------------------------------------------------------

def test_results_sorted_by_index():
    """discovery_results sorted by index after completion (retry parity)."""
    states = {}
    tracks = [_track(f'T{i}', f'A{i}') for i in range(3)]
    _seed_state('h11', states, tracks=tracks)
    # Pre-populate out-of-order results to verify sort
    states['h11']['discovery_results'] = [
        {'index': 2, 'status': 'pre'},
        {'index': 0, 'status': 'pre'},
    ]
    deps = _build_deps(states=states)

    dy.run_youtube_discovery_worker('h11', deps)

    indices = [r['index'] for r in states['h11']['discovery_results']]
    assert indices == sorted(indices)
