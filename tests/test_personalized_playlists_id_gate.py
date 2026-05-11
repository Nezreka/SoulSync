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
            -- Minimal `tracks` table: exists so the `exclude_owned`
            -- subquery in `_select_discovery_tracks` can join. Real
            -- schema has many more columns; we only need the source-id
            -- columns it actually inspects.
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY,
                spotify_track_id TEXT,
                itunes_track_id TEXT,
                deezer_id TEXT
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

    def insert_library_track(self, **kwargs):
        """Insert a row into the local `tracks` table (the user's library).
        Used to prove `exclude_owned=True` filters discovery rows whose IDs
        match a library row."""
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        self._conn.execute(
            f"INSERT INTO tracks ({cols}) VALUES ({placeholders})",
            tuple(kwargs.values()),
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


def test_discovery_helper_accepts_deezer_only_id_rows(service):
    """Discovery pool rows with NULL spotify + NULL itunes but a populated
    deezer_track_id MUST pass the gate. Regression test — early version
    of the gate only checked Spotify + iTunes, which silently filtered
    out every row for Deezer-primary users (entire Time Machine /
    Genre / Hidden Gems / Shuffle / Popular Picks rendered empty)."""
    svc, db = service
    db.insert_discovery_track(
        source='deezer', deezer_track_id='dz1',
        spotify_track_id=None, itunes_track_id=None,
        track_name='Deezer Only', artist_name='A', album_name='X',
        popularity=50, release_date='2024-01-01',
    )
    with patch.object(svc, '_get_active_source', return_value='deezer'):
        tracks = svc._select_discovery_tracks(
            source='deezer',
            order_by='track_name',
            fetch_limit=100,
        )
    assert [t['track_name'] for t in tracks] == ['Deezer Only']
    assert tracks[0]['deezer_track_id'] == 'dz1'


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


# ---------------------------------------------------------------------------
# Fix #1 — Hidden Gems + Discovery Shuffle apply diversity
# ---------------------------------------------------------------------------


def test_get_hidden_gems_applies_diversity(service):
    """10 low-popularity tracks all from the same album/artist should be
    capped at the per-album limit (2) — Hidden Gems no longer returns
    raw RANDOM() rows."""
    svc, db = service
    for i in range(10):
        db.insert_discovery_track(
            source='spotify', spotify_track_id=f'sp{i}',
            track_name=f't{i}', artist_name='SoloArtist',
            album_name='OnlyAlbum', popularity=20,
        )
    out = svc.get_hidden_gems(limit=10)
    # All 10 share the same album → diversity cap of 2 per album wins.
    assert len(out) == 2
    assert all(t['album_name'] == 'OnlyAlbum' for t in out)


def test_get_discovery_shuffle_applies_diversity(service):
    """Same idea for shuffle, with tighter caps (2 per artist)."""
    svc, db = service
    for i in range(10):
        db.insert_discovery_track(
            source='spotify', spotify_track_id=f'sp{i}',
            track_name=f't{i}', artist_name='SoloArtist',
            album_name=f'Album{i}',  # different albums so artist cap bites
            popularity=50,
        )
    out = svc.get_discovery_shuffle(limit=10)
    # Same artist → per-artist cap of 2 wins.
    assert len(out) == 2
    assert all(t['artist_name'] == 'SoloArtist' for t in out)


# ---------------------------------------------------------------------------
# Fix #2 — Source-aware popularity thresholds
# ---------------------------------------------------------------------------


def test_popularity_thresholds_spotify_returns_60_40():
    svc = PersonalizedPlaylistsService(_FakeDatabase())
    popular_min, hidden_max = svc._get_popularity_thresholds('spotify')
    assert popular_min == 60
    assert hidden_max == 40


def test_popularity_thresholds_deezer_returns_higher_scale():
    """Deezer writes `rank` (raw integer, often 100k+) into the popularity
    column, so thresholds must be in that range — Spotify's 60/40 would
    classify almost every Deezer track as a hidden gem."""
    svc = PersonalizedPlaylistsService(_FakeDatabase())
    popular_min, hidden_max = svc._get_popularity_thresholds('deezer')
    assert popular_min is not None and popular_min >= 100_000
    assert hidden_max is not None and hidden_max >= 50_000
    assert popular_min > hidden_max


def test_popularity_thresholds_itunes_skips_filter():
    """iTunes has no usable popularity data — both thresholds should be
    None so callers fall back to RANDOM + diversity only."""
    svc = PersonalizedPlaylistsService(_FakeDatabase())
    popular_min, hidden_max = svc._get_popularity_thresholds('itunes')
    assert popular_min is None
    assert hidden_max is None


def test_get_popular_picks_skips_threshold_when_none():
    """When the active source has no popularity data, Popular Picks
    should skip the popularity filter entirely (just diversity + ID
    gate). Insert rows with a mix of popularity values; with the iTunes
    source they should ALL pass the popularity gate."""
    db = _FakeDatabase()
    svc = PersonalizedPlaylistsService(db)
    db.insert_discovery_track(
        source='itunes', itunes_track_id='it1', track_name='Low',
        artist_name='A', album_name='Album1', popularity=5,
    )
    db.insert_discovery_track(
        source='itunes', itunes_track_id='it2', track_name='High',
        artist_name='B', album_name='Album2', popularity=95,
    )
    db.insert_discovery_track(
        source='itunes', itunes_track_id='it3', track_name='Zero',
        artist_name='C', album_name='Album3', popularity=0,
    )
    with patch.object(svc, '_get_active_source', return_value='itunes'):
        out = svc.get_popular_picks(limit=10)
    names = sorted(t['track_name'] for t in out)
    assert names == ['High', 'Low', 'Zero']


# ---------------------------------------------------------------------------
# Fix #3 — Genre keyword filter pushed to SQL
# ---------------------------------------------------------------------------


def test_get_genre_playlist_pushes_filter_to_sql(service):
    """Insert tracks with various artist_genres JSON values; only those
    whose genres contain a rock keyword should come through. The match
    happens in SQL (LIKE on the JSON-encoded string) rather than after
    a million-row over-fetch."""
    import json as _json
    svc, db = service
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp1', track_name='RockSong',
        artist_name='RockBand', album_name='Album1',
        artist_genres=_json.dumps(['indie rock', 'alternative']),
    )
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp2', track_name='JazzSong',
        artist_name='JazzCat', album_name='Album2',
        artist_genres=_json.dumps(['bebop', 'cool jazz']),
    )
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp3', track_name='PopSong',
        artist_name='PopStar', album_name='Album3',
        artist_genres=_json.dumps(['k-pop']),
    )
    out = svc.get_genre_playlist('rock', limit=10)
    names = [t['track_name'] for t in out]
    # Only the indie rock track matches the literal "rock" keyword.
    assert 'RockSong' in names
    assert 'JazzSong' not in names
    # k-pop doesn't contain "rock" as a substring → excluded.
    assert 'PopSong' not in names


