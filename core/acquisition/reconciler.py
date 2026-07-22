"""Evidence-based reconciliation for persistent legacy acquisition grabs.

Legacy/manual and wishlist-worker downloads are persisted before dispatch, but
their live task/context objects do not survive a process restart.  This module
joins the durable correlation with *observations* supplied by the host:

* terminal/active ``download_tasks``;
* ``matched_downloads_context`` post-processing state;
* external-client status snapshots;
* quarantine sidecars;
* durable acquisition imports and final, indexed files.

Analysis is read-only by default.  Applying a report performs only small,
idempotent lifecycle transitions.  Absence is never immediate failure evidence:
an evidence-less row is closed only after ``evidence_ttl_seconds`` and that
transition is a runtime failure, so the release candidate is never blocklisted.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from core.acquisition.grabs import get_grab, patch_grab_context, update_grab
from core.acquisition.history import record_history_event
from core.acquisition.requests import get_request, transition_request
from utils.logging_config import get_logger


logger = get_logger("acquisition.reconciler")

DEFAULT_EVIDENCE_TTL_SECONDS = 24 * 60 * 60
MIN_EVIDENCE_TTL_SECONDS = 60 * 60

_ACTIVE_TASK_STATUSES = {"pending", "queued", "searching", "downloading", "post_processing"}
_FAILED_TASK_STATUSES = {"failed", "not_found", "skipped"}
_COMPLETED_TASK_STATUSES = {"completed", "already_owned"}
_CANCELLED_TASK_STATUSES = {"cancelled", "canceled"}


def _normalized_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return os.path.normcase(os.path.abspath(text)).replace("\\", "/")
    except (OSError, ValueError):
        return text.replace("\\", "/").casefold()


def _decode_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _timestamp(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).replace(
                tzinfo=timezone.utc,
            ).timestamp()
        except ValueError:
            continue
    return None


def _table_exists(conn: Any, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone() is not None


def _known_index_paths(conn: Any) -> set[str]:
    paths: set[str] = set()
    if _table_exists(conn, "lib2_track_files"):
        rows = conn.execute(
            """SELECT path FROM lib2_track_files
                 WHERE path IS NOT NULL AND path<>''
                   AND COALESCE(file_state,'active') NOT IN ('missing_confirmed','deleted')"""
        ).fetchall()
        from core.library2.paths import resolve_lib2_path

        for row in rows:
            if not row[0]:
                continue
            paths.add(_normalized_path(row[0]))
            resolved = resolve_lib2_path(row[0])
            if resolved:
                paths.add(_normalized_path(resolved))
    if _table_exists(conn, "tracks"):
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(tracks)").fetchall()
        }
        if "file_path" in columns:
            rows = conn.execute(
                "SELECT file_path FROM tracks WHERE file_path IS NOT NULL AND file_path<>''"
            ).fetchall()
            from core.library2.paths import resolve_lib2_path

            for row in rows:
                if not row[0]:
                    continue
                paths.add(_normalized_path(row[0]))
                resolved = resolve_lib2_path(row[0])
                if resolved:
                    paths.add(_normalized_path(resolved))
    return paths


def _path_is_indexed(path: Any, known_paths: set[str]) -> bool:
    normalized = _normalized_path(path)
    if not normalized:
        return False
    try:
        if normalized in known_paths and os.path.isfile(str(path)):
            return True
    except OSError:
        pass
    from core.library2.paths import resolve_lib2_path

    resolved = resolve_lib2_path(str(path))
    return bool(
        resolved
        and _normalized_path(resolved) in known_paths
        and os.path.isfile(resolved)
    )


def _lib2_track_has_real_file(conn: Any, track_id: Any) -> bool:
    try:
        track_id = int(track_id)
    except (TypeError, ValueError):
        return False
    if not _table_exists(conn, "lib2_track_files"):
        return False
    rows = conn.execute(
        """SELECT path FROM lib2_track_files
             WHERE track_id=? AND path IS NOT NULL AND path<>''
               AND COALESCE(file_state,'active') NOT IN ('missing_confirmed','deleted')""",
        (track_id,),
    ).fetchall()
    from core.library2.paths import resolve_lib2_path

    return any(resolve_lib2_path(row[0]) for row in rows)


def _task_download_marker(task: Mapping[str, Any]) -> Optional[str]:
    from core.acquisition.manual_grab import GRAB_MARKER

    marker = task.get(GRAB_MARKER)
    if marker:
        return str(marker)
    track_info = task.get("track_info")
    if isinstance(track_info, Mapping) and track_info.get(GRAB_MARKER):
        return str(track_info[GRAB_MARKER])
    return None


def _context_download_marker(context: Mapping[str, Any]) -> Optional[str]:
    from core.acquisition.manual_grab import GRAB_MARKER

    marker = context.get(GRAB_MARKER)
    if marker:
        return str(marker)
    track_info = context.get("track_info")
    if isinstance(track_info, Mapping) and track_info.get(GRAB_MARKER):
        return str(track_info[GRAB_MARKER])
    return None


def _quarantine_markers(entries: Iterable[Mapping[str, Any]]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for entry in entries:
        context = entry.get("context")
        if not isinstance(context, Mapping):
            continue
        marker = _context_download_marker(context)
        if marker:
            result[marker] = str(entry.get("id") or "")
    return result


def _runtime_task_for(
    download_id: str,
    grab_context: Mapping[str, Any],
    request_options: Mapping[str, Any],
    tasks: Mapping[str, Mapping[str, Any]],
) -> Tuple[Optional[str], Optional[Mapping[str, Any]]]:
    task_id = grab_context.get("legacy_task_id") or request_options.get("legacy_task_id")
    if task_id is not None and str(task_id) in tasks:
        return str(task_id), tasks[str(task_id)]
    for candidate_id, task in tasks.items():
        if _task_download_marker(task) == download_id:
            return str(candidate_id), task
    return None, None


def _matched_context_for(
    download_id: str,
    contexts: Sequence[Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    return next(
        (context for context in contexts if _context_download_marker(context) == download_id),
        None,
    )


def _client_state(observation: Optional[Mapping[str, Any]]) -> str:
    return str((observation or {}).get("state") or "").strip().casefold()


def _active_business_status(raw_state: str) -> Optional[str]:
    state = str(raw_state or "").casefold()
    if any(token in state for token in ("download", "progress", "extract", "verify", "repair")):
        return "downloading"
    if any(token in state for token in ("queue", "pending", "initial", "pause", "search")):
        return "queued"
    return None


@dataclass(frozen=True)
class ReconciliationDecision:
    download_id: str
    request_id: str
    current_status: str
    action: str
    reason: str
    evidence: str
    details: Dict[str, Any]

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "download_id": self.download_id,
            "request_id": self.request_id,
            "current_status": self.current_status,
            "action": self.action,
            "reason": self.reason,
            "evidence": self.evidence,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class AcquisitionReconciliationReport:
    dry_run: bool
    observed: int
    applied: int
    counts: Dict[str, int]
    decisions: Tuple[ReconciliationDecision, ...]

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "observed": self.observed,
            "applied": self.applied,
            "counts": dict(self.counts),
            "decisions": [decision.to_public_dict() for decision in self.decisions],
        }


def _decision(
    row: Mapping[str, Any], action: str, reason: str, evidence: str, **details: Any,
) -> ReconciliationDecision:
    return ReconciliationDecision(
        download_id=str(row["download_id"]),
        request_id=str(row["request_id"]),
        current_status=str(row["grab_status"]),
        action=action,
        reason=reason,
        evidence=evidence,
        details=details,
    )


def _analyse_row(
    conn: Any,
    row: Mapping[str, Any],
    *,
    runtime_tasks: Mapping[str, Mapping[str, Any]],
    matched_contexts: Sequence[Mapping[str, Any]],
    client_observations: Mapping[str, Mapping[str, Any]],
    quarantine_markers: Mapping[str, str],
    known_paths: set[str],
    now: float,
    evidence_ttl_seconds: int,
) -> ReconciliationDecision:
    download_id = str(row["download_id"])
    grab_context = _decode_object(row.get("context_json"))
    request_options = _decode_object(row.get("search_options_json"))

    import_status = row.get("import_status")
    if import_status == "completed":
        import_result = _decode_object(row.get("import_result_json"))
        final_paths = [
            item.get("final_path")
            for item in import_result.get("processed", [])
            if isinstance(item, Mapping) and item.get("final_path")
        ]
        if not final_paths or not all(
            _path_is_indexed(path, known_paths) for path in final_paths
        ):
            return _decision(
                row, "none", "acquisition_import_completed_unindexed",
                "acquisition_import",
                import_id=row.get("import_id"),
                final_path_count=len(final_paths),
            )
        return _decision(
            row, "complete", "acquisition_import_completed_indexed",
            "acquisition_import",
            import_id=row.get("import_id"), final_path_count=len(final_paths),
        )
    if import_status == "failed":
        return _decision(
            row, "fail_runtime", "acquisition_import_failed", "acquisition_import",
            import_id=row.get("import_id"),
        )
    if import_status:
        return _decision(
            row, "none", f"acquisition_import_{import_status}", "acquisition_import",
            import_id=row.get("import_id"),
        )

    quarantine_id = quarantine_markers.get(download_id)
    if quarantine_id is not None:
        action = "none" if row.get("last_client_state") == "quarantined" else "mark_quarantined"
        return _decision(
            row, action, "quarantine_review_pending", "quarantine_sidecar",
            quarantine_entry_id=quarantine_id,
        )

    task_id, task = _runtime_task_for(
        download_id, grab_context, request_options, runtime_tasks,
    )
    if task is not None:
        status = str(task.get("status") or "unknown").strip().casefold()
        final_path = task.get("final_file_path") or task.get("file_path")
        if status in _COMPLETED_TASK_STATUSES:
            track_id = request_options.get("lib2_track_id")
            indexed = _path_is_indexed(final_path, known_paths) or (
                status == "already_owned" and _lib2_track_has_real_file(conn, track_id)
            )
            if indexed:
                return _decision(
                    row, "complete", "runtime_completed_indexed", "download_task",
                    task_id=task_id, status=status, has_final_path=bool(final_path),
                )
            return _decision(
                row, "none", "runtime_completed_unindexed", "download_task",
                task_id=task_id, status=status, has_final_path=bool(final_path),
            )
        if status in _FAILED_TASK_STATUSES:
            return _decision(
                row, "fail_runtime", f"runtime_{status}", "download_task",
                task_id=task_id,
            )
        if status in _CANCELLED_TASK_STATUSES:
            return _decision(
                row, "cancel", "runtime_cancelled", "download_task", task_id=task_id,
            )
        if status in _ACTIVE_TASK_STATUSES:
            business_status = "downloading" if status in {"downloading", "post_processing"} else "queued"
            reason = f"runtime_{status}"
            action = (
                "none"
                if row.get("grab_status") == business_status
                and row.get("last_client_state") == reason
                else "mark_active"
            )
            return _decision(
                row, action, reason, "download_task",
                task_id=task_id, business_status=business_status,
            )

    matched = _matched_context_for(download_id, matched_contexts)
    if matched is not None:
        final_path = matched.get("_final_processed_path") or matched.get("_final_path")
        if _path_is_indexed(final_path, known_paths):
            return _decision(
                row, "complete", "postprocess_completed_indexed", "matched_context",
                has_final_path=True,
            )
        action = (
            "none"
            if row.get("grab_status") == "downloading"
            and row.get("last_client_state") == "postprocess_context_active"
            else "mark_active"
        )
        return _decision(
            row, action, "postprocess_context_active", "matched_context",
            business_status="downloading",
        )

    legacy_download_id = str(grab_context.get("legacy_download_id") or "").strip()
    client = client_observations.get(legacy_download_id) if legacy_download_id else None
    if client is not None:
        state = _client_state(client)
        final_path = client.get("file_path") or client.get("save_path")
        if any(token in state for token in ("complete", "succeed", "finished")):
            if _path_is_indexed(final_path, known_paths):
                return _decision(
                    row, "complete", "client_completed_indexed", "client_snapshot",
                    client_state=state, has_final_path=True,
                )
            return _decision(
                row, "none", "client_completed_unindexed", "client_snapshot",
                client_state=state, has_final_path=bool(final_path),
            )
        if any(token in state for token in ("fail", "error", "abort")):
            return _decision(
                row, "fail_client", "client_reported_failure", "client_snapshot",
                client_state=state,
            )
        if any(token in state for token in ("cancel", "removed")):
            return _decision(
                row, "cancel", "client_reported_cancelled", "client_snapshot",
                client_state=state,
            )
        business_status = _active_business_status(state)
        if business_status:
            action = (
                "none"
                if row.get("grab_status") == business_status
                and row.get("last_client_state") == state
                else "mark_active"
            )
            return _decision(
                row, action, "client_active", "client_snapshot",
                client_state=state, business_status=business_status,
            )
        return _decision(
            row, "none", "client_state_unknown", "client_snapshot", client_state=state,
        )

    output_path = row.get("output_path")
    if _path_is_indexed(output_path, known_paths):
        return _decision(
            row, "complete", "persisted_output_indexed", "acquisition_grab",
            has_final_path=True,
        )

    updated = _timestamp(row.get("updated_at"))
    age_seconds = max(0, int(now - updated)) if updated is not None else None
    if age_seconds is not None and age_seconds >= evidence_ttl_seconds:
        return _decision(
            row, "fail_runtime", "evidence_ttl_expired", "absence_after_ttl",
            age_seconds=age_seconds, ttl_seconds=evidence_ttl_seconds,
        )
    return _decision(
        row, "none", "awaiting_evidence", "none",
        age_seconds=age_seconds, ttl_seconds=evidence_ttl_seconds,
    )


def _history_exists(conn: Any, event_type: str, download_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM acquisition_history WHERE event_type=? AND download_id=? LIMIT 1",
        (event_type, download_id),
    ).fetchone() is not None


def _complete(conn: Any, decision: ReconciliationDecision) -> None:
    grab = get_grab(conn, decision.download_id)
    request = get_request(conn, decision.request_id)
    if not grab or not request or request.status != "grabbing":
        return
    update_grab(
        conn,
        decision.download_id,
        status="completed",
        last_client_state=decision.reason,
        clear_error=True,
    )
    transition_request(
        conn, decision.request_id, "completed", expected_status="grabbing",
    )
    if not _history_exists(conn, "grab_completed", decision.download_id):
        record_history_event(
            conn,
            "grab_completed",
            request_id=decision.request_id,
            candidate_id=grab.get("release_candidate_id"),
            download_id=decision.download_id,
            reason_code=decision.reason,
            payload={"reconciled": True, "evidence": decision.evidence},
        )


def _fail(conn: Any, decision: ReconciliationDecision, *, failure_kind: str) -> None:
    from core.acquisition.workflow import record_grab_outcome

    record_grab_outcome(
        conn,
        decision.download_id,
        completed=False,
        error=f"Reconciled persistent grab: {decision.reason}",
        failure_kind=failure_kind,
    )


def _cancel(conn: Any, decision: ReconciliationDecision) -> None:
    from core.acquisition.workflow import record_grab_cancelled

    update_grab(
        conn,
        decision.download_id,
        status="cancel_pending",
        last_client_state=decision.reason,
    )
    record_grab_cancelled(
        conn, decision.download_id, client_state=decision.reason,
    )


def _apply_decision(conn: Any, decision: ReconciliationDecision) -> bool:
    grab = get_grab(conn, decision.download_id)
    request = get_request(conn, decision.request_id)
    if (
        not grab
        or not request
        or request.status != "grabbing"
        or grab.get("status") in {"completed", "failed", "cancelled"}
    ):
        return False
    if decision.action == "complete":
        _complete(conn, decision)
    elif decision.action == "fail_runtime":
        _fail(conn, decision, failure_kind="runtime")
    elif decision.action == "fail_client":
        _fail(conn, decision, failure_kind="client")
    elif decision.action == "cancel":
        _cancel(conn, decision)
    elif decision.action == "mark_quarantined":
        update_grab(
            conn, decision.download_id, last_client_state="quarantined",
        )
        patch_grab_context(
            conn,
            decision.download_id,
            {"quarantine_entry_id": decision.details.get("quarantine_entry_id")},
        )
    elif decision.action == "mark_active":
        update_grab(
            conn,
            decision.download_id,
            status=str(decision.details.get("business_status") or "downloading"),
            last_client_state=str(
                decision.details.get("client_state") or decision.reason
            ),
            clear_error=True,
        )
    else:
        return False
    return True


def reconcile_persistent_grabs(
    conn: Any,
    *,
    runtime_tasks: Optional[Mapping[str, Mapping[str, Any]]] = None,
    matched_contexts: Iterable[Mapping[str, Any]] = (),
    client_observations: Optional[Mapping[str, Mapping[str, Any]]] = None,
    quarantine_entries: Iterable[Mapping[str, Any]] = (),
    dry_run: bool = True,
    now: Optional[float] = None,
    evidence_ttl_seconds: int = DEFAULT_EVIDENCE_TTL_SECONDS,
    limit: int = 500,
) -> AcquisitionReconciliationReport:
    """Analyse or repair open request-bound grabs from the legacy pipeline."""
    evidence_ttl_seconds = max(
        int(evidence_ttl_seconds), MIN_EVIDENCE_TTL_SECONDS,
    )
    timestamp = time.time() if now is None else float(now)
    runtime_tasks = {
        str(key): dict(value)
        for key, value in (runtime_tasks or {}).items()
        if isinstance(value, Mapping)
    }
    matched_contexts = tuple(
        dict(value) for value in matched_contexts if isinstance(value, Mapping)
    )
    client_observations = {
        str(key): dict(value)
        for key, value in (client_observations or {}).items()
        if isinstance(value, Mapping)
    }
    quarantine = _quarantine_markers(quarantine_entries)
    known_paths = _known_index_paths(conn)

    has_imports = _table_exists(conn, "acquisition_imports")
    import_join = (
        "LEFT JOIN acquisition_imports ai ON ai.download_id=g.download_id"
        if has_imports else ""
    )
    import_columns = (
        ", ai.id AS import_id, ai.status AS import_status, "
        "ai.result_json AS import_result_json" if has_imports
        else ", NULL AS import_id, NULL AS import_status, "
        "NULL AS import_result_json"
    )
    rows = conn.execute(
        f"""SELECT g.download_id, g.status AS grab_status,
                   g.last_client_state, g.output_path, g.context_json,
                   g.updated_at, r.id AS request_id, r.status AS request_status,
                   r.search_options_json, r.trigger
                   {import_columns}
              FROM acquisition_grabs g
              JOIN acquisition_requests r ON r.id=g.acquisition_request_id
              {import_join}
             WHERE r.status='grabbing'
               AND g.status NOT IN ('completed','failed','cancelled')
             ORDER BY g.updated_at, g.id
             LIMIT ?""",
        (max(int(limit), 0),),
    ).fetchall()
    decisions = tuple(
        _analyse_row(
            conn,
            dict(row),
            runtime_tasks=runtime_tasks,
            matched_contexts=matched_contexts,
            client_observations=client_observations,
            quarantine_markers=quarantine,
            known_paths=known_paths,
            now=timestamp,
            evidence_ttl_seconds=evidence_ttl_seconds,
        )
        for row in rows
    )
    applied = 0
    if not dry_run:
        for index, decision in enumerate(decisions):
            savepoint = f"persistent_reconcile_{index}"
            conn.execute(f"SAVEPOINT {savepoint}")
            try:
                if _apply_decision(conn, decision):
                    applied += 1
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            except (KeyError, ValueError) as exc:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                logger.warning(
                    "Persistent grab %s changed during reconciliation: %s",
                    decision.download_id,
                    exc,
                )
    counts = Counter(decision.reason for decision in decisions)
    counts.update(
        {f"action:{action}": count for action, count in Counter(
            decision.action for decision in decisions
        ).items()}
    )
    return AcquisitionReconciliationReport(
        dry_run=bool(dry_run),
        observed=len(decisions),
        applied=applied,
        counts=dict(sorted(counts.items())),
        decisions=decisions,
    )


__all__ = [
    "AcquisitionReconciliationReport",
    "DEFAULT_EVIDENCE_TTL_SECONDS",
    "MIN_EVIDENCE_TTL_SECONDS",
    "ReconciliationDecision",
    "reconcile_persistent_grabs",
]
