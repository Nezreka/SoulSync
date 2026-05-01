"""Tests for core/automation/progress.py — progress state lifecycle + emit loop."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from core.automation import progress


@pytest.fixture(autouse=True)
def reset_state():
    """Each test gets a clean progress state dict."""
    progress.progress_states.clear()
    yield
    progress.progress_states.clear()


# ---------------------------------------------------------------------------
# init_progress
# ---------------------------------------------------------------------------

def test_init_progress_seeds_running_state():
    progress.init_progress(7, 'My Automation', 'process_wishlist')
    state = progress.progress_states[7]
    assert state['status'] == 'running'
    assert state['action_type'] == 'process_wishlist'
    assert state['progress'] == 0
    assert state['phase'] == 'Starting...'
    assert state['log'][0] == {'type': 'info', 'text': 'Starting My Automation'}
    assert state['started_at'] is not None
    assert state['finished_at'] is None


def test_init_progress_overwrites_existing_state():
    progress.init_progress(7, 'First', 'a')
    progress.init_progress(7, 'Second', 'b')
    assert progress.progress_states[7]['action_type'] == 'b'


# ---------------------------------------------------------------------------
# update_progress
# ---------------------------------------------------------------------------

def test_update_progress_writes_simple_fields():
    progress.init_progress(1, 'A', 'x')
    progress.update_progress(1, progress=42, phase='Working')
    assert progress.progress_states[1]['progress'] == 42
    assert progress.progress_states[1]['phase'] == 'Working'


def test_update_progress_log_line_appends_with_type():
    progress.init_progress(1, 'A', 'x')
    progress.update_progress(1, log_line='hello', log_type='success')
    log = progress.progress_states[1]['log']
    assert log[-1] == {'type': 'success', 'text': 'hello'}


def test_update_progress_log_caps_at_50_entries():
    progress.init_progress(1, 'A', 'x')
    for i in range(60):
        progress.update_progress(1, log_line=f'line {i}')
    assert len(progress.progress_states[1]['log']) == 50
    assert progress.progress_states[1]['log'][-1]['text'] == 'line 59'


def test_update_progress_log_type_not_stored_as_field():
    progress.init_progress(1, 'A', 'x')
    progress.update_progress(1, log_line='hi', log_type='warning')
    assert 'log_type' not in progress.progress_states[1]


def test_update_progress_finish_sets_finished_at_and_emits():
    progress.init_progress(1, 'A', 'x')
    emitted = []
    def _emit(event, data):
        emitted.append((event, data))
    progress.update_progress(1, status='finished', socketio_emit=_emit)
    assert progress.progress_states[1]['finished_at'] is not None
    assert emitted[0][0] == 'automation:progress'
    assert '1' in emitted[0][1]


def test_update_progress_error_status_also_emits():
    progress.init_progress(1, 'A', 'x')
    emitted = []
    progress.update_progress(1, status='error', socketio_emit=lambda e, d: emitted.append(e))
    assert emitted == ['automation:progress']


def test_update_progress_running_status_does_not_emit():
    progress.init_progress(1, 'A', 'x')
    emitted = []
    progress.update_progress(1, status='running', progress=50, socketio_emit=lambda e, d: emitted.append(e))
    assert emitted == []


def test_update_progress_emit_failure_swallowed():
    progress.init_progress(1, 'A', 'x')
    def _bad(event, data):
        raise RuntimeError('socket dead')
    # Should NOT raise
    progress.update_progress(1, status='finished', socketio_emit=_bad)
    assert progress.progress_states[1]['finished_at'] is not None


def test_update_progress_none_id_is_noop():
    progress.update_progress(None, progress=99)  # no exception


def test_update_progress_unknown_id_is_noop():
    progress.update_progress(999, progress=99)
    assert 999 not in progress.progress_states


# ---------------------------------------------------------------------------
# get_running_progress
# ---------------------------------------------------------------------------

def test_get_running_progress_returns_running_finished_error():
    progress.init_progress(1, 'A', 'x')
    progress.init_progress(2, 'B', 'y')
    progress.init_progress(3, 'C', 'z')
    progress.update_progress(2, status='finished')
    progress.update_progress(3, status='error')
    progress.progress_states[4] = {'status': 'unknown', 'log': []}

    snapshot = progress.get_running_progress()
    assert set(snapshot.keys()) == {'1', '2', '3'}


def test_get_running_progress_copies_log_list():
    progress.init_progress(1, 'A', 'x')
    progress.update_progress(1, log_line='first')
    snapshot = progress.get_running_progress()
    snapshot['1']['log'].append({'type': 'info', 'text': 'mutated'})
    # Original state should not be affected
    assert len(progress.progress_states[1]['log']) == 2  # init + 'first'


# ---------------------------------------------------------------------------
# record_history
# ---------------------------------------------------------------------------

class _FakeDB:
    def __init__(self):
        self.calls = []

    def insert_automation_run_history(self, **kw):
        self.calls.append(kw)


def test_record_history_uses_progress_log_when_available():
    progress.init_progress(1, 'A', 'wishlist')
    progress.update_progress(1, log_line='did stuff', log_type='success')
    progress.update_progress(1, status='finished', socketio_emit=None)

    db = _FakeDB()
    progress.record_history(1, {'status': 'completed'}, db)

    assert len(db.calls) == 1
    call = db.calls[0]
    assert call['automation_id'] == 1
    assert call['status'] == 'completed'
    assert call['summary'] == 'did stuff'
    assert call['duration_seconds'] is not None


def test_record_history_status_mapping():
    db = _FakeDB()
    progress.record_history(1, {'status': 'error'}, db)
    assert db.calls[-1]['status'] == 'error'

    progress.record_history(2, {'status': 'skipped'}, db)
    assert db.calls[-1]['status'] == 'skipped'

    progress.record_history(3, {'status': 'timeout'}, db)
    assert db.calls[-1]['status'] == 'timeout'

    progress.record_history(4, {'status': 'completed'}, db)
    assert db.calls[-1]['status'] == 'completed'


def test_record_history_underscore_keys_stripped_from_result_json():
    progress.init_progress(1, 'A', 'x')
    progress.update_progress(1, status='finished', socketio_emit=None)
    db = _FakeDB()
    progress.record_history(1, {'status': 'completed', '_internal': 'x', 'visible': 'y'}, db)

    import json as _json
    parsed = _json.loads(db.calls[0]['result_json'])
    assert '_internal' not in parsed
    assert parsed.get('visible') == 'y'


def test_record_history_falls_back_to_result_summary_when_no_log():
    db = _FakeDB()
    progress.record_history(1, {'status': 'error', 'reason': 'bad config'}, db)
    assert db.calls[0]['summary'] == 'bad config'


def test_record_history_no_progress_state_uses_now_for_times():
    db = _FakeDB()
    progress.record_history(99, {'status': 'completed'}, db)
    call = db.calls[0]
    assert call['started_at'] is not None
    assert call['finished_at'] is not None


def test_record_history_db_failure_swallowed():
    class _BrokenDB:
        def insert_automation_run_history(self, **kw):
            raise RuntimeError('db dead')
    progress.init_progress(1, 'A', 'x')
    # Should NOT raise
    progress.record_history(1, {'status': 'completed'}, _BrokenDB())


# ---------------------------------------------------------------------------
# emit_progress_loop
# ---------------------------------------------------------------------------

class _FakeSocket:
    def __init__(self):
        self.emitted = []
        self._sleep_count = 0
        self._max_sleeps = 1

    def sleep(self, seconds):
        self._sleep_count += 1

    def emit(self, event, data):
        self.emitted.append((event, data))


def _shutdown_after(n_sleeps, sock):
    """Build an is_shutting_down predicate that returns True after n loops."""
    def _check():
        return sock._sleep_count >= n_sleeps
    return _check


def test_emit_loop_emits_running_state():
    progress.init_progress(1, 'A', 'x')
    progress.update_progress(1, progress=50)
    sock = _FakeSocket()
    progress.emit_progress_loop(sock, is_shutting_down=_shutdown_after(1, sock), poll_interval=0)
    assert len(sock.emitted) == 1
    assert sock.emitted[0][0] == 'automation:progress'
    assert '1' in sock.emitted[0][1]


def test_emit_loop_attempts_timeout_check_with_naive_datetime():
    """Documents pre-existing bug: started_at is tz-aware, now is naive,
    so subtraction raises TypeError → caught → timeout never fires.
    Lift preserves the bug; fix lives in a separate PR.
    """
    progress.init_progress(1, 'A', 'x')
    state = progress.progress_states[1]
    state['started_at'] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    sock = _FakeSocket()
    progress.emit_progress_loop(sock, is_shutting_down=_shutdown_after(1, sock),
                                  poll_interval=0, timeout_seconds=7200)
    # Bug: timeout doesn't fire because datetime.now() is naive but
    # started_at is tz-aware → subtraction raises → except → fall through
    # to the normal "running" branch. State stays 'running'.
    assert progress.progress_states[1]['status'] == 'running'


def test_emit_loop_reaps_finished_states_due_to_naive_aware_mismatch():
    """Documents pre-existing bug: finished_at is tz-aware, now is naive,
    so the cleanup math raises → caught → state is reaped on FIRST tick
    regardless of `cleanup_after_seconds`. Lift preserves the bug.
    """
    progress.init_progress(1, 'A', 'x')
    progress.update_progress(1, status='finished', socketio_emit=None)
    sock = _FakeSocket()
    progress.emit_progress_loop(sock, is_shutting_down=_shutdown_after(1, sock),
                                  poll_interval=0, cleanup_after_seconds=300)
    # Bug: should be kept (300s window, just finished), but the TypeError
    # in the cleanup math is caught with a fall-through that ALSO reaps.
    assert 1 not in progress.progress_states


def test_emit_loop_no_active_states_no_emit():
    sock = _FakeSocket()
    progress.emit_progress_loop(sock, is_shutting_down=_shutdown_after(1, sock), poll_interval=0)
    assert sock.emitted == []
