"""Shared helpers for background workers."""

import threading


def interruptible_sleep(stop_event: threading.Event, seconds: float, step: float = 0.5) -> bool:
    """Sleep in chunks so shutdown can interrupt long waits."""
    if seconds <= 0:
        return stop_event.is_set()

    remaining = float(seconds)
    while remaining > 0 and not stop_event.is_set():
        wait_for = min(step, remaining)
        if stop_event.wait(wait_for):
            break
        remaining -= wait_for
    return stop_event.is_set()
