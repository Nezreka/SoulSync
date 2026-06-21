"""SoulSync — VIDEO side API package (isolated).

A SEPARATE Flask blueprint from the music API (api_v1). It reads only
database/video_library.db via VideoDatabase and imports nothing from the music
API or music database layer. Registered in web_server.py with a single additive
line at url_prefix '/api/video', so music routing is untouched.
"""

from __future__ import annotations

import threading

from flask import Blueprint

from utils.logging_config import get_logger

logger = get_logger("video_api")

# Lazily-created, process-wide VideoDatabase handle. VideoDatabase itself guards
# schema init once-per-path, so this just avoids re-opening the wrapper.
_video_db = None
_video_db_lock = threading.Lock()


def get_video_db():
    """Return the shared VideoDatabase instance (created on first use)."""
    global _video_db
    if _video_db is None:
        with _video_db_lock:
            if _video_db is None:
                from database.video_database import VideoDatabase
                _video_db = VideoDatabase()
    return _video_db


def create_video_blueprint() -> Blueprint:
    """Build the isolated /api/video blueprint with all video sub-routes."""
    bp = Blueprint("video_api", __name__)

    from .dashboard import register_routes as reg_dashboard
    from .scan import register_routes as reg_scan
    from .library import register_routes as reg_library
    from .libraries import register_routes as reg_libraries
    from .poster import register_routes as reg_poster
    from .enrichment import register_routes as reg_enrichment
    from .detail import register_routes as reg_detail
    from .search import register_routes as reg_search
    from .discover import register_routes as reg_discover
    from .calendar import register_routes as reg_calendar
    from .watchlist import register_routes as reg_watchlist
    from .wishlist import register_routes as reg_wishlist
    from .youtube import register_routes as reg_youtube
    from .downloads import register_routes as reg_downloads
    from .manual_import import register_routes as reg_manual_import
    from .automations import register_routes as reg_automations
    reg_dashboard(bp)
    reg_scan(bp)
    reg_library(bp)
    reg_libraries(bp)
    reg_poster(bp)
    reg_enrichment(bp)
    reg_detail(bp)
    reg_search(bp)
    reg_discover(bp)
    reg_calendar(bp)
    reg_watchlist(bp)
    reg_wishlist(bp)
    reg_youtube(bp)
    reg_downloads(bp)
    reg_manual_import(bp)
    reg_automations(bp)

    return bp
