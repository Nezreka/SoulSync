"""Append-only merge/move/link history for Library v2 entities.

The current Manage-Tracks and ADR-04 compatibility paths mutate relationships
in place.  Without a journal, a canonical re-link or file/recording move loses
where it came from.  SQLite triggers cover those existing mutation paths at
the shared DB boundary; explicit helpers cover future atomic merge/move
commands without making this module an identity or file-processing engine.

Only local entity types/ids and a tiny whitelisted context are stored.  File
paths, titles, provider payloads, and client identifiers never enter this log.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional


LIB2_ENTITY_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS lib2_entity_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id INTEGER NOT NULL,
    from_entity_type TEXT,
    from_entity_id INTEGER,
    to_entity_type TEXT,
    to_entity_id INTEGER,
    change_source TEXT NOT NULL DEFAULT 'database_write',
    context_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(event_type IN (
        'canonical_linked','canonical_unlinked','canonical_relinked',
        'file_moved','recording_moved','release_track_moved',
        'entity_merged','entity_moved'
    )),
    CHECK(subject_type IN (
        'artist','release_group','track','track_file',
        'release_edition','recording','release_track'
    )),
    CHECK(subject_id > 0),
    CHECK(from_entity_type IS NOT NULL OR to_entity_type IS NOT NULL),
    CHECK((from_entity_type IS NULL) = (from_entity_id IS NULL)),
    CHECK((to_entity_type IS NULL) = (to_entity_id IS NULL)),
    CHECK(from_entity_id IS NULL OR from_entity_id > 0),
    CHECK(to_entity_id IS NULL OR to_entity_id > 0)
)
"""


_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_lib2_entity_history_subject "
    "ON lib2_entity_history(subject_type, subject_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_entity_history_from "
    "ON lib2_entity_history(from_entity_type, from_entity_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_entity_history_to "
    "ON lib2_entity_history(to_entity_type, to_entity_id, id)",
)

_ENTITY_TYPES = frozenset({
    "artist",
    "release_group",
    "track",
    "track_file",
    "release_edition",
    "recording",
    "release_track",
})
_PUBLIC_COMMAND_TYPES = frozenset({
    "artist", "release_group", "track", "release_edition", "recording"
})
_CONTEXT_KEYS = frozenset({"reason", "command", "correlation_id"})


@dataclass(frozen=True)
class EntityHistoryEvent:
    id: int
    event_type: str
    subject_type: str
    subject_id: int
    from_entity_type: Optional[str]
    from_entity_id: Optional[int]
    to_entity_type: Optional[str]
    to_entity_id: Optional[int]
    change_source: str
    context: Dict[str, Any]
    occurred_at: str


def _table_exists(cursor: Any, table: str) -> bool:
    return cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _install_canonical_triggers(cursor: Any) -> None:
    if not _table_exists(cursor, "lib2_tracks"):
        return
    for suffix in ("insert", "update"):
        cursor.execute(
            f"DROP TRIGGER IF EXISTS trg_lib2_entity_history_canonical_{suffix}"
        )
    cursor.execute("""
        CREATE TRIGGER trg_lib2_entity_history_canonical_insert
        AFTER INSERT ON lib2_tracks
        FOR EACH ROW WHEN NEW.canonical_track_id IS NOT NULL
        BEGIN
            INSERT INTO lib2_entity_history(
                event_type, subject_type, subject_id,
                to_entity_type, to_entity_id, change_source)
            VALUES('canonical_linked', 'track', NEW.id,
                   'track', NEW.canonical_track_id, 'database_write');
        END
    """)
    cursor.execute("""
        CREATE TRIGGER trg_lib2_entity_history_canonical_update
        AFTER UPDATE OF canonical_track_id ON lib2_tracks
        FOR EACH ROW WHEN OLD.canonical_track_id IS NOT NEW.canonical_track_id
        BEGIN
            INSERT INTO lib2_entity_history(
                event_type, subject_type, subject_id,
                from_entity_type, from_entity_id,
                to_entity_type, to_entity_id, change_source)
            VALUES(
                CASE
                    WHEN OLD.canonical_track_id IS NULL THEN 'canonical_linked'
                    WHEN NEW.canonical_track_id IS NULL THEN 'canonical_unlinked'
                    ELSE 'canonical_relinked'
                END,
                'track', NEW.id,
                CASE WHEN OLD.canonical_track_id IS NOT NULL THEN 'track' END,
                OLD.canonical_track_id,
                CASE WHEN NEW.canonical_track_id IS NOT NULL THEN 'track' END,
                NEW.canonical_track_id,
                'database_write'
            );
        END
    """)


