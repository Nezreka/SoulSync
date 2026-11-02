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


class _Db:
    def __init__(self, path):
        import sqlite3
        self.path = path
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE repair_findings (id INTEGER PRIMARY KEY, job_id TEXT, "
                     "finding_type TEXT, status TEXT, entity_id TEXT)")
        conn.execute("CREATE TABLE lib2_albums (id INTEGER PRIMARY KEY, legacy_album_id INTEGER)")
        for i, entity in enumerate(("lib2:5", "lib2:6", "lib2:7", "lib2:8"), start=1):
            conn.execute("INSERT INTO repair_findings VALUES (?, 'album_completeness', "
                         "'incomplete_album', 'pending', ?)", (i, entity))
        conn.execute("INSERT INTO repair_findings VALUES (9, 'album_completeness', "
                     "'incomplete_album', 'resolved', 'lib2:9')")
        conn.execute("INSERT INTO lib2_albums VALUES (7, 311)")
        conn.commit()
        conn.close()

    def _get_connection(self):
        import sqlite3
        return sqlite3.connect(self.path)


_DONE = {'job_id': 'album_completeness', 'run_status': 'completed', 'errors': 0}


def test_a_completed_scan_applies_its_pending_findings_like_361(tmp_path):
    """3.6.1 automations fire on Album Completeness finishing: the missing
    tracks of up to max_albums incomplete albums go on the wishlist. for a
    while this skipped every time, so they did nothing at all."""
    fixed = []
    db = _Db(str(tmp_path / 'm.db'))
    deps = SimpleNamespace(
        get_database=lambda: db,
        bulk_fix_repair_findings=lambda ids: fixed.append(ids) or {'fixed': len(ids)},
        run_repair_job_now=lambda *a, **kw: pytest.fail('never re-runs the job on its own event'))
    result = auto_complete_incomplete_albums({'_event_data': _DONE, 'max_albums': 2}, deps)
    assert result['status'] == 'completed' and result['applied'] == 2
    assert fixed == [[1, 2]]


def test_a_saved_361_album_id_still_targets_its_album(tmp_path):
    fixed = []
    db = _Db(str(tmp_path / 'm.db'))
    deps = SimpleNamespace(
        get_database=lambda: db,
        bulk_fix_repair_findings=lambda ids: fixed.append(ids) or {'fixed': len(ids)})
    result = auto_complete_incomplete_albums(
        {'_event_data': _DONE, 'target_album_id': '311'}, deps)
    assert result['applied'] == 1 and fixed == [[3]]  # legacy album 311 is lib2 album 7
