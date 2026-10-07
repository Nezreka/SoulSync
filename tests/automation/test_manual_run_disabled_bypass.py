"""A disabled automation never runs on schedule, but an explicit manual
Run Now still executes it.

Contract: the enabled toggle controls scheduled (and event) runs only. A
manual trigger is an explicit user action and always runs — e.g. the
Audiobook Library page's Scan folder, which routes through the engine's
Run Now, must scan even when "Auto-Scan Audiobook Library" is disabled.
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

_DISABLED_AUTO = {'id': 7, 'name': 'Auto-Scan Audiobook Library', 'enabled': False,
                  'action_type': 'audiobook_scan_library', 'action_config': '{}',
                  'trigger_type': 'schedule', 'trigger_config': '{"interval": 6}',
                  'profile_id': 1}


def test_disabled_automation_skips_scheduled_run():
    eng, db, calls = _engine({_MUSIC_KEY: '1'}, _DISABLED_AUTO)
    eng.run_automation(7, skip_delay=False)
    assert calls == []                                # action never ran
    db.update_automation_run.assert_not_called()      # gated out before the run


def test_disabled_automation_runs_on_manual_run_now():
    # An explicit user click outranks the enabled toggle.
    eng, _, calls = _engine({_MUSIC_KEY: '1'}, _DISABLED_AUTO)
    eng.run_automation(7, skip_delay=True)
    assert len(calls) == 1


def test_enabled_automation_runs_on_both_paths():
    enabled = dict(_DISABLED_AUTO, enabled=True)
    eng, _, calls = _engine({_MUSIC_KEY: '1'}, enabled)
    eng.run_automation(7, skip_delay=False)
    assert len(calls) == 1
    eng.run_automation(7, skip_delay=True)
    assert len(calls) == 2
