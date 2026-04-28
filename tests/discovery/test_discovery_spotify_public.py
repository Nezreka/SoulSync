"""Tests for core/discovery/spotify_public.py — Spotify Public link discovery."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.discovery import spotify_public as dsp


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
    activity_log=None,
):
    activity_log = activity_log if activity_log is not None else []
    db = _FakeDB(cache_match=cache_match)
    spotify = _FakeSpotifyClient(authenticated=spotify_auth)
    itunes = object()

    deps = dsp.SpotifyPublicDiscoveryDeps(
        spotify_public_discovery_states=states if states is not None else {},
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
    )
    deps._db = db
    deps._spotify = spotify
    deps._activity_log = activity_log
    return deps


def _seed_state(url_hash, states, *, tracks=None, cancelled=False):
    states[url_hash] = {
        'cancelled': cancelled,
        'playlist': {'name': 'Public Playlist', 'tracks': tracks or []},
        'spotify_matches': 0,
        'discovery_results': [],
        'discovery_progress': 0,
    }


def _track(track_id='id1', name='Track', artists=None, album='Album', duration_ms=180000):
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
    """Cache hit appends Found result without live search."""
    states = {}
    cached = {
        'id': 'spt-c',
        'name': 'Cached',
        'artists': ['CA'],
        'album': {'name': 'CAlb'},
    }
    _seed_state('h1', states, tracks=[_track()])
    deps = _build_deps(states=states, cache_match=cached)

    dsp.run_spotify_public_discovery_worker('h1', deps)

    state = states['h1']
    assert state['spotify_matches'] == 1
    result = state['discovery_results'][0]
    assert result['status'] == 'Found'
    assert result['spotify_track'] == 'Cached'


def test_dict_artists_normalized_to_strings():
    """Artists with dict format normalize to plain strings during track unpack."""
    states = {}
    track = {
        'id': 'd1',
        'name': 'T',
        'artists': [{'name': 'A1'}, 'A2'],
        'album': {'name': 'X'},
        'duration_ms': 1000,
    }
    _seed_state('h2', states, tracks=[track])
    deps = _build_deps(states=states, search_result=None)

    dsp.run_spotify_public_discovery_worker('h2', deps)

    result = states['h2']['discovery_results'][0]
    sp = result['spotify_public_track']
    assert sp['artists'] == ['A1', 'A2']


# ---------------------------------------------------------------------------
# Spotify path (tuple result)
# ---------------------------------------------------------------------------

def test_spotify_match_preserves_track_disc_numbers():
    """Spotify result tuple → match_data preserves track_number & disc_number."""
    states = {}
    raw = {
        'album': {'name': 'A', 'release_date': '2024-05-05', 'images': [{'url': 'http://i'}]},
        'track_number': 3,
        'disc_number': 1,
    }
    track_obj = _FakeTrackObj()
    _seed_state('h3', states, tracks=[_track()])
    deps = _build_deps(states=states, search_result=(track_obj, raw, 0.92))

    dsp.run_spotify_public_discovery_worker('h3', deps)

    md = states['h3']['discovery_results'][0]['match_data']
    assert md['track_number'] == 3
    assert md['disc_number'] == 1
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
    _seed_state('h4', states, tracks=[_track()])
    deps = _build_deps(
        states=states, spotify_auth=False, discovery_source='itunes',
        search_result=track_data,
    )

    dsp.run_spotify_public_discovery_worker('h4', deps)

    result = states['h4']['discovery_results'][0]
    assert result['confidence'] == 0.85
    assert result['match_data']['source'] == 'itunes'
    assert result['match_data']['image_url'] == 'http://it'


# ---------------------------------------------------------------------------
# Wing It fallback
# ---------------------------------------------------------------------------

def test_no_match_wing_it_fallback():
    """No match → Wing It stub stored, status='Wing It'."""
    states = {}
    _seed_state('h5', states, tracks=[_track()])
    deps = _build_deps(states=states, search_result=None)

    dsp.run_spotify_public_discovery_worker('h5', deps)

    state = states['h5']
    assert state.get('wing_it_count') == 1
    result = state['discovery_results'][0]
    assert result['status'] == 'Wing It'
    assert result['wing_it_fallback'] is True


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def test_cancellation_breaks_loop():
    """state['cancelled']=True bails on first iteration."""
    states = {}
    _seed_state('h6', states, tracks=[_track(), _track('id2')], cancelled=True)
    deps = _build_deps(states=states)

    dsp.run_spotify_public_discovery_worker('h6', deps)

    assert states['h6']['discovery_results'] == []


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

def test_completion_marks_phase_discovered():
    """Completion → phase + status = 'discovered', progress=100."""
    states = {}
    _seed_state('h7', states, tracks=[_track()])
    deps = _build_deps(states=states, search_result=None)

    dsp.run_spotify_public_discovery_worker('h7', deps)

    assert states['h7']['phase'] == 'discovered'
    assert states['h7']['status'] == 'discovered'
    assert states['h7']['discovery_progress'] == 100


def test_activity_feed_logged():
    """Completion logs activity feed entry with 'Spotify Link Discovery Complete'."""
    states = {}
    _seed_state('h8', states, tracks=[_track()])
    deps = _build_deps(states=states, search_result=None)

    dsp.run_spotify_public_discovery_worker('h8', deps)

    args, _ = deps._activity_log[0]
    title = args[1]
    assert 'Spotify Link Discovery Complete' in title


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------

def test_top_level_error_marks_state_error():
    """Exception in main try → phase='error', status with error string."""
    states = {}
    _seed_state('herr', states, tracks=[_track()])
    deps = _build_deps(states=states)
    deps.get_active_discovery_source = lambda: (_ for _ in ()).throw(RuntimeError("boom"))

    dsp.run_spotify_public_discovery_worker('herr', deps)

    assert states['herr']['phase'] == 'error'
    assert 'boom' in states['herr']['status']


def test_per_track_error_appends_error_result():
    """Per-track exception → 'Error' result entry, loop continues."""
    states = {}
    tracks = [_track('a'), _track('b')]
    _seed_state('h9', states, tracks=tracks)
    deps = _build_deps(states=states)

    call_count = [0]

    def search_side_effect(track, use_spotify, itunes_client):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("track boom")
        return None

    deps.search_spotify_for_tidal_track = search_side_effect

    dsp.run_spotify_public_discovery_worker('h9', deps)

    state = states['h9']
    assert len(state['discovery_results']) == 2
    assert state['discovery_results'][0]['status'] == 'Error'
    assert state['discovery_results'][1]['status'] == 'Wing It'
