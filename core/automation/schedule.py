"""Pure functions for computing the next-run datetime of a scheduled
automation trigger.

The Auto-Sync schedule board currently exposes interval-based scheduling
(``every N hours``) backed by ``trigger_type='schedule'``. The
automation engine ALSO supports ``daily_time`` and ``weekly_time``
triggers via separate ``_setup_*_trigger`` methods inline on the engine
class. None of that logic is currently testable in isolation — the
engine's ``_finish_run`` reaches for ``datetime.now()``, threads it
through ``_next_weekly_occurrence``, and writes the result to the DB,
all on the same call.

This module lifts the "given a trigger config, what's the next run?"
question out of the engine into a pure function:

    next_run_at(trigger_type, trigger_config, now_utc, default_tz)
        -> Optional[datetime]

That means:
- ``now_utc`` is INJECTED, not pulled from the system clock. Tests
  freeze time without monkeypatching ``datetime.now``.
- ``default_tz`` is INJECTED. Daily / weekly / monthly schedules are
  inherently in the USER'S timezone (cron "every Monday at 9am" is
  not UTC), and the historic engine implicitly used the server's
  local tz via naive ``datetime.now()``. That broke for users on a
  different tz than their server. The pure function takes the tz
  explicitly so the caller controls it.
- Returns an aware UTC ``datetime`` ready to serialise to the DB's
  ``next_run`` string column, or ``None`` for unrecognised /
  event-based triggers (engine should not store a next_run for those).

PR 1 of the schedule-types feature ships ONLY this module + tests.
The engine continues to compute next_run via its existing inline
helpers; PR 2 collapses those into a single ``next_run_at`` call.
Net behavior is identical until the engine is wired through — this
PR is pure plumbing.

Schedule types supported here:

- ``schedule`` (interval): ``{interval: N, unit: 'minutes'|'hours'|'days'}``
  — adds the interval to ``now_utc``; no tz needed.
- ``daily_time``: ``{time: 'HH:MM', tz: '<IANA>'}`` — runs every day at
  the given local time in the given timezone. ``tz`` falls back to
  ``default_tz`` when absent.
- ``weekly_time``: ``{time: 'HH:MM', days: ['mon','wed',...], tz: '<IANA>'}``
  — runs on the matching weekday(s) at the given local time. Empty
  ``days`` list means "every day" (matches the engine's existing
  fallback in ``_next_weekly_occurrence``).
- ``monthly_time``: ``{time: 'HH:MM', day_of_month: 1-31, tz: '<IANA>'}``
  — runs on the given day each month. Days that don't exist in a
  given month (Feb 30, Apr 31) clamp to the LAST valid day of that
  month rather than skipping the run entirely; missing a whole
  month silently because the schedule was over-eager is worse than
  running a day early.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from utils.logging_config import get_logger

logger = get_logger("automation.schedule")


# Unknown-tz names already warned about in this process — avoids
# spamming the log on every poll cycle for the same misconfigured row.
_UNKNOWN_TZ_WARNED: set = set()


# Weekday abbreviation → ``datetime.weekday()`` index (Mon=0..Sun=6).
# Mirrors the engine's existing ``_next_weekly_occurrence`` mapping so
# schedules created against either implementation accept the same
# ``days`` strings.
_WEEKDAY_MAP = {
    'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6,
}

# Interval multipliers — kept aligned with the engine's existing
# ``_calc_delay_seconds`` in ``core/automation_engine.py``. Adding
# entries here without also updating the engine would silently drift:
# this function would honour the new unit while the live engine path
# defaults it to hours. Keep the maps in sync until PR 2 collapses the
# engine through this function.
_INTERVAL_MULTIPLIERS = {
    'minutes': 60,
    'hours':   60 * 60,
    'days':    60 * 60 * 24,
}


def next_run_at(
    trigger_type: str,
    trigger_config: Dict[str, Any],
    now_utc: datetime,
    default_tz: str = 'UTC',
) -> Optional[datetime]:
    """Compute the next-run timestamp (UTC, aware) for a scheduled
    trigger. Returns ``None`` for unrecognised types or event-based
    triggers — callers should not write a next_run for those.

    See module docstring for supported trigger types + config shapes.
    """
    if not isinstance(trigger_config, dict):
        trigger_config = {}

    if trigger_type == 'schedule':
        return _next_interval(trigger_config, now_utc)
    if trigger_type == 'daily_time':
        return _next_daily(trigger_config, now_utc, default_tz)
    if trigger_type == 'weekly_time':
        return _next_weekly(trigger_config, now_utc, default_tz)
    if trigger_type == 'monthly_time':
        return _next_monthly(trigger_config, now_utc, default_tz)
    return None


# ---------------------------------------------------------------------------
# Interval
# ---------------------------------------------------------------------------


def _next_interval(config: Dict[str, Any], now_utc: datetime) -> datetime:
    """``{interval: N, unit: 'hours'}`` → ``now_utc + N hours``.

    Mirrors the engine's existing ``_calc_delay_seconds``. Unit defaults
    to ``hours`` for backward compat with legacy DB rows that pre-date
    the unit field being mandatory; interval defaults to 1 so a fully
    empty config doesn't divide-by-zero or schedule for the past."""
    try:
        interval = max(int(config.get('interval', 1)), 1)
    except (TypeError, ValueError):
        interval = 1
    unit = config.get('unit') or 'hours'
    seconds = interval * _INTERVAL_MULTIPLIERS.get(unit, _INTERVAL_MULTIPLIERS['hours'])
    return _ensure_utc(now_utc) + timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# Daily
