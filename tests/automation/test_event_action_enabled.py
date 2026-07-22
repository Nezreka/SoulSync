"""is_event_action_enabled (#995 follow-up).

A simple (non-batch) download fires a library scan directly in the import
pipeline, bypassing the batch_complete automation — so it kept scanning even
with "Auto-Scan After Downloads" turned off ('Media scan completed' notification
that wouldn't stop). The pipeline now gates that direct scan on this helper, so
it honors the same toggle: True only when an ENABLED automation exists for the
(event → action) pair.
"""

from __future__ import annotations

from core.automation_engine import AutomationEngine


class _FakeDB:
    def __init__(self, autos):
        self._autos = {a["id"]: a for a in autos}

    def get_automations(self):
        return list(self._autos.values())

    def get_automation(self, aid):
        return self._autos.get(aid)


def _auto(aid=1, enabled=True, trigger="batch_complete", action="scan_library"):
    return {"id": aid, "name": "Auto-Scan After Downloads",
            "trigger_type": trigger, "action_type": action,
            "enabled": enabled, "trigger_config": "{}"}


def _engine(autos):
    return AutomationEngine(_FakeDB(autos))


def test_enabled_scan_automation_is_true():
    eng = _engine([_auto(enabled=True)])
    assert eng.is_event_action_enabled("batch_complete", "scan_library") is True


def test_disabled_scan_automation_is_false():
    # The exact scenario the user hit: automation off → simple download must not scan.
    eng = _engine([_auto(enabled=False)])
    assert eng.is_event_action_enabled("batch_complete", "scan_library") is False


def test_no_automation_is_false():
    eng = _engine([])
    assert eng.is_event_action_enabled("batch_complete", "scan_library") is False


def test_enabled_but_different_action_is_false():
    # An enabled batch_complete automation with some OTHER action must not count.
    eng = _engine([_auto(enabled=True, action="start_database_update")])
    assert eng.is_event_action_enabled("batch_complete", "scan_library") is False


def test_enabled_but_different_event_is_false():
    eng = _engine([_auto(enabled=True, trigger="schedule")])
    assert eng.is_event_action_enabled("batch_complete", "scan_library") is False


def test_picks_the_enabled_one_among_several():
    eng = _engine([
        _auto(aid=1, enabled=False, action="scan_library"),
        _auto(aid=2, enabled=True, action="scan_library"),
    ])
    assert eng.is_event_action_enabled("batch_complete", "scan_library") is True
