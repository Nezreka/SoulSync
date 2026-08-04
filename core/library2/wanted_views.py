"""Global Wanted Views for Library v2 (docs §64 I2 / §74): library-wide
"Missing" and "Cutoff Unmet" lists, Lidarr-style — every wanted track across
the whole library, not scoped to one artist/album.

Both lists read the already-materialized wanted projection
(``lib2_wanted_tracks``, see :mod:`core.library2.wanted``) rather than
recomputing monitor-rule priority here, and reuse
:mod:`core.library2.quality_eval` — the exact per-track evaluation
``get_album`` already uses — so this view can never disagree with the
per-album quality badges. Read-only; never raises.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from core.library2 import ADMIN_PROFILE_ID

from .quality_eval import evaluate_file, profile_targets
from .track_files import primary_file_rows

# A track is "consolidated away" when it deliberately has no file while its
# canonical duplicate partner (either link direction) owns one — the user
# just moved/deduped it. Mirrors api/library_v2.py's `_NOT_CONSOLIDATED_SQL`
# (kept duplicated rather than shared across the core/api layer boundary,
# same precedent as quality_upgrade.py/quality_upgrade_scanner.py's
# duplicated `_config_fingerprint`). Missing must never list these — they'd
# nag the user to redownload a duplicate they intentionally removed.
_CONSOLIDATED_ELSEWHERE_SQL = """
    EXISTS(
        SELECT 1 FROM lib2_tracks o
        JOIN lib2_track_files otf ON otf.track_id = o.id
             AND otf.path IS NOT NULL AND otf.path <> ''
             AND COALESCE(otf.file_state,'active')
                 NOT IN ('missing_confirmed','deleted')
        WHERE o.id = t.canonical_track_id
           OR o.canonical_track_id = t.id
    )
"""

_HAS_FILE_SQL = """
    EXISTS (
        SELECT 1 FROM lib2_track_files tf
         WHERE tf.track_id = t.id
           AND tf.path IS NOT NULL AND tf.path <> ''
           AND COALESCE(tf.file_state,'active') NOT IN ('missing_confirmed','deleted')
    )
"""

_ROW_SELECT = """
    SELECT t.id AS track_id, t.title AS track_title,
           t.track_number, t.disc_number, t.monitored,
           al.id AS album_id, al.title AS album_title, al.album_type,
           ar.id AS artist_id, ar.name AS artist_name,
           w.effective_profile_id
"""

_ROW_FROM = """
      FROM lib2_wanted_tracks w
      JOIN lib2_tracks t ON t.id = w.track_id
      JOIN lib2_albums al ON al.id = t.album_id
      JOIN lib2_artists ar ON ar.id = al.primary_artist_id
