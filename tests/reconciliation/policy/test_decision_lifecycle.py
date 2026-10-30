"""policy / decision lifecycle regression contracts."""
from __future__ import annotations

import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

import core.reconciliation.selection as reconciliation_selection
from core.library.maintenance_lock import mutation_lock
from core.reconciliation import search as search
from core.reconciliation.queue import (
    classify_case,
    get_case,
    list_cases,
    ready_batch_preview,
)
from core.reconciliation.selection import (
    _candidate_rank,
    _edition_preference,
)
from core.reconciliation.store import (
    persist_album_plan,
)
from core.repair_jobs.base import JobContext
from core.repair_jobs.library_reconciliation import LibraryReconciliationJob
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase
from tests.reconciliation.support.acceptance import (
    build_album,
    scan_case,
)
from tests.reconciliation.support.audio import (
    _Acoustid,
    _minimal_flac,
)
from tests.reconciliation.support.queue import _Database, _ready_selection
from tests.reconciliation.support.scenarios import (
    _album,
    _metadata_recommendation_album,
)


def test_album_apply_all_includes_reviewed_metadata_but_not_other_decisions():
    worker = RepairWorker.__new__(RepairWorker)
    case = {
        'folder': '/music/album', 'plan': {'decisions': []},
        'items': [
            {'id': 1, 'finding_type': 'reconciliation_metadata', 'review_tier': 'ready'},
            {'id': 2, 'finding_type': 'reconciliation_metadata', 'review_tier': 'manual'},
            {'id': 3, 'finding_type': 'reconciliation_unresolved', 'review_tier': 'manual'},
        ],
    }
    worker.get_review_case = lambda *_args, **_kwargs: case
    captured = {}
    worker.start_bulk_fix = lambda **kwargs: captured.update(kwargs) or {'started': True}
    assert worker.start_review_album_metadata('album-case')['started']
    assert captured['finding_ids'] == [1, 2]
    assert captured['review_case_folders'] == [('album-case', '/music/album')]
    case['plan']['decisions'] = [{'category': 'release_conflicts'}]
    assert not worker.start_review_album_metadata('album-case')['started']


def test_bulk_selection_admits_only_explicitly_reviewed_missing_slot_ids():
    for reviewed, expected in ((False, []), (True, [1])):
        db = _Database()
        db.add(1, None, {'album_id': 10, 'album_title': 'Album'},
               job_id='library_reconciliation', kind='reconciliation_missing')
        worker = RepairWorker.__new__(RepairWorker)
        worker.db = db
        assert worker._pending_fixable_ids(
            finding_ids=[1], allow_review_ids=reviewed) == expected


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
        'can_apply_ready': False, 'can_organize': False,
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


def test_numbering_decision_describes_the_resulting_scheme():
    rows = [{
        'finding_type': 'reconciliation_metadata',
        'details': {
            'numbering_mode': 'digital',
            'changes': {
                'track_number': {'old': str(index), 'new': str(index + 10)},
                'disc_number': {'old': '2', 'new': '1'},
            },
        },
    } for index in range(1, 4)]

    decision = classify_case(rows)['decisions'][0]

    assert decision['label'] == 'Use continuous track numbering'
    assert decision['summary'] == (
        'Renumber 3 tracks as one sequence instead of restarting at each disc.')


def test_album_consistency_question_explains_the_conflict_and_preserves_group_scope():
    conflict = {'finding_type': 'reconciliation_metadata', 'details': {
        'confidence': 'clear',
        'metadata_review_reason': 'Choose consistent embedded album metadata for this album',
        'album_consistency': {
            'album': [{'value': 'Album', 'files': 1}, {'value': 'Album Deluxe', 'files': 1}],
            'musicbrainz_release_id': [{'value': 'old', 'files': 1}, {'value': 'new', 'files': 1}],
        },
        'changes': {'album': {'old': 'Album Deluxe', 'new': 'Album'},
                    'musicbrainz_release_id': {'old': 'old', 'new': 'new'}},
    }}
    ready = {'finding_type': 'reconciliation_metadata', 'details': {
        'confidence': 'clear',
        'changes': {'musicbrainz_recording_id': {'old': '', 'new': 'recording'}},
    }}
    plan = classify_case([conflict, ready])
    decision = plan['decisions'][0]
    assert decision['label'] == 'Resolve conflicting album metadata'
    assert decision['summary'] == (
        'Files disagree on album title and MusicBrainz release ID. '
        'Proposed corrections affect 1 track; this group also includes metadata changes on 1 other track.')
    assert decision['action_count'] == 2
    assert not plan['can_apply_ready']

    conflict['details']['changes']['track_number'] = {'old': '7', 'new': '2'}
    numbering = classify_case([conflict])['decisions'][0]
    assert numbering['category'] == 'numbering'
    assert numbering['additional_metadata_summary'] == (
        'Numbering approval does not approve the other metadata changes shown for these tracks.')
    del conflict['details']['changes']['track_number']

    # Saved decisions and unrelated edits must not revive a resolved album question.
    conflict['details']['plan_approved'] = True
    assert classify_case([conflict, ready])['decisions'][0]['label'] != decision['label']
    conflict['details'].pop('plan_approved')
    conflict['details']['changes'] = {'musicbrainz_recording_id': {'old': '', 'new': 'recording'}}
    assert classify_case([conflict])['decisions'][0]['label'] == 'Add MusicBrainz recording IDs'


