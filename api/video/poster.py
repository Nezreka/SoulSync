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
    @bp.route("/poster/<kind>/<int:item_id>", methods=["GET"])
    def video_poster(kind, item_id):
        from . import get_video_db
        ref = get_video_db().get_poster_ref(kind, item_id)
        if not ref or not ref.get("poster_url"):
            abort(404)
        try:
            import requests
            from config.settings import config_manager
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
                url = base.rstrip("/") + f"/Items/{ref['server_id']}/Images/Primary"
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
            logger.exception("video poster proxy failed for %s/%s", kind, item_id)
            abort(404)
