"""Durable, short-lived retry state for Library-v2 download tasks.

The legacy worker keeps candidate and source state in memory.  This journal
captures only the minimum needed to resume an Acquisition retry after a
process restart.  It is operational state, not permanent history, and is
therefore expired and purgeable after the request reaches a terminal state.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Mapping, Optional, Tuple


RETRY_STATE_TTL_SECONDS = 7 * 24 * 60 * 60

RETRY_STATE_DDL = """
CREATE TABLE IF NOT EXISTS acquisition_retry_state (
    task_id TEXT PRIMARY KEY,
    import_id TEXT NOT NULL,
    track_id INTEGER NOT NULL,
    candidates_json TEXT NOT NULL DEFAULT '[]',
    used_sources_json TEXT NOT NULL DEFAULT '[]',
    exhausted_sources_json TEXT NOT NULL DEFAULT '[]',
    retry_counts_json TEXT NOT NULL DEFAULT '{}',
    retry_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    last_error TEXT,
    expires_at REAL NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(track_id > 0),
    CHECK(status IN ('active', 'completed', 'failed', 'approved'))
)
"""


def ensure_retry_state_schema(conn: Any) -> None:
    conn.execute(RETRY_STATE_DDL)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_acquisition_retry_state_expiry "
        "ON acquisition_retry_state(expires_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_acquisition_retry_state_import "
        "ON acquisition_retry_state(import_id, track_id)"
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(raw: Any, fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except (TypeError, ValueError):
        return fallback


def _candidate_payload(candidate: Any) -> Dict[str, Any]:
    """Keep only fields required to rebuild the legacy TrackResult walk."""
    names = (
        "username", "filename", "size", "bitrate", "duration", "quality",
        "free_upload_slots", "upload_speed", "queue_length", "result_type",
        "sample_rate", "bit_depth", "artist", "title", "album",
        "track_number",
    )
    payload: Dict[str, Any] = {}
    for name in names:
        value = candidate.get(name) if isinstance(candidate, Mapping) else getattr(candidate, name, None)
        if value is not None:
            payload[name] = value
    return payload


def _candidate_list(raw: Any) -> Tuple[Dict[str, Any], ...]:
    value = _decode(raw, [])
    if not isinstance(value, list):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


def record_candidate_snapshot(
    conn: Any,
    *,
    task_id: str,
    import_id: str,
    track_id: int,
    candidates: Any,
    ttl_seconds: int = RETRY_STATE_TTL_SECONDS,
) -> None:
    """Persist a redacted candidate snapshot before the first download try."""
    ensure_retry_state_schema(conn)
    now = time.time()
    payload = [_candidate_payload(item) for item in candidates or ()]
    conn.execute(
        """INSERT INTO acquisition_retry_state(
               task_id, import_id, track_id, candidates_json, expires_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(task_id) DO UPDATE SET
               candidates_json=excluded.candidates_json,
               expires_at=excluded.expires_at,
               updated_at=CURRENT_TIMESTAMP
        """,
        (str(task_id), str(import_id), int(track_id), _json(payload),
         now + max(int(ttl_seconds), 3600)),
    )


def update_retry_state(
    conn: Any,
    task_id: str,
    *,
    used_sources: Any = (),
    exhausted_sources: Any = (),
    retry_counts: Optional[Mapping[str, Any]] = None,
    retry_count: int = 0,
    status: str = "active",
    last_error: Optional[str] = None,
) -> bool:
    ensure_retry_state_schema(conn)
    cursor = conn.execute(
        """UPDATE acquisition_retry_state
              SET used_sources_json=?, exhausted_sources_json=?,
                  retry_counts_json=?, retry_count=?, status=?,
                  last_error=?, updated_at=CURRENT_TIMESTAMP
            WHERE task_id=? AND expires_at>?""",
        (_json(sorted({str(item) for item in (used_sources or ())})),
         _json(sorted({str(item) for item in (exhausted_sources or ())})),
         _json(dict(retry_counts or {})), max(int(retry_count), 0),
         str(status), str(last_error)[:2000] if last_error else None,
         str(task_id), time.time()),
    )
    return cursor.rowcount == 1


def get_retry_state(conn: Any, task_id: str) -> Optional[Dict[str, Any]]:
    ensure_retry_state_schema(conn)
    row = conn.execute(
        "SELECT * FROM acquisition_retry_state WHERE task_id=? AND expires_at>?",
        (str(task_id), time.time()),
    ).fetchone()
    if row is None:
        return None
    data = dict(row) if hasattr(row, "keys") else {
        key: value for key, value in zip((
            "task_id", "import_id", "track_id", "candidates_json",
            "used_sources_json", "exhausted_sources_json", "retry_counts_json",
            "retry_count", "status", "last_error", "expires_at", "created_at",
            "updated_at",
        ), row, strict=True)
    }
    return {
        "task_id": str(data["task_id"]),
        "import_id": str(data["import_id"]),
        "track_id": int(data["track_id"]),
        "candidates": _candidate_list(data["candidates_json"]),
        "used_sources": tuple(_decode(data["used_sources_json"], [])),
        "exhausted_sources": tuple(_decode(data["exhausted_sources_json"], [])),
        "retry_counts": dict(_decode(data["retry_counts_json"], {})),
        "retry_count": int(data["retry_count"] or 0),
        "status": str(data["status"]),
        "last_error": data["last_error"],
        "expires_at": float(data["expires_at"]),
    }


def purge_expired_retry_state(conn: Any, *, now: Optional[float] = None) -> int:
    ensure_retry_state_schema(conn)
    cursor = conn.execute(
        "DELETE FROM acquisition_retry_state WHERE expires_at<=?",
        (time.time() if now is None else float(now),),
    )
    return int(cursor.rowcount)


__all__ = [
    "RETRY_STATE_DDL",
    "RETRY_STATE_TTL_SECONDS",
    "ensure_retry_state_schema",
    "get_retry_state",
    "purge_expired_retry_state",
    "record_candidate_snapshot",
    "update_retry_state",
]