def test_ready_preview_returns_only_planner_approved_metadata():
    db = _Database()
    details = {'album_id': 10, 'album_title': 'Album'}
    db.add(1, '/music/Album/01.flac',
           {**details, 'changes': {'title': 'One'}, 'confidence': 'clear'},
           job_id='library_reconciliation', kind='reconciliation_metadata')
    db.add(2, '/music/Album/02.flac',
           {**details, 'changes': {'track_number': 2}, 'confidence': 'clear'},
           job_id='library_reconciliation', kind='reconciliation_metadata')
    case_id = list_cases(db, job_id='library_reconciliation')['items'][0]['case_id']

    assert list_cases(db, job_id='library_reconciliation')['ready_total'] == 0

    ids, cases = _ready_selection(db, case_id=case_id)

    assert ids == []
    assert cases == []


def test_grouped_title_convention_decision_stages_without_applying():
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
    worker._review_apply_lock = threading.RLock()
    calls = []
    worker.start_bulk_fix = lambda **kwargs: calls.append(kwargs) or {
        'started': True, 'operation_id': 'durable-operation', 'total': 2}
    result = worker.apply_review_decision_group(case_id, 'title_conventions', action='fix')
    assert result['success'] and result['saved'] == 2
    assert not calls
    case = get_case(db, case_id)
    assert all(item['status'] == 'pending' for item in case['items'])
    assert all(item['details']['plan_approved'] for item in case['items'])


def test_numbering_draft_keeps_independent_metadata_and_invalidates_on_change():
    db = _Database()
    db.add(1, '/music/Album/09.flac', {
        'album_id': 10, 'album_title': 'Album', 'confidence': 'clear',
        'changes': {'track_number': {'old': 9, 'new': 3},
                    'musicbrainz_recording_id': {'old': '', 'new': 'recording'}},
        'db_data': {'track_number': 3, 'musicbrainz_recording_id': 'recording'},
    }, job_id='library_reconciliation', kind='reconciliation_metadata')
    key = list_cases(db, job_id='library_reconciliation')['items'][0]['case_id']
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._review_apply_lock = threading.RLock()
    assert worker.apply_review_decision_group(key, 'numbering', action='dismiss')['success']
    item = get_case(db, key)['items'][0]
    assert item['details']['db_data'] == {'musicbrainz_recording_id': 'recording'}
    assert item['review_tier'] == 'ready'
    assert _ready_selection(db)[0] == [1]
    assert list_cases(db, job_id='library_reconciliation', risk='ready')['total'] == 1
    conn = db._get_connection()
    raw = json.loads(conn.execute('SELECT details_json FROM repair_findings WHERE id=1').fetchone()[0])
    assert raw['db_data']['track_number'] == 3  # untouched scan evidence
    raw['changes']['track_number']['new'] = 4
    conn.execute('UPDATE repair_findings SET details_json=? WHERE id=1', (json.dumps(raw),))
    conn.commit()
    conn.close()
    assert get_case(db, key)['items'][0]['review_tier'] == 'manual'
    assert _ready_selection(db)[0] == []
    assert list_cases(db, job_id='library_reconciliation', risk='ready')['total'] == 0


@pytest.mark.parametrize('numbering', ['fix', 'dismiss'])
def test_numbering_choice_does_not_approve_other_metadata_and_survives_followup(numbering):
    db = _Database()
    db.add(1, '/music/Album/11.flac', {
        'album_id': 10, 'album_title': 'Album', 'confidence': 'clear',
        'changes': {'track_number': {'old': 11, 'new': 1},
                    'disc_number': {'old': 1, 'new': 2},
                    'artist': {'old': 'Various Artists', 'new': 'Matchbox Twenty'},
                    'musicbrainz_recording_id': {'old': '', 'new': 'recording'}},
        'db_data': {'track_number': 1, 'disc_number': 2, 'artist_name': 'Matchbox Twenty'},
    }, job_id='library_reconciliation', kind='reconciliation_metadata')
    key = list_cases(db, job_id='library_reconciliation')['items'][0]['case_id']
    worker = RepairWorker.__new__(RepairWorker)
    worker.db, worker._review_apply_lock = db, threading.RLock()
    assert worker.apply_review_decision_group(key, 'numbering', action=numbering)['success']
    case = get_case(db, key)
    item = case['items'][0]
    assert item['category'] == 'metadata'
    assert item['review_tier'] == 'manual'
    assert not item['details'].get('plan_approved')
    assert list_cases(db, job_id='library_reconciliation', risk='ready')['total'] == 0
    assert _ready_selection(db)[0] == []
    assert worker.apply_review_decision_group(key, 'metadata', action='fix')['success']
    item = get_case(db, key)['items'][0]
    assert item['review_tier'] == 'ready'
    assert ('track_number' in item['details']['db_data']) == (numbering == 'fix')
    assert ('disc_number' in item['details']['changes']) == (numbering == 'fix')
    assert _ready_selection(db)[0] == [1]
    assert list_cases(db, job_id='library_reconciliation', risk='ready')['total'] == 1
    # Reopening metadata preserves the independently chosen numbering.
    assert worker.apply_review_decision_group(key, 'metadata', action='reset')['success']
    assert get_case(db, key)['items'][0]['details']['numbering_choice'] == numbering
    assert _ready_selection(db)[0] == []
    assert worker.apply_review_decision_group(key, 'numbering', action='reset')['success']
    assert get_case(db, key)['items'][0]['category'] == 'numbering'


