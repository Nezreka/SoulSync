"""Append-only external/legacy identifier history for Library v2.

Provider and legacy identifiers currently live on several compatibility and
ADR-04 shadow tables.  Import, refresh, and edition backfill write those fields
through different code paths, so application-level callbacks would inevitably
miss changes.  This module installs SQLite triggers at the shared persistence
boundary and records every assignment, replacement, removal, and entity delete.

The history deliberately has no foreign key to the entity tables: deleting or
re-importing an entity must not erase its old identifiers.  It is an audit/read
source, not a second identity resolver; current catalog columns remain the
write-side source of truth until the later Phase-3 read-projection cutover.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


LIB2_EXTERNAL_ID_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS lib2_external_id_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    namespace TEXT NOT NULL,
    event_type TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    change_source TEXT NOT NULL DEFAULT 'database_write',
    context_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(entity_type IN (
        'artist','release_group','track','release_edition','recording'
    )),
    CHECK(entity_id > 0),
    CHECK(event_type IN ('assigned','replaced','removed')),
    CHECK(old_value IS NOT NULL OR new_value IS NOT NULL)
)
"""


_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_lib2_external_id_history_entity "
    "ON lib2_external_id_history(entity_type, entity_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_external_id_history_lookup "
    "ON lib2_external_id_history(namespace, new_value, id)",
)


# (table, projected entity type, column, identifier namespace).  The tuple is
# intentionally exhaustive for today's scalar provider/legacy IDs plus the
# long-tail JSON object. Internal stable_id values are excluded: they are
# Library-v2-owned surrogate identities, not external/old IDs.
_IDENTITY_FIELDS = (
    ("lib2_artists", "artist", "spotify_id", "spotify"),
    ("lib2_artists", "artist", "musicbrainz_id", "musicbrainz"),
    ("lib2_artists", "artist", "legacy_artist_id", "legacy_artist"),
    ("lib2_artists", "artist", "external_ids", "external_ids_json"),
    ("lib2_albums", "release_group", "spotify_id", "spotify"),
    ("lib2_albums", "release_group", "musicbrainz_id", "musicbrainz"),
    ("lib2_albums", "release_group", "legacy_album_id", "legacy_album"),
    ("lib2_albums", "release_group", "external_ids", "external_ids_json"),
    ("lib2_tracks", "track", "isrc", "isrc"),
    ("lib2_tracks", "track", "musicbrainz_id", "musicbrainz"),
    ("lib2_tracks", "track", "spotify_id", "spotify"),
    ("lib2_tracks", "track", "legacy_track_id", "legacy_track"),
    ("lib2_release_editions", "release_edition", "spotify_id", "spotify"),
    ("lib2_release_editions", "release_edition", "musicbrainz_id", "musicbrainz"),
    ("lib2_release_editions", "release_edition", "external_ids", "external_ids_json"),
    ("lib2_recordings", "recording", "isrc", "isrc"),
    ("lib2_recordings", "recording", "musicbrainz_id", "musicbrainz"),
    ("lib2_recordings", "recording", "spotify_id", "spotify"),
)


@dataclass(frozen=True)
class ExternalIdHistoryEvent:
    id: int
    entity_type: str
    entity_id: int
    namespace: str
    event_type: str
    old_value: Optional[str]
    new_value: Optional[str]
    change_source: str
    context: Dict[str, Any]
    occurred_at: str


def _existing_columns(cursor: Any, table: str) -> set[str]:
    exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        return set()
    return {str(row[1]) for row in cursor.execute(f"PRAGMA table_info({table})")}


def _valid_value(alias: str, column: str) -> str:
    value = f"TRIM(CAST({alias}.{column} AS TEXT))"
    return f"({alias}.{column} IS NOT NULL AND {value} NOT IN ('', '{{}}', '[]', 'null'))"


def _normalized_value(alias: str, column: str) -> str:
    valid = _valid_value(alias, column)
    return f"CASE WHEN {valid} THEN CAST({alias}.{column} AS TEXT) END"


def _install_field_triggers(
    cursor: Any,
    *,
    table: str,
    entity_type: str,
    column: str,
    namespace: str,
) -> None:
    prefix = f"trg_lib2_idhist_{table.removeprefix('lib2_')}_{column}"
    new_valid = _valid_value("NEW", column)
    old_valid = _valid_value("OLD", column)
    new_value = _normalized_value("NEW", column)
    old_value = _normalized_value("OLD", column)

    for suffix in ("insert", "update", "delete"):
        cursor.execute(f"DROP TRIGGER IF EXISTS {prefix}_{suffix}")

    cursor.execute(f"""
        CREATE TRIGGER {prefix}_insert
        AFTER INSERT ON {table}
        FOR EACH ROW WHEN {new_valid}
        BEGIN
            INSERT INTO lib2_external_id_history(
                entity_type, entity_id, namespace, event_type,
                old_value, new_value, change_source)
            VALUES('{entity_type}', NEW.id, '{namespace}', 'assigned',
                   NULL, {new_value}, 'database_write');
        END
    """)
    cursor.execute(f"""
        CREATE TRIGGER {prefix}_update
        AFTER UPDATE OF {column} ON {table}
        FOR EACH ROW
        WHEN OLD.{column} IS NOT NEW.{column} AND ({old_valid} OR {new_valid})
        BEGIN
            INSERT INTO lib2_external_id_history(
                entity_type, entity_id, namespace, event_type,
                old_value, new_value, change_source)
            VALUES(
                '{entity_type}', NEW.id, '{namespace}',
                CASE
                    WHEN NOT {old_valid} THEN 'assigned'
                    WHEN NOT {new_valid} THEN 'removed'
                    ELSE 'replaced'
                END,
                {old_value}, {new_value}, 'database_write'
            );
        END
    """)
    cursor.execute(f"""
        CREATE TRIGGER {prefix}_delete
        BEFORE DELETE ON {table}
        FOR EACH ROW WHEN {old_valid}
        BEGIN
            INSERT INTO lib2_external_id_history(
                entity_type, entity_id, namespace, event_type,
                old_value, new_value, change_source)
            VALUES('{entity_type}', OLD.id, '{namespace}', 'removed',
                   {old_value}, NULL, 'entity_delete');
        END
    """)


