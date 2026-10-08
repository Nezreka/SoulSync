"""Queue cases use physical/album identity and never hide a failed action."""

from __future__ import annotations

import json
import sqlite3
import threading

from flask import Flask

from core.repair_review_identity import missing_slot_key, review_case_key
from core.repair_review_queue import classify_case, get_case, list_cases, safe_finding_ids
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


class _Connection(sqlite3.Connection):
    def close(self):
        pass


class _Database:
    def __init__(self):
        self.conn = sqlite3.connect(':memory:', factory=_Connection)
        self.conn.execute('''
            CREATE TABLE repair_findings (
                id INTEGER PRIMARY KEY, job_id TEXT, finding_type TEXT,
                severity TEXT, status TEXT, entity_type TEXT, entity_id TEXT,
                file_path TEXT, title TEXT, description TEXT, details_json TEXT,
                user_action TEXT, created_at TEXT, updated_at TEXT,
                last_error TEXT, review_case_key TEXT
            )
        ''')
        self.conn.execute('CREATE INDEX idx_review ON repair_findings(status, review_case_key)')

    def _get_connection(self):
        return self.conn

    def add(self, finding_id, path, details, *, status='pending', kind='acoustid_mismatch',
            job_id='acoustid_scanner'):
        self.conn.execute('''
            INSERT INTO repair_findings
            (id, job_id, finding_type, severity, status, file_path, title,
             details_json, created_at, updated_at, review_case_key)
            VALUES (?, ?, ?, 'warning', ?, ?, ?, ?,
                    '2026-10-05', '2026-10-05', ?)
        ''', (finding_id, job_id, kind, status, path, f'Finding {finding_id}',
              json.dumps(details), review_case_key(details, path, finding_id)))
        self.conn.commit()


def test_same_title_different_release_and_owner_never_merge():
    db = _Database()
    for finding_id, album_id, profile in ((1, 10, 1), (2, 11, 1), (3, 10, 2)):
        db.add(finding_id, f'/music/{finding_id}.flac',
               {'album_id': album_id, 'profile_id': profile,
                'album_title': 'Rated R', 'artist_name': 'Queens of the Stone Age'})
    cases = list_cases(db)
    assert cases['total'] == 3
    assert all(case['pending'] == 1 for case in cases['items'])


def test_one_album_row_in_two_physical_folders_is_two_scan_cases():
    db = _Database()
    db.add(1, '/music/Disc One/01.flac',
           {'album_id': 10, 'album_folder': '/music/Disc One'})
    db.add(2, '/music/Disc Two/01.flac',
           {'album_id': 10, 'album_folder': '/music/Disc Two'})
    assert list_cases(db)['total'] == 2


def test_missing_slot_identity_includes_source_track_when_positions_repeat():
    a = {'disc_number': 1, 'track_number': 1, 'source_track_id': 'intro-a'}
    b = {'disc_number': 1, 'track_number': 1, 'source_track_id': 'intro-b'}
    assert missing_slot_key(10, 'spotify', 'release', a) != missing_slot_key(
        10, 'spotify', 'release', b)


def test_folder_groups_bad_tags_but_unnamed_rows_are_not_lost():
    db = _Database()
    db.add(1, '/music/A/01.flac', {'album_title': 'Wrong'})
    db.add(2, '/music/A/02.flac', {'album_title': 'Other'})
    db.add(3, None, {})
    cases = list_cases(db)
    assert cases['total'] == 2
    grouped = next(case for case in cases['items'] if case['pending'] == 2)
    detail = get_case(db, grouped['case_id'])
    assert {finding['id'] for finding in detail['items']} == {1, 2}
    assert all(finding['evidence_token'] for finding in detail['items'])
    searched = list_cases(db, q='Wrong')
    assert searched['total'] == 1
    assert searched['items'][0]['pending'] == 2


