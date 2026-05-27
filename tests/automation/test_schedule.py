"""Tests for ``core/automation/schedule.py:next_run_at``.

Pure function over (trigger_type, trigger_config, now_utc, default_tz)
so each case can pin a single rule without monkeypatching the system
clock. Covers the existing engine behaviour (interval, daily, weekly)
plus the new ``monthly_time`` shape PR 1 introduces.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from core.automation.schedule import next_run_at


# ---------------------------------------------------------------------------
# Helper — clear, timezone-aware datetime construction in test bodies.
# ---------------------------------------------------------------------------


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """Aware UTC datetime — every ``now_utc`` injection in tests
    flows through this so a stray timezone bug is impossible."""
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Dispatcher: trigger_type routing.
# ---------------------------------------------------------------------------


def test_returns_none_for_unrecognised_trigger_type():
    """Event-based / unknown trigger types are not scheduled — the
    caller should NOT write a next_run for them."""
    now = _utc(2026, 5, 27, 12, 0)
    assert next_run_at('event', {}, now) is None
    assert next_run_at('garbage', {'interval': 1}, now) is None
    assert next_run_at('', {}, now) is None


def test_returns_none_for_non_dict_config():
    """Defensive — callers may pass through whatever ``json.loads``
    returned. Non-dict configs trigger the fallback path which is
    'treat as empty dict + use defaults'."""
    now = _utc(2026, 5, 27, 12, 0)
    # Interval-typed with garbage config falls back to defaults
    # (interval=1, unit='hours') rather than crashing.
    result = next_run_at('schedule', None, now)
    assert result == now + timedelta(hours=1)


# ---------------------------------------------------------------------------
# Interval (``trigger_type='schedule'``)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('unit,seconds_per_unit', [
    ('minutes', 60),
    ('hours',   3600),
    ('days',    86400),
    ('weeks',   86400 * 7),
])
def test_interval_units(unit, seconds_per_unit):
    """Every supported unit scales the interval correctly."""
    now = _utc(2026, 5, 27, 12, 0)
    result = next_run_at('schedule', {'interval': 3, 'unit': unit}, now)
    assert result == now + timedelta(seconds=3 * seconds_per_unit)


def test_interval_unknown_unit_defaults_to_hours():
    """Backward compat with DB rows whose ``unit`` field is missing
    or an unrecognised value — engine's historic behaviour was to
    treat as hours, and we preserve that."""
    now = _utc(2026, 5, 27, 12, 0)
    assert next_run_at('schedule', {'interval': 2, 'unit': 'fortnights'}, now) == now + timedelta(hours=2)
    assert next_run_at('schedule', {'interval': 2}, now) == now + timedelta(hours=2)


def test_interval_clamps_zero_and_negative_to_one():
    """Without a floor a zero/negative interval would schedule for
    the past or fire instantly in a loop. Engine clamped to >=1 via
    ``max(int(interval), 1)``; we preserve that contract."""
    now = _utc(2026, 5, 27, 12, 0)
    assert next_run_at('schedule', {'interval': 0, 'unit': 'hours'}, now) == now + timedelta(hours=1)
    assert next_run_at('schedule', {'interval': -5, 'unit': 'hours'}, now) == now + timedelta(hours=1)


def test_interval_garbage_interval_falls_back_to_one():
    """Non-numeric ``interval`` → default of 1. Survives a JSON column
    where the field was typed as a string by an old admin script."""
    now = _utc(2026, 5, 27, 12, 0)
    assert next_run_at('schedule', {'interval': 'oops', 'unit': 'hours'}, now) == now + timedelta(hours=1)


def test_interval_ignores_tz_field():
    """Interval scheduling is wall-clock-independent — adding 6 hours
    is the same in every timezone. The ``tz`` field is ignored even
    if a caller mistakenly sets it."""
    now = _utc(2026, 5, 27, 12, 0)
    result = next_run_at('schedule',
                         {'interval': 6, 'unit': 'hours', 'tz': 'America/Los_Angeles'},
                         now)
    assert result == now + timedelta(hours=6)


# ---------------------------------------------------------------------------
# Daily (``trigger_type='daily_time'``)
# ---------------------------------------------------------------------------


def test_daily_today_at_future_time_runs_today():
    """It's 12:00 UTC and the schedule says 18:00 UTC — next run is
    today at 18:00, not tomorrow."""
    now = _utc(2026, 5, 27, 12, 0)
    result = next_run_at('daily_time', {'time': '18:00', 'tz': 'UTC'}, now)
    assert result == _utc(2026, 5, 27, 18, 0)


def test_daily_today_at_past_time_runs_tomorrow():
    """It's 18:00 UTC and the schedule says 09:00 UTC — next run is
    tomorrow at 09:00."""
    now = _utc(2026, 5, 27, 18, 0)
    result = next_run_at('daily_time', {'time': '09:00', 'tz': 'UTC'}, now)
    assert result == _utc(2026, 5, 28, 9, 0)


def test_daily_at_exact_target_time_runs_tomorrow():
    """Edge case: schedule fires at exactly 09:00, and ``now`` is
    exactly 09:00. ``<=`` check pushes to tomorrow — otherwise we'd
    immediately reschedule for the present moment and the engine
    would run again in 0s."""
    now = _utc(2026, 5, 27, 9, 0)
    result = next_run_at('daily_time', {'time': '09:00', 'tz': 'UTC'}, now)
    assert result == _utc(2026, 5, 28, 9, 0)


def test_daily_respects_user_timezone_not_server_local():
    """User on Pacific time, schedule says ``09:00 America/Los_Angeles``.
    Server is UTC. At 12:00 UTC = 05:00 LA local, next run is 09:00 LA
    today = 16:00 UTC. Pre-fix the engine used naive ``datetime.now()``
    and read 12:00 as if it were the user's tz, mis-scheduling by the
    server-vs-user tz offset."""
    now = _utc(2026, 5, 27, 12, 0)
    result = next_run_at('daily_time',
                         {'time': '09:00', 'tz': 'America/Los_Angeles'},
                         now)
    # 09:00 LA on 2026-05-27 → 16:00 UTC (PDT, UTC-7).
    assert result == _utc(2026, 5, 27, 16, 0)


def test_daily_falls_back_to_default_tz_when_config_missing():
    """``tz`` field absent on the config — pulls from ``default_tz``
    (typically the app-level setting)."""
    now = _utc(2026, 5, 27, 12, 0)
    result = next_run_at('daily_time', {'time': '09:00'}, now,
                         default_tz='America/Los_Angeles')
    assert result == _utc(2026, 5, 27, 16, 0)


def test_daily_garbage_time_string_defaults_to_midnight():
    """Bad ``time`` string → defaults to 00:00 (engine's existing
    behaviour). Better than crashing the scheduler when a row's
    config was hand-edited."""
    now = _utc(2026, 5, 27, 12, 0)
    result = next_run_at('daily_time', {'time': 'garbage', 'tz': 'UTC'}, now)
    # 00:00 today already passed → tomorrow at 00:00.
    assert result == _utc(2026, 5, 28, 0, 0)


def test_daily_unknown_tz_falls_back_to_utc():
    """Unknown IANA tz string → fall back to UTC rather than crash."""
    now = _utc(2026, 5, 27, 12, 0)
    result = next_run_at('daily_time',
                         {'time': '15:00', 'tz': 'Imaginary/Place'},
                         now)
    # Treated as UTC → next run today at 15:00 UTC.
    assert result == _utc(2026, 5, 27, 15, 0)


# ---------------------------------------------------------------------------
# Weekly (``trigger_type='weekly_time'``)
# ---------------------------------------------------------------------------


def test_weekly_picks_next_matching_weekday():
    """It's Wednesday and the schedule wants Mon/Wed/Fri — same day
    qualifies if the time is still in the future."""
    # 2026-05-27 is a Wednesday.
    now = _utc(2026, 5, 27, 8, 0)
    result = next_run_at('weekly_time',
                         {'time': '14:00', 'days': ['mon', 'wed', 'fri'], 'tz': 'UTC'},
                         now)
    assert result == _utc(2026, 5, 27, 14, 0)


def test_weekly_rolls_to_next_allowed_day_when_today_passed():
    """Wednesday 18:00 UTC, schedule wants Mon/Wed/Fri at 14:00 —
    Wed 14:00 already passed today, next match is Friday at 14:00."""
    now = _utc(2026, 5, 27, 18, 0)  # Wed
    result = next_run_at('weekly_time',
                         {'time': '14:00', 'days': ['mon', 'wed', 'fri'], 'tz': 'UTC'},
                         now)
    assert result == _utc(2026, 5, 29, 14, 0)  # Fri


def test_weekly_wraps_to_next_week():
    """Sunday past the time, schedule wants only Monday — next match
    is the very next day."""
    # 2026-05-31 is a Sunday.
    now = _utc(2026, 5, 31, 15, 0)
    result = next_run_at('weekly_time',
                         {'time': '09:00', 'days': ['mon'], 'tz': 'UTC'},
                         now)
    assert result == _utc(2026, 6, 1, 9, 0)  # next Monday


def test_weekly_empty_days_means_every_day():
    """Empty ``days`` list → treat as every weekday. Matches the
    engine's existing fallback in ``_next_weekly_occurrence``."""
    now = _utc(2026, 5, 27, 8, 0)
    result = next_run_at('weekly_time',
                         {'time': '14:00', 'days': [], 'tz': 'UTC'},
                         now)
    # Today (Wed) qualifies since 14:00 is still future.
    assert result == _utc(2026, 5, 27, 14, 0)


