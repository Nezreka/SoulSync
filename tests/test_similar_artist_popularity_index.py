"""The popularity backfill's per-artist update finds its rows through an index.

It updates by case-folded name about 30k times a sweep; each one scanned the
234k-row table while holding the write lock, and showed up in the lock
watchdog's holder list (Oct 31 2026)."""

from __future__ import annotations

from database.music_database import MusicDatabase


def test_the_case_folded_name_update_uses_an_index(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    conn = db._get_connection()
    try:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' "
                            "AND name='idx_similar_artists_profile_name_lower'").fetchone()
        # a real library's table, with the stats the first start gathers for it
        conn.executemany(
            "INSERT INTO similar_artists (profile_id, source_artist_id, similar_artist_name) "
            "VALUES (1, ?, ?)", [(str(i % 300), f"Artist {i}") for i in range(3000)])
        conn.commit()
        conn.execute("ANALYZE idx_similar_artists_profile_name_lower")
        plan = " ".join(r[3] for r in conn.execute(
            "EXPLAIN QUERY PLAN UPDATE similar_artists SET popularity = 1 "
            "WHERE LOWER(similar_artist_name) = LOWER(?) AND profile_id = ?", ("Air", 1)))
    finally:
        conn.close()
    assert "idx_similar_artists_profile_name_lower" in plan, plan
