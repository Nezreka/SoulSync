"""Per-provider enrichment bookkeeping for Library-v2 entities.

The piece the sixteen enrichment workers need before any of them can leave the
legacy tables (docs §32.3.1 stage 2). A worker does not enrich everything on
every cycle: it picks a batch, and legacy drives that pick from two columns per
provider — ``<service>_match_status`` and ``<service>_last_attempted``. Without
an equivalent, a worker reading lib2 cannot tell "never tried" from "tried on
Tuesday and the provider has never heard of them", so it would re-ask every
provider about every entity, forever.

**Why a ledger and not column pairs.** The legacy shape needs two columns per
provider per table: adding Bandcamp meant three ``ALTER TABLE``s, and its 26
bookkeeping columns on ``artists`` alone are most of the reason that table has
63. A row per (entity, service) takes the fourteenth provider without touching
the schema, and lets a worker's batch query be one index lookup.

**Deliberately not mirrored from legacy by the trigger.** The mirror's own
contract is that ordinary bookkeeping traffic enqueues nothing, and
``*_last_attempted`` is written on every provider call — watching it would queue
the entire library on every enrichment cycle. The existing history is seeded once
by :func:`backfill_from_legacy` instead.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.provider_attempts")

PROVIDER_ATTEMPTS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_provider_attempts (
    entity_type TEXT NOT NULL,                 -- 'artist' | 'album' | 'track'
    entity_id INTEGER NOT NULL,
    service TEXT NOT NULL,                     -- a core.library2.match_status SERVICES key
    status TEXT NOT NULL,                      -- 'matched' | 'not_found' | 'error' | 'skipped'
    attempts INTEGER NOT NULL DEFAULT 1,       -- consecutive tries since the last success
    last_attempted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    detail TEXT,
    PRIMARY KEY (entity_type, entity_id, service)
)
"""

# The batch query filters on (entity_type, service) and orders by age. No
# partial predicate: a partial index with `<> ''` cannot serve a parameterised
# equality lookup at all (iss32-M08), and this index has to serve every worker's
# hot path.
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_lib2_provider_attempts_due "
    "ON lib2_provider_attempts(entity_type, service, status, last_attempted_at)",
)

_TABLES = {"artist": "lib2_artists", "album": "lib2_albums", "track": "lib2_tracks"}
_LEGACY = {
    "artist": ("artists", "legacy_artist_id"),
    "album": ("albums", "legacy_album_id"),
    "track": ("tracks", "legacy_track_id"),
}

# A miss is worth retrying eventually — providers add catalogue — but not on the
# next cycle. Matches the legacy workers' own refresh thinking.
DEFAULT_RETRY_AFTER_DAYS = 30

STATUSES = frozenset({"matched", "not_found", "error", "skipped"})
# Statuses that mean "settled — do not come back for this one". Rendered into SQL
# from this one constant so the batch query and the attempt-counter reset cannot
# disagree about what counts as success.
_SETTLED = ("matched",)
_SETTLED_SQL = ", ".join(f"'{status}'" for status in _SETTLED)


# Workers that keep attempt bookkeeping without being a provider identity.
# ``match_status.SERVICES`` lists sources whose id lands on the entity; these have
# no id of their own — Similar Artists stores rows keyed by the id some OTHER
# source already matched — but they still need a due/attempted record, which is
# what this ledger is.
DERIVED_SERVICES: frozenset = frozenset({"similar_artists", "listening_stats"})


def _services() -> set:
    from core.library2.match_status import SERVICES

    return {service for service, _label, _ids in SERVICES} | set(DERIVED_SERVICES)


def _entity_type(value: Any) -> str:
    text = str(value or "").rstrip("s")
    if text not in _TABLES:
        raise ValueError(f"Unknown entity type: {value!r}")
    return text


