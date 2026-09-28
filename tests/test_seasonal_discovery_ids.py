"""H13: SeasonalDiscoveryService._search_discovery_pool_seasonal must map each
source to its own provider id column (spotify/itunes/deezer), not the old
binary spotify-else-itunes choice that finds nothing for Deezer users."""

from __future__ import annotations

import contextlib
import sqlite3

import pytest

from core.seasonal_discovery import SeasonalDiscoveryService


class FakeDb:
    """Minimal seam: _get_connection + a discovery_pool with all id columns."""

    def __init__(self, path):
        self.path = str(path)
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE discovery_pool (
                    id INTEGER PRIMARY KEY, source TEXT,
                    spotify_track_id TEXT, itunes_track_id TEXT, deezer_track_id TEXT,
                    track_name TEXT, artist_name TEXT, album_name TEXT,
                    album_cover_url TEXT, duration_ms INTEGER, popularity INTEGER,
                    track_data_json TEXT);
            """)
            conn.commit()

    @contextlib.contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


@pytest.fixture
def db(tmp_path):
    return FakeDb(tmp_path / "seasonal.db")


def _seed_pool(db, *, source, spotify=None, itunes=None, deezer=None, name='Christmas Song'):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO discovery_pool (source, spotify_track_id, itunes_track_id,"
            " deezer_track_id, track_name, artist_name, album_name, album_cover_url,"
            " duration_ms, popularity, track_data_json)"
            " VALUES (?, ?, ?, ?, ?, 'Deezer Artist', 'Holiday Album',"
            " 'https://art.jpg', 1000, 90, '{}')",
            (source, spotify, itunes, deezer, name))
        conn.commit()


def _service(db):
    return SeasonalDiscoveryService(spotify_client=None, database=db)


def test_deezer_row_found_for_deezer_source(db):
    """H13: a Deezer-only row must be returned for source='deezer'."""
    _seed_pool(db, source='deezer', deezer='dz-1')
    rows = _service(db)._search_discovery_pool_seasonal('christmas', 'deezer')
    assert len(rows) == 1
    assert rows[0]['deezer_track_id'] == 'dz-1'
    # legacy alias kept for the storage column + frontend shape
    assert rows[0]['spotify_track_id'] == 'dz-1'


def test_itunes_row_found_for_itunes_source(db):
    _seed_pool(db, source='itunes', itunes='it-1')
    rows = _service(db)._search_discovery_pool_seasonal('christmas', 'itunes')
    assert len(rows) == 1
    assert rows[0]['spotify_track_id'] == 'it-1'


def test_spotify_row_found_for_spotify_source(db):
    _seed_pool(db, source='spotify', spotify='sp-1')
    rows = _service(db)._search_discovery_pool_seasonal('christmas', 'spotify')
    assert len(rows) == 1
    assert rows[0]['spotify_track_id'] == 'sp-1'


def test_cross_source_row_not_leaked(db):
    """A Deezer row must not show up for a spotify query (source filter intact)."""
    _seed_pool(db, source='deezer', deezer='dz-1')
    rows = _service(db)._search_discovery_pool_seasonal('christmas', 'spotify')
    assert rows == []
