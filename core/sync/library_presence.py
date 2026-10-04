"""are the library tracks a mirror's last sync matched still there? (#1417)

a mirror sync skips when its track list is unchanged and every track matched
last time. that fingerprint never looked at the library, so deleting a matched
file changed nothing it checked: every later sync said "unchanged", nothing was
re-matched or wishlisted, and only deleting and recreating the playlist got the
song back. the skip now also needs every matched library track to still exist.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Set

from utils.logging_config import get_logger

logger = get_logger("sync.library_presence")

_CHUNK = 500   # under sqlite's bound-variable limit


def _extra(row: Any) -> dict:
    raw = (row or {}).get('extra_data')
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def existing_track_ids(db: Any, ids: Iterable[str]) -> Set[str]:
    """the ids that are rows in the library. no profile scope on purpose: this
    asks whether a file is gone, not who may see it."""
    wanted = sorted({str(i) for i in ids if i not in (None, '')})
    found: Set[str] = set()
    with db._get_connection() as conn:
        for start in range(0, len(wanted), _CHUNK):
            chunk = wanted[start:start + _CHUNK]
            marks = ','.join('?' * len(chunk))
            for row in conn.execute(f"SELECT id FROM tracks WHERE id IN ({marks})", chunk):
                found.add(str(row[0]))
    return found


def matched_tracks_still_present(db: Any, mirror_rows: Iterable[dict]) -> bool:
    """True only when every track the last sync matched is provably still in the
    library. a matched row with no recorded id (synced before ids were kept)
    can't be checked, so it counts as not proven and the sync runs once."""
    ids = []
    for row in mirror_rows:
        extra = _extra(row)
        if not extra.get('in_library'):
            continue
        lib_id = extra.get('library_track_id')
        if not lib_id:
            return False
        ids.append(str(lib_id))
    if not ids:
        return True
    try:
        present = existing_track_ids(db, ids)
    except Exception as e:  # noqa: BLE001 - unsure means sync, never skip
        logger.debug("library presence check failed: %s", e)
        return False
    missing = set(ids) - present
    if missing:
        logger.info("[Sync] %d matched track(s) left the library since the last sync", len(missing))
        return False
    return True


__all__ = ['existing_track_ids', 'matched_tracks_still_present']
