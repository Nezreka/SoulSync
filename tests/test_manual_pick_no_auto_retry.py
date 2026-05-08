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


