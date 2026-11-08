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


def _strict_int(value: str) -> Optional[int]:
    """``value`` as an int only when it is exactly that int's text, the same
    rows ``CAST(id AS TEXT) = value`` matched."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if str(number) == value else None


def _matching_track_ids(conn: Any, fp: str, lib2_id: str, ext_id: str) -> Set[int]:
    """The lib2 tracks behind a path / these ids, one indexed lookup each.

    one OR across them, with ``CAST(t.id AS TEXT) = ?`` and
    ``CAST(t.legacy_track_id AS TEXT) = ?``, could use no index: every guarded
    play scanned the whole catalogue, seconds per play on a big library.
    review 5 R5-E-4 / R5-X3-2."""
    from core.library2.media_mappings import MEDIA_SERVER_SOURCES
    from core.media_server import specs

    ids: Set[int] = set()

    def add(sql: str, params) -> None:
        ids.update(int(r[0]) for r in conn.execute(sql, params).fetchall() if r[0] is not None)

    if fp:
        add("SELECT track_id FROM lib2_track_files WHERE path = ?", (fp,))
    if lib2_id:
        number = _strict_int(lib2_id)
        if number is not None:
            ids.add(number)
    if ext_id:
        # legacy_track_id is INTEGER affinity: the text compares as the
        # number it spells, and a text id (a plex/jellyfin key that was not
        # numeric) compares as text, the rows the CAST matched
        add("SELECT id FROM lib2_tracks WHERE legacy_track_id = ?", (ext_id,))
        # idx_lib2_tracks_server (server_source, server_id) and
        # idx_lib2_media_mappings_server (server_source, entity_type,
        # server_id) need the source; every writer stores a known one
        sources = sorted(set(specs.names()))
        marks = ",".join("?" * len(sources))
        add(f"SELECT id FROM lib2_tracks WHERE server_source IN ({marks}) AND server_id = ?",
            (*sources, ext_id))
        mapped = sorted(MEDIA_SERVER_SOURCES)
        marks = ",".join("?" * len(mapped))
        add("SELECT entity_id FROM lib2_media_server_mappings "
            f"WHERE server_source IN ({marks}) AND entity_type = 'track' AND server_id = ?",
            (*mapped, ext_id))
    return ids


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
    sql = _flags_sql(conn)
    rows = []
    ids = _matching_track_ids(conn, fp, lib2_id, ext_id)
    if ids:
        marks = ",".join("?" * len(ids))
        rows = conn.execute(sql + f"WHERE t.id IN ({marks})", sorted(ids)).fetchall()
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
