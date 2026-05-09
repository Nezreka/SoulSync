"""Tests for core/discovery/quality_scanner.py — library quality scanner."""

from __future__ import annotations

import threading
from dataclasses import dataclass

import pytest

from core.discovery import quality_scanner as qs


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

@dataclass
class _FakeSpotifyTrack:
    id: str = 'spt-1'
    name: str = 'Found'
    artists: list = None
    album: str = 'Found Album'
    duration_ms: int = 200000
    popularity: int = 50
    preview_url: str = ''
    external_urls: dict = None
    album_type: str = 'album'
    release_date: str = '2024-01-01'

    def __post_init__(self):
        if self.artists is None:
            self.artists = ['Found Artist']
        if self.external_urls is None:
            self.external_urls = {}


class _FakeMetadataClient:
    def __init__(self, results=None):
        self._results = results if results is not None else []
        self.search_calls = []

    def search_tracks(self, query, limit=5, allow_fallback=True):
        self.search_calls.append((query, limit, allow_fallback))
        return self._results


_TEST_PRIMARY_SOURCE = 'spotify'
_TEST_SOURCE_CLIENTS = {}


@pytest.fixture(autouse=True)
def _patch_source_resolution(monkeypatch):
    monkeypatch.setattr(qs, 'get_primary_source', lambda: _TEST_PRIMARY_SOURCE)
    monkeypatch.setattr(qs, 'get_client_for_source', lambda source, **_kwargs: _TEST_SOURCE_CLIENTS.get(source))
    monkeypatch.setattr(qs.time, 'sleep', lambda *_args, **_kwargs: None)
    yield
    _TEST_SOURCE_CLIENTS.clear()
    globals()['_TEST_PRIMARY_SOURCE'] = 'spotify'


class _FakeMatchingEngine:
    def generate_download_queries(self, t):
        return [f"{t.artists[0]} {t.name}"]

    def normalize_string(self, s):
        return (s or '').lower().strip()

    def similarity_score(self, a, b):
        if a == b:
            return 1.0
        if not a or not b:
            return 0.0
        return 0.95 if a in b or b in a else 0.0


class _MultiQueryMatchingEngine(_FakeMatchingEngine):
    def generate_download_queries(self, t):
        return [
            f"{t.artists[0]} {t.name} first",
            f"{t.artists[0]} {t.name} second",
        ]


class _FakeAutomationEngine:
    def __init__(self):
        self.events = []

    def emit(self, event_type, data):
        self.events.append((event_type, data))


class _FakeWishlistService:
    def __init__(self):
        self.added = []

    def add_spotify_track_to_wishlist(self, **kwargs):
        self.added.append(kwargs)
        return True


class _FakeMusicDB:
    def __init__(self, watchlist_artists=None, tracks=None, profile=None):
        self._watchlist_artists = watchlist_artists if watchlist_artists is not None else []
        self._tracks = tracks if tracks is not None else []
        self._profile = profile or {'qualities': {'flac': {'enabled': True}}}

    def get_quality_profile(self):
        return self._profile

    def get_watchlist_artists(self, profile_id=1):
        return self._watchlist_artists

    def _get_connection(self):
        rows = self._tracks
        return _FakeConn(rows)


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, query, params=None):
        return _FakeCursor(self._rows)

    def close(self):
        pass


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


@dataclass
class _WatchlistArtist:
    artist_name: str


def _build_deps(
    *,
    state=None,
    source_clients=None,
    primary_source='spotify',
    quality_tier_result=('lossless', 1),
    automation=None,
):
    globals()['_TEST_PRIMARY_SOURCE'] = primary_source
    _TEST_SOURCE_CLIENTS.clear()
    if source_clients is not None:
        _TEST_SOURCE_CLIENTS.update(source_clients)
    elif primary_source:
        _TEST_SOURCE_CLIENTS[primary_source] = _FakeMetadataClient(results=[])

    deps = qs.QualityScannerDeps(
        quality_scanner_state=state if state is not None else {},
        quality_scanner_lock=threading.Lock(),
        QUALITY_TIERS={'lossless': {'tier': 1}, 'low_lossy': {'tier': 4}, 'lossy': {'tier': 3}},
        matching_engine=_FakeMatchingEngine(),
        automation_engine=automation or _FakeAutomationEngine(),
        get_quality_tier_from_extension=lambda fp: quality_tier_result,
        add_activity_item=lambda *a, **kw: None,
    )
    return deps


