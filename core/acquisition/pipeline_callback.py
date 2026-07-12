"""Callback from the existing import pipeline into persistent acquisition."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from utils.logging_config import get_logger


logger = get_logger("acquisition.pipeline_callback")


def _context_value(context: Mapping[str, Any], key: str) -> Any:
    value = context.get(key)
    if value not in (None, ""):
        return value
    track_info = context.get("track_info")
    if isinstance(track_info, Mapping):
        return track_info.get(key)
    return None


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


__all__ = [
    "notify_pipeline_import_quarantined",
    "notify_pipeline_import_success",
    "notify_pipeline_retry_exhausted",
]