def _install_move_trigger(
    cursor: Any,
    *,
    table: str,
    column: str,
    trigger_name: str,
    event_type: str,
    subject_type: str,
    related_type: str,
) -> None:
    if not _table_exists(cursor, table):
        return
    columns = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        return
    cursor.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    cursor.execute(f"""
        CREATE TRIGGER {trigger_name}
        AFTER UPDATE OF {column} ON {table}
        FOR EACH ROW
        WHEN OLD.{column} IS NOT NEW.{column}
         AND OLD.{column} IS NOT NULL
         AND NEW.{column} IS NOT NULL
        BEGIN
            INSERT INTO lib2_entity_history(
                event_type, subject_type, subject_id,
                from_entity_type, from_entity_id,
                to_entity_type, to_entity_id, change_source)
            VALUES('{event_type}', '{subject_type}', NEW.id,
                   '{related_type}', OLD.{column},
                   '{related_type}', NEW.{column}, 'database_write');
        END
    """)


def _backfill_canonical_links(cursor: Any) -> int:
    if not _table_exists(cursor, "lib2_tracks"):
        return 0
    cursor.execute("""
        INSERT INTO lib2_entity_history(
            event_type, subject_type, subject_id,
            to_entity_type, to_entity_id, change_source)
        SELECT 'canonical_linked', 'track', track.id,
               'track', track.canonical_track_id, 'schema_backfill'
          FROM lib2_tracks track
         WHERE track.canonical_track_id IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM lib2_entity_history history
                WHERE history.subject_type='track'
                  AND history.subject_id=track.id
                  AND history.event_type IN (
                      'canonical_linked','canonical_relinked'
                  )
                  AND history.to_entity_type='track'
                  AND history.to_entity_id=track.canonical_track_id
           )
    """)
    return int(cursor.rowcount)


def ensure_entity_history_schema(cursor: Any) -> int:
    """Create immutable history + relationship triggers. Returns backfill count."""
    cursor.execute(LIB2_ENTITY_HISTORY_DDL)
    for index_sql in _INDEXES:
        cursor.execute(index_sql)
    cursor.execute("DROP TRIGGER IF EXISTS trg_lib2_entity_history_no_update")
    cursor.execute("DROP TRIGGER IF EXISTS trg_lib2_entity_history_no_delete")
    cursor.execute("""
        CREATE TRIGGER trg_lib2_entity_history_no_update
        BEFORE UPDATE ON lib2_entity_history
        BEGIN
            SELECT RAISE(ABORT, 'entity history is append-only');
        END
    """)
    cursor.execute("""
        CREATE TRIGGER trg_lib2_entity_history_no_delete
        BEFORE DELETE ON lib2_entity_history
        BEGIN
            SELECT RAISE(ABORT, 'entity history is append-only');
        END
    """)
    _install_canonical_triggers(cursor)
    _install_move_trigger(
        cursor,
        table="lib2_track_files",
        column="track_id",
        trigger_name="trg_lib2_entity_history_file_move",
        event_type="file_moved",
        subject_type="track_file",
        related_type="track",
    )
    _install_move_trigger(
        cursor,
        table="lib2_release_tracks",
        column="recording_id",
        trigger_name="trg_lib2_entity_history_recording_move",
        event_type="recording_moved",
        subject_type="release_track",
        related_type="recording",
    )
    _install_move_trigger(
        cursor,
        table="lib2_release_tracks",
        column="release_edition_id",
        trigger_name="trg_lib2_entity_history_release_track_move",
        event_type="release_track_moved",
        subject_type="release_track",
        related_type="release_edition",
    )
    return _backfill_canonical_links(cursor)


