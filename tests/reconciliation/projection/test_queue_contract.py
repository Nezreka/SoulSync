"""projection / queue contract regression contracts."""
from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from core.reconciliation import search as search
from core.reconciliation.display import (
    _release_display_metadata,
)
from core.reconciliation.queue import (
    classify_case,
    get_case,
    list_cases,
)
from core.reconciliation.store import (
    persist_album_plan,
)
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase
from tests.reconciliation.support.acceptance import build_album, scan_case
from tests.reconciliation.support.queue import _Database


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


def test_album_intent_summary_states_the_album_level_question():
    plan = classify_case([{
        'id': 1, 'finding_type': 'reconciliation_intent', 'details': {},
    }])

    assert plan['decisions'][0]['label'] == 'Album completeness'
    assert plan['decisions'][0]['summary'] == (
        'Choose whether this album should be complete.')


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


def test_recording_id_summary_distinguishes_replace_remove_and_mixed_without_double_counting():
    def row(old, new, library_old=None):
        return {'finding_type': 'reconciliation_metadata', 'details': {
            'changes': {'musicbrainz_recording_id': {'old': old, 'new': new},
                        'library_recording_mbid': {
                            'old': old if library_old is None else library_old, 'new': new}},
        }}
    for old, new, verb in [('old', 'new', 'Replace'), ('old', '', 'Remove')]:
        decision = classify_case([row(old, new)])['decisions'][0]
        assert decision['label'] == f'{verb} MusicBrainz recording IDs'
        assert decision['summary'] == f'{verb} on 1 track.'
    decision = classify_case([
        row('', 'new'), row('old', 'new'), row('old', ''), row('', 'new', 'old'),
    ])['decisions'][0]
    assert decision['label'] == 'Review recording ID changes'
    assert decision['summary'] == (
        'Add on 1 track; Replace on 1 track; Remove on 1 track; Mixed edits on 1 track.')


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


def test_summary_matches_paginated_modes_without_loading_plans(monkeypatch):
    import core.reconciliation.queue as queue
    from core.reconciliation.queue import queue_summary
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
                                            ('ready', 'ready'), ('files', 'destructive'), ('waiting', 'waiting')]}
            expected['ready_total'] = list_cases(db, job_id='library_reconciliation')['ready_total']
            expected['ready_albums_total'] = list_cases(db, job_id='library_reconciliation', risk='ready')['total']
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


def test_reopen_assignment_is_case_scoped_and_clears_only_its_saved_choice(tmp_path):
    db = _Database()
    file = tmp_path / 'track.flac'
    file.write_bytes(b'unchanged audio')
    details = {'album_id': 10, 'profile_id': 1, 'album_title': 'Album',
               'album_folder': str(tmp_path), 'source': 'musicbrainz', 'release_id': 'edition'}
    db.add(1, str(file), details, status='dismissed',
           job_id='library_reconciliation', kind='reconciliation_unresolved')
    db.conn.execute('''CREATE TABLE reconcile_track_assignment
        (album_id TEXT, source TEXT, release_id TEXT, file_path TEXT, slot_key TEXT)''')
    db.conn.executemany('INSERT INTO reconcile_track_assignment VALUES (?, ?, ?, ?, ?)', [
        ('10', 'musicbrainz', 'edition', str(file), 'one'),
        ('10', 'musicbrainz', 'edition', '/different.flac', 'two'),
    ])
    case_id = db.conn.execute('SELECT review_case_key FROM repair_findings WHERE id=1').fetchone()[0]
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._review_apply_lock = threading.RLock()
    refreshed = []
    worker._schedule_album_refresh = lambda *args, **kwargs: refreshed.append(args) or True
    db.conn.commit()

    assert worker.reopen_review_assignment('another-case', 1)['stale']
    assert not refreshed
    result = worker.reopen_review_assignment(case_id, 1)
    assert result['success'] and result['refresh_queued']
    assert db.conn.execute('SELECT status FROM repair_findings WHERE id=1').fetchone()[0] == 'pending'
    assert db.conn.execute('SELECT file_path FROM reconcile_track_assignment').fetchall() == [('/different.flac',)]
    assert file.read_bytes() == b'unchanged audio'


