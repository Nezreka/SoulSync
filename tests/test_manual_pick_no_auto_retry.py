"""Pin the ``_user_manual_pick`` flag — auto-retry monitor must not
yank a manually-picked download back to 'searching' when it fails.

User report: "I manually searched and selected a candidate. It went
to 0% downloading, then suddenly switched back to searching." The
download monitor's ``_should_retry_task`` was treating the failed
manual pick the same as a normal auto-attempt failure — fresh search,
new query, new candidates. From the user's perspective: "I picked
THIS one, why is it searching for something else now?"

Fix: when the user explicitly selects a candidate via
``/api/downloads/task/<id>/download-candidate``, the task is flagged
``_user_manual_pick=True``. The monitor's retry decision checks that
flag and bails — letting the failure surface to the user instead of
auto-falling-back.

These tests exercise ``_should_retry_task`` directly with the flag
set + various engine/transfer states.
"""

from __future__ import annotations

import time

import pytest

from core.downloads import monitor as dm


@pytest.fixture
def fake_monitor(monkeypatch):
    """Monitor with patched make_context_key so tests don't pull in the
    real one."""
    monkeypatch.setattr(dm, '_make_context_key', lambda u, f: f"{u}::{f}")
    return dm.WebUIDownloadMonitor()


def _task(*, username='youtube', filename='vid.mp3', status='downloading',
          download_id='dl-1', manual_pick=False, status_change_time=None):
    return {
        'track_info': {'name': 'Test Track'},
        'username': username,
        'filename': filename,
        'status': status,
        'download_id': download_id,
        '_user_manual_pick': manual_pick,
        'status_change_time': status_change_time or (time.time() - 1000),
    }


def test_manual_pick_skips_retry_when_not_in_live_transfers(fake_monitor):
    """The not-in-live-transfers stuck-task path normally triggers
    retry after 90s. Manual picks must bypass it — no retry submitted,
    no status reset to 'searching'."""
    task = _task(manual_pick=True)
    deferred_ops = []

    result = fake_monitor._should_retry_task(
        task_id='t1',
        task=task,
        live_transfers_lookup={},
        current_time=time.time(),
        deferred_ops=deferred_ops,
    )

    assert result is False
    assert deferred_ops == []
    assert task['status'] == 'downloading'


def test_manual_pick_skips_retry_on_errored_state(fake_monitor):
    """When the task IS in live_transfers but the engine reports
    Errored, the monitor would normally retry. Manual picks bypass
    that path too."""
    task = _task(manual_pick=True)
    deferred_ops = []
    live_lookup = {
        'youtube::vid.mp3': {
            'state': 'Completed, Errored',
            'percentComplete': 0,
        }
    }

    fake_monitor._should_retry_task(
        task_id='t1',
        task=task,
        live_transfers_lookup=live_lookup,
        current_time=time.time(),
        deferred_ops=deferred_ops,
    )

    assert deferred_ops == []
    assert task['status'] == 'downloading'


def test_monitor_waits_for_post_processing_before_batch_success(monkeypatch):
    """Engine completion is not the same as a successful import.

    The monitor should start post-processing when slskd reports a completed
    transfer, but the post-processing worker must be the only code path that
    reports final success/failure to the batch lifecycle.
    """
    monkeypatch.setattr(dm, '_make_context_key', lambda u, f: f"{u}::{f}")
    monkeypatch.setattr(dm.WebUIDownloadMonitor, '_validate_worker_counts', lambda self: None)

    submitted = []
    completions = []

    class FakeExecutor:
        def submit(self, func, task_id, batch_id):
            submitted.append((func, task_id, batch_id))

    def fake_post_processing_worker(task_id, batch_id):
        return None

    monkeypatch.setattr(dm, 'missing_download_executor', FakeExecutor())
    monkeypatch.setattr(dm, '_run_post_processing_worker', fake_post_processing_worker)
    monkeypatch.setattr(
        dm,
        '_on_download_completed',
        lambda batch_id, task_id, success: completions.append((batch_id, task_id, success)),
    )

    with dm.tasks_lock:
        previous_tasks = dict(dm.download_tasks)
        previous_batches = dict(dm.download_batches)
        dm.download_tasks.clear()
        dm.download_batches.clear()
        dm.download_tasks['task-1'] = {
            'track_info': {'name': 'Test Track'},
            'username': 'Pinasound',
            'filename': r'@@tmllb\Music\Album\01. Track.flac',
            'status': 'downloading',
            'download_id': 'download-1',
            'status_change_time': time.time(),
        }
        dm.download_batches['batch-1'] = {'queue': ['task-1']}

    try:
        monitor = dm.WebUIDownloadMonitor()
        monitor.monitoring = True
        monitor.monitored_batches.add('batch-1')
        monkeypatch.setattr(
            monitor,
            '_get_live_transfers',
            lambda: {
                r'Pinasound::@@tmllb\Music\Album\01. Track.flac': {
                    'state': 'Completed, Succeeded',
                    'size': 100,
                    'bytesTransferred': 100,
                }
            },
        )

        monitor._check_all_downloads()

        assert submitted == [(fake_post_processing_worker, 'task-1', 'batch-1')]
        assert completions == []
        assert dm.download_tasks['task-1']['status'] == 'post_processing'
    finally:
        with dm.tasks_lock:
            dm.download_tasks.clear()
            dm.download_tasks.update(previous_tasks)
            dm.download_batches.clear()
            dm.download_batches.update(previous_batches)