def test_saved_legacy_flattening_requires_reanalysis_before_apply():
    db = _Database()
    db.add(1, '/music/Album/01.flac', {
        'album_id': 10, 'confidence': 'clear', 'numbering_mode': 'digital',
        'release_disc_count': 2,
        'changes': {'track_number': {'old': 1, 'new': 11},
                    'disc_number': {'old': 2, 'new': 1}},
    }, job_id='library_reconciliation', kind='reconciliation_metadata')
    key = list_cases(db, job_id='library_reconciliation')['items'][0]['case_id']
    worker = RepairWorker.__new__(RepairWorker)
    worker.db, worker._review_apply_lock = db, threading.RLock()
    assert worker.apply_review_decision_group(key, 'numbering', action='fix')['success']
    assert worker.apply_review_decision_group(key, 'metadata', action='fix')['success']
    item = get_case(db, key)['items'][0]
    assert item['review_tier'] == 'manual'
    assert 'Reanalyze' in item['review_reason']
    assert _ready_selection(db)[0] == []
    assert list_cases(db, job_id='library_reconciliation', risk='ready')['total'] == 0


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


def test_saved_bulk_plan_is_bound_to_current_choices_and_evidence():
    db = _Database()
    db.add(1, '/music/Album/09.flac', {
        'album_id': 10, 'album_title': 'Album', 'confidence': 'clear',
        'changes': {'track_number': {'old': 9, 'new': 3}},
        'db_data': {'track_number': 3},
    }, job_id='library_reconciliation', kind='reconciliation_metadata')
    key = list_cases(db, job_id='library_reconciliation')['items'][0]['case_id']
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._review_apply_lock = threading.RLock()
    calls = []
    worker.start_bulk_fix = lambda **kwargs: calls.append(kwargs) or {'started': True}
    before = get_case(db, key)
    assert not worker.save_review_plan(key, before['plan_signature'])['success']
    assert worker.apply_review_decision_group(key, 'numbering', action='fix')['success']
    case = get_case(db, key)
    assert worker.save_review_plan(key, case['plan_signature'])['success']
    assert not calls
    saved = worker.saved_review_plans()
    assert len(saved) == 1
    assert worker.start_saved_review_plans(saved)['started']
    assert calls[0]['finding_ids'] == [1]
    assert worker.apply_review_decision_group(key, 'numbering', action='reset')['success']
    assert worker.saved_review_plans() == []
    assert not worker.start_saved_review_plans(saved)['started']
    assert len(calls) == 1
    assert get_case(db, key)['items'][0]['status'] == 'pending'


def test_retired_reconciliation_finding_is_not_treated_as_user_acceptance(tmp_path):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    worker = RepairWorker(database=db)
    finding = dict(job_id='library_reconciliation', finding_type='reconciliation_extra',
                   severity='warning', entity_type='track', entity_id='track',
                   file_path='/music/album/01.flac', title='Wrong audio', description='Wrong recording',
                   details={'album_id': 1, 'album_folder': '/music/album',
                            'fingerprint_key': 'same-audio', 'audio_conflict': 'different_recording'})
    assert worker._create_finding(**finding)
    case = worker.get_review_cases()['items'][0]['case_id']
    first = worker.get_review_case(case)['items'][0]['id']
    worker.resolve_finding(first, action='obsolete')
    assert worker._create_finding(**finding)
    assert worker.get_review_case(case)['items'][0]['id'] != first


def test_saved_approval_readiness_agrees_after_evidence_is_reserialized():
    db = _Database()
    details = {'album_id': 10, 'confidence': 'clear',
               'changes': {'track_number': {'old': 9, 'new': 3}}}
    db.add(1, '/music/Album/09.flac', details,
           job_id='library_reconciliation', kind='reconciliation_metadata')
    key = list_cases(db, job_id='library_reconciliation')['items'][0]['case_id']
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._review_apply_lock = threading.RLock()
    worker.apply_review_decision_group(key, 'numbering', action='fix')
    assert get_case(db, key)['plan']['ready'] == 1
    db.conn.execute('UPDATE repair_findings SET details_json=? WHERE id=1', (json.dumps(details, indent=2),))
    db.conn.commit()
    assert get_case(db, key)['plan']['ready'] == 0
    assert list_cases(db, job_id='library_reconciliation', risk='ready')['total'] == 0
    assert _ready_selection(db)[0] == []


