"""Persistent, server-scoped recognition mappings for Library v2.

Media servers observe an imported catalogue; they do not own it.  A separate
mapping row lets the same artist/release/track be recognised by Plex,
Jellyfin, and Navidrome at the same time without one scan overwriting another.
The old ``server_source``/``server_id`` entity columns remain a compatibility
projection while older call sites and existing databases converge.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


MEDIA_SERVER_SOURCES = frozenset({"plex", "jellyfin", "navidrome"})
ENTITY_TABLES = {
    "artist": "lib2_artists",
    "album": "lib2_albums",
    "track": "lib2_tracks",
}


DDL = """
CREATE TABLE IF NOT EXISTS lib2_media_server_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('artist','album','track')),
    entity_id INTEGER NOT NULL,
    server_source TEXT NOT NULL,
    server_id TEXT NOT NULL,
    match_status TEXT NOT NULL DEFAULT 'recognized',
    first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, entity_id, server_source),
    UNIQUE(entity_type, server_source, server_id)
)
"""


def ensure_media_mapping_schema(cursor: Any) -> None:
    cursor.execute(DDL)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib2_media_mappings_entity "
        "ON lib2_media_server_mappings(entity_type, entity_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib2_media_mappings_server "
        "ON lib2_media_server_mappings(server_source, entity_type, server_id)"
    )
    for entity_type, table in ENTITY_TABLES.items():
        cursor.execute(
            f"""CREATE TRIGGER IF NOT EXISTS trg_{table}_delete_media_mappings
                AFTER DELETE ON {table}
                BEGIN
                    DELETE FROM lib2_media_server_mappings
                     WHERE entity_type='{entity_type}' AND entity_id=OLD.id;
                END"""
        )


def is_media_server_source(server_source: Any) -> bool:
    return str(server_source or "").strip().lower() in MEDIA_SERVER_SOURCES


def resolve_mapping(cursor: Any, entity_type: str, server_source: Any,
                    server_id: Any) -> Optional[int]:
    row = cursor.execute(
        "SELECT entity_id FROM lib2_media_server_mappings "
        "WHERE entity_type=? AND server_source=? AND server_id=?",
        (entity_type, str(server_source), str(server_id)),
    ).fetchone()
    return int(row[0]) if row else None


def upsert_mapping(cursor: Any, entity_type: str, entity_id: int,
                   server_source: Any, server_id: Any) -> None:
    """Record one positive recognition, safely handling a server re-key."""
    source = str(server_source or "").strip().lower()
    sid = str(server_id or "").strip()
    if not source or not sid or not is_media_server_source(source):
        return
    # The same server id cannot truthfully identify two catalogue rows.  A
    # re-match moves it; the entity/source uniqueness below handles a re-key.
    cursor.execute(
        "DELETE FROM lib2_media_server_mappings "
        "WHERE entity_type=? AND server_source=? AND server_id=? AND entity_id<>?",
        (entity_type, source, sid, int(entity_id)),
    )
    cursor.execute(
        """INSERT INTO lib2_media_server_mappings(
               entity_type,entity_id,server_source,server_id,match_status)
           VALUES(?,?,?,?,'recognized')
           ON CONFLICT(entity_type,entity_id,server_source) DO UPDATE SET
               server_id=excluded.server_id,
               match_status='recognized',
               last_seen_at=CURRENT_TIMESTAMP""",
        (entity_type, int(entity_id), source, sid),
    )
    # Compatibility only.  New code reads the mapping table, so replacing this
    # snapshot cannot erase another server's durable mapping.
    table = ENTITY_TABLES[entity_type]
    cursor.execute(
        f"UPDATE {table} SET server_source=?,server_id=?,updated_at=CURRENT_TIMESTAMP "
        "WHERE id=?",
        (source, sid, int(entity_id)),
    )


def mapping_sources(cursor: Any, entity_type: str, entity_ids: Iterable[int]
                    ) -> Dict[int, List[str]]:
    ids = sorted({int(value) for value in entity_ids if value is not None})
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = cursor.execute(
        f"SELECT entity_id,server_source FROM lib2_media_server_mappings "
        f"WHERE entity_type=? AND entity_id IN ({marks}) "
        "AND match_status='recognized' ORDER BY server_source",
        [entity_type, *ids],
    ).fetchall()
    result: Dict[int, List[str]] = {entity_id: [] for entity_id in ids}
    for row in rows:
        result[int(row[0])].append(str(row[1]))
    return result


def backfill_legacy_mappings(cursor: Any, *, connection: Any = None,
                             batch_size: int = 500, on_batch=None,
                             should_stop=None) -> int:
    """Converge real media-server stamps from the old one-slot columns.

    The batch predicate has to select exactly what ``upsert_mapping`` will write.
    It is the loop's only exit condition, so a row the SELECT keeps returning and
    the write keeps skipping — a blank ``server_id`` was one — spins here forever.
    """
    changed = 0
    for entity_type, table in ENTITY_TABLES.items():
        while not (should_stop and should_stop()):
            rows = cursor.execute(
                f"""SELECT id,server_source,server_id FROM {table} old
                     WHERE old.server_source IN ('plex','jellyfin','navidrome')
                       AND old.server_id IS NOT NULL
                       AND TRIM(old.server_id) <> ''
                       AND NOT EXISTS (
                           SELECT 1 FROM lib2_media_server_mappings m
                            WHERE m.entity_type=? AND m.entity_id=old.id
                              AND m.server_source=old.server_source)
                     LIMIT ?""",
                (entity_type, max(1, int(batch_size))),
            ).fetchall()
            if not rows:
                break
            for row in rows:
                upsert_mapping(cursor, entity_type, int(row[0]), row[1], row[2])
            changed += len(rows)
            if connection is not None:
                connection.commit()
            if on_batch is not None:
                on_batch()
    return changed


__all__ = [
    "MEDIA_SERVER_SOURCES", "backfill_legacy_mappings",
    "ensure_media_mapping_schema", "is_media_server_source",
    "mapping_sources", "resolve_mapping", "upsert_mapping",
]
