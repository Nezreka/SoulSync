"""Durable, typed metadata-provider snapshots for Library v2.

Provider responses are normalized before they reach this store. Persisting the
normalized payload gives refresh code a stable provenance contract without
retaining request headers, signed URLs, access tokens, or provider-specific
wire noise.

Snapshots are keyed by provider + local entity + scope. A discography snapshot
therefore cannot overwrite an album tracklist snapshot, and multiple providers
can coexist while source priority changes. ``is_complete`` is explicit because
destructive reconciliation is only safe after a complete provider traversal.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


LIBRARY_PROVIDER_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS library_provider_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    scope TEXT NOT NULL,
    provider_entity_id TEXT,
    etag TEXT,
    provider_version TEXT,
    fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_complete INTEGER NOT NULL,
    cursor TEXT,
    page_count INTEGER,
    parser_version TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider, entity_type, entity_id, scope),
    CHECK(entity_id > 0),
    CHECK(is_complete IN (0, 1)),
    CHECK(page_count IS NULL OR page_count >= 0)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_provider_snapshots_entity "
    "ON library_provider_snapshots(entity_type, entity_id, scope)",
    "CREATE INDEX IF NOT EXISTS idx_provider_snapshots_fetched "
    "ON library_provider_snapshots(provider, fetched_at)",
)

_ENTITY_TABLES = {
    "artist": "lib2_artists",
    "album": "lib2_albums",
    "track": "lib2_tracks",
    "release_edition": "lib2_release_editions",
}


@dataclass(frozen=True)
class ProviderSnapshot:
    id: int
    provider: str
    entity_type: str
    entity_id: int
    scope: str
    provider_entity_id: Optional[str]
    etag: Optional[str]
    provider_version: Optional[str]
    fetched_at: str
    is_complete: bool
    cursor: Optional[str]
    page_count: Optional[int]
    parser_version: str
    payload_hash: str
    payload: Any


@dataclass(frozen=True)
class SnapshotWriteResult:
    snapshot: ProviderSnapshot
    payload_changed: bool
    previous_hash: Optional[str]


def ensure_provider_snapshot_schema(cursor: Any) -> None:
    """Create the provider snapshot store. Idempotent and transaction-neutral."""
    cursor.execute(LIBRARY_PROVIDER_SNAPSHOTS_DDL)
    for index_sql in _INDEXES:
        cursor.execute(index_sql)
    for entity_type, table in _ENTITY_TABLES.items():
        exists = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        trigger = f"trg_{table}_provider_snapshots_delete"
        cursor.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        cursor.execute(f"""
            CREATE TRIGGER {trigger}
            AFTER DELETE ON {table}
            FOR EACH ROW
            BEGIN
                DELETE FROM library_provider_snapshots
                 WHERE entity_type='{entity_type}' AND entity_id=OLD.id;
            END
        """)


def _required_text(value: Any, field: str, *, lowercase: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text.lower() if lowercase else text


def canonical_payload(payload: Any) -> tuple[str, str]:
    """Return stable JSON + SHA-256 for a normalized provider payload."""
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("provider snapshot payload must be valid JSON") from exc
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return encoded, digest


def _row_dict(cursor: Any, row: Any) -> Dict[str, Any]:
    if hasattr(row, "keys"):
        return dict(row)
    return {
        column[0]: value
        for column, value in zip(cursor.description, row, strict=True)
    }


def _snapshot_from_row(cursor: Any, row: Any) -> ProviderSnapshot:
    data = _row_dict(cursor, row)
    return ProviderSnapshot(
        id=int(data["id"]),
        provider=str(data["provider"]),
        entity_type=str(data["entity_type"]),
        entity_id=int(data["entity_id"]),
        scope=str(data["scope"]),
        provider_entity_id=data["provider_entity_id"],
        etag=data["etag"],
        provider_version=data["provider_version"],
        fetched_at=str(data["fetched_at"]),
        is_complete=bool(data["is_complete"]),
        cursor=data["cursor"],
        page_count=(int(data["page_count"])
                    if data["page_count"] is not None else None),
        parser_version=str(data["parser_version"]),
        payload_hash=str(data["payload_hash"]),
        payload=json.loads(data["payload_json"]),
    )


def get_provider_snapshot(conn: Any, *, provider: str, entity_type: str,
                          entity_id: int, scope: str) -> Optional[ProviderSnapshot]:
    """Read one exact provider/entity/scope snapshot."""
    provider = _required_text(provider, "provider", lowercase=True)
    entity_type = _required_text(entity_type, "entity_type", lowercase=True)
    scope = _required_text(scope, "scope", lowercase=True)
    entity_id = int(entity_id)
    cursor = conn.execute(
        """SELECT * FROM library_provider_snapshots
            WHERE provider=? AND entity_type=? AND entity_id=? AND scope=?""",
        (provider, entity_type, entity_id, scope),
    )
    row = cursor.fetchone()
    return _snapshot_from_row(cursor, row) if row is not None else None


def get_latest_provider_snapshot(
    conn: Any, *, entity_type: str, entity_id: int, scope: str,
) -> Optional[ProviderSnapshot]:
    """Read the most recently fetched snapshot across all providers."""
    entity_type = _required_text(entity_type, "entity_type", lowercase=True)
    scope = _required_text(scope, "scope", lowercase=True)
    cursor = conn.execute(
        """SELECT * FROM library_provider_snapshots
            WHERE entity_type=? AND entity_id=? AND scope=?
            ORDER BY fetched_at DESC, id DESC LIMIT 1""",
        (entity_type, int(entity_id), scope),
    )
    row = cursor.fetchone()
    return _snapshot_from_row(cursor, row) if row is not None else None


def record_provider_snapshot(
    conn: Any,
    *,
    provider: str,
    entity_type: str,
    entity_id: int,
    scope: str,
    parser_version: str,
    payload: Any,
    is_complete: bool,
    provider_entity_id: Optional[str] = None,
    etag: Optional[str] = None,
    provider_version: Optional[str] = None,
    cursor: Optional[str] = None,
    page_count: Optional[int] = None,
) -> SnapshotWriteResult:
    """Upsert one normalized snapshot without committing the caller's transaction."""
    ensure_provider_snapshot_schema(conn.cursor())
    provider = _required_text(provider, "provider", lowercase=True)
    entity_type = _required_text(entity_type, "entity_type", lowercase=True)
    scope = _required_text(scope, "scope", lowercase=True)
    parser_version = _required_text(parser_version, "parser_version")
    entity_id = int(entity_id)
    if entity_id <= 0:
        raise ValueError("entity_id must be positive")
    if page_count is not None:
        page_count = int(page_count)
        if page_count < 0:
            raise ValueError("page_count cannot be negative")

    payload_json, payload_hash = canonical_payload(payload)
    existing = get_provider_snapshot(
        conn,
        provider=provider,
        entity_type=entity_type,
        entity_id=entity_id,
        scope=scope,
    )
    previous_hash = existing.payload_hash if existing else None
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    conn.execute(
        """INSERT INTO library_provider_snapshots(
               provider, entity_type, entity_id, scope, provider_entity_id,
               etag, provider_version, fetched_at, is_complete, cursor, page_count,
               parser_version, payload_hash, payload_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(provider, entity_type, entity_id, scope) DO UPDATE SET
               provider_entity_id=excluded.provider_entity_id,
               etag=excluded.etag,
               provider_version=excluded.provider_version,
               fetched_at=excluded.fetched_at,
               is_complete=excluded.is_complete,
               cursor=excluded.cursor,
               page_count=excluded.page_count,
               parser_version=excluded.parser_version,
               payload_hash=excluded.payload_hash,
               payload_json=excluded.payload_json,
               updated_at=CURRENT_TIMESTAMP""",
        (
            provider, entity_type, entity_id, scope,
            str(provider_entity_id) if provider_entity_id is not None else None,
            etag, provider_version, fetched_at, 1 if is_complete else 0,
            cursor, page_count,
            parser_version, payload_hash, payload_json,
        ),
    )
    snapshot = get_provider_snapshot(
        conn,
        provider=provider,
        entity_type=entity_type,
        entity_id=entity_id,
        scope=scope,
    )
    if snapshot is None:  # pragma: no cover - guarded by the successful upsert
        raise RuntimeError("provider snapshot upsert did not produce a row")
    return SnapshotWriteResult(
        snapshot=snapshot,
        payload_changed=previous_hash != payload_hash,
        previous_hash=previous_hash,
    )


def delete_entity_snapshots(conn: Any, *, entity_type: str, entity_id: int) -> int:
    """Prune all provider scopes for a deleted local entity. Does not commit."""
    entity_type = _required_text(entity_type, "entity_type", lowercase=True)
    result = conn.execute(
        "DELETE FROM library_provider_snapshots WHERE entity_type=? AND entity_id=?",
        (entity_type, int(entity_id)),
    )
    return int(result.rowcount)


__all__ = [
    "LIBRARY_PROVIDER_SNAPSHOTS_DDL",
    "ProviderSnapshot",
    "SnapshotWriteResult",
    "canonical_payload",
    "delete_entity_snapshots",
    "ensure_provider_snapshot_schema",
    "get_latest_provider_snapshot",
    "get_provider_snapshot",
    "record_provider_snapshot",
]