def _service(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text not in _services():
        # A typo would create a ledger row no worker reads, leaving the entity
        # permanently "never attempted" while looking recorded.
        raise ValueError(f"Unknown provider service: {value!r}")
    return text


def _table_exists(conn, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone() is not None


def ensure_provider_attempt_schema(cursor: Any) -> None:
    cursor.execute(PROVIDER_ATTEMPTS_DDL)
    for statement in _INDEXES:
        cursor.execute(statement)


def record_attempt(conn, *, entity_type: str, entity_id: int, service: str,
                   status: str, detail: Optional[str] = None,
                   attempted_at: Optional[str] = None) -> None:
    """Note that ``service`` was asked about this entity, and what came back.

    ``attempts`` counts *consecutive* tries and resets on success: it exists to
    back off on repeated failure, and carrying it past a success would keep
    backing off a provider that has just answered.
    """
    entity = _entity_type(entity_type)
    key = _service(service)
    state = str(status or "").strip().lower()
    if state not in STATUSES:
        raise ValueError(f"Unknown attempt status: {status!r}")
    when = attempted_at or None
    conn.execute(
        """
        INSERT INTO lib2_provider_attempts
               (entity_type, entity_id, service, status, attempts,
                last_attempted_at, detail)
        VALUES (?, ?, ?, ?, 1, COALESCE(?, CURRENT_TIMESTAMP), ?)
        ON CONFLICT(entity_type, entity_id, service) DO UPDATE SET
            status = excluded.status,
            attempts = CASE WHEN excluded.status IN (""" + _SETTLED_SQL + """) THEN 1
                            ELSE lib2_provider_attempts.attempts + 1 END,
            last_attempted_at = excluded.last_attempted_at,
            detail = excluded.detail
        """,
        (entity, int(entity_id), key, state, when, detail),
    )


def attempt_state(conn, *, entity_type: str, entity_id: int) -> Dict[str, Dict[str, Any]]:
    """Everything recorded about one entity, keyed by service."""
    entity = _entity_type(entity_type)
    if not _table_exists(conn, "lib2_provider_attempts"):
        return {}
    return {
        str(row["service"]): {
            "status": row["status"],
            "attempts": int(row["attempts"]),
            "last_attempted_at": str(row["last_attempted_at"]),
            "detail": row["detail"],
        }
        for row in conn.execute(
            "SELECT service, status, attempts, last_attempted_at, detail "
            "FROM lib2_provider_attempts WHERE entity_type=? AND entity_id=?",
            (entity, int(entity_id)))
    }


def due_entities(conn, *, entity_type: str, service: str, limit: int = 200,
                 retry_after_days: int = DEFAULT_RETRY_AFTER_DAYS) -> List[int]:
    """The next entities this provider should be asked about.

    Never-attempted rows first, then failures whose retry window has expired.
    Ordering by ``last_attempted_at`` with NULLs first is what makes consecutive
    runs make progress instead of re-picking the same batch.
    """
    entity = _entity_type(entity_type)
    key = _service(service)
    table = _TABLES[entity]
    if not _table_exists(conn, "lib2_provider_attempts"):
        # An install whose bookkeeping has not been created yet has attempted
        # nothing; a worker must not stall on that.
        rows = conn.execute(
            f"SELECT id FROM {table} ORDER BY id LIMIT ?", (int(limit),))
        return [int(row["id"]) for row in rows]
    rows = conn.execute(
        f"""
        SELECT e.id AS id
          FROM {table} e
          LEFT JOIN lib2_provider_attempts a
                 ON a.entity_type = :entity AND a.entity_id = e.id
                AND a.service = :service
         WHERE a.entity_id IS NULL
            OR (a.status NOT IN ({_SETTLED_SQL})
                AND a.last_attempted_at <= datetime('now', :window))
         ORDER BY a.last_attempted_at IS NOT NULL, a.last_attempted_at, e.id
         LIMIT :limit
        """,
        {"entity": entity, "service": key, "limit": max(1, int(limit)),
         "window": f"-{max(0, int(retry_after_days))} days"},
    )
    return [int(row["id"]) for row in rows]


def backfill_from_legacy(conn, *, limit: int = 100000) -> Dict[str, int]:
    """Seed the ledger once from the legacy per-provider columns.

    Switching a worker to lib2 must not make it re-ask every provider about the
    whole library, so the history legacy already holds is carried over. Never
    overwrites an existing ledger row — anything lib2 recorded itself is newer by
    construction — which also makes this safe to run on every start.
    """
    from core.library2.match_status import SERVICES

    stats = {"seeded": 0, "scanned": 0}
    if not _table_exists(conn, "lib2_provider_attempts"):
        ensure_provider_attempt_schema(conn.cursor())
    for entity, (legacy_table, link_column) in _LEGACY.items():
        if not _table_exists(conn, legacy_table):
            continue
        legacy_columns = {
            str(row[1]) for row in conn.execute(f"PRAGMA table_info({legacy_table})")
        }
        pairs = [
            (service, f"{service}_match_status", f"{service}_last_attempted")
            for service, _label, ids in SERVICES if ids.get(entity)
        ]
        pairs = [
            (service, status_column, time_column)
            for service, status_column, time_column in pairs
            if status_column in legacy_columns
        ]
        if not pairs:
            continue
        selected = ", ".join(
            f"l.{status}, l.{stamp}" if stamp in legacy_columns else f"l.{status}"
            for _service, status, stamp in pairs)
        rows = conn.execute(
            f"""SELECT v.id AS lib2_id, {selected}
                  FROM {_TABLES[entity]} v
                  JOIN {legacy_table} l ON l.id = v.{link_column}
                 WHERE v.{link_column} IS NOT NULL
                 LIMIT ?""", (int(limit),)).fetchall()
        for row in rows:
            stats["scanned"] += 1
            for service, status_column, time_column in pairs:
                raw = row[status_column] if status_column in row.keys() else None
                status = str(raw or "").strip().lower()
                if status not in STATUSES:
                    continue
                stamp = row[time_column] if time_column in row.keys() else None
                inserted = conn.execute(
                    """
                    INSERT OR IGNORE INTO lib2_provider_attempts
                           (entity_type, entity_id, service, status, attempts,
                            last_attempted_at)
                    VALUES (?, ?, ?, ?, 1, COALESCE(?, CURRENT_TIMESTAMP))
                    """,
                    (entity, int(row["lib2_id"]), service, status, stamp),
                ).rowcount
                stats["seeded"] += max(0, int(inserted or 0))
    if stats["seeded"]:
        logger.info("Seeded %s provider-attempt row(s) from legacy", stats["seeded"])
    return stats


__all__ = [
    "DEFAULT_RETRY_AFTER_DAYS",
    "PROVIDER_ATTEMPTS_DDL",
    "STATUSES",
    "attempt_state",
    "backfill_from_legacy",
    "due_entities",
    "ensure_provider_attempt_schema",
    "record_attempt",
]
