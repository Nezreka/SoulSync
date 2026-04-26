"""Tests for the reorganize-queue DB helpers on `MusicDatabase`:

- ``get_album_display_meta(album_id)`` — returns the title/artist tuple
  the queue uses for status-panel display, or None when not found.
- ``get_artist_albums_for_reorganize(artist_id)`` — returns the
  bulk-enqueue list ordered by year then title.

These are isolated DB-method tests so the SQL itself is verified
without spinning up Flask, the queue worker, or the orchestrator.
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


# ── helpers ───────────────────────────────────────────────────────────────


class _InMemoryDB(MusicDatabase):
    """MusicDatabase that uses an in-memory sqlite that survives across
    `_get_connection()` calls. Lets tests seed rows once and have the
    methods under test see them."""

    def __init__(self):
        # Skip the real __init__ — it would try to migrate a real db.
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row

    def _get_connection(self):
        return _NonClosingConn(self._conn)


class _NonClosingConn:
    """Wraps the shared sqlite connection so `with db._get_connection()
    as conn:` doesn't close the underlying handle between calls."""
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


def _seed(db, *, artists=(), albums=()):
    cur = db._conn.cursor()
    cur.execute("CREATE TABLE artists (id TEXT PRIMARY KEY, name TEXT)")
    cur.execute("""
        CREATE TABLE albums (
            id TEXT PRIMARY KEY,
            artist_id TEXT,
            title TEXT,
            year INTEGER
        )
    """)
    for ar in artists:
        cur.execute("INSERT INTO artists VALUES (?, ?)", ar)
    for al in albums:
        cur.execute(
            "INSERT INTO albums (id, artist_id, title, year) VALUES (?, ?, ?, ?)",
            al,
        )
    db._conn.commit()


@pytest.fixture
def db():
    return _InMemoryDB()


# ── get_album_display_meta ────────────────────────────────────────────────


def test_get_album_display_meta_returns_dict_for_known_album(db):
    _seed(db,
          artists=[('ar-1', 'Kendrick Lamar')],
          albums=[('alb-1', 'ar-1', 'good kid, m.A.A.d city', 2012)])
    meta = db.get_album_display_meta('alb-1')
    assert meta == {
        'album_title': 'good kid, m.A.A.d city',
        'artist_id': 'ar-1',
        'artist_name': 'Kendrick Lamar',
    }


def test_get_album_display_meta_returns_none_for_missing_album(db):
    _seed(db, artists=[('ar-1', 'Aerosmith')])
    assert db.get_album_display_meta('does-not-exist') is None


def test_get_album_display_meta_falls_back_for_blank_strings(db):
    """Albums with empty title or artist name in the DB still need a
    safe display value — the queue UI should never render '(blank)'."""
    _seed(db,
          artists=[('ar-1', '')],
          albums=[('alb-1', 'ar-1', '', 2015)])
    meta = db.get_album_display_meta('alb-1')
    assert meta['album_title'] == 'Unknown Album'
    assert meta['artist_name'] == 'Unknown Artist'
    assert meta['artist_id'] == 'ar-1'


# ── get_artist_albums_for_reorganize ──────────────────────────────────────


def test_get_artist_albums_for_reorganize_orders_by_year_then_title(db):
    _seed(db,
          artists=[('ar-1', 'Aerosmith')],
          albums=[
              ('alb-c', 'ar-1', 'Toys in the Attic', 1975),
              ('alb-a', 'ar-1', 'Aerosmith', 1973),
              ('alb-b', 'ar-1', 'Get Your Wings', 1974),
          ])
    rows = db.get_artist_albums_for_reorganize('ar-1')
    assert [r['album_id'] for r in rows] == ['alb-a', 'alb-b', 'alb-c']
    assert all(r['artist_name'] == 'Aerosmith' for r in rows)


def test_get_artist_albums_for_reorganize_secondary_sorts_by_title(db):
    """Same release year → tiebreak on title alphabetically."""
    _seed(db,
          artists=[('ar-1', 'X')],
          albums=[
              ('alb-z', 'ar-1', 'Zebra', 1990),
              ('alb-a', 'ar-1', 'Apple', 1990),
              ('alb-m', 'ar-1', 'Mango', 1990),
          ])
    rows = db.get_artist_albums_for_reorganize('ar-1')
    assert [r['album_title'] for r in rows] == ['Apple', 'Mango', 'Zebra']


def test_get_artist_albums_for_reorganize_returns_empty_for_unknown_artist(db):
    _seed(db, artists=[('ar-1', 'Aerosmith')])
    assert db.get_artist_albums_for_reorganize('not-a-real-artist') == []


def test_get_artist_albums_for_reorganize_isolates_by_artist(db):
    """Pulling albums for artist A must NOT leak in albums from artist B."""
    _seed(db,
          artists=[('ar-1', 'A'), ('ar-2', 'B')],
          albums=[
              ('alb-1', 'ar-1', 'A1', 2000),
              ('alb-2', 'ar-2', 'B1', 2000),
              ('alb-3', 'ar-1', 'A2', 2001),
          ])
    rows = db.get_artist_albums_for_reorganize('ar-1')
    assert {r['album_id'] for r in rows} == {'alb-1', 'alb-3'}
