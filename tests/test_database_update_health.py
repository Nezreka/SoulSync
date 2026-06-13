"""Seam tests for the database-update stall watchdog (GitHub #859).

A DB-update job can hang (media-server call with no timeout, DB lock) and sit at
status='running' forever because the worker's finished/error callbacks never
fire. `is_db_update_stalled` is the pure decision that lets the watchdog flip
such a job to 'error' so the UI recovers. These tests pin that decision —
including the conservative cases where it must NOT false-positive.
"""

from __future__ import annotations

from core.database_update_health import (
    DEFAULT_STALL_TIMEOUT_SECONDS,
    is_db_update_stalled,
    stalled_error_message,
)


def _state(**over):
    base = {"status": "running", "phase": "Incremental: scanning",
            "processed": 2, "total": 3, "progress": 66.7, "last_progress_at": 1000.0}
    base.update(over)
    return base


def test_running_and_heartbeat_stale_is_stalled():
    # last tick at t=1000, now=1000+timeout → exactly at the boundary counts.
    now = 1000.0 + DEFAULT_STALL_TIMEOUT_SECONDS
    assert is_db_update_stalled(_state(), now) is True


def test_running_and_heartbeat_fresh_is_not_stalled():
    now = 1000.0 + 5  # ticked 5s ago, well within timeout
    assert is_db_update_stalled(_state(), now) is False


def test_just_under_timeout_is_not_stalled():
    now = 1000.0 + DEFAULT_STALL_TIMEOUT_SECONDS - 0.001
    assert is_db_update_stalled(_state(), now) is False


def test_non_running_statuses_never_stall():
    now = 1000.0 + 10_000  # very stale heartbeat
    for status in ("idle", "finished", "error"):
        assert is_db_update_stalled(_state(status=status), now) is False, status


def test_missing_heartbeat_cannot_judge():
    # No usable timestamp → we refuse to kill a job we have no clock for.
    now = 1_000_000.0
    assert is_db_update_stalled(_state(last_progress_at=0), now) is False
    assert is_db_update_stalled(_state(last_progress_at=None), now) is False
    s = _state()
    del s["last_progress_at"]
    assert is_db_update_stalled(s, now) is False


def test_non_positive_timeout_disables_watchdog():
    now = 1000.0 + 10_000
    assert is_db_update_stalled(_state(), now, timeout_seconds=0) is False
    assert is_db_update_stalled(_state(), now, timeout_seconds=-1) is False


def test_bad_inputs_are_safe():
    assert is_db_update_stalled(None, 123.0) is False
    assert is_db_update_stalled("not a dict", 123.0) is False
    assert is_db_update_stalled(_state(last_progress_at="oops"), 1_000_000.0) is False


def test_custom_timeout_respected():
    now = 1000.0 + 120
    assert is_db_update_stalled(_state(), now, timeout_seconds=60) is True
    assert is_db_update_stalled(_state(), now, timeout_seconds=180) is False


def test_stalled_message_is_informative():
    now = 1000.0 + 360
    msg = stalled_error_message(_state(phase="Incremental: scanning"), now)
    assert "stuck" in msg.lower()
    assert "360s" in msg
    assert "Incremental: scanning" in msg


def test_issue_859_frozen_running_job_is_caught():
    """Regression: the reported state — running, frozen at 2/3 (66.7%), heartbeat
    long stale — is detected as stalled so the card can self-heal."""
    state = _state(status="running", processed=2, total=3, progress=66.7,
                   last_progress_at=1000.0)
    now = 1000.0 + DEFAULT_STALL_TIMEOUT_SECONDS + 30
    assert is_db_update_stalled(state, now) is True
    # And a healthy job that's actively ticking is left alone.
    assert is_db_update_stalled(_state(last_progress_at=now - 2), now) is False
