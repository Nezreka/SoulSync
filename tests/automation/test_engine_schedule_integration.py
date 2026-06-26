"""Engine-level integration tests for the next_run_at refactor (PR 2).

PR 1 lifted next-run computation into ``core/automation/schedule.py``
as a pure function. PR 2 wires the engine through it — three setup
methods (daily / weekly / monthly) collapse to one ``_setup_timed_trigger``
helper, ``_finish_run`` drops its inline daily / weekly arithmetic,
and ``monthly_time`` becomes a real registered trigger type.

These tests pin the integration surface:
- ``_finish_run`` dispatches through ``next_run_at`` for every trigger
  type with the right args (trigger_type, trigger_config, now_utc,
  default_tz) and serialises the result into the DB ``next_run`` column.
- Retry-delay short-circuit bypasses ``next_run_at`` (immediate
  reschedule on transient failure, not on the regular cadence).
- Error path swallows + writes None next_run instead of crashing.
- Backward-compat: existing daily / weekly rows without an explicit
  ``tz`` field use the engine's ``_default_tz`` (server-local IANA),
  preserving "every Monday 09:00 server-local" behaviour.
- New ``monthly_time`` trigger registers in ``_trigger_handlers`` and
  arms a timer correctly.
- ``_setup_timed_trigger`` honours an existing future ``next_run`` in
  the DB (lets manual edits / restart-resume survive).
- ``_dt_to_db_str`` correctly normalises aware + naive datetimes to
  the engine's naive-UTC string convention.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.automation_engine import (
    AutomationEngine,
    _dt_to_db_str,
    _resolve_system_default_tz,
)


def _utc(year, month, day, hour=0, minute=0, second=0):
    """Aware UTC datetime for test clarity — matches the convention
    used in tests/automation/test_schedule.py."""
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _dt_to_db_str — engine-side serialiser for ``next_run_at`` results.
# ---------------------------------------------------------------------------


def test_dt_to_db_str_normalises_aware_utc():
    """Aware-UTC datetime → naive-UTC string the DB column expects.
    Format matches the engine's existing ``_utc_after``."""
    dt = _utc(2026, 5, 27, 14, 30, 0)
    assert _dt_to_db_str(dt) == '2026-05-27 14:30:00'


def test_dt_to_db_str_converts_aware_non_utc_to_utc_first():
    """An aware datetime in a non-UTC tz must be converted to UTC
    before stringifying — otherwise the DB column would silently
    hold a tz-shifted instant. This is the bug class the PR 1
    tests already cover at the next_run_at layer; pin it here so a
    future refactor that drops the ``.astimezone(UTC)`` step fails
    fast."""
    from zoneinfo import ZoneInfo
    la = ZoneInfo('America/Los_Angeles')
    dt = datetime(2026, 5, 27, 9, 0, 0, tzinfo=la)  # 09:00 PDT
    # 09:00 PDT (UTC-7) → 16:00 UTC.
    assert _dt_to_db_str(dt) == '2026-05-27 16:00:00'


def test_dt_to_db_str_assumes_naive_is_utc():
    """Defensive — naive inputs are assumed UTC (matches the engine's
    convention when parsing the DB column back out)."""
    dt = datetime(2026, 5, 27, 14, 30, 0)  # naive
    assert _dt_to_db_str(dt) == '2026-05-27 14:30:00'


# ---------------------------------------------------------------------------
# _resolve_system_default_tz — engine's tz fallback chain.
# ---------------------------------------------------------------------------


def test_resolve_system_default_tz_returns_iana_string():
    """The engine caches this at import time; the result must be a
    string (not a ZoneInfo object) so it can flow into next_run_at's
    ``default_tz`` param."""
    result = _resolve_system_default_tz()
    assert isinstance(result, str)
    assert len(result) > 0


def test_resolve_system_default_tz_falls_back_to_utc_when_tzlocal_missing():
    """tzlocal is in requirements but the engine should still boot
    without it — minimal Docker images / dev environments where
    tzlocal didn't install. Defensive fallback to UTC instead of
    crashing the engine."""
    with patch.dict('sys.modules', {'tzlocal': None}):
        # Force ImportError on the in-function import.
        import importlib
        import core.automation_engine as engine_mod
        importlib.reload(engine_mod)
        result = engine_mod._resolve_system_default_tz()
        assert result == 'UTC'
        # Reload again to restore normal state for subsequent tests.
        importlib.reload(engine_mod)


