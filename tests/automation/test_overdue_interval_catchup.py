"""an interval automation whose slot passed while the app was down runs soon
after restart, not a full interval later.

_setup_schedule_trigger honoured a FUTURE next_run and fell through to the
full interval for a past one. so a weekly automation on an install that
restarts more often than weekly (docker updates, reboots) re-armed seven
days out every start and never ran. ensure_system_automations already
caught the seeded rows up ("overdue or clock skew: run after initial
delay"); the rows users make got nothing.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.automation_engine import AutomationEngine

WEEK = 7 * 86400


@pytest.fixture()
def engine():
    db = MagicMock()
    db.update_automation = MagicMock(return_value=True)
    db.get_automation.return_value = None
    eng = AutomationEngine(db)
    eng._running = True
    return eng, db


def _row(next_run_offset_seconds, *, aid=7, interval=7, unit='days'):
    stamp = None
    if next_run_offset_seconds is not None:
        stamp = (datetime.now(timezone.utc) + timedelta(seconds=next_run_offset_seconds)).strftime('%Y-%m-%d %H:%M:%S')
    return {'id': aid, 'enabled': 1, 'trigger_type': 'schedule',
            'trigger_config': json.dumps({'interval': interval, 'unit': unit}), 'next_run': stamp}


def _arm(eng, db, row, config=None):
    db.get_automation.return_value = row
    config = config or json.loads(row['trigger_config'])
    with patch('core.automation_engine.threading.Timer') as timer_cls:
        timer_cls.return_value = MagicMock()
        eng._setup_schedule_trigger(row['id'], config)
    delay = timer_cls.call_args.args[0]
    written = db.update_automation.call_args.kwargs['next_run']
    return delay, datetime.strptime(written, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)


def test_a_missed_weekly_slot_runs_within_minutes(engine):
    eng, db = engine
    delay, written = _arm(eng, db, _row(-86400))          # slot was yesterday
    assert delay < 600, f"re-armed {delay/86400:.1f} days out"
    assert delay >= eng._OVERDUE_CATCHUP_SECONDS
    assert abs((written - datetime.now(timezone.utc)).total_seconds() - delay) < 5


def test_a_future_slot_is_still_honoured(engine):
    eng, db = engine
    delay, _ = _arm(eng, db, _row(3600))                   # due in an hour
    assert 3590 < delay <= 3600


def test_no_next_run_still_means_a_full_interval(engine):
    eng, db = engine
    delay, _ = _arm(eng, db, _row(None))
    assert delay == WEEK


def test_a_malformed_next_run_still_means_a_full_interval(engine):
    eng, db = engine
    row = _row(None)
    row['next_run'] = 'not a date'
    delay, _ = _arm(eng, db, row)
    assert delay == WEEK


def test_catch_up_never_waits_longer_than_the_interval_itself(engine):
    eng, db = engine
    delay, _ = _arm(eng, db, _row(-30, interval=1, unit='minutes'))
    assert delay == 60


def test_overdue_rows_are_staggered_by_id(engine):
    eng, db = engine
    delays = {aid: _arm(eng, db, _row(-3600, aid=aid))[0] for aid in range(1, 13)}
    assert len(set(delays.values())) == eng._OVERDUE_STAGGER_SLOTS
    assert min(delays.values()) == eng._OVERDUE_CATCHUP_SECONDS
    assert max(delays.values()) == eng._OVERDUE_CATCHUP_SECONDS + (eng._OVERDUE_STAGGER_SLOTS - 1) * eng._OVERDUE_STAGGER_SECONDS


def test_start_catches_up_a_missed_user_automation(engine):
    """through the front door: start() schedules every enabled row"""
    eng, db = engine
    row = _row(-86400)
    db.get_automations.return_value = [row]
    db.get_automation.return_value = row
    db.get_system_automation_by_action.return_value = None
    with patch('core.automation_engine.threading.Timer') as timer_cls, \
            patch.object(eng, 'ensure_system_automations'):
        timer_cls.return_value = MagicMock()
        eng.start()
    assert timer_cls.call_args.args[0] < 600


def test_after_the_catch_up_run_the_normal_cadence_resumes(engine):
    """_finish_run writes the next slot a full interval out, so one catch-up
    does not turn into a run every two minutes"""
    eng, db = engine
    row = _row(-86400)
    db.get_automation.return_value = row
    db.update_automation_run = MagicMock(return_value=True)
    with patch('core.automation_engine.threading.Timer') as timer_cls:
        timer_cls.return_value = MagicMock()
        eng._finish_run(row, row['id'], {'status': 'completed'}, error=None)
    written = db.update_automation_run.call_args.kwargs['next_run']
    nxt = datetime.strptime(written, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    assert abs((nxt - datetime.now(timezone.utc)).total_seconds() - WEEK) < 5
