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

    # Profile permission guards behind the frontend gating. Uses flask.g (set
    # app-wide by web_server's before_request: profile_id — 1==admin — and
    # can_download) so this stays isolated from the music DB. Parity with the
    # music side's @admin_only on Settings-class endpoints:
    #   • Overlay Studio / Import / Collections / Repair (management) → admin
    #   • Settings that EXPOSE or MUTATE tokens, API keys, server / slskd /
    #     download config → admin for BOTH reads and writes (a GET here returns
    #     raw keys — e.g. /enrichment/config, /downloads/slskd)
    #   • Config the Settings page WRITES but content views legitimately READ
    #     (library paths, quality tiers, server presence) → admin for WRITES only
    #   • download-triggering actions → require can_download (mirrors music)
    @bp.before_request
    def _video_perm_gate():
        from flask import request, g, jsonify
        path = request.path or ""
        writing = request.method in ("POST", "PUT", "PATCH", "DELETE")

        def _p(*prefixes):
            return any(path.startswith(x) for x in prefixes)

        # Admin = the profile's REAL is_admin flag (web_server stashes g.is_admin;
        # music supports secondary admins, and the frontend gates on the same
        # flag — a profile-1-only check here split-brained against it). Fallback
        # keeps the old convention when g wasn't populated (tests, edge callers).
        is_admin = bool(getattr(g, "is_admin", getattr(g, "profile_id", 1) == 1))

        # Per-profile side access: a music-only profile gets NOTHING from the
        # video blueprint (its whole UI is hidden for them — any request here is
        # a deep link or a probe). Admins always have both sides.
        if not is_admin and getattr(g, "allowed_sides", "both") == "music":
            return jsonify({"error": "Video access is disabled for this profile."}), 403

        # Management surfaces + credential/settings-only endpoints — admin for ANY
        # method (their GETs leak raw tokens/keys or expose server config, and are
        # only ever hit by the admin-only Settings page).
        admin = _p("/api/video/overlays", "/api/video/import", "/api/video/collections",
                   "/api/video/repair",
                   "/api/video/server-config", "/api/video/jellyfin", "/api/video/libraries",
                   "/api/video/organization", "/api/video/downloads/slskd",
                   "/api/video/enrichment/config", "/api/video/enrichment/priority",
                   "/api/video/notifications",   # P11: GETs return webhook URLs/bot tokens
                   "/api/video/backups")         # P10: restore/download the whole database
        # Config the Settings page WRITES but content views (download modal / grab)
        # legitimately READ — gate the writes, leave the GETs open.
        if writing:
            admin = admin or _p("/api/video/server", "/api/video/downloads/config",
                                 "/api/video/downloads/quality",
                                 "/api/video/downloads/youtube-quality",
                                 "/api/video/enrichment")   # all enrichment mutations
            # Library / metadata MANAGEMENT — parity with music's @admin_only library edits
            # (delete/sync/clear-match/delete-batch) + the download blocklist config. Content
            # views only READ metadata (the detail GET stays open); these MUTATE the library.
            admin = admin or _p("/api/video/bulk", "/api/video/monitor",
                                 "/api/video/poster/set", "/api/video/downloads/blocklist") \
                or path.endswith(("/metadata", "/lock", "/refresh-art"))
        if admin and not is_admin:
            return jsonify({"error": "Admin only."}), 403

        if writing and not getattr(g, "can_download", True) and _p(
                "/api/video/downloads/grab", "/api/video/downloads/retry",
                "/api/video/youtube/download", "/api/video/wishlist/add",
                "/api/video/watchlist/add", "/api/video/youtube/wishlist/add"):
            return jsonify({"error": "Downloads are disabled for this profile."}), 403

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
    from .overlays import register_routes as reg_overlays
    from .collections import register_routes as reg_collections
    from .bulk import register_routes as reg_bulk
    from .repair import register_routes as reg_repair
    from .issues import register_routes as reg_issues
    from .requests import register_routes as reg_requests
    from .notifications import register_routes as reg_notifications
    from .backups import register_routes as reg_backups
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
    reg_overlays(bp)
    reg_collections(bp)
    reg_bulk(bp)
    reg_repair(bp)
    reg_issues(bp)
    reg_requests(bp)
    reg_notifications(bp)
    reg_backups(bp)

    return bp
