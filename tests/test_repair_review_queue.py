"""Queue cases use physical/album identity and never hide a failed action."""

from __future__ import annotations

import json
import sqlite3
import threading

from flask import Flask

from core.repair_review_identity import missing_slot_key, review_case_key
from core.repair_review_queue import classify_case, get_case, list_cases, safe_finding_ids
from core.library.reconciliation_store import ensure_schema, persist_album_plan, retire_unseen_plans
from core.repair_worker import RepairWorker, _same_reviewed_file
from database.music_database import MusicDatabase


def test_reviewed_file_accepts_mount_alias_but_rejects_different_inode(tmp_path):
    scanned = tmp_path / 'scan' / 'track.flac'
    scanned.parent.mkdir()
    scanned.write_bytes(b'audio')
    alias = tmp_path / 'library' / 'track.flac'
    alias.parent.mkdir()
    alias.hardlink_to(scanned)
    other = tmp_path / 'other.flac'
    other.write_bytes(b'audio')
    assert _same_reviewed_file(str(scanned), str(alias))
    assert not _same_reviewed_file(str(scanned), str(other))


def test_existing_artist_credit_replacement_is_not_bulk_ready():
    finding = {'finding_type': 'reconciliation_metadata', 'details': {
        'confidence': 'clear',
        'changes': {'artist': {'old': 'Benny Andersson, Björn Ulvaeus, Lily James',
                               'new': 'Cast of Mamma Mia! The Movie'}},
    }}
    assert classify_case([finding])['manual'] == 1


def test_metadata_filter_keeps_credit_replacement_in_needs_input():
    db = _Database()
    db.add(1, '/music/album/01.flac', {
        'album_id': 1, 'album_title': 'Soundtrack',
        'changes': {'artist': {'old': 'Three performers', 'new': 'Cast'}},
        'confidence': 'clear',
    }, job_id='library_reconciliation', kind='reconciliation_metadata')
    assert list_cases(db, job_id='library_reconciliation', category='metadata',
                      risk='manual')['total'] == 1
    assert list_cases(db, job_id='library_reconciliation', category='metadata',
                      risk='ready')['total'] == 0
    assert safe_finding_ids(db)[0] == []


def test_queue_modes_partition_albums_with_mixed_decisions():
    db = _Database()
    db.add(1, '/music/ready/01.flac', {
        'album_id': 1, 'album_title': 'Ready',
        'changes': {'title': {'old': 'Typo', 'new': 'Title'}},
        'confidence': 'clear',
    }, job_id='library_reconciliation', kind='reconciliation_metadata')
    db.add(2, '/music/manual/01.flac', {
        'album_id': 2, 'album_title': 'Manual',
    }, job_id='library_reconciliation', kind='reconciliation_unresolved')
    db.add(3, '/music/files/01.flac', {
        'album_id': 3, 'album_title': 'Files',
    }, job_id='library_reconciliation', kind='reconciliation_extra')
    db.add(4, '/music/files/02.flac', {
        'album_id': 3, 'album_title': 'Files',
    }, job_id='library_reconciliation', kind='reconciliation_unresolved')

    totals = {mode: list_cases(db, job_id='library_reconciliation', risk=mode)['total']
              for mode in ('', 'ready', 'manual', 'destructive')}
    assert totals == {'': 3, 'ready': 1, 'manual': 1, 'destructive': 1}


def test_release_candidate_can_be_pinned_without_release_finding():
    class AlbumDatabase:
        selected = None

        def set_album_canonical(self, album_id, source, release_id, *, score, locked):
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
    stale = worker.pin_review_release(
        'case', source='musicbrainz', release_id='new',
        expected_source='itunes', expected_release_id='stale')
    assert stale['stale']


