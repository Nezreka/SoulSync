"""Regression tests for AutomationEngine handler-error storage.

The Discord-reported "Clean Search History" error
(`'DownloadOrchestrator' object has no attribute 'base_url'`) stayed
visible on the automation card long after the underlying handler bug
was fixed because the engine only stored uncaught exceptions in
``last_error``. Handlers that report failure by RETURNING
``{'status': 'error', ...}`` were treated as successful from the
engine's perspective, so subsequent successful runs never cleared the
stale error.

These tests pin the new behaviour: every reported failure mode
(``status=error`` with any of ``error`` / ``reason`` / ``message``,
or no key at all) must surface to ``update_automation_run`` so the
DB row reflects reality and a successful next run clears it.
"""

from unittest.mock import MagicMock

import pytest

from core.automation_engine import AutomationEngine


@pytest.fixture
def engine_with_handler():
    """Build an AutomationEngine with a stub DB and a stub handler we can swap.

    Returns a tuple of (engine, db_mock, set_handler) where set_handler
    swaps in the handler that the next run_automation call will execute.
    """
    db_mock = MagicMock()
    db_mock.get_automation.return_value = {
        'id': 1,
        'name': 'Clean Search History',
        'enabled': True,
        'action_type': 'clean_search_history',
        'action_config': '{}',
        'trigger_type': 'interval_hours',
        'trigger_config': '{"hours": 1}',
    }
    db_mock.update_automation_run = MagicMock(return_value=True)

    engine = AutomationEngine(db_mock)
    engine._running = True

    handler_holder = {'fn': lambda config: {'status': 'completed'}}

    def set_handler(fn):
        handler_holder['fn'] = fn

    engine._action_handlers['clean_search_history'] = {
        'handler': lambda config: handler_holder['fn'](config),
        'guard': None,
    }
    return engine, db_mock, set_handler


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_successful_run_clears_last_error(engine_with_handler) -> None:
    """A clean run must save error=None so any stale stored error clears."""
    engine, db_mock, set_handler = engine_with_handler
    set_handler(lambda config: {'status': 'completed'})
    engine.run_automation(1, skip_delay=True)
    db_mock.update_automation_run.assert_called_once()
    kwargs = db_mock.update_automation_run.call_args.kwargs
    assert kwargs.get('error') is None


def test_handler_returning_error_key_stores_it(engine_with_handler) -> None:
    """status=error with an 'error' key must populate last_error."""
    engine, db_mock, set_handler = engine_with_handler
    set_handler(lambda config: {'status': 'error', 'error': "no attribute 'base_url'"})
    engine.run_automation(1, skip_delay=True)
    kwargs = db_mock.update_automation_run.call_args.kwargs
    assert kwargs.get('error') == "no attribute 'base_url'"


def test_handler_returning_reason_key_stores_it(engine_with_handler) -> None:
    """Older handlers use 'reason' instead of 'error'. Must still surface."""
    engine, db_mock, set_handler = engine_with_handler
    set_handler(lambda config: {'status': 'error', 'reason': 'slskd unreachable'})
    engine.run_automation(1, skip_delay=True)
    kwargs = db_mock.update_automation_run.call_args.kwargs
    assert kwargs.get('error') == 'slskd unreachable'


def test_handler_returning_message_key_stores_it(engine_with_handler) -> None:
    """Some action handlers use 'message'. Must still surface."""
    engine, db_mock, set_handler = engine_with_handler
    set_handler(lambda config: {'status': 'error', 'message': 'rate limited'})
    engine.run_automation(1, skip_delay=True)
    kwargs = db_mock.update_automation_run.call_args.kwargs
    assert kwargs.get('error') == 'rate limited'


def test_handler_returning_error_status_with_no_message_stores_placeholder(
    engine_with_handler,
) -> None:
    """status=error with no detail key must still record SOMETHING so
    last_error is non-null and the UI can flag the run as failed."""
    engine, db_mock, set_handler = engine_with_handler
    set_handler(lambda config: {'status': 'error'})
    engine.run_automation(1, skip_delay=True)
    kwargs = db_mock.update_automation_run.call_args.kwargs
    assert kwargs.get('error') == 'Handler reported failure'


def test_handler_raising_exception_still_stores_error(engine_with_handler) -> None:
    """The original behaviour (uncaught exceptions get caught + stored)
    must keep working — this is the case that originally surfaced the
    Discord-reported AttributeError before the fix."""
    engine, db_mock, set_handler = engine_with_handler

    def raising_handler(config):
        raise AttributeError("'DownloadOrchestrator' object has no attribute 'base_url'")
    set_handler(raising_handler)

    engine.run_automation(1, skip_delay=True)
    kwargs = db_mock.update_automation_run.call_args.kwargs
    assert kwargs.get('error') and 'base_url' in kwargs['error']


def test_skipped_status_records_no_error(engine_with_handler) -> None:
    """status=skipped is a normal outcome, must not look like a failure."""
    engine, db_mock, set_handler = engine_with_handler
    set_handler(lambda config: {'status': 'skipped', 'reason': 'not configured'})
    engine.run_automation(1, skip_delay=True)
    kwargs = db_mock.update_automation_run.call_args.kwargs
    assert kwargs.get('error') is None
