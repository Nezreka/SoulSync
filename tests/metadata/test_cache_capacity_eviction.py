"""Tests for the metadata-cache hard capacity cap (LRU eviction).

TTL-only eviction had no upper bound, so heavy in-window caching let
metadata_cache_entities reach ~1.8M rows / 7.6 GB. evict_over_capacity adds an
LRU row ceiling. We test the pure decision function directly and the SQL
behavior against a real temp DB (proves it drops the LEAST-recently-accessed
rows, not arbitrary ones).
"""

from __future__ import annotations

import sqlite3
import sys
import types

import pytest

# Minimal stubs so importing core.metadata.cache doesn't drag in spotipy/config.
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

    class _DummyCM:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "plex"

    settings_mod.config_manager = _DummyCM()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod

from core.metadata.cache import (  # noqa: E402
    MetadataCache,
    entities_to_evict_for_capacity,
)


# ── pure decision function ─────────────────────────────────────────────────

def test_evict_count_over_cap():
    assert entities_to_evict_for_capacity(1000, 250) == 750


def test_evict_count_at_or_under_cap_is_zero():
    assert entities_to_evict_for_capacity(250, 250) == 0
    assert entities_to_evict_for_capacity(10, 250) == 0


def test_cap_zero_or_negative_means_no_eviction():
    assert entities_to_evict_for_capacity(10_000, 0) == 0
    assert entities_to_evict_for_capacity(10_000, -1) == 0


def test_never_negative():
    assert entities_to_evict_for_capacity(0, 100) == 0


# ── evict_over_capacity SQL behavior (real temp DB) ────────────────────────

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

    def __exit__(self, *a):
        pass


class _TempCache(MetadataCache):
    """MetadataCache whose _get_db returns a shim over a shared in-memory DB
    holding just the entities table — enough to exercise evict_over_capacity."""

    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE metadata_cache_entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT, entity_type TEXT, entity_id TEXT,
                name TEXT, raw_json TEXT,
                last_accessed_at TIMESTAMP,
                ttl_days INTEGER DEFAULT 30
            )
        """)
        self._conn.commit()

    def _get_db(self):
        outer = self

        class _DB:
            def _get_connection(self_inner):
                return _NonClosingConn(outer._conn)
        return _DB()

    # evict_over_capacity uses self._run_maintenance_write -> _get_db; the base
    # _run_maintenance_write just calls the operation. Add a tiny passthrough
    # if the base needs it (it does in the real class).
    def _run_maintenance_write(self, label, operation, default=0):
        try:
            return operation(self._get_db()._get_connection())
        except Exception:
            return default


def _add_rows(cache, specs):
    """specs: list of (entity_id, last_accessed_at). Inserts rows."""
    cur = cache._conn.cursor()
    for eid, ts in specs:
        cur.execute(
            "INSERT INTO metadata_cache_entities (source, entity_type, entity_id, name, raw_json, last_accessed_at) "
            "VALUES ('spotify','artist',?,?, '{}', ?)",
            (eid, eid, ts),
        )
    cache._conn.commit()


def _ids(cache):
    return [r["entity_id"] for r in cache._conn.execute(
        "SELECT entity_id FROM metadata_cache_entities ORDER BY entity_id"
    ).fetchall()]


def test_evict_over_capacity_drops_least_recently_accessed():
    cache = _TempCache()
    # 5 rows, distinct access times. Cap at 3 -> evict the 2 oldest-accessed.
    _add_rows(cache, [
        ("a", "2026-05-01T00:00:00"),  # oldest -> evicted
        ("b", "2026-05-02T00:00:00"),  # oldest -> evicted
        ("c", "2026-05-03T00:00:00"),
        ("d", "2026-05-04T00:00:00"),
        ("e", "2026-05-05T00:00:00"),  # newest -> kept
    ])
    evicted = cache.evict_over_capacity(max_rows=3)
    assert evicted == 2
    assert _ids(cache) == ["c", "d", "e"]   # a, b gone (LRU)


def test_evict_over_capacity_noop_under_cap():
    cache = _TempCache()
    _add_rows(cache, [("a", "2026-05-01T00:00:00"), ("b", "2026-05-02T00:00:00")])
    assert cache.evict_over_capacity(max_rows=10) == 0
    assert _ids(cache) == ["a", "b"]


def test_evict_over_capacity_disabled_with_zero_cap():
    cache = _TempCache()
    _add_rows(cache, [(str(i), f"2026-05-0{i}T00:00:00") for i in range(1, 6)])
    assert cache.evict_over_capacity(max_rows=0) == 0
    assert len(_ids(cache)) == 5


def test_null_access_times_evicted_first():
    """Rows never accessed since insert (NULL last_accessed_at) are the
    coldest — they should go before any touched row."""
    cache = _TempCache()
    _add_rows(cache, [
        ("never1", None),
        ("never2", None),
        ("touched", "2026-05-01T00:00:00"),
    ])
    evicted = cache.evict_over_capacity(max_rows=1)
    assert evicted == 2
    assert _ids(cache) == ["touched"]