def test_weekly_unrecognised_day_abbreviations_dropped():
    """``'mond'`` / ``'frid'`` are not in the map — silently drop.
    If ALL listed days are invalid, fall through to the every-day
    default (matches the empty-list behaviour)."""
    now = _utc(2026, 5, 27, 8, 0)
    result = next_run_at('weekly_time',
                         {'time': '14:00', 'days': ['mond', 'frid'], 'tz': 'UTC'},
                         now)
    # All garbage → every day → today (Wed) qualifies.
    assert result == _utc(2026, 5, 27, 14, 0)


def test_weekly_day_abbreviations_case_insensitive():
    """``MON`` / ``Mon`` / ``mon`` all parse to weekday 0."""
    now = _utc(2026, 5, 27, 8, 0)  # Wed
    result = next_run_at('weekly_time',
                         {'time': '14:00', 'days': ['MON', 'WED'], 'tz': 'UTC'},
                         now)
    assert result == _utc(2026, 5, 27, 14, 0)


def test_weekly_respects_user_tz_across_day_boundary():
    """It's 23:30 UTC on Wednesday → 16:30 LA local (still Wed).
    Schedule fires Mon/Wed/Fri at 18:00 LA. Next run is 18:00 LA
    today (Wed in LA, but Thursday in UTC because of the 7h offset)."""
    now = _utc(2026, 5, 27, 23, 30)  # Wed 23:30 UTC / Wed 16:30 LA
    result = next_run_at('weekly_time',
                         {'time': '18:00', 'days': ['mon', 'wed', 'fri'],
                          'tz': 'America/Los_Angeles'},
                         now)
    # 2026-05-27 18:00 LA → 2026-05-28 01:00 UTC.
    assert result == _utc(2026, 5, 28, 1, 0)


