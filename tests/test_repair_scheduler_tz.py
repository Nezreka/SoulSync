"""#885: repair-job scheduling must be timezone-independent.

`finished_at` is written by SQLite's CURRENT_TIMESTAMP (always UTC), but the
scheduler compared it against `datetime.now()` (naive LOCAL). With TZ=Australia/
Sydney (UTC+11) every job looked ~11h stale and ran every poll; America/New_York
(behind UTC) masked it. The fix parses finished_at as UTC and compares against a
UTC now, so the machine timezone no longer leaks into elapsed time.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from core.repair_worker import RepairWorker


# ── pure helper ───────────────────────────────────────────────────────────────
def test_hours_since_treats_naive_timestamp_as_utc():
    now = datetime(2026, 6, 18, 6, 0, 0, tzinfo=timezone.utc)
    # SQLite CURRENT_TIMESTAMP style: UTC, no tz suffix.
    assert RepairWorker._hours_since('2026-06-18 00:00:00', now) == pytest.approx(6.0)


def test_hours_since_handles_aware_timestamp():
    now = datetime(2026, 6, 18, 6, 0, 0, tzinfo=timezone.utc)
    assert RepairWorker._hours_since('2026-06-18T00:00:00+00:00', now) == pytest.approx(6.0)


def test_hours_since_recent_is_near_zero():
    now = datetime(2026, 6, 18, 0, 0, 30, tzinfo=timezone.utc)
    assert RepairWorker._hours_since('2026-06-18 00:00:00', now) == pytest.approx(30 / 3600, abs=1e-6)


# ── the #885 repro: a just-run job is never due, regardless of timezone ────────
def _set_tz(monkeypatch, tz):
    monkeypatch.setenv('TZ', tz)
    try:
        time.tzset()
    except AttributeError:
        pytest.skip('time.tzset() unavailable on this platform')


def test_just_run_job_not_due_under_any_timezone(monkeypatch):
    w = RepairWorker.__new__(RepairWorker)
    w._jobs = {'cache_evictor': object()}
    monkeypatch.setattr(RepairWorker, 'get_job_config',
                        lambda self, jid: {'enabled': True, 'interval_hours': 6})
    # Job finished "now" in UTC (exactly how CURRENT_TIMESTAMP records it).
    finished = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    monkeypatch.setattr(RepairWorker, '_get_last_run',
                        lambda self, jid: {'finished_at': finished})

    # Australia/Sydney is the exact repro; check the Americas + UTC too.
    for tz in ('Australia/Sydney', 'America/New_York', 'UTC'):
        _set_tz(monkeypatch, tz)
        assert w._pick_next_job() is None, f"just-run job wrongly due under TZ={tz}"


def test_stale_job_is_still_picked_under_sydney(monkeypatch):
    # Sanity: a genuinely-overdue job IS picked (we didn't break due-detection).
    w = RepairWorker.__new__(RepairWorker)
    w._jobs = {'cache_evictor': object()}
    monkeypatch.setattr(RepairWorker, 'get_job_config',
                        lambda self, jid: {'enabled': True, 'interval_hours': 6})
    # Finished ~10h ago in UTC.
    old = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    monkeypatch.setattr(RepairWorker, '_get_last_run',
                        lambda self, jid: {'finished_at': old})
    _set_tz(monkeypatch, 'Australia/Sydney')
    assert w._pick_next_job() == 'cache_evictor'
