"""Saved completion automations use the ordinary native maintenance job."""
from types import SimpleNamespace

import pytest

from core.automation.handlers.complete_incomplete_albums import auto_complete_incomplete_albums


def test_action_queues_native_job_with_bounded_target_and_library():
    calls = []
    deps = SimpleNamespace(run_repair_job_now=lambda *a, **kw: calls.append((a, kw)) or True)
    result = auto_complete_incomplete_albums({'max_albums': 20, 'target_album_id': 'lib2:42'}, deps)
    assert result['status'] == 'completed'
    assert calls[0][0] == ('album_completeness',)
    assert calls[0][1]['respect_enabled'] is True
    assert calls[0][1]['scope']['max_albums'] == 10
    assert calls[0][1]['scope']['target_album_id'] == 'lib2:42'
    assert 'library' in calls[0][1]['scope']


@pytest.mark.parametrize('event', [
    {'job_id': 'album_completeness', 'run_status': 'completed', 'errors': 0},
    {'job_id': 'album_completeness', 'run_status': 'failed', 'errors': 1},
    {'job_id': 'other', 'run_status': 'completed', 'errors': 0},
])
def test_scan_event_never_queues_a_recursive_or_failed_completion(event):
    deps = SimpleNamespace(run_repair_job_now=lambda *a, **kw: pytest.fail('recursive queue'))
    result = auto_complete_incomplete_albums({'_event_data': event}, deps)
    assert result['status'] == 'skipped'


def test_disabled_job_is_not_reenabled():
    result = auto_complete_incomplete_albums({}, SimpleNamespace(run_repair_job_now=lambda *a, **kw: False))
    assert result['status'] == 'skipped'


def test_unavailable_worker_is_skipped():
    assert auto_complete_incomplete_albums({}, SimpleNamespace())['status'] == 'skipped'