def test_job_filter_excludes_other_findings_even_in_the_same_case():
    db = _Database()
    details = {'album_id': 10, 'album_title': 'Rated R'}
    db.add(1, '/music/Rated R/01.flac', details, job_id='library_reconciliation',
           kind='reconciliation_metadata')
    db.add(2, '/music/Rated R/02.flac', details)
    db.add(3, '/music/Other/01.flac', {'album_id': 11})
    queue = list_cases(db, job_id='library_reconciliation')
    assert queue['total'] == 1
    assert queue['items'][0]['pending'] == 1
    case = get_case(db, queue['items'][0]['case_id'], job_id='library_reconciliation')
    assert [item['id'] for item in case['items']] == [1]
    assert list_cases(db)['total'] == 2


def test_album_plan_separates_safe_metadata_from_numbering_and_file_actions():
    items = [
        {'id': 1, 'finding_type': 'reconciliation_metadata',
         'details': {'changes': {'title': 'Correct title'}, 'confidence': 'clear'}},
        {'id': 2, 'finding_type': 'reconciliation_metadata',
         'details': {'changes': {'track_number': 1}, 'confidence': 'clear'}},
        {'id': 3, 'finding_type': 'reconciliation_extra', 'details': {}},
    ]

    plan = classify_case(items)

    assert plan == {
        'ready': 1, 'manual': 1, 'destructive': 1, 'blocked': 0,
        'total': 3, 'status': 'needs_review',
        'blocker_reasons': [
            'The release uses a different disc or track numbering scheme',
            'This action moves or removes a file and requires review',
        ],
        'can_apply_ready': True, 'can_organize': False,
    }
    assert [item['review_tier'] for item in items] == ['ready', 'manual', 'destructive']


def test_release_choice_blocks_metadata_from_bulk_apply():
    items = [
        {'id': 1, 'finding_type': 'reconciliation_release', 'details': {}},
        {'id': 2, 'finding_type': 'reconciliation_metadata',
         'details': {'changes': {'title': 'Correct title'}, 'confidence': 'clear'}},
    ]

    plan = classify_case(items)

    assert plan['ready'] == 0
    assert plan['blocked'] == 1
    assert items[1]['review_reason'] == 'The selected release must be resolved first'


def test_safe_finding_ids_returns_only_planner_approved_metadata():
    db = _Database()
    details = {'album_id': 10, 'album_title': 'Album'}
    db.add(1, '/music/Album/01.flac',
           {**details, 'changes': {'title': 'One'}, 'confidence': 'clear'},
           job_id='library_reconciliation', kind='reconciliation_metadata')
    db.add(2, '/music/Album/02.flac',
           {**details, 'changes': {'track_number': 2}, 'confidence': 'clear'},
           job_id='library_reconciliation', kind='reconciliation_metadata')
    case_id = list_cases(db, job_id='library_reconciliation')['items'][0]['case_id']

    assert list_cases(db, job_id='library_reconciliation')['ready_total'] == 1

    ids, cases = safe_finding_ids(db, case_id=case_id)

    assert ids == [1]
    assert cases == [case_id]


def test_stale_review_action_cannot_run_fix():
    db = _Database()
    db.add(1, '/music/A/01.flac', {'album_id': 5})
    case_id = list_cases(db)['items'][0]['case_id']
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._review_apply_lock = threading.Lock()
    called = []
    worker.fix_finding = lambda *_args, **_kwargs: called.append(True) or {'success': True}
    result = worker.apply_review_decision(case_id, 1, evidence_token='old', action='fix')
    assert result['stale'] is True
    assert called == []


def test_failed_fix_stays_in_same_case_with_error():
    db = _Database()
    db.add(1, '/music/A/01.flac', {'album_id': 5})
    case_id = list_cases(db)['items'][0]['case_id']
    token = get_case(db, case_id)['items'][0]['evidence_token']
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._review_apply_lock = threading.Lock()
    worker.fix_finding = lambda *_args, **_kwargs: {'success': False, 'error': 'file locked'}
    result = worker.apply_review_decision(case_id, 1, evidence_token=token, action='fix')
    assert result == {'success': False, 'error': 'file locked'}
    assert get_case(db, case_id)['count'] == 1


