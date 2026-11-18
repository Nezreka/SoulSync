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
    db = _video_db
    if db is None:
        with _video_db_lock:
            db = _video_db
            if db is None:
                from database.video_database import VideoDatabase
                db = VideoDatabase()
                # Anyone installing a handle from outside must hold
                # _video_db_lock, and an installed handle outranks a lazily
                # built one — publish only if the slot is still empty, and
                # yield to an install that beat us to it.
                if _video_db is None:
                    _video_db = db
                    _seed_folder_defaults(db)
    return db


def _seed_folder_defaults(db):
    """docker: start unset video folders on the compose file's mounts"""
    try:
        from core.onboarding.environment import is_docker
        from core.onboarding.paths import mount_points
        from core.video.library_roots import seed_docker_defaults
        seeded = seed_docker_defaults(db, docker=is_docker(), mounts=mount_points())
        if seeded:
            logger.info("video folders started on the compose defaults: %s", seeded)
    except Exception as exc:   # noqa: BLE001 - a nicety, the folders can be set by hand
        logger.debug("video folder defaults skipped: %s", exc)


_TITLE_PATH = __import__("re").compile(
    r"/(?:detail|title-delete|files/extra-copies)/(movie|show)/(\d+)")
_SHOW_PATH = __import__("re").compile(r"/(?:files/consolidate|detail/show)/(\d+)")
_FILE_PATH = __import__("re").compile(r"/files/(\d+)/")


def _refresh_manager_for(request) -> None:
    """which title a write was about, from its path or body, refreshed in the
    library manager's summary. a write that names no title is left to the
    summary's own rebuild."""
    from core.video import library_manager
    path = request.path or ""
    if path.startswith("/api/video/bulk") or path.startswith("/api/video/manager"):
        return                                   # the bulk job refreshes its own
    db = get_video_db()
    m = _TITLE_PATH.search(path)
    if m:
        library_manager.refresh(db, m.group(1), [int(m.group(2))])
        return
    m = _SHOW_PATH.search(path)
    if m:
        library_manager.refresh(db, "show", [int(m.group(1))])
        return
    m = _FILE_PATH.search(path)
    if m:
        conn = db._get_connection()
        try:
            row = conn.execute(
                "SELECT mf.movie_id, e.show_id FROM media_files mf "
                "LEFT JOIN episodes e ON e.id = mf.episode_id WHERE mf.id=?",
                (int(m.group(1)),)).fetchone()
        finally:
            conn.close()
        if row and row[0]:
            library_manager.refresh(db, "movie", [int(row[0])])
        elif row and row[1]:
            library_manager.refresh(db, "show", [int(row[1])])
        else:
            library_manager.forget(db)           # the file is gone: whose was it?
        return
    if path.endswith("/monitor") or path.endswith("/files/bulk"):
        body = request.get_json(silent=True) or {}
        kind, item_id = body.get("kind"), body.get("id") or body.get("library_id")
        if kind in ("movie", "show") and isinstance(item_id, int):
            library_manager.refresh(db, kind, [item_id])
        elif path.endswith("/files/bulk"):
            library_manager.forget(db)


