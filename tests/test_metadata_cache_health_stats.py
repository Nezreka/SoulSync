"""The repair page's cache health read every cached row, raw_json and all:
33s on a 340k-row cache (review 5 perf smoke). Every query it runs must read
an index, never the table, and the numbers must not change."""

from __future__ import annotations

import pytest

from core.metadata.cache import MetadataCache
from database.music_database import MusicDatabase


@pytest.fixture
def cache_and_db(tmp_path, monkeypatch):
    from core.metadata import cache as cache_module
    monkeypatch.setattr(cache_module, '_cache_instance', None, raising=False)
    db = MusicDatabase(str(tmp_path / 'cache.db'))
    result = MetadataCache()
    monkeypatch.setattr(result, '_get_db', lambda: db)
    conn = db._get_connection()
    try:
        rows = []
        for i in range(3000):
            name = f"Artist {i}"
            entity_id = str(i)
            if i % 500 == 0:
                name = "  Unknown Artist "
            elif i % 700 == 0:
                name = ""
                entity_id = f"{i}_tracks"
            rows.append((
                "spotify" if i % 2 else "deezer",
                "artist" if i % 3 else "album",
                entity_id, name, "x" * 3000,
                f"-{i % 40} days", 30 if i % 4 else 7, i % 5,
            ))
        conn.executemany(
            "INSERT INTO metadata_cache_entities (source, entity_type, entity_id, name, raw_json, "
            "updated_at, ttl_days, access_count) "
            "VALUES (?, ?, ?, ?, ?, datetime('now', ?), ?, ?)", rows)
        conn.execute("INSERT INTO musicbrainz_cache (entity_type, entity_name, musicbrainz_id) "
                     "VALUES ('artist', 'a', NULL), ('artist', 'b', 'mbid')")
        conn.commit()
        conn.execute("ANALYZE")
        conn.commit()
    finally:
        conn.close()
    return result, db


def _traced_statements(cache, db, monkeypatch):
    statements = []
    real = db._get_connection

    def _conn():
        conn = real()
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(db, '_get_connection', _conn)
    stats = cache.get_health_stats()
    monkeypatch.setattr(db, '_get_connection', real)
    return stats, [s for s in statements if s.lstrip().upper().startswith('SELECT')]


def test_health_stats_never_scan_the_cache_table(cache_and_db, monkeypatch):
    cache, db = cache_and_db
    stats, statements = _traced_statements(cache, db, monkeypatch)
    assert stats['total_entities'] == 3000
    conn = db._get_connection()
    try:
        for sql in statements:
            if 'metadata_cache_entities' not in sql:
                continue
            plan = [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql)]
            bare = [p for p in plan if p.strip() == 'SCAN metadata_cache_entities']
            assert not bare, (sql, plan)
    finally:
        conn.close()


def test_health_stats_numbers_match_a_plain_count(cache_and_db):
    cache, db = cache_and_db
    stats = cache.get_health_stats()
    conn = db._get_connection()
    try:
        want = conn.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN (TRIM(name) = '' OR LOWER(TRIM(name)) = 'unknown artist')
                            AND entity_id NOT LIKE '%\\_tracks' ESCAPE '\\' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN julianday('now') - julianday(updated_at) > ttl_days - 1 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN julianday('now') - julianday(updated_at) > ttl_days - 7 THEN 1 ELSE 0 END),
                   ROUND(AVG(julianday('now') - julianday(updated_at)), 1),
                   SUM(access_count)
              FROM metadata_cache_entities""").fetchone()
    finally:
        conn.close()
    assert stats['total_entities'] == want[0]
    assert stats['junk_entities'] == want[1] and want[1] > 0
    assert stats['expiring_24h'] == want[2]
    assert stats['expiring_7d'] == want[3] and want[3] > 0
    assert stats['avg_age_days'] == want[4]
    assert stats['total_access_hits'] == want[5]
    assert sum(stats['by_source'].values()) == 3000
    assert sum(stats['by_type'].values()) == 3000
    assert stats['total_musicbrainz'] == 2 and stats['stale_mb_nulls'] == 1
    assert list(stats) == [
        'total_entities', 'junk_entities', 'expiring_24h', 'expiring_7d',
        'avg_age_days', 'total_access_hits', 'by_source', 'by_type',
        'total_searches', 'total_musicbrainz', 'stale_mb_nulls',
    ]