def _track_row(track_id=1, title='Track', artist_id=1, album_id=1,
               file_path='/x.mp3', bitrate=128, artist_name='Artist',
               album_title='Album'):
    return (track_id, title, artist_id, album_id, file_path, bitrate, artist_name, album_title)


@pytest.fixture
def mock_db_and_wishlist(monkeypatch):
    """Patches MusicDatabase and get_wishlist_service used inside the worker."""
    db = _FakeMusicDB()
    ws = _FakeWishlistService()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)
    monkeypatch.setattr('core.wishlist_service.get_wishlist_service', lambda: ws)
    return db, ws


# ---------------------------------------------------------------------------
# State init + DB load
# ---------------------------------------------------------------------------

def test_state_initialized_on_run(mock_db_and_wishlist):
    """Scanner resets state to running with cleared counters."""
    db, _ = mock_db_and_wishlist
    db._watchlist_artists = []  # no artists → exits early but after init
    state = {}
    deps = _build_deps(state=state)

    qs.run_quality_scanner('watchlist', 1, deps)

    assert state['status'] == 'finished'  # exited early since no artists
    assert state['error_message'] == 'Please add artists to watchlist first'


def test_no_watchlist_artists_short_circuit(mock_db_and_wishlist):
    """Scope=watchlist with no artists → status=finished, error message."""
    db, _ = mock_db_and_wishlist
    db._watchlist_artists = []
    state = {}
    deps = _build_deps(state=state)

    qs.run_quality_scanner('watchlist', 1, deps)

    assert state['status'] == 'finished'
    assert 'add artists' in state['error_message']


# ---------------------------------------------------------------------------
# Provider availability gate
# ---------------------------------------------------------------------------

def test_no_available_provider_marks_error(mock_db_and_wishlist):
    """No available metadata providers → state['status']='error'."""
    db, _ = mock_db_and_wishlist
    db._watchlist_artists = [_WatchlistArtist('A')]
    db._tracks = [_track_row()]
    state = {}
    deps = _build_deps(state=state, source_clients={}, quality_tier_result=('low_lossy', 4))

    qs.run_quality_scanner('watchlist', 1, deps)

    assert state['status'] == 'error'
    assert 'metadata provider' in state['error_message'].lower()


# ---------------------------------------------------------------------------
# Quality tier check + skip
# ---------------------------------------------------------------------------

def test_high_quality_tracks_skipped(mock_db_and_wishlist):
    """Tracks meeting quality (tier_num <= min_acceptable) → quality_met += 1."""
    db, _ = mock_db_and_wishlist
    db._watchlist_artists = [_WatchlistArtist('A')]
    db._tracks = [_track_row(file_path='/x.flac')]
    state = {}
    # Default min_acceptable is from {flac: enabled} → tier 1 (lossless)
    # quality_tier_result=('lossless', 1) → 1 <= 1 → skip
    deps = _build_deps(state=state, quality_tier_result=('lossless', 1))

    qs.run_quality_scanner('watchlist', 1, deps)

    assert state['quality_met'] == 1
    assert state['low_quality'] == 0


def test_low_quality_tracks_attempted(mock_db_and_wishlist):
    """Low-quality tracks (tier_num > min) trigger a metadata search."""
    db, _ = mock_db_and_wishlist
    db._watchlist_artists = [_WatchlistArtist('Artist')]
    db._tracks = [_track_row(file_path='/x.mp3', artist_name='Artist', title='Track')]
    state = {}
    match = _FakeSpotifyTrack(name='Track', artists=['Artist'])
    spotify_client = _FakeMetadataClient(results=[match])
    deps = _build_deps(
        state=state,
        quality_tier_result=('low_lossy', 4),
        source_clients={'spotify': spotify_client},
        primary_source='spotify',
    )

    qs.run_quality_scanner('watchlist', 1, deps)

    assert state['low_quality'] == 1
    assert spotify_client.search_calls
    assert spotify_client.search_calls[0][2] is False


