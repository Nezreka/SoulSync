"""A wishlist add looks the track up by provider id through the index.

The bare json_extract matched no index and parsed every track's external_ids:
2.2s per add on a 312k-track library, 7 minutes for one playlist's 68 misses."""

from __future__ import annotations

import sqlite3

from core.library2.provider_ids import external_id_sql, indexed_provider_id_sql


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE lib2_tracks (id INTEGER PRIMARY KEY, spotify_id TEXT, external_ids TEXT)")
    expr = external_id_sql("external_ids", "deezer")
    conn.execute(f"CREATE INDEX idx_ext_deezer ON lib2_tracks({expr}) WHERE {expr} IS NOT NULL")
    conn.executemany("INSERT INTO lib2_tracks (id, spotify_id, external_ids) VALUES (?, ?, ?)", [
        (1, None, '{"deezer": "1755785907"}'),
        (2, None, '{"deezer": 3135556}'),          # stored as a json number
        (3, "sp1", '{}'),
    ])
    return conn


def test_the_lookup_uses_the_expression_index():
    conn = _db()
    sql = f"SELECT id FROM lib2_tracks t WHERE {indexed_provider_id_sql('deezer', alias='t')} = ?"
    plan = " ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, ("1755785907",)))
    assert "idx_ext_deezer" in plan
    assert [r[0] for r in conn.execute(sql, ("1755785907",))] == [1]


def test_a_numeric_json_id_matches_its_text_form():
    conn = _db()
    sql = f"SELECT id FROM lib2_tracks t WHERE {indexed_provider_id_sql('deezer', alias='t')} = ?"
    assert [r[0] for r in conn.execute(sql, ("3135556",))] == [2]


def test_a_dedicated_column_stays_the_column():
    assert indexed_provider_id_sql("spotify", alias="t") == "t.spotify_id"
    assert indexed_provider_id_sql("", alias="t") is None
