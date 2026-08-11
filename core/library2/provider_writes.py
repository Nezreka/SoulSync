"""Write one provider's enrichment result straight onto a Library-v2 row.

What an enrichment worker uses once it has left the legacy tables
(docs §32.3.1 stage 2). The shape is deliberately not a new invention: it is what
``core.library2.enrich``'s mirror would have produced from an equivalent legacy
row. The mirror's declaration *is* the contract for what a lib2 row looks like, so
a worker that laid its data out differently would make its own output appear as
divergence in the integrity report.

Backfill semantics match the legacy workers exactly: a provider's image, style or
genre list fills an empty column and never overwrites one. Last.fm's artwork is a
fallback, not an authority, and a worker that started overwriting a picture the
user or a better source chose would be a regression.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.provider_writes")

_TABLES = {"artist": "lib2_artists", "album": "lib2_albums", "track": "lib2_tracks"}
# lib2 stores these twice: a first-class indexed column the read paths join on,
# and the external_ids JSON. Writing only the JSON leaves the column behind.
_PROMOTED = {"spotify": "spotify_id", "musicbrainz": "musicbrainz_id"}


def _entity(value: Any) -> str:
    text = str(value or "").rstrip("s")
    if text not in _TABLES:
        raise ValueError(f"Unknown entity type: {value!r}")
    return text


def _columns(conn, table: str) -> set:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip() in ("", "[]", "{}")


def _clean(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Drop keys with nothing in them.

    The mirror never stores an empty key, so a native write must not either — the
    same data has to look the same regardless of which side produced it.
    """
    return {
        key: value for key, value in (payload or {}).items()
        if value not in (None, "", [], {})
    }


def _merge_json(conn, table: str, entity_id: int, column: str,
                key: str, value: Any) -> None:
    row = conn.execute(
        f"SELECT {column} FROM {table} WHERE id=?", (entity_id,)).fetchone()
    if row is None:
        return
    try:
        current = json.loads(row[column] or "{}")
        if not isinstance(current, dict):
            current = {}
    except (TypeError, ValueError):
        current = {}
    if isinstance(value, dict):
        bucket = current.get(key)
        bucket = dict(bucket) if isinstance(bucket, dict) else {}
        bucket.update(value)
        merged = {**current, key: bucket}
    else:
        merged = {**current, key: value}
    if merged != current:
        conn.execute(
            f"UPDATE {table} SET {column}=? WHERE id=?",
            (json.dumps(merged, sort_keys=True, separators=(",", ":")), entity_id))


def write_provider_enrichment(
    conn, *, entity_type: str, entity_id: int, service: str,
    payload: Optional[Mapping[str, Any]] = None,
    provider_id: Optional[str] = None,
    backfill: Optional[Mapping[str, Any]] = None,
) -> None:
    """Apply one provider's answer to one lib2 row.

    ``payload`` is merged under ``enrichment[service]`` — keys absent from this
    answer keep their previous value, so a bio-only refresh does not erase the
    listener count. ``provider_id`` goes to ``external_ids[service]`` (and the
    promoted column, where there is one). ``backfill`` fills the named columns
    only while they are empty.
    """
    entity = _entity(entity_type)
    table = _TABLES[entity]
    key = str(service or "").strip().lower()
    if not key:
        raise ValueError("service is required")
    entity_id = int(entity_id)

    cleaned = _clean(payload or {})
    if cleaned:
        _merge_json(conn, table, entity_id, "enrichment", key, cleaned)

    if provider_id not in (None, ""):
        value = str(provider_id).strip()
        _merge_json(conn, table, entity_id, "external_ids", key, value)
        promoted = _PROMOTED.get(key)
        if promoted and promoted in _columns(conn, table):
            conn.execute(
                f"UPDATE {table} SET {promoted}=? "
                f"WHERE id=? AND COALESCE({promoted},'')<>?",
                (value, entity_id, value))

    if backfill:
        available = _columns(conn, table)
        unknown = set(backfill) - available
        if unknown:
            # A typo'd column would silently never be written, and the field
            # would look like a provider that returns nothing.
            raise ValueError(
                f"{table} has no column(s) {sorted(unknown)} to backfill")
        row = conn.execute(
            f"SELECT {', '.join(backfill)} FROM {table} WHERE id=?",
            (entity_id,)).fetchone()
        if row is not None:
            for column, value in backfill.items():
                if value not in (None, "") and _is_empty(row[column]):
                    conn.execute(
                        f"UPDATE {table} SET {column}=? WHERE id=?",
                        (value, entity_id))

    conn.execute(
        f"UPDATE {table} SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (entity_id,))


__all__ = ["write_provider_enrichment"]