def test_review_http_api_returns_exact_case_and_rejects_stale_apply(monkeypatch):
    from api import repair

    db = _Database()
    db.add(1, '/music/A/01.flac', {'album_id': 5, 'album_title': 'OK Computer'})
    db.add(2, '/music/A/02.flac', {'album_id': 5, 'album_title': 'OK Computer'},
           job_id='library_reconciliation', kind='reconciliation_metadata')
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._review_apply_lock = threading.Lock()
    worker.fix_finding = lambda *_args, **_kwargs: {'success': True}
    monkeypatch.setattr(repair, '_worker_getter', lambda: worker)
    app = Flask(__name__)
    app.register_blueprint(repair.create_blueprint())
    client = app.test_client()
    queue = client.get('/api/repair/review-queue').json
    case_id = queue['items'][0]['case_id']
    detail = client.get(f'/api/repair/review-queue/{case_id}').json
    assert detail['count'] == 2
    assert detail['album'] == 'OK Computer'
    filtered = client.get('/api/repair/review-queue?job_id=library_reconciliation').json
    assert filtered['items'][0]['pending'] == 1
    filtered_detail = client.get(
        f'/api/repair/review-queue/{case_id}?job_id=library_reconciliation').json
    assert [item['id'] for item in filtered_detail['items']] == [2]
    response = client.post(f'/api/repair/review-queue/{case_id}/findings/1/apply',
                           json={'action': 'fix', 'evidence_token': 'old'})
    assert response.status_code == 409
    assert response.json['stale'] is True
    calls = []
    worker.start_review_safe = lambda **kwargs: calls.append(kwargs) or {
        'started': True, 'total': 4,
    }
    response = client.post('/api/repair/review-queue/apply-ready', json={
        'case_id': case_id, 'organize': True,
    })
    assert response.status_code == 200
    assert response.json == {'started': True, 'total': 4}
    assert calls == [{'case_id': case_id, 'organize': True}]


def test_accepted_reconciliation_exception_reopens_when_audio_evidence_changes(tmp_path):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    worker = RepairWorker(database=db)
    details = {'album_folder': '/music/Unknown', 'evidence_signature': 'first'}
    finding = dict(job_id='library_reconciliation',
                   finding_type='reconciliation_unresolved', severity='info',
                   entity_type='folder', entity_id='/music/Unknown', file_path=None,
                   title='Identify release', description='Audio identity unknown')
    assert worker._create_finding(**finding, details=details)
    case_id = worker.get_review_cases()['items'][0]['case_id']
    item = worker.get_review_case(case_id)['items'][0]
    assert worker.dismiss_finding(item['id'])
    assert not worker._create_finding(**finding, details=details)
    assert worker._create_finding(**finding,
                                  details={**details, 'evidence_signature': 'changed'})
    assert worker.get_review_case(case_id)['count'] == 1


def test_existing_findings_are_backfilled_into_exact_cases(tmp_path):
    conn = sqlite3.connect(tmp_path / 'old-repair.db')
    try:
        conn.execute('''CREATE TABLE repair_findings (
            id INTEGER PRIMARY KEY, job_id TEXT, finding_type TEXT, status TEXT,
            entity_type TEXT, entity_id TEXT, file_path TEXT, details_json TEXT,
            resolved_at TEXT
        )''')
        conn.execute('''CREATE TABLE repair_job_runs (id INTEGER PRIMARY KEY)''')
        conn.execute('''INSERT INTO repair_findings
            (id, job_id, finding_type, status, entity_type, entity_id, file_path, details_json)
            VALUES (1, 'acoustid_scanner', 'acoustid_mismatch', 'pending',
                    'track', '1', '/music/Rated R/01.flac', ?)''',
                     (json.dumps({'album_id': 10, 'profile_id': 2, 'album_title': 'Rated R'}),))
        db = MusicDatabase.__new__(MusicDatabase)
        db._migrate_repair_worker_columns(conn.cursor())
        row = conn.execute('SELECT review_case_key FROM repair_findings WHERE id = 1').fetchone()
        assert row[0] == 'album:2:10'
        indexes = {row[1] for row in conn.execute('PRAGMA index_list(repair_findings)')}
        assert 'idx_rf_review_case' in indexes
        assert 'idx_rf_review_job_case' in indexes
    finally:
        conn.close()