# ---------------------------------------------------------------------------
# Monthly (``trigger_type='monthly_time'`` — NEW in PR 1)
# ---------------------------------------------------------------------------


def test_monthly_picks_target_day_this_month_when_future():
    """It's the 5th, schedule fires on the 15th — next run is the
    15th of the current month."""
    now = _utc(2026, 5, 5, 12, 0)
    result = next_run_at('monthly_time',
                         {'time': '09:00', 'day_of_month': 15, 'tz': 'UTC'},
                         now)
    assert result == _utc(2026, 5, 15, 9, 0)


def test_monthly_rolls_to_next_month_when_target_day_passed():
    """It's the 20th, schedule fires on the 15th — already past in
    May, next run is June 15."""
    now = _utc(2026, 5, 20, 12, 0)
    result = next_run_at('monthly_time',
                         {'time': '09:00', 'day_of_month': 15, 'tz': 'UTC'},
                         now)
    assert result == _utc(2026, 6, 15, 9, 0)


def test_monthly_clamps_to_last_day_when_month_too_short():
    """Schedule wants day 31; February has 28 (or 29). Clamp to the
    LAST valid day of that month — running a day or two early in
    short months is less surprising than silently skipping a month
    entirely. Standard cron convention."""
    now = _utc(2026, 2, 1, 12, 0)  # 2026 is not a leap year
    result = next_run_at('monthly_time',
                         {'time': '09:00', 'day_of_month': 31, 'tz': 'UTC'},
                         now)
    # 2026 Feb has 28 days → run on the 28th instead.
    assert result == _utc(2026, 2, 28, 9, 0)


