"""The engine runs each automation AS its owner profile (per-profile automations).

A non-admin's scheduled job must execute with their profile in the background
context, so get_current_profile_id() / the per-profile clients act as them — not
admin. Admin/system automations (profile 1) are unchanged. The context must be
reset after every run so a pooled thread can't leak it.
"""

from unittest.mock import MagicMock

import pytest

from core.automation_engine import AutomationEngine
from core.profile_context import get_background_profile


def _engine(owner_profile_id):
    db = MagicMock()
    db.get_automation.return_value = {
        'id': 1, 'name': 'Auto-Sync', 'enabled': True,
        'action_type': 'sync_playlist', 'action_config': '{}',
        'trigger_type': 'interval_hours', 'trigger_config': '{"hours": 1}',
        'profile_id': owner_profile_id,
    }
    db.update_automation_run = MagicMock(return_value=True)
    eng = AutomationEngine(db)
    eng._running = True
    seen = {}
    eng._action_handlers['sync_playlist'] = {
        'handler': lambda config: seen.update(profile=get_background_profile()) or {'status': 'completed'},
        'guard': None,
    }
    return eng, seen


def test_nonadmin_owned_automation_runs_as_owner():
    eng, seen = _engine(owner_profile_id=4)
    eng.run_automation(1, skip_delay=True)
    assert seen['profile'] == 4                 # handler ran AS profile 4
    assert get_background_profile() is None      # reset after the run


def test_admin_owned_automation_runs_as_admin():
    eng, seen = _engine(owner_profile_id=1)
    eng.run_automation(1, skip_delay=True)
    assert seen['profile'] == 1                 # unchanged for admin/system
    assert get_background_profile() is None


def test_explicit_trigger_profile_overrides_owner():
    # A manual trigger (run_automation(profile_id=...)) wins over the owner.
    eng, seen = _engine(owner_profile_id=4)
    eng.run_automation(1, skip_delay=True, profile_id=9)
    assert seen['profile'] == 9


def test_context_reset_even_when_handler_raises():
    eng, _ = _engine(owner_profile_id=4)
    eng._action_handlers['sync_playlist'] = {
        'handler': lambda config: (_ for _ in ()).throw(RuntimeError('boom')),
        'guard': None,
    }
    eng.run_automation(1, skip_delay=True)       # error is caught + stored
    assert get_background_profile() is None       # finally reset it
