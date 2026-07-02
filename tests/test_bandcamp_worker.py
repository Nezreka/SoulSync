"""BandcampWorker._update_entity's re-enrichment fast path.

Bug found while diagnosing "Full Body Recordings" (2026-07-02): once an
album/track is matched, _process_album/_process_track's "already has a
bandcamp_url" branch re-fetches the release page but has no numeric id to
report, so it calls _update_entity(..., {'id': None, ...}). _update_entity
used to write that None straight into bandcamp_id, silently nulling out a
previously-recorded id on every subsequent enrichment pass — even though
bandcamp_url/bandcamp_match_status stayed correctly 'matched'. That broke
anything keyed on bandcamp_id: the enhanced library view's per-track match
chip (always showed red/not-found after the first re-enrichment) and the
artist enrichment-coverage percentage in web_server.py.

Fixed via COALESCE(?, bandcamp_id) in the UPDATE, so a None id preserves
whatever was already there instead of overwriting it.
"""

from __future__ import annotations

import sqlite3

from core.bandcamp_worker import BandcampWorker


class _NonClosingConn:
    """_update_entity closes the connection it's handed after every call;
    the tests below call it twice against the same in-memory db, so
    .close() must be a no-op (the real MusicDatabase hands out a fresh
    connection each time — this fake reuses one instead)."""

    def __init__(self, real):
        self._real = real

    def cursor(self):
        return self._real.cursor()

    def commit(self):
        return self._real.commit()

    def execute(self, *args, **kwargs):
        return self._real.execute(*args, **kwargs)

    def close(self):
        pass


class _FakeDB:
    def __init__(self, conn):
        self._conn = _NonClosingConn(conn)

    def _get_connection(self):
        return self._conn


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE albums (
            id INTEGER PRIMARY KEY, artist_id INTEGER, title TEXT,
            bandcamp_id TEXT, bandcamp_url TEXT, bandcamp_match_status TEXT,
            bandcamp_last_attempted TEXT, bandcamp_tags TEXT, bandcamp_label TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY, album_id INTEGER, title TEXT,
            bandcamp_id TEXT, bandcamp_url TEXT, bandcamp_match_status TEXT,
            bandcamp_last_attempted TEXT, bandcamp_tags TEXT, bandcamp_label TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("INSERT INTO albums (id, artist_id, title) VALUES (1, 1, 'Episode 1')")
    conn.execute("INSERT INTO tracks (id, album_id, title) VALUES (1, 1, 'Track One')")
    conn.commit()
    return conn


def _worker():
    return BandcampWorker(database=_FakeDB(_make_db()))


def test_first_match_records_id_url_and_status():
    worker = _worker()
    worker._update_entity('album', 1, {
        'id': 3317386587, 'url': 'https://fbr.bandcamp.com/album/episode-1',
        'title': 'Episode 1', 'tags': ['idm'], 'label': None,
    })

    row = worker.db._get_connection().execute(
        "SELECT bandcamp_id, bandcamp_url, bandcamp_match_status FROM albums WHERE id = 1"
    ).fetchone()
    assert row['bandcamp_id'] == '3317386587'
    assert row['bandcamp_url'] == 'https://fbr.bandcamp.com/album/episode-1'
    assert row['bandcamp_match_status'] == 'matched'


def test_reenrichment_with_no_id_preserves_existing_bandcamp_id():
    """Pins the fix: the 'already matched, re-fetch from existing_url' path
    (core/bandcamp_worker.py's _process_album/_process_track) always passes
    id=None on the second call — that must NOT wipe the id recorded by the
    first match."""
    worker = _worker()
    worker._update_entity('album', 1, {
        'id': 3317386587, 'url': 'https://fbr.bandcamp.com/album/episode-1',
        'title': 'Episode 1', 'tags': [], 'label': None,
    })

    # Simulate the re-enrichment fast path exactly as _process_album calls it.
    worker._update_entity('album', 1, {
        'id': None, 'url': 'https://fbr.bandcamp.com/album/episode-1',
        'title': 'Episode 1 (refreshed)', 'tags': ['idm', 'ambient'], 'label': 'FBR',
    })

    row = worker.db._get_connection().execute(
        "SELECT bandcamp_id, bandcamp_url, bandcamp_match_status, bandcamp_label FROM albums WHERE id = 1"
    ).fetchone()
    assert row['bandcamp_id'] == '3317386587', "re-enrichment must not null out the previously matched id"
    assert row['bandcamp_url'] == 'https://fbr.bandcamp.com/album/episode-1'
    assert row['bandcamp_match_status'] == 'matched'
    assert row['bandcamp_label'] == 'FBR'  # other fields still refresh normally


def test_track_reenrichment_also_preserves_id():
    worker = _worker()
    worker._update_entity('track', 1, {
        'id': 3131312045, 'url': 'https://fbr.bandcamp.com/track/track-one',
        'title': 'Track One', 'tags': [], 'label': None,
    })
    worker._update_entity('track', 1, {
        'id': None, 'url': 'https://fbr.bandcamp.com/track/track-one',
        'title': 'Track One', 'tags': [], 'label': None,
    })

    row = worker.db._get_connection().execute(
        "SELECT bandcamp_id FROM tracks WHERE id = 1"
    ).fetchone()
    assert row['bandcamp_id'] == '3131312045'
