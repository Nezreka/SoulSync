"""Automatic quality upgrades: when to ask again, and what is worth trying.

A file below its quality profile is an upgrade candidate for as long as it
stays that way. Asked for with no memory, that is a loop (#2060): a source
that can only deliver 128 kbps under a 320 kbps profile handed back the same
file every hour, forever. So every automatic attempt is remembered on the
track, and three rules follow from it:

  * A track is asked for again only after a wait that doubles each time
    nothing better turned up: ``retry_days``, then twice that, up to
    ``MAX_WAIT_DAYS``. A new file starts the count over, since a better copy
    that is still below the profile is progress worth following up.
  * An attempt gets ``WINDOW_HOURS`` on the Wishlist. One that found nothing
    in that time steps aside until its next turn, instead of being searched
    for on every Wishlist run.
  * A download is only started for a copy that can be better than the one it
    would replace (``candidate_refusal``): not one whose quality is known and
    no better, and not the same streaming source that just delivered this
    file, which can only deliver it again.

The record is ``lib2_tracks.upgrade_attempt``: ``{"at", "count", "file"}``,
``file`` being the primary file the attempt was made against.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

COLUMN = "upgrade_attempt"
MAX_WAIT_DAYS = 90
WINDOW_HOURS = 12
#: how long the "this source just gave me this file" rule holds: accounts and
#: catalogues change, so after it one more try at the same source is allowed
SAME_SOURCE_DAYS = 30
_STAMP = "%Y-%m-%d %H:%M:%S"


def _now(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)


def _parse(value: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value)[:19].replace("T", " "), _STAMP)
    except (TypeError, ValueError):
        return None


def read(raw: Any) -> Optional[Dict[str, Any]]:
    """The stored attempt, or None when there is none that can be read."""
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or _parse(data.get("at")) is None:
        return None
    try:
        count = max(1, int(data.get("count") or 1))
    except (TypeError, ValueError):
        count = 1
    return {"at": _parse(data["at"]), "count": count, "file": data.get("file")}


def wait_days(count: int, retry_days: float) -> float:
    """How long to wait after the ``count``-th attempt that changed nothing."""
    return min(float(MAX_WAIT_DAYS), float(retry_days) * (2 ** max(0, int(count) - 1)))


def is_due(raw: Any, file_id: Any, *, retry_days: float,
           now: Optional[datetime] = None) -> bool:
    """Whether a track may be asked for again."""
    attempt = read(raw)
    if attempt is None:
        return True
    if file_id is not None and attempt["file"] is not None and str(attempt["file"]) != str(file_id):
        return True  # the file changed since: a new start
    return _now(now) >= attempt["at"] + timedelta(days=wait_days(attempt["count"], retry_days))


def next_stamp(raw: Any, file_id: Any, now: Optional[datetime] = None) -> str:
    """The record for an attempt made now."""
    attempt = read(raw)
    same_file = (attempt is not None and attempt["file"] is not None
                 and file_id is not None and str(attempt["file"]) == str(file_id))
    count = attempt["count"] + 1 if same_file else 1
    return json.dumps({"at": _now(now).strftime(_STAMP), "count": count, "file": file_id},
                      sort_keys=True)


def due_tracks(conn, track_ids: Iterable[int], *, retry_days: float, limit: int,
               now: Optional[datetime] = None) -> List[int]:
    """The first ``limit`` of ``track_ids`` that may be asked for now: the
    ones never asked first, then the longest-waiting."""
    from core.library2.track_files import primary_file_rows
    ids = [int(t) for t in track_ids]
    if not ids or limit <= 0:
        return []
    due: List[tuple] = []
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        primaries = primary_file_rows(conn, chunk)
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(
                f"SELECT id, {COLUMN} FROM lib2_tracks WHERE id IN ({marks})", chunk):
            file_id = (primaries.get(int(row[0])) or {}).get("id")
            if is_due(row[1], file_id, retry_days=retry_days, now=now):
                attempt = read(row[1])
                due.append((attempt["at"] if attempt else datetime.min, int(row[0])))
    due.sort()
    return [track_id for _at, track_id in due[:limit]]


def stamp_tracks(conn, track_ids: Iterable[int], now: Optional[datetime] = None) -> None:
    """Remember an attempt made now for each track."""
    from core.library2.track_files import primary_file_rows
    ids = [int(t) for t in track_ids]
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        primaries = primary_file_rows(conn, chunk)
        marks = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT id, {COLUMN} FROM lib2_tracks WHERE id IN ({marks})", chunk).fetchall()
        for row in rows:
            file_id = (primaries.get(int(row[0])) or {}).get("id")
            conn.execute(f"UPDATE lib2_tracks SET {COLUMN}=? WHERE id=?",
                         (next_stamp(row[1], file_id, now), int(row[0])))


def is_upgrade_wish(source_info: Any) -> Optional[int]:
    """The library track a Wishlist row asks a BETTER copy of, else None.

    Such a row is about a track that has its file: owning it is the premise of
    the wish, not a reason to drop it."""
    if isinstance(source_info, str):
        try:
            source_info = json.loads(source_info)
        except (TypeError, ValueError):
            return None
    if not isinstance(source_info, dict):
        return None
    if source_info.get("source") != "library_v2" or source_info.get("upgrade_check") is not True:
        return None
    try:
        return int(source_info.get("lib2_track_id"))
    except (TypeError, ValueError):
        return None


def expired_wishes(conn, profile_ids: Iterable[int], *, now: Optional[datetime] = None,
                   window_hours: float = WINDOW_HOURS) -> List[int]:
    """Wishlist row ids of automatic upgrade attempts whose time is up.

    Only a row made by the attempt on record counts: one added well after it
    (Enhance Quality, asked for by a person) is not this module's to remove.
    """
    profiles = [int(p) for p in profile_ids]
    if not profiles:
        return []
    try:
        rows = conn.execute(
            "SELECT id, source_info, date_added FROM wishlist_tracks "
            f"WHERE profile_id IN ({','.join('?' for _ in profiles)}) "
            "AND source_info LIKE '%upgrade_check%'", profiles).fetchall()
    except Exception:  # noqa: BLE001 - no wishlist table yet
        return []
    wishes = [(int(r[0]), is_upgrade_wish(r[1]), _parse(r[2])) for r in rows]
    track_ids = sorted({t for _id, t, _added in wishes if t})
    attempts: Dict[int, Any] = {}
    for start in range(0, len(track_ids), 500):
        chunk = track_ids[start:start + 500]
        for row in conn.execute(
                f"SELECT id, {COLUMN} FROM lib2_tracks "
                f"WHERE id IN ({','.join('?' for _ in chunk)})", chunk):
            attempts[int(row[0])] = read(row[1])
    cutoff = _now(now) - timedelta(hours=float(window_hours))
    expired = []
    for row_id, track_id, added in wishes:
        attempt = attempts.get(track_id) if track_id else None
        if attempt is None or added is None or attempt["at"] > cutoff:
            continue
        if abs((added - attempt["at"]).total_seconds()) <= 600:
            expired.append(row_id)
    return expired


def upgrade_basis(conn, track_id: int) -> Optional[Dict[str, Any]]:
    """What a download has to beat: the track's file as its profile ranks it."""
    from core.library2.quality_eval import (
        audio_quality_from_file, effective_track_profile, profile_targets,
    )
    from core.library2.track_files import primary_file_row
    from core.quality.model import rank_candidate
    file_row = primary_file_row(conn, int(track_id), scoped=True)
    quality = audio_quality_from_file(file_row)
    if file_row is None or quality is None or str(quality.format).lower() == "unknown":
        return None
    targets, _policy, _cutoff = profile_targets(effective_track_profile(conn, int(track_id)))
    rank = rank_candidate(quality, targets)[0] if targets else 0
    return {
        "targets": targets, "rank": rank, "score": quality.tier_score(),
        "source": str(file_row.get("source") or "").strip().lower(),
        "added": _parse(file_row.get("added_at")),
    }