# ---------------------------------------------------------------------------


def _next_daily(
    config: Dict[str, Any], now_utc: datetime, default_tz: str,
) -> datetime:
    """``{time: 'HH:MM', tz: '<IANA>'}`` → next occurrence of that
    wall-clock time in the user's timezone, expressed as aware UTC.

    DST-aware via ``zoneinfo``: when the local time falls during a
    spring-forward gap, the ``replace`` lands on a non-existent
    instant; ``zoneinfo`` resolves that to the gap's later side
    (e.g. 02:30 on the DST-forward day becomes 03:30 local). Tests
    pin both spring-forward and fall-back behaviour."""
    tz = _resolve_tz(config.get('tz') or default_tz)
    hour, minute = _parse_hhmm(config.get('time'))
    now_local = _ensure_utc(now_utc).astimezone(tz)
    target_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target_local <= now_local:
        target_local = target_local + timedelta(days=1)
    return target_local.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Weekly
# ---------------------------------------------------------------------------


def _next_weekly(
    config: Dict[str, Any], now_utc: datetime, default_tz: str,
) -> datetime:
    """``{time: 'HH:MM', days: ['mon',...], tz: '<IANA>'}`` → next
    occurrence of that wall-clock time on any of the listed weekdays
    in the user's timezone.

    Empty ``days`` list ≡ every day, matching the engine's existing
    fallback. Unrecognised day abbreviations are silently dropped
    (an empty result-set then triggers the every-day fallback)."""
    tz = _resolve_tz(config.get('tz') or default_tz)
    hour, minute = _parse_hhmm(config.get('time'))
    days = _parse_weekdays(config.get('days'))

    now_local = _ensure_utc(now_utc).astimezone(tz)
    # Scan today + next 7 days; the matching day with a future
    # local time wins. 8-day scan is enough to handle the case where
    # today already passed the time AND today is the only allowed
    # weekday (next occurrence is exactly one week out).
    for offset in range(8):
        candidate = now_local + timedelta(days=offset)
        if candidate.weekday() not in days:
            continue
        target = candidate.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target > now_local:
            return target.astimezone(timezone.utc)
    # Shouldn't reach: 8-day scan always finds a hit when ``days``
    # is non-empty. Defensive fallback: next week, same weekday as today.
    fallback = (now_local + timedelta(days=7)).replace(
        hour=hour, minute=minute, second=0, microsecond=0,
    )
    return fallback.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Monthly
