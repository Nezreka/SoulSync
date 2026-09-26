"""kids profiles on the video side: the route-level helpers.

the rules live in core/content_filter.py. this is the glue that looks up a
title's rating (library row first, the tmdb detail for anything we don't
own) and answers "may this profile see / play it".

every helper takes the cap up front and does nothing when it is None, so an
admin or unrestricted profile never pays for an extra query.
"""

from __future__ import annotations

from flask import jsonify

from core.content_filter import current_restrictions, strictest, title_allowed
from utils.logging_config import get_logger

logger = get_logger("video_api.kids")


def video_cap():
    """the current profile's rating cap, None when it has none."""
    return current_restrictions().get("cap")


def restricted_response():
    return jsonify({"success": False, "error": "restricted", "restricted": True}), 403


def _kind(k):
    k = str(k or "").lower()
    if k in ("movie", "movies", "m"):
        return "movie"
    if k in ("show", "shows", "tv", "t", "episode"):
        return "show"
    return None


def library_title_allowed(db, kind, library_id, cap) -> bool:
    if cap is None:
        return True
    k = _kind(kind)
    if not k:
        return False
    try:
        rating = db.content_ratings_by_id(k, [library_id]).get(int(library_id))
    except Exception:  # noqa: BLE001 - can't vouch, so no
        logger.exception("kids: rating lookup failed for %s %s", kind, library_id)
        return False
    return title_allowed(rating, cap)


def tmdb_title_rating(db, kind, tmdb_id):
    """the rating for a tmdb title: the library's (strictest across servers),
    else the certification on the tmdb detail. None when neither knows."""
    k = _kind(kind)
    if not k:
        return None
    try:
        rows = db.content_ratings_by_tmdb(k, [tmdb_id]).get(int(tmdb_id)) or []
    except Exception:  # noqa: BLE001
        logger.exception("kids: library rating lookup failed for %s %s", kind, tmdb_id)
        rows = []
    known = strictest(rows)
    if known is not None:
        return known
    try:
        from core.video.enrichment.engine import get_video_enrichment_engine
        d = get_video_enrichment_engine().tmdb_detail(k, int(tmdb_id)) or {}
    except Exception:  # noqa: BLE001 - no detail, no rating, blocked under a cap
        logger.debug("kids: tmdb detail lookup failed for %s %s", kind, tmdb_id, exc_info=True)
        return None
    return d.get("content_rating")


def tmdb_title_allowed(db, kind, tmdb_id, cap) -> bool:
    if cap is None:
        return True
    return title_allowed(tmdb_title_rating(db, kind, tmdb_id), cap)


def filter_tmdb_items(db, items, cap, kind_key="kind", id_key="tmdb_id"):
    """drop tmdb-keyed cards over the cap. library ratings only (one query per
    kind), no tmdb round trip per card: a card we can't rate is dropped.
    non-title cards (people, studios) pass."""
    if cap is None or not items:
        return items
    want: dict = {"movie": set(), "show": set()}
    for it in items:
        k = _kind(it.get(kind_key)) if isinstance(it, dict) else None
        if k and it.get(id_key) is not None:
            want[k].add(it.get(id_key))
    ratings = {}
    for k, ids in want.items():
        if ids:
            try:
                ratings[k] = db.content_ratings_by_tmdb(k, ids)
            except Exception:  # noqa: BLE001
                logger.exception("kids: rating lookup failed")
                ratings[k] = {}
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        raw_kind = str(it.get(kind_key) or "").lower()
        k = _kind(raw_kind)
        if k is None and raw_kind in ("person", "studio", "collection", "company", "channel"):
            out.append(it)
            continue
        if k is None:
            continue
        try:
            rows = ratings.get(k, {}).get(int(it.get(id_key))) or []
        except (TypeError, ValueError):
            rows = []
        if title_allowed(strictest(rows), cap):
            out.append(it)
    return out


def filter_library_items(db, items, cap, kind, id_key="id"):
    """drop library-keyed rows (one kind) over the cap, one query."""
    if cap is None or not items:
        return items
    k = _kind(kind)
    ids = [it.get(id_key) for it in items if isinstance(it, dict) and it.get(id_key) is not None]
    try:
        ratings = db.content_ratings_by_id(k, ids) if k else {}
    except Exception:  # noqa: BLE001
        logger.exception("kids: rating lookup failed")
        ratings = {}
    out = []
    for it in items:
        try:
            rid = int(it.get(id_key))
        except (TypeError, ValueError):
            continue
        if title_allowed(ratings.get(rid), cap):
            out.append(it)
    return out


def filter_mixed_library_items(db, items, cap, kind_key="kind", id_key="id", show_id_key=None):
    """drop rows that mix movies and shows by library id (dashboard rails).
    ``show_id_key`` reads a show row's show id from another key (continue
    watching puts the episode id in ``id``)."""
    if cap is None or not items:
        return items
    ids: dict = {"movie": [], "show": []}

    def _ref(it):
        k = _kind(it.get(kind_key))
        key = show_id_key if (k == "show" and show_id_key) else id_key
        return k, it.get(key)

    for it in items:
        k, rid = _ref(it)
        if k and rid is not None:
            ids[k].append(rid)
    ratings = {}
    for k, v in ids.items():
        try:
            ratings[k] = db.content_ratings_by_id(k, v) if v else {}
        except Exception:  # noqa: BLE001
            logger.exception("kids: rating lookup failed")
            ratings[k] = {}
    out = []
    for it in items:
        k, rid = _ref(it)
        if not k or rid is None:
            continue
        if title_allowed(ratings.get(k, {}).get(int(rid)), cap):
            out.append(it)
    return out