def test_saved_file_choice_is_automatically_ready_and_revalidates_evidence():
    db = _Database()
    db.add(1, '/music/Album/extra.flac', {'album_id': 10, 'album_title': 'Album'},
           job_id='library_reconciliation', kind='reconciliation_extra')
    db.conn.execute("UPDATE repair_findings SET last_error='A previous apply failed' WHERE id=1")
    db.conn.commit()
    key = list_cases(db, job_id='library_reconciliation')['items'][0]['case_id']
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._review_apply_lock = threading.RLock()
    item = get_case(db, key)['items'][0]
    assert worker.apply_review_decision(key, 1, evidence_token=item['evidence_token'],
                                      action='fix', fix_action='quarantine')['success']
    case = get_case(db, key)
    assert case['plan']['ready'] == 1
    assert case['items'][0]['last_error'] is None
    assert _ready_selection(db)[0] == [1]
    assert _ready_selection(db, case_id=key)[0] == [1]
    assert worker.save_review_plan(key, case['plan_signature'])['success']
    saved = worker.saved_review_plans()
    assert saved[0]['file_actions'] == 1
    db.conn.execute("UPDATE repair_findings SET details_json=json_set(details_json,'$.evidence_signature','changed') WHERE id=1")
    db.conn.commit()
    assert _ready_selection(db, case_id=key)[0] == []
    assert worker.saved_review_plans() == []


def test_whole_album_readiness_counts_choices_and_waiting_consistently():
    from core.reconciliation import choices as choices
    from core.reconciliation.queue import queue_summary
    db = _Database()
    for fid, folder, kind, details in [
        (1, 'mixed', 'reconciliation_metadata', {'changes': {'title': {'new': 'One'}}}),
        (2, 'mixed', 'reconciliation_metadata', {'changes': {'track_number': {'new': 2}}}),
        (3, 'waiting', 'reconciliation_missing', {'fulfillment_state': 'wishlisted'}),
        (4, 'ready', 'reconciliation_metadata', {'changes': {'title': {'new': 'Four'}}}),
        (5, 'files', 'reconciliation_extra', {}),
    ]:
        db.add(fid, f'/music/{folder}/{fid}.flac', {'album_folder': f'/music/{folder}', **details},
               job_id='library_reconciliation', kind=kind)
    totals = queue_summary(db, job_id='library_reconciliation')
    assert totals == {'all': 4, 'review': 1, 'ready': 1, 'files': 1, 'waiting': 1, 'ready_total': 1, 'ready_albums_total': 1}
    assert _ready_selection(db)[0] == [4]
    db.conn.rollback()
    # Saving the only remaining choice automatically makes the entire album ready.
    choices.ensure_schema(db.conn)
    key, raw = db.conn.execute('SELECT review_case_key, details_json FROM repair_findings WHERE id=2').fetchone()
    db.conn.execute("INSERT INTO reconcile_review_choices(finding_id,case_id,evidence_json,category,choice) VALUES (2,?,?, 'numbering','fix')", (key, raw))
    db.conn.commit()
    assert set(_ready_selection(db)[0]) == {1, 2, 4}
    preview = ready_batch_preview(db)
    assert preview['action_count'] == 3 and len(preview['albums']) == 2
    db.add(6, '/music/mixed/6.flac', {'album_folder': '/music/mixed'}, job_id='library_reconciliation', kind='reconciliation_unresolved')
    assert _ready_selection(db)[0] == [4]
    assert ready_batch_preview(db)['signature'] != preview['signature']


def test_only_exact_audio_supported_version_metadata_is_ready():
    details = {'confidence': 'clear', 'title_difference_kind': 'version',
               'changes': {'title': {'old': 'Song', 'new': 'Song (demo)'}},
               'recording_mbid': 'demo-recording', 'current_tags': {'duration_ms': 200000},
               'expected_duration_ms': 200050,
               'acoustid': {'recordings': [{'mbid': 'demo-recording', 'score': .98}]}}
    def ready():
        return classify_case([{'finding_type': 'reconciliation_metadata', 'details': details}])['can_apply_ready']
    assert ready()
    details['acoustid']['recordings'][0]['mbid'] = 'studio-recording'
    assert not ready()
    details['acoustid']['recordings'][0]['mbid'] = 'demo-recording'
    details['expected_duration_ms'] = 240000
    assert not ready()


def test_decode_error_requires_review_for_file_edits_but_not_album_display_cleanup():
    details = {'confidence': 'clear', 'acoustid': {'status': 'fingerprint_error'},
               'changes': {'musicbrainz_release_id': {'old': 'old', 'new': 'new'}}}
    assert classify_case([{'finding_type': 'reconciliation_metadata', 'details': details}])['manual'] == 1
    details['changes'] = {'library_album_title': {'old': 'Album (pressing)', 'new': 'Album'}}
    assert classify_case([{'finding_type': 'reconciliation_metadata', 'details': details}])['ready'] == 1


