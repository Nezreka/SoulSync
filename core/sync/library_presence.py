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

_CHUNK = 450   # two IN lists plus two source binds stay under SQLite's oldest limit


def _extra(row: Any) -> dict:
    raw = (row or {}).get('extra_data')
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def existing_track_ids(db: Any, ids: Iterable[str]) -> Set[str]:
    """The active server's ids with a live file in the selected library.

    the id a sync records is the media server's track id (that is what the
    sync matches against), so it is looked up through the server mappings and
    the track row's own server id. Another server can reuse the same id, and
    another library can own a different copy of the same catalogue track;
    neither proves that this sync's matched file is still there."""
    from core.library2.sql_util import owner_clause
    from core.settings import config_manager

    server_source = config_manager.get_active_media_server()
    if not server_source:
        return set()
    wanted = sorted({str(i) for i in ids if i not in (None, '')})
    found: Set[str] = set()
    live = ("EXISTS (SELECT 1 FROM lib2_track_files f WHERE f.track_id = {track}"
            " AND COALESCE(f.file_state, 'active') = 'active'"
            " AND f.path IS NOT NULL AND TRIM(f.path) <> ''"
            f"{owner_clause(column='f.owner_profile_id')})")
    with db._get_connection() as conn:
        for start in range(0, len(wanted), _CHUNK):
            chunk = wanted[start:start + _CHUNK]
            marks = ','.join('?' * len(chunk))
            for row in conn.execute(
                    f"SELECT m.server_id FROM lib2_media_server_mappings m"
                    f" WHERE m.entity_type = 'track' AND m.server_source = ?"
                    f" AND m.server_id IN ({marks})"
                    f" AND {live.format(track='m.entity_id')}"
                    f" UNION SELECT t.server_id FROM lib2_tracks t"
                    f" WHERE t.server_source = ? AND t.server_id IN ({marks})"
                    f" AND {live.format(track='t.id')}",
                    [server_source, *chunk, server_source, *chunk]):
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
