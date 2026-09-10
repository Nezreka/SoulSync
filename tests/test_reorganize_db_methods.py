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


# ── error propagation ────────────────────────────────────────────────────
# Regression for review feedback on the original PR: helpers used to
# swallow every Exception and return None / [], so a real DB outage
# masqueraded as "album not found" / "no albums". Now they let the
# error bubble — the route layer turns it into a 500 — so the user sees
# a real failure instead of a phantom empty state.


def test_get_album_display_meta_propagates_db_errors(db):
    """If the underlying tables don't exist, the helper must raise
    rather than swallow it as a missing-album result."""
    # Don't seed — the schema is empty, so the SELECT will fail with
    # OperationalError ("no such table: albums").
    with pytest.raises(sqlite3.OperationalError):
        db.get_album_display_meta('alb-1')


def test_get_artist_albums_for_reorganize_propagates_db_errors(db):
    with pytest.raises(sqlite3.OperationalError):
        db.get_artist_albums_for_reorganize('ar-1')


# ── reorganize_queue durability helpers (#1235) ───────────────────────────
#
# The queue's own tests use a fake store, so nothing there executes this SQL.
# A typo in the upsert would sail past all of them and only surface as a lost
# backlog on someone's server, which is exactly the failure being fixed.


def _queue_table(db):
    with db._get_connection() as conn:
        conn.cursor().execute("""
            CREATE TABLE IF NOT EXISTS reorganize_queue (
                queue_id TEXT PRIMARY KEY,
                album_id TEXT NOT NULL,
                status TEXT NOT NULL,
                enqueued_at REAL NOT NULL,
                payload TEXT NOT NULL
            )
        """)


def _snap(queue_id, album_id, status='queued', at=1.0):
    return {'queue_id': queue_id, 'album_id': album_id, 'status': status,
            'enqueued_at': at, 'album_title': 'T', 'artist_name': 'A',
            'artist_id': 'ar-1', 'source': None, 'metadata_source': 'api',
            'rename_only': False}


def test_queue_save_is_an_upsert_not_a_duplicate(db):
    _queue_table(db)
    db.reorganize_queue_save(_snap('q1', 'alb-1'))
    db.reorganize_queue_save(_snap('q1', 'alb-1', status='running'))
    pending = db.reorganize_queue_load_pending()
    assert len(pending) == 1, "the second save inserted a second row"


def test_queue_load_returns_interrupted_work_as_queued(db):
    """A row still marked 'running' means the process that owned it died. It has
    to come back as work to do - left as 'running' it would be invisible to the
    worker, which only ever claims 'queued'."""
    _queue_table(db)
    db.reorganize_queue_save(_snap('q1', 'alb-1', status='running'))
    pending = db.reorganize_queue_load_pending()
    assert [p['status'] for p in pending] == ['queued']
    assert pending[0]['started_at'] is None


def test_queue_load_is_oldest_first(db):
    _queue_table(db)
    db.reorganize_queue_save(_snap('q2', 'alb-2', at=200.0))
    db.reorganize_queue_save(_snap('q1', 'alb-1', at=100.0))
    assert [p['album_id'] for p in db.reorganize_queue_load_pending()] == ['alb-1', 'alb-2']


def test_queue_delete_removes_only_that_item(db):
    _queue_table(db)
    db.reorganize_queue_save(_snap('q1', 'alb-1'))
    db.reorganize_queue_save(_snap('q2', 'alb-2'))
    db.reorganize_queue_delete('q1')
    assert [p['album_id'] for p in db.reorganize_queue_load_pending()] == ['alb-2']


def test_queue_clear_spares_the_running_item(db):
    """Clearing the backlog must not delete the album currently being moved -
    it is still in flight, and losing its row makes it unresumable."""
    _queue_table(db)
    db.reorganize_queue_save(_snap('q1', 'alb-1', status='queued'))
    db.reorganize_queue_save(_snap('q2', 'alb-2', status='running'))
    assert db.reorganize_queue_delete_queued() == 1
    assert [p['album_id'] for p in db.reorganize_queue_load_pending()] == ['alb-2']


def test_a_corrupt_payload_does_not_sink_the_whole_backlog(db):
    """One unreadable row must cost one album, not every album behind it."""
    _queue_table(db)
    db.reorganize_queue_save(_snap('q1', 'alb-1'))
    with db._get_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO reorganize_queue VALUES ('q2','alb-2','queued',2.0,'{not json')")
        conn.commit()
    db.reorganize_queue_save(_snap('q3', 'alb-3', at=3.0))
    assert [p['album_id'] for p in db.reorganize_queue_load_pending()] == ['alb-1', 'alb-3']


def test_a_fresh_database_gets_the_queue_table(db):
    """The helpers above are useless if the table is never created. This runs the
    REAL _initialize_database against the in-memory harness, so it fails if the
    CREATE TABLE is dropped or moved out of the init path - which would leave the
    backlog silently unpersisted again, the exact shape of the original bug."""
    db._initialize_database()
    with db._get_connection() as conn:
        names = [r[0] for r in conn.cursor().execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        idx = [r[0] for r in conn.cursor().execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
        cols = [r[1] for r in conn.cursor().execute(
            "PRAGMA table_info(reorganize_queue)").fetchall()]
    assert 'reorganize_queue' in names
    assert 'idx_reorg_queue_status' in idx, "the status lookup has no index"
    assert cols == ['queue_id', 'album_id', 'status', 'enqueued_at', 'payload']

    # and the real helpers work against the real schema, not just the hand-rolled
    # table the other tests create
    db.reorganize_queue_save(_snap('q1', 'alb-1'))
    assert [p['album_id'] for p in db.reorganize_queue_load_pending()] == ['alb-1']