def test_accepted_unchanged_review_does_not_expire_after_legacy_grace(tmp_path):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    worker = RepairWorker(database=db)
    finding = dict(job_id='library_reconciliation', finding_type='reconciliation_metadata',
                   severity='info', entity_type='track', entity_id='track', file_path='/song.flac',
                   title='Title update', description='Supported',
                   details={'album_id':'album','album_folder':'/album','fingerprint_key':'audio',
                            'changes':{'title':{'old':'Old','new':'New'}}})
    assert worker._create_finding(**finding)
    with db._get_connection() as c:
        c.execute("UPDATE repair_findings SET status='resolved', user_action='retagged', resolved_at='2000-01-01 00:00:00'")
    assert not worker._create_finding(**finding)
    assert worker._create_finding(**{**finding, 'details': {**finding['details'],'fingerprint_key':'new audio'}})


def test_verified_manual_assignment_allows_harmless_metadata_but_not_new_title_decisions():
    finding = {'finding_type': 'reconciliation_metadata', 'details': {
        'confidence': 'manual', 'acoustid': {'status': 'ok'},
        'changes': {'filename': {'old': 'song.flac', 'new': '01 - Song.flac'}},
    }}
    assert classify_case([finding])['ready'] == 1
    finding['details']['title_difference_kind'] = 'material'
    finding['details']['changes']['title'] = {'old': 'Song', 'new': 'Other song'}
    assert classify_case([finding])['manual'] == 1


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_manual_assignment_is_saved_only_for_current_release_and_file(tmp_path):
    path = tmp_path / '01 - Laid to Rest.flac'
    path.write_bytes(b'audio')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    worker = RepairWorker(database=db, transfer_folder=str(tmp_path))
    stat = path.stat()
    details = {
        'album_id': 10, 'source': 'spotify', 'release_id': 'ashes-release',
        'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
        'assignment_options': [{'key': 'id:alternate', 'title': 'Alternate'}],
        '_fix_action': 'assign:id:alternate',
    }
    result = worker._fix_reconciliation_assignment('track', '1', str(path), details)
    assert result['success'], result
    with db._get_connection() as conn:
        row = conn.execute('SELECT slot_key, release_id FROM reconcile_track_assignment').fetchone()
    assert tuple(row) == ('id:alternate', 'ashes-release')
    path.write_bytes(b'changed audio')
    assert not worker._fix_reconciliation_assignment('track', '1', str(path), details)['success']


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize('previous_annotation_scan', [False, True])
def test_explanatory_annotations_keep_saved_choice_but_new_conflict_reopens_it(tmp_path, monkeypatch, previous_annotation_scan):
    db, worker, context, paths, _ = _metadata_recommendation_album(tmp_path, monkeypatch)
    assert not LibraryReconciliationJob().scan(context).errors
    case_id = worker.get_review_cases()['items'][0]['case_id']
    item = worker.get_review_case(case_id)['items'][0]
    # A decision saved before the assignment picker gained full artist labels
    # remains bound to the same release slots after the scan is refreshed.
    current_details = item['details']
    previous_details = json.loads(json.dumps(current_details))
    for option in previous_details['assignment_options']:
        option.pop('artist', None)
    with db._get_connection() as conn:
        conn.execute('UPDATE repair_findings SET details_json=? WHERE id=?',
                     (json.dumps(previous_details), item['id']))
        conn.commit()
    item = next(row for row in worker.get_review_case(case_id)['items'] if row['id'] == item['id'])
    result = worker.apply_review_decision(case_id, item['id'], evidence_token=item['evidence_token'], action='fix')
    assert result['success'], result
    details = {**current_details, 'assignment_evidence': 'audio', 'tool_checks': [{'title': 'Additional context'}]}
    # Removing the obsolete display mode alone does not revoke an unrelated
    # metadata decision; actual changes/source/identity still must match.
    details['numbering_mode'] = 'digital'
    details['match_reasons'] = ['AcoustID recording title matches release slot']
    if previous_annotation_scan:
        with db._get_connection() as conn:
            conn.execute('UPDATE repair_findings SET details_json=? WHERE id=?',
                         (json.dumps(details), item['id']))
            conn.commit()
    def refresh():
        worker._create_finding(job_id='library_reconciliation', finding_type=item['finding_type'],
            severity='warning', entity_type=item['entity_type'], entity_id=item['entity_id'],
            file_path=item['file_path'], title=item['title'], description=item['description'], details=details)
        return next(row for row in worker.get_review_case(case_id)['items'] if row['id'] == item['id'])
    assert refresh()['details']['plan_approved']
    details['metadata_review_reason'] = 'New conflicting recording evidence needs review'
    changed = refresh()
    assert not changed['details'].get('plan_approved')
    assert changed['review_tier'] == 'manual'


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize('substantive_change', [None, 'assignment', 'target', 'numbering', 'recording'])
def test_new_display_labels_do_not_hide_changed_saved_choice_targets(substantive_change):
    from copy import deepcopy

    from core.reconciliation.choices import same_annotated_evidence
    previous = {
        'recording_mbid': 'recording-a',
        'assignment_options': [{'key': 'slot-a', 'title': 'Song', 'track_number': 1}],
        'changes': {'library_album_assignment': {'old': 'album-a', 'new': 'album-b'}},
        'db_data': {'track_number': 1},
    }
    current = deepcopy(previous)
    current['assignment_options'][0]['artist'] = 'Artist & Guest'
    current['changes']['library_album_assignment'].update(
        old_label='Separate library entry', new_label='Reviewed album')
    if substantive_change == 'assignment':
        current['assignment_options'][0]['key'] = 'slot-b'
    elif substantive_change == 'target':
        current['changes']['library_album_assignment']['new'] = 'album-c'
    elif substantive_change == 'numbering':
        current['db_data']['track_number'] = 2
    elif substantive_change == 'recording':
        current['recording_mbid'] = 'recording-b'
    assert same_annotated_evidence(previous, current) is (substantive_change is None)
    assert same_annotated_evidence(current, previous) is (substantive_change is None)


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_configured_lossy_companion_shares_assignment_without_duplicate_decision(tmp_path, monkeypatch):
    from types import SimpleNamespace
    db, worker, context, paths, _ = _metadata_recommendation_album(tmp_path, monkeypatch)
    companion = paths[0].with_suffix('.mp3')
    companion.write_bytes(paths[0].read_bytes())
    context.acoustid_client.matches[companion.name] = context.acoustid_client.matches[paths[0].name]
    context.config_manager = SimpleNamespace(get=lambda key, default=None:
        True if key == 'lossy_copy.enabled' else default)
    with db._get_connection() as conn:
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path) VALUES (3,10,1,'Laid to Rest',1,?)", (str(companion),))
        conn.commit()
    scan = LibraryReconciliationJob()
    for _ in range(2):  # The protected role is also part of the matching cache key.
        result = scan.scan(context)
        assert not result.errors
        case_id = worker.get_review_cases()['items'][0]['case_id']
        case = worker.get_review_case(case_id)
        assert {item['finding_type'] for item in case['items']} == {'reconciliation_metadata'}
        plan = case['album_plan']
        assert plan['matched'] == plan['release_track_count'] == 2
        assert len(plan['assignments']) == 3
        row = next(row for row in plan['assignments'] if row['file_path'] == str(companion))
        assert row['companion_of'] == str(paths[0])


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_candidate_rank_does_not_reward_previously_flattened_numbering():
    files = [
        {'tags': {'track_number': number, 'disc_number': 1}}
        for number in range(1, 5)
    ]
    pairs = [{'file_index': index, 'slot_index': index, 'confidence': 'clear'}
             for index in range(4)]
    plan = {'pairs': pairs, 'extra_files': [], 'missing_slots': []}
    continuous_slots = [
        {'track_number': number, 'disc_number': 1} for number in range(1, 5)
    ]
    cd_slots = [
        {'track_number': 1, 'disc_number': 1},
        {'track_number': 2, 'disc_number': 1},
        {'track_number': 1, 'disc_number': 2},
        {'track_number': 2, 'disc_number': 2},
    ]
    continuous = (4, 4, 0, 'musicbrainz', 'digital', continuous_slots, plan)
    compact_disc = (4, 4, 0, 'musicbrainz', 'cd', cd_slots, plan)

    assert _candidate_rank(files, continuous) == _candidate_rank(files, compact_disc)
    assert _edition_preference({'format': 'Digital Media'}) > _edition_preference({'format': 'Cassette'})


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_retired_numbering_setting_is_hidden_and_ignored():
    from types import SimpleNamespace
    worker = RepairWorker.__new__(RepairWorker)
    worker._jobs = {'library_reconciliation': LibraryReconciliationJob()}
    worker._config_manager = SimpleNamespace(get=lambda _key, default=None: {
        'settings': {'numbering_mode': 'digital', 'min_acoustid_score': .9}})
    config = worker.get_job_config('library_reconciliation')
    assert 'numbering_mode' not in config['settings']
    assert config['settings']['min_acoustid_score'] == .9


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_kept_numbering_survives_other_metadata_apply_and_can_be_reopened(tmp_path, monkeypatch):
    from mutagen.flac import FLAC

    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    root.mkdir()
    path = root / '09 - Laid to Rest.flac'
    _minimal_flac(path, {'title': 'Laid to Rest', 'artist': 'Lamb of God',
                         'album': 'Ashes of the Wake', 'tracknumber': '9',
                         'musicbrainz_albumid': 'old-pressing'})
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    db.set_album_canonical(10, 'musicbrainz', 'ashes-mb-release', 0.9, locked=True)
    worker = RepairWorker(database=db)
    client = _Acoustid({path.name: {'mbid': 'laid-id', 'title': 'Laid to Rest',
                                   'artist': 'Lamb of God', 'score': 0.99}})
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda _self: client))
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *_args: [
        {'id': 'laid-id', 'name': 'Laid to Rest', 'artists': [{'name': 'Lamb of God'}],
         'track_number': 1, 'disc_number': 1, 'duration_ms': 200000}])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda *_args: {'name': 'Ashes of the Wake'})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)
    LibraryReconciliationJob().scan(context)
    key = worker.get_review_cases()['items'][0]['case_id']
    finding = worker.get_review_case(key)['items'][0]
    assert finding['category'] == 'numbering'
    assert worker.apply_review_decision_group(key, 'numbering', action='dismiss')['success']
    assert worker.fix_finding(finding['id'], review_approved=True)['success']
    assert FLAC(str(path))['tracknumber'][0] == '9'
    assert FLAC(str(path))['musicbrainz_albumid'][0] == 'ashes-mb-release'
    LibraryReconciliationJob().scan(context)
    case = worker.get_review_case(key)
    assert not any('track_number' in item['details'].get('changes', {}) for item in case['items'])
    assert worker.reopen_review_assignment(key, finding['id'])['success']
    LibraryReconciliationJob().scan(context)
    case = worker.get_review_case(key)
    assert any('track_number' in item['details'].get('changes', {}) for item in case['items'])