_LOSSLESS = {"flac", "wav", "alac", "aiff", "ape", "dsf", "dff"}


def candidate_refusal(basis: Optional[Dict[str, Any]], candidate: Any, *,
                      streaming_sources: Iterable[str] = (),
                      now: Optional[datetime] = None) -> Optional[str]:
    """Why this candidate cannot be an upgrade, or None when it may be tried.

    Unknown is never a reason: a candidate whose quality cannot be told (a
    lossy file with no bitrate listed) is left for the import to measure.
    """
    if not basis:
        return None
    source = str(getattr(candidate, "username", "") or "").strip().lower()
    if (source and source == basis["source"] and source in set(streaming_sources)
            and basis["added"] is not None
            and _now(now) - basis["added"] < timedelta(days=SAME_SOURCE_DAYS)):
        return "the same source just delivered the file this would replace"
    quality = getattr(candidate, "audio_quality", None)
    fmt = str(getattr(quality, "format", "") or "").lower()
    if quality is None or fmt in ("", "unknown"):
        return None
    if fmt not in _LOSSLESS and not getattr(quality, "bitrate", None):
        return None
    try:
        from core.quality.model import rank_candidate
        rank = rank_candidate(quality, basis["targets"])[0] if basis["targets"] else 0
        score = quality.tier_score()
    except Exception:  # noqa: BLE001 - cannot rank it: let the import decide
        return None
    if rank > basis["rank"] or (rank == basis["rank"] and score <= basis["score"]):
        return "no better than the file you have"
    return None


__all__ = [
    "COLUMN", "MAX_WAIT_DAYS", "SAME_SOURCE_DAYS", "WINDOW_HOURS",
    "candidate_refusal", "due_tracks", "expired_wishes", "is_due", "is_upgrade_wish",
    "next_stamp", "read", "stamp_tracks", "upgrade_basis", "wait_days",
]
