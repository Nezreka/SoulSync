"""Append-only business history for the acquisition lifecycle."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from core.acquisition.candidates import redact_payload, redact_sensitive_text
from core.acquisition.requests import ADMIN_PROFILE_ID


HISTORY_ID_PREFIX = "ahe1-"
EVENT_TYPES = frozenset({
    "request_created",
    "search_started",
    "search_completed",
    "search_failed",
    "candidates_evaluated",
    "no_candidate",
    "grab_prepared",
    "grab_submitted",
    "grab_submission_uncertain",
    "force_grab",
    "grab_completed",
    "grab_failed",
    "candidate_blocklisted",
    "candidate_unblocked",
    "retry_started",
    "cancelled",
    "import_started",
    "import_completed",
    "import_failed",
})

ACQUISITION_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS acquisition_history (
    id TEXT PRIMARY KEY,
    request_id TEXT,
    candidate_id TEXT,
    download_id TEXT,
    event_type TEXT NOT NULL,
    actor_profile_id INTEGER NOT NULL,
    reason_code TEXT,
    message TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(actor_profile_id = 1)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_acquisition_history_request "
    "ON acquisition_history(request_id, created_at, id)",
    "CREATE INDEX IF NOT EXISTS idx_acquisition_history_candidate "
    "ON acquisition_history(candidate_id, created_at, id)",
    "CREATE INDEX IF NOT EXISTS idx_acquisition_history_download "
    "ON acquisition_history(download_id, created_at, id)",
)

_TRIGGERS = (
    """CREATE TRIGGER IF NOT EXISTS trg_acquisition_history_no_update
       BEFORE UPDATE ON acquisition_history
       BEGIN SELECT RAISE(ABORT, 'acquisition_history is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS trg_acquisition_history_no_delete
       BEFORE DELETE ON acquisition_history
       BEGIN SELECT RAISE(ABORT, 'acquisition_history is append-only'); END""",
)

_COLUMNS = (
    "id", "request_id", "candidate_id", "download_id", "event_type",
    "actor_profile_id", "reason_code", "message", "payload_json", "created_at",
)


@dataclass(frozen=True)
class AcquisitionHistoryEvent:
    id: str
    request_id: Optional[str]
    candidate_id: Optional[str]
    download_id: Optional[str]
    event_type: str
    actor_profile_id: int
    reason_code: Optional[str]
    message: Optional[str]
    payload: Dict[str, Any]
    created_at: str

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "candidate_id": self.candidate_id,
            "download_id": self.download_id,
            "event_type": self.event_type,
            "actor_profile_id": self.actor_profile_id,
            "reason_code": self.reason_code,
            "message": self.message,
            "payload": dict(self.payload),
            "created_at": self.created_at,
        }


def ensure_acquisition_history_schema(conn: Any) -> None:
    existing = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='acquisition_history'"
    ).fetchone()
    existing_sql = str(existing[0] or "") if existing is not None else ""
    if existing_sql and "CHECK(event_type IN" in existing_sql:
        # Early Phase-4 builds constrained event names in SQLite. That makes
        # an append-only history impossible to extend without a table rebuild.
        # Preserve every row, remove only the closed enum constraint, then
        # recreate append-only triggers below.
        for trigger in (
            "trg_acquisition_history_no_update",
            "trg_acquisition_history_no_delete",
        ):
            conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        conn.execute(
            "ALTER TABLE acquisition_history RENAME TO acquisition_history_legacy")
        conn.execute(ACQUISITION_HISTORY_DDL)
        columns = ", ".join(_COLUMNS)
        conn.execute(
            f"""INSERT INTO acquisition_history({columns})
                SELECT {columns} FROM acquisition_history_legacy""")
        conn.execute("DROP TABLE acquisition_history_legacy")
    conn.execute(ACQUISITION_HISTORY_DDL)
    for sql in _INDEXES:
        conn.execute(sql)
    for sql in _TRIGGERS:
        conn.execute(sql)


def _json_object(value: Optional[Mapping[str, Any]]) -> str:
    if value is not None and not isinstance(value, Mapping):
        raise ValueError("acquisition history payload must be an object")
    try:
        return json.dumps(
            redact_payload(dict(value or {})),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("acquisition history payload must be JSON serializable") from exc


def _from_row(row: Any) -> AcquisitionHistoryEvent:
    data = dict(row) if hasattr(row, "keys") else dict(zip(_COLUMNS, row, strict=True))
    try:
        payload = json.loads(data["payload_json"] or "{}")
    except (TypeError, ValueError):
        payload = {}
    return AcquisitionHistoryEvent(
        id=str(data["id"]),
        request_id=data["request_id"],
        candidate_id=data["candidate_id"],
        download_id=data["download_id"],
        event_type=str(data["event_type"]),
        actor_profile_id=int(data["actor_profile_id"]),
        reason_code=data["reason_code"],
        message=data["message"],
        payload=payload if isinstance(payload, dict) else {},
        created_at=str(data["created_at"]),
    )


def record_history_event(
    conn: Any,
    event_type: str,
    *,
    request_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    download_id: Optional[str] = None,
    actor_profile_id: int = ADMIN_PROFILE_ID,
    reason_code: Optional[str] = None,
    message: Optional[str] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> AcquisitionHistoryEvent:
    """Append one redacted event. The caller owns the transaction."""
    ensure_acquisition_history_schema(conn)
    event_type = str(event_type or "").strip().lower()
    if event_type not in EVENT_TYPES:
        raise ValueError(f"invalid acquisition history event: {event_type!r}")
    if int(actor_profile_id) != ADMIN_PROFILE_ID:
        raise ValueError("acquisition history is admin-profile only")
    if not any((request_id, candidate_id, download_id)):
        raise ValueError("acquisition history event requires a business correlation id")
    event_id = HISTORY_ID_PREFIX + secrets.token_urlsafe(18)
    conn.execute(
        """INSERT INTO acquisition_history(
               id, request_id, candidate_id, download_id, event_type,
               actor_profile_id, reason_code, message, payload_json)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            event_id,
            str(request_id) if request_id else None,
            str(candidate_id) if candidate_id else None,
            str(download_id) if download_id else None,
            event_type,
            ADMIN_PROFILE_ID,
            str(reason_code)[:100] if reason_code else None,
            redact_sensitive_text(message) if message else None,
            _json_object(payload),
        ),
    )
    row = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM acquisition_history WHERE id=?",
        (event_id,),
    ).fetchone()
    return _from_row(row)


def list_history_events(
    conn: Any,
    *,
    request_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    download_id: Optional[str] = None,
    limit: int = 200,
) -> Tuple[AcquisitionHistoryEvent, ...]:
    ensure_acquisition_history_schema(conn)
    limit = int(limit)
    if limit <= 0 or limit > 1000:
        raise ValueError("history limit must be between 1 and 1000")
    clauses = []
    args = []
    for column, value in (
        ("request_id", request_id),
        ("candidate_id", candidate_id),
        ("download_id", download_id),
    ):
        if value:
            clauses.append(f"{column}=?")
            args.append(str(value))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(
        f"""SELECT {', '.join(_COLUMNS)} FROM acquisition_history{where}
              ORDER BY created_at, rowid LIMIT ?""",
        (*args, limit),
    ).fetchall()
    return tuple(_from_row(row) for row in rows)


__all__ = [
    "ACQUISITION_HISTORY_DDL",
    "EVENT_TYPES",
    "HISTORY_ID_PREFIX",
    "AcquisitionHistoryEvent",
    "ensure_acquisition_history_schema",
    "list_history_events",
    "record_history_event",
]
