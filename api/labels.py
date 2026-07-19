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

from flask import Blueprint, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("labels.api")

# Host-injected callables (configure() below).
_db_getter: Optional[Callable] = None      # () -> MusicDatabase
_mb_getter: Optional[Callable] = None       # () -> MusicBrainzClient | None (or None -> label_catalog default)

# The label catalog is expensive (MB is rate-limited to ~1 req/s and we page
# up to 8 deep), so memoize per label for a while. Additive, in-process only.
_CATALOG_TTL_S = 1800.0
_catalog_cache: Dict[str, Dict[str, Any]] = {}   # mbid -> {"at": epoch, "items": [...]}


def configure(*, db_getter: Callable, mb_getter: Optional[Callable] = None) -> None:
    global _db_getter, _mb_getter
    _db_getter = db_getter
    _mb_getter = mb_getter


def _db():
    try:
        return _db_getter() if _db_getter else None
    except Exception:
        return None


def _now() -> float:
    import time as _time
    return _time.time()


def _group_by_artist(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group a newest-first catalog into per-artist buckets. Because ``items``
    arrive newest-first, first-appearance ordering puts the artist with the
    most recent release at the top."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for it in items or []:
        artist = str(it.get('artist') or '').strip() or 'Unknown Artist'
        if artist not in groups:
            groups[artist] = []
            order.append(artist)
        groups[artist].append(it)
    return [{'artist': a, 'releases': groups[a]} for a in order]


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


def create_blueprint() -> Blueprint:
    bp = Blueprint("labels_api", __name__)

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
        return jsonify({
            "label": {"id": mbid, "name": name},
            "is_watching": is_watching,
            "backlog": backlog,
            "release_count": len(items),
            "groups": _group_by_artist(items),
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