def test_verified_case_redirect_follows_new_case_without_title_guessing():
    from core.reconciliation.redirects import record_relocation, resolve_case
    db = _Database()
    old = persist_album_plan(db, {'album_id': 10, 'album_folder': '/music/old'}, {'assignments': []})
    new = persist_album_plan(db, {'album_id': 10, 'album_folder': '/music/new'}, {'assignments': []})
    record_relocation(db, '/music/old', new)
    assert resolve_case(db, old) == new
    assert resolve_case(db, 'unrelated-case') == 'unrelated-case'


def test_compact_queue_policy_agrees_with_full_execution_evidence():
    from core.reconciliation.policy import effective_case_modes
    db = _Database()
    db.add(1, '/music/album/one.flac', {'confidence': 'clear', 'title_difference_kind': 'version',
           'recording_mbid': 'demo', 'expected_title': 'Song (demo)', 'current_tags': {'duration_ms': 100000},
           'expected_duration_ms': 100000, 'acoustid': {'status': 'ok', 'recordings': [{'mbid': 'demo','title': 'Song','score': 1}]},
           'changes': {'title': {'old': 'Song','new': 'Song (demo)'}},
           'assignment_options': [{'irrelevant': 'large evidence'}] * 100},
           job_id='library_reconciliation', kind='reconciliation_metadata')
    for index, extra in enumerate([
        {'title_difference_kind': 'cosmetic', 'recording_mbid': 'recording',
         'current_tags': {'duration_ms': 200000}, 'expected_duration_ms': 200100,
         'acoustid': {'status': 'ok', 'recordings': [{'mbid': 'recording', 'score': .99}]},
         'changes': {'title': {'old': 'Part 1', 'new': 'Part One'}}},
        {'assignment_evidence': 'audio', 'expected_artist_names': ['Artist', 'Guest'],
         'artist_aliases': [], 'changes': {'track_artist': {'old': 'Artist', 'new': 'Artist; Guest'}}},
        {'routine_numbering': True, 'changes': {'track_number': {'old': 9, 'new': 1}}},
        {'routine_numbering': True, 'numbering_recovery': True,
         'changes': {'disc_number': {'old': 1, 'new': 2}}},
    ], 2):
        db.add(index, f'/music/album/{index}.flac', {'confidence': 'clear', **extra},
               job_id='library_reconciliation', kind='reconciliation_metadata')
    with db._get_connection() as conn:
        full = effective_case_modes(conn, job_id='library_reconciliation')
        compact = effective_case_modes(conn, job_id='library_reconciliation', compact=True)
    assert {k: (v['mode'], v['ready_ids']) for k,v in full.items()} == {k: (v['mode'], v['ready_ids']) for k,v in compact.items()}
    assert all('assignment_options' not in item['details'] for v in compact.values() for item in v['items'])


def test_queue_navigation_spans_pages_in_the_same_filtered_order():
    db = _Database()
    for index in range(1, 6):
        db.add(index, f'/music/Album {index}/song.flac',
               {'album_folder': f'/music/Album {index}', 'album_title': f'Album {index}'},
               job_id='library_reconciliation', kind='reconciliation_metadata')
    first = list_cases(db, limit=2, job_id='library_reconciliation')
    second = list_cases(db, page=1, limit=2, job_id='library_reconciliation')
    assert first['case_ids'] == second['case_ids']
    assert len(first['case_ids']) == 5
    assert first['case_ids'][2] == second['items'][0]['case_id']
    assert first['case_ids'][1] == first['items'][-1]['case_id']
    filtered = list_cases(db, limit=2, q='Album 3', job_id='library_reconciliation')
    assert filtered['case_ids'] == [filtered['items'][0]['case_id']]
    assert list_cases(db, page=99, limit=2)['page'] == 2


