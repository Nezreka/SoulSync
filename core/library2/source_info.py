"""Download-provenance ("Source Info") lookup for a Library-v2 track.

The legacy Enhanced View shows a per-track ``ℹ`` popover with where the actual
audio file came from (service, Soulseek user, original filename, size, quality,
download time, status, history count) plus a "Blacklist This Source" action.

Resolution mirrors the legacy ``/api/library/track/<id>/source-info`` chain
(§16.1): ``track_downloads.track_id`` references the legacy ``tracks`` row, and
the importer records that id on every migrated track as
``lib2_tracks.legacy_track_id`` (and ``lib2_track_files.legacy_track_id``). So we
resolve by the LEGACY TRACK ID first — the authoritative, rename-proof link —
and only fall back to the primary FILE PATH (exact, then filename-suffix) when a
track has no legacy id at all (a pure autolink creation). Resolving purely by
path used to return "No download source data" for essentially every track whose
file had been renamed/reorganized/repaired since download, because none of those
paths update ``track_downloads.file_path``. Returns every matching record
newest-first so the popover can show a history count. Best-effort: a missing
``track_downloads`` table (fresh install) yields ``[]`` rather than raising.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.source_info")


def _legacy_track_id(conn, track_id: int) -> Optional[Any]:
    """The legacy ``tracks`` id this lib2 track was migrated from, if any.

    Prefers the track row's own link; falls back to its primary file's link
    (both are set by the importer, ``lib2_track_files`` also by autolink of a
    legacy-owned file)."""
    row = conn.execute(
        "SELECT legacy_track_id FROM lib2_tracks WHERE id=?", (int(track_id),)
    ).fetchone()
    legacy_id = row["legacy_track_id"] if row and "legacy_track_id" in row.keys() else None
    if legacy_id is not None:
        return legacy_id
    frow = conn.execute(
        "SELECT legacy_track_id FROM lib2_track_files "
        "WHERE track_id=? AND legacy_track_id IS NOT NULL "
        "ORDER BY is_primary DESC, id DESC LIMIT 1",
        (int(track_id),),
    ).fetchone()
    if frow and frow["legacy_track_id"] is not None:
        return frow["legacy_track_id"]
    return None


def track_source_info(conn, track_id: int) -> List[Dict[str, Any]]:
    """All download-provenance rows for a lib2 track, newest first."""
    from core.library2.track_files import primary_file_row

    try:
        # 1) Authoritative link: the legacy track id the provenance is keyed on.
        #    ``track_downloads.track_id`` is stored as text (legacy contract),
        #    so bind the id as a string exactly like ``get_track_downloads``.
        legacy_id = _legacy_track_id(conn, track_id)
        if legacy_id is not None:
            rows = conn.execute(
                "SELECT * FROM track_downloads WHERE track_id = ? ORDER BY id DESC",
                (str(legacy_id),),
            ).fetchall()
            if rows:
                return [dict(row) for row in rows]

        # 2) Fallback for tracks with no legacy id (pure autolink creations):
        #    resolve by the primary file path, then a filename-suffix match.
        file_row = primary_file_row(conn, track_id)
        path = file_row["path"] if file_row and "path" in file_row.keys() else None
        if not path:
            return []
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
