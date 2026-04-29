"""Tests for core/discovery/tidal.py — Tidal discovery worker."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.discovery import tidal as dt


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

@dataclass
class _TidalTrack:
    id: str = 'tid-1'
    name: str = 'Track'
    artists: list = None
    album: str = 'Album'
    duration_ms: int = 180000

    def __post_init__(self):
        if self.artists is None:
            self.artists = ['Artist']


class _FakePlaylist:
    def __init__(self, name='My Tidal Playlist', tracks=None):
        self.name = name
        self.tracks = tracks or []


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
    itunes = object()

    deps = dt.TidalDiscoveryDeps(
        tidal_discovery_states=states if states is not None else {},
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
            'name': title, 'artists': [artist], 'duration_ms': dur, 'wing_it': True,
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
        'playlist': _FakePlaylist(tracks=tracks or []),
        'spotify_matches': 0,
        'discovery_results': [],
        'discovery_progress': 0,
    }


# ---------------------------------------------------------------------------
# Cache hit
# ---------------------------------------------------------------------------

def test_cache_hit_short_circuits():
    """Cache hit appends Found result without live search."""
    states = {}
    cached = {'name': 'Cached', 'artists': ['CA'], 'album': {'name': 'CAlb'}}
    _seed_state('p1', states, tracks=[_TidalTrack()])
    deps = _build_deps(states=states, cache_match=cached)

    dt.run_tidal_discovery_worker('p1', deps)

    state = states['p1']
    assert state['spotify_matches'] == 1
    result = state['discovery_results'][0]
    assert result['status'] == 'found'
    assert result['spotify_data'] == cached


# ---------------------------------------------------------------------------
# Spotify path (tuple result)
# ---------------------------------------------------------------------------

def test_spotify_match_preserves_track_disc_numbers():
    """Spotify result tuple → match_data preserves track_number & disc_number."""
    states = {}
    raw = {
        'album': {'name': 'A', 'release_date': '2024-05-05', 'images': [{'url': 'http://i'}]},
        'track_number': 4,
        'disc_number': 2,
    }
    track_obj = _FakeTrackObj()
    _seed_state('p2', states, tracks=[_TidalTrack()])
    deps = _build_deps(states=states, search_result=(track_obj, raw, 0.93))

    dt.run_tidal_discovery_worker('p2', deps)

    md = states['p2']['discovery_results'][0]['match_data']
    assert md['track_number'] == 4
    assert md['disc_number'] == 2
    assert md['album']['release_date'] == '2024-05-05'


# ---------------------------------------------------------------------------
# iTunes path
# ---------------------------------------------------------------------------

def test_itunes_dict_result_path():
    """Non-spotify dict result → match_data with source set to discovery_source."""
    states = {}
    track_data = {
        'id': 'it-1', 'name': 'iT', 'artists': ['iA'],
        'album': {'name': 'iAlb', 'images': [{'url': 'http://it'}]},
        'duration_ms': 200000, 'confidence': 0.85,
    }
    _seed_state('p3', states, tracks=[_TidalTrack()])
    deps = _build_deps(
        states=states, spotify_auth=False, discovery_source='itunes',
        search_result=track_data,
    )

    dt.run_tidal_discovery_worker('p3', deps)

    result = states['p3']['discovery_results'][0]
    assert result['status'] == 'found'
    assert result['confidence'] == 0.85
    assert result['match_data']['source'] == 'itunes'
    assert result['match_data']['image_url'] == 'http://it'


# ---------------------------------------------------------------------------
# Wing It fallback
# ---------------------------------------------------------------------------

def test_no_match_wing_it_fallback():
    """No match → Wing It stub stored, status_class='wing-it'."""
    states = {}
    _seed_state('p4', states, tracks=[_TidalTrack()])
    deps = _build_deps(states=states, search_result=None)

    dt.run_tidal_discovery_worker('p4', deps)

    state = states['p4']
    assert state.get('wing_it_count') == 1
    result = state['discovery_results'][0]
    assert result['status'] == 'found'  # Wing It is also "found" status
    assert result['status_class'] == 'wing-it'
    assert result['wing_it_fallback'] is True


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def test_cancellation_breaks_loop():
    """state['cancelled']=True bails immediately."""
    states = {}
    _seed_state('p5', states, tracks=[_TidalTrack(), _TidalTrack(id='t2')], cancelled=True)
    deps = _build_deps(states=states)

    dt.run_tidal_discovery_worker('p5', deps)

    assert states['p5']['discovery_results'] == []


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

def test_completion_marks_phase_discovered():
    """Completion → phase='discovered', status='discovered', progress=100."""
    states = {}
    _seed_state('p6', states, tracks=[_TidalTrack()])
    deps = _build_deps(states=states, search_result=None)

    dt.run_tidal_discovery_worker('p6', deps)

    assert states['p6']['phase'] == 'discovered'
    assert states['p6']['status'] == 'discovered'
    assert states['p6']['discovery_progress'] == 100


def test_activity_feed_logged():
    """Completion appends activity feed entry mentioning Tidal."""
    states = {}
    _seed_state('p7', states, tracks=[_TidalTrack()])
    deps = _build_deps(states=states, search_result=None)

    dt.run_tidal_discovery_worker('p7', deps)

    args, _ = deps._activity_log[0]
    title = args[1]
    assert 'Tidal Discovery Complete' in title


def test_sync_to_mirrored_invoked():
    """Completion calls sync_discovery_results_to_mirrored with 'tidal' tag."""
    states = {}
    _seed_state('p8', states, tracks=[_TidalTrack()])
    states['p8']['_profile_id'] = 5
    deps = _build_deps(states=states, search_result=None)

    dt.run_tidal_discovery_worker('p8', deps)

    assert len(deps._sync_calls) == 1
    args, kwargs = deps._sync_calls[0]
    assert args[0] == 'tidal'
    assert args[1] == 'p8'
    assert kwargs.get('profile_id') == 5


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------

def test_per_track_error_appends_error_entry():
    """Per-track exception → 'error' result entry, loop continues."""
    states = {}
    tracks = [_TidalTrack(id='a'), _TidalTrack(id='b')]
    _seed_state('p9', states, tracks=tracks)
    deps = _build_deps(states=states)

    call_count = [0]

    def search_side_effect(track, use_spotify, itunes_client):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("track boom")
        return None

    deps.search_spotify_for_tidal_track = search_side_effect

    dt.run_tidal_discovery_worker('p9', deps)

    state = states['p9']
    assert len(state['discovery_results']) == 2
    assert state['discovery_results'][0]['status'] == 'error'
    # Second one falls through to Wing It (no match returned)
    assert state['discovery_results'][1]['status_class'] == 'wing-it'