def test_get_genre_playlist_handles_parent_genre(service):
    """Parent genres in GENRE_MAPPING expand to all their child keywords;
    a track tagged with any child genre should be included."""
    import json as _json
    svc, db = service
    # 'Electronic/Dance' parent expands to keywords like 'house', 'techno',
    # 'edm' etc. Tag tracks with various children.
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp1', track_name='HouseTrack',
        artist_name='DJ1', album_name='A1',
        artist_genres=_json.dumps(['deep house']),
    )
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp2', track_name='TechnoTrack',
        artist_name='DJ2', album_name='A2',
        artist_genres=_json.dumps(['minimal techno']),
    )
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp3', track_name='RockTrack',
        artist_name='Band1', album_name='A3',
        artist_genres=_json.dumps(['indie rock']),
    )
    out = svc.get_genre_playlist('Electronic/Dance', limit=10)
    names = sorted(t['track_name'] for t in out)
    assert 'HouseTrack' in names
    assert 'TechnoTrack' in names
    assert 'RockTrack' not in names


# ---------------------------------------------------------------------------
# Fix #4 — `_select_discovery_tracks` excludes already-owned tracks
# ---------------------------------------------------------------------------


def test_discovery_helper_excludes_owned_tracks(service):
    """Discovery row with spotify_track_id='sp1' + library track with the
    same spotify_track_id should be filtered out. Owned tracks shouldn't
    surface in Hidden Gems / Shuffle / Popular Picks."""
    svc, db = service
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp1', track_name='Owned',
        artist_name='A', album_name='X', popularity=50,
    )
    db.insert_library_track(spotify_track_id='sp1')

    tracks = svc._select_discovery_tracks(
        source='spotify',
        order_by='track_name',
        fetch_limit=100,
    )
    assert tracks == []


def test_discovery_helper_keeps_unowned_tracks(service):
    """Same shape but the library row carries a different spotify_track_id
    — discovery row should pass through."""
    svc, db = service
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp1', track_name='NotOwned',
        artist_name='A', album_name='X', popularity=50,
    )
    db.insert_library_track(spotify_track_id='sp_different')

    tracks = svc._select_discovery_tracks(
        source='spotify',
        order_by='track_name',
        fetch_limit=100,
    )
    assert [t['track_name'] for t in tracks] == ['NotOwned']


def test_discovery_helper_can_disable_owned_filter(service):
    """`exclude_owned=False` lets owned rows pass through — used by code
    paths that legitimately want library matches (currently none, but
    the flag should still work)."""
    svc, db = service
    db.insert_discovery_track(
        source='spotify', spotify_track_id='sp1', track_name='Owned',
        artist_name='A', album_name='X', popularity=50,
    )
    db.insert_library_track(spotify_track_id='sp1')

    tracks = svc._select_discovery_tracks(
        source='spotify',
        order_by='track_name',
        fetch_limit=100,
        exclude_owned=False,
    )
    assert [t['track_name'] for t in tracks] == ['Owned']


def test_discovery_helper_owned_filter_handles_deezer_id_asymmetry(service):
    """Column-name asymmetry: discovery_pool.deezer_track_id vs
    tracks.deezer_id. Pin this — easy to break in a future refactor."""
    svc, db = service
    db.insert_discovery_track(
        source='deezer', deezer_track_id='dz1',
        spotify_track_id=None, itunes_track_id=None,
        track_name='OwnedDeezer', artist_name='A', album_name='X',
        popularity=50,
    )
    db.insert_library_track(deezer_id='dz1')

    with patch.object(svc, '_get_active_source', return_value='deezer'):
        tracks = svc._select_discovery_tracks(
            source='deezer',
            order_by='track_name',
            fetch_limit=100,
        )
    assert tracks == []

