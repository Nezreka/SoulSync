"""Durable, short-lived retry journal for acquisition-dispatched tasks.

The legacy worker keeps its candidate walk (cached candidates, used sources,
exhausted source buckets, quarantine-retry counters) in process memory. This
journal captures a redacted copy of exactly that state for tasks that belong
to a persistent acquisition import, so a process restart can rebuild the
legacy task and continue with the NEXT candidate instead of losing the walk
(docs/library-v2.md §8, LIB2-F07).

It is operational state, not history: rows are closed on success, manual
approval, cancellation or final exhaustion, and expire after a short
retention either way. The permanent acquisition history keeps only the
business outcome. Candidate rows are whitelisted fields only — never URLs,
magnet links, tokens or provider secrets.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from core.acquisition.candidates import redact_sensitive_text
from utils.logging_config import get_logger


logger = get_logger("acquisition.retry_state")

RETRY_STATE_TTL_SECONDS = 7 * 24 * 60 * 60

RETRY_STATE_STATUSES = frozenset({
    "active", "completed", "failed", "approved", "cancelled",
})

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
    query_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    last_error TEXT,
    last_progress TEXT,
    expires_at REAL NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(track_id > 0),
    CHECK(status IN ('active','completed','failed','approved','cancelled'))
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_acquisition_retry_state_expiry "
    "ON acquisition_retry_state(expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_acquisition_retry_state_import "
    "ON acquisition_retry_state(import_id, track_id)",
)

# Whitelist of candidate fields required to rebuild the legacy TrackResult
# walk. Everything else (notably ``_source_metadata`` with client URLs) is
# dropped on purpose.
_CANDIDATE_FIELDS = (
    "username", "filename", "size", "bitrate", "duration", "quality",
    "free_upload_slots", "upload_speed", "queue_length", "sample_rate",
    "bit_depth", "artist", "title", "album", "track_number", "confidence",
)


def ensure_retry_state_schema(conn: Any) -> None:
    conn.execute(RETRY_STATE_DDL)
    for sql in _INDEXES:
        conn.execute(sql)


@dataclass(frozen=True)
class RetryState:
    task_id: str
    import_id: str
    track_id: int
    candidates: Tuple[Dict[str, Any], ...]
    used_sources: Tuple[str, ...]
    exhausted_sources: Tuple[str, ...]
    retry_counts: Dict[str, int]
    retry_count: int
    query_count: int
    status: str
    last_error: Optional[str]
    last_progress: Optional[str]
    expires_at: float


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(raw: Any, fallback: Any) -> Any:
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def redact_candidate(candidate: Any) -> Dict[str, Any]:
    """Keep only whitelisted scalar fields of one search candidate."""
    payload: Dict[str, Any] = {}
    for name in _CANDIDATE_FIELDS:
        if isinstance(candidate, Mapping):
            value = candidate.get(name)
        else:
            value = getattr(candidate, name, None)
        if isinstance(value, (str, int, float, bool)):
            payload[name] = value
    return payload


def redact_candidates(candidates: Any) -> Tuple[Dict[str, Any], ...]:
    redacted = []
    for candidate in candidates or ():
        payload = redact_candidate(candidate)
        if payload.get("username") and payload.get("filename"):
            redacted.append(payload)
    return tuple(redacted)


def restore_candidates(items: Any) -> list:
    """Rebuild TrackResult objects the legacy candidate walk can consume."""
    from core.download_plugins.types import TrackResult

    results = []
    for item in items or ():
        if not isinstance(item, Mapping):
            continue
        username = item.get("username")
        filename = item.get("filename")
        if not username or not filename:
            continue
        try:
            result = TrackResult(
                username=str(username),
                filename=str(filename),
                size=int(item.get("size") or 0),
                bitrate=item.get("bitrate"),
                duration=item.get("duration"),
                quality=str(item.get("quality") or "unknown"),
                free_upload_slots=int(item.get("free_upload_slots") or 0),
                upload_speed=int(item.get("upload_speed") or 0),
                queue_length=int(item.get("queue_length") or 0),
                sample_rate=item.get("sample_rate"),
                bit_depth=item.get("bit_depth"),
                artist=item.get("artist"),
                title=item.get("title"),
                album=item.get("album"),
                track_number=item.get("track_number"),
            )
        except (TypeError, ValueError):
            continue
        # The candidate walk reads .confidence directly (sort key + logs).
        result.confidence = float(item.get("confidence") or 0.0)
        results.append(result)
    return results


def _sorted_strings(values: Any) -> Tuple[str, ...]:
    return tuple(sorted({str(item) for item in (values or ()) if item}))


def journal_retry_snapshot(
    conn: Any,
    *,
    task_id: str,
    import_id: str,
    track_id: int,
    candidates: Any = (),
    used_sources: Any = (),
    exhausted_sources: Any = (),
    retry_counts: Optional[Mapping[str, int]] = None,
    retry_count: int = 0,
    query_count: int = 0,
    last_error: Optional[str] = None,
    last_progress: Optional[str] = None,
    ttl_seconds: int = RETRY_STATE_TTL_SECONDS,
    now: Optional[float] = None,
) -> None:
    """Upsert the full redacted retry state for one acquisition task.

    A closed row is never reopened — task ids embed import and track identity,
    so a conflicting closed row means the walk already ended.
    """
    ensure_retry_state_schema(conn)
    timestamp = float(now) if now is not None else time.time()
    counts = {
        str(key): int(value)
        for key, value in dict(retry_counts or {}).items()
    }
    safe_error = redact_sensitive_text(last_error) if last_error else None
    conn.execute(
        """INSERT INTO acquisition_retry_state(
               task_id, import_id, track_id, candidates_json,
               used_sources_json, exhausted_sources_json, retry_counts_json,
               retry_count, query_count, status, last_error, last_progress,
               expires_at)
           VALUES(?,?,?,?,?,?,?,?,?,'active',?,?,?)
           ON CONFLICT(task_id) DO UPDATE SET
               candidates_json=excluded.candidates_json,
               used_sources_json=excluded.used_sources_json,
               exhausted_sources_json=excluded.exhausted_sources_json,
               retry_counts_json=excluded.retry_counts_json,
               retry_count=excluded.retry_count,
               query_count=excluded.query_count,
               last_error=excluded.last_error,
               last_progress=excluded.last_progress,
               expires_at=excluded.expires_at,
               updated_at=CURRENT_TIMESTAMP
           WHERE acquisition_retry_state.status='active'
        """,
        (
            str(task_id),
            str(import_id),
            int(track_id),
            _json(list(redact_candidates(candidates))),
            _json(list(_sorted_strings(used_sources))),
            _json(list(_sorted_strings(exhausted_sources))),
            _json(counts),
            max(int(retry_count), 0),
            max(int(query_count), 0),
            safe_error,
            str(last_progress)[:200] if last_progress else None,
            timestamp + max(int(ttl_seconds), 3600),
        ),
    )


def update_retry_progress(
    conn: Any,
    task_id: str,
    *,
    used_sources: Any = None,
    last_progress: Optional[str] = None,
) -> bool:
    """Light-weight progress update; no-op unless an active row exists."""
    ensure_retry_state_schema(conn)
    assignments = ["updated_at=CURRENT_TIMESTAMP"]
    params: list = []
    if used_sources is not None:
        assignments.append("used_sources_json=?")
        params.append(_json(list(_sorted_strings(used_sources))))
    if last_progress is not None:
        assignments.append("last_progress=?")
        params.append(str(last_progress)[:200])
    params.append(str(task_id))
    cursor = conn.execute(
        "UPDATE acquisition_retry_state SET "
        + ", ".join(assignments)
        + " WHERE task_id=? AND status='active'",
        params,
    )
    return bool(cursor.rowcount)


def close_retry_state(
    conn: Any,
    *,
    status: str,
    task_id: Optional[str] = None,
    import_id: Optional[str] = None,
    track_id: Optional[int] = None,
    error: Optional[str] = None,
) -> int:
    """Terminally close active rows by task id or import (+ optional track).

    Returns the number of rows closed. Closed rows stay until the purge so a
    late resume attempt can see WHY the walk ended instead of recreating it.
    """
    if status not in RETRY_STATE_STATUSES or status == "active":
        raise ValueError(f"invalid terminal retry-state status: {status}")
    if not task_id and not import_id:
        raise ValueError("closing retry state requires task_id or import_id")
    ensure_retry_state_schema(conn)
    conditions = ["status='active'"]
    params: list = [status, redact_sensitive_text(error) if error else None]
    if task_id:
        conditions.append("task_id=?")
        params.append(str(task_id))
    if import_id:
        conditions.append("import_id=?")
        params.append(str(import_id))
    if track_id is not None:
        conditions.append("track_id=?")
        params.append(int(track_id))
    cursor = conn.execute(
        "UPDATE acquisition_retry_state SET status=?, "
        "last_error=COALESCE(?, last_error), updated_at=CURRENT_TIMESTAMP "
        "WHERE " + " AND ".join(conditions),
        params,
    )
    return int(cursor.rowcount or 0)


def _from_row(row: Any) -> RetryState:
    data = dict(row)
    counts = _decode(data.get("retry_counts_json"), {})
    return RetryState(
        task_id=str(data["task_id"]),
        import_id=str(data["import_id"]),
        track_id=int(data["track_id"]),
        candidates=tuple(
            dict(item) for item in _decode(data.get("candidates_json"), [])
            if isinstance(item, Mapping)
        ),
        used_sources=_sorted_strings(_decode(data.get("used_sources_json"), [])),
        exhausted_sources=_sorted_strings(
            _decode(data.get("exhausted_sources_json"), [])),
        retry_counts={
            str(key): int(value) for key, value in counts.items()
            if isinstance(value, (int, float))
        },
        retry_count=int(data.get("retry_count") or 0),
        query_count=int(data.get("query_count") or 0),
        status=str(data.get("status") or "active"),
        last_error=data.get("last_error"),
        last_progress=data.get("last_progress"),
        expires_at=float(data.get("expires_at") or 0.0),
    )


def get_retry_state(conn: Any, task_id: str) -> Optional[RetryState]:
    ensure_retry_state_schema(conn)
    row = conn.execute(
        "SELECT * FROM acquisition_retry_state WHERE task_id=?",
        (str(task_id),),
    ).fetchone()
    return _from_row(row) if row is not None else None


def list_active_retry_states(
    conn: Any,
    *,
    import_id: Optional[str] = None,
    now: Optional[float] = None,
    limit: int = 20,
) -> Tuple[RetryState, ...]:
    ensure_retry_state_schema(conn)
    timestamp = float(now) if now is not None else time.time()
    conditions = ["status='active'", "expires_at>?"]
    params: list = [timestamp]
    if import_id:
        conditions.append("import_id=?")
        params.append(str(import_id))
    params.append(max(int(limit), 0))
    rows = conn.execute(
        "SELECT * FROM acquisition_retry_state WHERE "
        + " AND ".join(conditions)
        + " ORDER BY updated_at, task_id LIMIT ?",
        params,
    ).fetchall()
    return tuple(_from_row(row) for row in rows)


def purge_expired_retry_state(conn: Any, *, now: Optional[float] = None) -> int:
    """Drop every expired row — terminal AND abandoned-active alike."""
    ensure_retry_state_schema(conn)
    timestamp = float(now) if now is not None else time.time()
    cursor = conn.execute(
        "DELETE FROM acquisition_retry_state WHERE expires_at<=?",
        (timestamp,),
    )
    return int(cursor.rowcount or 0)


def acquisition_task_ref(track_info: Any) -> Optional[Tuple[str, int]]:
    """Return ``(import_id, track_id)`` when a task belongs to an acquisition.

    Ordinary legacy tasks carry no acquisition markers and return None, which
    keeps every journal hook a cheap dict lookup for them.
    """
    if not isinstance(track_info, Mapping):
        return None
    import_id = track_info.get("_acquisition_import_id")
    track_id = track_info.get("_acquisition_track_id")
    if not import_id or not track_id:
        return None
    try:
        return str(import_id), int(track_id)
    except (TypeError, ValueError):
        return None


__all__ = [
    "RETRY_STATE_STATUSES",
    "RETRY_STATE_TTL_SECONDS",
    "RetryState",
    "acquisition_task_ref",
    "close_retry_state",
    "ensure_retry_state_schema",
    "get_retry_state",
    "journal_retry_snapshot",
    "list_active_retry_states",
    "purge_expired_retry_state",
    "redact_candidate",
    "redact_candidates",
    "restore_candidates",
    "update_retry_progress",
]
