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


def _add_genre_finding(db, _artist_id, n):
    """A pending genre_cleanup finding whose fix is a pure DB update."""
    native_id = n + 1
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO lib2_artists (id, name, genres) VALUES (?, ?, ?)",
            (native_id, f'Artist {n}', json.dumps(['Rock', 'junk'])))
        conn.execute(
            "INSERT INTO repair_findings (job_id, finding_type, severity, status, "
            "entity_type, entity_id, title, details_json) VALUES "
            "('genre_cleanup', 'genre_cleanup', 'info', 'pending', 'artist', ?, ?, ?)",
            (f'lib2:{native_id}', f'Off-whitelist genres: Artist {n}',
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
        conn.execute("DELETE FROM lib2_artists WHERE id=2")
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
