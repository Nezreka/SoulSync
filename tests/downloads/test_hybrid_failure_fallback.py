"""Tests for Bug 1 (gzetk report): Cross-source retry on download failure in hybrid mode.

When Soulseek is the primary source in hybrid mode (e.g. ['soulseek', 'youtube'])
and a download fails (errored, queue timeout, 0% progress timeout, disappearing),
SoulSync must not immediately mark the task as failed. Instead it must:
1. Mark the failed source as exhausted in task['exhausted_download_sources']
2. Cancel the remote transfer in slskd to avoid slot leaks
3. Reset task status to 'searching' and reset retry counters
4. Dispatch a worker restart so the next hybrid source (e.g. YouTube) is tried
5. Only mark failed when all sources in the hybrid chain are exhausted
"""

from __future__ import annotations

import time
from types import SimpleNamespace
import pytest

from core.downloads import monitor as dm


@pytest.fixture
def mon(monkeypatch):
    monkeypatch.setattr(dm, '_make_context_key', lambda u, f: f"{u}::{f}")
    monkeypatch.setattr(dm, '_orphaned_download_keys', set())
    return dm.WebUIDownloadMonitor()


def _task(**over):
    task = {
        'track_info': {'name': 'Oye como va'},
        'username': 'jedzs',
        'filename': r'contemporary\Santana\1-05 Oye como va.flac',
        'download_id': 'dl-1',
        'status': 'downloading',
        'batch_id': 'b1',
        'status_change_time': time.time() - 1000,
    }
    task.update(over)
    return task


def _live(state, progress=0, transferred=0):
    return {
        r'jedzs::contemporary\Santana\1-05 Oye como va.flac': {
            'state': state,
            'percentComplete': progress,
            'bytesTransferred': transferred,
        }
    }


def _run(mon, task, live, now=None):
    ops = []
    now = now or time.time()
    mon._should_retry_task(
        task_id='t1', task=task, live_transfers_lookup=live,
        current_time=now, deferred_ops=ops,
    )
    return ops


def test_errored_download_triggers_hybrid_fallback_to_next_source(mon, monkeypatch):
    """When a Soulseek download errors 3 times, hybrid mode falls back to YouTube."""
    fake_orch = SimpleNamespace(
        mode='hybrid',
        hybrid_order=['soulseek', 'youtube'],
    )
    monkeypatch.setattr(dm, 'download_orchestrator', fake_orch)

    now = time.time()
    task = _task(error_retry_count=3, last_error_retry_time=now - 100)
    ops = _run(mon, task, _live('Completed, Errored'), now)

    # Must NOT fail outright — should transition to searching for next source
    assert task['status'] == 'searching'
    assert 'soulseek' in task.get('exhausted_download_sources', set())
    assert task['error_retry_count'] == 0
    assert ('restart_worker', 't1', 'b1') in ops

    # Remote transfer must be cancelled to avoid holding slskd slots
    cancels = [op for op in ops if op[0] == 'cancel_download']
    assert len(cancels) == 1
    assert cancels[0][1] == 'dl-1'
    assert cancels[0][2] == 'jedzs'


def test_queued_timeout_triggers_hybrid_fallback_to_next_source(mon, monkeypatch):
    """When a Soulseek download sits queued too long 3 times, hybrid mode falls back to YouTube."""
    fake_orch = SimpleNamespace(
        mode='hybrid',
        hybrid_order=['soulseek', 'youtube'],
    )
    monkeypatch.setattr(dm, 'download_orchestrator', fake_orch)

    now = time.time()
    task = _task(stuck_retry_count=3, queued_start_time=now - 200)
    ops = _run(mon, task, _live('Queued, Locally'), now)

    assert task['status'] == 'searching'
    assert 'soulseek' in task.get('exhausted_download_sources', set())
    assert task['stuck_retry_count'] == 0
    assert ('restart_worker', 't1', 'b1') in ops


def test_zero_progress_triggers_hybrid_fallback_to_next_source(mon, monkeypatch):
    """When a Soulseek download gets stuck at 0% 3 times, hybrid mode falls back to YouTube."""
    fake_orch = SimpleNamespace(
        mode='hybrid',
        hybrid_order=['soulseek', 'youtube'],
    )
    monkeypatch.setattr(dm, 'download_orchestrator', fake_orch)

    now = time.time()
    task = _task(stuck_retry_count=3, downloading_start_time=now - 200)
    ops = _run(mon, task, _live('InProgress', progress=0), now)

    assert task['status'] == 'searching'
    assert 'soulseek' in task.get('exhausted_download_sources', set())
    assert ('restart_worker', 't1', 'b1') in ops


def test_not_in_live_transfers_triggers_hybrid_fallback_to_next_source(mon, monkeypatch):
    """When a transfer disappears 3 times, hybrid mode falls back to YouTube."""
    fake_orch = SimpleNamespace(
        mode='hybrid',
        hybrid_order=['soulseek', 'youtube'],
    )
    monkeypatch.setattr(dm, 'download_orchestrator', fake_orch)

    now = time.time()
    task = _task(stuck_retry_count=3, status_change_time=now - 200)
    ops = _run(mon, task, {}, now)

    assert task['status'] == 'searching'
    assert 'soulseek' in task.get('exhausted_download_sources', set())
    assert ('restart_worker', 't1', 'b1') in ops


def test_hybrid_fallback_gives_up_when_all_sources_exhausted(mon, monkeypatch):
    """When all hybrid sources have been exhausted, task marks failed as before."""
    fake_orch = SimpleNamespace(
        mode='hybrid',
        hybrid_order=['soulseek', 'youtube'],
    )
    monkeypatch.setattr(dm, 'download_orchestrator', fake_orch)

    now = time.time()
    # youtube is already exhausted, soulseek is now exhausting
    task = _task(
        error_retry_count=3,
        last_error_retry_time=now - 100,
        exhausted_download_sources={'youtube'},
    )
    ops = _run(mon, task, _live('Completed, Errored'), now)

    assert task['status'] == 'failed'
    assert not any(op[0] == 'restart_worker' for op in ops)
