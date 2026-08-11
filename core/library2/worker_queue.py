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
_STATUSES = frozenset({"matched", "not_found", "error", "skipped"})

# "This entity is matched to some metadata source." lib2 keeps Spotify and
# MusicBrainz in promoted columns and everything else in external_ids, so all
# three have to be consulted.
_HAS_PROVIDER_ID = (
    "COALESCE(e.spotify_id,'') <> '' OR COALESCE(e.musicbrainz_id,'') <> '' "
    "OR COALESCE(e.external_ids,'{}') NOT IN ('', '{}')"
)


def _retryable_sql(retry_statuses: tuple) -> str:
    """Whitelisted against the ledger's own status vocabulary, because it is
    interpolated rather than bound — SQLite cannot parameterize an IN list."""
    wanted = [str(status).strip().lower() for status in retry_statuses]
    unknown = [status for status in wanted if status not in _STATUSES]
    if unknown:
        raise ValueError(f"Unknown attempt status(es): {sorted(unknown)}")
    return ", ".join(f"'{status}'" for status in wanted) or "''"


def _pending_sql(entity_type: str, retry_statuses: tuple = _RETRYABLE,
                 require_provider_id: bool = False) -> str:
    """Unattempted first, then expired retryable failures, oldest attempt first."""
    universe = f"AND ({_HAS_PROVIDER_ID})" if require_provider_id else ""
    return f"""
        {_SOURCES[entity_type]}
          LEFT JOIN lib2_provider_attempts a
                 ON a.entity_type = :entity AND a.entity_id = e.id
                AND a.service = :service
         WHERE (a.entity_id IS NULL
                OR (a.status IN ({_retryable_sql(retry_statuses)})
                    AND a.last_attempted_at <= datetime('now', :window)))
           {universe}
         ORDER BY a.last_attempted_at IS NOT NULL, a.last_attempted_at, e.id
    """


def _fetch(conn, entity_type: str, service: str, retry_after_days: int,
           retry_statuses: tuple = _RETRYABLE,
           require_provider_id: bool = False) -> Optional[Any]:
    return conn.execute(
        _pending_sql(entity_type, retry_statuses, require_provider_id) + " LIMIT 1",
        {"entity": entity_type, "service": str(service).strip().lower(),
         "window": f"-{max(0, int(retry_after_days))} days"},
    ).fetchone()


def next_pending(
    conn, service: str, *,
    retry_after_days: int = DEFAULT_RETRY_AFTER_DAYS,
    pinned: Optional[str] = None,
    type_overrides: Optional[Mapping[str, str]] = None,
    entity_types: tuple = ENTITY_ORDER,
    retry_statuses: tuple = _RETRYABLE,
    require_provider_id: bool = False,
) -> Optional[Dict[str, Any]]:
    """The next item this provider should look at, or None when nothing is due.

    ``pinned`` puts one entity type at the front and then falls through to the
    normal order once it is exhausted — unset or exhausted behaves exactly like
    the default artist→album→track chain.

    ``retry_statuses`` defaults to ``not_found`` only. Widen it to include
    ``error`` only where the provider's errors really are transient and its own
    fetch already sorts a definitive miss into ``not_found`` — Similar Artists is
    the case. ``require_provider_id`` narrows the universe to entities already
    matched to a metadata source, for work that is keyed by that id and has
    nothing to do without one.
    """
    overrides = dict(type_overrides or {})
    order = list(entity_types)
    if pinned in order:
        order.remove(pinned)
        order.insert(0, pinned)
    for entity_type in order:
        row = _fetch(conn, entity_type, service, retry_after_days,
                     retry_statuses, require_provider_id)
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
                  entity_types: tuple = ENTITY_ORDER,
                  retry_statuses: tuple = _RETRYABLE,
                  require_provider_id: bool = False) -> int:
    """How many items still need this provider looked at."""
    total = 0
    for entity_type in entity_types:
        total += int(conn.execute(
            "SELECT COUNT(*) FROM ("
            + _pending_sql(entity_type, retry_statuses, require_provider_id) + ")",
            {"entity": entity_type, "service": str(service).strip().lower(),
             "window": f"-{max(0, int(retry_after_days))} days"},
        ).fetchone()[0])
    return total


def status_counts(conn, service: str, entity_type: str, *,
                  require_provider_id: bool = False) -> Dict[str, int]:
    """Persistent tallies over one entity type: each outcome, plus never-attempted.

    ``total`` counts the same population the queue picks from, so the two agree —
    a tally over a wider universe than the selection would show a percentage that
    never reaches 100.
    """
    universe = f"WHERE {_HAS_PROVIDER_ID}" if require_provider_id else ""
    row = conn.execute(
        f"""
        SELECT
            SUM(CASE WHEN a.status='matched'   THEN 1 ELSE 0 END) AS matched,
            SUM(CASE WHEN a.status='not_found' THEN 1 ELSE 0 END) AS not_found,
            SUM(CASE WHEN a.status='error'     THEN 1 ELSE 0 END) AS error,
            SUM(CASE WHEN a.entity_id IS NULL  THEN 1 ELSE 0 END) AS pending,
            COUNT(*) AS total
          FROM (SELECT e.id, e.spotify_id, e.musicbrainz_id, e.external_ids
                  FROM {_TABLES[entity_type]} e {universe}) e
          LEFT JOIN lib2_provider_attempts a
                 ON a.entity_type=:entity AND a.entity_id=e.id AND a.service=:service
        """,
        {"entity": entity_type, "service": str(service).strip().lower()},
    ).fetchone()
    return {key: int(row[key] or 0)
            for key in ("matched", "not_found", "error", "pending", "total")}


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


__all__ = ["ENTITY_ORDER", "next_pending", "pending_count", "progress_breakdown",
           "status_counts"]
