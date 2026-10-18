"""Background bulk fix — "Fix All" at library scale (pertti's 5000-finding retag).

The synchronous ``bulk_fix_findings`` runs its whole loop inside the caller's
thread; from an HTTP request that means the browser times out at thousands of
findings while the server quietly keeps fixing — the user is told it failed
while it's actually still working. ``start_bulk_fix`` runs the same loop on a
worker thread; these tests pin the lifecycle: start → progress → completion,
single-flight, stop, and the empty/no-match refusal.

All DB-only (genre_cleanup findings fix via DB updates) — no files, no
network, no live services.
"""

from __future__ import annotations

import json
import threading
import time

from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


def _worker(db, tmp_path):
    w = RepairWorker(database=db)
    w._config_manager = None
    w.transfer_folder = str(tmp_path)
    return w


def _add_genre_finding(db, artist_id, n):
    """A pending genre_cleanup finding whose fix is a pure DB update."""
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO artists (id, name, genres, server_source) VALUES (?, ?, ?, 'test')",
            (artist_id, f'Artist {n}', json.dumps(['Rock', 'junk'])))
        conn.execute(
            "INSERT INTO repair_findings (job_id, finding_type, severity, status, "
            "entity_type, entity_id, title, details_json) VALUES "
            "('genre_cleanup', 'genre_cleanup', 'info', 'pending', 'artist', ?, ?, ?)",
            (artist_id, f'Off-whitelist genres: Artist {n}',
             json.dumps({'kept_genres': ['Rock'], 'removed_genres': ['junk']})))
        conn.commit()