def test_queue_summary_preserves_current_album_title(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    summary = next(item for item in list_cases(album.db)['items'] if item['case_id'] == case['case_id'])
    assert summary['current_album_title'] == case['current_album_title']
    with album.db._get_connection() as conn:
        for finding in case['items']:
            details = {**finding['details'], 'library_album_title': 'Current collection',
                       'album_title': 'Proposed edition'}
            conn.execute('UPDATE repair_findings SET details_json=? WHERE id=?',
                         (json.dumps(details), finding['id']))
    found = list_cases(album.db, q='current collection')['items']
    assert len(found) == 1 and found[0]['current_album_title'] == 'Current collection'


def test_search_documents_reuse_names_and_invalidate_only_changed_findings(monkeypatch):
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE repair_findings(id INTEGER PRIMARY KEY,title TEXT,details_json TEXT,status TEXT,job_id TEXT,review_case_key TEXT)')
    conn.executemany('INSERT INTO repair_findings VALUES(?,?,?,?,?,?)', [
        (1, 'TRON: Legacy', json.dumps({'artist': 'Daft Punk', 'acoustid': {'recordings': [{'title': 'Excluded'}]}}), 'pending', 'job', 'a'),
        (2, 'Reflections', '{}', 'pending', 'job', 'a'),
        (3, 'Other', '{}', 'resolved', 'job', 'b')])
    original, calls = search.names, []
    monkeypatch.setattr(search, 'names', lambda *args: calls.append(args) or original(*args))
    search.prepare(conn, status='pending', job_id='job')
    assert len(calls) == 2
    assert search.matching_cases(conn, status='pending', job_id='job', query='tron refl') == ['a']
    assert search.matching_cases(conn, status='pending', job_id='job', query='excluded') == []
    search.prepare(conn, status='pending', job_id='job')
    assert len(calls) == 2
    conn.execute("UPDATE repair_findings SET title='New name' WHERE id=1")
    search.prepare(conn, status='pending', job_id='job')
    assert len(calls) == 3
    assert search.matching_cases(conn, status='pending', job_id='job', query='tron') == []
    conn.execute('DELETE FROM repair_findings WHERE id=2')
    assert conn.execute('SELECT COUNT(*) FROM repair_review_search_documents').fetchone()[0] == 1


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


def test_persisted_plan_marks_previous_actions_settled_without_hiding_current_action():
    db = _Database()
    details = {'album_id': 10, 'profile_id': 1, 'album_title': 'Album'}
    db.add(2, '/music/Album/01.flac', details, status='dismissed',
           job_id='library_reconciliation', kind='reconciliation_metadata')
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


def test_obsolete_extra_keeps_evidence_and_coverage_without_claiming_user_review():
    db = _Database()
    details = {'album_id': 10, 'profile_id': 1, 'album_title': 'Album'}
    db.add(1, '/music/Album/01.flac', {**details, 'changes': {'title': {'new': 'One'}},
           'confidence': 'clear'}, job_id='library_reconciliation', kind='reconciliation_metadata')
    audio = {'status': 'ok', 'recordings': [{'title': 'Different song', 'score': 1}]}
    db.add(2, '/music/Album/02.flac', {**details, 'acoustid': audio,
           'current_tags': {'title': 'Expected song'}}, status='resolved',
           job_id='library_reconciliation', kind='reconciliation_extra')
    db.conn.execute("UPDATE repair_findings SET user_action='obsolete' WHERE id=2")
    case_id = persist_album_plan(db, {**details, 'evidence_signature': 'evidence'}, {
        'source': 'musicbrainz', 'release_id': 'release-id',
        'assignments': [{'status': 'extra', 'file_path': '/music/Album/02.flac',
                         'audio_conflict': 'different_recording'}],
    })
    case = get_case(db, case_id, job_id='library_reconciliation')
    extra = case['album_plan']['assignments'][0]
    assert extra['status'] == 'extra'
    assert extra['review_state'] == 'obsolete'
    assert extra['acoustid'] == audio
    assert case['album_plan']['counts']['extra'] == 1
    assert case['album_plan']['counts']['settled'] == 0

    db.conn.execute("UPDATE repair_findings SET status='dismissed', user_action=NULL WHERE id=2")
    case = get_case(db, case_id, job_id='library_reconciliation')
    assert case['album_plan']['assignments'][0]['review_state'] == 'reviewed'
    assert case['album_plan']['counts']['extra'] == 1
    assert case['album_plan']['counts']['settled'] == 1


def test_refresh_snapshot_does_not_restore_case_to_old_bucket():
    db = _Database()
    db.add(1, '/music/Album/01.flac', {'album_id': 10, 'album_title': 'Album',
        'changes': {'track_number': {'new': 1}}},
        job_id='library_reconciliation', kind='reconciliation_metadata')
    key = list_cases(db)['items'][0]['case_id']
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._album_refresh_lock = threading.Lock()
    worker._album_refreshes = {key: {'state': 'running', 'snapshot': get_case(db, key)}}
    db.conn.rollback()
    db.conn.execute("UPDATE repair_findings SET details_json=json_set(details_json, '$.changes', json('{}'))")
    db.conn.commit()
    assert worker.get_review_cases(risk='manual')['items'] == []
    summary = worker.get_review_summary()
    assert summary['all'] == summary['ready'] == 1
    assert summary['review'] == 0


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_candidate_preview_keeps_missing_slot_positions():
    from core.reconciliation.projection import candidate_preview
    slots = [{'title': 'Mrs. Rita', 'disc_number': 2, 'track_number': 13}]
    preview = candidate_preview([], (0, 0, 0, 'musicbrainz', 'release', slots,
        {'pairs': [], 'missing_slots': [0], 'extra_files': []}), {})
    assert preview['missing_slots'][0] == {
        'slot_title': 'Mrs. Rita', 'disc_number': 2, 'track_number': 13,
        'display_track_number': 13, 'missing': True}


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_release_catalog_details_preserves_label_catalog_associations():
    from core.reconciliation.projection import release_catalog_details
    result = release_catalog_details({'label-info': [
        {'label': {'name': 'First label'}, 'catalog-number': 'A-1'},
        {'label': {'name': 'Second label'}, 'catalog-number': 'B-2'},
    ]})
    assert result['label_catalogs'] == [
        {'label': 'First label', 'catalog_number': 'A-1'},
        {'label': 'Second label', 'catalog_number': 'B-2'},
    ]


def test_provider_display_uses_full_credit_and_native_artwork():
    itunes = _release_display_metadata('itunes', '1', {
        'collectionName': 'Album', 'artistName': 'Artist', 'artworkUrl100': 'https://example.com/100x100bb.jpg'})
    assert itunes['release_artist'] == 'Artist'
    assert itunes['image_url']
    deezer = _release_display_metadata('deezer', '2', {
        'title': 'Album', 'artist': {'name': 'Artist'}, 'cover_xl': 'https://example.com/cover.jpg'})
    assert deezer['image_url'] == 'https://example.com/cover.jpg'
    cached = _release_display_metadata('deezer', '2', {
        'name': 'Album', 'artists': [{'name': 'Artist'}],
        'genres': ['Rock'], 'image_url': 'https://example.com/cached.jpg'})
    assert cached == {'release_artist': 'Artist', 'image_url': 'https://example.com/cached.jpg'}
    mb = _release_display_metadata('musicbrainz', '3', {
        'title': 'Album', 'artist-credit': [{'name': 'Artist', 'joinphrase': ' & '}, {'name': 'Guest'}]})
    assert mb['release_artist'] == 'Artist & Guest'
