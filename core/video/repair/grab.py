"""One-movie grab for repair-job fixes (quality upgrade / broken file).

The wishlist drain deliberately refuses movies you already own (that exclusion
breaks re-download loops), so upgrade/replacement grabs can't ride it. Instead
a fix runs the drain's OWN seams directly for the single movie: blocking
Soulseek search → pick the top profile-accepted release → enqueue the download.
The download pipeline takes it from there (its import step already knows how
to judge an upgrade vs the existing file).

Synchronous by design: the approve toast reports the real outcome ("grabbed X"
or "nothing acceptable found — stays pending"), never a hopeful maybe.
"""

from __future__ import annotations

from utils.logging_config import get_logger

logger = get_logger("video.repair.grab")


def grab_movie(details: dict) -> dict:
    """Search + enqueue one movie. Returns the music fix-handler contract:
    {'success', 'action', 'message'|'error'}."""
    item = {"tmdb_id": details.get("tmdb_id"), "title": details.get("title"),
            "year": details.get("year"), "poster_url": details.get("poster_url")}
    if not item["tmdb_id"] or not item["title"]:
        return {"success": False, "error": "movie is not TMDB-matched yet"}
    try:
        from core.automation.handlers import video_process_wishlist as vpw
        candidates = vpw._default_search(item, "movie") or []
        best = vpw.pick_best(candidates)
        if not best:
            return {"success": False,
                    "error": "no release matched your quality profile — finding stays pending"}
        vpw._default_enqueue(item, best, candidates, "movie")
        name = best.get("quality") or best.get("resolution") or "release"
        return {"success": True, "action": "grabbed",
                "message": f"Grabbed a {name} of {item['title']} — watch the Downloads page"}
    except Exception as e:   # noqa: BLE001 - a failed grab is a result, not a crash
        logger.exception("repair grab failed for %s", item.get("title"))
        return {"success": False, "error": str(e)}
