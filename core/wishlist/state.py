"""Wishlist processing state helpers."""

from __future__ import annotations

from typing import Any, Callable, Optional


WISHLIST_STUCK_TIMEOUT_SECONDS = 900


def flag_age_seconds(started_at: Optional[float], now: Optional[float] = None) -> float:
    """Return the age of a flag in seconds."""
    if not started_at:
        return 0.0
    if now is None:
        import time

        now = time.time()
    return max(0.0, now - started_at)


def is_flag_recent(
    active: bool,
    started_at: Optional[float],
    timeout_seconds: int = WISHLIST_STUCK_TIMEOUT_SECONDS,
    now: Optional[float] = None,
) -> bool:
    """Return True when an active flag is still within the allowed window."""
    if not active or not started_at:
        return False
    return flag_age_seconds(started_at, now=now) <= timeout_seconds


def is_flag_stuck(
    active: bool,
    started_at: Optional[float],
    timeout_seconds: int = WISHLIST_STUCK_TIMEOUT_SECONDS,
    now: Optional[float] = None,
) -> bool:
    """Return True when an active flag has exceeded the timeout."""
    if not active or not started_at:
        return False
    return flag_age_seconds(started_at, now=now) > timeout_seconds


def is_wishlist_actually_processing(
    active: bool,
    started_at: Optional[float],
    timeout_seconds: int = WISHLIST_STUCK_TIMEOUT_SECONDS,
    now: Optional[float] = None,
    on_stuck: Callable[[], None] | None = None,
    logger: Any | None = None,
) -> bool:
    """Return True only when wishlist processing is active and still recent."""
    if not is_flag_recent(active, started_at, timeout_seconds=timeout_seconds, now=now):
        if active:
            stuck_minutes = flag_age_seconds(started_at, now=now) / 60
            if logger is not None:
                logger.warning(f"[Stuck Detection] Wishlist flag stuck for {stuck_minutes:.1f} minutes - auto-recovering")
            if on_stuck is not None:
                on_stuck()
        return False

    return True


def reset_flag_if_stuck(
    active: bool,
    started_at: Optional[float],
    *,
    timeout_seconds: int = WISHLIST_STUCK_TIMEOUT_SECONDS,
    now: Optional[float] = None,
    label: str = "Wishlist auto-processing",
    logger: Any | None = None,
    reset_callback: Callable[[], None],
) -> bool:
    """Reset a processing flag if it has exceeded the timeout."""
    if not is_flag_stuck(active, started_at, timeout_seconds=timeout_seconds, now=now):
        return False

    stuck_minutes = flag_age_seconds(started_at, now=now) / 60
    if logger is not None:
        logger.info(f"[Stuck Detection] {label} flag has been stuck for {stuck_minutes:.1f} minutes - RESETTING")
    reset_callback()
    return True


def get_wishlist_cycle(db_factory: Callable[[], Any], default_cycle: str = "albums") -> str:
    """Return the stored wishlist cycle, creating the default entry if needed."""
    db = db_factory()
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key = 'wishlist_cycle'")
        row = cursor.fetchone()

        if row:
            return row["value"]

        cursor.execute(
            """
                INSERT OR REPLACE INTO metadata (key, value, updated_at)
                VALUES ('wishlist_cycle', ?, CURRENT_TIMESTAMP)
            """,
            (default_cycle,),
        )
        conn.commit()
        return default_cycle


def set_wishlist_cycle(db_factory: Callable[[], Any], cycle: str) -> None:
    """Persist the wishlist cycle value."""
    db = db_factory()
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
                INSERT OR REPLACE INTO metadata (key, value, updated_at)
                VALUES ('wishlist_cycle', ?, CURRENT_TIMESTAMP)
            """,
            (cycle,),
        )
        conn.commit()


__all__ = [
    "WISHLIST_STUCK_TIMEOUT_SECONDS",
    "flag_age_seconds",
    "is_flag_recent",
    "is_flag_stuck",
    "is_wishlist_actually_processing",
    "reset_flag_if_stuck",
    "get_wishlist_cycle",
    "set_wishlist_cycle",
]
