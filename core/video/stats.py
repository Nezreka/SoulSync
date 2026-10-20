"""
Video side numbers for the public api (#1723).

one read of video.db that a homepage widget can poll: what is in the
library, how big the db is and when it last changed, and where the
download queue stands. the music side has had /system/stats for this.
"""

import datetime as _dt

# the statuses a row sits in while it is still moving. failed covers the
# import step failing too, a widget only cares that it needs a look.
ACTIVE_STATUSES = ("queued", "searching", "downloading", "importing")
FAILED_STATUSES = ("failed", "import_failed")
DOWNLOAD_STATUSES = ACTIVE_STATUSES + FAILED_STATUSES + ("completed", "cancelled")


def _iso_utc(value):
    """sqlite CURRENT_TIMESTAMP text or an epoch number, as iso utc. None stays None."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).replace(".", "", 1).isdigit():
            stamp = _dt.datetime.fromtimestamp(float(value), tz=_dt.timezone.utc)
        else:
            stamp = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=_dt.timezone.utc)
        return stamp.replace(microsecond=0).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def video_stats(db, server_source=None, profile_id=1) -> dict:
    """library, database, downloads and wishlist counts. library numbers are
    scoped to the video server when one is given, same as the dashboard, so
    plex and jellyfin rows never add up together."""
    mw = " WHERE server_source=?" if server_source else ""
    sw = " AND s.server_source=?" if server_source else ""
    sv = (server_source,) if server_source else ()

    conn = db.connect()
    try:
        def scalar(sql, params=()):
            return conn.execute(sql, params).fetchone()[0] or 0

        episodes = conn.execute(
            "SELECT SUM(CASE WHEN e.has_file=1 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN e.has_file=0 AND e.monitored=1 AND e.air_date IS NOT NULL "
            "AND e.air_date <= date('now') THEN 1 ELSE 0 END) "
            "FROM episodes e JOIN shows s ON s.id=e.show_id WHERE 1=1" + sw, sv).fetchone()
        size_bytes = scalar(
            "SELECT COALESCE(SUM(size_bytes), 0) FROM media_files mf "
            "WHERE mf.movie_id IN (SELECT id FROM movies WHERE server_source=?) "
            "OR mf.episode_id IN (SELECT e.id FROM episodes e JOIN shows s ON s.id=e.show_id "
            "WHERE s.server_source=?)", sv + sv) if server_source else \
            scalar("SELECT COALESCE(SUM(size_bytes), 0) FROM media_files")
        last_update = conn.execute(
            "SELECT MAX(u) FROM (SELECT MAX(updated_at) u FROM movies" + mw +
            " UNION ALL SELECT MAX(updated_at) u FROM shows" + mw + ")", sv + sv).fetchone()[0]

        by_status = {s: 0 for s in DOWNLOAD_STATUSES}
        for status, n in conn.execute("SELECT status, COUNT(*) FROM video_downloads GROUP BY status"):
            by_status[status] = by_status.get(status, 0) + n
        speed = scalar("SELECT COALESCE(SUM(speed_bps), 0) FROM video_downloads WHERE status='downloading'")

        library = {
            "movies": scalar("SELECT COUNT(*) FROM movies" + mw, sv),
            "shows": scalar("SELECT COUNT(*) FROM shows" + mw, sv),
            "episodes": episodes[0] or 0,
            "episodes_missing": episodes[1] or 0,
            "size_bytes": size_bytes,
        }
    finally:
        conn.close()

    try:
        db_bytes = db.database_path.stat().st_size
    except OSError:
        db_bytes = None
    try:
        last_scan = db.get_setting("video_last_scan_at")
    except Exception:
        last_scan = None
    history = db.download_history_counts()
    wished = db.wishlist_counts(profile_id=profile_id)
    yt = db.youtube_wishlist_counts(profile_id=profile_id)

    return {
        "server": server_source,
        "library": library,
        "database": {
            "size_mb": round(db_bytes / (1024 * 1024), 2) if db_bytes is not None else None,
            "last_update": _iso_utc(last_update),
            "last_scan": _iso_utc(last_scan),
        },
        "downloads": {
            "active": sum(by_status[s] for s in ACTIVE_STATUSES),
            "failed": sum(by_status[s] for s in FAILED_STATUSES),
            "completed": by_status["completed"],
            "cancelled": by_status["cancelled"],
            "by_status": by_status,
            "speed_bps": speed,
            "completed_all_time": history.get("total", 0),
        },
        "wishlist": {
            "movies": wished.get("movie", 0),
            "episodes": wished.get("episode", 0),
            "videos": yt.get("video", 0),
            "total": wished.get("total", 0) + yt.get("video", 0),
        },
    }
