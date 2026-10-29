"""Commands, scan publication and recovery work share explicit boundaries."""
import sqlite3
import threading

import pytest

import core.reconciliation.projection as reconciliation_projection
from core.reconciliation import album_state, decisions, work
from core.reconciliation import search as search
from core.reconciliation.fulfillment import (
    outstanding,
)
from core.repair_jobs.library_reconciliation import LibraryReconciliationJob
from core.repair_worker import RepairWorker
from tests.reconciliation.support.acceptance import build_album, scan_case


def test_decision_during_scan_retains_previous_snapshot_and_queues_fresh_plan(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'numbering_metadata_conflict')
    case = scan_case(album)
    with album.db._get_connection() as conn:
        before = conn.execute('SELECT plan_json FROM reconcile_album_plans').fetchall()
    publish = reconciliation_projection._persist_selected_plan
    changed = []

    def decide_before_publication(*args, **kwargs):
        if not changed:
            changed.append(True)
            assert album.worker.apply_review_decision_group(case['case_id'], 'numbering', action='dismiss')['success']
        return publish(*args, **kwargs)

    monkeypatch.setattr('core.reconciliation.projection._persist_selected_plan', decide_before_publication)
    result = LibraryReconciliationJob().scan(album.context)
    assert 'decisions changed' in result.stopped_early
    with album.db._get_connection() as conn:
        assert conn.execute('SELECT plan_json FROM reconcile_album_plans').fetchall() == before
        assert decisions.numbering_choices(conn)[case['case_id']] == 'dismiss'
        assert work.pending(conn)[0][0] == case['case_id']
    queued = []
    restarted = RepairWorker(database=album.db, transfer_folder=str(album.root))
    monkeypatch.setattr(restarted, '_schedule_album_refresh', lambda key, folder, **kw: queued.append((key, kw)))
    restarted._resume_review_work()
    assert queued == [(case['case_id'], {'scope': 'plan', 'refresh_catalogue': False})]


def test_failed_plan_publication_rolls_back_finding_updates(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'contradictory_metadata')
    scan_case(album)
    with album.db._get_connection() as conn:
        before = conn.execute('SELECT id,details_json,updated_at FROM repair_findings').fetchall()
    original_emit = album.context.create_finding

    def changed_findings(**finding):
        finding['details']['test_new_guard'] = 'must not be partly published'
        return original_emit(**finding)

    def fail_plan(*args, **kwargs):
        raise RuntimeError('simulated snapshot write failure')

    album.context.create_finding = changed_findings
    monkeypatch.setattr('core.reconciliation.projection.persist_album_plan', fail_plan)
    result = LibraryReconciliationJob().scan(album.context)
    assert result.stopped_early
    with album.db._get_connection() as conn:
        assert conn.execute('SELECT id,details_json,updated_at FROM repair_findings').fetchall() == before


def test_scope_command_rolls_back_choice_and_projection_together(tmp_path, monkeypatch):
    from core.reconciliation import store
    album = build_album(tmp_path, monkeypatch, 'partial')
    case = scan_case(album)
    item = next(row for row in case['items'] if row['finding_type'] == 'reconciliation_intent')
    with album.db._get_connection() as conn:
        before = {table: conn.execute(f'SELECT * FROM {table}').fetchall() for table in (
            'reconcile_album_intent', 'reconcile_wanted_slots', 'reconcile_album_plans', 'reconcile_review_work')}
    monkeypatch.setattr(store, 'persist_album_plan', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('write failed')))
    result = album.worker.fix_finding(item['id'], fix_action='partial', review_approved=True)
    assert not result['success']
    with album.db._get_connection() as conn:
        assert {table: conn.execute(f'SELECT * FROM {table}').fetchall() for table in before} == before


def test_release_command_rolls_back_pin_when_followup_cannot_be_saved(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    before = album.db.get_album_canonical('10')
    monkeypatch.setattr(work, 'enqueue', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('work storage failed')))
    with pytest.raises(RuntimeError):
        with album.db._get_connection() as conn:
            album_state.choose_release(conn, album.db, case['case_id'], '10', 'musicbrainz', 'different', 1,
                                       audio_copy=True, folder=str(album.folder))
    assert album.db.get_album_canonical('10') == before
    with album.db._get_connection() as conn:
        assert not conn.execute('SELECT * FROM reconcile_audio_copy_releases').fetchall()
        assert not conn.execute('SELECT * FROM reconcile_release_decisions').fetchall()


def test_old_replan_cannot_acknowledge_new_work_and_retries_survive_restart(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    with album.db._get_connection() as conn:
        first = work.enqueue(conn, case['case_id'], str(album.folder))
        second = work.enqueue(conn, case['case_id'], str(album.folder), scope='discovery')
        work.complete(conn, case['case_id'], first)
        assert work.pending(conn)[0][-1] == second
        work.complete(conn, case['case_id'], second, error='retry later')
    with album.db._get_connection() as conn:
        assert not work.pending(conn)
        row = conn.execute('SELECT generation,error,next_attempt FROM reconcile_review_work').fetchone()
        assert row[:2] == (second, 'retry later') and row[2] > 0


def test_state_readers_do_not_write_or_repair_data(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'partial')
    case = scan_case(album)
    # A plain connection permits SQLite authorizer checks without pool internals.
    with sqlite3.connect(album.db.database_path if hasattr(album.db, 'database_path') else str(tmp_path / 'music.db')) as conn:
        writes = []
        def authorize(action, table, column, database, trigger):
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                writes.append((table, column))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        conn.set_authorizer(authorize)
        album_state.intents(conn)
        album_state.assignments(conn)
        decisions.numbering_choices(conn)
        decisions.find_review(conn, case['case_id'])
        outstanding(conn)
        work.pending(conn)
        assert not writes


def test_same_album_planning_waits_and_next_mutation_cannot_overtake_a_plan():
    from core.library.maintenance_lock import MaintenanceLock
    lock = MaintenanceLock()
    entered, finished = threading.Event(), threading.Event()
    def mutate():
        entered.set()
        with lock:
            finished.set()
    with lock.planning('other'):
        thread = threading.Thread(target=mutate)
        thread.start()
        assert entered.wait(1)
        assert not finished.wait(.05)
    thread.join(1)
    assert finished.is_set()
    blocked = threading.Event()
    def plan():
        with lock.planning('same'):
            blocked.set()
    with lock.for_album('same'):
        thread = threading.Thread(target=plan)
        thread.start()
        assert not blocked.wait(.05)
    thread.join(1)
    assert blocked.is_set()
