"""The v1 API's retag queue, read from Library v2.

``/api/v1/retag/*`` used to list the legacy ``retag_groups`` / ``retag_tracks``
tables: albums and tracks waiting for a metadata correction. Nothing has filled
those since Library v2; what is waiting for a tag correction now is the Re-tag
job's pending findings (``core/repair_jobs/library_retag``), one per file whose
tags disagree with the catalogue. This answers the old routes from those, in
the shape the API docs describe, so a script built on them keeps working:

* a group is an album with pending Re-tag findings, its id the album's id;
* a track is one pending finding, its id the finding's id;
* deleting a group dismisses its findings, the way the queue entry went away.
"""

from __future__ import annotations

import os
from contextlib import closing
from typing import Any, Dict, List, Optional

_JOB = "library_retag"


def _rows(db, album_id: Optional[int] = None) -> List[Dict[str, Any]]:
    from core.library2.provider_ids import provider_id_sql
    from core.library2.sql_util import in_library_sql

    where = " AND al.id = ?" if album_id is not None else ""
    sql = f"""
        SELECT f.id AS finding_id, f.file_path, f.created_at,
               t.title, t.track_number, t.disc_number,
               {provider_id_sql('spotify', alias='t')} AS spotify_track_id,
               {provider_id_sql('itunes', alias='t')} AS itunes_track_id,
               al.id AS album_id, al.title AS album_name, al.image_url, al.release_date,
               {provider_id_sql('spotify', alias='al')} AS spotify_album_id,
               {provider_id_sql('itunes', alias='al')} AS itunes_album_id,
               COALESCE(al.expected_track_count, al.track_count) AS total_tracks,
               ar.name AS artist_name
        FROM repair_findings f
        JOIN lib2_tracks t ON t.id = CAST(substr(f.entity_id, 6) AS INTEGER)
        JOIN lib2_albums al ON al.id = t.album_id
        LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
        WHERE f.job_id = ? AND f.status = 'pending' AND f.entity_id LIKE 'lib2:%'
              {in_library_sql('track', 't')}{where}
    """
    with closing(db._get_connection()) as conn:
        cur = conn.execute(sql, (_JOB,) + ((album_id,) if album_id is not None else ()))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _group(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    first = rows[0]
    return {
        "id": first["album_id"], "group_type": "album",
        "artist_name": first["artist_name"] or "", "album_name": first["album_name"] or "",
        "image_url": first["image_url"], "spotify_album_id": first["spotify_album_id"],
        "itunes_album_id": first["itunes_album_id"], "total_tracks": first["total_tracks"] or 0,
        "release_date": first["release_date"], "created_at": min(r["created_at"] for r in rows),
        "track_count": len(rows),
    }


def _by_album(rows):
    albums: Dict[int, List[Dict[str, Any]]] = {}
    for row in rows:
        albums.setdefault(row["album_id"], []).append(row)
    return albums


def retag_groups(db) -> List[Dict[str, Any]]:
    """Every album with pending Re-tag findings, by artist, newest first."""
    groups = [_group(rows) for rows in _by_album(_rows(db)).values()]
    groups.sort(key=lambda g: g["created_at"] or "", reverse=True)
    groups.sort(key=lambda g: g["artist_name"].lower())
    return groups


def retag_group(db, album_id: int) -> Optional[Dict[str, Any]]:
    """One album's group and its tracks, by disc then track, or None."""
    rows = _rows(db, album_id)
    if not rows:
        return None
    rows.sort(key=lambda r: (r["disc_number"] or 1, r["track_number"] or 0))
    tracks = [{
        "id": r["finding_id"], "group_id": r["album_id"],
        "track_number": r["track_number"], "disc_number": r["disc_number"] or 1,
        "title": r["title"] or "", "file_path": r["file_path"],
        "file_format": os.path.splitext(r["file_path"] or "")[1].lstrip(".").lower() or None,
        "spotify_track_id": r["spotify_track_id"], "itunes_track_id": r["itunes_track_id"],
        "created_at": r["created_at"],
    } for r in rows]
    return {"group": _group(rows), "tracks": tracks}


def _dismiss(db, finding_ids: List[int]) -> int:
    if not finding_ids:
        return 0
    marks = ",".join("?" * len(finding_ids))
    with closing(db._get_connection()) as conn:
        cur = conn.execute(
            f"UPDATE repair_findings SET status = 'dismissed', resolved_at = CURRENT_TIMESTAMP,"
            f" updated_at = CURRENT_TIMESTAMP WHERE status = 'pending' AND id IN ({marks})",
            finding_ids)
        conn.commit()
        return cur.rowcount


def dismiss_retag_group(db, album_id: int) -> bool:
    """Dismiss one album's pending Re-tag findings. False when it had none."""
    return _dismiss(db, [r["finding_id"] for r in _rows(db, album_id)]) > 0


def dismiss_all_retag_groups(db) -> int:
    """Dismiss every pending Re-tag finding the caller's library holds; the
    number of albums (groups) that were cleared."""
    albums = _by_album(_rows(db))
    _dismiss(db, [r["finding_id"] for rows in albums.values() for r in rows])
    return len(albums)


def retag_stats(db) -> Dict[str, int]:
    rows = _rows(db)
    return {
        "groups": len({r["album_id"] for r in rows}),
        "tracks": len(rows),
        "artists": len({(r["artist_name"] or "").lower() for r in rows}),
    }
