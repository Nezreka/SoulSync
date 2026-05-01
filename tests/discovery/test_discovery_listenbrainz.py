"""Tests for core/discovery/listenbrainz.py — ListenBrainz discovery worker."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.discovery import listenbrainz as dl


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
    release_date: str = '2024-01-01'

    def __post_init__(self):
        if self.artists is None:
            self.artists = ['Found Artist']


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
    def generate_download_queries(self, track):
        return [f"{track.artists[0]} {track.name}"]


class _FakeDB:
    def __init__(self, cache_match=None):
        self._cache_match = cache_match
        self.cache_saves = []

    def get_discovery_cache_match(self, t, a, src):
        return self._cache_match

    def save_discovery_cache_match(self, t, a, src, conf, data, raw_t, raw_a):
        self.cache_saves.append((t, a, src, conf))


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
    rate_limited=False,
    score_result=(None, 0.0, 0),
    activity_log=None,
):
    activity_log = activity_log if activity_log is not None else []
    db = _FakeDB(cache_match=cache_match)
    spotify = _FakeSpotifyClient(results=spotify_results or [], authenticated=spotify_auth)
    itunes = _FakeITunesClient(results=itunes_results or [])

    deps = dl.ListenbrainzDiscoveryDeps(
        listenbrainz_playlist_states=states if states is not None else {},
        spotify_client=spotify,
        matching_engine=_FakeMatchingEngine(),
        pause_enrichment_workers=lambda label: {'paused': True},
        resume_enrichment_workers=lambda state, label: None,
        get_active_discovery_source=lambda: discovery_source,
        get_metadata_fallback_client=lambda: itunes,
        get_discovery_cache_key=lambda title, artist: (title.lower(), artist.lower()),
        get_database=lambda: db,
        validate_discovery_cache_artist=lambda artist, m: True,
        extract_artist_name=lambda a: a if isinstance(a, str) else a.get('name', ''),
        spotify_rate_limited=lambda: rate_limited,
        discovery_score_candidates=lambda *args, **kw: score_result,
        get_metadata_cache=lambda: _FakeMetadataCache(),
        build_discovery_wing_it_stub=lambda title, artist, dur: {
            'name': title, 'artists': [artist], 'duration_ms': dur, 'wing_it': True
        },
        add_activity_item=lambda *a, **kw: activity_log.append((a, kw)),
    )
    deps._db = db
    deps._spotify = spotify
    deps._itunes = itunes
    deps._activity_log = activity_log
    return deps


def _seed_state(state_key, states, *, tracks=None, phase='discovering'):
    states[state_key] = {
        'phase': phase,
        'playlist': {'name': 'My LB Playlist', 'tracks': tracks or []},
        'spotify_matches': 0,
        'discovery_results': [],
        'discovery_progress': 0,
    }


def _track(track_name='LB Track', artist_name='LB Artist', album_name='LB Album', duration_ms=180000):
    return {
        'track_name': track_name,
        'artist_name': artist_name,
        'album_name': album_name,
        'duration_ms': duration_ms,
    }


# ---------------------------------------------------------------------------
# Cache hit
# ---------------------------------------------------------------------------

def test_cache_hit_short_circuits():
    """Cache hit appends Found result, no live search."""
    states = {}
    cached = {
        'name': 'Cached Match',
        'artists': ['Cached Artist'],
        'album': {'name': 'Cached Album'},
    }
    _seed_state('lb1', states, tracks=[_track()])
    deps = _build_deps(states=states, cache_match=cached)

    dl.run_listenbrainz_discovery_worker('lb1', deps)

    state = states['lb1']
    assert state['spotify_matches'] == 1
    result = state['discovery_results'][0]
    assert result['status'] == 'Found'
    assert result['spotify_track'] == 'Cached Match'
    assert deps._spotify.search_calls == []


# ---------------------------------------------------------------------------
# Strategy 1 match
# ---------------------------------------------------------------------------

def test_strategy1_match_records_found():
    """Strategy 1 match >= 0.9 → result['status']='Found' with confidence."""
    states = {}
    match = _FakeMatch()
    _seed_state('lb2', states, tracks=[_track()])
    deps = _build_deps(states=states, spotify_results=[match],
                       score_result=(match, 0.95, 0))

    dl.run_listenbrainz_discovery_worker('lb2', deps)

    state = states['lb2']
    assert state['spotify_matches'] == 1
    result = state['discovery_results'][0]
    assert result['status'] == 'Found'
    assert result['confidence'] == 0.95
    assert deps._db.cache_saves


# ---------------------------------------------------------------------------
# Wing It fallback
# ---------------------------------------------------------------------------

def test_no_match_wing_it_fallback():
    """No match in any strategy → Wing It stub."""
    states = {}
    _seed_state('lb3', states, tracks=[_track()])
    deps = _build_deps(states=states)

    dl.run_listenbrainz_discovery_worker('lb3', deps)

    state = states['lb3']
    assert state.get('wing_it_count') == 1
    result = state['discovery_results'][0]
    assert result['status'] == 'Wing It'
    assert result['wing_it_fallback'] is True


# ---------------------------------------------------------------------------
# iTunes fallback
# ---------------------------------------------------------------------------

def test_itunes_fallback_when_spotify_unauthenticated():
    """spotify unauthenticated → iTunes searched."""
    states = {}
    match = _FakeMatch()
    _seed_state('lb4', states, tracks=[_track()])
    deps = _build_deps(
        states=states, spotify_auth=False, discovery_source='itunes',
        itunes_results=[match], score_result=(match, 0.95, 0),
    )

    dl.run_listenbrainz_discovery_worker('lb4', deps)

    assert deps._itunes.search_calls
    assert deps._spotify.search_calls == []


def test_spotify_skipped_when_rate_limited():
    """Spotify globally rate-limited → falls through to iTunes."""
    states = {}
    match = _FakeMatch()
    _seed_state('lb5', states, tracks=[_track()])
    deps = _build_deps(states=states, rate_limited=True,
                       itunes_results=[match],
                       score_result=(match, 0.95, 0))

    dl.run_listenbrainz_discovery_worker('lb5', deps)

    assert deps._itunes.search_calls


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def test_phase_changed_cancels_loop():
    """Phase changed → worker exits early."""
    states = {}
    _seed_state('lb6', states, tracks=[_track(), _track('T2')], phase='cancelled')
    deps = _build_deps(states=states)

    dl.run_listenbrainz_discovery_worker('lb6', deps)

    assert states['lb6']['discovery_results'] == []


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

def test_completion_marks_phase_discovered():
    """All tracks processed → phase='discovered', status='complete', progress=100."""
    states = {}
    _seed_state('lb7', states, tracks=[_track()])
    deps = _build_deps(states=states)

    dl.run_listenbrainz_discovery_worker('lb7', deps)

    assert states['lb7']['phase'] == 'discovered'
    assert states['lb7']['status'] == 'complete'
    assert states['lb7']['discovery_progress'] == 100


def test_activity_feed_logged():
    """Completion appends activity feed entry mentioning ListenBrainz Discovery Complete."""
    states = {}
    _seed_state('lb8', states, tracks=[_track()])
    deps = _build_deps(states=states)

    dl.run_listenbrainz_discovery_worker('lb8', deps)

    args, _ = deps._activity_log[0]
    title = args[1]
    assert 'ListenBrainz Discovery Complete' in title


# ---------------------------------------------------------------------------
# Per-track error
# ---------------------------------------------------------------------------

def test_per_track_error_appends_error_result():
    """Track-level exception (outside strategies' inner try) → 'Error' result, loop continues."""
    states = {}
    _seed_state('lb9', states, tracks=[_track('A'), _track('B')])
    deps = _build_deps(states=states)

    call_count = [0]

    # Raising in get_discovery_cache_key bubbles past strategies' inner try/except.
    def raising_cache_key(title, artist):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("boom")
        return (title.lower(), artist.lower())

    deps.get_discovery_cache_key = raising_cache_key

    dl.run_listenbrainz_discovery_worker('lb9', deps)

    state = states['lb9']
    assert len(state['discovery_results']) == 2
    assert state['discovery_results'][0]['status'] == 'Error'
    assert state['discovery_results'][1]['status'] == 'Wing It'


# ---------------------------------------------------------------------------
# Float duration_ms tolerance (regression for :02d format)
# ---------------------------------------------------------------------------

def test_float_duration_does_not_crash():
    """yt_dlp/LB can pass float durations — int() cast prevents :02d crash."""
    states = {}
    track = _track(duration_ms=212345.7)  # float
    _seed_state('lb10', states, tracks=[track])
    deps = _build_deps(states=states)

    dl.run_listenbrainz_discovery_worker('lb10', deps)

    result = states['lb10']['discovery_results'][0]
    assert result['status'] != 'Error'
    assert ':' in result['duration']


# ---------------------------------------------------------------------------
# Resume enrichment workers always
# ---------------------------------------------------------------------------

def test_resume_enrichment_called_on_completion():
    """Successful run → resume_enrichment_workers called via finally."""
    states = {}
    _seed_state('lb11', states, tracks=[_track()])
    resumes = []
    deps = _build_deps(states=states)
    deps.resume_enrichment_workers = lambda state, label: resumes.append((state, label))

    dl.run_listenbrainz_discovery_worker('lb11', deps)

    assert resumes  # finally fired