def create_video_blueprint() -> Blueprint:
    """Build the isolated /api/video blueprint with all video sub-routes."""
    bp = Blueprint("video_api", __name__)

    # Profile permission guards behind the frontend gating (#2166). What each
    # path needs lives in one table, access.route_access, which the route
    # inventory reads too; this gate enforces it off the request's effective
    # permissions (g.perms, put there by web_server's before_request):
    #   • management surfaces + settings that expose or mutate tokens, keys,
    #     server / slskd / download config → admin (reads too where they leak)
    #   • config the Settings page writes but content views read → admin writes
    #   • everything else → movies or tv access; a path that names its kind
    #     (/detail/movie/…, /tmdb/show/…) needs that kind's own access
    #   • actions that fetch something → "download right away" for the kind
    #     the body names (movies.get / tv.get); youtube follows also need
    #     video.youtube
    @bp.before_request
    def _video_perm_gate():
        from flask import request, jsonify
        from . import access
        path = request.path or ""
        method = request.method

        need = access.route_access(path, method)
        if need[0] == "public":
            # the iCal feed is fetched by calendar apps with no session at all.
            # its token is the credential, and the route checks that token's
            # profile (video access, rating caps) itself.
            return None

        if access.is_admin():
            # a request web_server didn't stamp with permissions reads the old
            # g stamps as they were: a can_download of False holds even here
            from flask import g
            if (not hasattr(g, "perms") and getattr(g, "can_download", True) is False
                    and access.is_get_action(path, method)):
                return jsonify({"error": "Downloads are disabled for this profile."}), 403
            return None

        def _no(error, perm, status=403):
            body = dict(access.P.refusal_body(perm))
            body["error"] = error
            return jsonify(body), status

        # Per-profile side access: a music-only profile gets NOTHING from the
        # video blueprint (its whole UI is hidden for them, any request here is
        # a deep link or a probe).
        if not access.any_side():
            return _no("Video access is disabled for this profile.", "movies.access")

        if need[0] == "admin":
            return jsonify({"error": "Admin only."}), 403

        # a path that names its kind: a tv-only profile never reaches a movie
        kind = access.kind_in_path(path)
        if kind and not access.has_kind(kind):
            return access.kind_refusal(kind)

        if access.is_get_action(path, method):
            body = request.get_json(silent=True)
            youtube = path.startswith(access.YOUTUBE_PREFIXES)
            if youtube:
                if not access.can("video.youtube"):
                    return access.refusal("video.youtube")
                if not access.may_get(None):
                    return _no("Downloads are disabled for this profile.", "movies.get")
                return None
            k = access.path_kind(path, body)
            if k and not access.has_kind(k):
                return access.kind_refusal(k)
            if not access.may_get(k):
                row = access.row_for(k) or "movies"
                return _no("Downloads are disabled for this profile.", "%s.get" % row)
        return None

    # the library manager keeps each title summed up in memory. a write to
    # one title, from any page, refreshes that title's row right away; the
    # rest wait for the next rebuild
    @bp.after_request
    def _keep_manager_current(response):
        from flask import request
        try:
            if request.method not in ("GET", "HEAD", "OPTIONS") and response.status_code < 400:
                _refresh_manager_for(request)
        except Exception:   # noqa: BLE001 - never turn a good answer into an error
            logger.debug("library manager refresh hook failed", exc_info=True)
        return response

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
    from .watch import register_routes as reg_watch
    from .manual_match import register_routes as reg_manual_match
    from .subtitles import register_routes as reg_subtitles
    from .library_manage import register_routes as reg_library_manage
    from .manager import register_routes as reg_manager
    from .title_status import register_routes as reg_title_status
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
    reg_watch(bp)
    reg_manual_match(bp)
    reg_subtitles(bp)
    reg_library_manage(bp)
    reg_manager(bp)
    reg_title_status(bp)

    # the route inventory (#2166): every video view declares what it needs,
    # read from the same table the gate enforces, once the routes are on the app
    bp.record_once(_declare_access)

    return bp


_ORDER = {"public": 0, "perm": 1, "admin": 2}


def _declare_access(state) -> None:
    """mark each video view with route_access for its rule(s). a view whose
    methods differ (a GET any member may read, a POST that's the admin's)
    declares the least it needs to be called at all, plus every method's own
    answer on ``_soulsync_access_by_method``."""
    from core.permissions import ACCESS_ATTR
    from .access import route_access
    app = state.app
    prefix = "%s." % state.name
    by_endpoint: dict = {}
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith(prefix):
            continue
        for method in sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}):
            by_endpoint.setdefault(rule.endpoint, {})["%s %s" % (method, rule.rule)] = \
                route_access(rule.rule, method)
    for endpoint, answers in by_endpoint.items():
        fn = app.view_functions.get(endpoint)
        if fn is None:
            continue
        least = min(answers.values(), key=lambda a: _ORDER.get(a[0], 1))
        try:
            setattr(fn, ACCESS_ATTR, least)
            fn._soulsync_access_by_method = answers
        except (AttributeError, TypeError):  # pragma: no cover - builtins
            pass
