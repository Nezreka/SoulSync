"""Batch selection for enrichment workers, from lib2 (docs §32.3.1 stage 2).

All sixteen workers pick their next item by the same rules: unattempted artists,
then albums, then tracks, then failures whose retry window has expired, with an
optional pinned entity type served first (Manage Enrichment Workers). Legacy drove
that from ``<service>_match_status`` / ``<service>_last_attempted``; this drives it
from :mod:`core.library2.provider_attempts`.

Written once here rather than sixteen times, and it returns the exact dict shape
the workers already consume, so the change inside each one stays small.

Only ``not_found`` is retried. Legacy did the same, deliberately: an ``error``
means the provider or the network misbehaved, and auto-retrying that turns a
provider outage into an infinite loop. Errors clear on a user-triggered refresh.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from core.library2.provider_attempts import DEFAULT_RETRY_AFTER_DAYS

ENTITY_ORDER = ("artist", "album", "track")

# name/title plus the artist name the provider query needs, per entity type.
_SOURCES: Dict[str, str] = {
    "artist": """
        SELECT e.id AS id, e.name AS name, NULL AS artist_name
          FROM lib2_artists e
    """,
    "album": """
        SELECT e.id AS id, e.title AS name, ar.name AS artist_name
          FROM lib2_albums e
          JOIN lib2_artists ar ON ar.id = e.primary_artist_id
    """,
    "track": """
        SELECT e.id AS id, e.title AS name, ar.name AS artist_name
          FROM lib2_tracks e
          JOIN lib2_albums al ON al.id = e.album_id
          JOIN lib2_artists ar ON ar.id = al.primary_artist_id
    """,
}
_TABLES = {"artist": "lib2_artists", "album": "lib2_albums", "track": "lib2_tracks"}
_RETRYABLE = ("not_found",)
_RETRYABLE_SQL = ", ".join(f"'{status}'" for status in _RETRYABLE)


def _pending_sql(entity_type: str) -> str:
    """Unattempted first, then expired retryable failures, oldest attempt first."""
    return f"""
        {_SOURCES[entity_type]}
          LEFT JOIN lib2_provider_attempts a
                 ON a.entity_type = :entity AND a.entity_id = e.id
                AND a.service = :service
         WHERE a.entity_id IS NULL
            OR (a.status IN ({_RETRYABLE_SQL})
                AND a.last_attempted_at <= datetime('now', :window))
         ORDER BY a.last_attempted_at IS NOT NULL, a.last_attempted_at, e.id
    """


def _fetch(conn, entity_type: str, service: str, retry_after_days: int) -> Optional[Any]:
    return conn.execute(
        _pending_sql(entity_type) + " LIMIT 1",
        {"entity": entity_type, "service": str(service).strip().lower(),
         "window": f"-{max(0, int(retry_after_days))} days"},
    ).fetchone()


def next_pending(
    conn, service: str, *,
    retry_after_days: int = DEFAULT_RETRY_AFTER_DAYS,
    pinned: Optional[str] = None,
    type_overrides: Optional[Mapping[str, str]] = None,
    entity_types: tuple = ENTITY_ORDER,
) -> Optional[Dict[str, Any]]:
    """The next item this provider should look at, or None when nothing is due.

    ``pinned`` puts one entity type at the front and then falls through to the
    normal order once it is exhausted — unset or exhausted behaves exactly like
    the default artist→album→track chain.
    """
    overrides = dict(type_overrides or {})
    order = list(entity_types)
    if pinned in order:
        order.remove(pinned)
        order.insert(0, pinned)
    for entity_type in order:
        row = _fetch(conn, entity_type, service, retry_after_days)
        if row is None:
            continue
        item: Dict[str, Any] = {
            "type": overrides.get(entity_type, entity_type),
            "id": int(row["id"]),
            "name": row["name"],
        }
        if row["artist_name"] is not None:
            item["artist"] = row["artist_name"]
        return item
    return None


def pending_count(conn, service: str, *,
                  retry_after_days: int = DEFAULT_RETRY_AFTER_DAYS,
                  entity_types: tuple = ENTITY_ORDER) -> int:
    """How many items still need this provider looked at."""
    total = 0
    for entity_type in entity_types:
        total += int(conn.execute(
            f"SELECT COUNT(*) FROM ({_pending_sql(entity_type)})",
            {"entity": entity_type, "service": str(service).strip().lower(),
             "window": f"-{max(0, int(retry_after_days))} days"},
        ).fetchone()[0])
    return total


def progress_breakdown(conn, service: str, *,
                       entity_types: tuple = ENTITY_ORDER) -> Dict[str, Dict[str, int]]:
    """Per-entity-type progress, keyed the way the UI already expects.

    Any recorded attempt counts as progress, including ``not_found``: legacy
    counted every non-NULL ``match_status`` the same way, and a bar that only
    counted successes would never reach 100% on a library with obscure releases.
    """
    out: Dict[str, Dict[str, int]] = {}
    key = str(service).strip().lower()
    for entity_type in entity_types:
        row = conn.execute(
            f"""
            SELECT (SELECT COUNT(*) FROM {_TABLES[entity_type]}) AS total,
                   (SELECT COUNT(*) FROM lib2_provider_attempts
                     WHERE entity_type=:entity AND service=:service) AS processed
            """,
            {"entity": entity_type, "service": key},
        ).fetchone()
        total = int(row["total"] or 0)
        processed = min(int(row["processed"] or 0), total)
        out[f"{entity_type}s"] = {
            "matched": processed,
            "total": total,
            "percent": int((processed / total * 100) if total else 0),
        }
    return out


__all__ = ["ENTITY_ORDER", "next_pending", "pending_count", "progress_breakdown"]
