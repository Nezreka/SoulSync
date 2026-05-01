"""Tests for core/downloads/cancel.py — slskd cancel + clear + local task pruning."""

from __future__ import annotations

import pytest

from core.downloads import cancel
from core.runtime_state import (
    batch_locks,
    download_batches,
    download_tasks,
)


@pytest.fixture(autouse=True)
def reset_state():
    """Each test gets clean download_tasks / download_batches / batch_locks."""
    download_tasks.clear()
    download_batches.clear()
    batch_locks.clear()
    yield
    download_tasks.clear()
    download_batches.clear()
    batch_locks.clear()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeSoulseek:
    def __init__(self, cancel_result=True, cancel_all_result=True, clear_result=True):
        self._cancel_result = cancel_result
        self._cancel_all_result = cancel_all_result
        self._clear_result = clear_result
        self.cancel_calls = []
        self.cancel_all_calls = 0
        self.clear_calls = 0

    async def cancel_download(self, download_id, username, remove=False):
        self.cancel_calls.append((download_id, username, remove))
        return self._cancel_result

    async def cancel_all_downloads(self):
        self.cancel_all_calls += 1
        return self._cancel_all_result

    async def clear_all_completed_downloads(self):
        self.clear_calls += 1
        return self._clear_result


def _sync_run_async(coro):
    """Drain a coroutine on a fresh loop."""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# cancel_single_download
# ---------------------------------------------------------------------------

def test_cancel_single_passes_args_with_remove_true():
    sk = _FakeSoulseek()
    result = cancel.cancel_single_download(sk, _sync_run_async, 'dl-123', 'user-x')
    assert result is True
    assert sk.cancel_calls == [('dl-123', 'user-x', True)]


def test_cancel_single_propagates_failure():
    sk = _FakeSoulseek(cancel_result=False)
    result = cancel.cancel_single_download(sk, _sync_run_async, 'dl', 'u')
    assert result is False


# ---------------------------------------------------------------------------
# cancel_all_active
# ---------------------------------------------------------------------------

def test_cancel_all_happy_path():
    sk = _FakeSoulseek()
    sweeps = []
    success, msg = cancel.cancel_all_active(sk, _sync_run_async, lambda: sweeps.append(1))
    assert success is True
    assert msg == "All downloads cancelled and cleared."
    assert sk.cancel_all_calls == 1
    assert sk.clear_calls == 1
    assert sweeps == [1]


def test_cancel_all_returns_failure_if_cancel_step_fails():
    sk = _FakeSoulseek(cancel_all_result=False)
    sweeps = []
    success, msg = cancel.cancel_all_active(sk, _sync_run_async, lambda: sweeps.append(1))
    assert success is False
    assert msg == "Failed to cancel active downloads."
    # Clear/sweep should NOT run when cancel fails
    assert sk.clear_calls == 0
    assert sweeps == []


def test_cancel_all_runs_sweep_even_if_clear_returns_false():
    """Clear returning False is not a hard error — sweep still runs (matches original)."""
    sk = _FakeSoulseek(clear_result=False)
    sweeps = []
    success, msg = cancel.cancel_all_active(sk, _sync_run_async, lambda: sweeps.append(1))
    assert success is True
    assert sweeps == [1]


# ---------------------------------------------------------------------------
# clear_finished_active
# ---------------------------------------------------------------------------

def test_clear_finished_happy_path_calls_sweep():
    sk = _FakeSoulseek()
    sweeps = []
    success = cancel.clear_finished_active(sk, _sync_run_async, lambda: sweeps.append(1))
    assert success is True
    assert sk.clear_calls == 1
    assert sweeps == [1]


def test_clear_finished_failure_skips_sweep():
    sk = _FakeSoulseek(clear_result=False)
    sweeps = []
    success = cancel.clear_finished_active(sk, _sync_run_async, lambda: sweeps.append(1))
    assert success is False
    assert sweeps == []


# ---------------------------------------------------------------------------
# clear_completed_local
# ---------------------------------------------------------------------------

def test_clear_completed_removes_terminal_tasks():
    download_tasks['t1'] = {'status': 'completed'}
    download_tasks['t2'] = {'status': 'failed'}
    download_tasks['t3'] = {'status': 'downloading'}  # still active
    download_tasks['t4'] = {'status': 'cancelled'}
    download_tasks['t5'] = {'status': 'not_found'}
    download_tasks['t6'] = {'status': 'skipped'}
    download_tasks['t7'] = {'status': 'already_owned'}

    cleared = cancel.clear_completed_local()
    assert cleared == 6
    assert set(download_tasks.keys()) == {'t3'}


def test_clear_completed_keeps_searching_and_queued():
    """Active states ('searching', 'queued', 'downloading', 'pending') stay."""
    download_tasks['t1'] = {'status': 'searching'}
    download_tasks['t2'] = {'status': 'queued'}
    download_tasks['t3'] = {'status': 'downloading'}
    download_tasks['t4'] = {'status': 'pending'}
    cleared = cancel.clear_completed_local()
    assert cleared == 0
    assert set(download_tasks.keys()) == {'t1', 't2', 't3', 't4'}


def test_clear_completed_drops_empty_batches():
    download_tasks['t1'] = {'status': 'completed'}
    download_batches['b1'] = {'queue': ['t1']}  # all tasks will be cleared
    download_batches['b2'] = {'queue': ['t2']}  # t2 doesn't exist either
    download_tasks['t3'] = {'status': 'downloading'}
    download_batches['b3'] = {'queue': ['t3']}  # t3 stays

    cancel.clear_completed_local()
    assert 'b1' not in download_batches
    assert 'b2' not in download_batches
    assert 'b3' in download_batches
    assert download_batches['b3']['queue'] == ['t3']


def test_clear_completed_prunes_terminal_task_ids_from_batch_queues():
    """Batch with mix of terminal + active tasks gets queue trimmed, not deleted."""
    download_tasks['t1'] = {'status': 'completed'}
    download_tasks['t2'] = {'status': 'downloading'}
    download_batches['b1'] = {'queue': ['t1', 't2']}

    cancel.clear_completed_local()
    assert 'b1' in download_batches
    assert download_batches['b1']['queue'] == ['t2']


def test_clear_completed_drops_batch_locks_for_deleted_batches():
    import threading
    download_tasks['t1'] = {'status': 'completed'}
    download_batches['b1'] = {'queue': ['t1']}
    batch_locks['b1'] = threading.Lock()

    cancel.clear_completed_local()
    assert 'b1' not in batch_locks


def test_clear_completed_keeps_batch_locks_for_surviving_batches():
    import threading
    download_tasks['t1'] = {'status': 'downloading'}
    download_batches['b1'] = {'queue': ['t1']}
    batch_locks['b1'] = threading.Lock()

    cancel.clear_completed_local()
    assert 'b1' in batch_locks


def test_clear_completed_returns_zero_on_empty_state():
    assert cancel.clear_completed_local() == 0