"""


def _search_clause(search: str) -> Tuple[str, Dict[str, Any]]:
    if not search:
        return "", {}
    return (
        " AND (t.title LIKE :like OR al.title LIKE :like OR ar.name LIKE :like)",
        {"like": f"%{search}%"},
    )


def _row_dict(row: Any) -> Dict[str, Any]:
    return {
        "track_id": row["track_id"],
        "title": row["track_title"],
        "track_number": row["track_number"],
        "disc_number": row["disc_number"],
        "monitored": bool(row["monitored"]),
        "album": {
            "id": row["album_id"],
            "title": row["album_title"],
            "album_type": row["album_type"],
        },
        "artist": {
            "id": row["artist_id"],
            "name": row["artist_name"],
        },
    }


def list_missing(conn: Any, *, search: str = "", page: int = 1, limit: int = 75,
                 profile_id: int = ADMIN_PROFILE_ID) -> Tuple[List[Dict[str, Any]], int]:
    """Wanted tracks with no owned file, excluding consolidated duplicates.

    Pure-SQL pagination — "has no file" is a plain EXISTS check, no
    per-track evaluation needed.
    """
    page = max(1, int(page))
    limit = max(1, min(int(limit), 500))
    offset = (page - 1) * limit
    like_sql, like_params = _search_clause(search)
    where = (
        "WHERE w.profile_id = :profile_id AND w.wanted = 1 "
        f"AND NOT ({_HAS_FILE_SQL}) AND NOT ({_CONSOLIDATED_ELSEWHERE_SQL})"
        + like_sql
    )
    params: Dict[str, Any] = {"profile_id": int(profile_id), **like_params}

    total = conn.execute(
        f"SELECT COUNT(*) AS c {_ROW_FROM} {where}", params
    ).fetchone()["c"]

    rows = conn.execute(
        f"""{_ROW_SELECT}
        {_ROW_FROM}
        {where}
        ORDER BY ar.sort_name COLLATE NOCASE, ar.name COLLATE NOCASE,
                 al.title COLLATE NOCASE, t.disc_number, t.track_number
        LIMIT :limit OFFSET :offset""",
        {**params, "limit": limit, "offset": offset},
    ).fetchall()
    return [_row_dict(r) for r in rows], int(total)


def list_cutoff_unmet(conn: Any, *, search: str = "", page: int = 1, limit: int = 75,
                      profile_id: int = ADMIN_PROFILE_ID) -> Tuple[List[Dict[str, Any]], int]:
    """Wanted tracks with a file that doesn't meet their quality profile's
    cutoff yet.

    Whether a file is an upgrade candidate depends on evaluating it against
    its resolved quality profile's ranked targets — not expressible in SQL —
    so this fetches every wanted+owned candidate, evaluates each in Python
    (grouped by the small set of distinct profiles actually in use, not one
    profile lookup per track), then paginates the filtered result.
    """
    page = max(1, int(page))
    limit = max(1, min(int(limit), 500))
    like_sql, like_params = _search_clause(search)
    where = (
        "WHERE w.profile_id = :profile_id AND w.wanted = 1 "
        f"AND ({_HAS_FILE_SQL})"
        + like_sql
    )
    params: Dict[str, Any] = {"profile_id": int(profile_id), **like_params}

    candidates = conn.execute(
        f"""{_ROW_SELECT}
        {_ROW_FROM}
        {where}
        ORDER BY ar.sort_name COLLATE NOCASE, ar.name COLLATE NOCASE,
                 al.title COLLATE NOCASE, t.disc_number, t.track_number""",
        params,
    ).fetchall()
    if not candidates:
        return [], 0

    track_ids = [int(r["track_id"]) for r in candidates]
    files = primary_file_rows(conn, track_ids)

    profile_ids = sorted({
        int(r["effective_profile_id"]) for r in candidates
        if r["effective_profile_id"] is not None
    })
    profile_rows: Dict[int, Dict[str, Any]] = {}
    if profile_ids:
        marks = ",".join("?" for _ in profile_ids)
        for prow in conn.execute(
            f"SELECT * FROM quality_profiles WHERE id IN ({marks})", profile_ids
        ):
            profile_rows[int(prow["id"])] = dict(prow)

    targets_cache: Dict[Any, Tuple[List[Any], str, int]] = {}

    def _targets_for(pid: Any) -> Tuple[List[Any], str, int]:
        if pid not in targets_cache:
            targets_cache[pid] = profile_targets(profile_rows.get(pid))
        return targets_cache[pid]

    unmet: List[Dict[str, Any]] = []
    for row in candidates:
        pid = int(row["effective_profile_id"]) if row["effective_profile_id"] is not None else None
        targets, policy, cutoff_index = _targets_for(pid)
        file_row = files.get(int(row["track_id"]))
        ev = evaluate_file(file_row, targets, policy, cutoff_index)
        if ev["upgrade_candidate"] is not True:
            continue
        entry = _row_dict(row)
        entry["meets_profile"] = ev["meets_profile"]
        entry["file"] = {
            "format": file_row.get("format") if file_row else None,
            "bitrate": file_row.get("bitrate") if file_row else None,
            "sample_rate": file_row.get("sample_rate") if file_row else None,
            "bit_depth": file_row.get("bit_depth") if file_row else None,
            "quality_tier": file_row.get("quality_tier") if file_row else None,
        }
        unmet.append(entry)

    total = len(unmet)
    offset = (page - 1) * limit
    return unmet[offset:offset + limit], total


__all__ = ["list_missing", "list_cutoff_unmet"]