def test_monitor_matches_release_download_by_id_when_filename_changes(monkeypatch):
    """Torrent/usenet rows can expose the completed audio filename, not
    the original indexer URL/title stored on the task. The monitor must
    still claim the completed release by stable download_id.
    """
    monkeypatch.setattr(dm, '_make_context_key', lambda u, f: f"{u}::{f}")
    monkeypatch.setattr(dm.WebUIDownloadMonitor, '_validate_worker_counts', lambda self: None)

    submitted = []

    class FakeExecutor:
        def submit(self, func, task_id, batch_id):
            submitted.append((func, task_id, batch_id))

    def fake_post_processing_worker(task_id, batch_id):
        return None

    monkeypatch.setattr(dm, 'missing_download_executor', FakeExecutor())
    monkeypatch.setattr(dm, '_run_post_processing_worker', fake_post_processing_worker)
    monkeypatch.setattr(dm, '_on_download_completed', lambda *args: None)

    with dm.tasks_lock:
        previous_tasks = dict(dm.download_tasks)
        previous_batches = dict(dm.download_batches)
        dm.download_tasks.clear()
        dm.download_batches.clear()
        dm.download_tasks['task-1'] = {
            'track_info': {'name': 'Ran To Atlanta'},
            'username': 'torrent',
            'filename': 'http://prowlarr/download?id=123||Drake - ICEMAN',
            'status': 'downloading',
            'download_id': 'torrent-1',
            'status_change_time': time.time(),
        }
        dm.download_batches['batch-1'] = {'queue': ['task-1']}

    try:
        monitor = dm.WebUIDownloadMonitor()
        monitor.monitoring = True
        monitor.monitored_batches.add('batch-1')
        monkeypatch.setattr(
            monitor,
            '_get_live_transfers',
            lambda: {
                'download_id::torrent-1': {
                    'id': 'torrent-1',
                    'username': 'torrent',
                    'filename': '01. Drake - Make Them Cry.flac',
                    'state': 'Completed, Succeeded',
                    'size': 100,
                    'bytesTransferred': 100,
                }
            },
        )

        monitor._check_all_downloads()

        assert submitted == [(fake_post_processing_worker, 'task-1', 'batch-1')]
        assert dm.download_tasks['task-1']['status'] == 'post_processing'
    finally:
        with dm.tasks_lock:
            dm.download_tasks.clear()
            dm.download_tasks.update(previous_tasks)
            dm.download_batches.clear()
            dm.download_batches.update(previous_batches)


def test_monitor_recovers_premature_failed_release_download(monkeypatch):
    monkeypatch.setattr(dm, '_make_context_key', lambda u, f: f"{u}::{f}")
    monkeypatch.setattr(dm.WebUIDownloadMonitor, '_validate_worker_counts', lambda self: None)

    submitted = []

    class FakeExecutor:
        def submit(self, func, task_id, batch_id):
            submitted.append((func, task_id, batch_id))

    def fake_post_processing_worker(task_id, batch_id):
        return None

    monkeypatch.setattr(dm, 'missing_download_executor', FakeExecutor())
    monkeypatch.setattr(dm, '_run_post_processing_worker', fake_post_processing_worker)
    monkeypatch.setattr(dm, '_on_download_completed', lambda *args: None)

    with dm.tasks_lock:
        previous_tasks = dict(dm.download_tasks)
        previous_batches = dict(dm.download_batches)
        dm.download_tasks.clear()
        dm.download_batches.clear()
        dm.download_tasks['task-1'] = {
            'track_info': {'name': 'DAISIES'},
            'username': 'torrent',
            'filename': 'http://prowlarr/download?id=123||Justin Bieber - Swag',
            'status': 'failed',
            'download_id': 'torrent-1',
            'status_change_time': time.time(),
        }
        dm.download_batches['batch-1'] = {'queue': ['task-1']}

    try:
        monitor = dm.WebUIDownloadMonitor()
        monitor.monitoring = True
        monitor.monitored_batches.add('batch-1')
        monkeypatch.setattr(
            monitor,
            '_get_live_transfers',
            lambda: {
                'download_id::torrent-1': {
                    'id': 'torrent-1',
                    'username': 'torrent',
                    'filename': '02. Justin Bieber - DAISIES.flac',
                    'state': 'Completed, Succeeded',
                    'size': 100,
                    'bytesTransferred': 100,
                }
            },
        )

        monitor._check_all_downloads()

        assert submitted == [(fake_post_processing_worker, 'task-1', 'batch-1')]
        assert dm.download_tasks['task-1']['status'] == 'post_processing'
    finally:
        with dm.tasks_lock:
            dm.download_tasks.clear()
            dm.download_tasks.update(previous_tasks)
            dm.download_batches.clear()
            dm.download_batches.update(previous_batches)

