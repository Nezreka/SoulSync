"""The "Your library" tab: what you own of a title and the file-level actions.

GET  /api/video/detail/<kind>/<id>/library[?season=N]
    the tab's payload (core.video.library_manage). a show's seasons always
    carry counts; only ``season`` gets the per-file disk checks. file paths
    are an admin's view: other profiles get the facts without them.
GET  /api/video/files/<file_id>/rename-plan
    {current, expected, conforms} — the preview before a rename.
POST /api/video/files/<file_id>/rename
    rename to the current template (sidecars follow, nothing overwritten).
POST /api/video/files/<file_id>/delete
    move to the recycle bin and forget the file. never a silent permanent
    delete: a file with no recycle bin is refused.
GET  /api/video/detail/<kind>/<id>/identify/search?q=
    TMDB candidates for "Fix match".
POST /api/video/detail/<kind>/<id>/identify   {tmdb_id}
    re-point the title and lock it (core.video.identity).
POST /api/video/detail/<kind>/<id>/identify/release
    undo: the next scan takes the server's identity again.

Every write is admin-gated by the blueprint's permission gate.
"""

from __future__ import annotations

import threading

from flask import g, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.library_manage")

_KINDS = ("movie", "show")
_PATH_KEYS = ("path", "local_path", "folder")


def _is_admin() -> bool:
    return bool(getattr(g, "is_admin", getattr(g, "profile_id", 1) == 1))


def _strip_paths(obj):
    """a non-admin profile sees what's owned, not where it lives."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _PATH_KEYS:
                out[k] = None
            elif k == "naming" and isinstance(v, dict):
                out[k] = {"conforms": v.get("conforms")}
            else:
                out[k] = _strip_paths(v)
        return out
    if isinstance(obj, list):
        return [_strip_paths(x) for x in obj]
    return obj


def _refresh_server(kind: str) -> None:
    """tell the media server to look again, so a rename or delete shows up
    there without waiting for its own schedule. best-effort, off-thread."""
    def run():
        try:
            from core.video.sources import refresh_video_server_sections
            refresh_video_server_sections("movie" if kind == "movie" else "show")
        except Exception:   # noqa: BLE001 - the server's own scan catches up anyway
            logger.debug("server refresh after a file change failed", exc_info=True)
    threading.Thread(target=run, name="video-file-refresh", daemon=True).start()


def register_routes(bp):
    @bp.route("/detail/<kind>/<int:item_id>/library", methods=["GET"])
    def video_title_library(kind, item_id):
        from . import get_video_db
        from .kids import restricted_response, video_cap
        from core.content_filter import title_allowed
        from core.video import library_manage
        if kind not in _KINDS:
            return jsonify({"error": "kind must be movie|show"}), 400
        db = get_video_db()
        cap = video_cap()
        if cap is not None:
            # kids profiles: over the cap (or unrated) is a hard no, as on the detail page
            ident = db.title_identity(kind, item_id) or {}
            if not title_allowed(ident.get("content_rating"), cap):
                return restricted_response()
        season = request.args.get("season")
        try:
            season = int(season) if season not in (None, "") else None
        except ValueError:
            return jsonify({"error": "season must be a number"}), 400
        try:
            data = (library_manage.movie_library(db, item_id) if kind == "movie"
                    else library_manage.show_library(db, item_id, season=season))
        except Exception:
            logger.exception("library tab failed for %s %s", kind, item_id)
            return jsonify({"error": "Couldn't read this title's files"}), 500
        if data is None:
            return jsonify({"error": "not found"}), 404
        data["can_manage"] = _is_admin()
        return jsonify(data if _is_admin() else _strip_paths(data))

    @bp.route("/files/<int:file_id>/rename-plan", methods=["GET"])
    def video_file_rename_plan(file_id):
        from . import get_video_db
        from core.video import file_ops
        if not _is_admin():
            return jsonify({"error": "Admin only."}), 403
        plan = file_ops.rename_plan(get_video_db(), file_id)
        if plan.get("error"):
            return jsonify({"success": False, "error": plan["error"]}), 409
        return jsonify({"success": True, **plan})

    @bp.route("/files/<int:file_id>/rename", methods=["POST"])
    def video_file_rename(file_id):
        from . import get_video_db
        from core.video import file_ops
        db = get_video_db()
        row = db.library_file_row(file_id) or {}
        try:
            res = file_ops.rename_to_template(db, file_id)
        except Exception:
            logger.exception("rename failed for file %s", file_id)
            return jsonify({"success": False, "error": "Rename failed — see app.log"}), 500
        if not res.get("success"):
            return jsonify(res), 409
        if res.get("action") == "renamed":
            _refresh_server("movie" if row.get("scope") == "movie" else "show")
        return jsonify(res)

    @bp.route("/files/<int:file_id>/delete", methods=["POST"])
    def video_file_delete(file_id):
        from . import get_video_db
        from core.video import file_ops
        db = get_video_db()
        row = db.library_file_row(file_id) or {}
        try:
            res = file_ops.delete_to_recycle(db, file_id)
        except Exception:
            logger.exception("delete failed for file %s", file_id)
            return jsonify({"success": False, "error": "Delete failed — see app.log"}), 500
        if not res.get("success"):
            return jsonify(res), 409
        logger.info("library tab deleted file %s (%s) — recycled=%s",
                    file_id, row.get("relative_path"), res.get("recycled"))
        _refresh_server("movie" if row.get("scope") == "movie" else "show")
        return jsonify(res)

    @bp.route("/detail/<kind>/<int:item_id>/identify/search", methods=["GET"])
    def video_identify_search(kind, item_id):
        from core.video.enrichment.engine import get_video_enrichment_engine
        if kind not in _KINDS:
            return jsonify({"error": "kind must be movie|show"}), 400
        query = (request.args.get("q") or "").strip()
        if not query:
            return jsonify({"results": []})
        w = get_video_enrichment_engine().workers.get("tmdb")
        client = getattr(w, "client", None)
        if not client or not hasattr(client, "search_candidates"):
            return jsonify({"error": "TMDB isn't configured"}), 503
        results = []
        try:
            # a bare TMDB id is a search too: paste it, get exactly that title
            if query.isdigit():
                d = get_video_enrichment_engine().tmdb_full_detail(kind, int(query)) or {}
                if d.get("title"):
                    results.append({"id": int(query), "title": d.get("title"),
                                    "year": int(str(d["year"])[:4]) if str(d.get("year") or "")[:4].isdigit() else None,
                                    "overview": d.get("overview") or "",
                                    "poster_url": d.get("poster_url")})
            results.extend(r for r in client.search_candidates(kind, query, limit=10)
                           if not results or r.get("id") != results[0]["id"])
        except Exception:
            logger.exception("identify search failed (%s: %r)", kind, query)
            return jsonify({"error": "Search failed — try again"}), 502
        return jsonify({"results": results})

    @bp.route("/detail/<kind>/<int:item_id>/identify", methods=["POST"])
    def video_identify(kind, item_id):
        from . import get_video_db
        from core.video import identity
        body = request.get_json(silent=True) or {}
        res = identity.identify(get_video_db(), kind, item_id, body.get("tmdb_id"))
        return jsonify(res), (200 if res.get("success") else 409)

    @bp.route("/detail/<kind>/<int:item_id>/identify/release", methods=["POST"])
    def video_identify_release(kind, item_id):
        from . import get_video_db
        from core.video import identity
        if kind not in _KINDS:
            return jsonify({"success": False, "error": "kind must be movie|show"}), 400
        res = identity.release(get_video_db(), kind, item_id)
        return jsonify(res), (200 if res.get("success") else 404)
