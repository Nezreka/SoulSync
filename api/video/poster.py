"""Video poster proxy.

GET /api/video/poster/<kind>/<id> streams a movie/show poster from the media
server, server-side (so the Plex token / Jellyfin key never reaches the
browser). Falls back to 404 so the frontend shows its placeholder.
"""

from __future__ import annotations

from flask import Response, abort, request
from werkzeug.exceptions import HTTPException

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
                # An expected miss (e.g. a video with no maxresdefault.jpg — the UI
                # falls back) — a quiet 404, never an ERROR traceback in the log.
                logger.debug("img proxy upstream %s for %s", upstream.status_code, url)
                abort(404)
            resp = Response(upstream.iter_content(8192),
                            content_type=upstream.headers.get("Content-Type", "image/jpeg"))
            resp.headers["Cache-Control"] = "public, max-age=604800"
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp
        except HTTPException:
            raise   # our own abort(404) above — already handled, don't re-log it
        except Exception:
            logger.exception("video image proxy failed for %s", url)
            abort(404)

    @bp.route("/poster/options/<kind>/<int:tmdb_id>", methods=["GET"])
    def video_poster_options(kind, tmdb_id):
        """Candidate posters for a title (the poster manager grid). Keyed by TMDB id
        so it works for a library item or a fresh search hit alike."""
        from flask import jsonify
        if kind not in ("movie", "show"):
            return jsonify({"posters": []}), 400
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            posters = get_video_enrichment_engine().poster_options(kind, tmdb_id) or []
        except Exception:
            logger.exception("poster options failed for %s %s", kind, tmdb_id)
            posters = []
        return jsonify({"posters": posters})

    @bp.route("/poster/set", methods=["POST"])
    def video_set_poster():
        """Change a movie/show poster: write the chosen image into the item's folder
        as poster.jpg (the server picks it up on its own scan), point the local DB at
        it so it shows immediately, and best-effort push it straight to the media
        server + nudge a rescan.

        Body: {kind: 'movie'|'show', id: <library id>, poster_url: <http(s) image>}.
        Everything after the image fetch is best-effort — a poster still changes even
        if the folder is a container path we can't reach or the server upload API fails;
        the next scan reconciles whatever we couldn't do here."""
        from flask import jsonify, request
        from . import get_video_db
        from core.video.sources import refresh_video_server_sections, set_video_poster

        data = request.get_json(silent=True) or {}
        kind = str(data.get("kind") or "").lower()
        item_id = data.get("id")
        poster_url = str(data.get("poster_url") or "").strip()

        if kind not in ("movie", "show"):
            return jsonify({"ok": False, "error": "kind must be 'movie' or 'show'"}), 400
        if not (poster_url.startswith("http://") or poster_url.startswith("https://")):
            return jsonify({"ok": False, "error": "poster_url must be an http(s) image URL"}), 400
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "id must be an integer"}), 400

        db = get_video_db()
        target = db.poster_set_target(kind, item_id)
        if not target:
            return jsonify({"ok": False, "error": "Item not found"}), 404

        # Fetch the chosen poster once, server-side (so the browser never has to).
        # This is the only genuine failure — everything after is best-effort.
        try:
            import requests
            r = requests.get(poster_url, timeout=20)
            r.raise_for_status()
            img_bytes = r.content
        except Exception as e:
            logger.exception("set_poster: failed to fetch %s", poster_url)
            return jsonify({"ok": False, "error": "Couldn't fetch that image: %s" % e}), 502

        # (1) Best-effort — drop poster.jpg into the item's folder; the server picks
        # this up on its own scan (this is the primary path).
        wrote_folder = False
        path = target.get("path")
        if path:
            try:
                import os
                if os.path.isdir(path):
                    with open(os.path.join(path, "poster.jpg"), "wb") as f:
                        f.write(img_bytes)
                    wrote_folder = True
            except Exception:
                logger.warning("set_poster: folder write skipped for %s", path, exc_info=True)

        # (2) Best-effort — show it now; the next scan reconciles with the server.
        db.set_item_poster_url(kind, item_id, poster_url)

        # (3) Best-effort — push straight to the server so it changes without waiting
        # for a scan (nice-to-have; the server will get it from poster.jpg regardless).
        pushed_server = False
        if target.get("server_id"):
            try:
                pushed_server = bool(set_video_poster(
                    target["server_id"], image_bytes=img_bytes, kind=kind).get("ok"))
            except Exception:
                logger.warning("set_poster: server upload skipped", exc_info=True)

        # (4) Best-effort — nudge the server to re-read the section.
        try:
            refresh_video_server_sections(kind)
        except Exception:
            logger.warning("set_poster: rescan nudge failed", exc_info=True)

        return jsonify({"ok": True, "wrote_folder": wrote_folder, "pushed_server": pushed_server})
