"""#1428: scheduled automations owned by a non-admin profile are never re-armed
after a restart.

AutomationEngine.start() loaded automations with db.get_automations(), which
defaults to profile_id=1 — a non-admin's scheduled automation was only ever
armed by schedule_automation() at creation time, and the first restart dropped
the in-memory timer permanently. The same profile-1 default hid non-admin
event automations from _rebuild_event_cache(), so they never fired either.

The engine now loads via get_all_automations() (unfiltered) while the UI keeps
the profile-filtered get_automations().
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from core.automation_engine import AutomationEngine
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _scheduled(db, profile_id, name):
    return db.create_automation(
        name=name, trigger_type='schedule',
        trigger_config=json.dumps({'interval': 6, 'unit': 'hours'}),
        action_type='playlist_pipeline', action_config='{}',
        profile_id=profile_id,
    )


def _event_based(db, profile_id, name):
    return db.create_automation(
        name=name, trigger_type='download_complete',
        trigger_config=json.dumps({}),
        action_type='scan_library', action_config='{}',
        profile_id=profile_id,
    )


def test_get_all_automations_is_unfiltered(db):
    admin_id = _scheduled(db, 1, 'Admin job')
    user_id = _scheduled(db, 2, 'User job')

    scoped_ids = {a['id'] for a in db.get_automations()}  # profile 1 default, UI view
    assert admin_id in scoped_ids
    assert user_id not in scoped_ids, 'non-admin row must stay out of the profile-1 UI view'

    everything_ids = {a['id'] for a in db.get_all_automations()}
    assert {admin_id, user_id} <= everything_ids


def test_start_rearms_nonadmin_scheduled_automations(db):
    admin_id = _scheduled(db, 1, 'Admin job')
    user_id = _scheduled(db, 2, 'User playlist pipeline')

    eng = AutomationEngine(db)
    with patch('core.automation_engine.threading.Timer') as timer_cls:
        timer_cls.return_value = MagicMock()
        eng.start()

    # Both profiles' timers armed — before the fix only admin_id was.
    assert admin_id in eng._timers, 'admin automation not armed'
    assert user_id in eng._timers, 'non-admin automation not re-armed after restart (#1428)'


def test_event_cache_includes_nonadmin_automations(db):
    admin_id = _event_based(db, 1, 'Admin watcher')
    user_id = _event_based(db, 2, 'User watcher')

    eng = AutomationEngine(db)
    eng._running = True
    eng._rebuild_event_cache()

    cached = eng._event_automations.get('download_complete', [])
    assert admin_id in cached
    assert user_id in cached, 'non-admin event automation missing from cache (#1428)'


def test_disabled_nonadmin_automations_stay_unarmed(db):
    user_id = db.create_automation(
        name='Disabled user job', trigger_type='schedule',
        trigger_config=json.dumps({'interval': 6, 'unit': 'hours'}),
        action_type='playlist_pipeline', action_config='{}',
        profile_id=2,
    )
    db.update_automation(user_id, enabled=False)

    eng = AutomationEngine(db)
    with patch('core.automation_engine.threading.Timer') as timer_cls:
        timer_cls.return_value = MagicMock()
        eng.start()

    assert user_id not in eng._timers, 'disabled automation must not be armed'
