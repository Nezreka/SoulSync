"""Crash-safe lifecycle bridge for Quarantine -> Staging recovery.

The filesystem move is journaled before it happens.  The quarantine sidecar is
kept until the database transition commits, so a crash in either half can be
retried without losing the acquisition/manual-grab correlation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

from core.acquisition.history import record_history_event
from core.acquisition.imports import (
    get_import,
    record_recovered_reimport_started,
    record_recovered_to_staging,
)
from core.acquisition.manual_grab import GRAB_MARKER
from utils.logging_config import get_logger


logger = get_logger("acquisition.recovery")

RECOVERY_STATUSES = frozenset({
    "prepared", "recovered", "reimporting", "completed", "failed",
})

QUARANTINE_RECOVERY_DDL = """
CREATE TABLE IF NOT EXISTS acquisition_quarantine_recoveries (
    entry_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    sidecar_path TEXT,
    staged_path TEXT NOT NULL,
    request_id TEXT,
    candidate_id TEXT,
    download_id TEXT,
    import_id TEXT,
    relative_path TEXT,
    track_id INTEGER,
    status TEXT NOT NULL DEFAULT 'prepared',
    error TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    CHECK(status IN ('prepared','recovered','reimporting','completed','failed'))
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_acquisition_quarantine_recovery_path "
    "ON acquisition_quarantine_recoveries(staged_path, status)",
    "CREATE INDEX IF NOT EXISTS idx_acquisition_quarantine_recovery_import "
    "ON acquisition_quarantine_recoveries(import_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_acquisition_quarantine_recovery_download "
    "ON acquisition_quarantine_recoveries(download_id, status)",
)

_COLUMNS = (
    "entry_id", "source_path", "sidecar_path", "staged_path", "request_id",
    "candidate_id", "download_id", "import_id", "relative_path", "track_id",
    "status", "error", "created_at", "updated_at", "completed_at",
)


@dataclass(frozen=True)
class QuarantineRecovery:
    entry_id: str
    source_path: str
    sidecar_path: Optional[str]
    staged_path: str
    request_id: Optional[str]
    candidate_id: Optional[str]
    download_id: Optional[str]
    import_id: Optional[str]
    relative_path: Optional[str]
    track_id: Optional[int]
    status: str
    error: Optional[str]
    created_at: str
    updated_at: str
    completed_at: Optional[str]

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "staged_path": self.staged_path,
            "request_id": self.request_id,
            "download_id": self.download_id,
            "import_id": self.import_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }


def ensure_quarantine_recovery_schema(conn: Any) -> None:
    conn.execute(QUARANTINE_RECOVERY_DDL)
    for sql in _INDEXES:
        conn.execute(sql)


def _from_row(row: Any) -> QuarantineRecovery:
    data = dict(row) if hasattr(row, "keys") else dict(zip(_COLUMNS, row, strict=True))
    return QuarantineRecovery(
        entry_id=str(data["entry_id"]),
        source_path=str(data["source_path"]),
        sidecar_path=data["sidecar_path"],
        staged_path=str(data["staged_path"]),
        request_id=data["request_id"],
        candidate_id=data["candidate_id"],
        download_id=data["download_id"],
        import_id=data["import_id"],
        relative_path=data["relative_path"],
        track_id=int(data["track_id"]) if data["track_id"] is not None else None,
        status=str(data["status"]),
        error=data["error"],
        created_at=str(data["created_at"]),
        updated_at=str(data["updated_at"]),
        completed_at=data["completed_at"],
    )


def get_quarantine_recovery(conn: Any, entry_id: str) -> Optional[QuarantineRecovery]:
    ensure_quarantine_recovery_schema(conn)
    row = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM acquisition_quarantine_recoveries "
        "WHERE entry_id=?",
        (str(entry_id),),
    ).fetchone()
    return _from_row(row) if row is not None else None


def _context_value(context: Mapping[str, Any], key: str) -> Any:
    value = context.get(key)
    if value not in (None, ""):
        return value
    track_info = context.get("track_info")
    return track_info.get(key) if isinstance(track_info, Mapping) else None


def _correlation(conn: Any, context: Mapping[str, Any]) -> Dict[str, Any]:
    import_id = _context_value(context, "_acquisition_import_id")
    if import_id:
        record = get_import(conn, str(import_id))
        if record is not None:
            return {
                "request_id": record.request_id,
                "candidate_id": record.candidate_id,
                "download_id": record.download_id,
                "import_id": record.id,
                "relative_path": _context_value(
                    context, "_acquisition_relative_path"
                ),
                "track_id": _context_value(context, "_acquisition_track_id"),
            }
    download_id = _context_value(context, GRAB_MARKER)
    if download_id:
        from core.acquisition.grabs import get_grab

        grab = get_grab(conn, str(download_id))
        if grab is not None:
            return {
                "request_id": grab.get("acquisition_request_id"),
                "candidate_id": grab.get("release_candidate_id"),
                "download_id": str(download_id),
                "import_id": None,
                "relative_path": None,
                "track_id": None,
            }
    return {}