# ---------------------------------------------------------------------------
# Engine fixture — minimal AutomationEngine with mocked DB.
# ---------------------------------------------------------------------------


@pytest.fixture
def engine_with_db():
    """AutomationEngine wired to a mock DB. Used across the
    integration tests below — each test sets ``trigger_type`` and
    ``trigger_config`` on the mock's ``get_automation`` return value."""
    db_mock = MagicMock()
    db_mock.update_automation_run = MagicMock(return_value=True)
    db_mock.update_automation = MagicMock(return_value=True)
    db_mock.get_automation.return_value = None  # tests override
    engine = AutomationEngine(db_mock)
    engine._running = True
    return engine, db_mock


# ---------------------------------------------------------------------------
# _finish_run — single integration point with next_run_at.
# ---------------------------------------------------------------------------


def test_finish_run_dispatches_interval_trigger_through_next_run_at(engine_with_db):
    """Interval trigger flows through the same next_run_at call as
    daily/weekly/monthly — no special-case branch left in the engine
    for the legacy ``schedule`` type."""
    engine, db_mock = engine_with_db
    auto = {
        'id': 1, 'enabled': True,
        'trigger_type': 'schedule',
        'trigger_config': json.dumps({'interval': 6, 'unit': 'hours'}),
    }
    with patch('core.automation_engine.next_run_at') as mock_nra:
        mock_nra.return_value = _utc(2026, 5, 27, 18, 0)
        engine._finish_run(auto, 1, {'status': 'completed'}, error=None)
    assert mock_nra.called
    call = mock_nra.call_args
    assert call.args[0] == 'schedule'
    assert call.args[1] == {'interval': 6, 'unit': 'hours'}
    assert call.kwargs['default_tz'] == engine._default_tz


def test_finish_run_dispatches_daily_time_through_next_run_at(engine_with_db):
    """Daily trigger no longer has its own inline arithmetic — the
    refactor must route through next_run_at with the unmodified
    trigger_config so tz / time fields flow through cleanly."""
    engine, db_mock = engine_with_db
    auto = {
        'id': 1, 'enabled': True,
        'trigger_type': 'daily_time',
        'trigger_config': json.dumps({'time': '09:00', 'tz': 'America/Los_Angeles'}),
    }
    with patch('core.automation_engine.next_run_at') as mock_nra:
        mock_nra.return_value = _utc(2026, 5, 27, 16, 0)
        engine._finish_run(auto, 1, {}, error=None)
    assert mock_nra.call_args.args[0] == 'daily_time'
    assert mock_nra.call_args.args[1] == {'time': '09:00', 'tz': 'America/Los_Angeles'}


def test_finish_run_dispatches_weekly_time_through_next_run_at(engine_with_db):
    """Weekly trigger same as daily — single integration point."""
    engine, db_mock = engine_with_db
    cfg = {'time': '09:00', 'days': ['mon', 'wed', 'fri'], 'tz': 'America/Los_Angeles'}
    auto = {
        'id': 1, 'enabled': True,
        'trigger_type': 'weekly_time',
        'trigger_config': json.dumps(cfg),
    }
    with patch('core.automation_engine.next_run_at') as mock_nra:
        mock_nra.return_value = _utc(2026, 5, 27, 16, 0)
        engine._finish_run(auto, 1, {}, error=None)
    assert mock_nra.call_args.args[0] == 'weekly_time'
    assert mock_nra.call_args.args[1] == cfg


def test_finish_run_dispatches_monthly_time_through_next_run_at(engine_with_db):
    """New monthly_time trigger — added to _trigger_handlers in PR 2.
    Without this entry, the if-trigger_type-in-handlers gate above
    skips computation entirely and the DB next_run stays stale."""
    engine, db_mock = engine_with_db
    cfg = {'time': '09:00', 'day_of_month': 15, 'tz': 'America/Los_Angeles'}
    auto = {
        'id': 1, 'enabled': True,
        'trigger_type': 'monthly_time',
        'trigger_config': json.dumps(cfg),
    }
    with patch('core.automation_engine.next_run_at') as mock_nra:
        mock_nra.return_value = _utc(2026, 6, 15, 16, 0)
        engine._finish_run(auto, 1, {}, error=None)
    assert mock_nra.call_args.args[0] == 'monthly_time'