# ---------------------------------------------------------------------------


def _next_monthly(
    config: Dict[str, Any], now_utc: datetime, default_tz: str,
) -> datetime:
    """``{time: 'HH:MM', day_of_month: 1-31, tz: '<IANA>'}`` → next
    occurrence in the user's timezone.

    ``day_of_month`` is clamped to ``[1, 31]``. When the target day
    doesn't exist in a given month (Feb 30, Apr 31), the schedule
    falls back to the LAST valid day of that month — running a day
    or two early in short months is less surprising than skipping
    a month entirely. This matches the convention every cron
    implementation in the wild settled on."""
    tz = _resolve_tz(config.get('tz') or default_tz)
    hour, minute = _parse_hhmm(config.get('time'))
    raw_day = config.get('day_of_month', 1)
    try:
        target_day = max(1, min(31, int(raw_day)))
    except (TypeError, ValueError):
        target_day = 1

    now_local = _ensure_utc(now_utc).astimezone(tz)
    # Try this month first; if the target day has already passed
    # (or doesn't exist this month and the clamped day is in the
    # past), advance to next month. Loop bounded to 12 iterations
    # so a pathologically broken config can't infinite-loop us.
    year, month = now_local.year, now_local.month
    for _ in range(12):
        day = min(target_day, _days_in_month(year, month))
        target = now_local.replace(
            year=year, month=month, day=day,
            hour=hour, minute=minute, second=0, microsecond=0,
        )
        if target > now_local:
            return target.astimezone(timezone.utc)
        # Roll to next month.
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    # Defensive — should be unreachable.
    return (now_local + timedelta(days=30)).astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_utc(dt: datetime) -> datetime:
    """Coerce a possibly-naive datetime to aware UTC. Naive inputs
    are assumed UTC (matches the convention the engine uses when
    parsing the DB ``next_run`` column)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _resolve_tz(name: Optional[str]):
    """Look up an IANA tz by name. Falls back to UTC when the name is
    unknown — ``ZoneInfoNotFoundError`` is the symptom of either a
    typo in the tz string or ``tzdata`` missing on the host. Logged
    once per unknown name so the user can see WHY their schedule
    isn't running in the timezone they configured."""
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name not in _UNKNOWN_TZ_WARNED:
            _UNKNOWN_TZ_WARNED.add(name)
            logger.warning(
                "Unknown timezone %r — schedule will run against UTC. "
                "Check the spelling (IANA format like 'America/Los_Angeles') "
                "or install the `tzdata` package on minimal hosts.",
                name,
            )
        return timezone.utc


def _parse_hhmm(time_str: Optional[str]) -> tuple:
    """Parse ``HH:MM`` → ``(hour, minute)``. Defaults to 00:00 on
    garbage input — same defensive shape as the engine's existing
    daily/weekly time parsing."""
    if not isinstance(time_str, str):
        return 0, 0
    try:
        h, m = time_str.split(':', 1)
        return max(0, min(23, int(h))), max(0, min(59, int(m)))
    except (ValueError, AttributeError):
        return 0, 0


def _parse_weekdays(days) -> set:
    """``['mon', 'wed']`` → ``{0, 2}``. Empty / missing / all-invalid
    list returns ``set(range(7))`` ("every day"), matching the
    engine's existing ``_next_weekly_occurrence`` fallback."""
    if not isinstance(days, (list, tuple)):
        return set(range(7))
    parsed = {_WEEKDAY_MAP[d.lower()] for d in days
              if isinstance(d, str) and d.lower() in _WEEKDAY_MAP}
    return parsed or set(range(7))


def _days_in_month(year: int, month: int) -> int:
    """Last calendar day of ``year-month``. Stdlib-only — no calendar
    module import needed; cycle through the 12 months."""
    if month == 12:
        next_first = datetime(year + 1, 1, 1)
    else:
        next_first = datetime(year, month + 1, 1)
    last_day = next_first - timedelta(days=1)
    return last_day.day
