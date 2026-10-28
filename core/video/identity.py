"""Fix match: tell SoulSync which title a library movie or show really is.

The media server reports an identity for every item (its tmdb/imdb/tvdb
guids) and the scan writes it onto the library row. When the server matched
the file to the wrong title, every SoulSync surface inherits that: the wrong
poster, the wrong cast, the wrong ratings, upgrades searched for the wrong
release, renames to the wrong name.

``identify`` re-points the row to the TMDB title the user picked and makes it
stick: the tmdb id, and the imdb/tvdb ids TMDB gives for it, are written and
locked, so neither a scan nor an enrichment worker can put the old ones back.
When TMDB names the title differently (the classic case: Dune 1984 matched as
Dune 2021) the title and year follow too, as a user edit. Everything derived
from the old match is reset by ``rematch_item`` and re-enriches by the new id.

The media server itself is not touched: its own match is the server's to fix.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("video.identity")


def _default_lookup(kind: str, tmdb_id: int):
    from core.video.enrichment.engine import get_video_enrichment_engine
    return get_video_enrichment_engine().tmdb_full_detail(kind, tmdb_id)


def _year(v: Any) -> Optional[int]:
    try:
        return int(str(v)[:4]) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def identify(db, kind: str, item_id: int, tmdb_id: Any, *,
             lookup: Optional[Callable] = None) -> Dict[str, Any]:
    """re-point one library movie/show to ``tmdb_id`` and lock it there.

    returns {success, tmdb_id, title, year, renamed} or {success: False, error}.
    the TMDB lookup happens first, so a typo'd or dead id changes nothing."""
    if kind not in ("movie", "show"):
        return {"success": False, "error": "kind must be movie or show"}
    try:
        tmdb_id = int(tmdb_id)
        item_id = int(item_id)
    except (TypeError, ValueError):
        return {"success": False, "error": "tmdb_id must be a number"}
    if tmdb_id <= 0:
        return {"success": False, "error": "tmdb_id must be a number"}
    current = db.title_identity(kind, item_id)
    if not current:
        return {"success": False, "error": "That title isn't in your library."}
    try:
        detail = (lookup or _default_lookup)(kind, tmdb_id)
    except Exception:   # noqa: BLE001 - a TMDB failure must not half-apply a match
        logger.exception("fix-match lookup failed for %s %s", kind, tmdb_id)
        detail = None
    if not detail or not detail.get("title"):
        return {"success": False,
                "error": "TMDB didn't answer for that id — nothing was changed."}

    if not db.rematch_item(kind, item_id, "tmdb", tmdb_id):
        return {"success": False, "error": "Couldn't update the match."}
    ids = {"imdb_id": detail.get("imdb_id")}
    if kind == "show":
        ids["tvdb_id"] = detail.get("tvdb_id")
    db.set_identity_ids(kind, item_id, **ids)

    # the server's name for a mismatched file is the wrong title's name. adopt
    # TMDB's when they differ, as a user edit (locked), so naming, search and
    # every list read the right title.
    changes = {}
    new_title, new_year = detail.get("title"), _year(detail.get("year"))
    if new_title and str(new_title) != str(current.get("title") or ""):
        changes["title"] = new_title
    if new_year and new_year != _year(current.get("year")):
        changes["year"] = new_year
    renamed = False
    if changes:
        try:
            renamed = bool(db.update_item_fields(kind, item_id, changes))
        except ValueError:
            logger.warning("fix-match could not adopt %s for %s %s", changes, kind, item_id)
    db.clear_manual_matches_for_library(kind, item_id, keep_tmdb=tmdb_id)
    logger.info("fix match: %s %s -> tmdb %s (%s %s)", kind, item_id, tmdb_id,
                new_title, new_year or "")
    return {"success": True, "kind": kind, "id": item_id, "tmdb_id": tmdb_id,
            "imdb_id": ids.get("imdb_id"), "tvdb_id": ids.get("tvdb_id"),
            "title": new_title, "year": new_year, "renamed": renamed}


def release(db, kind: str, item_id: int) -> Dict[str, Any]:
    """undo the lock: the next scan takes the server's ids again."""
    if not db.release_identity(kind, int(item_id)):
        return {"success": False, "error": "That title isn't in your library."}
    return {"success": True}


__all__ = ["identify", "release"]
