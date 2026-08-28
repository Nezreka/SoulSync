"""Regression test: ``MusicDatabase.get_artist_full_detail`` must not leak
the internal Discogs master/release type-tag into its album payload.

Bug: Discogs album ids are tagged 'm12345'/'r12345' at parse time so
``_discogs_album_endpoints`` can route between ``/masters/{id}`` and
``/releases/{id}`` without guessing (see ``core.discogs_client`` and
``tests/test_discogs_id_typing.py``). The background auto-match worker
(``core.discogs_worker._update_album``) persists that TAGGED id straight
into the ``albums.discogs_id`` column. ``get_artist_full_detail`` used to
read the column back verbatim (``dict(album_row)``) with no un-tagging
step, so the frontend's "View on Discogs" badge -- which just does
``https://www.discogs.com/release/${discogs_id}`` -- built a 404
(.../release/r5743831 instead of .../release/5743831).

Fix: strip the tag via ``core.discogs_client._untag_discogs_album_id``
right where the album row is serialized, leaving the stored DB value (and
every internal ``DiscogsClient`` consumer that still needs the tag for
routing) untouched.

Same isolated in-memory-sqlite pattern as
``tests/test_artist_full_detail_source_id.py`` -- no Flask, no route
layer -- so the DB method itself is exercised directly.
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

if "core.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("core.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "primary"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["core.settings"] = settings_mod


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
    cur.execute("""
        CREATE TABLE artists (
            id INTEGER PRIMARY KEY,
            name TEXT,
            server_source TEXT,
            genres TEXT
        )
    """)
    # discogs_id is what get_artist_full_detail must untag before returning.
    cur.execute("""
        CREATE TABLE albums (
            id TEXT PRIMARY KEY,
            artist_id INTEGER,
            title TEXT,
            year INTEGER,
            genres TEXT,
            record_type TEXT,
            track_count INTEGER,
            discogs_id TEXT
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


def _seed_artist_with_album(db, discogs_id):
    cur = db._conn.cursor()
    cur.execute(
        "INSERT INTO artists (id, name, server_source) VALUES (?, ?, ?)",
        (1, 'Naoya Matsuoka', 'primary'),
    )
    cur.execute(
        "INSERT INTO albums (id, artist_id, title, year, record_type, discogs_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ('alb-1', 1, 'Watermelon Dandies', 1981, 'album', discogs_id),
    )
    db._conn.commit()


@pytest.fixture
def db():
    d = _InMemoryDB()
    _seed_schema(d)
    return d


def test_release_tagged_discogs_id_is_untagged_in_response(db):
    """The exact reported bug: a release auto-matched by the background
    worker has a 'r'-tagged discogs_id in the DB. The API response must
    hand back the bare numeric id, not the tag."""
    _seed_artist_with_album(db, discogs_id='r5743831')

    result = db.get_artist_full_detail(1)

    assert result['success'] is True
    assert len(result['albums']) == 1
    assert result['albums'][0]['discogs_id'] == '5743831'


def test_master_tagged_discogs_id_is_untagged_in_response(db):
    _seed_artist_with_album(db, discogs_id='m12345')

    result = db.get_artist_full_detail(1)

    assert result['albums'][0]['discogs_id'] == '12345'


def test_album_with_no_discogs_id_is_unaffected(db):
    _seed_artist_with_album(db, discogs_id=None)

    result = db.get_artist_full_detail(1)

    assert result['albums'][0]['discogs_id'] is None


def test_legacy_untagged_discogs_id_is_left_alone(db):
    """A pre-#848 bare numeric id (never tagged) must pass through inert --
    this fix only strips a real tag, it doesn't reformat the field."""
    _seed_artist_with_album(db, discogs_id='5743831')

    result = db.get_artist_full_detail(1)

    assert result['albums'][0]['discogs_id'] == '5743831'
