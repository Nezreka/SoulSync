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
        # 1) Authoritative link: the rows keyed on this catalogue track, and a
        #    3.6.1 row the upgrade carry could not map (still on its legacy
        #    id). Both, newest first: a re-download after the upgrade must not
        #    hide behind the old 3.6.1 row ("Blacklist This Source" then hit
        #    the wrong uploader). ``track_id_kind`` keeps the two id spaces
        #    apart, so a legacy number never reads as a catalogue id.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(track_downloads)")}
        legacy_id = _legacy_track_id(conn, track_id)
        if "track_id_kind" in cols:
            rows = conn.execute(
                "SELECT * FROM track_downloads WHERE (track_id = ? AND track_id_kind = 'lib2')"
                " OR (track_id = ? AND track_id_kind = 'legacy') ORDER BY id DESC",
                (str(int(track_id)), str(legacy_id) if legacy_id is not None else None),
            ).fetchall()
        elif legacy_id is not None:
            rows = conn.execute(
                "SELECT * FROM track_downloads WHERE track_id = ? ORDER BY id DESC",
                (str(legacy_id),),
            ).fetchall()
        else:
            rows = []
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


def carry_legacy_download_ids(conn) -> int:
    """once, after the upgrade import: a 3.6.1 download row's track_id is a
    legacy tracks.id. map it to the catalogue track (same rule as the other
    legacy references: lib2_tracks.legacy_track_id, then a joined copy's file
    row); a row nothing maps stays, marked 'legacy'. rows written after the
    upgrade began hold catalogue ids already. returns how many mapped."""
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(track_downloads)")}
        if "track_id_kind" not in cols:
            return 0
        state = conn.execute("SELECT started_at FROM lib2_bootstrap_state WHERE id=1").fetchone() \
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='lib2_bootstrap_state'").fetchone() else None
        started = state[0] if state else None
        # written since the upgrade began: already a catalogue id
        if started:
            # review 4 R4-A-2: started_at is ISO ('...T10:00:00+00:00') and
            # created_at is CURRENT_TIMESTAMP ('... 10:30:00'); as text a
            # same-day row sorted before the upgrade and was read as legacy
            conn.execute("UPDATE track_downloads SET track_id_kind='lib2' WHERE track_id_kind IS NULL "
                         "AND track_id IS NOT NULL AND julianday(created_at) >= julianday(?)",
                         (started,))
        rows = conn.execute("SELECT id, track_id FROM track_downloads "
                            "WHERE track_id_kind IS NULL AND track_id IS NOT NULL").fetchall()
        mapped = 0
        for row_id, legacy in rows:
            hit = conn.execute(
                "SELECT id FROM lib2_tracks WHERE legacy_track_id=? "
                "UNION SELECT track_id FROM lib2_track_files WHERE legacy_track_id=? "
                "AND track_id IS NOT NULL LIMIT 2", (legacy, legacy)).fetchall()
            if len(hit) == 1:
                conn.execute("UPDATE track_downloads SET track_id=?, track_id_kind='lib2' WHERE id=?",
                             (str(int(hit[0][0])), row_id))
                mapped += 1
            else:
                conn.execute("UPDATE track_downloads SET track_id_kind='legacy' WHERE id=?", (row_id,))
        conn.commit()
        return mapped
    except Exception as exc:  # noqa: BLE001 - the next start retries
        logger.warning("download history ids not carried yet: %s", exc)
        return 0


__all__ = ["track_source_info", "carry_legacy_download_ids"]

