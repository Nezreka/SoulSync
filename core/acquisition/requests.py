"""Persistent acquisition requests and idempotent lifecycle management.

An acquisition request describes *what* SoulSync should acquire. It is source-
agnostic and survives search retries, provider fallback, download-client restarts,
and process restarts. Search candidates and grabs attach to this durable root.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple


REQUEST_ID_PREFIX = "arq1-"
ADMIN_PROFILE_ID = 1

SCOPES = frozenset({
    "recording",
    "release_group",
    "release_edition",
    "artist_missing",
    "upgrade",
})
TRIGGERS = frozenset({"manual", "monitor", "scheduled", "retry", "upgrade"})
STATUSES = frozenset({
    "pending",
    "searching",
    "candidates_ready",
    "no_candidate",
    "grabbing",
    "completed",
    "failed",
    "cancelled",
})
TERMINAL_STATUSES = frozenset({"completed", "cancelled"})

_ALLOWED_TRANSITIONS = {
    "pending": {"searching", "failed", "cancelled"},
    "searching": {"candidates_ready", "no_candidate", "failed", "cancelled"},
    "candidates_ready": {"searching", "grabbing", "no_candidate", "failed", "cancelled"},
    "no_candidate": {"searching", "failed", "cancelled"},
    "grabbing": {"completed", "failed", "cancelled"},
    "failed": {"searching", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}

ACQUISITION_REQUESTS_DDL = """
CREATE TABLE IF NOT EXISTS acquisition_requests (
    id TEXT PRIMARY KEY,
    profile_id INTEGER NOT NULL,
    scope TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    quality_profile_id INTEGER NOT NULL,
    trigger TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    search_options_json TEXT NOT NULL DEFAULT '{}',
    force_options_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    next_retry_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    UNIQUE(profile_id, idempotency_key),
    CHECK(profile_id = 1),
    CHECK(entity_id > 0),
    CHECK(quality_profile_id > 0),
    CHECK(attempts >= 0),
    CHECK(scope IN ('recording','release_group','release_edition','artist_missing','upgrade')),
    CHECK(trigger IN ('manual','monitor','scheduled','retry','upgrade')),
    CHECK(status IN ('pending','searching','candidates_ready','no_candidate','grabbing','completed','failed','cancelled'))
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_acquisition_requests_status "
    "ON acquisition_requests(status, next_retry_at)",
    "CREATE INDEX IF NOT EXISTS idx_acquisition_requests_entity "
    "ON acquisition_requests(scope, entity_id, profile_id)",
)

_COLUMNS = (
    "id", "profile_id", "scope", "entity_id", "quality_profile_id",
    "trigger", "idempotency_key", "status", "search_options_json",
    "force_options_json", "attempts", "last_error", "next_retry_at",
    "created_at", "updated_at", "completed_at",
)


class IdempotencyConflict(ValueError):
    """The same profile/key was reused for a semantically different request."""


class InvalidRequestTransition(ValueError):
    """The requested lifecycle transition is not allowed."""


@dataclass(frozen=True)
class AcquisitionRequest:
    id: str
    profile_id: int
    scope: str
    entity_id: int
    quality_profile_id: int
    trigger: str
    idempotency_key: str
    status: str
    search_options: Dict[str, Any]
    force_options: Dict[str, Any]
    attempts: int
    last_error: Optional[str]
    next_retry_at: Optional[str]
    created_at: str
    updated_at: str
    completed_at: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "profile_id": self.profile_id,
            "scope": self.scope,
            "entity_id": self.entity_id,
            "quality_profile_id": self.quality_profile_id,
            "trigger": self.trigger,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "search_options": dict(self.search_options),
            "force_options": dict(self.force_options),
            "attempts": self.attempts,
            "last_error": self.last_error,
            "next_retry_at": self.next_retry_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }


