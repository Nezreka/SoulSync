"""Exact release-candidate blocklist used by failed-download re-search."""

from __future__ import annotations

import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from core.acquisition.candidates import get_candidate, redact_sensitive_text
from core.acquisition.history import (
    ensure_acquisition_history_schema,
    record_history_event,
)
from core.acquisition.requests import ADMIN_PROFILE_ID


BLOCKLIST_ID_PREFIX = "abl1-"

RELEASE_BLOCKLIST_DDL = """
CREATE TABLE IF NOT EXISTS release_blocklist (
    id TEXT PRIMARY KEY,
    block_key TEXT NOT NULL,
    source TEXT NOT NULL,
    indexer TEXT,
    guid TEXT,
    dedupe_key TEXT NOT NULL,
    request_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    actor_profile_id INTEGER NOT NULL,
    reason_code TEXT NOT NULL,
    message TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    expires_at REAL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    removed_at TIMESTAMP,
    removed_by_profile_id INTEGER,
    CHECK(actor_profile_id = 1),
    CHECK(removed_by_profile_id IS NULL OR removed_by_profile_id = 1),
    CHECK(active IN (0,1)),
    CHECK(expires_at IS NULL OR expires_at > 0)
)
"""

_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_release_blocklist_active_key "
    "ON release_blocklist(block_key) WHERE active=1",
    "CREATE INDEX IF NOT EXISTS idx_release_blocklist_candidate "
    "ON release_blocklist(candidate_id, active)",
    "CREATE INDEX IF NOT EXISTS idx_release_blocklist_expiry "
    "ON release_blocklist(active, expires_at)",
)

_COLUMNS = (
    "id", "block_key", "source", "indexer", "guid", "dedupe_key",
    "request_id", "candidate_id", "actor_profile_id", "reason_code",
    "message", "active", "expires_at", "created_at", "removed_at",
    "removed_by_profile_id",
)


@dataclass(frozen=True)
class BlocklistEntry:
    id: str
    block_key: str
    source: str
    indexer: Optional[str]
    guid: Optional[str]
    dedupe_key: str
    request_id: str
    candidate_id: str
    actor_profile_id: int
    reason_code: str
    message: Optional[str]
    active: bool
    expires_at: Optional[float]
    created_at: str
    removed_at: Optional[str]
    removed_by_profile_id: Optional[int]

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "indexer": self.indexer,
            "guid": self.guid,
            "request_id": self.request_id,
            "candidate_id": self.candidate_id,
            "reason_code": self.reason_code,
            "message": self.message,
            "active": self.active,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "removed_at": self.removed_at,
        }


def ensure_release_blocklist_schema(conn: Any) -> None:
    conn.execute(RELEASE_BLOCKLIST_DDL)
    for sql in _INDEXES:
        conn.execute(sql)
    ensure_acquisition_history_schema(conn)


def _from_row(row: Any) -> BlocklistEntry:
    data = dict(row) if hasattr(row, "keys") else dict(zip(_COLUMNS, row, strict=True))
    return BlocklistEntry(
        id=str(data["id"]),
        block_key=str(data["block_key"]),
        source=str(data["source"]),
        indexer=data["indexer"],
        guid=data["guid"],
        dedupe_key=str(data["dedupe_key"]),
        request_id=str(data["request_id"]),
        candidate_id=str(data["candidate_id"]),
        actor_profile_id=int(data["actor_profile_id"]),
        reason_code=str(data["reason_code"]),
        message=data["message"],
        active=bool(data["active"]),
        expires_at=(
            float(data["expires_at"]) if data["expires_at"] is not None else None),
        created_at=str(data["created_at"]),
        removed_at=data["removed_at"],
        removed_by_profile_id=data["removed_by_profile_id"],
    )


def _get_entry(conn: Any, entry_id: str) -> Optional[BlocklistEntry]:
    row = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM release_blocklist WHERE id=?",
        (str(entry_id),),
    ).fetchone()
    return _from_row(row) if row is not None else None


