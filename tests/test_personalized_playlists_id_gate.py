"""Tests for the lifted PersonalizedPlaylistsService selectors and the
mandatory ID-validity gate every section must enforce.

Context: discover-page selection methods used to return tracks/albums
with all source IDs NULL — the UI displayed them, the user clicked
download, the download silently failed because there was nothing to
look up. This test file pins the gate at the helper level so a future
section can't accidentally bypass it.

Coverage:
- `_select_discovery_tracks` filters out rows where every source ID is NULL
- `_select_discovery_tracks` honors source filter + blacklist filter
- `_apply_diversity_filter` caps per-album + per-artist counts
- `_compute_adaptive_diversity_limits` returns the right tier for the
  unique-artist count + relaxed flag
- The 5 discovery_pool methods (decade / genre / popular_picks /
  hidden_gems / discovery_shuffle) each filter NULL-id rows
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from core.personalized_playlists import PersonalizedPlaylistsService


# ---------------------------------------------------------------------------
# Fake DB
# ---------------------------------------------------------------------------


class _FakeDatabase:
    """Wraps an in-memory sqlite connection so the service's
    `database._get_connection()` calls work the same as in production.

    The schema mirrors the real `discovery_pool` + `tracks` + `albums`
    + `artists` shape just enough for the selection methods to exercise.
    """

    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._setup_schema()

    def _setup_schema(self):
        cursor = self._conn.cursor()
        cursor.executescript("""
            CREATE TABLE discovery_pool (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                spotify_track_id TEXT,
                itunes_track_id TEXT,
                deezer_track_id TEXT,
                track_name TEXT,
                artist_name TEXT,
                album_name TEXT,
                album_cover_url TEXT,
                duration_ms INTEGER,
                popularity INTEGER,
                release_date TEXT,
                artist_genres TEXT,
                track_data_json TEXT
            );
            CREATE TABLE discovery_artist_blacklist (
                artist_name TEXT PRIMARY KEY
            );
        """)
        self._conn.commit()

    @contextmanager
    def _get_connection(self):
        # Match the production interface (`with database._get_connection() as conn`)
        try:
            yield self._conn
        finally:
            pass

    def insert_discovery_track(self, **kwargs):
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        self._conn.execute(
            f"INSERT INTO discovery_pool ({cols}) VALUES ({placeholders})",
            tuple(kwargs.values()),
        )
        self._conn.commit()

    def blacklist(self, artist_name):
        self._conn.execute(
            "INSERT INTO discovery_artist_blacklist (artist_name) VALUES (?)",
            (artist_name,),
        )
        self._conn.commit()


@pytest.fixture
def service():
    """Service with a fresh in-memory DB. `_get_active_source` patched
    to return 'spotify' so every selector targets the same source."""
    db = _FakeDatabase()
    svc = PersonalizedPlaylistsService(db)
    with patch.object(svc, '_get_active_source', return_value='spotify'):
        yield svc, db


# ---------------------------------------------------------------------------
# `_select_discovery_tracks` — the helper everyone goes through
# ---------------------------------------------------------------------------


def test_discovery_helper_filters_null_id_rows(service):
    svc, db = service
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp1', track_name='Has Spotify ID',
        artist_name='A', album_name='A', popularity=50,
    )
    db.insert_discovery_track(
        source='spotify', spotify_track_id=None, itunes_track_id=None,
        track_name='No IDs', artist_name='B', album_name='B', popularity=50,
    )
    db.insert_discovery_track(
        source='spotify', itunes_track_id='it1', track_name='Has iTunes ID',
        artist_name='C', album_name='C', popularity=50,
    )

    tracks = svc._select_discovery_tracks(
        source='spotify',
        order_by='track_name',
        fetch_limit=100,
    )
    names = sorted(t['track_name'] for t in tracks)
    assert names == ['Has Spotify ID', 'Has iTunes ID']


def test_discovery_helper_filters_blacklisted_artists(service):
    svc, db = service
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp1', track_name='Keep',
        artist_name='Good Artist', album_name='X',
    )
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp2', track_name='Drop',
        artist_name='Bad Artist', album_name='X',
    )
    db.blacklist('bad artist')

    tracks = svc._select_discovery_tracks(
        source='spotify',
        order_by='track_name',
        fetch_limit=100,
    )
    assert [t['track_name'] for t in tracks] == ['Keep']


def test_discovery_helper_honors_source_filter(service):
    svc, db = service
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp1', track_name='SP',
        artist_name='A', album_name='X',
    )
    db.insert_discovery_track(
        source='itunes', itunes_track_id='it1', track_name='IT',
        artist_name='A', album_name='X',
    )

    tracks = svc._select_discovery_tracks(
        source='spotify',
        order_by='track_name',
        fetch_limit=100,
    )
    assert [t['track_name'] for t in tracks] == ['SP']


def test_discovery_helper_honors_extra_where(service):
    svc, db = service
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp1', track_name='Pop60',
        artist_name='A', album_name='X', popularity=60,
    )
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp2', track_name='Pop20',
        artist_name='A', album_name='X', popularity=20,
    )

    tracks = svc._select_discovery_tracks(
        source='spotify',
        extra_where='AND popularity >= 50',
        order_by='popularity DESC',
        fetch_limit=100,
    )
    assert [t['track_name'] for t in tracks] == ['Pop60']


# ---------------------------------------------------------------------------
# Diversity filter
# ---------------------------------------------------------------------------


def test_diversity_filter_caps_per_album():
    svc = PersonalizedPlaylistsService(_FakeDatabase())
    tracks = [
        {'track_name': f't{i}', 'artist_name': 'A', 'album_name': 'Album1'}
        for i in range(10)
    ]
    out = svc._apply_diversity_filter(
        tracks, max_per_album=3, max_per_artist=10, limit=10,
    )
    assert len(out) == 3


def test_diversity_filter_caps_per_artist():
    svc = PersonalizedPlaylistsService(_FakeDatabase())
    tracks = [
        {'track_name': f't{i}', 'artist_name': 'OnlyArtist', 'album_name': f'Album{i}'}
        for i in range(10)
    ]
    out = svc._apply_diversity_filter(
        tracks, max_per_album=10, max_per_artist=2, limit=10,
    )
    assert len(out) == 2


def test_diversity_filter_stops_at_limit():
    svc = PersonalizedPlaylistsService(_FakeDatabase())
    tracks = [
        {'track_name': f't{i}', 'artist_name': f'a{i}', 'album_name': f'b{i}'}
        for i in range(20)
    ]
    out = svc._apply_diversity_filter(
        tracks, max_per_album=10, max_per_artist=10, limit=5,
    )
    assert len(out) == 5


# ---------------------------------------------------------------------------
# Adaptive diversity limits
# ---------------------------------------------------------------------------


def test_adaptive_limits_high_variety():
    svc = PersonalizedPlaylistsService(_FakeDatabase())
    tracks = [{'artist_name': f'a{i}'} for i in range(30)]
    max_album, max_artist = svc._compute_adaptive_diversity_limits(tracks)
    # High variety tier — strict limits
    assert (max_album, max_artist) == (3, 5)


def test_adaptive_limits_low_variety():
    svc = PersonalizedPlaylistsService(_FakeDatabase())
    tracks = [{'artist_name': f'a{i % 3}'} for i in range(30)]  # only 3 unique artists
    max_album, max_artist = svc._compute_adaptive_diversity_limits(tracks)
    # Low variety tier — much more lenient (matches existing decade-style limits)
    assert max_album >= 4
    assert max_artist >= 8


def test_adaptive_limits_relaxed_flag_loosens_genre_tier():
    svc = PersonalizedPlaylistsService(_FakeDatabase())
    # 15 unique artists triggers the moderate tier
    tracks = [{'artist_name': f'a{i}'} for i in range(15)]
    strict = svc._compute_adaptive_diversity_limits(tracks, relaxed=False)
    relaxed = svc._compute_adaptive_diversity_limits(tracks, relaxed=True)
    assert relaxed[1] >= strict[1]


# ---------------------------------------------------------------------------
# Public methods enforce the gate (smoke test on each)
# ---------------------------------------------------------------------------


def test_get_hidden_gems_filters_null_id_rows(service):
    svc, db = service
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp1', track_name='Gem',
        artist_name='A', album_name='X', popularity=20,
    )
    db.insert_discovery_track(
        source='spotify', spotify_track_id=None, itunes_track_id=None,
        track_name='Nogem', artist_name='A', album_name='X', popularity=20,
    )
    out = svc.get_hidden_gems(limit=10)
    assert [t['track_name'] for t in out] == ['Gem']


def test_get_discovery_shuffle_filters_null_id_rows(service):
    svc, db = service
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp1', track_name='Yes',
        artist_name='A', album_name='X',
    )
    db.insert_discovery_track(
        source='spotify', spotify_track_id=None, itunes_track_id=None,
        track_name='No', artist_name='A', album_name='X',
    )
    out = svc.get_discovery_shuffle(limit=10)
    assert [t['track_name'] for t in out] == ['Yes']


def test_get_popular_picks_filters_null_id_rows(service):
    svc, db = service
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp1', track_name='Yes',
        artist_name='A', album_name='X', popularity=80,
    )
    db.insert_discovery_track(
        source='spotify', spotify_track_id=None, itunes_track_id=None,
        track_name='No', artist_name='A', album_name='X', popularity=80,
    )
    out = svc.get_popular_picks(limit=10)
    assert [t['track_name'] for t in out] == ['Yes']


def test_get_decade_playlist_filters_null_id_rows(service):
    svc, db = service
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp1', track_name='Yes',
        artist_name='A', album_name='X', release_date='2024-06-01',
    )
    db.insert_discovery_track(
        source='spotify', spotify_track_id=None, itunes_track_id=None,
        track_name='No', artist_name='A', album_name='X', release_date='2024-06-01',
    )
    out = svc.get_decade_playlist(2020, limit=10)
    assert [t['track_name'] for t in out] == ['Yes']


