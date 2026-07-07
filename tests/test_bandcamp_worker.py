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
from database.music_database import MusicDatabase


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
            label TEXT, release_date TEXT, genres TEXT, api_track_count INTEGER,
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


# ---------------------------------------------------------------------------
# _get_next_item honors the Manage Enrichment Workers priority override.
# ---------------------------------------------------------------------------


def _real_db_worker(tmp_path):
    db = MusicDatabase(str(tmp_path / "bc.db"))
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO artists (id, name) VALUES ('a1', 'Some Artist')")
    cur.execute("INSERT INTO albums (id, artist_id, title) VALUES ('al1', 'a1', 'Pending Album')")
    cur.execute("INSERT INTO tracks (id, album_id, artist_id, title) VALUES ('t1', 'al1', 'a1', 'Pending Track')")
    conn.commit()
    conn.close()
    return BandcampWorker(database=db)


# ---------------------------------------------------------------------------
# Shared-column persistence: a match enriches the album's real metadata, not
# just the bandcamp_* namespace (PR #968 review).
# ---------------------------------------------------------------------------


def _album_row(worker, cols):
    return worker.db._get_connection().execute(
        f"SELECT {cols} FROM albums WHERE id = 1"
    ).fetchone()


def test_album_match_persists_shared_columns():
    worker = _worker()
    worker._update_entity('album', 1, {
        'id': 555, 'url': 'https://x.bandcamp.com/album/y', 'title': 'Y',
        'tags': ['Techno', 'Detroit'], 'label': 'Underground Resistance',
        'release_date': '1992-05-01', 'total_tracks': 8,
    })
    row = _album_row(worker, "label, release_date, genres, api_track_count, bandcamp_label")
    assert row['label'] == 'Underground Resistance'
    assert row['release_date'] == '1992-05-01'
    assert row['api_track_count'] == 8
    assert 'Techno' in (row['genres'] or '')
    assert row['bandcamp_label'] == 'Underground Resistance'  # namespace still written too


def test_shared_columns_are_backfill_only():
    """Must never clobber a value another source or the user already set."""
    worker = _worker()
    conn = worker.db._get_connection()
    conn.execute("UPDATE albums SET label = 'Original Label', release_date = '2000-01-01' WHERE id = 1")
    conn.commit()

    worker._update_entity('album', 1, {
        'id': 555, 'url': 'https://x.bandcamp.com/album/y', 'title': 'Y',
        'tags': ['rock'], 'label': 'Bandcamp Label',
        'release_date': '1999-09-09', 'total_tracks': 3,
    })
    row = _album_row(worker, "label, release_date")
    assert row['label'] == 'Original Label'
    assert row['release_date'] == '2000-01-01'


def test_track_match_does_not_touch_album_shared_columns():
    # Tracks have no album-level shared columns — the shared-col block is
    # album-only, so a track update must not error on missing columns.
    worker = _worker()
    worker._update_entity('track', 1, {
        'id': 9, 'url': 'https://x.bandcamp.com/track/t', 'title': 'T',
        'tags': ['ambient'], 'label': 'L', 'release_date': '2010-01-01', 'total_tracks': 1,
    })
    row = worker.db._get_connection().execute(
        "SELECT bandcamp_url FROM tracks WHERE id = 1"
    ).fetchone()
    assert row['bandcamp_url'] == 'https://x.bandcamp.com/track/t'


# ---------------------------------------------------------------------------
# honor_stored_match: a stored bandcamp_url refreshes by direct fetch, never
# by re-searching, and a transient refresh failure preserves the match.
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, release=None, search_result=None):
        self._release = release
        self._search_result = search_result
        self.release_calls = []
        self.search_calls = []

    def get_release_metadata(self, url):
        self.release_calls.append(url)
        return self._release

    def search_album(self, artist, album):
        self.search_calls.append((artist, album))
        return self._search_result

    def search_track(self, artist, title):
        self.search_calls.append((artist, title))
        return self._search_result


def test_stored_url_refreshes_by_fetch_not_search():
    worker = _worker()
    conn = worker.db._get_connection()
    conn.execute("UPDATE albums SET bandcamp_url = 'https://x.bandcamp.com/album/y', "
                 "bandcamp_id = '555', bandcamp_match_status = 'matched' WHERE id = 1")
    conn.commit()
    worker.client = _FakeClient(release={
        'url': 'https://x.bandcamp.com/album/y', 'title': 'Y', 'tags': ['idm'],
        'label': 'FBR', 'release_date': '2021-01-01', 'total_tracks': 4,
    })

    worker._process_album(1, 'Y', 'Artist')

    assert worker.client.release_calls == ['https://x.bandcamp.com/album/y']
    assert worker.client.search_calls == []  # never re-searched
    row = _album_row(worker, "bandcamp_id, label, api_track_count")
    assert row['bandcamp_id'] == '555'          # preserved via COALESCE
    assert row['label'] == 'FBR'                # shared cols refreshed
    assert row['api_track_count'] == 4


def test_stored_url_refresh_failure_preserves_match_without_searching():
    worker = _worker()
    conn = worker.db._get_connection()
    conn.execute("UPDATE albums SET bandcamp_url = 'https://x.bandcamp.com/album/y', "
                 "bandcamp_match_status = 'matched' WHERE id = 1")
    conn.commit()
    worker.client = _FakeClient(release=None, search_result={
        'id': 1, 'url': 'https://other.bandcamp.com/album/z', 'title': 'Y', 'tags': [], 'label': None,
    })

    worker._process_album(1, 'Y', 'Artist')

    # Transient fetch miss must NOT fall through to a name search that could
    # overwrite the manual match.
    assert worker.client.search_calls == []
    row = _album_row(worker, "bandcamp_url, bandcamp_match_status")
    assert row['bandcamp_url'] == 'https://x.bandcamp.com/album/y'
    assert row['bandcamp_match_status'] == 'matched'


def test_no_stored_url_falls_through_to_search():
    worker = _worker()
    worker.client = _FakeClient(search_result={
        'id': 777, 'url': 'https://x.bandcamp.com/album/y', 'title': 'Episode 1',
        'tags': ['idm'], 'label': 'FBR', 'release_date': '2021-01-01', 'total_tracks': 2,
    })

    worker._process_album(1, 'Episode 1', 'Artist')

    assert worker.client.search_calls == [('Artist', 'Episode 1')]
    row = _album_row(worker, "bandcamp_id, bandcamp_url, bandcamp_match_status")
    assert row['bandcamp_id'] == '777'
    assert row['bandcamp_url'] == 'https://x.bandcamp.com/album/y'
    assert row['bandcamp_match_status'] == 'matched'


def test_get_next_item_defaults_to_album_first(tmp_path):
    worker = _real_db_worker(tmp_path)
    item = worker._get_next_item()
    assert item['type'] == 'album' and item['id'] == 'al1'


def test_get_next_item_honors_track_priority_override(tmp_path):
    """PR #968 review: the Bandcamp worker must respect the Manage Enrichment
    Workers 'process this group first' override like the other workers."""
    from config.settings import config_manager
    worker = _real_db_worker(tmp_path)
    key = 'bandcamp_enrichment_priority'
    old = config_manager.get(key, '')
    try:
        config_manager.set(key, 'track')
        item = worker._get_next_item()
        assert item['type'] == 'track' and item['id'] == 't1', "pinned track group must jump ahead of the album"
    finally:
        config_manager.set(key, old)
