"""Tests for core/discovery/deezer.py — Deezer discovery worker."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.discovery import deezer as dd


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

@dataclass
class _FakeTrackObj:
    id: str = 'sp-1'
    name: str = 'Found'
    artists: list = None
    album: str = 'Album X'
    duration_ms: int = 200000
    image_url: str = ''
    external_urls: dict = None
    release_date: str = '2024-01-01'

    def __post_init__(self):
        if self.artists is None:
            self.artists = ['Found Artist']
        if self.external_urls is None:
            self.external_urls = {}


class _FakeSpotifyClient:
    def __init__(self, authenticated=True):
        self._authenticated = authenticated

    def is_spotify_authenticated(self):
        return self._authenticated


class _FakeDB:
    def __init__(self, cache_match=None):
        self._cache_match = cache_match
        self.cache_saves = []

    def get_discovery_cache_match(self, t, a, src):
        return self._cache_match

    def save_discovery_cache_match(self, t, a, src, conf, data, raw_t, raw_a):
        self.cache_saves.append((t, a, src, conf))


def _build_deps(
    *,
    states=None,
    spotify_auth=True,
    discovery_source='spotify',
    cache_match=None,
    search_result=None,
    sync_calls=None,
    activity_log=None,
):
    sync_calls = sync_calls if sync_calls is not None else []
    activity_log = activity_log if activity_log is not None else []
    db = _FakeDB(cache_match=cache_match)
    spotify = _FakeSpotifyClient(authenticated=spotify_auth)
    itunes = object()  # placeholder

    deps = dd.DeezerDiscoveryDeps(
        deezer_discovery_states=states if states is not None else {},
        spotify_client=spotify,
        pause_enrichment_workers=lambda label: {'paused': True},
        resume_enrichment_workers=lambda state, label: None,
        get_active_discovery_source=lambda: discovery_source,
        get_metadata_fallback_client=lambda: itunes,
        get_discovery_cache_key=lambda title, artist: (title.lower(), artist.lower()),
        get_database=lambda: db,
        validate_discovery_cache_artist=lambda artist, m: True,
        search_spotify_for_tidal_track=lambda track, use_spotify, itunes_client: search_result,
        build_discovery_wing_it_stub=lambda title, artist, dur: {
            'name': title, 'artists': [artist], 'duration_ms': dur, 'wing_it': True
        },
        add_activity_item=lambda *a, **kw: activity_log.append((a, kw)),
        sync_discovery_results_to_mirrored=lambda *a, **kw: sync_calls.append((a, kw)),
    )
    deps._db = db
    deps._spotify = spotify
    deps._sync_calls = sync_calls
    deps._activity_log = activity_log
    return deps


def _seed_state(playlist_id, states, *, tracks=None, cancelled=False):
    states[playlist_id] = {
        'cancelled': cancelled,
        'playlist': {'name': 'My Deezer Playlist', 'tracks': tracks or []},
        'spotify_matches': 0,
        'discovery_results': [],
        'discovery_progress': 0,
    }


def _track(track_id=1, name='Track', artists=None, album='Album', duration_ms=180000):
    return {
        'id': track_id,
        'name': name,
        'artists': artists or ['Artist'],
        'album': album,
        'duration_ms': duration_ms,
    }


# ---------------------------------------------------------------------------
# Cache hit
# ---------------------------------------------------------------------------

def test_cache_hit_short_circuits():
    """Discovery cache hit appends Found result, no live search."""
    states = {}
    cached = {
        'id': 'spt-cached',
        'name': 'Cached Track',
        'artists': ['Cached Artist'],
        'album': {'name': 'Cached Album'},
    }
    _seed_state('p1', states, tracks=[_track()])
    deps = _build_deps(states=states, cache_match=cached)

    dd.run_deezer_discovery_worker('p1', deps)

    state = states['p1']
    assert state['spotify_matches'] == 1
    result = state['discovery_results'][0]
    assert result['status'] == 'Found'
    assert result['spotify_track'] == 'Cached Track'


# ---------------------------------------------------------------------------
# Spotify path (tuple result)
# ---------------------------------------------------------------------------

def test_spotify_match_extracts_full_data():
    """Spotify result tuple → builds match_data with track/disc numbers preserved."""
    states = {}
    raw = {
        'album': {'name': 'Spt Album', 'release_date': '2024-05-05', 'images': [{'url': 'http://i'}]},
        'track_number': 4,
        'disc_number': 2,
    }
    track_obj = _FakeTrackObj(name='Found Track', artists=['Found Artist'])
    _seed_state('p2', states, tracks=[_track()])
    deps = _build_deps(states=states, search_result=(track_obj, raw, 0.95))

    dd.run_deezer_discovery_worker('p2', deps)

    state = states['p2']
    result = state['discovery_results'][0]
    assert result['status'] == 'Found'
    assert result['confidence'] == 0.95
    md = result['match_data']
    assert md['track_number'] == 4
    assert md['disc_number'] == 2
    assert md['album']['release_date'] == '2024-05-05'
    assert md['image_url'] == 'http://i'


# ---------------------------------------------------------------------------
# iTunes path (dict result)
# ---------------------------------------------------------------------------

def test_itunes_fallback_dict_result():
    """When use_spotify=False, dict result populates match_data."""
    states = {}
    track_data = {
        'id': 'it-1',
        'name': 'iTunes Match',
        'artists': ['iA'],
        'album': {'name': 'iAlb', 'images': [{'url': 'http://it'}]},
        'duration_ms': 210000,
        'confidence': 0.85,
    }
    _seed_state('p3', states, tracks=[_track()])
    deps = _build_deps(states=states, spotify_auth=False, discovery_source='itunes',
                       search_result=track_data)

    dd.run_deezer_discovery_worker('p3', deps)

    result = states['p3']['discovery_results'][0]
    assert result['status'] == 'Found'
    assert result['confidence'] == 0.85
    assert result['match_data']['source'] == 'itunes'
    assert result['match_data']['image_url'] == 'http://it'


# ---------------------------------------------------------------------------
# Wing It fallback
# ---------------------------------------------------------------------------

def test_no_match_falls_back_to_wing_it():
    """No match (None) → Wing It stub stored, status='Wing It'."""
    states = {}
    _seed_state('p4', states, tracks=[_track(name='Untitled', artists=['A'])])
    deps = _build_deps(states=states, search_result=None)

    dd.run_deezer_discovery_worker('p4', deps)

    state = states['p4']
    assert state.get('wing_it_count') == 1
    result = state['discovery_results'][0]
    assert result['status'] == 'Wing It'
    assert result['wing_it_fallback'] is True


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def test_cancellation_breaks_loop():
    """state['cancelled']=True bails immediately."""
    states = {}
    _seed_state('p5', states, tracks=[_track(), _track(track_id=2)], cancelled=True)
    deps = _build_deps(states=states)

    dd.run_deezer_discovery_worker('p5', deps)

    assert states['p5']['discovery_results'] == []


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

def test_completion_marks_phase_discovered():
    """Completion → phase='discovered', status='discovered', progress=100."""
    states = {}
    _seed_state('p6', states, tracks=[_track()])
    deps = _build_deps(states=states, search_result=None)

    dd.run_deezer_discovery_worker('p6', deps)

    assert states['p6']['phase'] == 'discovered'
    assert states['p6']['status'] == 'discovered'
    assert states['p6']['discovery_progress'] == 100


def test_activity_feed_logged():
    """Completion emits an activity feed entry."""
    states = {}
    _seed_state('p7', states, tracks=[_track()])
    deps = _build_deps(states=states, search_result=None)

    dd.run_deezer_discovery_worker('p7', deps)

    assert len(deps._activity_log) == 1


# ---------------------------------------------------------------------------
# Mirrored sync
# ---------------------------------------------------------------------------

def test_sync_discovery_to_mirrored_called():
    """Completion calls sync_discovery_results_to_mirrored with playlist_id and source."""
    states = {}
    _seed_state('p8', states, tracks=[_track()])
    states['p8']['_profile_id'] = 7
    deps = _build_deps(states=states, search_result=None)

    dd.run_deezer_discovery_worker('p8', deps)

    assert len(deps._sync_calls) == 1
    args, kwargs = deps._sync_calls[0]
    assert args[0] == 'deezer'
    assert args[1] == 'p8'
    assert kwargs.get('profile_id') == 7


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------

def test_error_during_setup_marks_state_error():
    """Exception in main try (state lookup ok) → state phase='error'."""
    states = {}
    _seed_state('perr', states, tracks=[_track()])
    deps = _build_deps(states=states)
    # Force search helper to raise unhandled
    deps.get_active_discovery_source = lambda: (_ for _ in ()).throw(RuntimeError("boom"))

    dd.run_deezer_discovery_worker('perr', deps)

    assert states['perr']['phase'] == 'error'
    assert 'boom' in states['perr']['status']


# ---------------------------------------------------------------------------
# Per-track error
# ---------------------------------------------------------------------------

def test_per_track_error_appends_error_result():
    """Track-level exception → 'Error' result entry, loop continues."""
    states = {}
    tracks = [_track(track_id=1), _track(track_id=2)]
    _seed_state('p9', states, tracks=tracks)
    deps = _build_deps(states=states)

    # First call raises, second returns None (Wing It path)
    call_count = [0]

    def search_side_effect(track, use_spotify, itunes_client):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("track-level boom")
        return None

    deps.search_spotify_for_tidal_track = search_side_effect

    dd.run_deezer_discovery_worker('p9', deps)

    state = states['p9']
    assert len(state['discovery_results']) == 2
    assert state['discovery_results'][0]['status'] == 'Error'
    assert state['discovery_results'][1]['status'] == 'Wing It'