def _entity_type(value: Any, *, public_command: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    allowed = _PUBLIC_COMMAND_TYPES if public_command else _ENTITY_TYPES
    if normalized not in allowed:
        raise ValueError(f"unsupported entity type: {normalized!r}")
    return normalized


def _entity_id(value: Any, field: str) -> int:
    normalized = int(value)
    if normalized <= 0:
        raise ValueError(f"{field} must be positive")
    return normalized


def _redacted_context(context: Optional[Mapping[str, Any]]) -> str:
    clean: Dict[str, str] = {}
    for key, value in dict(context or {}).items():
        if key not in _CONTEXT_KEYS or value is None:
            continue
        text = str(value).strip()
        if text:
            clean[key] = text[:200]
    return json.dumps(clean, sort_keys=True, separators=(",", ":"))


def _record_command(
    conn: Any,
    *,
    event_type: str,
    source_type: str,
    source_id: int,
    target_type: str,
    target_id: int,
    change_source: str,
    context: Optional[Mapping[str, Any]],
) -> int:
    ensure_entity_history_schema(conn.cursor())
    source_type = _entity_type(source_type, public_command=True)
    target_type = _entity_type(target_type, public_command=True)
    source_id = _entity_id(source_id, "source_id")
    target_id = _entity_id(target_id, "target_id")
    if source_type == target_type and source_id == target_id:
        raise ValueError("source and target entity are identical")
    change_source = str(change_source or "").strip()
    if not change_source:
        raise ValueError("change_source is required")
    cursor = conn.execute(
        """INSERT INTO lib2_entity_history(
               event_type, subject_type, subject_id,
               from_entity_type, from_entity_id,
               to_entity_type, to_entity_id, change_source, context_json)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            event_type,
            source_type,
            source_id,
            source_type,
            source_id,
            target_type,
            target_id,
            change_source[:100],
            _redacted_context(context),
        ),
    )
    return int(cursor.lastrowid)


def record_entity_merge(
    conn: Any,
    *,
    source_type: str,
    source_id: int,
    target_type: str,
    target_id: int,
    change_source: str = "user_command",
    context: Optional[Mapping[str, Any]] = None,
) -> int:
    """Journal a merge in the caller's transaction; never performs the merge."""
    return _record_command(
        conn,
        event_type="entity_merged",
        source_type=source_type,
        source_id=source_id,
        target_type=target_type,
        target_id=target_id,
        change_source=change_source,
        context=context,
    )


def record_entity_move(
    conn: Any,
    *,
    source_type: str,
    source_id: int,
    target_type: str,
    target_id: int,
    change_source: str = "user_command",
    context: Optional[Mapping[str, Any]] = None,
) -> int:
    """Journal a catalog move in the caller's transaction; never mutates it."""
    return _record_command(
        conn,
        event_type="entity_moved",
        source_type=source_type,
        source_id=source_id,
        target_type=target_type,
        target_id=target_id,
        change_source=change_source,
        context=context,
    )


def _row_dict(cursor: Any, row: Any) -> Dict[str, Any]:
    if hasattr(row, "keys"):
        return dict(row)
    return {
        column[0]: value
        for column, value in zip(cursor.description, row, strict=True)
    }


def list_entity_history(
    conn: Any,
    *,
    entity_type: str,
    entity_id: int,
    limit: int = 100,
) -> List[EntityHistoryEvent]:
    """Events where the entity is subject, source, or target, newest first."""
    entity_type = _entity_type(entity_type)
    entity_id = _entity_id(entity_id, "entity_id")
    limit = int(limit)
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")
    cursor = conn.execute(
        """SELECT * FROM lib2_entity_history
            WHERE (subject_type=? AND subject_id=?)
               OR (from_entity_type=? AND from_entity_id=?)
               OR (to_entity_type=? AND to_entity_id=?)
            ORDER BY id DESC LIMIT ?""",
        (entity_type, entity_id, entity_type, entity_id,
         entity_type, entity_id, limit),
    )
    events = []
    for row in cursor.fetchall():
        data = _row_dict(cursor, row)
        try:
            context = json.loads(data["context_json"] or "{}")
        except (TypeError, ValueError):
            context = {}
        events.append(EntityHistoryEvent(
            id=int(data["id"]),
            event_type=str(data["event_type"]),
            subject_type=str(data["subject_type"]),
            subject_id=int(data["subject_id"]),
            from_entity_type=data["from_entity_type"],
            from_entity_id=(int(data["from_entity_id"])
                            if data["from_entity_id"] is not None else None),
            to_entity_type=data["to_entity_type"],
            to_entity_id=(int(data["to_entity_id"])
                          if data["to_entity_id"] is not None else None),
            change_source=str(data["change_source"]),
            context=context if isinstance(context, dict) else {},
            occurred_at=str(data["occurred_at"]),
        ))
    return events


__all__ = [
    "EntityHistoryEvent",
    "LIB2_ENTITY_HISTORY_DDL",
    "ensure_entity_history_schema",
    "list_entity_history",
    "record_entity_merge",
    "record_entity_move",
]
