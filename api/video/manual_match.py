"""Manual library matches — the "I have this" button.

The auto-matcher keys library rows by tmdb_id and sometimes whiffs: a show
sits in the library with a NULL or wrong tmdb_id while the TMDB detail page
insists you own nothing. These endpoints let the user point a TMDB id at the
right library row themselves.

Writes are admin-gated (the blueprint's permission gate treats
/api/video/manual-match writes like the other library mutations); the GETs
stay open like the other content-view reads.

GET  /api/video/manual-match/search?q=&kind=movie|show
    Library titles matching ``q`` (includes never-auto-matched rows — those
    are exactly what this is for). [{id, title, year, tmdb_id}]
GET  /api/video/manual-match/status?kind=&tmdb_id=
    {library_id} — the current manual link for a TMDB id, if any.
GET  /api/video/manual-match/status?kind=&library_id=
    {tmdb_id} — the TMDB id manually linked to a library row, if any.
POST /api/video/manual-match  {kind, tmdb_id, library_id}
    Record the link. 404 when the library row doesn't exist.
DELETE /api/video/manual-match  {kind, tmdb_id}
    Remove the link. 404 when there wasn't one.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.manual_match")

_KINDS = ("movie", "show")


def register_routes(bp):
    @bp.route("/manual-match/search", methods=["GET"])
    def video_manual_match_search():
        from . import get_video_db
        kind = (request.args.get("kind") or "").strip()
        q = (request.args.get("q") or "").strip()
        if kind not in _KINDS:
            return jsonify({"error": "kind must be movie|show"}), 400
        if not q:
            return jsonify({"results": [], "query": ""})
        try:
            results = get_video_db().search_library_titles(kind, q, limit=12)
        except Exception:
            logger.exception("manual-match search failed for %r", q)
            results = []
        return jsonify({"results": results, "query": q})

    @bp.route("/manual-match/status", methods=["GET"])
    def video_manual_match_status():
        from . import get_video_db
        kind = (request.args.get("kind") or "").strip()
        if kind not in _KINDS:
            return jsonify({"error": "kind must be movie|show"}), 400
        db = get_video_db()
        tmdb_id = request.args.get("tmdb_id")
        library_id = request.args.get("library_id")
        if tmdb_id is not None:
            return jsonify({"kind": kind, "tmdb_id": tmdb_id,
                            "library_id": db.manual_match_for_tmdb(kind, tmdb_id)})
        if library_id is not None:
            return jsonify({"kind": kind, "library_id": library_id,
                            "tmdb_id": db.manual_match_for_library(kind, library_id)})
        return jsonify({"error": "tmdb_id or library_id required"}), 400

    @bp.route("/manual-match", methods=["POST"])
    def video_manual_match_set():
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        kind = body.get("kind")
        if kind not in _KINDS:
            return jsonify({"success": False, "error": "kind must be movie|show"}), 400
        if not get_video_db().set_manual_match(kind, body.get("tmdb_id"),
                                                body.get("library_id")):
            return jsonify({"success": False,
                            "error": "Could not link — check the title is still in your library."}), 404
        return jsonify({"success": True, "kind": kind,
                        "tmdb_id": int(body["tmdb_id"]),
                        "library_id": int(body["library_id"])})

    @bp.route("/manual-match", methods=["DELETE"])
    def video_manual_match_clear():
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        kind = body.get("kind")
        if kind not in _KINDS:
            return jsonify({"success": False, "error": "kind must be movie|show"}), 400
        if not get_video_db().clear_manual_match(kind, body.get("tmdb_id")):
            return jsonify({"success": False, "error": "No manual link to remove."}), 404
        return jsonify({"success": True})
