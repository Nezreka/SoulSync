"""Discover's genre explorer and new releases read only the cached rows that
carry genres / a release date, through partial indexes: they walked every
cached artist or album (~1.2s each on 550k rows)."""

from __future__ import annotations

from database.music_database import MusicDatabase


def _plan(db, sql, params=()):
    conn = db._get_connection()
    try:
        return " ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params))
    finally:
        conn.close()


def _realistic(db):
    """thousands of cached artists, a few with genres, and planner stats: an
    empty table ties every index and tells nothing"""
    conn = db._get_connection()
    try:
        conn.executemany(
            "INSERT INTO metadata_cache_entities (source, entity_type, entity_id, name, genres, raw_json) "
            "VALUES (?, 'artist', ?, ?, ?, '{}')",
            [("spotify", str(i), f"Artist {i}", '["pop"]' if i % 200 == 0 else None) for i in range(4000)])
        conn.commit()
        conn.execute("ANALYZE")
        conn.commit()
    finally:
        conn.close()


def test_the_genre_rows_come_from_their_partial_index(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _realistic(db)
    plan = _plan(db, "SELECT name, genres FROM metadata_cache_entities WHERE entity_type = 'artist' "
                     "AND genres IS NOT NULL AND genres != '' AND genres != '[]' AND source IN (?,?)",
                 ("spotify", "deezer"))
    assert "idx_mce_with_genres" in plan, plan


def test_recent_releases_come_from_the_release_date_index(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    plan = _plan(db, "SELECT name FROM metadata_cache_entities WHERE entity_type = 'album' "
                     "AND release_date != '' AND release_date IS NOT NULL "
                     "AND release_date >= date('now', '-180 days') ORDER BY release_date DESC")
    assert "idx_mce_release_date" in plan, plan