def test_manual_musicbrainz_release_is_previewed_before_pinning(monkeypatch, tmp_path):
    release_id = '301434dc-e59a-467d-b950-abfa9a2ad32e'
    class AlbumDatabase:
        selected = None
        def set_album_canonical(self, album_id, source, selected_id, *, score, locked):
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
    from core.library.reconciliation_evidence import ReconciliationEvidence
    from core.repair_jobs import library_reconciliation as job
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
    monkeypatch.setattr(job, 'get_album_tracks_for_source', lambda *args, **kwargs: [
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


def test_complete_album_can_request_all_missing_slots_after_scope_choice():
    worker = RepairWorker.__new__(RepairWorker)
    case = {
        'folder': '/music/album',
        'items': [
            {'id': 1, 'finding_type': 'reconciliation_missing'},
            {'id': 2, 'finding_type': 'reconciliation_missing'},
            {'id': 3, 'finding_type': 'reconciliation_metadata'},
        ],
    }
    worker.get_review_case = lambda *_args, **_kwargs: case
    captured = {}
    worker.start_bulk_fix = lambda **kwargs: captured.update(kwargs) or {'started': True}

    assert worker.start_review_missing_tracks('album-case')['started']
    assert captured['finding_ids'] == [1, 2]
    assert not captured.get('review_case_folders')
    case['items'][1]['details'] = {'fulfillment_state': 'wishlisted'}
    assert worker.start_review_missing_tracks('album-case')['started']
    assert captured['finding_ids'] == [1]
    case['items'].append({'id': 4, 'finding_type': 'reconciliation_intent'})
    assert not worker.start_review_missing_tracks('album-case')['started']


def test_reanalyze_review_case_targets_only_its_folder():
    worker = RepairWorker.__new__(RepairWorker)
    case = {'folder': '/music/Ghost/Prequelle', 'items': []}
    worker.get_review_case = lambda *_args, **_kwargs: case
    calls = []
    worker._schedule_review_refresh = lambda *args, **kwargs: calls.append((args, kwargs)) or True

    assert worker.reanalyze_review_case('prequelle-case')
    assert calls == [(('prequelle-case', '/music/Ghost/Prequelle', case), {'scope': 'discovery'})]
    worker.get_review_case = lambda *_args, **_kwargs: None
    assert not worker.reanalyze_review_case('stale-case')


def test_bulk_selection_admits_only_explicitly_reviewed_missing_slot_ids():
    for reviewed, expected in ((False, []), (True, [1])):
        db = _Database()
        db.add(1, None, {'album_id': 10, 'album_title': 'Album'},
               job_id='library_reconciliation', kind='reconciliation_missing')
        worker = RepairWorker.__new__(RepairWorker)
        worker.db = db
        assert worker._pending_fixable_ids(
            finding_ids=[1], allow_review_ids=reviewed) == expected


def test_album_title_cleanup_keeps_edition_and_drops_pressing_plant():
    from core.repair_jobs.library_reconciliation import _clean_album_display_title

    assert _clean_album_display_title(
        'The Fame Monster (deluxe, EDC Blackburn Ltd.)') == 'The Fame Monster (deluxe)'
    assert _clean_album_display_title('Greatest Hits (Deluxe)') == 'Greatest Hits (Deluxe)'


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


def test_queue_artwork_uses_album_identity_not_shared_title():
    db = _Database()
    db.conn.execute('CREATE TABLE albums (id TEXT PRIMARY KEY, title TEXT, thumb_url TEXT)')
    db.conn.executemany('INSERT INTO albums VALUES (?, ?, ?)', [
        ('10', 'Greatest Hits', '/api/image-cache/album-ten'),
        ('11', 'Greatest Hits', '/api/image-cache/album-eleven'),
    ])
    for album_id in (10, 11):
        db.add(album_id, f'/music/{album_id}/01.flac', {
            'album_id': album_id, 'album_title': 'Greatest Hits',
            'artist_name': 'Various Artists',
        }, job_id='library_reconciliation', kind='reconciliation_metadata')
    cases = list_cases(db, job_id='library_reconciliation')
    assert len(cases['items']) == 2
    for summary in cases['items']:
        detail = get_case(db, summary['case_id'], job_id='library_reconciliation')
        album_id = str(detail['album_id'])
        expected = f'/api/image-cache/album-{"ten" if album_id == "10" else "eleven"}'
        assert summary['album_thumb_url'] == expected
        assert detail['album_thumb_url'] == expected


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


def test_library_album_title_change_is_described_as_one_album_change():
    plan = classify_case([{
        'id': 1,
        'finding_type': 'reconciliation_metadata',
        'details': {
            'changes': {
                'library_album_title': {
                    'old': 'Rio (Columbia House)', 'new': 'Rio',
                },
            },
            'confidence': 'clear',
        },
    }])

    assert plan['decisions'][0]['label'] == 'Update library album title'
    assert plan['decisions'][0]['summary'] == (
        'Rename the library album: Rio (Columbia House) → Rio.')


def test_album_intent_summary_states_the_album_level_question():
    plan = classify_case([{
        'id': 1, 'finding_type': 'reconciliation_intent', 'details': {},
    }])

    assert plan['decisions'][0]['label'] == 'Album completeness'
    assert plan['decisions'][0]['summary'] == (
        'Choose whether this album should be complete.')


def test_featured_artist_repairs_are_described_as_one_album_wide_change():
    items = [{
        'id': finding_id,
        'finding_type': 'reconciliation_metadata',
        'details': {
            'confidence': 'clear',
            'changes': {
                'track_artist': {'old': 'Zero 7', 'new': f'Zero 7; Guest {finding_id}'},
                'library_track_artist': {'old': '', 'new': f'Zero 7; Guest {finding_id}'},
            },
        },
    } for finding_id in range(1, 4)]

    decision = classify_case(items)['decisions'][0]

    assert decision['label'] == 'Featured artist credits'
    assert decision['summary'] == (
        'Adding missing featured artists to metadata on 3 tracks.')


def test_album_completeness_filter_includes_intent_and_known_missing_tracks():
    db = _Database()
    details = {'album_id': 10, 'album_title': 'Album'}
    db.add(1, None, details, job_id='library_reconciliation',
           kind='reconciliation_intent')
    db.add(2, None, {**details, 'album_id': 11}, job_id='library_reconciliation',
           kind='reconciliation_missing')

    filtered = list_cases(
        db, job_id='library_reconciliation', category='album_completeness')

    assert filtered['total'] == 2


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


def test_wrong_audio_is_not_reconnected_to_missing_slot_by_title():
    db = _Database()
    details = {'album_id': 10, 'profile_id': 1, 'album_title': 'Collection'}
    extra_path = '/music/Album/26 - Who Wants to Live Forever.flac'
    db.add(1, extra_path, {**details, 'audio_conflict': 'different_recording'},
           job_id='library_reconciliation', kind='reconciliation_unresolved')
    case_id = persist_album_plan(db, {**details, 'evidence_signature': 'evidence'}, {
        'source': 'musicbrainz', 'release_id': 'release-id',
        'assignments': [
            {'status': 'missing', 'slot_title': 'Who Wants to Live Forever',
             'slot_disc_number': 2, 'slot_track_number': 9},
            {'status': 'extra', 'file_path': extra_path,
             'file': '26 - Who Wants to Live Forever.flac',
             'current_title': 'Who Wants to Live Forever',
             'audio_conflict': 'different_recording'},
        ],
    })

    case = get_case(db, case_id, job_id='library_reconciliation')
    missing, extra = case['album_plan']['assignments']

    assert 'relationship' not in missing
    assert 'relationship' not in extra
    assert extra['finding_id'] == 1


def test_unassigned_album_file_can_use_the_existing_quarantine_guard():
    worker = RepairWorker.__new__(RepairWorker)
    captured = []
    worker._fix_reconciliation_extra = lambda *args: captured.append(args) or {'success': True}
    details = {'_fix_action': 'quarantine', 'track_id': 7, 'album_id': 10}

    assert worker._fix_reconciliation_assignment('track', '7', '/music/extra.flac', details)['success']
    assert captured == [('track', '7', '/music/extra.flac', details)]
    assert not worker._fix_reconciliation_assignment('track', '7', '/music/extra.flac',
                                                     {**details, 'version_conflict': True})['success']


def test_actionable_wrong_audio_remains_an_audio_conflict():
    item = {
        'id': 1,
        'finding_type': 'reconciliation_extra',
        'details': {'audio_conflict': 'different_recording'},
    }

    plan = classify_case([item])

    assert item['category'] == 'audio_conflicts'
    assert item['review_tier'] == 'destructive'
    assert plan['decisions'] == [{
        'id': 'audio_conflicts',
        'category': 'audio_conflicts',
        'label': 'Wrong audio',
        'risk': 'destructive',
        'summary': '1 local file contains different audio than the expected release track.',
        'finding_ids': [1],
        'action_count': 1,
    }]


def test_wrong_audio_case_is_found_by_audio_filter_and_not_extra_filter():
    db = _Database()
    db.add(1, '/music/Album/01.flac', {
        'album_id': 10, 'album_title': 'Album',
        'audio_conflict': 'different_recording',
    }, job_id='library_reconciliation', kind='reconciliation_extra')

    assert list_cases(db, job_id='library_reconciliation',
                      category='audio_conflicts')['total'] == 1
    assert list_cases(db, job_id='library_reconciliation',
                      category='extra_files')['total'] == 0


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


def test_recording_id_only_album_says_what_ready_changes_do():
    rows = [{
        'finding_type': 'reconciliation_metadata',
        'details': {
            'confidence': 'clear',
            'changes': {
                'musicbrainz_recording_id': {'old': '', 'new': f'mbid-{index}'},
                'library_recording_mbid': {'old': '', 'new': f'mbid-{index}'},
            },
        },
    } for index in range(1, 4)]

    decision = classify_case(rows)['decisions'][0]

    assert decision['label'] == 'Link MusicBrainz recording IDs'
    assert decision['summary'] == (
        'Link 3 tracks to their confirmed MusicBrainz recordings.')


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
    refreshes = []
    worker._schedule_album_refresh = lambda key, folder: (
        refreshes.append((key, folder)) or True)

    result = worker.apply_review_decision_group(case_id, 'title_conventions', action='fix')

    assert result == {'success': True, 'applied': 2, 'total': 2,
                      'refresh_queued': True}
    assert applied == [1, 2]
    assert refreshes == [(case_id, '/music/Album')]


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


def test_album_stays_in_review_queue_during_scoped_reanalysis(tmp_path):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._album_refresh_lock = threading.Lock()
    snapshot = {
        'case_id': 'album:1:10', 'album': 'The Wall', 'artist': 'Pink Floyd',
        'album_thumb_url': None, 'items': [], 'plan': {'status': 'needs_review'},
    }
    worker._album_refreshes = {
        'album:1:10': {'state': 'running', 'snapshot': snapshot}
    }

    queue = worker.get_review_cases(job_id='library_reconciliation')
    case = worker.get_review_case('album:1:10')

    assert queue['items'][0]['refreshing'] is True
    assert queue['items'][0]['album'] == 'The Wall'
    assert case['refresh']['state'] == 'running'


def test_known_completed_case_is_explicit_but_unknown_case_is_missing():
    db = _Database()
    db.add(1, '/music/Album/01.flac', {'album_id': 10}, status='resolved',
           job_id='library_reconciliation', kind='reconciliation_metadata')
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    case = worker.get_review_case('album:1:10', job_id='library_reconciliation')
    assert case['status'] == 'completed' and case['items'] == []
    assert worker.get_review_case('album:1:404') is None
    db.add(2, '/music/Album/02.flac', {'album_id': 10},
           job_id='library_reconciliation', kind='reconciliation_unresolved')
    assert worker.get_review_case('album:1:10')['status'] != 'completed'


def test_requested_missing_track_stays_pending_without_reanalysis():
    db = _Database()
    details = {'album_id': 10, 'album_folder': '/music/Album',
               'missing_tracks': [{'source_track_id': 'recording', 'name': 'Song',
                                   'disc_number': 1, 'track_number': 2}]}
    db.add(1, None, details, job_id='library_reconciliation', kind='reconciliation_missing')
    case_id = list_cases(db)['items'][0]['case_id']
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._config_manager = None
    worker._review_apply_lock = threading.Lock()
    worker._execute_fix = lambda *args: {'success': True, 'action': 'wishlisted'}
    worker._schedule_review_refresh = lambda *args: (_ for _ in ()).throw(AssertionError('No rescan needed'))
    case = worker.get_review_case(case_id)
    result = worker.apply_review_decision(case_id, 1, evidence_token=case['items'][0]['evidence_token'], action='fix')
    assert result['success'] and not result.get('refresh_queued')
    updated = worker.get_review_case(case_id)
    assert updated['status'] != 'completed'
    assert updated['items'][0]['details']['fulfillment_state'] == 'wishlisted'
    assert updated['decisions'][0]['category'] == 'pending_fulfillment'


def test_stale_duplicate_library_id_refreshes_without_removing_a_file(tmp_path):
    db = _Database()
    db.conn.execute('CREATE TABLE tracks (id TEXT, file_path TEXT)')
    path = tmp_path / 'Song.flac'
    path.write_bytes(b'unchanged audio')
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker.transfer_folder = str(tmp_path)
    worker._config_manager = None
    worker._reconciliation_release_guard = lambda details: None
    worker._fix_duplicates = lambda *args: (_ for _ in ()).throw(AssertionError('Must not remove stale copies'))
    result = worker._fix_reconciliation_duplicate('track', 'old', str(path), {
        '_fix_action': 'keep:old', 'duplicate_copies': [
            {'track_id': 'old', 'file_path': str(path)},
            {'track_id': 'other-old', 'file_path': str(path)},
        ]})
    assert not result['success'] and result['refresh_required']
    assert path.read_bytes() == b'unchanged audio'


def test_summary_matches_paginated_modes_without_loading_plans(monkeypatch):
    from core.repair_review_queue import queue_summary
    import core.repair_review_queue as queue
    db = _Database()
    for index, (kind, changes, confidence) in enumerate([
        ('reconciliation_metadata', {}, 'clear'),
        ('reconciliation_metadata', {'track_number': {'old': 2, 'new': 1}}, 'clear'),
        ('reconciliation_metadata', {'artist': {'old': 'Old', 'new': 'New'}}, 'clear'),
        ('reconciliation_release', {}, 'clear'),
        ('reconciliation_duplicate', {}, 'clear'),
        ('reconciliation_missing', {}, 'clear'),
        ('reconciliation_metadata', {}, 'ambiguous'),
    ], 1):
        db.add(index, f'/music/{index}/song.flac', {
            'album_id': index, 'album_title': f'Album {index}',
            'changes': changes, 'confidence': confidence,
        }, job_id='library_reconciliation', kind=kind)
    for category in ('', 'metadata', 'numbering', 'album_completeness', 'duplicates'):
        for query in ('', 'Album 1', 'no matching album'):
            expected = {label: list_cases(db, job_id='library_reconciliation',
                                         category=category, q=query, risk=risk)['total']
                        for label, risk in [('all', ''), ('review', 'manual'),
                                            ('ready', 'ready'), ('files', 'destructive')]}
            expected['ready_total'] = list_cases(db, job_id='library_reconciliation')['ready_total']
            with monkeypatch.context() as patch:
                patch.setattr(queue, 'classify_case', lambda *a: (_ for _ in ()).throw(
                    AssertionError('Summary must not hydrate page plans')))
                assert queue_summary(db, job_id='library_reconciliation',
                                     category=category, q=query) == expected
            db.conn.rollback()  # test connection deliberately ignores close()


def test_refresh_overlay_counts_existing_case_once_across_pages():
    db = _Database()
    for finding_id in (1, 2):
        db.add(finding_id, f'/music/{finding_id}/song.flac', {
            'album_id': finding_id, 'album_title': f'Album {finding_id}',
            'changes': {}, 'confidence': 'clear',
        }, job_id='library_reconciliation', kind='reconciliation_metadata')
    cases = list_cases(db, job_id='library_reconciliation')['items']
    key = cases[1]['case_id']
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._album_refresh_lock = threading.Lock()
    worker._album_refreshes = {key: {'state': 'running', 'snapshot': get_case(db, key)}}
    page = worker.get_review_cases(limit=1, job_id='library_reconciliation')
    assert len(page['items']) == 2  # Keep the active album visible while refreshing.
    assert page['total'] == 2  # It is already counted on a later page.
    summary = worker.get_review_summary(job_id='library_reconciliation')
    assert summary['all'] == summary['ready'] == 2
    db.conn.rollback()
    db.conn.execute('UPDATE repair_findings SET status = ? WHERE review_case_key = ?',
                    ('resolved', key))
    db.conn.commit()
    # The snapshot temporarily fills the gap after apply retires the findings.
    assert worker.get_review_summary(job_id='library_reconciliation')['all'] == 2
    db.conn.rollback()