def test_finish_run_retry_delay_short_circuits_next_run_at(engine_with_db):
    """When a transient failure asks for a retry-delay reschedule
    (e.g. action handler returns ``status='retry'``), the next_run
    is just now+delay — NOT the regular schedule cadence. The
    refactor must preserve this short-circuit path."""
    engine, db_mock = engine_with_db
    auto = {
        'id': 1, 'enabled': True,
        'trigger_type': 'daily_time',
        'trigger_config': json.dumps({'time': '09:00'}),
    }
    with patch('core.automation_engine.next_run_at') as mock_nra:
        engine._finish_run(auto, 1, {}, error='boom', retry_delay_seconds=120)
    # next_run_at NOT called — we used the retry delay instead.
    mock_nra.assert_not_called()
    # DB write happened (with a next_run computed from now + 120s).
    assert db_mock.update_automation_run.called
    written = db_mock.update_automation_run.call_args.kwargs.get('next_run')
    assert written is not None


def test_finish_run_writes_none_when_next_run_at_returns_none(engine_with_db):
    """Defensive — next_run_at can return None for unknown trigger
    types or completely broken configs. The engine must write
    None to the DB rather than skip the update (which would leave
    a stale next_run sitting in the row forever)."""
    engine, db_mock = engine_with_db
    auto = {
        'id': 1, 'enabled': True,
        'trigger_type': 'daily_time',
        'trigger_config': json.dumps({'time': '09:00'}),
    }
    with patch('core.automation_engine.next_run_at', return_value=None):
        engine._finish_run(auto, 1, {}, error=None)
    assert db_mock.update_automation_run.called
    assert db_mock.update_automation_run.call_args.kwargs.get('next_run') is None


def test_finish_run_swallows_next_run_at_exception(engine_with_db):
    """next_run_at is pure so it shouldn't raise — but if it does
    (programmer error in the helper, weird tz lookup), the engine
    must not crash the finish-run cycle. Existing behaviour
    swallows + logs at debug; the refactor preserves that."""
    engine, db_mock = engine_with_db
    auto = {
        'id': 1, 'enabled': True,
        'trigger_type': 'daily_time',
        'trigger_config': json.dumps({'time': '09:00'}),
    }
    with patch('core.automation_engine.next_run_at', side_effect=RuntimeError('boom')):
        engine._finish_run(auto, 1, {}, error=None)
    # DB write still happens, just with None next_run.
    assert db_mock.update_automation_run.called


def test_finish_run_skips_next_run_for_event_triggers(engine_with_db):
    """Event-based triggers (not in _trigger_handlers) have no
    scheduled next-run — the existing gate must still skip them
    after the refactor."""
    engine, db_mock = engine_with_db
    auto = {
        'id': 1, 'enabled': True,
        'trigger_type': 'event',
        'trigger_config': json.dumps({}),
    }
    with patch('core.automation_engine.next_run_at') as mock_nra:
        engine._finish_run(auto, 1, {}, error=None)
    mock_nra.assert_not_called()
    # update_automation_run still fires but with next_run=None.
    assert db_mock.update_automation_run.call_args.kwargs.get('next_run') is None


def test_finish_run_passes_engine_default_tz(engine_with_db):
    """Backward-compat: existing daily/weekly rows without ``tz`` in
    their config must use the engine's ``_default_tz`` (server-local
    IANA via tzlocal). Pre-fix, the engine implicitly used naive
    ``datetime.now()`` = server local; post-fix the explicit
    default_tz preserves that behaviour."""
    engine, db_mock = engine_with_db
    engine._default_tz = 'America/Los_Angeles'  # simulate server-local
    auto = {
        'id': 1, 'enabled': True,
        'trigger_type': 'daily_time',
        'trigger_config': json.dumps({'time': '09:00'}),  # NO tz field
    }
    with patch('core.automation_engine.next_run_at') as mock_nra:
        mock_nra.return_value = _utc(2026, 5, 27, 16, 0)
        engine._finish_run(auto, 1, {}, error=None)
    assert mock_nra.call_args.kwargs['default_tz'] == 'America/Los_Angeles'


# ---------------------------------------------------------------------------
# Trigger handler registration.
# ---------------------------------------------------------------------------


