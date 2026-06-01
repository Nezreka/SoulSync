"""Regression: metadata cache tables must self-heal when the migration marker
is stale.

After a DB corruption-recovery the `metadata` table (with the
'metadata_cache_v1' marker) can survive while the large
metadata_cache_entities/searches tables do not. A marker-only guard then
permanently skips re-creating them, so the cache silently stops working and the
browser shows nothing. _add_metadata_cache_tables must re-create the tables when
the marker is present but the tables are gone.
"""

from __future__ import annotations

from database.music_database import MusicDatabase


def _tables(cur):
    return {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'metadata_cache_%'"
    ).fetchall()}


def test_recreates_cache_tables_when_marker_stale(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    conn = db._get_connection()
    cur = conn.cursor()

    # Fresh DB: tables + marker present.
    assert 'metadata_cache_entities' in _tables(cur)
    assert cur.execute("SELECT value FROM metadata WHERE key='metadata_cache_v1'").fetchone()

    # Simulate corruption-recovery: marker survives, cache tables don't.
    cur.execute("DROP TABLE metadata_cache_entities")
    cur.execute("DROP TABLE metadata_cache_searches")
    conn.commit()
    assert 'metadata_cache_entities' not in _tables(cur)
    assert cur.execute("SELECT value FROM metadata WHERE key='metadata_cache_v1'").fetchone()  # stale marker

    # Re-run the migration — must self-heal despite the stale marker.
    db._add_metadata_cache_tables(cur)
    conn.commit()
    assert 'metadata_cache_entities' in _tables(cur)
    assert 'metadata_cache_searches' in _tables(cur)


def test_skips_when_marker_and_tables_both_present(tmp_path):
    # Idempotent: a healthy DB shouldn't error on re-run.
    db = MusicDatabase(str(tmp_path / "m2.db"))
    conn = db._get_connection()
    cur = conn.cursor()
    before = _tables(cur)
    db._add_metadata_cache_tables(cur)  # no-op fast path
    conn.commit()
    assert _tables(cur) == before