def test_complete_choice_during_other_album_apply_is_immediate_and_does_not_scan(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'partial')
    case = scan_case(album)
    item = next(row for row in case['items'] if row['finding_type'] == 'reconciliation_intent')
    monkeypatch.setattr(album.worker, '_schedule_review_refresh', lambda *a, **k: (_ for _ in ()).throw(AssertionError('No scan needed')))
    entered, release = threading.Event(), threading.Event()
    def applying_other_album():
        with mutation_lock.for_album('another-album'):
            entered.set()
            release.wait(5)
    thread = threading.Thread(target=applying_other_album)
    thread.start()
    assert entered.wait(1)
    try:
        result = album.worker.apply_review_decision(case['case_id'], item['id'], evidence_token=item['evidence_token'], action='fix', fix_action='complete')
        assert result['success'], result
        after = album.worker.get_review_case(case['case_id'])
        assert after['album_plan']['intent_scope'] == 'complete'
        assert len([item for item in after['items'] if item['finding_type'] == 'reconciliation_missing']) == 2
        assert not after['plan']['can_apply_ready']
    finally:
        release.set()
        thread.join()


def test_leave_album_closes_review_without_touching_files_or_membership(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    originals = [path.read_bytes() for path in album.paths]
    assert not album.worker.leave_review_album(case['case_id'], 'stale')['success']
    result = album.worker.leave_review_album(case['case_id'], case['plan_signature'])
    assert result['success'], result
    assert album.worker.get_review_case(case['case_id'])['status'] == 'completed'
    assert [path.read_bytes() for path in album.paths] == originals
    scan_case(album)
    assert not list_cases(album.db)['items']


def test_audio_copy_choice_is_scoped_and_automatic_video_exclusion_remains(tmp_path, monkeypatch):
    from core.reconciliation.audio_copy import load_choices, save_choice
    db = SimpleNamespace(_get_connection=lambda: sqlite3.connect(str(tmp_path / 'video.db')))
    save_choice(db, 'album', 'musicbrainz', 'video-release', True)
    choices = load_choices(db)
    assert choices == {'album': ('musicbrainz', 'video-release')}
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *a, **kw: [
        {'name': 'Present Tense', 'track_number': 1, 'disc_number': 1, 'video': True}])
    files = [{'path': 'song.mp3', 'tags': {'title': 'Present Tense'}, 'acoustid': {'recordings': []}}]
    assert reconciliation_selection._score_release_candidates(files, [('musicbrainz', 'video-release')], {}) == []
    scored = reconciliation_selection._score_release_candidates(files, [('musicbrainz', 'video-release')], {}, audio_copy_release=choices['album'])
    assert len(scored) == 1
    assert scored[0].slots[0]['audio_copy_of_video']
    assert reconciliation_selection._score_release_candidates(files, [('musicbrainz', 'another-release')], {}, audio_copy_release=choices['album']) == []
    save_choice(db, 'album', 'musicbrainz', 'audio-release', False)
    assert load_choices(db) == {}


