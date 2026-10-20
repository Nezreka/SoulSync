"""#1719 seam test: an automation's manual Run Now must reach the
discovery-pool refresh as a forced run, while scheduled and
event-triggered runs keep the scanner's 6-hour cooldown.

The handler-level and scanner-level halves are pinned in
test_handlers_maintenance.py and test_watchlist_scanner_scan.py; this
file pins the seam between them — the engine's ``_manual_run``
injection (``run_automation(skip_delay=True)`` for Run Now,
``skip_delay=False`` for timers, hard ``False`` for event triggers)
feeding the real ``auto_update_discovery_pool`` handler.

Not covered here: the UI → ``engine.run_now()`` hop that produces
``skip_delay=True`` (pre-existing, unchanged by #1719), and the
engine's background-profile resolution — the deps stub returns a
fixed profile id, so the assertions pin handler→scanner forwarding
of that id, not profile selection.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from core.automation_engine import AutomationEngine
from core.automation.handlers.maintenance import auto_update_discovery_pool


_AUTO = {'id': 7, 'name': 'Update Discovery', 'enabled': True,
         'action_type': 'update_discovery_pool', 'action_config': '{}',
         'trigger_type': 'schedule', 'trigger_config': '{"interval": 6}',
         'profile_id': 1}

_MUSIC_KEY = AutomationEngine.MASTER_KEYS['music']


def _engine_and_calls():
    masters = {_MUSIC_KEY: '1'}
    db = MagicMock()
    db.get_automation.return_value = _AUTO
    db.update_automation_run = MagicMock(return_value=True)
    db.get_metadata.side_effect = lambda key: masters.get(key)
    db.set_metadata.side_effect = lambda key, value: masters.__setitem__(key, value)

    eng = AutomationEngine(db)
    eng._running = True
    eng.schedule_automation = MagicMock()  # no real timers in tests

    calls = []

    class _Scanner:
        def update_discovery_pool_incremental(self, profile_id, force=False):
            calls.append((profile_id, force))

    # The handler only touches these four deps attributes.
    deps = SimpleNamespace(
        spotify_client=None,
        get_watchlist_scanner=lambda spc: _Scanner(),
        get_current_profile_id=lambda: 3,
        update_progress=lambda *a, **k: None,
    )
    eng._action_handlers[_AUTO['action_type']] = {
        'handler': lambda config: auto_update_discovery_pool(config, deps),
        'guard': None,
    }
    return eng, calls


def test_manual_run_now_forces_the_refresh():
    eng, calls = _engine_and_calls()
    eng.run_automation(7, skip_delay=True)  # Run Now press
    assert calls == [(3, True)]


def test_scheduled_run_keeps_the_cooldown():
    eng, calls = _engine_and_calls()
    eng.run_automation(7, skip_delay=False)  # timer tick
    assert calls == [(3, False)]


def test_event_triggered_run_keeps_the_cooldown():
    eng, calls = _engine_and_calls()
    eng._run_event_automation(_AUTO, 7, {})
    assert calls == [(3, False)]