def block_candidate(
    conn: Any,
    candidate_id: str,
    *,
    reason_code: str,
    message: Optional[str] = None,
    actor_profile_id: int = ADMIN_PROFILE_ID,
    download_id: Optional[str] = None,
    expires_at: Optional[float] = None,
    now: Optional[float] = None,
) -> Tuple[BlocklistEntry, bool]:
    """Block an exact candidate identity; repeated active blocks are idempotent."""
    ensure_release_blocklist_schema(conn)
    if int(actor_profile_id) != ADMIN_PROFILE_ID:
        raise ValueError("release blocklist is admin-profile only")
    candidate = get_candidate(conn, candidate_id)
    if candidate is None:
        raise ValueError("release candidate does not exist")
    reason_code = str(reason_code or "").strip().lower()
    if not reason_code or len(reason_code) > 100:
        raise ValueError("blocklist reason_code must contain 1 to 100 characters")
    timestamp = time.time() if now is None else float(now)
    if expires_at is not None and float(expires_at) <= timestamp:
        raise ValueError("blocklist expiry must be in the future")
    block_key = candidate.dedupe_key
    conn.execute(
        """UPDATE release_blocklist
              SET active=0, removed_at=CURRENT_TIMESTAMP
            WHERE active=1 AND expires_at IS NOT NULL AND expires_at<=?""",
        (timestamp,),
    )
    existing = conn.execute(
        f"""SELECT {', '.join(_COLUMNS)} FROM release_blocklist
             WHERE block_key=? AND active=1""",
        (block_key,),
    ).fetchone()
    if existing is not None:
        return _from_row(existing), False

    entry_id = BLOCKLIST_ID_PREFIX + secrets.token_urlsafe(18)
    try:
        conn.execute(
            """INSERT INTO release_blocklist(
                   id, block_key, source, indexer, guid, dedupe_key,
                   request_id, candidate_id, actor_profile_id, reason_code,
                   message, expires_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                entry_id,
                block_key,
                candidate.source,
                candidate.indexer,
                candidate.guid,
                candidate.dedupe_key,
                candidate.request_id,
                candidate.id,
                ADMIN_PROFILE_ID,
                reason_code,
                redact_sensitive_text(message) if message else None,
                float(expires_at) if expires_at is not None else None,
            ),
        )
    except sqlite3.IntegrityError:
        concurrent = conn.execute(
            f"""SELECT {', '.join(_COLUMNS)} FROM release_blocklist
                 WHERE block_key=? AND active=1""",
            (block_key,),
        ).fetchone()
        if concurrent is None:
            raise
        return _from_row(concurrent), False
    entry = _get_entry(conn, entry_id)
    if entry is None:  # pragma: no cover - guarded by successful INSERT
        raise RuntimeError("blocklist insert did not produce a row")
    record_history_event(
        conn,
        "candidate_blocklisted",
        request_id=candidate.request_id,
        candidate_id=candidate.id,
        download_id=download_id,
        actor_profile_id=ADMIN_PROFILE_ID,
        reason_code=reason_code,
        message=message,
        payload={
            "blocklist_id": entry.id,
            "source": candidate.source,
            "indexer": candidate.indexer,
            "guid": candidate.guid,
            "expires_at": entry.expires_at,
        },
    )
    return entry, True


def unblock_candidate(
    conn: Any,
    entry_id: str,
    *,
    actor_profile_id: int = ADMIN_PROFILE_ID,
    message: Optional[str] = None,
) -> Tuple[BlocklistEntry, bool]:
    ensure_release_blocklist_schema(conn)
    if int(actor_profile_id) != ADMIN_PROFILE_ID:
        raise ValueError("release blocklist is admin-profile only")
    entry = _get_entry(conn, entry_id)
    if entry is None:
        raise KeyError(f"blocklist entry not found: {entry_id}")
    if not entry.active:
        return entry, False
    updated = conn.execute(
        """UPDATE release_blocklist
              SET active=0, removed_at=CURRENT_TIMESTAMP,
                  removed_by_profile_id=?
            WHERE id=? AND active=1""",
        (ADMIN_PROFILE_ID, entry.id),
    )
    if updated.rowcount != 1:
        current = _get_entry(conn, entry.id)
        if current is None:  # pragma: no cover - rows are never deleted here
            raise RuntimeError("blocklist entry disappeared")
        return current, False
    entry = _get_entry(conn, entry.id)
    record_history_event(
        conn,
        "candidate_unblocked",
        request_id=entry.request_id,
        candidate_id=entry.candidate_id,
        actor_profile_id=ADMIN_PROFILE_ID,
        reason_code="manual_unblock",
        message=message,
        payload={"blocklist_id": entry.id},
    )
    return entry, True


def active_blocklisted_dedupe_keys(
    conn: Any, *, now: Optional[float] = None,
) -> frozenset[str]:
    ensure_release_blocklist_schema(conn)
    timestamp = time.time() if now is None else float(now)
    rows = conn.execute(
        """SELECT dedupe_key FROM release_blocklist
            WHERE active=1 AND (expires_at IS NULL OR expires_at>?)""",
        (timestamp,),
    ).fetchall()
    return frozenset(str(row[0]) for row in rows)


def list_blocklist_entries(
    conn: Any, *, active_only: bool = True, now: Optional[float] = None,
) -> Tuple[BlocklistEntry, ...]:
    ensure_release_blocklist_schema(conn)
    args = []
    where = ""
    if active_only:
        where = " WHERE active=1 AND (expires_at IS NULL OR expires_at>?)"
        args.append(time.time() if now is None else float(now))
    rows = conn.execute(
        f"""SELECT {', '.join(_COLUMNS)} FROM release_blocklist{where}
              ORDER BY created_at, rowid""",
        args,
    ).fetchall()
    return tuple(_from_row(row) for row in rows)


__all__ = [
    "BLOCKLIST_ID_PREFIX",
    "RELEASE_BLOCKLIST_DDL",
    "BlocklistEntry",
    "active_blocklisted_dedupe_keys",
    "block_candidate",
    "ensure_release_blocklist_schema",
    "list_blocklist_entries",
    "unblock_candidate",
]
