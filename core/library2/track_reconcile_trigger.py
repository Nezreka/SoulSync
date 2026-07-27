"""Debounced post-import track-identity reconciliation.

Imports arrive per file, while provider tracklists are album-scoped. A short
debounce therefore turns a multi-track download into one exact-provider fetch
per album and keeps metadata network work out of the import transaction.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Iterable, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.track_reconcile_trigger")

DEFAULT_DEBOUNCE_SECONDS = 5.0

_lock = threading.Lock()
_timer: Optional[threading.Timer] = None
_running = False
_pending_album_ids: set[int] = set()
_database: Any = None
_idle = threading.Event()
_idle.set()


def _config_value(config_manager: Any, key: str, default: Any) -> Any:
    getter = getattr(config_manager, "get", None)
    if getter is None:
        return default
    value = getter(key, default)
    return default if value is None else value


def run_album_track_reconcile(
    database: Any,
    *,
    album_ids: Iterable[int],
) -> Dict[str, int]:
    """Run exact-provider reconciliation, committing each album separately."""
    from core.library2.track_identity_reconcile import (
        reconcile_album_track_provider_ids,
    )

    totals = {
        "albums": 0,
        "failed": 0,
        "providers": 0,
        "matched": 0,
        "ids_added": 0,
        "conflicts": 0,
        "skipped": 0,
    }
    for album_id in sorted(set(int(value) for value in album_ids)):
        conn = database._get_connection()
        try:
            stats = reconcile_album_track_provider_ids(conn, album_id)
            conn.commit()
            totals["albums"] += 1
            for key in ("providers", "matched", "ids_added", "conflicts", "skipped"):
                totals[key] += int(stats.get(key, 0))
        except Exception as exc:  # noqa: BLE001 - background healing is fail-open
            conn.rollback()
            totals["failed"] += 1
            logger.warning(
                "Post-import track identity reconcile failed for album %s: %s",
                album_id,
                exc,
            )
        finally:
            conn.close()
    return totals


def schedule_album_track_reconcile(
    database: Any,
    album_id: int,
    config_manager: Any = None,
    *,
    runner: Optional[Callable[..., Any]] = None,
) -> bool:
    """Add one album to the next debounced run; never raises into an import."""
    global _database, _timer
    try:
        album_id = int(album_id)
        if album_id <= 0:
            return False
        if not _coerce_bool(_config_value(
            config_manager,
            "library_v2.track_identity_reconcile.auto_after_import",
            True,
        )):
            return False
        delay = float(_config_value(
            config_manager,
            "library_v2.track_identity_reconcile.debounce_seconds",
            DEFAULT_DEBOUNCE_SECONDS,
        ))
    except Exception as exc:  # noqa: BLE001
        logger.debug("track identity reconcile not scheduled: %s", exc)
        return False

    with _lock:
        _pending_album_ids.add(album_id)
        _database = database
        if _timer is not None:
            return False
        _arm_locked(delay, runner)
    return True


def schedule_file_track_reconcile(
    database: Any,
    file_id: int,
    config_manager: Any = None,
    *,
    runner: Optional[Callable[..., Any]] = None,
) -> bool:
    """Resolve a freshly linked file's album and enqueue it."""
    try:
        conn = database._get_connection()
        try:
            row = conn.execute(
                """SELECT t.album_id
                     FROM lib2_track_files tf
                     JOIN lib2_tracks t ON t.id=tf.track_id
                    WHERE tf.id=?""",
                (int(file_id),),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return False
        return schedule_album_track_reconcile(
            database,
            row["album_id"],
            config_manager,
            runner=runner,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("track identity reconcile file lookup failed: %s", exc)
        return False


def _arm_locked(delay: float, runner: Optional[Callable[..., Any]]) -> None:
    """Start a timer. Caller holds ``_lock``."""
    global _timer
    _idle.clear()
    timer = threading.Timer(
        max(0.0, delay),
        _fire,
        kwargs={"delay": delay, "runner": runner},
    )
    timer.daemon = True
    timer.name = "lib2-track-identity-reconcile"
    _timer = timer
    timer.start()


def _fire(*, delay: float, runner: Optional[Callable[..., Any]]) -> None:
    global _running, _timer
    with _lock:
        _timer = None
        if _running:
            _arm_locked(max(delay, 0.05), runner)
            return
        album_ids = sorted(_pending_album_ids)
        _pending_album_ids.clear()
        database = _database
        _running = True
    try:
        stats = (runner or run_album_track_reconcile)(
            database,
            album_ids=album_ids,
        )
        logger.info("Post-import track identity reconcile: %s", stats)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Post-import track identity reconcile failed: %s", exc)
    finally:
        with _lock:
            _running = False
            if _pending_album_ids and _timer is None:
                _arm_locked(max(delay, 0.05), runner)
            elif _timer is None:
                _idle.set()


def _coerce_bool(value: Any) -> bool:
    from core.library2.feature import coerce_bool

    return coerce_bool(value, True)


def wait_for_idle(timeout: float = 5.0) -> bool:
    return _idle.wait(timeout)


def reset_for_tests() -> None:
    global _database, _running, _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
        _timer = None
        _running = False
        _pending_album_ids.clear()
        _database = None
        _idle.set()


__all__ = [
    "DEFAULT_DEBOUNCE_SECONDS",
    "reset_for_tests",
    "run_album_track_reconcile",
    "schedule_album_track_reconcile",
    "schedule_file_track_reconcile",
    "wait_for_idle",
]
