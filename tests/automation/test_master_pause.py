"""The per-side global automation pause (the Automations pages' master toggles).

Contract: the master gates whether anything RUNS — individual enabled flags are
never touched. Scheduled slots skip but stay scheduled (so un-pausing resumes at
the normal cadence), event triggers are dropped, manual run_now still executes,
and direct-effect callers (is_event_action_enabled) see a paused side as off.
State lives in the engine DB's `metadata` KV (music_library.db), NEVER
config.json. Sides: owned_by='video' or a video_* trigger/action → video;
everything else → music. Defaults: music ON (historic behaviour), video OFF
(most installs predate the video side).
"""

from unittest.mock import MagicMock

from core.automation_engine import AutomationEngine


def _engine(masters, auto):
    """masters = {metadata_key: '1'/'0'} — the DB rows backing the toggles."""
    db = MagicMock()
    db.get_automation.return_value = auto
    db.update_automation_run = MagicMock(return_value=True)
    db.get_metadata.side_effect = lambda key: masters.get(key)
    db.set_metadata.side_effect = lambda key, value: masters.__setitem__(key, value)
    eng = AutomationEngine(db)
    eng._running = True
    eng.schedule_automation = MagicMock()   # no real timers in tests
    calls = []
    eng._action_handlers[auto['action_type']] = {
        'handler': lambda config: calls.append(config) or {'status': 'completed'},
        'guard': None,
    }
    return eng, db, calls


_MUSIC_KEY = AutomationEngine.MASTER_KEYS['music']
_VIDEO_KEY = AutomationEngine.MASTER_KEYS['video']

_MUSIC_AUTO = {'id': 1, 'name': 'Auto-Sync', 'enabled': True,
               'action_type': 'sync_playlist', 'action_config': '{}',
               'trigger_type': 'schedule', 'trigger_config': '{"interval": 1}',
               'profile_id': 1}
_VIDEO_AUTO = {'id': 2, 'name': 'Process Video Wishlist', 'enabled': True,
               'action_type': 'video_process_episode_wishlist', 'action_config': '{}',
               'trigger_type': 'schedule', 'trigger_config': '{"interval": 1}',
               'owned_by': 'video', 'profile_id': 1}


# ── side classification + defaults ───────────────────────────────────────────

def test_automation_side_classification():
    side = AutomationEngine.automation_side
    assert side(_VIDEO_AUTO) == 'video'                                   # owned_by
    assert side({'action_type': 'video_scan_server'}) == 'video'          # action prefix
    assert side({'trigger_type': 'video_batch_complete'}) == 'video'      # trigger prefix
    assert side(_MUSIC_AUTO) == 'music'
    assert side({}) == 'music'


def test_master_defaults_music_on_video_off():
    # No DB rows yet → the shipped defaults apply.
    eng, _, _ = _engine({}, _MUSIC_AUTO)
    assert eng.master_enabled('music') is True
    assert eng.master_enabled('video') is False


def test_master_state_round_trips_through_the_db():
    masters = {}
    eng, db, _ = _engine(masters, _MUSIC_AUTO)
    assert eng.set_master_enabled('music', False) is True
    assert masters[_MUSIC_KEY] == '0'              # persisted as a metadata row
    assert eng.master_enabled('music') is False    # read back live
    eng.set_master_enabled('video', True)
    assert eng.master_enabled('video') is True
    assert eng.set_master_enabled('weird', True) is False


def test_db_error_falls_back_to_the_side_default():
    eng, db, _ = _engine({}, _MUSIC_AUTO)
    db.get_metadata.side_effect = RuntimeError('locked')
    assert eng.master_enabled('music') is True
    assert eng.master_enabled('video') is False


# ── scheduled runs ───────────────────────────────────────────────────────────

def test_paused_side_skips_scheduled_run_but_keeps_the_schedule():
    eng, db, calls = _engine({_MUSIC_KEY: '0'}, _MUSIC_AUTO)
    eng.run_automation(1, skip_delay=False)
    assert calls == []                                    # action never ran
    db.update_automation_run.assert_called_once()         # skip recorded…
    eng.schedule_automation.assert_called_once_with(1)    # …and the schedule stays alive


def test_enabled_side_runs_scheduled_normally():
    eng, _, calls = _engine({_MUSIC_KEY: '1'}, _MUSIC_AUTO)
    eng.run_automation(1, skip_delay=False)
    assert len(calls) == 1


def test_video_default_off_pauses_video_scheduled_runs():
    # A fresh DB (no video row saved) must NOT run video automations.
    eng, _, calls = _engine({}, _VIDEO_AUTO)
    eng.run_automation(2, skip_delay=False)
    assert calls == []


def test_video_pause_does_not_touch_music():
    eng, _, calls = _engine({_VIDEO_KEY: '0', _MUSIC_KEY: '1'}, _MUSIC_AUTO)
    eng.run_automation(1, skip_delay=False)
    assert len(calls) == 1


def test_manual_run_now_bypasses_the_pause():
    # An explicit click outranks the global pause.
    eng, _, calls = _engine({_VIDEO_KEY: '0'}, _VIDEO_AUTO)
    eng.run_automation(2, skip_delay=True)
    assert len(calls) == 1


# ── event triggers ───────────────────────────────────────────────────────────

def test_paused_side_drops_event_automations():
    eng, _, calls = _engine({_VIDEO_KEY: '0'}, _VIDEO_AUTO)
    eng._run_event_automation(_VIDEO_AUTO, 2, {'some': 'event'})
    assert calls == []


def test_enabled_side_runs_event_automations():
    eng, _, calls = _engine({_VIDEO_KEY: '1'}, _VIDEO_AUTO)
    eng._run_event_automation(_VIDEO_AUTO, 2, {'some': 'event'})
    assert len(calls) == 1


def test_is_event_action_enabled_honors_the_pause():
    masters = {_MUSIC_KEY: '0'}
    eng, db, _ = _engine(masters, dict(_MUSIC_AUTO, trigger_type='batch_complete'))
    eng._event_automations = {'batch_complete': [1]}
    eng._event_cache_dirty = False
    assert eng.is_event_action_enabled('batch_complete', 'sync_playlist') is False
    masters[_MUSIC_KEY] = '1'
    assert eng.is_event_action_enabled('batch_complete', 'sync_playlist') is True