def _backfill_field(
    cursor: Any,
    *,
    table: str,
    entity_type: str,
    column: str,
    namespace: str,
) -> int:
    valid = _valid_value("source", column)
    value = _normalized_value("source", column)
    cursor.execute(f"""
        INSERT INTO lib2_external_id_history(
            entity_type, entity_id, namespace, event_type,
            old_value, new_value, change_source)
        SELECT '{entity_type}', source.id, '{namespace}', 'assigned',
               NULL, {value}, 'schema_backfill'
          FROM {table} AS source
         WHERE {valid}
           AND NOT EXISTS (
               SELECT 1 FROM lib2_external_id_history history
                WHERE history.entity_type='{entity_type}'
                  AND history.entity_id=source.id
                  AND history.namespace='{namespace}'
                  AND history.new_value={value}
           )
    """)
    return int(cursor.rowcount)


def ensure_external_id_history_schema(cursor: Any) -> int:
    """Create guards/triggers and baseline current IDs. Returns rows backfilled."""
    cursor.execute(LIB2_EXTERNAL_ID_HISTORY_DDL)
    for index_sql in _INDEXES:
        cursor.execute(index_sql)
    cursor.execute("DROP TRIGGER IF EXISTS trg_lib2_external_id_history_no_update")
    cursor.execute("DROP TRIGGER IF EXISTS trg_lib2_external_id_history_no_delete")
    cursor.execute("""
        CREATE TRIGGER trg_lib2_external_id_history_no_update
        BEFORE UPDATE ON lib2_external_id_history
        BEGIN
            SELECT RAISE(ABORT, 'external id history is append-only');
        END
    """)
    cursor.execute("""
        CREATE TRIGGER trg_lib2_external_id_history_no_delete
        BEFORE DELETE ON lib2_external_id_history
        BEGIN
            SELECT RAISE(ABORT, 'external id history is append-only');
        END
    """)

    backfilled = 0
    columns_by_table: Dict[str, set[str]] = {}
    for table, entity_type, column, namespace in _IDENTITY_FIELDS:
        columns = columns_by_table.setdefault(
            table, _existing_columns(cursor, table)
        )
        if column not in columns:
            continue
        # Install triggers before backfill so all later writes are covered.
        _install_field_triggers(
            cursor,
            table=table,
            entity_type=entity_type,
            column=column,
            namespace=namespace,
        )
        backfilled += _backfill_field(
            cursor,
            table=table,
            entity_type=entity_type,
            column=column,
            namespace=namespace,
        )
    return backfilled


def _row_dict(cursor: Any, row: Any) -> Dict[str, Any]:
    if hasattr(row, "keys"):
        return dict(row)
    return {
        column[0]: value
        for column, value in zip(cursor.description, row, strict=True)
    }


def list_external_id_history(
    conn: Any,
    *,
    entity_type: str,
    entity_id: int,
    limit: int = 100,
) -> List[ExternalIdHistoryEvent]:
    """Newest-first immutable identity events for one local entity."""
    entity_type = str(entity_type or "").strip().lower()
    allowed = {field[1] for field in _IDENTITY_FIELDS}
    if entity_type not in allowed:
        raise ValueError(f"unsupported identity entity_type: {entity_type!r}")
    entity_id = int(entity_id)
    if entity_id <= 0:
        raise ValueError("entity_id must be positive")
    limit = int(limit)
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")
    cursor = conn.execute(
        """SELECT * FROM lib2_external_id_history
            WHERE entity_type=? AND entity_id=?
            ORDER BY id DESC LIMIT ?""",
        (entity_type, entity_id, limit),
    )
    events = []
    for row in cursor.fetchall():
        data = _row_dict(cursor, row)
        try:
            context = json.loads(data["context_json"] or "{}")
        except (TypeError, ValueError):
            context = {}
        events.append(ExternalIdHistoryEvent(
            id=int(data["id"]),
            entity_type=str(data["entity_type"]),
            entity_id=int(data["entity_id"]),
            namespace=str(data["namespace"]),
            event_type=str(data["event_type"]),
            old_value=data["old_value"],
            new_value=data["new_value"],
            change_source=str(data["change_source"]),
            context=context if isinstance(context, dict) else {},
            occurred_at=str(data["occurred_at"]),
        ))
    return events


__all__ = [
    "ExternalIdHistoryEvent",
    "LIB2_EXTERNAL_ID_HISTORY_DDL",
    "ensure_external_id_history_schema",
    "list_external_id_history",
]
