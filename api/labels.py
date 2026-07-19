"""Record-label watchlist API — search labels, browse a label's catalog, and
follow/unfollow a label so its new releases get wishlisted.

Purely additive and self-contained (same standalone-blueprint pattern as
api/chat.py): absolute /api/labels/* paths, no url_prefix, host deps injected
via configure() to dodge circular imports with web_server. It reads only the
new watchlist_labels table + the keyless MusicBrainz catalog layer
(core/metadata/label_catalog) — it touches no existing route or table.

A label is monitored like the video-side studio watchlist and displayed like
an artist's discography: its catalog is a list of releases that each resolve
to a REAL artist (never the label), grouped by artist for display.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from flask import Blueprint, jsonify, redirect, request

from utils.logging_config import get_logger

logger = get_logger("labels.api")

# Host-injected callables (configure() below).
_db_getter: Optional[Callable] = None      # () -> MusicDatabase
_mb_getter: Optional[Callable] = None       # () -> MusicBrainzClient | None (or None -> label_catalog default)
_itunes_getter: Optional[Callable] = None   # () -> iTunesClient | None (cover art fallback)
_deezer_getter: Optional[Callable] = None   # () -> DeezerClient | None (cover art, faster rate limit)

# The label catalog is expensive (MB is rate-limited to ~1 req/s and we page
# up to 8 deep), so memoize per label for a while. Additive, in-process only.
_CATALOG_TTL_S = 1800.0
_catalog_cache: Dict[str, Dict[str, Any]] = {}   # mbid -> {"at": epoch, "items": [...]}

# Cover art resolved by (artist, album) → an Apple-CDN URL (or '' for a miss).
_COVER_TTL_S = 86400.0
_cover_cache: Dict[tuple, Dict[str, Any]] = {}

# Cover Art Archive is the PREFERRED source — we have the exact release mbid, so
# it's a direct lookup (no fuzzy match). But it's frequently slow/unreachable,
# so a circuit breaker trips after a few failures and we skip it (fast fallback
# to Deezer/iTunes) for a cooldown, then retry — so a healthy CAA is used and a
# downed one doesn't cost every request an 11s timeout.
_CAA_TIMEOUT_S = 2.0
_CAA_FAIL_THRESHOLD = 2
_CAA_COOLDOWN_S = 600.0
_caa_breaker: Dict[str, Any] = {"fails": 0, "skip_until": 0.0}


def configure(*, db_getter: Callable, mb_getter: Optional[Callable] = None,
              itunes_getter: Optional[Callable] = None,
              deezer_getter: Optional[Callable] = None) -> None:
    global _db_getter, _mb_getter, _itunes_getter, _deezer_getter
    _db_getter = db_getter
    _mb_getter = mb_getter
    _itunes_getter = itunes_getter
    _deezer_getter = deezer_getter


def _db():
    try:
        return _db_getter() if _db_getter else None
    except Exception:
        return None


def _now() -> float:
    import time as _time
    return _time.time()


def _fetch_catalog(mbid: str) -> List[Dict[str, Any]]:
    """Label catalog with a TTL memo so repeat page loads don't re-walk MB."""
    now = _now()
    hit = _catalog_cache.get(mbid)
    if hit and (now - hit['at']) < _CATALOG_TTL_S:
        return hit['items']
    from core.metadata import label_catalog as lc
    items = lc.label_catalog(mbid, mb_getter=_mb_getter)
    _catalog_cache[mbid] = {'at': now, 'items': items}
    return items


def _norm(s: Any) -> str:
    import re as _re
    return _re.sub(r'[^a-z0-9]+', ' ', str(s or '').lower()).strip()


def _try_caa(release_id: str) -> str:
    """A same-origin proxied URL for the release's Cover Art Archive front cover
    — EXACT, by the release mbid we already have (no fuzzy match). '' when CAA
    has no art for it or is unreachable. Circuit-broken: after repeated failures
    we skip CAA for a cooldown so a downed CAA doesn't time out every request."""
    rid = str(release_id or '').strip()
    if not rid or _now() < _caa_breaker["skip_until"]:
        return ''
    caa = f"https://coverartarchive.org/release/{rid}/front-500"
    try:
        import requests
        # allow_redirects=False: CAA returns a 3xx to the actual image when art
        # exists, 404 when not — so we learn "has art" WITHOUT waiting on the
        # (often unreachable) archive.org redirect target. A downed CAA just
        # times out fast (2s) → breaker.
        r = requests.head(caa, timeout=_CAA_TIMEOUT_S, allow_redirects=False)
        ok = r.status_code in (200, 301, 302, 303, 307, 308)
    except Exception:
        ok = False
    if ok:
        _caa_breaker["fails"] = 0
        from urllib.parse import quote
        # Served via the app's image proxy so the BROWSER never has to reach
        # coverartarchive.org directly (it often can't either).
        return f"/api/image-proxy?url={quote(caa, safe='')}"
    _caa_breaker["fails"] += 1
    if _caa_breaker["fails"] >= _CAA_FAIL_THRESHOLD:
        _caa_breaker["skip_until"] = _now() + _CAA_COOLDOWN_S
    return ''


