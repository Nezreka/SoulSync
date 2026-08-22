"""Giving up on a task must also cancel it in slskd (sassmastawillis).

    "When a download exhausts its configured retry attempts it remains
    sitting in the queue indefinitely. removing all queued manually resumes
    downloads and frees up new slots."

Same shape as 611b64f2b, one layer down. Every RETRY path in
``_should_retry_task`` cancels the transfer before searching again:

    deferred_ops.append(('cancel_download', download_id, username, '...'))

...but every GIVE-UP path (the branch taken once retries hit 3) only set
``task['status'] = 'failed'`` and returned. SoulSync stopped tracking the
task while the enqueue lived on in slskd forever — holding one of the
user's download slots, which is why clearing the queue by hand "resumes
downloads and frees up new slots".

Four branches gave up without cancelling: errored-state, queued-timeout
(the reported one), 0%-progress, and unknown-state. The fifth path
(not-in-live-transfers) has nothing to cancel — the transfer is already
gone from slskd — and is pinned here so it stays that way.
"""

from __future__ import annotations

import time

import pytest

from core.downloads import monitor as dm


@pytest.fixture
def mon(monkeypatch):
    # _orphaned_download_keys is a module global bound by the monitor's init()
    # at startup; the retry paths write to it, so a test that exercises them
    # has to stand it up the same way production does.
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


def _cancels(deferred_ops):
    return [op for op in deferred_ops if op[0] == 'cancel_download']


def _run(mon, task, live, now=None):
    ops = []
    now = now or time.time()
    mon._should_retry_task(
        task_id='t1', task=task, live_transfers_lookup=live,
        current_time=now, deferred_ops=ops,
    )
    return ops


# ── the reported case: queued forever after the retries are spent ──────────

def test_queued_giveup_cancels_the_transfer(mon):
    """His exact payload: state 'Queued, Locally', slskd's own attempts
    exhausted, zero bytes. SoulSync burns its 3 retries, gives up — and used
    to leave the enqueue alive in slskd for hours."""
    now = time.time()
    task = _task(stuck_retry_count=3, queued_start_time=now - 200)
    ops = _run(mon, task, _live('Queued, Locally'), now)

    assert task['status'] == 'failed', 'precondition: this is the give-up branch'
    assert _cancels(ops), 'gave up on the task but never told slskd'
    assert _cancels(ops)[0][1] == 'dl-1'
    assert _cancels(ops)[0][2] == 'jedzs'


def test_queued_remotely_giveup_cancels_too(mon):
    """The originally-reported wording was 'Queued, Remotely' — same branch,
    since the check is a substring match on 'Queued'."""
    now = time.time()
    task = _task(stuck_retry_count=3, queued_start_time=now - 200)
    ops = _run(mon, task, _live('Queued, Remotely'), now)
    assert task['status'] == 'failed'
    assert _cancels(ops)


# ── the other three give-up branches leaked the same way ───────────────────

def test_errored_giveup_cancels_the_transfer(mon):
    now = time.time()
    task = _task(error_retry_count=3, last_error_retry_time=now - 100)
    ops = _run(mon, task, _live('Completed, Errored'), now)
    assert task['status'] == 'failed'
    assert _cancels(ops)


def test_zero_progress_giveup_cancels_the_transfer(mon):
    now = time.time()
    task = _task(stuck_retry_count=3, downloading_start_time=now - 200)
    ops = _run(mon, task, _live('InProgress', progress=0), now)
    assert task['status'] == 'failed'
    assert _cancels(ops)


def test_unknown_state_giveup_cancels_the_transfer(mon):
    now = time.time()
    task = _task(stuck_retry_count=3, downloading_start_time=now - 200)
    ops = _run(mon, task, _live('Requested'), now)
    assert task['status'] == 'failed'
    assert _cancels(ops)


# ── guards: don't cancel what isn't there, don't cancel what's alive ───────

def test_not_in_live_transfers_giveup_has_nothing_to_cancel(mon):
    """The transfer already vanished from slskd — there is no id to act on,
    and inventing a cancel here would just log noise."""
    now = time.time()
    task = _task(stuck_retry_count=3, status_change_time=now - 200)
    ops = _run(mon, task, {}, now)
    assert task['status'] == 'failed'
    assert not _cancels(ops)


def test_healthy_transfer_is_never_cancelled(mon):
    """A transfer making real progress must not be touched by any of this."""
    now = time.time()
    task = _task(stuck_retry_count=3, downloading_start_time=now - 200)
    ops = _run(mon, task, _live('InProgress', progress=42, transferred=99), now)
    assert task['status'] != 'failed'
    assert not _cancels(ops)


def test_retry_path_still_cancels_once(mon):
    """The retry branches already cancelled; the fix must not double up."""
    now = time.time()
    task = _task(stuck_retry_count=0, queued_start_time=now - 200, last_retry_time=0)
    ops = _run(mon, task, _live('Queued, Remotely'), now)
    assert task['status'] == 'searching', 'precondition: this is the retry branch'
    assert len(_cancels(ops)) == 1


def test_giveup_without_a_download_id_does_not_crash(mon):
    """Nothing to cancel and no id to cancel it with — must still fail the
    task cleanly rather than raising inside the monitor loop."""
    now = time.time()
    task = _task(download_id=None, stuck_retry_count=3, queued_start_time=now - 200)
    ops = _run(mon, task, _live('Queued, Locally'), now)
    assert task['status'] == 'failed'
    assert not _cancels(ops)
