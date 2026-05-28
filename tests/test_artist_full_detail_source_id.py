"""Regression tests for ``MusicDatabase.get_artist_full_detail`` source-ID
resolution.

Bug (reported by Boulder, 2026-05-28): opening a library artist from a
*non-library* search result (e.g. a MusicBrainz hit) leaves the artist-detail
page holding the source ID — the MBID — not the integer library PK. The
standard /api/artist-detail route resolves that via
``find_library_artist_for_source``, but the **enhanced-view** endpoint
(``/api/library/artist/<id>/enhanced``) and the quality-analysis endpoint call
``get_artist_full_detail`` directly with whatever ID the page holds. With only
a ``WHERE id = ?`` lookup, that 404'd ("Artist with ID <mbid> not found") and
the enhanced view failed to load.

Fix: when the direct PK lookup misses, resolve against any per-service ID
column (``SOURCE_ID_FIELD``).

These are isolated DB-method tests — no Flask, no route layer — so the SQL
fallback itself is exercised.
"""

import sqlite3
import sys
import types

import pytest


# ── stubs (same shape used elsewhere in the test suite) ───────────────────
if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")
    spotipy.Spotify = object
    oauth2 = types.ModuleType("spotipy.oauth2")
    oauth2.SpotifyOAuth = object
    oauth2.SpotifyClientCredentials = object
    spotipy.oauth2 = oauth2
    sys.modules["spotipy"] = spotipy
    sys.modules["spotipy.oauth2"] = oauth2

if "config.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("config.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "primary"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod


from database.music_database import MusicDatabase  # noqa: E402


class _InMemoryDB(MusicDatabase):
    """MusicDatabase backed by an in-memory sqlite that survives across
    ``_get_connection()`` calls."""

    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row

    def _get_connection(self):
        return _NonClosingConn(self._conn)


class _NonClosingConn:
    def __init__(self, real):
        self._real = real

    def cursor(self):
        return self._real.cursor()

    def commit(self):
        return self._real.commit()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _seed_schema(db):
    cur = db._conn.cursor()
    # Only the columns get_artist_full_detail touches (it uses SELECT *, so
    # the per-service ID columns must exist for the resolution fallback).
    cur.execute("""
        CREATE TABLE artists (
            id INTEGER PRIMARY KEY,
            name TEXT,
            server_source TEXT,
            genres TEXT,
            musicbrainz_id TEXT,
            spotify_artist_id TEXT,
            deezer_id TEXT,
            itunes_artist_id TEXT,
            discogs_id TEXT,
            soul_id TEXT,
            amazon_id TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE albums (
            id TEXT PRIMARY KEY,
            artist_id INTEGER,
            title TEXT,
            year INTEGER,
            genres TEXT,
            record_type TEXT,
            track_count INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE tracks (
            id TEXT PRIMARY KEY,
            album_id TEXT,
            title TEXT,
            track_number INTEGER
        )
    """)
    db._conn.commit()


def _seed_kendrick(db, **id_columns):
    """Insert a Kendrick Lamar library artist (PK 187926) with one album,
    setting whichever per-service ID columns the test needs."""
    cols = ['id', 'name', 'server_source'] + list(id_columns)
    vals = [187926, 'Kendrick Lamar', 'primary'] + list(id_columns.values())
    placeholders = ','.join('?' * len(cols))
    cur = db._conn.cursor()
    cur.execute(f"INSERT INTO artists ({','.join(cols)}) VALUES ({placeholders})", vals)
    cur.execute(
        "INSERT INTO albums (id, artist_id, title, year, record_type) VALUES (?, ?, ?, ?, ?)",
        ('alb-1', 187926, 'DAMN.', 2017, 'album'),
    )
    db._conn.commit()


@pytest.fixture
def db():
    d = _InMemoryDB()
    _seed_schema(d)
    return d


def test_direct_pk_lookup_still_works(db):
    """The primary path — integer library PK — must be unaffected by the
    new fallback."""
    _seed_kendrick(db, musicbrainz_id='381086ea-mbid')

    result = db.get_artist_full_detail(187926)

    assert result['success'] is True
    assert result['artist']['name'] == 'Kendrick Lamar'
    assert [a['title'] for a in result['albums']] == ['DAMN.']


def test_resolves_by_musicbrainz_id(db):
    """The exact bug: page holds the MBID, not the PK. Must resolve and
    return the library artist + albums instead of 404ing."""
    _seed_kendrick(db, musicbrainz_id='381086ea-mbid')

    result = db.get_artist_full_detail('381086ea-mbid')

    assert result['success'] is True
    assert result['artist']['name'] == 'Kendrick Lamar'
    assert [a['title'] for a in result['albums']] == ['DAMN.']


def test_resolves_by_spotify_id(db):
    """Resolution isn't MusicBrainz-specific — any per-service ID column
    works (proves SOURCE_ID_FIELD reuse, not a hardcoded mbid check)."""
    _seed_kendrick(db, spotify_artist_id='sp-kdot')

    result = db.get_artist_full_detail('sp-kdot')

    assert result['success'] is True
    assert result['artist']['name'] == 'Kendrick Lamar'


def test_unknown_id_returns_not_found(db):
    """An ID that matches neither the PK nor any source column still 404s."""
    _seed_kendrick(db, musicbrainz_id='381086ea-mbid')

    result = db.get_artist_full_detail('totally-unknown-id')

    assert result['success'] is False
    assert 'not found' in result['error']
