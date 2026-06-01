"""Tests for the DB-update / deep-scan monitor decision (stall-based timeout).

Regression: a large library can deep-scan for many hours while progressing
fine. The old monitor used a hard 2-hour TOTAL cap, so it falsely marked a
healthy, still-running scan 'error' (the scan thread kept going uncancelled).
The decision now keys off STALL (no progress), so an actively-progressing scan
never times out no matter how long the whole library takes.
"""

from __future__ import annotations

from core.automation.handlers.database_update import (
    scan_wait_action,
    _STALL_WARNING_SECONDS,
    _STALL_TIMEOUT_SECONDS,
    _ABSOLUTE_CAP_SECONDS,
)


# --- the headline regression -------------------------------------------------


def test_long_but_progressing_scan_never_times_out():
    # 5 hours elapsed total, but progress moved 5s ago -> keep waiting, NOT error.
    assert scan_wait_action(
        status='running', idle_seconds=5, total_seconds=5 * 3600,
    ) == 'continue'


def test_very_long_progressing_scan_still_continues():
    # 12h total, just progressed — old code would have failed at 2h.
    assert scan_wait_action(
        status='running', idle_seconds=2, total_seconds=12 * 3600,
    ) == 'continue'


# --- finished / not-running --------------------------------------------------


def test_finished_when_not_running():
    for st in ('completed', 'error', 'idle', 'finished'):
        assert scan_wait_action(status=st, idle_seconds=0, total_seconds=0) == 'finished'


def test_finished_takes_precedence_even_if_stalled():
    # Task already ended — don't report a stall.
    assert scan_wait_action(
        status='completed', idle_seconds=_STALL_TIMEOUT_SECONDS + 1, total_seconds=10,
    ) == 'finished'


# --- stall warning vs stall timeout ------------------------------------------


def test_warns_after_stall_warning_threshold():
    assert scan_wait_action(
        status='running', idle_seconds=_STALL_WARNING_SECONDS + 1, total_seconds=1000,
    ) == 'warn'


def test_stall_timeout_after_no_progress():
    assert scan_wait_action(
        status='running', idle_seconds=_STALL_TIMEOUT_SECONDS + 1, total_seconds=2000,
    ) == 'stall_timeout'


def test_just_below_warning_keeps_going():
    assert scan_wait_action(
        status='running', idle_seconds=_STALL_WARNING_SECONDS - 1, total_seconds=1000,
    ) == 'continue'


# --- absolute backstop -------------------------------------------------------


def test_absolute_cap_is_last_resort():
    # Even if somehow progressing, a 24h+ wait trips the runaway-loop backstop.
    assert scan_wait_action(
        status='running', idle_seconds=1, total_seconds=_ABSOLUTE_CAP_SECONDS + 1,
    ) == 'abs_timeout'


def test_thresholds_are_ordered_sensibly():
    assert _STALL_WARNING_SECONDS < _STALL_TIMEOUT_SECONDS < _ABSOLUTE_CAP_SECONDS