def prepare_quarantine_recovery(
    conn: Any,
    *,
    entry_id: str,
    source_path: str,
    sidecar_path: Optional[str],
    staged_path: str,
    context: Optional[Mapping[str, Any]] = None,
) -> QuarantineRecovery:
    """Commit the intended move and its correlation before touching disk."""
    ensure_quarantine_recovery_schema(conn)
    existing = get_quarantine_recovery(conn, entry_id)
    if existing is not None:
        if (
            existing.source_path != str(source_path)
            or existing.staged_path != str(staged_path)
        ):
            raise ValueError("quarantine recovery entry already has a different move plan")
        return existing
    correlation = _correlation(conn, dict(context or {}))
    track_id = correlation.get("track_id")
    try:
        track_id = int(track_id) if track_id not in (None, "") else None
    except (TypeError, ValueError):
        track_id = None
    conn.execute(
        """INSERT INTO acquisition_quarantine_recoveries(
               entry_id, source_path, sidecar_path, staged_path, request_id,
               candidate_id, download_id, import_id, relative_path, track_id)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            str(entry_id), str(source_path), str(sidecar_path) if sidecar_path else None,
            str(staged_path), correlation.get("request_id"),
            correlation.get("candidate_id"), correlation.get("download_id"),
            correlation.get("import_id"), correlation.get("relative_path"), track_id,
        ),
    )
    created = get_quarantine_recovery(conn, entry_id)
    if created is None:  # pragma: no cover
        raise RuntimeError("quarantine recovery journal insert disappeared")
    return created


def _event_exists(conn: Any, entry_id: str, download_id: str) -> bool:
    rows = conn.execute(
        """SELECT payload_json FROM acquisition_history
             WHERE event_type='recovered_to_staging' AND download_id=?""",
        (str(download_id),),
    ).fetchall()
    import json

    for row in rows:
        try:
            payload = json.loads(row[0] or "{}")
        except (TypeError, ValueError):
            continue
        if str(payload.get("entry_id") or "") == str(entry_id):
            return True
    return False


def finalize_quarantine_recovery(conn: Any, entry_id: str) -> QuarantineRecovery:
    """Persist the recovered lifecycle after the planned target exists."""
    record = get_quarantine_recovery(conn, entry_id)
    if record is None:
        raise KeyError(f"quarantine recovery not found: {entry_id}")
    if record.status in {"recovered", "reimporting", "completed"}:
        return record
    if record.status != "prepared":
        raise ValueError(f"quarantine recovery cannot finalize while {record.status}")
    if record.import_id:
        record_recovered_to_staging(
            conn,
            record.import_id,
            entry_id=record.entry_id,
            previous_path=record.source_path,
            staged_path=record.staged_path,
        )
    elif record.download_id:
        from core.acquisition.grabs import get_grab, update_grab

        grab = get_grab(conn, record.download_id)
        if grab is not None:
            update_grab(
                conn,
                record.download_id,
                last_client_state="recovered_to_staging",
            )
            if not _event_exists(conn, record.entry_id, record.download_id):
                record_history_event(
                    conn,
                    "recovered_to_staging",
                    request_id=record.request_id,
                    candidate_id=record.candidate_id,
                    download_id=record.download_id,
                    reason_code="manual_quarantine_recovery",
                    payload={
                        "entry_id": record.entry_id,
                        "manual_grab": True,
                        "previous_path": record.source_path,
                        "staged_path": record.staged_path,
                    },
                )
    conn.execute(
        """UPDATE acquisition_quarantine_recoveries
              SET status='recovered', error=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE entry_id=?""",
        (record.entry_id,),
    )
    updated = get_quarantine_recovery(conn, record.entry_id)
    if updated is None:  # pragma: no cover
        raise RuntimeError("quarantine recovery disappeared during finalize")
    return updated


def recover_quarantine_entry_to_staging(
    connection_factory: Callable[[], Any],
    *,
    quarantine_dir: str,
    staging_dir: str,
    entry_id: str,
) -> Optional[QuarantineRecovery]:
    """Journal, move, lifecycle-commit, then remove the sidecar."""
    from core.imports.quarantine import (
        StagingRecoveryPlan,
        execute_staging_recovery,
        finalize_staging_recovery_sidecar,
        plan_recover_to_staging,
    )

    conn = connection_factory()
    try:
        existing = get_quarantine_recovery(conn, entry_id)
    finally:
        conn.close()

    if existing is None:
        plan = plan_recover_to_staging(quarantine_dir, staging_dir, entry_id)
        if plan is None:
            return None
        conn = connection_factory()
        try:
            prepare_quarantine_recovery(
                conn,
                entry_id=plan.entry_id,
                source_path=plan.source_path,
                sidecar_path=plan.sidecar_path,
                staged_path=plan.target_path,
                context=plan.context,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        plan = StagingRecoveryPlan(
            entry_id=existing.entry_id,
            source_path=existing.source_path,
            sidecar_path=existing.sidecar_path,
            target_path=existing.staged_path,
            context={},
        )

    if existing is None or existing.status == "prepared":
        if not execute_staging_recovery(plan):
            return None
        conn = connection_factory()
        try:
            recovered = finalize_quarantine_recovery(conn, entry_id)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        finalize_staging_recovery_sidecar(plan)
        return recovered
    # A crash after the lifecycle commit but before the final unlink leaves a
    # harmless sidecar behind.  Retrying the same recovery is the durable
    # cleanup boundary and must be idempotent as well.
    if existing.status in {"recovered", "reimporting", "completed"}:
        finalize_staging_recovery_sidecar(plan)
    return existing


def _same_path(left: str, right: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
    except (OSError, ValueError):
        return str(left).replace("\\", "/") == str(right).replace("\\", "/")


def attach_recovered_staging_context(
    connection_factory: Callable[[], Any],
    file_path: str,
    context: Mapping[str, Any],
) -> Dict[str, Any]:
    """Restore acquisition markers when the staged file is manually imported."""
    conn = connection_factory()
    try:
        ensure_quarantine_recovery_schema(conn)
        rows = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM acquisition_quarantine_recoveries "
            "WHERE status IN ('recovered','reimporting') ORDER BY created_at"
        ).fetchall()
        recovery = next(
            (_from_row(row) for row in rows if _same_path(row[3], file_path)),
            None,
        )
        if recovery is None:
            return dict(context)
        enriched = dict(context)
        track_info = dict(enriched.get("track_info") or {})
        if recovery.import_id:
            record = get_import(conn, recovery.import_id)
            if record is None:
                return enriched
            relative_path = recovery.relative_path
            track_id = recovery.track_id
            if not relative_path or not track_id:
                candidates = [dict(item) for item in record.matches]
                if len(candidates) == 1:
                    relative_path = candidates[0].get("relative_path")
                    track_id = candidates[0].get("track_id")
            if not relative_path or not track_id:
                return enriched
            record_recovered_reimport_started(
                conn, recovery.import_id, staged_path=file_path,
            )
            markers = {
                "_acquisition_import_id": recovery.import_id,
                "_acquisition_relative_path": str(relative_path),
                "_acquisition_track_id": int(track_id),
            }
            enriched.update(markers)
            track_info.update(markers)
        elif recovery.download_id:
            enriched[GRAB_MARKER] = recovery.download_id
            track_info[GRAB_MARKER] = recovery.download_id
        enriched["_quarantine_recovery_entry_id"] = recovery.entry_id
        enriched["track_info"] = track_info
        conn.execute(
            """UPDATE acquisition_quarantine_recoveries
                  SET status='reimporting', error=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE entry_id=?""",
            (recovery.entry_id,),
        )
        conn.commit()
        return enriched
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_recovered_staging_result(
    connection_factory: Callable[[], Any],
    context: Mapping[str, Any],
    *,
    success: bool,
    error: Optional[str] = None,
) -> bool:
    """Close the move journal after the shared pipeline returns."""
    entry_id = str(context.get("_quarantine_recovery_entry_id") or "").strip()
    if not entry_id:
        return False
    conn = connection_factory()
    try:
        current = get_quarantine_recovery(conn, entry_id)
        if current is None:
            return False
        retryable = bool(
            not success and os.path.isfile(str(current.staged_path or ""))
        )
        conn.execute(
            """UPDATE acquisition_quarantine_recoveries
                  SET status=?, error=?, updated_at=CURRENT_TIMESTAMP,
                      completed_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE completed_at END
                WHERE entry_id=?""",
            (
                "completed" if success else ("recovered" if retryable else "failed"),
                None if success else str(error or "re-import failed")[:2000],
                1 if success else 0,
                entry_id,
            ),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


__all__ = [
    "QUARANTINE_RECOVERY_DDL",
    "QuarantineRecovery",
    "attach_recovered_staging_context",
    "ensure_quarantine_recovery_schema",
    "finalize_quarantine_recovery",
    "get_quarantine_recovery",
    "prepare_quarantine_recovery",
    "record_recovered_staging_result",
    "recover_quarantine_entry_to_staging",
]
