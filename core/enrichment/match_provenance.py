"""Normalized provenance for metadata-provider identity matches.

Legacy enrichment workers write provider ids and match status directly on the
``artists``/``albums``/``tracks`` tables. Database triggers mirror those writes
into ``metadata_match_provenance`` as automatic matches. User-confirmed writes
call :func:`record_manual_match` in the same transaction to replace that origin
with an explicit manual audit record.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable


def record_manual_match(
    conn,
    *,
    entity_type: str,
    entity_id: int,
    service: str,
    external_id: str,
    actor: str = "admin",
) -> None:
    """Mark the current provider identity as explicitly chosen by a user."""
    conn.execute(
        """INSERT INTO metadata_match_provenance(
               entity_type, entity_id, service, origin, external_id,
               matched_at, actor)
           VALUES(?, ?, ?, 'manual', ?, CURRENT_TIMESTAMP, ?)
           ON CONFLICT(entity_type, entity_id, service) DO UPDATE SET
               origin='manual', external_id=excluded.external_id,
               matched_at=CURRENT_TIMESTAMP, actor=excluded.actor""",
        (str(entity_type), int(entity_id), str(service), str(external_id), str(actor)),
    )


def load_match_provenance(
    conn,
    entity_type: str,
    entity_ids: Iterable[int],
) -> Dict[int, Dict[str, Dict[str, Any]]]:
    """Return ``entity id -> service -> provenance`` without requiring schema.

    Minimal/older test databases and installations opened before migrations may
    not have the table yet. Match-status reads must still work, so absence is a
    graceful empty mapping rather than an error.
    """
    ids = sorted({int(entity_id) for entity_id in entity_ids})
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    try:
        rows = conn.execute(
            f"""SELECT entity_id, service, origin, external_id, matched_at, actor
                  FROM metadata_match_provenance
                 WHERE entity_type=? AND entity_id IN ({marks})""",
            (str(entity_type), *ids),
        ).fetchall()
    except Exception:  # table is additive and optional for compatibility reads
        return {}

    out: Dict[int, Dict[str, Dict[str, Any]]] = {}
    for row in rows:
        payload = {
            "origin": row["origin"],
            "external_id": row["external_id"],
            "matched_at": row["matched_at"],
            "actor": row["actor"],
        }
        out.setdefault(int(row["entity_id"]), {})[str(row["service"])] = payload
    return out


__all__ = ["load_match_provenance", "record_manual_match"]
