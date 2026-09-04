"""Per-movie synchronize - a deep scan scoped to ONE movie.

Fetches the movie from the active video server and reconciles the local row
through the scanner's own ingest path (upsert_movie). Deletion is deliberately
paranoid: a server error aborts, and "gone" requires the source adapter's
positive not-found signal.
"""

from __future__ import annotations

from utils.logging_config import get_logger

logger = get_logger("video.movie_sync")


class MovieSyncError(RuntimeError):
    """Sync could not run (server unreachable, wrong server, busy...)."""


def _file_count(db, movie_id: int) -> int:
    conn = db._get_connection()
    try:
        return conn.execute(
            "SELECT COUNT(*) c FROM media_files WHERE movie_id=?",
            (int(movie_id),),
        ).fetchone()["c"]
    finally:
        conn.close()


def sync_movie(db, movie_id: int) -> dict:
    """Reconcile ONE local movie against the server.

    Returns {status, title, files_added, files_removed, movie_removed} or raises
    MovieSyncError for expected refusal cases. Unexpected server failures are
    allowed to bubble so callers do not mistake a hiccup for deletion.
    """
    conn = db._get_connection()
    try:
        row = conn.execute(
            "SELECT id, title, server_id, server_source, tmdb_id FROM movies WHERE id=?",
            (int(movie_id),),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise MovieSyncError("Movie not found in the library")

    from core.video.scanner import get_video_scanner
    if (get_video_scanner(db).get_status() or {}).get("state") == "running":
        raise MovieSyncError("A library scan is already running - try again when it finishes")

    from core.video.sources import get_active_video_source
    source = get_active_video_source()
    if source is None:
        raise MovieSyncError("No video server configured/reachable")
    if source.server_name != row["server_source"]:
        raise MovieSyncError(
            "This movie belongs to %s but the active server is %s"
            % (row["server_source"], source.server_name)
        )
    if not hasattr(source, "movie_item"):
        raise MovieSyncError("This video server does not support per-movie sync")

    item = source.movie_item(row["server_id"], title=row["title"], tmdb_id=row["tmdb_id"])
    if item is None:
        conn = db._get_connection()
        try:
            conn.execute("DELETE FROM movies WHERE id=?", (int(movie_id),))
            conn.commit()
        finally:
            conn.close()
        logger.info("movie sync: '%s' verified gone from %s - removed locally",
                    row["title"], row["server_source"])
        return {"status": "ok", "title": row["title"], "movie_removed": True,
                "files_added": 0, "files_removed": 0}

    files_before = _file_count(db, int(movie_id))
    new_id = db.upsert_movie(row["server_source"], item, preserve_enrichment=True)
    target_id = int(new_id or movie_id)

    if target_id != int(movie_id):
        conn = db._get_connection()
        try:
            conn.execute("DELETE FROM movies WHERE id=?", (int(movie_id),))
            conn.commit()
        finally:
            conn.close()
        logger.info("movie sync: '%s' re-keyed on %s (%s -> %s) - row healed",
                    row["title"], row["server_source"], row["server_id"], item.get("server_id"))

    metadata_refresh = "skipped"
    try:
        from core.video.enrichment.engine import get_video_enrichment_engine
        res = get_video_enrichment_engine().refresh_movie_art(target_id)
        metadata_refresh = "ok" if (res and res.get("ok")) else "failed:%s" % ((res or {}).get("reason") or "unknown")
    except Exception as e:  # noqa: BLE001 - metadata refresh is useful, not required
        metadata_refresh = "error:%s" % e
        logger.warning("movie sync: metadata refresh failed for '%s': %s", row["title"], e)

    files_after = _file_count(db, target_id)
    return {
        "status": "ok", "title": row["title"], "movie_removed": False,
        "movie_id": target_id, "rekeyed": target_id != int(movie_id),
        "metadata_refresh": metadata_refresh,
        "files_added": max(0, files_after - files_before),
        "files_removed": max(0, files_before - files_after),
    }