def test_client_lookup_happens_per_query(mock_db_and_wishlist, monkeypatch):
    """Each generated query re-resolves the source client."""
    db, _ = mock_db_and_wishlist
    db._watchlist_artists = [_WatchlistArtist('Artist')]
    db._tracks = [_track_row(file_path='/x.mp3', artist_name='Artist', title='Track')]
    state = {}
    spotify_client = _FakeMetadataClient(results=[])
    lookups = []

    monkeypatch.setattr(
        qs,
        'get_client_for_source',
        lambda source, **_kwargs: (lookups.append(source), _TEST_SOURCE_CLIENTS.get(source))[1],
    )

    deps = _build_deps(
        state=state,
        quality_tier_result=('low_lossy', 4),
        source_clients={'spotify': spotify_client},
        primary_source='spotify',
    )
    deps.matching_engine = _MultiQueryMatchingEngine()

    qs.run_quality_scanner('watchlist', 1, deps)

    assert len(lookups) % 2 == 0
    midpoint = len(lookups) // 2
    assert lookups[:midpoint] == lookups[midpoint:]
    assert len(spotify_client.search_calls) == 2


def test_low_quality_tracks_follow_source_priority(mock_db_and_wishlist):
    """Primary source is searched before Spotify."""
    db, ws = mock_db_and_wishlist
    db._watchlist_artists = [_WatchlistArtist('Artist')]
    db._tracks = [_track_row(file_path='/x.mp3', artist_name='Artist', title='Track')]
    state = {}
    match = _FakeSpotifyTrack(name='Track', artists=['Artist'])
    deezer_client = _FakeMetadataClient(results=[match])
    spotify_client = _FakeMetadataClient(results=[])
    deps = _build_deps(
        state=state,
        source_clients={'deezer': deezer_client, 'spotify': spotify_client},
        primary_source='deezer',
        quality_tier_result=('low_lossy', 4),
    )

    qs.run_quality_scanner('watchlist', 1, deps)

    assert state['matched'] == 1
    assert deezer_client.search_calls
    assert spotify_client.search_calls == []
    assert len(ws.added) == 1
    add_args = ws.added[0]
    assert add_args['track_data']['source'] == 'deezer'


# ---------------------------------------------------------------------------
# Match → wishlist add
# ---------------------------------------------------------------------------

def test_match_adds_to_wishlist(mock_db_and_wishlist):
    """High-confidence match → wishlist_service.add_spotify_track_to_wishlist called."""
    db, ws = mock_db_and_wishlist
    db._watchlist_artists = [_WatchlistArtist('Artist')]
    db._tracks = [_track_row(artist_name='Artist', title='Track', file_path='/x.mp3', bitrate=128)]
    state = {}
    match = _FakeSpotifyTrack(name='Track', artists=['Artist'])
    deps = _build_deps(
        state=state,
        quality_tier_result=('low_lossy', 4),
        source_clients={'spotify': _FakeMetadataClient(results=[match])},
        primary_source='spotify',
    )

    qs.run_quality_scanner('watchlist', 1, deps)

    assert state['matched'] == 1
    assert len(ws.added) == 1
    add_args = ws.added[0]
    assert add_args['source_type'] == 'quality_scanner'
    assert add_args['source_context']['original_file_path'] == '/x.mp3'


