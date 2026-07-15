"""Resync a lib2 entity's provider-sourced fields from its legacy counterpart
row right after ``core.metadata`` enrichment workers (see ``web_server.py``'s
``_run_single_enrichment``) re-query a provider and write fresh data into it.

lib2 rows are a point-in-time mirror of the legacy library (see
``core.library2.importer``): enrichment only ever updates the LEGACY row, so
without this the refreshed data would be invisible in the lib2 UI until a
full re-import. Unlike the bulk importer's upsert (which never regresses a
richer existing value across incremental imports — see
``_ArtistResolver.upsert_legacy``), a user-triggered Enrich is an explicit
"pull fresh data now" action for ONE entity, so its provider-owned fields are
safe to overwrite outright — except we still guard against clobbering good
existing data with a legacy column that some OTHER, untouched provider left
NULL, hence ``COALESCE``. Identity fields (name/title) are intentionally left
alone; Enrich only refreshes descriptive metadata. User overrides
(``core.library2.metadata_overrides``) are layered on top at read time
regardless of the base row, so overwriting the base row here is always safe.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional


def _row_get(row: Any, col: str) -> Optional[Any]:
    return row[col] if col in row.keys() else None


def _normalize_genres(raw: Any) -> str:
    """Mirror legacy genre storage (JSON array OR comma string) → JSON array
    string. Duplicated from ``importer._normalize_genres`` (module-private,
    same tiny-helper-duplication precedent as ``_precache_max_workers`` in
    ``artwork.py``/``completeness.py``)."""
    if not raw:
        return "[]"
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return json.dumps([str(g).strip() for g in parsed if str(g).strip()])
    except (ValueError, TypeError):
        pass
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    return json.dumps(parts)


def resync_artist_from_legacy(conn, lib2_artist_id: int, legacy_row: Any) -> bool:
    genres = _row_get(legacy_row, "genres")
    conn.execute(
        "UPDATE lib2_artists SET "
        "image_url=COALESCE(?, image_url), "
        "genres=COALESCE(?, genres), "
        "summary=COALESCE(?, summary), style=COALESCE(?, style), "
        "mood=COALESCE(?, mood), label=COALESCE(?, label), "
        "banner_url=COALESCE(?, banner_url), updated_at=CURRENT_TIMESTAMP "
        "WHERE id=?",
        (
            _row_get(legacy_row, "thumb_url"),
            _normalize_genres(genres) if genres else None,
            _row_get(legacy_row, "summary"),
            _row_get(legacy_row, "style"),
            _row_get(legacy_row, "mood"),
            _row_get(legacy_row, "label"),
            _row_get(legacy_row, "banner_url"),
            lib2_artist_id,
        ),
    )
    return True


def resync_album_from_legacy(conn, lib2_album_id: int, legacy_row: Any) -> bool:
    genres = _row_get(legacy_row, "genres")
    conn.execute(
        "UPDATE lib2_albums SET "
        "image_url=COALESCE(?, image_url), "
        "genres=COALESCE(?, genres), "
        "label=COALESCE(?, label), explicit=COALESCE(?, explicit), "
        "upc=COALESCE(?, upc), updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (
            _row_get(legacy_row, "thumb_url"),
            _normalize_genres(genres) if genres else None,
            _row_get(legacy_row, "label"),
            _row_get(legacy_row, "explicit"),
            _row_get(legacy_row, "upc"),
            lib2_album_id,
        ),
    )
    return True


def resync_track_from_legacy(conn, lib2_track_id: int, legacy_row: Any) -> bool:
    conn.execute(
        "UPDATE lib2_tracks SET "
        "bpm=COALESCE(?, bpm), explicit=COALESCE(?, explicit), "
        "genius_lyrics=COALESCE(?, genius_lyrics), "
        "copyright=COALESCE(?, copyright), updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (
            _row_get(legacy_row, "bpm"),
            _row_get(legacy_row, "explicit"),
            _row_get(legacy_row, "genius_lyrics"),
            _row_get(legacy_row, "copyright"),
            lib2_track_id,
        ),
    )
    return True


_RESYNC: Dict[str, tuple] = {
    "artist": ("artists", resync_artist_from_legacy),
    "album": ("albums", resync_album_from_legacy),
    "track": ("tracks", resync_track_from_legacy),
}


def resync_entity_from_legacy(conn, entity_type: str, lib2_id: int, legacy_id: int) -> bool:
    """Re-read the legacy row and overwrite the lib2 row's provider fields.

    Returns False (no-op) if the legacy row is gone or ``entity_type`` is
    unrecognized — the caller's enrichment result is unaffected either way.
    """
    spec = _RESYNC.get(entity_type)
    if spec is None:
        return False
    legacy_table, fn = spec
    row = conn.execute(f"SELECT * FROM {legacy_table} WHERE id=?", (legacy_id,)).fetchone()
    if row is None:
        return False
    return fn(conn, lib2_id, row)


__all__ = [
    "resync_artist_from_legacy",
    "resync_album_from_legacy",
    "resync_track_from_legacy",
    "resync_entity_from_legacy",
]