def test_new_disc_evidence_retains_choices_only_when_the_proposed_effects_match():
    from core.reconciliation.choices import same_annotated_evidence
    original = {'current_tags': {'title': 'Song'}, 'changes': {'title': {'old': 'Song', 'new': 'Song!'}}}
    enriched = {**original, 'current_tags': {'title': 'Song', 'total_discs': 2}}
    assert same_annotated_evidence(original, enriched)
    corrected = {**enriched, 'changes': {**original['changes'], 'total_discs': {'old': 2, 'new': 1}}}
    assert not same_annotated_evidence(original, corrected)


def test_release_candidate_can_be_pinned_without_release_finding():
    class AlbumDatabase(_Database):
        selected = None

        def set_album_canonical(self, album_id, source, release_id, *, score, locked, connection=None):
            self.selected = (album_id, source, release_id, locked)
            return True

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = AlbumDatabase()
    worker._review_apply_lock = threading.RLock()
    worker.get_review_case = lambda *_args, **_kwargs: {
        'album_id': 7, 'folder': '/music/album',
        'album_plan': {
            'source': 'itunes', 'release_id': 'old',
            'candidates': [{'source': 'musicbrainz', 'release_id': 'new',
                            'clear': 10, 'matched': 10}],
        },
    }
    worker._schedule_album_refresh = lambda *_args, **_kwargs: True

    result = worker.pin_review_release(
        'case', source='musicbrainz', release_id='new',
        expected_source='itunes', expected_release_id='old')

    assert result['success'] and result['refresh_queued']
    assert worker.db.selected == (7, 'musicbrainz', 'new', True)
    from core.reconciliation.choices import load_release_choices
    assert load_release_choices(worker.db) == {'7': ('musicbrainz', 'new')}
    stale = worker.pin_review_release(
        'case', source='musicbrainz', release_id='new',
        expected_source='itunes', expected_release_id='stale')
    assert stale['stale']


