"""Tests for core/discovery/beatport.py — Beatport chart discovery worker."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.discovery import beatport as db


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

@dataclass
class _FakeMatch:
    id: str = 'spt-1'
    name: str = 'Found'
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
    def generate_download_queries(self, t):
        return [f"{t.artists[0]} {t.name}"]


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
    sync_calls=None,
):
    activity_log = activity_log if activity_log is not None else []
    sync_calls = sync_calls if sync_calls is not None else []
    db_inst = _FakeDB(cache_match=cache_match)
    spotify = _FakeSpotifyClient(results=spotify_results or [], authenticated=spotify_auth)
    itunes = _FakeITunesClient(results=itunes_results or [])

    deps = db.BeatportDiscoveryDeps(
        beatport_chart_states=states if states is not None else {},
        spotify_client=spotify,
        matching_engine=_FakeMatchingEngine(),
        pause_enrichment_workers=lambda label: {'paused': True},
        resume_enrichment_workers=lambda state, label: None,
        get_active_discovery_source=lambda: discovery_source,
        get_metadata_fallback_client=lambda: itunes,
        clean_beatport_text=lambda s: (s or '').strip(),
        get_discovery_cache_key=lambda title, artist: (title.lower(), artist.lower()),
        get_database=lambda: db_inst,
        validate_discovery_cache_artist=lambda artist, m: True,
        spotify_rate_limited=lambda: rate_limited,
        discovery_score_candidates=lambda *args, **kw: score_result,
        get_metadata_cache=lambda: _FakeMetadataCache(),
        build_discovery_wing_it_stub=lambda title, artist: {
            'name': title, 'artists': [artist], 'wing_it': True
        },
        add_activity_item=lambda *a, **kw: activity_log.append((a, kw)),
        sync_discovery_results_to_mirrored=lambda *a, **kw: sync_calls.append((a, kw)),
    )
    deps._db = db_inst
    deps._spotify = spotify
    deps._itunes = itunes
    deps._activity_log = activity_log
    deps._sync_calls = sync_calls
    return deps


def _seed_state(url_hash, states, *, tracks=None, phase='discovering'):
    states[url_hash] = {
        'chart': {'name': 'Top 100', 'tracks': tracks or []},
        'phase': phase,
        'spotify_matches': 0,
        'discovery_results': [],
        'discovery_progress': 0,
    }


def _track(name='Track', artists=None):
    return {'name': name, 'artists': artists or ['Artist']}


# ---------------------------------------------------------------------------
# Cache hit
# ---------------------------------------------------------------------------

def test_cache_hit_short_circuits():
    """Cache hit appends Found result, normalizes artists ['str'] → [{'name'}]."""
    states = {}
    cached = {'name': 'Cached', 'artists': ['CA']}
    _seed_state('h1', states, tracks=[_track()])
    deps = _build_deps(states=states, cache_match=cached)

    db.run_beatport_discovery_worker('h1', deps)

    state = states['h1']
    assert state['spotify_matches'] == 1
    result = state['discovery_results'][0]
    assert result['status'] == 'found'
    assert deps._spotify.search_calls == []  # no live search
    # artists normalized in the cached_match passed by reference
    assert cached['artists'] == [{'name': 'CA'}]


# ---------------------------------------------------------------------------
# Spotify match path
# ---------------------------------------------------------------------------

def test_spotify_match_formats_artists_as_objects():
    """Spotify match → artists formatted as [{'name': str}] for frontend."""
    states = {}
    match = _FakeMatch(artists=['A1', 'A2'])
    _seed_state('h2', states, tracks=[_track()])
    deps = _build_deps(states=states, spotify_results=[match],
                       score_result=(match, 0.95, 0))

    db.run_beatport_discovery_worker('h2', deps)

    result = states['h2']['discovery_results'][0]
    assert result['status'] == 'found'
    assert result['confidence'] == 0.95
    assert result['spotify_data']['artists'] == [{'name': 'A1'}, {'name': 'A2'}]
    assert deps._db.cache_saves  # cached


def test_spotify_artists_string_format_normalized():
    """Spotify result with single string artist → list-of-objects format."""
    states = {}
    match = _FakeMatch(artists='SoloArtist')  # single string
    _seed_state('h3', states, tracks=[_track()])
    deps = _build_deps(states=states, spotify_results=[match],
                       score_result=(match, 0.95, 0))

    db.run_beatport_discovery_worker('h3', deps)

    result = states['h3']['discovery_results'][0]
    assert result['spotify_data']['artists'] == [{'name': 'SoloArtist'}]


# ---------------------------------------------------------------------------
# iTunes match path
# ---------------------------------------------------------------------------

def test_itunes_match_includes_image_url():
    """iTunes match → album.images includes image_url object."""
    states = {}
    match = _FakeMatch(image_url='http://it', name='iName', artists=['iA'])
    _seed_state('h4', states, tracks=[_track()])
    deps = _build_deps(
        states=states, spotify_auth=False, discovery_source='itunes',
        itunes_results=[match], score_result=(match, 0.95, 0),
    )

    db.run_beatport_discovery_worker('h4', deps)

    result = states['h4']['discovery_results'][0]
    assert result['spotify_data']['source'] == 'itunes'
    images = result['spotify_data']['album']['images']
    assert images[0]['url'] == 'http://it'


# ---------------------------------------------------------------------------
# Wing It fallback
# ---------------------------------------------------------------------------

def test_no_match_falls_back_to_wing_it():
    """No high-confidence match → Wing It stub stored."""
    states = {}
    _seed_state('h5', states, tracks=[_track()])
    deps = _build_deps(states=states, score_result=(None, 0.0, 0))

    db.run_beatport_discovery_worker('h5', deps)

    state = states['h5']
    assert state.get('wing_it_count') == 1
    result = state['discovery_results'][0]
    assert result['wing_it_fallback'] is True
    assert result['status_class'] == 'wing-it'


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def test_phase_changed_aborts():
    """state['phase'] != 'discovering' aborts immediately."""
    states = {}
    _seed_state('h6', states, tracks=[_track(), _track('T2')], phase='cancelled')
    deps = _build_deps(states=states)

    db.run_beatport_discovery_worker('h6', deps)

    assert states['h6']['discovery_results'] == []


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

def test_completion_marks_discovered():
    """Completion → phase='discovered', status='discovered', progress=100."""
    states = {}
    _seed_state('h7', states, tracks=[_track()])
    deps = _build_deps(states=states)

    db.run_beatport_discovery_worker('h7', deps)

    assert states['h7']['phase'] == 'discovered'
    assert states['h7']['status'] == 'discovered'
    assert states['h7']['discovery_progress'] == 100


def test_activity_feed_logged():
    """Completion appends activity feed entry mentioning Beatport Discovery Complete."""
    states = {}
    _seed_state('h8', states, tracks=[_track()])
    deps = _build_deps(states=states)

    db.run_beatport_discovery_worker('h8', deps)

    args, _ = deps._activity_log[0]
    title = args[1]
    assert 'Beatport Discovery Complete' in title


# ---------------------------------------------------------------------------
# Mirrored sync
# ---------------------------------------------------------------------------

def test_sync_to_mirrored_invoked():
    """Completion calls sync_discovery_results_to_mirrored with 'beatport' tag."""
    states = {}
    _seed_state('h9', states, tracks=[_track()])
    states['h9']['_profile_id'] = 3
    deps = _build_deps(states=states)

    db.run_beatport_discovery_worker('h9', deps)

    assert len(deps._sync_calls) == 1
    args, kwargs = deps._sync_calls[0]
    assert args[0] == 'beatport'
    assert args[1] == 'h9'
    assert kwargs.get('profile_id') == 3


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_top_level_error_marks_state():
    """Exception in main try (state lookup ok) → phase='fresh', status='error'."""
    states = {}
    _seed_state('herr', states, tracks=[_track()])
    deps = _build_deps(states=states)
    deps.get_active_discovery_source = lambda: (_ for _ in ()).throw(RuntimeError("boom"))

    db.run_beatport_discovery_worker('herr', deps)

    assert states['herr']['phase'] == 'fresh'
    assert states['herr']['status'] == 'error'


def test_per_track_error_appends_error_entry():
    """Per-track exception → 'error' result entry appended, loop continues."""
    states = {}
    _seed_state('h10', states, tracks=[_track('A'), _track('B')])
    deps = _build_deps(states=states)

    call_count = [0]

    def raising_score(*args, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("score boom")
        return (None, 0.0, 0)

    deps.discovery_score_candidates = raising_score

    db.run_beatport_discovery_worker('h10', deps)

    state = states['h10']
    # First track errored mid-search but loop continues; second processes Wing It path
    assert len(state['discovery_results']) == 2


# ---------------------------------------------------------------------------
# Artist normalization (Beatport "CID,Taylr Renee" comma-split)
# ---------------------------------------------------------------------------

def test_comma_separated_artists_split_to_first():
    """Beatport 'CID,Taylr Renee' single-string-artists → first artist used."""
    states = {}
    track = _track(name='ABC', artists=['CID,Taylr Renee'])
    _seed_state('h11', states, tracks=[track])
    deps = _build_deps(states=states)

    db.run_beatport_discovery_worker('h11', deps)

    result = states['h11']['discovery_results'][0]
    assert result['beatport_track']['artist'] == 'CID'