def test_engine_registers_monthly_time_trigger(engine_with_db):
    """``monthly_time`` joins schedule / daily_time / weekly_time in
    the _trigger_handlers registry — without this, calling
    ``schedule_automation`` on a monthly row falls through the
    ``trigger_type in self._trigger_handlers`` gate and the
    automation never gets armed."""
    engine, _ = engine_with_db
    assert 'monthly_time' in engine._trigger_handlers
    assert callable(engine._trigger_handlers['monthly_time'])


def test_engine_keeps_existing_trigger_registrations(engine_with_db):
    """Backward-compat: the refactor must not drop the historic
    trigger types. schedule / daily_time / weekly_time stay
    registered alongside the new monthly_time."""
    engine, _ = engine_with_db
    assert 'schedule' in engine._trigger_handlers
    assert 'daily_time' in engine._trigger_handlers
    assert 'weekly_time' in engine._trigger_handlers


# ---------------------------------------------------------------------------
# _setup_timed_trigger — shared skeleton for daily / weekly / monthly.
# ---------------------------------------------------------------------------


def test_setup_monthly_time_trigger_writes_next_run_and_arms_timer(engine_with_db):
    """Sanity check that the new monthly handler actually wires up
    a timer (it's the new-shaped trigger so a "no timer armed"
    regression would otherwise be silent — the automation just
    never fires)."""
    engine, db_mock = engine_with_db
    db_mock.get_automation.return_value = {'id': 1, 'next_run': None}
    with patch('core.automation_engine.threading.Timer') as mock_timer_cls:
        mock_timer = MagicMock()
        mock_timer_cls.return_value = mock_timer
        engine._setup_monthly_time_trigger(
            1, {'time': '09:00', 'day_of_month': 15, 'tz': 'UTC'},
        )
    # Timer armed.
    assert mock_timer.start.called
    # next_run written to DB.
    assert db_mock.update_automation.called
    written = db_mock.update_automation.call_args.kwargs.get('next_run')
    assert written is not None and isinstance(written, str)


def test_setup_timed_trigger_honours_future_db_next_run(engine_with_db):
    """If the DB row already has a future ``next_run`` (e.g. a
    manual edit, or a process restart picking up where it left
    off), the setup must keep that instant — not recompute from
    scratch. Matches the existing interval-path behaviour and
    prevents losing pending retries."""
    engine, db_mock = engine_with_db
    # Far-future next_run in the DB.
    future = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    db_mock.get_automation.return_value = {'id': 1, 'next_run': future}
    with patch('core.automation_engine.threading.Timer') as mock_timer_cls:
        mock_timer_cls.return_value = MagicMock()
        engine._setup_daily_time_trigger(1, {'time': '09:00', 'tz': 'UTC'})
    # Engine writes the EXISTING next_run back (the if-future-in-DB
    # branch overrides the freshly-computed delay).
    written = db_mock.update_automation.call_args.kwargs.get('next_run')
    assert written == future


def test_setup_timed_trigger_skips_when_next_run_at_returns_none(engine_with_db):
    """If next_run_at can't compute a valid next-run (e.g. broken
    config that defeats every defensive fallback in the helper),
    the setup must NOT arm a timer with bogus delay. Skip-with-log
    is safer than scheduling-for-the-past or scheduling-immediately."""
    engine, db_mock = engine_with_db
    db_mock.get_automation.return_value = {'id': 1, 'next_run': None}
    with patch('core.automation_engine.next_run_at', return_value=None), \
         patch('core.automation_engine.threading.Timer') as mock_timer_cls:
        engine._setup_monthly_time_trigger(1, {})
    # No timer armed.
    mock_timer_cls.assert_not_called()


# ---------------------------------------------------------------------------
# End-to-end: real next_run_at + engine wiring (no mocks).
# ---------------------------------------------------------------------------


def test_end_to_end_monthly_schedule_produces_valid_db_string(engine_with_db):
    """No-mock smoke: monthly_time config flows from engine through
    the real next_run_at into a valid DB string. Catches any
    serialisation drift between PR 1 (helper returns aware UTC) and
    PR 2 (engine writes naive UTC string)."""
    engine, db_mock = engine_with_db
    engine._default_tz = 'UTC'
    db_mock.get_automation.return_value = {'id': 1, 'next_run': None}
    with patch('core.automation_engine.threading.Timer') as mock_timer_cls:
        mock_timer_cls.return_value = MagicMock()
        engine._setup_monthly_time_trigger(
            1, {'time': '09:00', 'day_of_month': 15},
        )
    written = db_mock.update_automation.call_args.kwargs['next_run']
    # Format matches the engine's existing _utcnow_str / _utc_after.
    parsed = datetime.strptime(written, '%Y-%m-%d %H:%M:%S')
    # Day-of-month is 15 in the user's tz (UTC here).
    assert parsed.day == 15
    assert parsed.hour == 9
    assert parsed.minute == 0