def _resolve_cover(artist: str, album: str) -> str:
    """Fallback cover URL for (artist, album) when CAA has nothing. Tries Deezer
    first (1s rate limit + reachable CDN) then iTunes (3s). '' on a miss. Only
    accepts a result whose album name reasonably matches, so a card never shows
    the WRONG cover."""
    want = _norm(album)
    for getter in (_deezer_getter, _itunes_getter):
        try:
            client = getter() if getter else None
        except Exception:
            client = None
        if client is None:
            continue
        try:
            results = client.search_albums(f"{artist} {album}", limit=5) or []
        except Exception:
            logger.debug("labels cover: album search failed for %s - %s", artist, album)
            continue
        for a in results:
            name = _norm(getattr(a, 'name', ''))
            if name and (name == want or want in name or name in want):
                img = str(getattr(a, 'image_url', '') or '')
                if img:
                    return img.replace('3000x3000bb', '500x500bb')
    return ''


def create_blueprint() -> Blueprint:
    bp = Blueprint("labels_api", __name__)

    @bp.route("/api/labels/cover", methods=["GET"])
    def labels_cover():
        """A cover for a release. Cover Art Archive FIRST (exact, by release
        mbid, proxied same-origin), then Deezer/iTunes fuzzy-by-name as the
        fallback. The label grid + download modal both point here."""
        release_id = str(request.args.get("release_id") or "").strip()
        artist = str(request.args.get("artist") or "").strip()
        album = str(request.args.get("album") or "").strip()

        # 1) Cover Art Archive — preferred, exact lookup by the id we already have.
        caa = _try_caa(release_id)
        if caa:
            resp = redirect(caa, code=302)
            resp.headers["Cache-Control"] = "private, max-age=86400"
            return resp

        # 2) Fallback — Deezer/iTunes by name (cached), redirect to their CDN.
        if not artist or not album:
            return "", 404
        key = (artist.lower(), album.lower())
        now = _now()
        hit = _cover_cache.get(key)
        if hit and (now - hit["at"]) < _COVER_TTL_S:
            url = hit["url"]
        else:
            url = _resolve_cover(artist, album)
            _cover_cache[key] = {"at": now, "url": url}
        if not url:
            return "", 404
        resp = redirect(url, code=302)
        resp.headers["Cache-Control"] = "private, max-age=86400"
        return resp

    @bp.route("/api/labels/cover-url", methods=["GET"])
    def labels_cover_url():
        """The resolved ABSOLUTE cover URL (Deezer/iTunes CDN) for (artist,
        album) as JSON. Used by the download modal + wishlist entry, which need
        a directly browser-loadable url: the redirect/proxy cover endpoints are
        RELATIVE, and the wishlist image normaliser rewrites any relative url
        (treats '/...' as an internal media path) → breaks it. No CAA here (it
        needs same-origin proxying, which is relative)."""
        artist = str(request.args.get("artist") or "").strip()
        album = str(request.args.get("album") or "").strip()
        if not artist or not album:
            return jsonify({"url": ""})
        key = (artist.lower(), album.lower())
        now = _now()
        hit = _cover_cache.get(key)
        if hit and (now - hit["at"]) < _COVER_TTL_S:
            return jsonify({"url": hit["url"]})
        url = _resolve_cover(artist, album)
        _cover_cache[key] = {"at": now, "url": url}
        return jsonify({"url": url})

    @bp.route("/api/labels/search", methods=["POST"])
    def labels_search():
        """Label search results for the search page's Labels section."""
        body = request.get_json(silent=True) or {}
        query = str(body.get("query") or body.get("q") or "").strip()
        if not query:
            return jsonify({"labels": []})
        try:
            from core.metadata import label_catalog as lc
            labels = lc.search_labels(query, mb_getter=_mb_getter, limit=10)
        except Exception:
            logger.exception("labels_search failed for %r", query)
            return jsonify({"labels": [], "error": "search failed"}), 200
        db = _db()
        if db is not None:
            for lab in labels:
                try:
                    lab["is_watching"] = bool(db.is_label_in_watchlist(lab.get("id")))
                except Exception:
                    lab["is_watching"] = False
        return jsonify({"labels": labels})

    @bp.route("/api/labels/<path:label_mbid>/catalog", methods=["GET"])
    def labels_catalog(label_mbid):
        """A label's distinct albums, grouped by their real artist (newest
        first). ``?name=`` supplies the display name (the browse call doesn't
        return it); falls back to a watchlist row when followed."""
        mbid = str(label_mbid or "").strip()
        if not mbid:
            return jsonify({"error": "label id required"}), 400
        try:
            items = _fetch_catalog(mbid)
        except Exception:
            logger.exception("labels_catalog failed for %s", mbid)
            return jsonify({"error": "catalog failed"}), 200

        # Newest-first flat grid, paginated so the page paints fast instead of
        # dumping a whole label (hundreds of releases) in one shot.
        try:
            page = max(1, int(request.args.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = min(120, max(1, int(request.args.get("page_size", 60))))
        except (TypeError, ValueError):
            page_size = 60
        total = len(items)
        start = (page - 1) * page_size
        releases = items[start:start + page_size]

        db = _db()
        is_watching = False
        backlog = False
        name = str(request.args.get("name") or "").strip()
        if db is not None:
            try:
                is_watching = bool(db.is_label_in_watchlist(mbid))
                if is_watching:
                    for row in (db.get_watchlist_labels() or []):
                        if str(row.get("musicbrainz_label_id")) == mbid:
                            backlog = bool(row.get("backlog"))
                            name = name or str(row.get("label_name") or "")
                            break
            except Exception as exc:
                logger.debug("labels_catalog watch-state lookup failed: %s", exc)

        artist_count = len({str(it.get("artist") or "") for it in items if it.get("artist")})
        return jsonify({
            "label": {"id": mbid, "name": name},
            "is_watching": is_watching,
            "backlog": backlog,
            "total": total,
            "release_count": total,      # back-compat alias
            "artist_count": artist_count,
            "page": page,
            "page_size": page_size,
            "has_more": (start + page_size) < total,
            "releases": releases,
        })

    @bp.route("/api/labels/watchlist", methods=["GET"])
    def labels_watchlist_list():
        db = _db()
        if db is None:
            return jsonify({"labels": []})
        try:
            return jsonify({"labels": db.get_watchlist_labels() or []})
        except Exception:
            logger.exception("labels_watchlist_list failed")
            return jsonify({"labels": []})

    @bp.route("/api/labels/watchlist/check", methods=["POST"])
    def labels_watchlist_check():
        body = request.get_json(silent=True) or {}
        mbid = str(body.get("musicbrainz_label_id") or body.get("id") or "").strip()
        db = _db()
        if db is None or not mbid:
            return jsonify({"success": True, "is_watching": False, "backlog": False})
        try:
            watching = bool(db.is_label_in_watchlist(mbid))
            backlog = False
            if watching:
                for row in (db.get_watchlist_labels() or []):
                    if str(row.get("musicbrainz_label_id")) == mbid:
                        backlog = bool(row.get("backlog"))
                        break
            return jsonify({"success": True, "is_watching": watching, "backlog": backlog})
        except Exception:
            logger.exception("labels_watchlist_check failed")
            return jsonify({"success": False, "is_watching": False, "backlog": False})

    @bp.route("/api/labels/watchlist/add", methods=["POST"])
    def labels_watchlist_add():
        body = request.get_json(silent=True) or {}
        mbid = str(body.get("musicbrainz_label_id") or body.get("id") or "").strip()
        name = str(body.get("label_name") or body.get("name") or "").strip()
        backlog = bool(body.get("backlog", False))
        if not mbid or not name:
            return jsonify({"success": False, "error": "label id and name required"}), 400
        db = _db()
        if db is None:
            return jsonify({"success": False, "error": "database unavailable"}), 500
        try:
            ok = db.add_watchlist_label(
                mbid, name,
                discogs_id=str(body.get("discogs_label_id") or "") or None,
                source=str(body.get("source") or "musicbrainz"),
                backlog=backlog,
            )
            return jsonify({"success": bool(ok), "is_watching": True})
        except Exception:
            logger.exception("labels_watchlist_add failed for %s", mbid)
            return jsonify({"success": False, "error": "add failed"}), 500

    @bp.route("/api/labels/watchlist/remove", methods=["POST"])
    def labels_watchlist_remove():
        body = request.get_json(silent=True) or {}
        mbid = str(body.get("musicbrainz_label_id") or body.get("id") or "").strip()
        if not mbid:
            return jsonify({"success": False, "error": "label id required"}), 400
        db = _db()
        if db is None:
            return jsonify({"success": False, "error": "database unavailable"}), 500
        try:
            ok = db.remove_watchlist_label(mbid)
            return jsonify({"success": bool(ok), "is_watching": False})
        except Exception:
            logger.exception("labels_watchlist_remove failed for %s", mbid)
            return jsonify({"success": False, "error": "remove failed"}), 500

    @bp.route("/api/labels/watchlist/backlog", methods=["POST"])
    def labels_watchlist_backlog():
        body = request.get_json(silent=True) or {}
        mbid = str(body.get("musicbrainz_label_id") or body.get("id") or "").strip()
        backlog = bool(body.get("backlog", False))
        if not mbid:
            return jsonify({"success": False, "error": "label id required"}), 400
        db = _db()
        if db is None:
            return jsonify({"success": False, "error": "database unavailable"}), 500
        try:
            ok = db.set_watchlist_label_backlog(mbid, backlog)
            return jsonify({"success": bool(ok), "backlog": backlog})
        except Exception:
            logger.exception("labels_watchlist_backlog failed for %s", mbid)
            return jsonify({"success": False, "error": "backlog toggle failed"}), 500

    return bp
