"""Cache hits count their access stats without writing on the read path.

A hit used to UPDATE its row before returning, so every cache read waited on
the write lock behind other writers (3.7s seen during a sync). The stats are
now written in one batch shortly after.
"""

import json
import sqlite3
from unittest.mock import MagicMock

import pytest

from core.metadata import cache as metadata_cache
from core.metadata.cache import MetadataCache


@pytest.fixture
def cache_db(tmp_path, monkeypatch):
    # a long timer: nothing is written until flush_touches runs
    monkeypatch.setattr(metadata_cache, "_TOUCH_FLUSH_SECONDS", 3600.0)
    db_path = tmp_path / "cache.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE metadata_cache_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT, entity_type TEXT, entity_id TEXT, raw_json TEXT,
            updated_at TEXT, ttl_days INTEGER DEFAULT 30,
            last_accessed_at TEXT, access_count INTEGER DEFAULT 0
        );
        """
    )
    for eid in ("a", "b"):
        conn.execute(
            "INSERT INTO metadata_cache_entities (source, entity_type, entity_id, raw_json, updated_at) "
            "VALUES ('spotify', 'track', ?, ?, datetime('now', 'localtime'))",
            (eid, json.dumps({"id": eid})),
        )
    conn.commit()

    writes = []

    def _connect():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        c.set_trace_callback(lambda sql: writes.append(sql) if sql.lstrip().upper().startswith("UPDATE") else None)
        return c

    fake_db = MagicMock()
    fake_db._get_connection.side_effect = _connect
    cache = MetadataCache()
    cache._get_db = lambda: fake_db  # type: ignore[method-assign]
    yield cache, conn, writes
    timer = cache._touch_timer
    if timer is not None:
        timer.cancel()


def _stats(conn, eid):
    return conn.execute(
        "SELECT access_count, last_accessed_at FROM metadata_cache_entities WHERE entity_id = ?", (eid,)
    ).fetchone()


def test_hits_do_not_write_until_flushed(cache_db):
    cache, conn, writes = cache_db
    assert cache.get_entity("spotify", "track", "a") == {"id": "a"}
    found, missing = cache.get_entities_batch("spotify", "track", ["a", "b", "zz"])
    assert set(found) == {"a", "b"} and missing == ["zz"]

    assert writes == []
    assert _stats(conn, "a") == (0, None)

    cache.flush_touches()

    count_a, touched_a = _stats(conn, "a")
    count_b, _ = _stats(conn, "b")
    assert (count_a, count_b) == (2, 1)
    assert touched_a is not None


def test_failed_flush_keeps_the_hits(cache_db):
    cache, conn, _ = cache_db
    cache.get_entity("spotify", "track", "a")
    real = cache._get_db
    broken = MagicMock()
    broken._get_connection.side_effect = sqlite3.OperationalError("database is locked")
    cache._get_db = lambda: broken  # type: ignore[method-assign]
    cache.flush_touches()
    assert _stats(conn, "a") == (0, None)

    cache._get_db = real  # type: ignore[method-assign]
    cache.flush_touches()
    assert _stats(conn, "a")[0] == 1


def test_eviction_flushes_first(cache_db):
    cache, conn, _ = cache_db
    cache.get_entity("spotify", "track", "a")
    cache.evict_over_capacity(max_rows=1)
    # "a" was hit, so the never-touched "b" is the one evicted
    left = [r[0] for r in conn.execute("SELECT entity_id FROM metadata_cache_entities")]
    assert left == ["a"]