# ---------------------------------------------------------------------------
# System-automation seeding — owned_by / action_config (video side).
# ---------------------------------------------------------------------------


def test_ensure_system_automations_seeds_video_with_owned_by_and_mode():
    """The video post-download chain twins must seed with owned_by='video' so they
    stay off the music page; the standalone 'Scan Video Library' is NO LONGER seeded
    (superseded by the chain). Music system automations keep owned_by=None."""
    db = MagicMock()
    db.get_system_automation_by_action.return_value = None  # nothing seeded yet
    created = {}

    def _create(**kw):
        created[kw['action_type']] = kw
        return 'id-' + kw['action_type']

    db.create_automation.side_effect = _create

    engine = AutomationEngine(db)
    engine.ensure_system_automations()

    # The standalone scheduled scan is gone; the chain twins are seeded, video-tagged.
    assert 'video_scan_library' not in created
    assert created['video_scan_server']['owned_by'] == 'video'
    assert created['video_update_database']['owned_by'] == 'video'
    assert json.loads(created['video_update_database']['action_config']) == {'mode': 'incremental'}

    # Music automations stay owned_by=None (shown on the music page).
    assert created['scan_library']['owned_by'] is None
    assert json.loads(created['scan_library']['action_config']) == {}


def test_video_scan_library_system_automation_is_cleaned_up():
    """The obsolete standalone 'Scan Video Library' system automation is deleted when
    present (superseded by the post-download chain). No stuck flag — it just keys off
    the lookup, so once the row is gone it no-ops."""
    # present → deleted (matches only the is_system-seeded row)
    db = MagicMock()
    db.get_system_automation_by_action.return_value = {'id': 99, 'is_system': 1}
    AutomationEngine(db)._fix_video_scan_default()
    db.delete_automation.assert_any_call(99)

    # absent (already gone) → idempotent no-op, never errors or deletes
    db2 = MagicMock()
    db2.get_system_automation_by_action.return_value = None
    AutomationEngine(db2)._fix_video_scan_default()
    db2.delete_automation.assert_not_called()


def test_airing_automation_migrates_24h_interval_to_daily_1am():
    """The old rolling-24h 'Auto-Wishlist Episodes Airing Today' row is rewritten to a
    fixed daily 01:00 (server-local) so it stops drifting with restarts."""
    db = MagicMock()
    db.get_system_automation_by_action.return_value = {
        'id': 42, 'is_system': 1, 'trigger_type': 'schedule',
        'trigger_config': json.dumps({'interval': 24, 'unit': 'hours'})}
    eng = AutomationEngine(db)
    eng._default_tz = 'UTC'
    eng._fix_airing_automation_schedule()

    db.update_automation.assert_called_once()
    args, kwargs = db.update_automation.call_args
    assert args[0] == 42
    assert kwargs['trigger_type'] == 'daily_time'
    assert json.loads(kwargs['trigger_config']) == {'time': '01:00'}
    # next_run is armed for 01:00 UTC (the next occurrence)
    assert kwargs['next_run'].endswith(' 01:00:00')


def test_airing_automation_migration_is_idempotent():
    """Once the row is already daily_time, re-running the migration is a no-op (it
    must not rewrite a user's edited time or re-arm next_run every startup)."""
    db = MagicMock()
    db.get_system_automation_by_action.return_value = {
        'id': 42, 'is_system': 1, 'trigger_type': 'daily_time',
        'trigger_config': json.dumps({'time': '06:30'})}
    AutomationEngine(db)._fix_airing_automation_schedule()
    db.update_automation.assert_not_called()

    # absent (fresh install, created straight as daily_time) → no-op, never errors
    db2 = MagicMock()
    db2.get_system_automation_by_action.return_value = None
    AutomationEngine(db2)._fix_airing_automation_schedule()
    db2.update_automation.assert_not_called()


