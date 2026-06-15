"""Video poster proxy.

GET /api/video/poster/<kind>/<id> streams a movie/show poster from the media
server, server-side (so the Plex token / Jellyfin key never reaches the
browser). Falls back to 404 so the frontend shows its placeholder.
"""

from __future__ import annotations

from flask import Response, abort

from utils.logging_config import get_logger

logger = get_logger("video_api.poster")


def register_routes(bp):
    def _stream_art(kind, item_id, art):
        from . import get_video_db
        ref = get_video_db().get_art_ref(kind, item_id, art)
        if not ref or not ref.get("poster_url"):
            abort(404)
        try:
            import requests
            from config.settings import config_manager
            path = ref["poster_url"]
            # Enrichment can store a full external URL (e.g. a TMDB season poster) —
            # stream it directly; otherwise it's a server path needing the token.
            if path.startswith("http://") or path.startswith("https://"):
                upstream = requests.get(path, timeout=15, stream=True)
                if upstream.status_code != 200:
                    abort(404)
                resp = Response(upstream.iter_content(8192),
                                content_type=upstream.headers.get("Content-Type", "image/jpeg"))
                resp.headers["Cache-Control"] = "public, max-age=86400"
                return resp
            source = ref.get("server_source")
            if source == "plex":
                cfg = config_manager.get_plex_config() or {}
                base, token = cfg.get("base_url"), cfg.get("token")
                if not base or not token:
                    abort(404)
                url = base.rstrip("/") + ref["poster_url"]
                params = {"X-Plex-Token": token}
            elif source == "jellyfin":
                cfg = config_manager.get_jellyfin_config() or {}
                base, key = cfg.get("base_url"), cfg.get("api_key")
                if not base:
                    abort(404)
                image = "Backdrop" if art == "backdrop" else "Primary"
                url = base.rstrip("/") + f"/Items/{ref['server_id']}/Images/{image}"
                params = {"api_key": key} if key else {}
            else:
                abort(404)

            upstream = requests.get(url, params=params, timeout=15, stream=True)
            if upstream.status_code != 200:
                abort(404)
            ctype = upstream.headers.get("Content-Type", "image/jpeg")
            resp = Response(upstream.iter_content(8192), content_type=ctype)
            resp.headers["Cache-Control"] = "public, max-age=86400"
            return resp
        except Exception:
            logger.exception("video %s proxy failed for %s/%s", art, kind, item_id)
            abort(404)

    @bp.route("/poster/<kind>/<int:item_id>", methods=["GET"])
    def video_poster(kind, item_id):
        return _stream_art(kind, item_id, "poster")

    @bp.route("/backdrop/<kind>/<int:item_id>", methods=["GET"])
    def video_backdrop(kind, item_id):
        return _stream_art(kind, item_id, "backdrop")

    @bp.route("/img", methods=["GET"])
    def video_img_proxy():
        """Same-origin proxy for TMDB images (image.tmdb.org ONLY — SSRF-safe).
        Lets the detail/person pages canvas-sample a poster/portrait for the
        per-title accent colour, which a direct cross-origin image taints."""
        from flask import request
        url = request.args.get("u", "")
        if not url.startswith("https://image.tmdb.org/"):
            abort(404)
        try:
            import requests
            upstream = requests.get(url, timeout=15, stream=True)
            if upstream.status_code != 200:
                abort(404)
            resp = Response(upstream.iter_content(8192),
                            content_type=upstream.headers.get("Content-Type", "image/jpeg"))
            resp.headers["Cache-Control"] = "public, max-age=604800"
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp
        except Exception:
            logger.exception("video image proxy failed for %s", url)
            abort(404)
