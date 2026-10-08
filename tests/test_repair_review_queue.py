"""Queue cases use physical/album identity and never hide a failed action."""

from __future__ import annotations

import json
import sqlite3
import threading

from flask import Flask

from core.repair_review_identity import missing_slot_key, review_case_key
from core.repair_review_queue import classify_case, get_case, list_cases, safe_finding_ids
from core.library.reconciliation_store import ensure_schema, persist_album_plan, retire_unseen_plans
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

    assert {key: plan[key] for key in (
        'ready', 'manual', 'destructive', 'blocked', 'total', 'status',
        'blocker_reasons', 'can_apply_ready', 'can_organize',
    )} == {
        'ready': 1, 'manual': 1, 'destructive': 1, 'blocked': 0,
        'total': 3, 'status': 'needs_review',
        'blocker_reasons': [
            'The release uses a different disc or track numbering scheme',
            'This action moves or removes a file and requires review',
        ],
        'can_apply_ready': True, 'can_organize': False,
    }
    assert plan['decision_count'] == 3
    assert plan['categories'] == ['extra_files', 'metadata', 'numbering']
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


def test_title_flavor_is_safe_but_recording_version_change_is_manual():
    items = [
        {'id': 1, 'finding_type': 'reconciliation_metadata',
         'details': {'changes': {'title': {'new': 'Song'}}, 'confidence': 'clear',
                     'title_difference_kind': 'flavor'}},
        {'id': 2, 'finding_type': 'reconciliation_metadata',
         'details': {'changes': {'title': {'new': 'Song (Live)'}}, 'confidence': 'clear',
                     'title_difference_kind': 'version'}},
    ]

    plan = classify_case(items)

    assert [item['review_tier'] for item in items] == ['ready', 'manual']
    assert plan['categories'] == ['title_conventions', 'title_issues']


def test_artist_typography_change_is_grouped_as_an_artist_update():
    items = [{
        'id': finding_id,
        'finding_type': 'reconciliation_metadata',
        'details': {
            'changes': {'artist': {'old': 'Tool', 'new': 'TOOL'}},
            'confidence': 'clear',
            # The matcher may still describe the compared track titles. That
            # must not relabel an artist-only repair as a title convention.
            'title_difference_kind': 'typography',
        },
    } for finding_id in range(1, 14)]

    plan = classify_case(items)

    assert plan['categories'] == ['metadata']
    assert plan['ready'] == 13
    assert plan['decisions'][0]['label'] == 'Artist update'
    assert plan['decisions'][0]['summary'] == '13 tracks: Tool → TOOL.'


def test_album_intent_summary_states_the_album_level_question():
    plan = classify_case([{
        'id': 1, 'finding_type': 'reconciliation_intent', 'details': {},
    }])

    assert plan['decisions'][0]['label'] == 'Album intent'
    assert plan['decisions'][0]['summary'] == (
        'Choose whether this album should be complete.')


def test_material_title_conflict_is_not_hidden_inside_numbering_group():
    items = [{
        'id': 1, 'finding_type': 'reconciliation_metadata',
        'details': {
            'changes': {
                'title': {'new': 'Song (Live)'},
                'track_number': {'new': 2},
            },
            'confidence': 'clear', 'title_difference_kind': 'version',
        },
    }]

    plan = classify_case(items)

    assert plan['decisions'][0]['category'] == 'title_issues'
    assert plan['decisions'][0]['risk'] == 'manual'


def test_queue_category_filters_return_album_context_not_only_matching_rows():
    db = _Database()
    details = {'album_id': 10, 'album_title': 'Album'}
    db.add(1, '/music/Album/01.flac', {
        **details, 'changes': {'title': {'new': 'One'}}, 'confidence': 'clear',
        'title_difference_kind': 'flavor',
    }, job_id='library_reconciliation', kind='reconciliation_metadata')
    db.add(2, '/music/Album/02.flac', {
        **details, 'changes': {'track_number': {'new': 2}}, 'confidence': 'clear',
    }, job_id='library_reconciliation', kind='reconciliation_metadata')

    filtered = list_cases(db, job_id='library_reconciliation', category='title_conventions')

    assert filtered['total'] == 1
    assert filtered['items'][0]['pending'] == 2
    assert filtered['items'][0]['plan']['categories'] == ['numbering', 'title_conventions']


