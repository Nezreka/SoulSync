"""Tests for batched queries in listening stats worker (fix 2.1).

Before this fix the worker ran one SELECT per item for:
  - resolving db_track_id when inserting history events
  - mapping server play-count IDs to existing DB track IDs
  - enriching top_artists / top_albums / top_tracks in the stats cache

Each pattern was N+1 on the DB. The fix replaces them with single
batched IN queries (chunked for safety).
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase
from core.listening_stats_worker import ListeningStatsWorker


class _FakeConfigManager:
    def get(self, key, default=None):
        return default


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


@pytest.fixture
def worker(db):
    return ListeningStatsWorker(db, _FakeConfigManager())


def _install_query_counter(db):
    """Replace db._get_connection with a wrapper that counts execute() calls.

    Returns the counter dict (has key 'n') and a restore callback.
    """
    original_get_connection = db._get_connection
    counter = {"n": 0}

    class _CursorProxy:
        def __init__(self, real_cursor):
            self._real = real_cursor

        def execute(self, sql, params=()):
            counter["n"] += 1
            return self._real.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._real, name)

        def __iter__(self):
            return iter(self._real)

    class _ConnProxy:
        def __init__(self, real_conn):
            self._real = real_conn

        def cursor(self, *a, **k):
            return _CursorProxy(self._real.cursor(*a, **k))

        def __enter__(self):
            self._real.__enter__()
            return self

        def __exit__(self, *exc):
            return self._real.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def wrapped():
        return _ConnProxy(original_get_connection())

    db._get_connection = wrapped

    def restore():
        db._get_connection = original_get_connection

    return counter, restore


def _insert_track(db, track_id, title, artist_id, artist_name, album_id, album_title):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO artists (id, name) VALUES (?, ?)",
            (artist_id, artist_name),
        )
        conn.execute(
            "INSERT OR IGNORE INTO albums (id, title, artist_id, thumb_url) VALUES (?, ?, ?, ?)",
            (album_id, album_title, artist_id, f"http://img/{album_id}.jpg"),
        )
        conn.execute(
            """INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, file_path)
               VALUES (?, ?, ?, ?, 1, 180, ?)""",
            (track_id, album_id, artist_id, title, f"/music/{title}.mp3"),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# _resolve_db_track_ids_batch
# ---------------------------------------------------------------------------

class TestResolveDbTrackIdsBatch:
    def test_batch_returns_same_ids_as_per_event_lookup(self, db, worker):
        _insert_track(db, "t1", "Alpha", "a1", "Band One", "al1", "X")
        _insert_track(db, "t2", "Bravo", "a1", "Band One", "al1", "X")
        _insert_track(db, "t3", "Alpha", "a2", "Band Two", "al2", "Y")

        events = [
            {"title": "Alpha", "artist": "Band One"},
            {"title": "Bravo", "artist": "Band One"},
            {"title": "Alpha", "artist": "Band Two"},
            {"title": "Nonexistent", "artist": "Nobody"},
        ]

        id_map = worker._resolve_db_track_ids_batch(events)

        assert id_map[("alpha", "band one")] == "t1"
        assert id_map[("bravo", "band one")] == "t2"
        assert id_map[("alpha", "band two")] == "t3"
        assert ("nonexistent", "nobody") not in id_map

    def test_is_case_insensitive(self, db, worker):
        _insert_track(db, "t1", "Great Song", "a1", "Some Band", "al1", "X")

        id_map = worker._resolve_db_track_ids_batch(
            [{"title": "GREAT SONG", "artist": "SOME BAND"}]
        )
        assert id_map[("great song", "some band")] == "t1"

    def test_empty_list_returns_empty_dict(self, worker):
        assert worker._resolve_db_track_ids_batch([]) == {}

    def test_events_without_title_are_skipped(self, db, worker):
        _insert_track(db, "t1", "Song", "a1", "Band", "al1", "X")
        id_map = worker._resolve_db_track_ids_batch(
            [{"title": "", "artist": "Band"}, {"title": "Song", "artist": "Band"}]
        )
        assert id_map == {("song", "band"): "t1"}

    def test_runs_single_query_regardless_of_event_count(self, db, worker):
        """The whole point: 50 events must not trigger 50 queries."""
        for i in range(50):
            _insert_track(db, f"t{i}", f"Song {i}", "a1", "Band", "al1", "Album")

        counter, restore = _install_query_counter(db)
        try:
            events = [{"title": f"Song {i}", "artist": "Band"} for i in range(50)]
            id_map = worker._resolve_db_track_ids_batch(events)
        finally:
            restore()

        assert len(id_map) == 50
        # One batched query (everything fits in one chunk).
        assert counter["n"] == 1


# ---------------------------------------------------------------------------
# _map_play_counts_to_db
# ---------------------------------------------------------------------------

class TestMapPlayCountsToDb:
    def test_returns_updates_only_for_existing_ids(self, db, worker):
        _insert_track(db, "t1", "A", "a1", "Band", "al1", "Album")
        _insert_track(db, "t2", "B", "a1", "Band", "al1", "Album")

        server_counts = {"t1": 5, "t2": 3, "ghost": 99}
        updates = worker._map_play_counts_to_db(server_counts, "plex")

        ids = {u["db_track_id"]: u["play_count"] for u in updates}
        assert ids == {"t1": 5, "t2": 3}

    def test_empty_input_returns_empty_list(self, worker):
        assert worker._map_play_counts_to_db({}, "plex") == []

    def test_runs_single_query_regardless_of_count_size(self, db, worker):
        for i in range(30):
            _insert_track(db, f"t{i}", f"S{i}", "a1", "Band", "al1", "Album")

        counter, restore = _install_query_counter(db)
        try:
            server_counts = {f"t{i}": i for i in range(30)}
            updates = worker._map_play_counts_to_db(server_counts, "plex")
        finally:
            restore()

        assert len(updates) == 30
        assert counter["n"] == 1


# ---------------------------------------------------------------------------
# _enrich_stats_items
# ---------------------------------------------------------------------------

class TestEnrichStatsItems:
    def test_enriches_artists_albums_and_tracks(self, db, worker):
        _insert_track(db, "t1", "Alpha", "a1", "Band One", "al1", "First Album")
        _insert_track(db, "t2", "Bravo", "a2", "Band Two", "al2", "Second Album")

        cache = {
            "top_artists": [{"name": "Band One"}, {"name": "Band Two"}],
            "top_albums": [{"name": "First Album"}, {"name": "Second Album"}],
            "top_tracks": [
                {"name": "Alpha", "artist": "Band One"},
                {"name": "Bravo", "artist": "Band Two"},
            ],
        }

        worker._enrich_stats_items(cache)

        by_name = {a["name"]: a for a in cache["top_artists"]}
        assert by_name["Band One"]["id"] == "a1"
        assert by_name["Band Two"]["id"] == "a2"

        album_by_name = {a["name"]: a for a in cache["top_albums"]}
        assert album_by_name["First Album"]["id"] == "al1"
        assert album_by_name["First Album"]["image_url"] == "http://img/al1.jpg"

        track_by_name = {t["name"]: t for t in cache["top_tracks"]}
        assert track_by_name["Alpha"]["id"] == "t1"
        assert track_by_name["Bravo"]["id"] == "t2"
        assert track_by_name["Alpha"]["artist_id"] == "a1"

    def test_unknown_entries_left_untouched(self, db, worker):
        _insert_track(db, "t1", "Real", "a1", "Real Band", "al1", "Real Album")

        cache = {
            "top_artists": [{"name": "Unknown Band"}],
            "top_albums": [{"name": "Unknown Album"}],
            "top_tracks": [{"name": "Unknown", "artist": "Nobody"}],
        }
        worker._enrich_stats_items(cache)

        assert "id" not in cache["top_artists"][0]
        assert "id" not in cache["top_albums"][0]
        assert "id" not in cache["top_tracks"][0]

    def test_empty_cache_is_safe(self, worker):
        worker._enrich_stats_items({})  # must not raise
        worker._enrich_stats_items({"top_artists": [], "top_albums": [], "top_tracks": []})

    def test_runs_one_query_per_section(self, db, worker):
        for i in range(20):
            _insert_track(db, f"t{i}", f"Song {i}", f"a{i}", f"Band {i}",
                          f"al{i}", f"Album {i}")

        cache = {
            "top_artists": [{"name": f"Band {i}"} for i in range(20)],
            "top_albums": [{"name": f"Album {i}"} for i in range(20)],
            "top_tracks": [
                {"name": f"Song {i}", "artist": f"Band {i}"} for i in range(20)
            ],
        }

        counter, restore = _install_query_counter(db)
        try:
            worker._enrich_stats_items(cache)
        finally:
            restore()

        # 3 batched queries total (artists + albums + tracks), not 60.
        assert counter["n"] == 3
