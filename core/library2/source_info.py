"""Download-provenance ("Source Info") lookup for a Library-v2 track.

The legacy Enhanced View shows a per-track ``ℹ`` popover with where the actual
audio file came from (service, Soulseek user, original filename, size, quality,
download time, status, history count) plus a "Blacklist This Source" action.

Library v2 track ids are their OWN id space, unrelated to the legacy ``tracks``
rows that ``track_downloads.track_id`` references — so we resolve provenance by
the track's primary FILE PATH instead: exact path first, then a filename-suffix
match (handles Plex/local path-format mismatches), mirroring the legacy
``/api/library/track/<id>/source-info`` fallback chain. Returns every matching
record newest-first so the popover can show a history count. Best-effort: a
missing ``track_downloads`` table (fresh install) yields ``[]`` rather than
raising.
"""

from __future__ import annotations

from typing import Any, Dict, List

from utils.logging_config import get_logger

logger = get_logger("library2.source_info")


def track_source_info(conn, track_id: int) -> List[Dict[str, Any]]:
    """All download-provenance rows for a lib2 track, newest first."""
    from core.library2.track_files import primary_file_row

    file_row = primary_file_row(conn, track_id)
    path = file_row["path"] if file_row and "path" in file_row.keys() else None
    if not path:
        return []
    try:
        rows = conn.execute(
            "SELECT * FROM track_downloads WHERE file_path = ? ORDER BY id DESC",
            (path,),
        ).fetchall()
        if not rows:
            fname = str(path).replace("\\", "/").rsplit("/", 1)[-1]
            if fname:
                rows = conn.execute(
                    "SELECT * FROM track_downloads "
                    "WHERE file_path LIKE ? OR file_path LIKE ? ORDER BY id DESC",
                    (f"%/{fname}", f"%\\{fname}"),
                ).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:  # noqa: BLE001 — legacy table may be absent
        logger.debug("track_source_info lookup failed (track %s): %s", track_id, e)
        return []


__all__ = ["track_source_info"]
