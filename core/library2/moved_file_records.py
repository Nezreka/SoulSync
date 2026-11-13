"""A file that moved, and the record of where it used to be.

A file is on disk once. The catalogue can still hold it twice: the record of
its old location and a second record of where it is now, when something moved
the file and a scan found it there before the first record was repointed. The
old record names a path that is gone, yet it stayed a live file until two
scans had missed it, so the song was listed twice (wolf39us, Nov 13 2026: one
`01 - Lost Stars.flac` of 32,170,087 bytes under two albums' folders, only one
of which existed).

The scan already knows the old record's file is absent. This asks one more
question before it starts the two-scan wait: is the very same file, by name and
byte size, on disk under another live record? If exactly one is, the absent
record is settled at once:

- on the same track, it is a spare record of a file the track still has;
- on another track, the two tracks are the same recording on two releases. They
  are linked the way "Linked as duplicate" links them, so the track left
  without a file is not read as missing and downloaded again, and the file
  stays where it is recorded as being on disk.

Nothing on disk is touched and no track is deleted. Anything less certain
(several candidates, a different name, a size the catalogue never recorded, a
pair the duplicate rules refuse) is left to the scan's ordinary lifecycle.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.moved_file_records")


def _basename(path: Any) -> str:
    return os.path.basename(str(path or "").replace("\\", "/")).casefold()


def find_present_twins(conn, rows: Iterable[Any], *,
                       exists: Callable[[str], bool]) -> Dict[int, Dict[str, Any]]:
    """``{absent file id: the one live record holding the same file}``.

    ``rows`` are file rows whose path does not resolve. ``exists`` says whether
    a stored path is on disk right now."""
    wanted: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        try:
            size = int(row["size"] or 0)
        except (TypeError, ValueError, KeyError, IndexError):
            size = 0
        if size > 0 and _basename(row["path"]):
            wanted[int(row["id"])] = {"size": size, "name": _basename(row["path"])}
    if not wanted:
        return {}
    ids = sorted(wanted)
    owners: Dict[int, Any] = {}
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        for found in conn.execute(
                f"SELECT id, owner_profile_id FROM lib2_track_files "
                f"WHERE id IN ({','.join('?' * len(part))})", part):
            owners[int(found["id"])] = found["owner_profile_id"]
    by_size: Dict[int, List[Any]] = {}
    sizes = sorted({entry["size"] for entry in wanted.values()})
    for start in range(0, len(sizes), 500):
        part = sizes[start:start + 500]
        for found in conn.execute(
                f"SELECT id, track_id, path, size, owner_profile_id FROM lib2_track_files "
                f"WHERE size IN ({','.join('?' * len(part))}) AND track_id IS NOT NULL "
                f"AND COALESCE(file_state,'active')='active' "
                f"AND path IS NOT NULL AND TRIM(path)<>''", part):
            by_size.setdefault(int(found["size"]), []).append(found)
    twins: Dict[int, Dict[str, Any]] = {}
    for file_id, entry in wanted.items():
        candidates = [
            c for c in by_size.get(entry["size"], ())
            if int(c["id"]) != file_id and int(c["id"]) not in wanted
            and c["owner_profile_id"] == owners.get(file_id)
            and _basename(c["path"]) == entry["name"]
        ]
        present = [c for c in candidates if exists(str(c["path"]))]
        if len(present) == 1:
            twins[file_id] = dict(present[0])
    return twins


def _has_other_live_file(conn, track_id: int, file_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM lib2_track_files WHERE track_id=? AND id<>? "
        "AND COALESCE(file_state,'active')='active' AND path IS NOT NULL AND TRIM(path)<>'' "
        "LIMIT 1", (int(track_id), int(file_id))).fetchone() is not None


def _confirm_gone(conn, file_id: int) -> None:
    from core.library2.track_files import set_file_state
    conn.execute(
        "UPDATE lib2_track_files SET missing_since=COALESCE(missing_since, CURRENT_TIMESTAMP),"
        " missing_scan_count=COALESCE(missing_scan_count,0)+1, updated_at=CURRENT_TIMESTAMP"
        " WHERE id=?", (int(file_id),))
    set_file_state(conn, int(file_id), "missing_confirmed")


def settle(conn, absent: Any, twin: Dict[str, Any]) -> Optional[str]:
    """Settle one absent record against the live record of the same file.

    Returns what was done: ``"spare_record"`` (the track still has the file),
    ``"linked"`` (the two tracks are now one recording on two releases), or
    None when the pair is left to the ordinary lifecycle. Does not commit."""
    from core.library2.duplicate_relationship import (
        DuplicateRelationshipError, validate_duplicate_pair,
    )

    file_id = int(absent["id"])
    if absent["track_id"] is None:
        return None
    track_id, twin_track = int(absent["track_id"]), int(twin["track_id"])
    if track_id == twin_track or _has_other_live_file(conn, track_id, file_id):
        _confirm_gone(conn, file_id)
        return "spare_record"
    link = conn.execute(
        "SELECT (SELECT canonical_track_id FROM lib2_tracks WHERE id=?),"
        "       (SELECT canonical_track_id FROM lib2_tracks WHERE id=?)",
        (track_id, twin_track)).fetchone()
    already = link[0] == twin_track or link[1] == track_id
    if not already:
        try:
            # the same bytes under the same name: that confirms the recording
            # where an artist or title tag disagrees. durations, hard ids and
            # the shape of existing links are still checked.
            validate_duplicate_pair(conn, track_id, twin_track, confirmed_recording=True)
        except DuplicateRelationshipError as exc:
            logger.debug("moved file: track %s not linked to %s: %s", track_id, twin_track, exc)
            return None
        conn.execute(
            "UPDATE lib2_tracks SET canonical_track_id=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=?", (twin_track, track_id))
    _confirm_gone(conn, file_id)
    return "linked"


def settle_absent_rows(database, rows: List[Any], *,
                       exists: Optional[Callable[[str], bool]] = None,
                       root_healthy: Optional[Callable[[str], bool]] = None) -> Dict[int, str]:
    """Settle every absent row that has a present twin. ``{file id: outcome}``.

    One short transaction per record; a record whose own folder cannot be
    judged (the storage it was on is away) is not touched."""
    from contextlib import closing

    if exists is None:
        from core.library2.paths import resolve_lib2_path
        exists = lambda path: bool(resolve_lib2_path(path))  # noqa: E731
    if root_healthy is None:
        from core.library2.paths import missing_path_root_is_healthy
        root_healthy = missing_path_root_is_healthy
    judged = [row for row in rows
              if (row["file_state"] or "active") in ("active", "missing_suspected")
              and root_healthy(row["path"])]
    if not judged:
        return {}
    done: Dict[int, str] = {}
    with closing(database._get_connection()) as conn:
        twins = find_present_twins(conn, judged, exists=exists)
        for row in judged:
            twin = twins.get(int(row["id"]))
            if not twin:
                continue
            try:
                outcome = settle(conn, row, twin)
                conn.commit()
            except Exception as exc:  # noqa: BLE001 - one record must not stop the scan
                conn.rollback()
                logger.warning("moved file: record %s not settled: %s", row["id"], exc)
                continue
            if outcome:
                done[int(row["id"])] = outcome
                logger.info(
                    "Library scan: %s is where a file used to be; it is at %s now (%s)",
                    row["path"], twin["path"], outcome.replace("_", " "))
    return done


__all__ = ["find_present_twins", "settle", "settle_absent_rows"]
