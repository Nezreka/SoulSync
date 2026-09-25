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
    columns: Optional[Mapping[str, Any]] = None,
) -> None:
    """Apply one provider's answer to one lib2 row.

    ``payload`` is merged under ``enrichment[service]`` — keys absent from this
    answer keep their previous value, so a bio-only refresh does not erase the
    listener count. ``provider_id`` goes to ``external_ids[service]`` (and the
    promoted column, where there is one).

    Two column modes, because the providers genuinely differ. ``backfill`` fills a
    column only while it is empty — for fallbacks like Last.fm artwork. ``columns``
    writes outright, for fields where a fresh fetch is the newer truth (Genius
    lyrics); a ``None`` there still leaves the existing value alone, so a failed
    lyrics fetch cannot blank lyrics already stored.
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

    if columns:
        available = _columns(conn, table)
        unknown = set(columns) - available
        if unknown:
            raise ValueError(
                f"{table} has no column(s) {sorted(unknown)} to write")
        for column, value in columns.items():
            if value not in (None, ""):
                conn.execute(
                    f"UPDATE {table} SET {column}=? WHERE id=?", (value, entity_id))

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


def claim_provider_id(conn, *, entity_type: str, entity_id: int, service: str,
                      provider_id: Any) -> bool:
    """Write one provider id ONLY while the row still has none. Returns whether
    this call is the one that landed it.

    ``write_provider_enrichment`` writes outright, which is right for a worker
    reporting the answer it just fetched. A gap-fill is a different operation:
    it says "if nobody knows this id, here is one from a file's tags" — and
    read-then-write cannot express that across connections. An enrichment
    worker running concurrently can settle the same entity in the window
    between the read and the write, and the outright write then replaces a
    freshly matched id with a tag that may be years old.

    So the guard lives in the statement, exactly as the legacy tables' guarded
    ``UPDATE ... WHERE id-column IS NULL OR ''`` did. Emptiness is tested the
    way :func:`core.library2.worker_support.stored_provider_id` reads it —
    promoted column first, then the ``external_ids`` key — because a claim that
    used a narrower definition would overwrite an id stored only as JSON.
    """
    from .provider_ids import external_id_sql, normalize_provider_name

    entity = _entity(entity_type)
    table = _TABLES[entity]
    key = normalize_provider_name(service)
    if not key:
        raise ValueError(f"service is required: {service!r}")
    value = str(provider_id or "").strip()
    if not value:
        return False
    entity_id = int(entity_id)

    # json_extract/json_set both raise on a malformed column, and a row whose
    # external_ids somehow is not JSON must be claimable, not fatal.
    valid_json = ("CASE WHEN json_valid(COALESCE(NULLIF(external_ids, ''), '{}')) "
                  "THEN COALESCE(NULLIF(external_ids, ''), '{}') ELSE '{}' END")
    json_guard = f"COALESCE({external_id_sql(valid_json, key)}, '') = ''"
    promoted = _PROMOTED.get(key)
    if promoted and promoted not in _columns(conn, table):
        promoted = None
    promoted_guard = f" AND COALESCE({promoted}, '') = ''" if promoted else ""
    promoted_set = f", {promoted}=?" if promoted else ""
    params = [value]
    if promoted:
        params.append(value)
    params.append(entity_id)

    cursor = conn.execute(
        f"""UPDATE {table}
               SET external_ids=json_set({valid_json}, '$.{key}', ?){promoted_set},
                   updated_at=CURRENT_TIMESTAMP
             WHERE id=? AND {json_guard}{promoted_guard}""",
        params)
    return bool(cursor.rowcount)


__all__ = ["write_provider_enrichment", "claim_provider_id"]
