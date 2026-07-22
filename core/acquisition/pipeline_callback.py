"""Callback from the existing import pipeline into persistent acquisition."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Optional

from utils.logging_config import get_logger


logger = get_logger("acquisition.pipeline_callback")

CHECK_EVENT_TYPES = {
    "quality": "quality_checked",
    "acoustic_id": "acoustic_id_checked",
}
CHECK_STATUSES = frozenset({"passed", "failed", "skipped", "not_run", "error"})


def _context_value(context: Mapping[str, Any], key: str) -> Any:
    value = context.get(key)
    if value not in (None, ""):
        return value
    track_info = context.get("track_info")
    if isinstance(track_info, Mapping):
        return track_info.get(key)
    return None


def _pipeline_correlation(
    conn: Any, context: Mapping[str, Any],
) -> Optional[dict[str, Optional[str]]]:
    """Resolve native-import or correlated legacy-grab business ids."""
    import_id = _context_value(context, "_acquisition_import_id")
    if import_id:
        row = conn.execute(
            """SELECT request_id, candidate_id, download_id
                 FROM acquisition_imports WHERE id=?""",
            (str(import_id),),
        ).fetchone()
        if row is not None:
            return {
                "request_id": str(row[0]),
                "candidate_id": str(row[1]) if row[1] else None,
                "download_id": str(row[2]),
                "import_id": str(import_id),
            }

    from core.acquisition.manual_grab import GRAB_MARKER

    download_id = _context_value(context, GRAB_MARKER)
    if not download_id:
        return None
    from core.acquisition.grabs import get_grab

    grab = get_grab(conn, str(download_id))
    if grab is None or not grab.get("acquisition_request_id"):
        return None
    return {
        "request_id": str(grab["acquisition_request_id"]),
        "candidate_id": (
            str(grab["release_candidate_id"])
            if grab.get("release_candidate_id")
            else None
        ),
        "download_id": str(download_id),
        "import_id": None,
    }


def _history_event_exists(
    conn: Any,
    event_type: str,
    *,
    request_id: str,
    download_id: str,
) -> bool:
    from core.acquisition.history import ensure_acquisition_history_schema

    ensure_acquisition_history_schema(conn)
    return conn.execute(
        """SELECT 1 FROM acquisition_history
            WHERE event_type=? AND request_id=? AND download_id=? LIMIT 1""",
        (event_type, request_id, download_id),
    ).fetchone() is not None


def notify_pipeline_import_started(
    context: Mapping[str, Any],
    *,
    connection_factory: Optional[Callable[[], Any]] = None,
) -> bool:
    """Journal entry into the shared pipeline once per correlated grab.

    Acquisition-native bundle imports already record this before dispatch;
    correlated manual/scheduled legacy grabs do not. The existence check makes
    the callback safe for both and idempotent across duplicate dispatches.
    """
    if not _context_value(context, "_acquisition_import_id"):
        from core.acquisition.manual_grab import GRAB_MARKER

        if not _context_value(context, GRAB_MARKER):
            return False
    if connection_factory is None:
        from database.music_database import get_database

        connection_factory = get_database()._get_connection

    conn = connection_factory()
    try:
        correlation = _pipeline_correlation(conn, context)
        if correlation is None:
            return False
        if not _history_event_exists(
            conn,
            "import_started",
            request_id=correlation["request_id"],
            download_id=correlation["download_id"],
        ):
            from core.acquisition.history import record_history_event

            record_history_event(
                conn,
                "import_started",
                request_id=correlation["request_id"],
                candidate_id=correlation["candidate_id"],
                download_id=correlation["download_id"],
                payload={
                    "pipeline": "main",
                    "import_id": correlation["import_id"],
                    "track_id": _context_value(context, "_acquisition_track_id"),
                },
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        logger.exception("Acquisition pipeline-start journal failed")
        return False
    finally:
        conn.close()


def notify_pipeline_check_result(
    context: Mapping[str, Any],
    *,
    check: str,
    status: str,
    reason_code: Optional[str] = None,
    message: Optional[str] = None,
    actor: str = "system",
    payload: Optional[Mapping[str, Any]] = None,
    connection_factory: Optional[Callable[[], Any]] = None,
) -> bool:
    """Append one structured quality/Acoustic-ID verdict.

    Multiple attempts intentionally produce multiple events. Ordinary imports
    carry no acquisition marker and remain a zero-write no-op.
    """
    normalized_check = str(check or "").strip().lower().replace("acoustid", "acoustic_id")
    normalized_status = str(status or "").strip().lower()
    normalized_actor = str(actor or "system").strip().lower()
    if normalized_check not in CHECK_EVENT_TYPES:
        logger.warning("Unknown acquisition pipeline check: %s", check)
        return False
    if normalized_status not in CHECK_STATUSES:
        logger.warning("Unknown acquisition pipeline check status: %s", status)
        return False
    if normalized_actor not in {"system", "user"}:
        logger.warning("Unknown acquisition pipeline check actor: %s", actor)
        return False
    if not _context_value(context, "_acquisition_import_id"):
        from core.acquisition.manual_grab import GRAB_MARKER

        if not _context_value(context, GRAB_MARKER):
            return False
    if connection_factory is None:
        from database.music_database import get_database

        connection_factory = get_database()._get_connection

    conn = connection_factory()
    try:
        correlation = _pipeline_correlation(conn, context)
        if correlation is None:
            return False
        event_payload = dict(payload or {})
        event_payload.update({
            "check": normalized_check,
            "status": normalized_status,
            "actor": normalized_actor,
            "pipeline": "main",
            "import_id": correlation["import_id"],
            "track_id": _context_value(context, "_acquisition_track_id"),
        })
        from core.acquisition.history import record_history_event

        record_history_event(
            conn,
            CHECK_EVENT_TYPES[normalized_check],
            request_id=correlation["request_id"],
            candidate_id=correlation["candidate_id"],
            download_id=correlation["download_id"],
            reason_code=(reason_code or f"{normalized_check}_{normalized_status}"),
            message=message,
            payload=event_payload,
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        logger.exception(
            "Acquisition %s check journal failed (%s)",
            normalized_check,
            normalized_status,
        )
        return False
    finally:
        conn.close()


def notify_pipeline_import_success(
    context: Mapping[str, Any],
    *,
    connection_factory: Optional[Callable[[], Any]] = None,
) -> bool:
    """Journal a shared-pipeline success when it belongs to an acquisition.

    Ordinary legacy imports have no acquisition markers and remain untouched.
    The markers survive quarantine sidecar serialization, so a later manual
    approval reaches this same callback after all non-approved checks pass.
    """
    import_id = _context_value(context, "_acquisition_import_id")
    relative_path = _context_value(context, "_acquisition_relative_path")
    track_id = _context_value(context, "_acquisition_track_id")
    final_path = context.get("_final_processed_path") or context.get("_final_path")
    if not import_id:
        return False
    if not relative_path or not track_id or not final_path:
        logger.warning(
            "Acquisition pipeline callback missing completion context for %s",
            import_id,
        )
        return False

    if connection_factory is None:
        from database.music_database import get_database
        connection_factory = get_database()._get_connection

    conn = connection_factory()
    try:
        from core.acquisition.imports import record_pipeline_file_completed
        record_pipeline_file_completed(
            conn,
            str(import_id),
            relative_path=str(relative_path),
            final_path=str(final_path),
            track_id=int(track_id),
        )
        conn.commit()
        return True
    except (KeyError, ValueError) as exc:
        conn.rollback()
        logger.warning(
            "Acquisition pipeline completion rejected for %s: %s",
            import_id,
            exc,
        )
        return False
    except Exception:
        conn.rollback()
        logger.exception(
            "Acquisition pipeline completion failed for %s", import_id)
        return False
    finally:
        conn.close()


def notify_pipeline_import_quarantined(
    context: Mapping[str, Any],
    *,
    trigger: str,
    reason: str,
    connection_factory: Optional[Callable[[], Any]] = None,
) -> bool:
    """Journal quarantine only for files dispatched by Acquisition."""
    import_id = _context_value(context, "_acquisition_import_id")
    relative_path = _context_value(context, "_acquisition_relative_path")
    track_id = _context_value(context, "_acquisition_track_id")
    if not import_id:
        return False
    if not relative_path or not track_id:
        logger.warning(
            "Acquisition quarantine callback missing context for %s", import_id)
        return False

    if connection_factory is None:
        from database.music_database import get_database
        connection_factory = get_database()._get_connection

    conn = connection_factory()
    try:
        from core.acquisition.imports import record_pipeline_file_quarantined
        record_pipeline_file_quarantined(
            conn,
            str(import_id),
            relative_path=str(relative_path),
            track_id=int(track_id),
            trigger=str(trigger or "unknown"),
            reason=str(reason or "Shared pipeline quarantine"),
        )
        conn.commit()
        return True
    except (KeyError, ValueError) as exc:
        conn.rollback()
        logger.warning(
            "Acquisition pipeline quarantine rejected for %s: %s",
            import_id,
            exc,
        )
        return False
    except Exception:
        conn.rollback()
        logger.exception(
            "Acquisition pipeline quarantine failed for %s", import_id)
        return False
    finally:
        conn.close()


def notify_force_quarantine_auto_approved(
    context: Mapping[str, Any],
    *,
    reason_code: str,
    trigger: str,
    reason: str,
    connection_factory: Optional[Callable[[], Any]] = None,
) -> bool:
    """Consume an exact Force-Grab approval at a shared pipeline guard.

    The serialized context is only correlation data. Authorization comes from
    the immutable forced decision run linked to the acquisition grab, so a
    forged or stale context cannot broaden the approved reason.
    """
    import_id = _context_value(context, "_acquisition_import_id")
    relative_path = _context_value(context, "_acquisition_relative_path")
    track_id = _context_value(context, "_acquisition_track_id")
    code = str(reason_code or "").strip().lower()
    if not import_id or not relative_path or not track_id or not code:
        return False
    if len(code) > 100:
        return False

    if connection_factory is None:
        from database.music_database import get_database
        connection_factory = get_database()._get_connection

    conn = connection_factory()
    try:
        row = conn.execute(
            """SELECT ai.request_id, ai.candidate_id, ai.download_id
                 FROM acquisition_imports ai
                 JOIN acquisition_grabs grab
                   ON grab.download_id=ai.download_id
                 JOIN candidate_decision_runs run
                   ON run.id=grab.decision_run_id
                  AND run.request_id=ai.request_id
                  AND run.candidate_id=ai.candidate_id
                 JOIN candidate_decisions decision
                   ON decision.run_id=run.id
                  AND decision.candidate_id=ai.candidate_id
                WHERE ai.id=?
                  AND run.forced=1
                  AND decision.severity='rejection'
                  AND decision.overridable=1
                  AND lower(decision.code)=?
                LIMIT 1""",
            (str(import_id), code),
        ).fetchone()
        if row is None:
            return False

        from core.acquisition.imports import get_import
        record = get_import(conn, str(import_id))
        expected = {
            (
                str(item.get("relative_path") or "").replace("\\", "/"),
                int(item.get("track_id") or 0),
            )
            for item in (record.matches if record else ())
            if isinstance(item, Mapping)
        }
        key = (
            str(relative_path).strip().replace("\\", "/"),
            int(track_id),
        )
        if record is None or record.status != "importing" or key not in expected:
            return False

        from core.acquisition.history import record_history_event
        record_history_event(
            conn,
            "force_quarantine_auto_approved",
            request_id=str(row[0]),
            candidate_id=str(row[1]),
            download_id=str(row[2]),
            reason_code=code,
            message=str(reason or "Shared pipeline rejection auto-approved"),
            payload={
                "import_id": str(import_id),
                "relative_path": key[0],
                "track_id": key[1],
                "trigger": str(trigger or "unknown"),
            },
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        logger.exception(
            "Force-Grab quarantine approval lookup failed for %s", import_id)
        return False
    finally:
        conn.close()


def notify_pipeline_retry_exhausted(
    context: Mapping[str, Any],
    *,
    error: str,
    connection_factory: Optional[Callable[[], Any]] = None,
) -> bool:
    """Fail and blocklist an Acquisition release after legacy retries end."""
    import_id = _context_value(context, "_acquisition_import_id")
    if not import_id:
        return False
    if connection_factory is None:
        from database.music_database import get_database
        connection_factory = get_database()._get_connection

    conn = connection_factory()
    try:
        from core.acquisition.imports import record_import_failure
        record_import_failure(
            conn,
            str(import_id),
            error=str(error or "Shared pipeline exhausted all candidates"),
            failure_kind="candidate",
            reason_code="pipeline_retry_exhausted",
        )
        conn.commit()
        return True
    except (KeyError, ValueError) as exc:
        conn.rollback()
        logger.warning(
            "Acquisition retry exhaustion rejected for %s: %s", import_id, exc)
        return False
    except Exception:
        conn.rollback()
        logger.exception(
            "Acquisition retry exhaustion failed for %s", import_id)
        return False
    finally:
        conn.close()


def _close_retry_journal(
    context: Mapping[str, Any],
    *,
    status: str,
    connection_factory: Optional[Callable[[], Any]] = None,
) -> bool:
    """Terminally close the retry journal for one acquisition track."""
    import_id = _context_value(context, "_acquisition_import_id")
    track_id = _context_value(context, "_acquisition_track_id")
    if not import_id or not track_id:
        return False
    if connection_factory is None:
        from database.music_database import get_database
        connection_factory = get_database()._get_connection

    conn = connection_factory()
    try:
        from core.acquisition.retry_state import close_retry_state
        closed = close_retry_state(
            conn,
            status=status,
            import_id=str(import_id),
            track_id=int(track_id),
        )
        conn.commit()
        return bool(closed)
    except Exception:
        conn.rollback()
        logger.exception(
            "Acquisition retry journal close (%s) failed for %s",
            status,
            import_id,
        )
        return False
    finally:
        conn.close()


def notify_manual_grab_import_success(
    context: Mapping[str, Any],
    *,
    connection_factory: Optional[Callable[[], Any]] = None,
) -> bool:
    """Complete a correlated manual grab after shared-pipeline success.

    Manual legacy grabs carry only the grab marker, never an acquisition
    import id; ordinary downloads carry neither and remain untouched. The
    marker survives quarantine sidecar serialization, so a later manual
    approval reaches this callback after the remaining checks pass.
    """
    from core.acquisition.manual_grab import GRAB_MARKER

    download_id = _context_value(context, GRAB_MARKER)
    if not download_id:
        return False
    final_path = context.get("_final_processed_path") or context.get("_final_path")

    if connection_factory is None:
        from database.music_database import get_database
        connection_factory = get_database()._get_connection

    conn = connection_factory()
    try:
        from core.acquisition.grabs import get_grab
        from core.acquisition.history import record_history_event
        from core.acquisition.workflow import record_grab_outcome

        grab = get_grab(conn, str(download_id))
        if grab is None or not grab.get("acquisition_request_id"):
            return False
        if grab.get("status") != "completed":
            record_grab_outcome(
                conn,
                str(download_id),
                completed=True,
                output_path=str(final_path) if final_path else None,
            )
        request_id = str(grab["acquisition_request_id"])
        if not _history_event_exists(
            conn,
            "import_completed",
            request_id=request_id,
            download_id=str(download_id),
        ):
            record_history_event(
                conn,
                "import_completed",
                request_id=request_id,
                candidate_id=grab.get("release_candidate_id"),
                download_id=str(download_id),
                payload={
                    "file_count": 1,
                    "pipeline": "main",
                    "manual_grab": True,
                    "has_output_path": bool(final_path),
                },
            )
        conn.commit()
        return True
    except (KeyError, ValueError) as exc:
        conn.rollback()
        logger.warning(
            "Manual grab completion rejected for %s: %s", download_id, exc)
        return False
    except Exception:
        conn.rollback()
        logger.exception("Manual grab completion failed for %s", download_id)
        return False
    finally:
        conn.close()


def notify_manual_grab_quarantined(
    context: Mapping[str, Any],
    *,
    trigger: str,
    reason: str,
    connection_factory: Optional[Callable[[], Any]] = None,
) -> bool:
    """Journal quarantine of a correlated manual grab as history only.

    The request deliberately stays ``grabbing``: legacy semantics keep the
    file waiting for manual review, and an approval re-enters the shared
    pipeline whose success closes the grab. Stale rows are eventually failed
    by ``manual_grab.fail_stale_correlated_grabs``.
    """
    from core.acquisition.manual_grab import GRAB_MARKER

    download_id = _context_value(context, GRAB_MARKER)
    if not download_id:
        return False

    if connection_factory is None:
        from database.music_database import get_database
        connection_factory = get_database()._get_connection

    conn = connection_factory()
    try:
        from core.acquisition.grabs import get_grab
        from core.acquisition.history import record_history_event
        grab = get_grab(conn, str(download_id)) or {}
        record_history_event(
            conn,
            "import_file_quarantined",
            request_id=grab.get("acquisition_request_id"),
            candidate_id=grab.get("release_candidate_id"),
            download_id=str(download_id),
            reason_code=str(trigger or "unknown")[:100],
            message=str(reason or "Shared pipeline quarantine"),
            payload={"manual_grab": True},
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        logger.exception("Manual grab quarantine journal failed for %s", download_id)
        return False
    finally:
        conn.close()


def notify_correlated_grab_cancelled(
    legacy_download_id: str,
    *,
    connection_factory: Optional[Callable[[], Any]] = None,
) -> bool:
    """Close a cancelled legacy manual/scheduled grab, without affecting it.

    The Downloads endpoint calls this only after its existing client cancel
    succeeded.  The callback intentionally looks up only Roadmap-3
    correlations and delegates the actual state/history transition to the
    established acquisition cancellation workflow.  It is therefore safe to
    call for ordinary or already-terminal downloads as a no-op.
    """
    transfer_id = str(legacy_download_id or "").strip()
    if not transfer_id:
        return False
    if connection_factory is None:
        from database.music_database import get_database
        connection_factory = get_database()._get_connection

    conn = connection_factory()
    try:
        rows = conn.execute(
            """SELECT g.download_id, g.context_json
                 FROM acquisition_grabs g
                 JOIN acquisition_requests r ON r.id=g.acquisition_request_id
                WHERE r.trigger IN ('manual', 'scheduled')
                  AND r.status='grabbing'
                  AND g.status NOT IN ('completed', 'failed', 'cancelled')"""
        ).fetchall()
        grab_id = None
        for row in rows:
            try:
                context = json.loads(row[1] or "{}")
            except (TypeError, ValueError):
                continue
            if context.get("legacy_download_id") == transfer_id:
                grab_id = str(row[0])
                break
        if not grab_id:
            return False

        from core.acquisition.grabs import STATUS_CANCEL_PENDING, update_grab
        from core.acquisition.workflow import record_grab_cancelled

        update_grab(
            conn,
            grab_id,
            status=STATUS_CANCEL_PENDING,
            last_client_state="cancelled_by_user",
        )
        record_grab_cancelled(
            conn, grab_id, client_state="cancelled_by_user")
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        logger.exception("Correlated grab cancellation failed for %s", transfer_id)
        return False
    finally:
        conn.close()


def notify_quarantine_approved(
    context: Mapping[str, Any],
    *,
    connection_factory: Optional[Callable[[], Any]] = None,
) -> bool:
    """End the retry walk when a user approves the quarantined file.

    The approval re-dispatches the shared pipeline itself; after a restart
    the journal must not resurrect an automatic candidate walk for a track
    the user already resolved by hand.
    """
    return _close_retry_journal(
        context, status="approved", connection_factory=connection_factory)


def notify_task_retry_cancelled(
    track_info: Any,
    *,
    connection_factory: Optional[Callable[[], Any]] = None,
) -> bool:
    """End the retry walk when the user cancels the task.

    Cancel must never trigger an automatic restart (docs/library-v2.md §8).
    Ordinary tasks carry no acquisition markers and are a no-op.
    """
    if not isinstance(track_info, Mapping):
        return False
    return _close_retry_journal(
        track_info, status="cancelled", connection_factory=connection_factory)


__all__ = [
    "CHECK_EVENT_TYPES",
    "CHECK_STATUSES",
    "notify_manual_grab_import_success",
    "notify_manual_grab_quarantined",
    "notify_correlated_grab_cancelled",
    "notify_pipeline_check_result",
    "notify_pipeline_import_quarantined",
    "notify_pipeline_import_started",
    "notify_pipeline_import_success",
    "notify_pipeline_retry_exhausted",
    "notify_quarantine_approved",
    "notify_task_retry_cancelled",
]