def test_case_includes_persisted_successful_assignments_around_the_open_action():
    db = _Database()
    details = {'album_id': 10, 'profile_id': 1, 'album_title': 'Album'}
    db.add(1, '/music/Album/02.flac', {
        **details, 'changes': {'track_number': {'new': 2}}, 'confidence': 'clear',
    }, job_id='library_reconciliation', kind='reconciliation_metadata')
    case_id = persist_album_plan(db, {**details, 'evidence_signature': 'evidence'}, {
        'source': 'musicbrainz', 'release_id': 'release-id',
        'assignments': [
            {'status': 'confirmed', 'file_path': '/music/Album/01.flac',
             'current_title': 'One', 'slot_title': 'One'},
            {'status': 'change', 'file_path': '/music/Album/02.flac',
             'current_title': 'Two', 'slot_title': 'Two',
             'changes': {'track_number': {'old': 1, 'new': 2}}},
        ],
    })

    case = get_case(db, case_id, job_id='library_reconciliation')

    assert len(case['album_plan']['assignments']) == 2
    assert case['album_plan']['counts'] == {
        'confirmed': 1, 'changed': 1, 'uncertain': 0,
        'missing': 0, 'not_owned': 0, 'extra': 0, 'settled': 0,
    }
    changed = case['album_plan']['assignments'][1]
    assert changed['finding_id'] == 1
    assert changed['review_tier'] == 'manual'


def test_persisted_plan_marks_previous_actions_settled_without_hiding_current_action():
    db = _Database()
    details = {'album_id': 10, 'profile_id': 1, 'album_title': 'Album'}
    db.add(1, '/music/Album/02.flac', {
        **details, 'changes': {'title': {'new': 'Current'}}, 'confidence': 'clear',
    }, job_id='library_reconciliation', kind='reconciliation_metadata')
    case_id = persist_album_plan(db, {**details, 'evidence_signature': 'evidence'}, {
        'source': 'musicbrainz', 'release_id': 'release-id',
        'assignments': [
            {'status': 'change', 'file_path': '/music/Album/01.flac',
             'current_title': 'Already handled', 'slot_title': 'Already handled',
             'changes': {'album': {'old': 'Old', 'new': 'Album'}}},
            {'status': 'change', 'file_path': '/music/Album/02.flac',
             'current_title': 'Current', 'slot_title': 'Current',
             'changes': {'title': {'old': 'Curent', 'new': 'Current'}}},
        ],
    })

    case = get_case(db, case_id, job_id='library_reconciliation')

    previous, current = case['album_plan']['assignments']
    assert previous['status'] == 'settled'
    assert previous['evidence_status'] == 'change'
    assert previous['changes'] == {}
    assert previous['action_state'] == 'resolved'
    assert current['status'] == 'change'
    assert current['finding_id'] == 1
    assert case['album_plan']['counts']['settled'] == 1
    assert case['album_plan']['counts']['changed'] == 1


def test_album_intent_decision_owns_missing_plan_rows():
    db = _Database()
    details = {'album_id': 10, 'profile_id': 1, 'album_title': 'Partial Album'}
    db.add(1, None, details, job_id='library_reconciliation',
           kind='reconciliation_intent')
    case_id = persist_album_plan(db, {**details, 'evidence_signature': 'evidence'}, {
        'source': 'musicbrainz', 'release_id': 'release-id',
        'assignments': [
            {'status': 'confirmed', 'file_path': '/music/Album/01.flac',
             'current_title': 'One', 'slot_title': 'One'},
            {'status': 'missing', 'source_track_id': 'track-2',
             'slot_disc_number': 1, 'slot_track_number': 2, 'slot_title': 'Two'},
        ],
    })

    case = get_case(db, case_id, job_id='library_reconciliation')

    missing = case['album_plan']['assignments'][1]
    assert missing['status'] == 'missing'
    assert missing['finding_id'] == 1
    assert missing['review_tier'] == 'manual'
    assert case['album_plan']['counts']['missing'] == 1


def test_pending_version_extra_keeps_its_paired_missing_slot_visible():
    db = _Database()
    details = {'album_id': 10, 'profile_id': 1, 'album_title': 'Deluxe Album'}
    extra_path = '/music/Album/16 - Song (Abbey Road Sessions).flac'
    db.add(1, extra_path, details, job_id='library_reconciliation',
           kind='reconciliation_extra')
    case_id = persist_album_plan(db, {**details, 'evidence_signature': 'evidence'}, {
        'source': 'musicbrainz', 'release_id': 'release-id',
        'assignments': [
            {'status': 'missing', 'slot_title': 'Song',
             'slot_disc_number': 2, 'slot_track_number': 3},
            {'status': 'extra', 'file_path': extra_path,
             'file': '16 - Song (Abbey Road Sessions).flac',
             'current_title': 'Song (Abbey Road Sessions)'},
        ],
    })

    case = get_case(db, case_id, job_id='library_reconciliation')

    missing, extra = case['album_plan']['assignments']
    assert missing['status'] == 'missing'
    assert extra['status'] == 'extra'
    assert missing['relationship'] == extra['relationship'] == 'version_conflict'
    assert missing['finding_id'] == extra['finding_id'] == 1
    assert missing['action_state'] == extra['action_state'] == 'destructive'
    assert case['album_plan']['counts']['missing'] == 1
    assert case['album_plan']['counts']['extra'] == 1


