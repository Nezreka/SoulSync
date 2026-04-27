"""Unit tests for batched metadata cache entity lookups.

Fix 1.3: `MetadataCache.get_search_results` previously resolved cached
entity IDs one-by-one, producing N extra SELECT queries per cached
search. The resolution now runs as a single batched `IN` query (chunked
to stay below SQLite's variable limit) and preserves the original
`result_ids` ordering.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from core.metadata_cache import MetadataCache


@pytest.fixture
def cache_with_db(tmp_path):
    """MetadataCache wired to a temporary SQLite DB with the required tables."""
    db_path = tmp_path / "cache.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE metadata_cache_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            search_type TEXT NOT NULL,
            query_normalized TEXT NOT NULL,
            search_limit INTEGER NOT NULL,
            result_ids TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_accessed_at TEXT,
            access_count INTEGER DEFAULT 0
        );
        CREATE TABLE metadata_cache_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            raw_json TEXT NOT NULL
        );
        """
    )

    # Fake MusicDatabase that yields this sqlite connection.
    fake_db = MagicMock()

    def _get_connection():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    fake_db._get_connection.side_effect = _get_connection

    cache = MetadataCache()
    cache._get_db = lambda: fake_db  # type: ignore[method-assign]

    return cache, conn


def _insert_search(conn, source, search_type, query, limit, result_ids, created_at=None):
    created_at = created_at or datetime.now().isoformat()
    conn.execute(
        """INSERT INTO metadata_cache_searches
           (source, search_type, query_normalized, search_limit, result_ids, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (source, search_type, query.lower(), limit, json.dumps(result_ids), created_at),
    )
    conn.commit()


def _insert_entity(conn, source, entity_type, entity_id, payload):
    conn.execute(
        """INSERT INTO metadata_cache_entities
           (source, entity_type, entity_id, raw_json) VALUES (?, ?, ?, ?)""",
        (source, entity_type, entity_id, json.dumps(payload)),
    )
    conn.commit()


def test_returns_results_in_original_order(cache_with_db):
    cache, conn = cache_with_db
    ids = ["z", "a", "m", "b"]
    _insert_search(conn, "spotify", "track", "hello", 50, ids)
    for eid in ids:
        _insert_entity(conn, "spotify", "track", eid, {"id": eid, "name": f"track {eid}"})

    results = cache.get_search_results("spotify", "track", "hello", 50)

    assert results is not None
    assert [r["id"] for r in results] == ids


def test_missing_entities_below_threshold_returns_none(cache_with_db):
    cache, conn = cache_with_db
    ids = [f"id{i}" for i in range(10)]
    _insert_search(conn, "spotify", "track", "partial", 50, ids)
    # Only insert 5/10 entities — below the 80 percent threshold.
    for eid in ids[:5]:
        _insert_entity(conn, "spotify", "track", eid, {"id": eid})

    assert cache.get_search_results("spotify", "track", "partial", 50) is None


def test_missing_entities_at_threshold_returns_partial(cache_with_db):
    cache, conn = cache_with_db
    ids = [f"id{i}" for i in range(10)]
    _insert_search(conn, "spotify", "track", "threshold", 50, ids)
    # Insert 8/10 = exactly 80 percent — should return the 8 found.
    for eid in ids[:8]:
        _insert_entity(conn, "spotify", "track", eid, {"id": eid})

    results = cache.get_search_results("spotify", "track", "threshold", 50)
    assert results is not None
    assert len(results) == 8
    assert [r["id"] for r in results] == ids[:8]


def test_empty_result_ids_returns_empty_list(cache_with_db):
    cache, conn = cache_with_db
    _insert_search(conn, "spotify", "track", "empty", 50, [])
    assert cache.get_search_results("spotify", "track", "empty", 50) == []


def test_expired_search_returns_none(cache_with_db):
    cache, conn = cache_with_db
    old = (datetime.now() - timedelta(days=10)).isoformat()
    _insert_search(conn, "spotify", "track", "stale", 50, ["a"], created_at=old)
    _insert_entity(conn, "spotify", "track", "a", {"id": "a"})

    assert cache.get_search_results("spotify", "track", "stale", 50) is None


def test_cache_miss_on_unknown_query(cache_with_db):
    cache, _ = cache_with_db
    assert cache.get_search_results("spotify", "track", "nothing cached", 50) is None


def test_batch_lookup_uses_single_round_trip(tmp_path):
    """Sanity check that resolution does not issue one SELECT per entity_id."""
    db_path = tmp_path / "cache2.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE metadata_cache_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT, search_type TEXT, query_normalized TEXT,
            search_limit INTEGER, result_ids TEXT, created_at TEXT,
            last_accessed_at TEXT, access_count INTEGER DEFAULT 0
        );
        CREATE TABLE metadata_cache_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT, entity_type TEXT, entity_id TEXT, raw_json TEXT
        );
        """
    )
    ids = [f"e{i}" for i in range(50)]
    _insert_search(conn, "spotify", "track", "bulk", 50, ids)
    for eid in ids:
        _insert_entity(conn, "spotify", "track", eid, {"id": eid})

    raw_selects = {"n": 0}

    class CountingConnection:
        def __init__(self, inner):
            self._inner = inner

        def cursor(self):
            inner_cursor = self._inner.cursor()

            class CountingCursor:
                def __init__(self, c):
                    self._c = c

                def execute(self, sql, params=()):
                    if "raw_json FROM metadata_cache_entities" in sql:
                        raw_selects["n"] += 1
                    return self._c.execute(sql, params)

                def fetchone(self):
                    return self._c.fetchone()

                def fetchall(self):
                    return self._c.fetchall()

            return CountingCursor(inner_cursor)

        def commit(self):
            return self._inner.commit()

        def close(self):
            return self._inner.close()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _connect():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return CountingConnection(c)

    fake_db = MagicMock()
    fake_db._get_connection.side_effect = _connect
    cache = MetadataCache()
    cache._get_db = lambda: fake_db  # type: ignore[method-assign]

    results = cache.get_search_results("spotify", "track", "bulk", 50)

    assert results is not None and len(results) == 50
    # With batching, 50 entities should resolve in a single SELECT, not 50.
    assert raw_selects["n"] == 1

