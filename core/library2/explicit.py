"""Which Library v2 tracks are explicit, for the kids guard (api/content_guard).

Upstream's guard asks the legacy ``tracks``/``albums`` tables; on this branch
the catalogue is lib2, so the same question is answered here. A track counts
as explicit when its own flag or its release's flag says so, after the user's
metadata overrides (``lib2_metadata_overrides``) -- the value the library page
shows is the one the guard enforces. NULL stays "nobody told us", exactly as
core.content_filter.is_explicit reads it.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional, Set

from core.content_filter import is_explicit


def _overrides_present(conn: Any) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lib2_metadata_overrides'"
    ).fetchone() is not None


def _flags_sql(conn: Any) -> str:
    """SELECT of (track id, effective track flag, effective release flag)."""
    if not _overrides_present(conn):
        return ("SELECT t.id, t.explicit, al.explicit FROM lib2_tracks t "
                "LEFT JOIN lib2_albums al ON al.id = t.album_id ")
    return ("SELECT t.id, COALESCE(ot.value_json, t.explicit), COALESCE(oa.value_json, al.explicit) "
            "FROM lib2_tracks t "
            "LEFT JOIN lib2_albums al ON al.id = t.album_id "
            "LEFT JOIN lib2_metadata_overrides ot ON ot.entity_type = 'track' "
            " AND ot.entity_id = t.id AND ot.field_name = 'explicit' "
            "LEFT JOIN lib2_metadata_overrides oa ON oa.entity_type = 'release_group' "
            " AND oa.entity_id = t.album_id AND oa.field_name = 'explicit' ")


def _any_explicit(rows) -> bool:
    return any(is_explicit(r[1]) or is_explicit(r[2]) for r in rows)


def explicit_track_ids(conn: Any, track_ids: Iterable[Any]) -> Set[int]:
    """The lib2 track ids among ``track_ids`` that are explicit."""
    ids = set()
    for tid in track_ids or ():
        try:
            ids.add(int(tid))
        except (TypeError, ValueError):
            continue
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    rows = conn.execute(_flags_sql(conn) + f"WHERE t.id IN ({marks})", sorted(ids)).fetchall()
    return {int(r[0]) for r in rows if is_explicit(r[1]) or is_explicit(r[2])}


def library_track_is_explicit(conn: Any, *, file_path: Optional[str] = None,
                              lib2_track_id: Any = None, track_id: Any = None) -> bool:
    """Is the library track behind this path / these ids explicit?

    ``track_id`` is what the player sends as a server or legacy id (a lib2 id
    rides separately as ``lib2_track_id``). A path the catalogue doesn't know
    exactly is matched on its last two parts (album folder + file), so a
    re-rooted path can't walk around the check.
    """
    fp = str(file_path or "").strip()
    lib2_id = str(lib2_track_id or "").strip()
    ext_id = str(track_id or "").strip()
    if not fp and not lib2_id and not ext_id:
        return False
    conds, params = [], []
    if fp:
        conds.append("t.id IN (SELECT track_id FROM lib2_track_files WHERE path = ?)")
        params.append(fp)
    if lib2_id:
        conds.append("CAST(t.id AS TEXT) = ?")
        params.append(lib2_id)
    if ext_id:
        conds.append("(CAST(t.legacy_track_id AS TEXT) = ? OR t.server_id = ? OR t.id IN "
                     "(SELECT entity_id FROM lib2_media_server_mappings "
                     " WHERE entity_type = 'track' AND server_id = ?))")
        params.extend([ext_id, ext_id, ext_id])
    sql = _flags_sql(conn)
    rows = conn.execute(sql + "WHERE " + " OR ".join(conds), params).fetchall()
    if not rows and fp:
        parts = [p for p in re.split(r"[\\/]+", fp) if p]
        tail = "/".join(parts[-2:])
        if tail:
            esc = tail.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = conn.execute(
                sql + "WHERE t.id IN (SELECT track_id FROM lib2_track_files "
                      "WHERE REPLACE(path, '\\', '/') LIKE ? ESCAPE '\\')",
                ("%/" + esc,)).fetchall()
    return _any_explicit(rows)


__all__ = ["explicit_track_ids", "library_track_is_explicit"]
