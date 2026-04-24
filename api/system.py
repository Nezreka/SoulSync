"""
System endpoints — status, activity feed, stats.
"""

import time

from flask import current_app
from .auth import require_api_key
from .helpers import api_success, api_error


def register_routes(bp):

    @bp.route("/system/status", methods=["GET"])
    @require_api_key
    def system_status():
        """Server status including uptime and service connectivity."""
        try:
            app = current_app._get_current_object()
            ctx = app.soulsync

            uptime_seconds = time.time() - getattr(app, "start_time", time.time())
            hours, remainder = divmod(int(uptime_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)

            spotify = ctx.get("spotify_client")
            spotify_ok = bool(spotify and spotify.is_authenticated())

            soulseek = ctx.get("soulseek_client")
            soulseek_ok = bool(soulseek)

            hydrabase = ctx.get("hydrabase_client")
            hydrabase_ok = False
            if hydrabase:
                try:
                    ws, _ = hydrabase.get_ws_and_lock()
                    hydrabase_ok = ws is not None and ws.connected
                except Exception:
                    pass

            return api_success({
                "uptime": f"{hours}h {minutes}m {seconds}s",
                "uptime_seconds": int(uptime_seconds),
                "services": {
                    "spotify": spotify_ok,
                    "soulseek": soulseek_ok,
                    "hydrabase": hydrabase_ok,
                },
            })
        except Exception as e:
            return api_error("SYSTEM_ERROR", str(e), 500)

    @bp.route("/system/activity", methods=["GET"])
    @require_api_key
    def system_activity():
        """Recent activity feed."""
        try:
            from core.import_runtime_state import activity_feed
            items = list(activity_feed) if activity_feed else []
            return api_success({"activities": items})
        except Exception as e:
            return api_error("SYSTEM_ERROR", str(e), 500)

    @bp.route("/system/stats", methods=["GET"])
    @require_api_key
    def system_stats():
        """Combined library + download statistics."""
        try:
            from database.music_database import get_database
            db = get_database()
            lib_stats = db.get_statistics_for_server()
            db_info = db.get_database_info_for_server()

            # Active download count
            download_count = 0
            try:
                from core.import_runtime_state import download_tasks, tasks_lock
                with tasks_lock:
                    download_count = sum(
                        1 for t in download_tasks.values()
                        if t.get("status") in ("downloading", "queued", "searching")
                    )
            except ImportError:
                pass

            return api_success({
                "library": {
                    "artists": lib_stats.get("artists", 0),
                    "albums": lib_stats.get("albums", 0),
                    "tracks": lib_stats.get("tracks", 0),
                },
                "database": {
                    "size_mb": db_info.get("database_size_mb"),
                    "last_update": db_info.get("last_update"),
                },
                "downloads": {
                    "active": download_count,
                },
            })
        except Exception as e:
            return api_error("SYSTEM_ERROR", str(e), 500)
