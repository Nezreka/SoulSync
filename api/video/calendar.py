"""Video Calendar — upcoming TV episodes for OWNED shows.

GET /api/video/calendar?days=N → episodes airing from today through today+N-1,
grouped client-side into the agenda view. Isolated: reads only video_library.db
via VideoDatabase, writes nothing, never touches the music side.
"""

from __future__ import annotations

from datetime import date, timedelta

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video.calendar")


def register_routes(bp):
    @bp.route("/calendar", methods=["GET"])
    def video_calendar():
        from . import get_video_db
        try:
            days = request.args.get("days", default=7, type=int) or 7
            days = max(1, min(days, 90))            # clamp: today .. +90d
            start = date.today()
            end = start + timedelta(days=days - 1)
            db = get_video_db()

            # One-time backfill: existing shows matched TVDB before air time was
            # captured, so re-queue them once (background, only those missing it).
            try:
                if (db.get_setting("airs_time_backfill") or "") != "1":
                    n = db.requeue_shows_for_airtime()
                    db.set_setting("airs_time_backfill", "1")
                    if n:
                        logger.info("calendar: queued %d shows for TVDB air-time backfill", n)
            except Exception:
                logger.exception("airs_time backfill queue failed")

            eps = db.calendar_upcoming(start.isoformat(), end.isoformat())

            # Per-date counts drive the day-strip dots without a second query.
            counts: dict[str, int] = {}
            owned = 0
            for e in eps:
                counts[e["air_date"]] = counts.get(e["air_date"], 0) + 1
                if e.get("has_file"):
                    owned += 1
            return jsonify({
                "today": start.isoformat(),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "days": days,
                "counts_by_date": counts,
                "total": len(eps),
                "owned": owned,
                "episodes": eps,
            })
        except Exception:
            logger.exception("video calendar failed")
            return jsonify({"error": "calendar failed"}), 500
