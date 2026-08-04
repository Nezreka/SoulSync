"""Post-import trigger for the unmapped-artist reconcile (status.md §28).

An import is the moment new native artists appear: featured credits, wishlist
rows and discography discoveries are all born with ``legacy_artist_id = NULL``
and no provider id, so until something resolves them their chips stay
``pending`` and they carry no artwork (``core.library2.native_enrich``). The
user asked for that healing pass to run on its own after an import instead of
waiting for the maintenance button.

Two properties make that safe, and this module exists to own both:

* **Coalescing.** The hook sits in the per-file post-import side effects, so a
  30-track album import calls it 30 times. A debounce window turns the burst
  into one run.
* **Backoff.** The run passes ``cooldown_hours`` into the reconcile, so names
  that no provider can resolve (real collaboration strings) are skipped for a
  window instead of re-hitting every configured provider on every import
  (issues.md §16 Finding 2).

Nothing here may raise into the pipeline: the file is already imported by the
time the hook fires, and a healing pass is never worth failing that.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.unmapped_trigger")

# A week: long enough that a genuinely unresolvable collaboration name costs one
# provider round per week, short enough that a name a provider only just started
# carrying is picked up without the user pressing anything.
DEFAULT_COOLDOWN_HOURS = 168.0
# Long enough to swallow a whole album/playlist import, short enough that a
# single-track download is still healed within a couple of minutes.
DEFAULT_DEBOUNCE_SECONDS = 120.0

_lock = threading.Lock()
_timer: Optional[threading.Timer] = None
_running = False
_idle = threading.Event()
_idle.set()


def _config_value(config_manager: Any, key: str, default: Any) -> Any:
    getter = getattr(config_manager, "get", None)
    if getter is None:
        return default
    value = getter(key, default)
    return default if value is None else value


def run_unmapped_artist_reconcile(*, cooldown_hours: float) -> Dict[str, Any]:
    """Own-connection reconcile pass; the production runner behind the timer."""
    from database.music_database import get_database
    from core.library2.native_enrich import reconcile_unmapped_native_artists

    db = get_database()
    conn = db._get_connection()
    try:
        stats = reconcile_unmapped_native_artists(conn, cooldown_hours=cooldown_hours)
        conn.commit()
    finally:
        conn.close()
    return stats


def schedule_unmapped_artist_reconcile(
    config_manager: Any = None,
    *,
    runner: Optional[Callable[..., Any]] = None,
) -> bool:
    """Arm one debounced reconcile run. ``True`` when this call armed it.

    ``False`` means "nothing to do here": the trigger is switched off, a run is
    already armed for this burst, or the config could not even be read. Callers
    treat the return value as information, never as an error.
    """
    global _timer
    try:
        if not _coerce_bool(_config_value(
            config_manager, "library_v2.unmapped_reconcile.auto_after_import", True,
        )):
            return False
        delay = float(_config_value(
            config_manager, "library_v2.unmapped_reconcile.debounce_seconds",
            DEFAULT_DEBOUNCE_SECONDS,
        ))
        cooldown = float(_config_value(
            config_manager, "library_v2.unmapped_reconcile.cooldown_hours",
            DEFAULT_COOLDOWN_HOURS,
        ))
    except Exception as exc:  # noqa: BLE001 — an import must never fail on this
        logger.debug("unmapped reconcile trigger not scheduled: %s", exc)
        return False

    with _lock:
        if _timer is not None:
            return False  # already armed for this burst
        _arm_locked(delay, cooldown, runner, delay)
    return True


def _arm_locked(
    delay: float,
    cooldown_hours: float,
    runner: Optional[Callable[..., Any]],
    burst_delay: float,
) -> None:
    """Start the debounce timer. Caller holds ``_lock``."""
    global _timer
    _idle.clear()
    timer = threading.Timer(
        max(0.0, delay),
        _fire,
        kwargs={
            "cooldown_hours": cooldown_hours,
            "runner": runner,
            "burst_delay": burst_delay,
        },
    )
    timer.daemon = True
    timer.name = "lib2-unmapped-reconcile"
    _timer = timer
    timer.start()


def _fire(
    *,
    cooldown_hours: float,
    runner: Optional[Callable[..., Any]],
    burst_delay: float,
) -> None:
    global _timer, _running
    with _lock:
        _timer = None
        if _running:
            # A previous burst is still resolving. Re-arm instead of dropping:
            # that run read its candidate list before this burst's artists
            # existed, so silently returning here would leave them unmapped
            # until the next unrelated import.
            _arm_locked(max(burst_delay, 0.05), cooldown_hours, runner, burst_delay)
            return
        _running = True
    try:
        stats = (runner or run_unmapped_artist_reconcile)(cooldown_hours=cooldown_hours)
        logger.info("Post-import unmapped-artist reconcile: %s", stats)
    except Exception as exc:  # noqa: BLE001 — background healing, never fatal
        logger.warning("Post-import unmapped-artist reconcile failed: %s", exc)
    finally:
        with _lock:
            _running = False
            if _timer is None:
                _idle.set()


def _coerce_bool(value: Any) -> bool:
    from core.library2.feature import coerce_bool

    return coerce_bool(value, True)


def wait_for_idle(timeout: float = 5.0) -> bool:
    """Block until no run is armed or in flight (tests, shutdown)."""
    return _idle.wait(timeout)


def reset_for_tests() -> None:
    global _timer, _running
    with _lock:
        if _timer is not None:
            _timer.cancel()
        _timer = None
        _running = False
        _idle.set()


__all__ = [
    "DEFAULT_COOLDOWN_HOURS",
    "DEFAULT_DEBOUNCE_SECONDS",
    "reset_for_tests",
    "run_unmapped_artist_reconcile",
    "schedule_unmapped_artist_reconcile",
    "wait_for_idle",
]
