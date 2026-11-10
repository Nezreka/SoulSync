"""the video dashboard took 10s+ on a 9k movie / 105k episode library: recently
added read every movie row to sort them, and the library size summed every
media file on each load."""

from __future__ import annotations

from database.video_database import VideoDatabase


def _db(tmp_path):
    return VideoDatabase(str(tmp_path / "video.db"))


def test_recent_movies_sort_on_an_index(tmp_path):
    db = _db(tmp_path)
    conn = db._get_connection()
    try:
        plan = " ".join(r[3] for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM movies WHERE server_source = ? "
            "ORDER BY added_at DESC LIMIT 20", ("plex",)))
    finally:
        conn.close()
    assert "idx_movies_server_added" in plan
    assert "TEMP B-TREE" not in plan


def test_library_size_is_read_once_then_cached(tmp_path, monkeypatch):
    db = _db(tmp_path)
    VideoDatabase._size_cache.clear()
    calls = []
    real = VideoDatabase._library_size

    def counting(self, conn, server_source, size_sql, sv):
        calls.append(server_source)
        return real(self, conn, server_source, size_sql, sv)

    monkeypatch.setattr(VideoDatabase, "_library_size", counting)
    first = db.dashboard_stats(server_source="plex")["library"]["size_bytes"]
    conn = db._get_connection()
    conn.execute("INSERT INTO movies(title, server_source, server_id) VALUES('M', 'plex', 'x1')")
    movie_id = conn.execute("SELECT id FROM movies").fetchone()[0]
    conn.execute("INSERT INTO media_files(movie_id, relative_path, size_bytes) VALUES(?, '/m.mkv', 5)", (movie_id,))
    conn.commit()
    conn.close()
    # inside the window the total stays what it was, a scan's worth of change
    # shows up on the next read after it
    assert db.dashboard_stats(server_source="plex")["library"]["size_bytes"] == first
    VideoDatabase._size_cache.clear()
    assert db.dashboard_stats(server_source="plex")["library"]["size_bytes"] == first + 5