def test_deep_scans_migrate_to_fixed_weekly_times():
    """The two video deep scans move from a rolling 7-day interval to fixed weekly
    times — TV Mondays 02:00, Movies Tuesdays 02:00."""
    rows = {
        'video_deep_scan_tv': {'id': 7, 'is_system': 1, 'trigger_type': 'schedule',
                               'trigger_config': json.dumps({'interval': 7, 'unit': 'days'})},
        'video_deep_scan_movies': {'id': 8, 'is_system': 1, 'trigger_type': 'schedule',
                                   'trigger_config': json.dumps({'interval': 7, 'unit': 'days'})},
    }
    db = MagicMock()
    db.get_system_automation_by_action.side_effect = lambda a: rows.get(a)
    eng = AutomationEngine(db)
    eng._default_tz = 'UTC'
    eng._fix_deep_scan_schedules()

    by_id = {c.args[0]: c.kwargs for c in db.update_automation.call_args_list}
    assert json.loads(by_id[7]['trigger_config']) == {'time': '02:00', 'days': ['mon']}   # TV → Mon
    assert json.loads(by_id[8]['trigger_config']) == {'time': '02:00', 'days': ['tue']}   # Movies → Tue
    assert by_id[7]['trigger_type'] == 'weekly_time' and by_id[8]['trigger_type'] == 'weekly_time'
    assert by_id[7]['next_run'].endswith(' 02:00:00') and by_id[8]['next_run'].endswith(' 02:00:00')


def test_deep_scan_migration_is_idempotent_and_safe_when_absent():
    # already weekly_time → no-op (a hand-tuned day/time isn't reverted)
    db = MagicMock()
    db.get_system_automation_by_action.side_effect = lambda a: {
        'id': 7, 'is_system': 1, 'trigger_type': 'weekly_time',
        'trigger_config': json.dumps({'time': '05:00', 'days': ['sat']})}
    AutomationEngine(db)._fix_deep_scan_schedules()
    db.update_automation.assert_not_called()

    # absent (not seeded yet) → no-op, never errors
    db2 = MagicMock()
    db2.get_system_automation_by_action.return_value = None
    AutomationEngine(db2)._fix_deep_scan_schedules()
    db2.update_automation.assert_not_called()


def test_wishlist_processor_rename_removes_orphans_and_renames_youtube():
    """The Download→Process rename: a DB seeded under the old names has orphaned
    'Auto-Download Movie/Episode Wishlist' rows (dead action_type) — they're deleted (after
    lifting the is_system delete-guard); the YouTube row (action_type unchanged) is renamed
    in place, never deleted."""
    rows = {
        'video_download_movie_wishlist': {'id': 10, 'is_system': 1, 'name': 'Auto-Download Movie Wishlist'},
        'video_download_episode_wishlist': {'id': 11, 'is_system': 1, 'name': 'Auto-Download Episode Wishlist'},
        'video_process_youtube_wishlist': {'id': 12, 'is_system': 1, 'name': 'Auto-Download YouTube Wishlist'},
    }
    db = MagicMock()
    db.get_system_automation_by_action.side_effect = lambda a: rows.get(a)
    AutomationEngine(db)._fix_wishlist_processor_rename()

    db.delete_automation.assert_any_call(10)
    db.delete_automation.assert_any_call(11)
    # is_system=0 lifted on each orphan before deleting
    cleared = {c.args[0] for c in db.update_automation.call_args_list if c.kwargs.get('is_system') == 0}
    assert cleared == {10, 11}
    # youtube renamed (in place), NOT deleted
    renamed = [c for c in db.update_automation.call_args_list if c.kwargs.get('name')]
    assert renamed and renamed[0].args[0] == 12
    assert renamed[0].kwargs['name'] == 'Auto-Process YouTube Wishlist'
    assert all(c.args[0] != 12 for c in db.delete_automation.call_args_list)


def test_wishlist_processor_rename_is_idempotent_when_clean():
    # orphans already gone + youtube already renamed → no deletes, no rename
    rows = {'video_process_youtube_wishlist': {'id': 12, 'is_system': 1, 'name': 'Auto-Process YouTube Wishlist'}}
    db = MagicMock()
    db.get_system_automation_by_action.side_effect = lambda a: rows.get(a)
    AutomationEngine(db)._fix_wishlist_processor_rename()
    db.delete_automation.assert_not_called()
    assert not any(c.kwargs.get('name') for c in db.update_automation.call_args_list)
