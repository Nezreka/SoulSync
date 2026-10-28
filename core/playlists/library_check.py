"""does the library already have a mirror's tracks? (#1910)

a mirror's track statuses (#1721) read an ownership answer the sync matcher
stamps onto each track. only a sync stamps it, so a playlist pushed some other
way, or never synced, read "not synced yet" on every track even when the
server playlist held all of them.

this asks the same question the download modal's analysis asks (a saved
manual match, then the library) and stamps the same three fields the sync
does. read-only toward everything else: nothing is queued, wishlisted or
pushed, and the next sync overwrites the stamp with its own verdict.

a "missing" this check wrote is asked again once it's RECHECK_MISSING_AFTER
old: a song downloaded since shows up as owned without a sync, and a lookup
that failed quietly (the library search logs and answers "not found") can't
leave a wrong "not downloaded" behind for good. a sync's own stamp is the
sync's to refresh.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from core.discovery.wing_it import is_stub_id
from core.playlists.track_status import parse_extra
from utils.logging_config import get_logger

logger = get_logger("playlists.library_check")

# one call checks for at most this long (a library lookup is a few hundred
# ms on a big library, and requests time out at 120s). the caller asks again
# while some remain.
TIME_BUDGET_SECONDS = 15.0
# and never more than this many in one call
MAX_PER_CALL = 500
# a "missing" this check wrote is checked again after this long
RECHECK_MISSING_AFTER = 6 * 3600

# marks a stamp this check wrote, so it's the only kind it re-asks
_VIA = "library_checked_via"


def _query_track(row: Dict[str, Any], extra: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """the track to look for: the identified match when there is a real one,
    else the source's own title and artist."""
    md = extra.get("matched_data")
    if isinstance(md, dict) and md.get("name") and not is_stub_id(str(md.get("id") or "")):
        artists = md.get("artists") or []
        if isinstance(artists, str):
            artists = [artists]
        if artists:
            return {
                "name": md.get("name"),
                "artists": artists,
                "id": md.get("id"),
                "source_track_id": row.get("source_track_id"),
            }
    name = (row.get("track_name") or "").strip()
    artist = (row.get("artist_name") or "").strip()
    if not name or not artist:
        return None
    return {
        "name": name,
        "artists": [artist],
        "id": row.get("source_track_id"),
        "source_track_id": row.get("source_track_id"),
    }


def _due(extra: Dict[str, Any], now: float) -> bool:
    if extra.get("in_library") is True:
        return False
    checked_at = extra.get("library_checked_at")
    if not checked_at:
        return True
    if extra.get(_VIA) != "check":
        return False
    try:
        return now - float(checked_at) >= RECHECK_MISSING_AFTER
    except (TypeError, ValueError):
        return True


def unchecked_rows(tracks: List[Dict[str, Any]], now: Optional[float] = None) -> List[Dict[str, Any]]:
    """the rows nothing has looked at yet, plus this check's own stale misses."""
    now = time.time() if now is None else now
    return [row for row in tracks if _due(parse_extra(row.get("extra_data")), now)]


def check_mirror_tracks(db: Any, tracks: List[Dict[str, Any]], *, profile_id: int,
                        source: Optional[str], active_server: Optional[str],
                        limit: int = MAX_PER_CALL,
                        time_budget: float = TIME_BUDGET_SECONDS) -> Dict[str, int]:
    """stamp in_library / library_track_id / library_checked_at on the rows
    that are due, up to ``limit`` of them or ``time_budget`` seconds,
    whichever comes first. returns {checked, found, remaining}."""
    from core.downloads.master import live_manual_match, match_track_in_library

    pending = unchecked_rows(tracks)
    batch = pending[:max(0, int(limit))]
    checked_at = int(time.time())
    deadline = time.monotonic() + max(0.0, float(time_budget))
    checked = found_count = attempted = 0
    for row in batch:
        # at least one per call, so a slow library still makes progress
        if attempted and time.monotonic() >= deadline:
            break
        attempted += 1
        extra = parse_extra(row.get("extra_data"))
        query = _query_track(row, extra)
        found, lib_id = False, None
        if query is not None:
            try:
                manual = live_manual_match(db, profile_id, query, source)
                if manual:
                    found, lib_id = True, manual.get("library_track_id")
                else:
                    hit, _conf, db_track = match_track_in_library(
                        db, query, album_tracks_map={}, album_context=None,
                        expected_year=None, album_only=False, active_server=active_server)
                    if hit:
                        found = True
                        lib_id = getattr(db_track, "id", None) if db_track is not None else None
            except Exception as e:  # noqa: BLE001 - one bad row must not stop the rest
                logger.debug("library check failed for mirror track %s: %s", row.get("id"), e)
                continue
        if db.update_mirrored_track_extra_data(row["id"], {
            "in_library": found,
            "library_track_id": lib_id if found else None,
            "library_checked_at": checked_at,
            _VIA: "check",
        }):
            checked += 1
            found_count += int(found)
    return {"checked": checked, "found": found_count,
            "remaining": max(0, len(pending) - attempted)}


__all__ = ["MAX_PER_CALL", "check_mirror_tracks", "unchecked_rows"]