def test_repeated_audio_conflicts_are_one_album_question():
    items = [
        {'id': 1, 'finding_type': 'reconciliation_unresolved', 'details': {}},
        {'id': 2, 'finding_type': 'reconciliation_unresolved', 'details': {}},
    ]

    plan = classify_case(items)

    assert plan['decision_count'] == 1
    assert plan['decisions'][0]['category'] == 'audio_conflicts'
    assert plan['decisions'][0]['finding_ids'] == [1, 2]


def test_fully_ready_filter_excludes_an_album_with_manual_work():
    db = _Database()
    ready = {'album_id': 10, 'album_title': 'Mixed', 'confidence': 'clear',
             'changes': {'album': {'new': 'Mixed'}}}
    db.add(1, '/music/Mixed/01.flac', ready,
           job_id='library_reconciliation', kind='reconciliation_metadata')
    db.add(2, '/music/Mixed/02.flac', {'album_id': 10, 'album_title': 'Mixed'},
           job_id='library_reconciliation', kind='reconciliation_unresolved')
    db.add(3, '/music/Ready/01.flac', {
        'album_id': 11, 'album_title': 'Ready', 'confidence': 'clear',
        'changes': {'album': {'new': 'Ready'}},
    }, job_id='library_reconciliation', kind='reconciliation_metadata')

    result = list_cases(db, job_id='library_reconciliation', risk='ready')

    assert result['total'] == 1
    assert result['items'][0]['album'] == 'Ready'


def test_title_convention_filter_excludes_numbering_changes_with_title_flavor():
    db = _Database()
    db.add(1, '/music/Numbered/01.flac', {
        'album_id': 10, 'album_title': 'Numbered', 'confidence': 'clear',
        'title_difference_kind': 'flavor',
        'changes': {'track_number': {'new': 2}, 'title': {'new': 'Song'}},
    }, job_id='library_reconciliation', kind='reconciliation_metadata')

    result = list_cases(
        db, job_id='library_reconciliation', category='title_conventions')

    assert result['total'] == 0


def test_title_convention_filter_excludes_artist_only_typography_change():
    db = _Database()
    db.add(1, '/music/Lateralus/01.flac', {
        'album_id': 10, 'album_title': 'Lateralus', 'confidence': 'clear',
        'title_difference_kind': 'typography',
        'changes': {'artist': {'old': 'Tool', 'new': 'TOOL'}},
    }, job_id='library_reconciliation', kind='reconciliation_metadata')

    title_result = list_cases(
        db, job_id='library_reconciliation', category='title_conventions')
    metadata_result = list_cases(
        db, job_id='library_reconciliation', category='metadata')

    assert title_result['total'] == 0
    assert metadata_result['total'] == 1
    assert metadata_result['items'][0]['plan']['categories'] == ['metadata']


def test_stale_plan_retirement_handles_more_than_sqlite_parameter_limit():
    db = _Database()
    ensure_schema(db.conn)
    rows = [(f'album:1:{index}', '{}', f'sig-{index}') for index in range(1100)]
    db.conn.executemany('''
        INSERT INTO reconcile_album_plans
            (case_id, plan_json, evidence_signature)
        VALUES (?, ?, ?)
    ''', rows)
    db.conn.commit()

    retire_unseen_plans(db, {'album:1:1099'})

    assert db.conn.execute(
        'SELECT case_id FROM reconcile_album_plans').fetchall() == [('album:1:1099',)]


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


def test_grouped_title_convention_decision_applies_every_current_action():
    db = _Database()
    for finding_id in (1, 2):
        db.add(finding_id, f'/music/Album/{finding_id:02}.flac', {
            'album_id': 10, 'album_title': 'Album', 'confidence': 'clear',
            'title_difference_kind': 'flavor',
            'changes': {'title': {'new': f'Song {finding_id}'}},
        }, job_id='library_reconciliation', kind='reconciliation_metadata')
    case_id = list_cases(db, job_id='library_reconciliation')['items'][0]['case_id']
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._review_apply_lock = threading.Lock()
    applied = []
    worker.fix_finding = lambda finding_id, **_kwargs: (
        applied.append(finding_id) or {'success': True})

    result = worker.apply_review_decision_group(case_id, 'title_conventions', action='fix')

    assert result == {'success': True, 'applied': 2, 'total': 2}
    assert applied == [1, 2]


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
