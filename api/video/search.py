"""Video search API (in-app, isolated).

  GET /api/video/search?q=...   → TMDB multi-search (movies / shows / people),
                                  movie/show results annotated with library_id
                                  if already owned.

Everything resolves back into SoulSync — results link to the library detail
(owned) or the TMDB-backed detail (not owned); people open the in-app person
page. Reads only the video engine + video.db.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.search")


def register_routes(bp):
    @bp.route("/search", methods=["GET"])
    def video_search():
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"results": [], "query": ""})
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            results = get_video_enrichment_engine().search(q)
        except Exception:
            logger.exception("video search failed for %r", q)
            results = []
        return jsonify({"results": results, "query": q})

    @bp.route("/trending", methods=["GET"])
    def video_trending():
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            results = get_video_enrichment_engine().trending()
        except Exception:
            logger.exception("video trending failed")
            results = []
        return jsonify({"results": results})