def _wait_done(worker, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not worker.get_bulk_fix_status().get('running'):
            return worker.get_bulk_fix_status()
        time.sleep(0.02)
    raise AssertionError('background bulk fix did not finish in time')


def test_background_fix_processes_all_findings(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    for i in range(5):
        _add_genre_finding(db, f'AR{i}', i)
    w = _worker(db, tmp_path)

    result = w.start_bulk_fix(job_id='genre_cleanup')
    assert result['started'] is True and result['total'] == 5

    status = _wait_done(w)
    assert status['fixed'] == 5 and status['failed'] == 0
    assert status['done'] == 5 and status['total'] == 5
    assert status['stopped'] is False

    with db._get_connection() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM repair_findings WHERE status = 'pending'").fetchone()[0]
    assert remaining == 0


def test_start_returns_immediately_and_only_one_runs(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    for i in range(3):
        _add_genre_finding(db, f'AR{i}', i)
    w = _worker(db, tmp_path)

    # Slow the fixes down so the run is observably in-flight
    gate = threading.Event()
    original = w.fix_finding

    def slow_fix(fid, fix_action=None):
        gate.wait(timeout=5)
        return original(fid, fix_action=fix_action)

    w.fix_finding = slow_fix
    t0 = time.monotonic()
    assert w.start_bulk_fix()['started'] is True
    assert time.monotonic() - t0 < 1.0          # start is non-blocking

    second = w.start_bulk_fix()                  # single-flight
    assert second['started'] is False and second.get('already_running') is True

    gate.set()
    status = _wait_done(w)
    assert status['fixed'] == 3

    # After completion a new run may start again (nothing left → no-match)
    third = w.start_bulk_fix()
    assert third['started'] is False and 'already_running' not in third


def test_stop_halts_mid_run(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    for i in range(10):
        _add_genre_finding(db, f'AR{i}', i)
    w = _worker(db, tmp_path)

    fixed_before_stop = threading.Event()
    original = w.fix_finding

    def stopping_fix(fid, fix_action=None):
        result = original(fid, fix_action=fix_action)
        if not fixed_before_stop.is_set():
            fixed_before_stop.set()
            w.stop_bulk_fix()                   # ask to stop after the first fix
        return result

    w.fix_finding = stopping_fix
    assert w.start_bulk_fix()['started'] is True

    status = _wait_done(w)
    assert status['stopped'] is True
    assert 0 < status['done'] < 10              # halted early, not exhausted


def test_no_matching_findings_refuses_to_start(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    w = _worker(db, tmp_path)
    result = w.start_bulk_fix()
    assert result['started'] is False
    assert w.get_bulk_fix_status().get('running') is not True


def test_failed_fixes_are_counted_not_fatal(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    for i in range(3):
        _add_genre_finding(db, f'AR{i}', i)
    # Sabotage one finding: entity the fix can't find
    with db._get_connection() as conn:
        conn.execute("DELETE FROM artists WHERE id = 'AR1'")
        conn.commit()
    w = _worker(db, tmp_path)

    assert w.start_bulk_fix()['started'] is True
    status = _wait_done(w)
    assert status['done'] == 3
    assert status['fixed'] == 2 and status['failed'] == 1
    assert status['errors'] and status['errors'][0]['id']


def test_sync_bulk_fix_unchanged_by_refactor(tmp_path):
    """The id-query extraction must not change the synchronous path."""
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _add_genre_finding(db, 'AR0', 0)
    result = _worker(db, tmp_path).bulk_fix_findings(job_id='genre_cleanup')
    assert result['fixed'] == 1 and result['total'] == 1


def test_a_finished_run_never_reports_already_running(tmp_path):
    """Waiting for a run to finish must be enough to start the next one.

    The single-flight guard reads `_bulk_fix_thread.is_alive()`; callers wait on
    `state['running']`. Those are two signals for one condition, and the flag
    used to flip while the thread was still winding down — so a caller that had
    correctly waited was told "a bulk fix is already running". It passed locally
    and failed on CI, which is exactly the profile of a timing window.

    Asserted as an invariant rather than by racing it: anyone who observes
    running=False must also observe a released thread reference.
    """
    db = MusicDatabase(str(tmp_path / 'm.db'))
    for i in range(3):
        _add_genre_finding(db, f'AR{i}', i)
    w = _worker(db, tmp_path)

    assert w.start_bulk_fix()['started'] is True
    _wait_done(w)

    assert w._bulk_fix_thread is None, (
        "the run reported finished while still holding its thread — the next "
        "start will be refused with already_running")

    again = w.start_bulk_fix()
    assert 'already_running' not in again, again


def test_large_review_batch_waits_for_its_followup_scan(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    for index in range(4):
        _add_genre_finding(db, f'AR{index}', index)
    worker = _worker(db, tmp_path)
    worker.run_job_now = lambda job: job == 'library_reconciliation'
    cases = [(f'album:1:{index}', f'/music/{index}') for index in range(4)]
    started = worker.start_bulk_fix(review_case_folders=cases)
    assert started['operation_id']
    status = _wait_done(worker)
    assert status['reconciliation_queued'] and status['fixed'] == 4
    assert status['review_case_ids'] == [key for key, _ in cases]
    assert all(worker._album_refreshes[key]['state'] == 'running' for key, _ in cases)


def test_completed_operation_survives_restart_and_new_operation(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _add_genre_finding(db, 'first', 1)
    worker = _worker(db, tmp_path)
    first = worker.start_bulk_fix(job_id='genre_cleanup')['operation_id']
    _wait_done(worker)
    _add_genre_finding(db, 'second', 2)
    second = worker.start_bulk_fix(job_id='genre_cleanup')['operation_id']
    _wait_done(worker)
    restarted = _worker(db, tmp_path)
    for operation_id in [first, second]:
        report = restarted.get_bulk_fix_status(operation_id)
        assert report['operation_id'] == operation_id
        assert report['fixed'] == 1
        assert not report['running']
    assert restarted.get_bulk_fix_status('unknown')['report_unavailable']


def test_interrupted_operation_is_not_reported_as_success(tmp_path):
    from core.repair_operation_reports import save_report
    db = MusicDatabase(str(tmp_path / 'm.db'))
    save_report(db, {'operation_id': 'interrupted', 'running': True, 'done': 1, 'fixed': 1})
    report = _worker(db, tmp_path).get_bulk_fix_status('interrupted')
    assert report['stopped'] and not report['running']
    assert 'restarted' in report['error']
    assert report['fixed'] == 1


def test_review_apply_waits_for_scan_and_rejects_changed_proposal(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _add_genre_finding(db, 'AR1', 1)
    worker = _worker(db, tmp_path)
    worker._reconciliation_scan_lock.acquire()
    try:
        result = worker.start_bulk_fix(review_approved=True)
        assert result['started']
        deadline = time.monotonic() + 2
        while worker.get_bulk_fix_status()['phase'] != 'waiting_for_scan':
            assert time.monotonic() < deadline
            time.sleep(0.01)
        with db._get_connection() as conn:
            conn.execute("UPDATE repair_findings SET details_json = '{}' WHERE status = 'pending'")
            conn.commit()
    finally:
        worker._reconciliation_scan_lock.release()
    status = _wait_done(worker)
    assert status['fixed'] == 0 and status['failed'] == 1
    assert 'changed during reconciliation' in status['errors'][0]['error']


def test_saved_file_decision_is_included_in_ready_album_batch(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    worker = _worker(db, tmp_path)
    worker._create_finding(job_id='library_reconciliation',
        finding_type='reconciliation_extra', severity='warning', entity_type='file',
        entity_id='extra', file_path=str(tmp_path / 'extra.flac'), title='Extra',
        description='Choose a file action', details={'album_folder': str(tmp_path),
            'album_id': 'album', 'album_title': 'Album'})
    case_id = worker.get_review_cases()['items'][0]['case_id']
    item = worker.get_review_case(case_id)['items'][0]
    calls = []
    worker._execute_fix = lambda kind, entity, key, path, details: calls.append(
        (kind, details.get('_fix_action'))) or {'success': True, 'action': 'quarantined'}
    worker._schedule_album_refresh = lambda *args, **kwargs: True
    assert worker.apply_review_decision(case_id, item['id'],
        evidence_token=item['evidence_token'], action='fix', fix_action='quarantine')['success']
    assert not calls
    assert not worker.start_bulk_fix(job_id='library_reconciliation')['started']
    assert worker.start_review_safe()['started']
    result = _wait_done(worker)
    assert result['fixed'] == 1 and result['failed'] == 0
    assert calls == [('reconciliation_extra', 'quarantine')]
    assert result['review_scope'] == 'global'


def test_changed_ready_proposal_blocks_entire_album_after_scan_wait(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    worker = _worker(db, tmp_path)
    for index in (1, 2):
        worker._create_finding(job_id='library_reconciliation', finding_type='reconciliation_metadata',
            severity='warning', entity_type='track', entity_id=str(index),
            file_path=str(tmp_path / f'{index}.flac'), title=f'Track {index}', description='Metadata',
            details={'album_id': 'album', 'album_folder': str(tmp_path), 'confidence': 'clear',
                     'changes': {'title': {'old': 'song', 'new': 'Song'}}})
    calls = []
    worker.fix_finding = lambda *a, **kw: calls.append(a) or {'success': True}
    worker._schedule_album_refresh = lambda *a, **kw: True
    worker._reconciliation_scan_lock.acquire()
    try:
        assert worker.start_review_safe()['started']
        with db._get_connection() as conn:
            conn.execute("UPDATE repair_findings SET details_json=json_set(details_json, '$.changes.title.new', 'Different proposal') WHERE entity_id='1'")
            conn.commit()
    finally:
        worker._reconciliation_scan_lock.release()
    result = _wait_done(worker)
    assert calls == []
    assert result['failed'] == 2 and result['fixed'] == 0


def test_confirmed_batch_snapshot_survives_startup_race(tmp_path):
    from core.repair_review_queue import ready_batch_preview
    db = MusicDatabase(str(tmp_path / 'm.db'))
    worker = _worker(db, tmp_path)
    for index in (1, 2):
        worker._create_finding(job_id='library_reconciliation', finding_type='reconciliation_metadata',
            severity='warning', entity_type='track', entity_id=str(index),
            file_path=str(tmp_path / f'{index}.flac'), title=f'Track {index}', description='Metadata',
            details={'album_id': 'album', 'album_folder': str(tmp_path), 'confidence': 'clear',
                     'changes': {'title': {'old': 'song', 'new': 'Song'}}})
    preview = ready_batch_preview(db)
    assert worker.start_review_safe(expected_signature='outdated')['stale']
    original = worker.get_review_case
    def changed_case(*args, **kwargs):
        case = original(*args, **kwargs)
        with db._get_connection() as conn:
            conn.execute("UPDATE repair_findings SET details_json=json_set(details_json, '$.changes.title.new', 'Unconfirmed proposal') WHERE entity_id='1'")
            conn.commit()
        return case
    worker.get_review_case = changed_case
    calls = []
    worker.fix_finding = lambda *a, **kw: calls.append(a) or {'success': True}
    worker._schedule_album_refresh = lambda *a, **kw: True
    assert worker.start_review_safe(expected_signature=preview['signature'])['started']
    result = _wait_done(worker)
    assert calls == []
    assert result['failed'] == 2 and result['fixed'] == 0