def ensure_acquisition_requests_schema(conn: Any) -> None:
    cursor = conn.cursor()
    cursor.execute(ACQUISITION_REQUESTS_DDL)
    for index_sql in _INDEXES:
        cursor.execute(index_sql)


def _required_choice(value: Any, name: str, allowed: frozenset[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in allowed:
        raise ValueError(f"invalid acquisition {name}: {value!r}")
    return normalized


def _positive_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _options_json(value: Optional[Mapping[str, Any]], name: str) -> str:
    try:
        return json.dumps(
            dict(value or {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a JSON object") from exc


def _row_mapping(cursor: Any, row: Any) -> Dict[str, Any]:
    if hasattr(row, "keys"):
        return dict(row)
    return {
        column[0]: value
        for column, value in zip(cursor.description, row, strict=True)
    }


def _decode_object(raw: Any) -> Dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _from_row(cursor: Any, row: Any) -> AcquisitionRequest:
    data = _row_mapping(cursor, row)
    return AcquisitionRequest(
        id=str(data["id"]),
        profile_id=int(data["profile_id"]),
        scope=str(data["scope"]),
        entity_id=int(data["entity_id"]),
        quality_profile_id=int(data["quality_profile_id"]),
        trigger=str(data["trigger"]),
        idempotency_key=str(data["idempotency_key"]),
        status=str(data["status"]),
        search_options=_decode_object(data["search_options_json"]),
        force_options=_decode_object(data["force_options_json"]),
        attempts=int(data["attempts"] or 0),
        last_error=data["last_error"],
        next_retry_at=data["next_retry_at"],
        created_at=str(data["created_at"]),
        updated_at=str(data["updated_at"]),
        completed_at=data["completed_at"],
    )


def get_request(conn: Any, request_id: str) -> Optional[AcquisitionRequest]:
    cursor = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM acquisition_requests WHERE id=?",
        (str(request_id),),
    )
    row = cursor.fetchone()
    return _from_row(cursor, row) if row is not None else None


def get_request_by_key(
    conn: Any, *, profile_id: int, idempotency_key: str,
) -> Optional[AcquisitionRequest]:
    cursor = conn.execute(
        f"""SELECT {', '.join(_COLUMNS)} FROM acquisition_requests
             WHERE profile_id=? AND idempotency_key=?""",
        (int(profile_id), str(idempotency_key)),
    )
    row = cursor.fetchone()
    return _from_row(cursor, row) if row is not None else None


def create_request(
    conn: Any,
    *,
    profile_id: int,
    scope: str,
    entity_id: int,
    quality_profile_id: int,
    trigger: str,
    idempotency_key: str,
    search_options: Optional[Mapping[str, Any]] = None,
    force_options: Optional[Mapping[str, Any]] = None,
) -> Tuple[AcquisitionRequest, bool]:
    """Create a request or return its exact idempotent predecessor.

    The caller owns the transaction. ``created`` is false only when every
    semantic field matches the existing row for ``profile_id/idempotency_key``.
    """
    ensure_acquisition_requests_schema(conn)
    profile_id = _positive_int(profile_id, "profile_id")
    if profile_id != ADMIN_PROFILE_ID:
        raise ValueError("Library v2 acquisition requests are admin-profile only")
    entity_id = _positive_int(entity_id, "entity_id")
    quality_profile_id = _positive_int(quality_profile_id, "quality_profile_id")
    scope = _required_choice(scope, "scope", SCOPES)
    trigger = _required_choice(trigger, "trigger", TRIGGERS)
    idempotency_key = str(idempotency_key or "").strip()
    if not idempotency_key or len(idempotency_key) > 255:
        raise ValueError("idempotency_key must contain 1 to 255 characters")
    search_json = _options_json(search_options, "search_options")
    force_json = _options_json(force_options, "force_options")

    existing = get_request_by_key(
        conn, profile_id=profile_id, idempotency_key=idempotency_key)
    expected = (
        scope, entity_id, quality_profile_id, trigger, search_json, force_json,
    )
    if existing is not None:
        actual = (
            existing.scope,
            existing.entity_id,
            existing.quality_profile_id,
            existing.trigger,
            _options_json(existing.search_options, "search_options"),
            _options_json(existing.force_options, "force_options"),
        )
        if actual != expected:
            raise IdempotencyConflict(
                "idempotency key already belongs to a different acquisition request")
        return existing, False

    request_id = REQUEST_ID_PREFIX + secrets.token_urlsafe(18)
    try:
        conn.execute(
            """INSERT INTO acquisition_requests(
                   id, profile_id, scope, entity_id, quality_profile_id, trigger,
                   idempotency_key, search_options_json, force_options_json)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                request_id, profile_id, scope, entity_id, quality_profile_id,
                trigger, idempotency_key, search_json, force_json,
            ),
        )
    except sqlite3.IntegrityError as exc:
        # Another worker may have won the same idempotency-key insert after
        # our initial read. Resolve it through the exact same semantic guard.
        concurrent = get_request_by_key(
            conn, profile_id=profile_id, idempotency_key=idempotency_key)
        if concurrent is None:
            raise
        actual = (
            concurrent.scope,
            concurrent.entity_id,
            concurrent.quality_profile_id,
            concurrent.trigger,
            _options_json(concurrent.search_options, "search_options"),
            _options_json(concurrent.force_options, "force_options"),
        )
        if actual != expected:
            raise IdempotencyConflict(
                "idempotency key concurrently claimed by a different request") from exc
        return concurrent, False
    created = get_request(conn, request_id)
    if created is None:  # pragma: no cover - guarded by successful INSERT
        raise RuntimeError("acquisition request insert did not produce a row")
    return created, True


def transition_request(
    conn: Any,
    request_id: str,
    status: str,
    *,
    expected_status: Optional[str] = None,
    error: Optional[str] = None,
    next_retry_at: Optional[str] = None,
    increment_attempts: bool = False,
) -> AcquisitionRequest:
    """Apply a validated, optionally compare-and-set lifecycle transition."""
    current = get_request(conn, request_id)
    if current is None:
        raise KeyError(f"acquisition request not found: {request_id}")
    status = _required_choice(status, "status", STATUSES)
    if expected_status is not None and current.status != expected_status:
        raise InvalidRequestTransition(
            f"expected request status {expected_status}, found {current.status}")
    if status != current.status and status not in _ALLOWED_TRANSITIONS[current.status]:
        raise InvalidRequestTransition(
            f"cannot transition acquisition request {current.status} -> {status}")

    completed_at_sql = (
        "CURRENT_TIMESTAMP" if status in TERMINAL_STATUSES else "NULL")
    cursor = conn.execute(
        f"""UPDATE acquisition_requests
               SET status=?, last_error=?, next_retry_at=?,
                   attempts=attempts+?, updated_at=CURRENT_TIMESTAMP,
                   completed_at={completed_at_sql}
             WHERE id=? AND status=?""",
        (
            status,
            str(error)[:2000] if error is not None else None,
            next_retry_at,
            1 if increment_attempts else 0,
            current.id,
            current.status,
        ),
    )
    if cursor.rowcount != 1:
        raise InvalidRequestTransition("acquisition request changed concurrently")
    updated = get_request(conn, request_id)
    if updated is None:  # pragma: no cover - row cannot disappear inside UPDATE
        raise RuntimeError("acquisition request disappeared after transition")
    return updated


__all__ = [
    "ACQUISITION_REQUESTS_DDL",
    "ADMIN_PROFILE_ID",
    "AcquisitionRequest",
    "IdempotencyConflict",
    "InvalidRequestTransition",
    "REQUEST_ID_PREFIX",
    "SCOPES",
    "STATUSES",
    "TERMINAL_STATUSES",
    "TRIGGERS",
    "create_request",
    "ensure_acquisition_requests_schema",
    "get_request",
    "get_request_by_key",
    "transition_request",
]