def test_match_preserves_album_and_artist_images(mock_db_and_wishlist):
    """Image metadata from the provider payload should survive the wishlist handoff."""
    db, ws = mock_db_and_wishlist
    db._watchlist_artists = [_WatchlistArtist('Artist')]
    db._tracks = [_track_row(artist_name='Artist', title='Track', file_path='/x.mp3', bitrate=128)]
    state = {}
    match = {
        'id': 'sp-1',
        'name': 'Track',
        'artists': [{'name': 'Artist', 'image_url': 'https://example.test/artist.jpg'}],
        'album': 'Album',
        'image_url': 'https://example.test/cover.jpg',
        'duration_ms': 200000,
        'popularity': 50,
        'external_urls': {},
        'album_type': 'album',
        'release_date': '2024-01-01',
    }
    deps = _build_deps(
        state=state,
        quality_tier_result=('low_lossy', 4),
        source_clients={'spotify': _FakeMetadataClient(results=[match])},
        primary_source='spotify',
    )

    qs.run_quality_scanner('watchlist', 1, deps)

    assert state['matched'] == 1
    assert len(ws.added) == 1
    add_args = ws.added[0]
    assert add_args['track_data']['image_url'] == 'https://example.test/cover.jpg'
    assert add_args['track_data']['album']['image_url'] == 'https://example.test/cover.jpg'
    assert add_args['track_data']['album']['images'] == [{'url': 'https://example.test/cover.jpg'}]
    assert add_args['track_data']['artists'][0]['image_url'] == 'https://example.test/artist.jpg'


def test_no_match_no_wishlist_add(mock_db_and_wishlist):
    """No match found → no wishlist add, matched stays 0."""
    db, ws = mock_db_and_wishlist
    db._watchlist_artists = [_WatchlistArtist('A')]
    db._tracks = [_track_row(artist_name='A', title='Z', file_path='/x.mp3')]
    state = {}
    # No spotify results → no match
    deps = _build_deps(
        state=state,
        quality_tier_result=('low_lossy', 4),
        source_clients={'spotify': _FakeMetadataClient(results=[])},
        primary_source='spotify',
    )

    qs.run_quality_scanner('watchlist', 1, deps)

    assert state['matched'] == 0
    assert ws.added == []


# ---------------------------------------------------------------------------
# Stop request gate
# ---------------------------------------------------------------------------

def test_stop_request_halts_loop(mock_db_and_wishlist):
    """Setting state['status'] != 'running' mid-loop halts processing."""
    db, _ = mock_db_and_wishlist
    db._watchlist_artists = [_WatchlistArtist('A')]
    db._tracks = [_track_row(track_id=1), _track_row(track_id=2)]
    state = {}
    deps = _build_deps(state=state, quality_tier_result=('lossless', 1))

    # Override get_quality_tier_from_extension to set stop after first track
    call_count = [0]

    def stop_after_first(fp):
        call_count[0] += 1
        if call_count[0] == 1:
            # Set status to non-running BEFORE second track iter checks
            with deps.quality_scanner_lock:
                state['status'] = 'stopping'
        return ('lossless', 1)

    deps.get_quality_tier_from_extension = stop_after_first

    qs.run_quality_scanner('watchlist', 1, deps)

    # Only first track processed
    assert state['quality_met'] == 1


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

def test_completion_marks_finished(mock_db_and_wishlist):
    """All tracks processed → status='finished', progress=100."""
    db, _ = mock_db_and_wishlist
    db._watchlist_artists = [_WatchlistArtist('A')]
    db._tracks = [_track_row()]
    state = {}
    deps = _build_deps(state=state)

    qs.run_quality_scanner('watchlist', 1, deps)

    assert state['status'] == 'finished'
    assert state['progress'] == 100


def test_automation_event_emitted(mock_db_and_wishlist):
    """Successful completion emits 'quality_scan_completed' on automation engine."""
    db, _ = mock_db_and_wishlist
    db._watchlist_artists = [_WatchlistArtist('A')]
    db._tracks = [_track_row()]
    automation = _FakeAutomationEngine()
    state = {}
    deps = _build_deps(state=state, automation=automation)

    qs.run_quality_scanner('watchlist', 1, deps)

    assert any(name == 'quality_scan_completed' for name, _ in automation.events)


# ---------------------------------------------------------------------------
# All-library scope
# ---------------------------------------------------------------------------

def test_scope_all_loads_all_tracks(mock_db_and_wishlist):
    """scope != 'watchlist' loads all tracks (no watchlist filter)."""
    db, _ = mock_db_and_wishlist
    db._tracks = [_track_row(track_id=1), _track_row(track_id=2)]
    state = {}
    deps = _build_deps(state=state)

    qs.run_quality_scanner('all', 1, deps)

    assert state['total'] == 2