def test_manual_musicbrainz_release_is_previewed_before_pinning(monkeypatch, tmp_path):
    release_id = '301434dc-e59a-467d-b950-abfa9a2ad32e'
    class AlbumDatabase(_Database):
        selected = None
        def set_album_canonical(self, album_id, source, selected_id, *, score, locked, connection=None):
            self.selected = (album_id, source, selected_id, locked)
            return True
    class MusicBrainz:
        def get_release(self, mbid, includes):
            assert mbid == release_id
            return {'id': release_id, 'title': 'The Essential *NSYNC',
                    'media': [{'tracks': [{'title': 'Song one'}, {'title': 'Song two'}]}]}
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = AlbumDatabase()
    worker._mb_client = MusicBrainz()
    worker._review_apply_lock = threading.RLock()
    worker.get_review_case = lambda *_args, **_kwargs: {
        'album_id': 7, 'folder': '/music/album',
        'album_plan': {'source': 'itunes', 'release_id': 'old', 'candidates': []},
    }
    worker._schedule_album_refresh = lambda *_args, **_kwargs: True
    args = {'case_id': 'case', 'source': 'musicbrainz', 'release_id': release_id,
            'expected_source': 'itunes', 'expected_release_id': 'old'}
    from core.reconciliation.evidence import ReconciliationEvidence
    path = tmp_path / 'Song one.flac'
    path.write_bytes(b'audio')
    worker.transfer_folder = str(tmp_path)
    worker._config_manager = None
    worker._acoustid_client = None
    case = worker.get_review_case('case')
    case['album_plan']['assignments'] = [{'file_path': str(path)}]
    worker.get_review_case = lambda *_args, **_kwargs: case
    monkeypatch.setattr(ReconciliationEvidence, '__init__', lambda *args: None)
    monkeypatch.setattr(ReconciliationEvidence, 'close', lambda *args: None)
    monkeypatch.setattr(ReconciliationEvidence, 'read_tags', lambda *args: {'title': 'Song one', 'duration_ms': 200000})
    monkeypatch.setattr(ReconciliationEvidence, 'identify', lambda *args: {'recordings': []})
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *args, **kwargs: [
        {'name': 'Song one', 'track_number': 1, 'duration_ms': 200000},
        {'name': 'Song two', 'track_number': 2, 'duration_ms': 210000},
    ])
    preview = worker.pin_review_release(**args, preview=True)
    assert preview['success'] and preview['candidate']['total_slots'] == 2
    assert preview['candidate']['matched'] == 1
    assert preview['candidate']['missing_tracks'] == ['Song two']
    assert preview['candidate']['pairs'][0]['slot_title'] == 'Song one'
    assert worker.db.selected is None
    selected = worker.pin_review_release(**args, manual=True)
    assert selected['success'] and worker.db.selected == (7, 'musicbrainz', release_id, True)


def test_redundant_disc_markers_on_complete_continuous_sequence_are_ready():
    details = {'numbering_mode': 'digital', 'confidence': 'clear',
               'release_disc_count': 1, 'release_track_count': 19, 'local_file_count': 19,
               'changes': {'disc_number': {'old': '2', 'new': '1'},
                           'library_disc_number': {'old': '2', 'new': '1'}}}
    item = {'finding_type': 'reconciliation_metadata', 'details': details}
    assert classify_case([item])['can_apply_ready']
    details['changes']['track_number'] = {'old': '1', 'new': '11'}
    assert not classify_case([item])['can_apply_ready']
    del details['changes']['track_number']
    details['local_file_count'] = 10
    assert not classify_case([item])['can_apply_ready']
    details['local_file_count'] = 19
    details['numbering_mode'] = 'release'
    assert classify_case([item])['can_apply_ready']
    details['release_disc_count'] = 2
    assert not classify_case([item])['can_apply_ready']


def test_unknown_release_has_local_files_and_can_be_left_as_is(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *a, **k: [])
    case = scan_case(album)
    assert case['album_plan']['state'] == 'unidentified'
    assert len(case['album_plan']['assignments']) == len(album.paths)
    item = case['items'][0]
    assert item['details']['checked_files'][0]['file_path']
    assert album.worker.apply_review_decision(case['case_id'], item['id'], evidence_token=item['evidence_token'], action='dismiss')['success']
    assert album.worker.get_review_case(case['case_id'])['plan']['can_apply_ready']
