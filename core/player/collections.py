"""the collections an admin built in collection studio, as the player shows
them: each one's owned members (library ids), so a profile sees a shelf of
what's on disk. the player intersects these with its own catalog, so a
kids profile never sees a member its cap hides.

a list collection may fetch a remote list (trakt, imdb), so the whole read
has a time budget; what doesn't fit waits for the next read.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from utils.logging_config import get_logger

logger = get_logger("player.collections")

BUDGET_SECONDS = 12.0


def read(db, budget_s: float = BUDGET_SECONDS) -> Dict[str, Any]:
    from core.video.collections.resolver import resolve_collection
    from core.video.collections.sync import _default_fetcher, in_season

    started = time.monotonic()
    fetcher = None
    out: List[Dict[str, Any]] = []
    partial = False
    for row in db.list_collection_definitions():
        if not row.get("enabled", 1):
            continue
        if row.get("window_start") and row.get("window_end") and not in_season(row):
            continue
        if time.monotonic() - started > budget_s:
            partial = True
            break
        full = db.get_collection_definition(row["id"])
        if not full or (full.get("definition") or {}).get("acquire_only"):
            continue
        if full.get("kind") == "list" and fetcher is None:
            fetcher = _default_fetcher(db, None)
        try:
            res = resolve_collection(db, full, list_fetcher=fetcher)
        except Exception:  # noqa: BLE001 - one bad collection never hides the rest
            logger.debug("collection %s resolve failed", row["id"], exc_info=True)
            continue
        if not res.ok or not res.owned:
            continue
        members = [m["id"] for m in res.owned if m.get("id") is not None]
        poster_kind = "movie" if res.media_type == "movie" else "show"
        member_posters = [f"/api/video/poster/{poster_kind}/{mid}?w=342" for mid in members[:4]]
        raw_poster = row.get("poster_url")
        if isinstance(raw_poster, str) and raw_poster.startswith("/api/video/collections/"):
            raw_poster = "/api/player/collections/" + raw_poster[len("/api/video/collections/"):]
        poster = raw_poster or (member_posters[0] if member_posters else None)
        out.append({
            "id": row["id"],
            "name": row.get("name") or "Untitled collection",
            "media_type": res.media_type,
            "summary": row.get("summary"),
            "poster": poster,
            "member_posters": member_posters,
            "pinned": bool(row.get("pinned")),
            "members": members,
            "missing": len(res.missing or []),
        })
    out.sort(key=lambda c: (not c["pinned"], -len(c["members"]), c["name"].lower()))
    return {"collections": out, "partial": partial}
