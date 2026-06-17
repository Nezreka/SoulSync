"""Video poster proxy.

GET /api/video/poster/<kind>/<id> streams a movie/show poster from the media
server, server-side (so the Plex token / Jellyfin key never reaches the
browser). Falls back to 404 so the frontend shows its placeholder.
"""

from __future__ import annotations

from flask import Response, abort, request

from utils.logging_config import get_logger

logger = get_logger("video_api.poster")


def _req_width():
    """Optional ?w= thumbnail width (clamped) so the calendar/library don't pull
    full-size art into tiny cells. None = original."""
    try:
        w = request.args.get("w", type=int)
    except Exception:
        w = None
    if not w:
        return None
    return max(48, min(1600, w))


def _tmdb_resize(url, w, backdrop):
    """Rewrite a TMDB image URL's size segment (/t/p/<size>/...) to a bucket near
    ``w`` so episode stills load small. Best-effort — unchanged if it doesn't match."""
    import re
    buckets = [300, 780, 1280] if backdrop else [185, 342, 500, 780]
    pick = next((b for b in buckets if w <= b), buckets[-1])
    return re.sub(r"/t/p/[^/]+/", "/t/p/w%d/" % pick, url, count=1)


def register_routes(bp):
    def _stream_art(kind, item_id, art):
        from . import get_video_db
        ref = get_video_db().get_art_ref(kind, item_id, art)
        if not ref or not ref.get("poster_url"):
            abort(404)
        try:
            import requests
            w = _req_width()
            backdrop = art == "backdrop"
            path = ref["poster_url"]
            # Enrichment can store a full external URL (e.g. a TMDB season poster) —
            # stream it directly; otherwise it's a server path needing the token.
            if path.startswith("http://") or path.startswith("https://"):
                if w and "image.tmdb.org" in path:
                    path = _tmdb_resize(path, w, backdrop)
                upstream = requests.get(path, timeout=15, stream=True)
                if upstream.status_code != 200:
                    abort(404)
                resp = Response(upstream.iter_content(8192),
                                content_type=upstream.headers.get("Content-Type", "image/jpeg"))
                resp.headers["Cache-Control"] = "public, max-age=86400"
                return resp
            # Art is served from the VIDEO side's effective connection (its own
            # creds, or inherited from music) — that's where the item was scanned.
            from core.video.sources import video_plex_config, video_jellyfin_config
            source = ref.get("server_source")
            fallback = None
            if source == "plex":
                cfg = video_plex_config()
                base, token = cfg.get("base_url"), cfg.get("token")
                if not base or not token:
                    abort(404)
                base = base.rstrip("/")
                params = {"X-Plex-Token": token}
                if w:
                    # Plex photo transcoder → a right-sized JPEG instead of full art;
                    # fall back to the original if a server has transcoding disabled.
                    from urllib.parse import quote
                    h = int(w * (9 / 16 if backdrop else 3 / 2))
                    url = (base + "/photo/:/transcode?width=%d&height=%d&minSize=1&upscale=1&url=%s"
                           % (w, h, quote(ref["poster_url"], safe="")))
                    fallback = base + ref["poster_url"]
                else:
                    url = base + ref["poster_url"]
            elif source == "jellyfin":
                cfg = video_jellyfin_config()
                base, key = cfg.get("base_url"), cfg.get("api_key")
                if not base:
                    abort(404)
                image = "Backdrop" if backdrop else "Primary"
                url = base.rstrip("/") + f"/Items/{ref['server_id']}/Images/{image}"
                params = {"api_key": key} if key else {}
                if w:
                    params["maxWidth"] = w
                    params["quality"] = 90
            else:
                abort(404)

            upstream = requests.get(url, params=params, timeout=15, stream=True)
            if upstream.status_code != 200 and fallback:
                upstream = requests.get(fallback, params=params, timeout=15, stream=True)
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
        """Same-origin image proxy (SSRF-safe allowlist). TMDB for accent-sampling,
        and YouTube CDN (avatars/banners/thumbnails) so channel art loads reliably
        regardless of hotlink/CORS policy."""
        from flask import request
        from urllib.parse import urlparse
        url = request.args.get("u", "")
        if not url.startswith("https://"):
            abort(404)
        host = (urlparse(url).hostname or "").lower()
        # image.tmdb.org + any YouTube image host (yt3/yt4.ggpht, googleusercontent, *.ytimg)
        ok = host == "image.tmdb.org" or any(
            host == s or host.endswith("." + s) for s in ("ytimg.com", "ggpht.com", "googleusercontent.com"))
        if not ok:
            abort(404)
        try:
            import requests
            # A browser UA — Google's image CDN (yt3/googleusercontent) 403s some
            # avatars when fetched with no User-Agent, which blanked search results.
            upstream = requests.get(url, timeout=15, stream=True, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            })
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
