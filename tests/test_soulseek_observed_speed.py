"""Observed-speed fallback for automatic Soulseek downloads."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.downloads import monitor as dm
from core.downloads import task_worker as tw
from core.downloads.observed_speed import ObservedSpeedTracker


def test_tracker_requires_a_full_window_and_sustained_low_period():
    tracker = ObservedSpeedTracker()

    assert tracker.observe(0, 0, 500_000) == (None, False)
    average, abandon = tracker.observe(30, 3_000_000, 500_000)
    assert average == pytest.approx(100_000)
    assert abandon is False
    assert tracker.observe(59, 5_900_000, 500_000)[1] is False
    assert tracker.observe(60, 6_000_000, 500_000)[1] is True


def test_tracker_resets_low_period_when_the_average_recovers():
    tracker = ObservedSpeedTracker()

    tracker.observe(0, 0, 500_000)
    tracker.observe(30, 3_000_000, 500_000)
    average, abandon = tracker.observe(45, 30_000_000, 500_000)
    assert average > 500_000
    assert abandon is False
    assert tracker.low_since is None


@pytest.fixture
def monitor(monkeypatch):
    monkeypatch.setattr(dm, '_make_context_key', lambda u, f: f"{u}::{f}")
    monkeypatch.setattr(dm, '_orphaned_download_keys', set())
    monkeypatch.setattr(
        dm.config_manager,
        'get',
        lambda key, default=None: (
            500 if key == 'soulseek.min_observed_download_speed_kbps' else
            True if key == 'soulseek.observed_speed_fallback_enabled' else default
        ),
    )
    return dm.WebUIDownloadMonitor()


def _task(**overrides):
    task = {
        'track_info': {'name': 'Slow Song'},
        'username': 'slow-peer',
        'filename': r'Artist\Album\01 - Slow Song.flac',
        'download_id': 'dl-1',
        'status': 'downloading',
        'batch_id': 'batch-1',
        'candidate_count': 2,
        'current_candidate_index': 0,
        'used_sources': set(),
    }
    task.update(overrides)
    return task


def _observe(monitor, task, now, transferred, *, state='InProgress'):
    ops = []
    handled = monitor._retry_slow_soulseek_transfer(
        'task-1', task,
        {'state': state, 'bytesTransferred': transferred},
        state, now, ops,
    )
    return handled, ops


def test_sustained_slow_transfer_reuses_existing_retry_path(monitor):
    task = _task()

    assert _observe(monitor, task, 0, 0)[0] is False
    assert _observe(monitor, task, 30, 3_000_000)[0] is False
    handled, ops = _observe(monitor, task, 60, 6_000_000)

    assert handled is True
    assert task['status'] == 'searching'
    assert task['_slow_fallback_source_key'].startswith('slow-peer_')
    assert task['retry_trigger'] == 'observed_speed'
    assert task.get('download_id') is None
    assert [op[0] for op in ops] == ['replace_slow_download']


@pytest.mark.parametrize('cancelled', [False, True])
def test_slow_replacement_waits_for_confirmed_cancellation(monitor, monkeypatch, cancelled):
    task = _task()
    monkeypatch.setattr(dm, 'download_tasks', {'task-1': task})
    monkeypatch.setattr(dm, 'matched_downloads_context', {
        'slow-peer::' + task['filename']: {'title': 'Slow Song'},
    })
    cancel = AsyncMock(return_value=cancelled)
    submit = Mock()
    monkeypatch.setattr(dm, 'run_async', asyncio.run)
    monkeypatch.setattr(dm, 'download_orchestrator', SimpleNamespace(
        cancel_download=cancel, get_all_downloads=AsyncMock(return_value=[]),
    ))
    monkeypatch.setattr(dm, 'missing_download_executor', SimpleNamespace(submit=submit))
    for now in (0, 30):
        _observe(monitor, task, now, now * 100_000)
    _, ops = _observe(monitor, task, 60, 6_000_000)

    dm._replace_slow_download(*ops[0][1:])

    cancel.assert_awaited_once_with('dl-1', 'slow-peer', remove=True)
    if cancelled:
        submit.assert_called_once()
        assert task['status'] == 'searching'
        assert not dm.matched_downloads_context
    else:
        submit.assert_not_called()
        assert task['status'] == 'downloading'
        assert task['download_id'] == 'dl-1'
        assert task['_observed_speed_exempt'] is True
        assert dm.matched_downloads_context


def test_accepted_cancel_without_terminal_transfer_does_not_restart(monitor, monkeypatch):
    task = _task()
    monkeypatch.setattr(dm, 'download_tasks', {'task-1': task})
    monkeypatch.setattr(dm, 'run_async', asyncio.run)
    monkeypatch.setattr(dm.time, 'sleep', lambda seconds: None)
    submit = Mock()
    monkeypatch.setattr(dm, 'missing_download_executor', SimpleNamespace(submit=submit))
    still_running = SimpleNamespace(id='dl-1', username='slow-peer', state='InProgress')
    monkeypatch.setattr(dm, 'download_orchestrator', SimpleNamespace(
        cancel_download=AsyncMock(return_value=True),
        get_all_downloads=AsyncMock(return_value=[still_running]),
    ))
    for now in (0, 30):
        _observe(monitor, task, now, now * 100_000)
    _, ops = _observe(monitor, task, 60, 6_000_000)

    dm._replace_slow_download(*ops[0][1:])

    submit.assert_not_called()
    assert task['status'] == 'downloading'
    assert task['_observed_speed_exempt'] is True


@pytest.mark.parametrize(
    ('candidate_count', 'candidate_index'),
    [(1, 0), (3, 2)],
)
def test_last_candidate_is_allowed_to_continue(monitor, candidate_count, candidate_index):
    task = _task(
        candidate_count=candidate_count,
        current_candidate_index=candidate_index,
    )

    _observe(monitor, task, 0, 0)
    _observe(monitor, task, 30, 3_000_000)
    handled, ops = _observe(monitor, task, 60, 6_000_000)

    assert handled is True
    assert task['status'] == 'downloading'
    assert task['_observed_speed_exempt'] is True
    assert ops == []


def test_zero_threshold_disables_observed_speed_retry(monitor, monkeypatch):
    monkeypatch.setattr(
        dm.config_manager,
        'get',
        lambda key, default=None: (
            0 if key == 'soulseek.min_observed_download_speed_kbps' else
            True if key == 'soulseek.observed_speed_fallback_enabled' else default
        ),
    )
    task = _task()
    task['_observed_speed_tracker'] = ObservedSpeedTracker()

    for now in (0, 30, 60, 90):
        handled, ops = _observe(monitor, task, now, now * 100_000)
        assert handled is False
        assert ops == []

    assert len(task['_observed_speed_tracker'].samples) >= 2


def test_fallback_disabled_by_default(monitor, monkeypatch):
    monkeypatch.setattr(
        dm.config_manager, 'get',
        lambda key, default=None: (
            250 if key == 'soulseek.min_observed_download_speed_kbps' else default
        ),
    )
    task = _task()

    for now in (0, 30, 60, 90):
        assert _observe(monitor, task, now, now * 100_000) == (False, [])
    assert len(task['_observed_speed_tracker'].samples) >= 2


def test_completed_transfer_keeps_samples_for_peer_observation(monitor, monkeypatch):
    """_should_retry_task runs before the completion branch; a healthy
    transfer's samples must survive it so the peer's speed gets recorded."""
    recorded = []
    monkeypatch.setattr(dm, 'observe_peer', lambda *args: recorded.append(args))
    monkeypatch.setattr(dm, '_resolve_download_source', lambda username: 'soulseek')
    task = _task()

    for now in (0, 20, 40):
        assert _observe(monitor, task, now, now * 2_000_000)[0] is False
    handled, ops = _observe(monitor, task, 41, 100_000_000, state='Completed, Succeeded')

    assert (handled, ops) == (False, [])
    tracker = task['_observed_speed_tracker']
    assert isinstance(tracker, ObservedSpeedTracker)
    speed_bps, sample_seconds = tracker.window_speed()
    assert speed_bps == pytest.approx(2_000_000)
    assert sample_seconds == 40

    # Any other inactive state drops the samples.
    _observe(monitor, task, 42, 0, state='Queued, Remotely')
    assert '_observed_speed_tracker' not in task


def test_manual_and_non_soulseek_downloads_are_untouched(monitor):
    manual = _task(_user_manual_pick=True)
    streaming = _task(username='tidal')

    for task in (manual, streaming):
        for now in (0, 30, 60):
            handled, ops = _observe(monitor, task, now, now * 100_000)
            assert handled is False
            assert ops == []
        assert task['status'] == 'downloading'


def test_cached_retry_keeps_slow_source_as_last_resort(monkeypatch):
    slow = SimpleNamespace(username='slow', filename='slow.flac')
    alternative = SimpleNamespace(username='fast', filename='fast.flac')
    captured = []

    class _Deps:
        @staticmethod
        def attempt_download_with_candidates(task_id, candidates, track, batch_id, **kwargs):
            captured.extend(candidates)
            return True

    with tw.tasks_lock:
        previous = dict(tw.download_tasks)
        tw.download_tasks.clear()
        tw.download_tasks['task-1'] = {
            'cached_candidates': [slow, alternative],
            'used_sources': {'slow_slow.flac'},
            '_slow_fallback_source_key': 'slow_slow.flac',
            'track_info': {},
        }
    try:
        assert tw._try_cached_candidates('task-1', 'batch-1', object(), _Deps()) is True
        assert captured == [alternative, slow]
    finally:
        with tw.tasks_lock:
            tw.download_tasks.clear()
            tw.download_tasks.update(previous)


def test_setting_is_wired_through_defaults_and_web_ui():
    root = Path(__file__).resolve().parents[1]
    settings_py = (root / 'core/settings.py').read_text()
    settings_js = (root / 'webui/static/settings.js').read_text()
    index_html = (root / 'webui/index.html').read_text()

    assert '"observed_speed_fallback_enabled": False' in settings_py
    assert '"min_observed_download_speed_kbps": 250' in settings_py
    assert 'settings.soulseek?.min_observed_download_speed_kbps ?? 250' in settings_js
    assert "min_observed_download_speed_kbps: _cfgInt('soulseek-min-observed-download-speed', 250)" in settings_js
    assert 'id="soulseek-observed-speed-fallback-enabled"' in index_html
    assert 'id="soulseek-min-observed-download-speed"' in index_html