def test_monthly_handles_leap_year_february():
    """2024 was a leap year — February has 29 days, so day-31 clamps
    to the 29th, not the 28th."""
    now = _utc(2024, 2, 1, 12, 0)
    result = next_run_at('monthly_time',
                         {'time': '09:00', 'day_of_month': 31, 'tz': 'UTC'},
                         now)
    assert result == _utc(2024, 2, 29, 9, 0)


def test_monthly_clamps_day_above_31_and_below_1():
    """Defensive — config values outside [1, 31] clamp to the nearest
    valid bound rather than crashing the scheduler."""
    now = _utc(2026, 5, 5, 12, 0)
    high = next_run_at('monthly_time',
                      {'time': '09:00', 'day_of_month': 99, 'tz': 'UTC'},
                      now)
    low = next_run_at('monthly_time',
                     {'time': '09:00', 'day_of_month': -5, 'tz': 'UTC'},
                     now)
    # 99 → clamped to 31 → May has 31 days → May 31st.
    assert high == _utc(2026, 5, 31, 9, 0)
    # -5 → clamped to 1 → next 1st is June 1 (May 1 already passed).
    assert low == _utc(2026, 6, 1, 9, 0)


def test_monthly_rolls_year_at_december_to_january():
    """December 20, schedule fires on the 5th — next run is January 5
    of the FOLLOWING year, not month 13 of the current year."""
    now = _utc(2026, 12, 20, 12, 0)
    result = next_run_at('monthly_time',
                         {'time': '09:00', 'day_of_month': 5, 'tz': 'UTC'},
                         now)
    assert result == _utc(2027, 1, 5, 9, 0)


def test_monthly_respects_user_tz():
    """Schedule wants the 1st of each month at 02:00 LA. ``now`` is
    May 1 at 06:00 UTC = April 30 at 23:00 LA. So locally we haven't
    hit May 1 02:00 LA yet → next run is May 1 02:00 LA = May 1 09:00
    UTC (PDT, UTC-7)."""
    now = _utc(2026, 5, 1, 6, 0)
    result = next_run_at('monthly_time',
                         {'time': '02:00', 'day_of_month': 1,
                          'tz': 'America/Los_Angeles'},
                         now)
    assert result == _utc(2026, 5, 1, 9, 0)


# ---------------------------------------------------------------------------
# Result shape — every returned datetime must be aware UTC so the engine
# can serialise it to the DB ``next_run`` column without ambiguity.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('trigger_type,config', [
    ('schedule',     {'interval': 1, 'unit': 'hours'}),
    ('daily_time',   {'time': '09:00', 'tz': 'America/Los_Angeles'}),
    ('weekly_time',  {'time': '09:00', 'days': ['mon'], 'tz': 'America/Los_Angeles'}),
    ('monthly_time', {'time': '09:00', 'day_of_month': 15, 'tz': 'America/Los_Angeles'}),
])
def test_result_is_always_aware_utc(trigger_type, config):
    """Engine writes the result as a naive string to the DB but the
    convention is "stored as UTC". Returning a naive datetime would
    leak the caller's local tz into the column. Pin the contract:
    every result has ``tzinfo`` and is at UTC offset zero."""
    now = _utc(2026, 5, 27, 12, 0)
    result = next_run_at(trigger_type, config, now)
    assert result is not None
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)


def test_naive_now_utc_is_coerced_to_aware_utc():
    """Defensive — naive ``now_utc`` inputs are assumed UTC and the
    result is still aware UTC. Matches the engine's convention
    when parsing the DB ``next_run`` column."""
    naive_now = datetime(2026, 5, 27, 12, 0)
    result = next_run_at('schedule', {'interval': 1, 'unit': 'hours'}, naive_now)
    assert result == _utc(2026, 5, 27, 13, 0)
    assert result.tzinfo is not None
