"""matching / audio identity regression contracts."""
from __future__ import annotations

from copy import deepcopy

import pytest
from mutagen.flac import FLAC

import core.reconciliation.selection as reconciliation_selection
from core.reconciliation.display import (
    _suggest_filename,
)
from core.reconciliation.file_evidence import _same_recording_copies
from core.reconciliation.plan import (
    _same_base_recording_title,
    audio_contradicts_local_title,
    reconcile_folder,
    versions_compatible,
)
from core.reconciliation.projection import _candidate_evidence
from core.reconciliation.queue import (
    classify_case,
    get_case,
)
from core.reconciliation.selection import (
    _candidate_shapes_equivalent,
    _has_unexplained_identified_extras,
    _normalize_slot,
    _promote_album_context_candidate,
)
from core.reconciliation.store import (
    persist_album_plan,
)
from core.repair_jobs.base import JobContext
from core.repair_jobs.library_reconciliation import LibraryReconciliationJob
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase
from tests.reconciliation.support.acceptance import (
    ARTIST,
    build_album,
    scan_case,
)
from tests.reconciliation.support.audio import (
    _Acoustid,
    version_pair,
)
from tests.reconciliation.support.queue import _Database
from tests.reconciliation.support.scenarios import (
    _album,
    _metadata_recommendation_album,
)


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


def test_repeated_audio_conflicts_are_one_album_question():
    items = [
        {'id': 1, 'finding_type': 'reconciliation_unresolved', 'details': {}},
        {'id': 2, 'finding_type': 'reconciliation_unresolved', 'details': {}},
    ]

    plan = classify_case(items)

    assert plan['decision_count'] == 1
    assert plan['decisions'][0]['category'] == 'audio_conflicts'
    assert plan['decisions'][0]['finding_ids'] == [1, 2]


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

    assert decision['label'] == 'Add MusicBrainz recording IDs'
    assert decision['summary'] == 'Add on 3 tracks.'


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_candidate_evidence_displays_the_acoustid_recording_that_supports_the_slot():
    files = [{
        'path': '/album/01 - Tautou.mp3',
        'tags': {'title': 'Tautou', 'duration_ms': 102453},
        'acoustid': {'recordings': [
            {'mbid': 'play-id', 'title': 'Play Crack the Sky',
             'artist': 'Brand New', 'score': 0.999},
            {'mbid': 'play-id-2', 'title': 'Play Crack the Sky!',
             'artist': 'Brand New', 'score': 0.999},
            {'mbid': 'play-live-id', 'title': 'Play Crack the Sky (Live)',
             'artist': 'Brand New', 'score': 0.999},
            {'mbid': 'tautou-id', 'title': 'Tautou',
             'artist': 'Brand New', 'score': 0.999},
        ]},
    }]
    slots = [{'title': 'Tautou', 'artist': 'Brand New',
              'recording_mbid': 'tautou-id', 'duration_ms': 102453,
              'track_number': 1, 'disc_number': 1}]
    plan = {'pairs': [{
        'file_index': 0, 'slot_index': 0, 'confidence': 'clear',
        'reasons': ['AcoustID recording ID matches release slot'],
        'duration_delta_ms': 0, 'ambiguity_reasons': [],
    }], 'missing_slots': [], 'extra_files': []}

    evidence = _candidate_evidence(
        files, (1, 1, 0, 'musicbrainz', 'release-id', slots, plan))
    pair = evidence['pairs'][0]

    assert evidence['image_url'] == 'https://coverartarchive.org/release/release-id/front-250'

    assert pair['audio_title'] == 'Tautou'
    assert pair['audio_alternatives'] == [
        {'mbid': 'play-id', 'title': 'Play Crack the Sky',
         'artist': 'Brand New', 'score': 0.999, 'recording_count': 2},
        {'mbid': 'play-live-id', 'title': 'Play Crack the Sky (Live)',
         'artist': 'Brand New', 'score': 0.999, 'recording_count': 1},
    ]
    assert pair['audio_recording_count'] == 1


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_release_shapes_keep_two_explicit_recording_versions_distinct():
    plan = {'pairs': [{'file_index': 0, 'slot_index': 0, 'confidence': 'clear'}],
            'missing_slots': [], 'extra_files': []}
    live_1977 = (1, 1, 0, 'musicbrainz', 'a', [{
        'title': 'Song', 'track_number': 1, 'disc_number': 1,
        'recording_disambiguation': 'live 1977',
    }], plan)
    live_1978 = (1, 1, 0, 'musicbrainz', 'b', [{
        'title': 'Song', 'track_number': 1, 'disc_number': 1,
        'recording_disambiguation': 'live 1978',
    }], plan)

    assert not _candidate_shapes_equivalent(live_1977, live_1978)
    assert not _candidate_shapes_equivalent(live_1977, live_1978, ignore_positions=True)


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_musicbrainz_slot_keeps_recording_disambiguation():
    slot = _normalize_slot({
        'id': 'recording-id', 'name': 'Song',
        'disambiguation': 'Connect Sets acoustic',
    }, 'musicbrainz')

    assert slot['recording_disambiguation'] == 'Connect Sets acoustic'


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_filename_keeps_version_text_confirmed_by_recording_disambiguation():
    slot = {'title': 'Home', 'track_number': 1, 'disc_number': 1,
            'recording_disambiguation': 'live'}

    assert _suggest_filename('/album/01 - Home (Live).flac', slot) == ''


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_filename_does_not_keep_a_different_recording_version():
    slot = {'title': 'Home', 'track_number': 1, 'disc_number': 1,
            'recording_disambiguation': 'demo'}

    assert _suggest_filename('/album/01 - Home (Live).flac', slot) == \
        '/album/01 - Home.flac'


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_strong_audio_conflict_uses_the_release_slot_as_well_as_local_title():
    from core.reconciliation.projection import _audio_conflicts_with_slot

    file = {'tags': {'title': 'Misnamed'}, 'acoustid': {'recordings': [
        {'title': 'Correct Song', 'artist': 'Artist', 'score': 0.99},
    ]}}
    assert not _audio_conflicts_with_slot(file, {'title': 'Correct Song', 'artist': 'Artist'})
    assert _audio_conflicts_with_slot(file, {'title': 'Other Song', 'artist': 'Artist'})


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_same_audio_is_not_a_duplicate_across_recording_versions():
    files = [
        {
            'path': '/album/01 - Luv (sic), Part 5.flac',
            'tags': {'title': 'Luv (sic), Part 5', 'duration_ms': 300000},
            'db': {'title': 'Luv (sic), Part 5',
                   'recording_disambiguation': 'vocal version'},
            'acoustid': {'fingerprint_key': 'shared', 'recordings': []},
        },
        {
            'path': '/album/02 - Luv (sic), Part 5 (Instrumental).flac',
            'tags': {'title': 'Luv (sic), Part 5 (Instrumental)',
                     'duration_ms': 300000},
            'db': {'title': 'Luv (sic), Part 5',
                   'recording_disambiguation': 'instrumental'},
            'acoustid': {'fingerprint_key': 'shared', 'recordings': []},
        },
    ]

    assert _same_recording_copies(files, [0, 1]) is False


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_same_audio_is_not_a_duplicate_across_live_years():
    files = [
        {
            'path': '/album/01 - Song (Live 1977).flac',
            'tags': {'title': 'Song (Live 1977)', 'duration_ms': 300000},
            'db': {'title': 'Song (Live 1977)', 'recording_disambiguation': None},
            'acoustid': {'fingerprint_key': 'shared', 'recordings': []},
        },
        {
            'path': '/album/02 - Song (Live 1978).flac',
            'tags': {'title': 'Song (Live 1978)', 'duration_ms': 300000},
            'db': {'title': 'Song (Live 1978)', 'recording_disambiguation': None},
            'acoustid': {'fingerprint_key': 'shared', 'recordings': []},
        },
    ]

    assert _same_recording_copies(files, [0, 1]) is False


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_same_audio_is_not_a_duplicate_across_named_remixes():
    files = [
        {
            'path': '/album/01 - Song (Foo Remix).flac',
            'tags': {'title': 'Song (Foo Remix)', 'duration_ms': 300000},
            'db': {'title': 'Song', 'recording_disambiguation': 'Foo remix'},
            'acoustid': {'fingerprint_key': 'shared', 'recordings': []},
        },
        {
            'path': '/album/02 - Song (Bar Remix).flac',
            'tags': {'title': 'Song (Bar Remix)', 'duration_ms': 300000},
            'db': {'title': 'Song', 'recording_disambiguation': 'Bar remix'},
            'acoustid': {'fingerprint_key': 'shared', 'recordings': []},
        },
    ]

    assert _same_recording_copies(files, [0, 1]) is False


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize('source', ['itunes', 'deezer'])
def test_non_musicbrainz_release_repairs_audio_supported_recording_ids(tmp_path, monkeypatch, source):
    db, worker, context, paths, mbids = _metadata_recommendation_album(tmp_path, monkeypatch)
    with db._get_connection() as conn:
        conn.execute('UPDATE albums SET canonical_source=?', (source,))
        conn.commit()
    result = LibraryReconciliationJob().scan(context)
    assert not result.errors
    case_id = worker.get_review_cases()['items'][0]['case_id']
    case = worker.get_review_case(case_id)
    items = [item for item in case['items'] if item['finding_type'] == 'reconciliation_metadata']
    assert len(items) == 2
    for item in items:
        details = item['details']
        expected = mbids[int(details['track_id']) - 1]
        assert details['changes']['musicbrainz_recording_id']['new'] == expected
        assert details['source'] == source
        applied = worker._fix_reconciliation_metadata('track', details['track_id'], item['file_path'], details)
        assert applied['success'], applied
        assert FLAC(item['file_path'])['musicbrainz_trackid'] == [expected]
    assert db.get_album_canonical(10)['source'] == source


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize('alias_in_catalogue', [False, True])
def test_stored_artist_aliases_support_cross_script_audio_identity(tmp_path, monkeypatch, alias_in_catalogue):
    db, worker, context, paths, mbids = _metadata_recommendation_album(tmp_path, monkeypatch)
    if alias_in_catalogue:
        get_tracks = reconciliation_selection.get_album_tracks_for_source
        monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *a, **k: [
            {**track, 'artists': [{'name': 'ラム・オブ・ゴッド'}]} for track in get_tracks(*a, **k)])
    else:
        for recording in context.acoustid_client.matches.values():
            recording['artist'] = 'ラム・オブ・ゴッド'
    assert not LibraryReconciliationJob().scan(context).errors
    case_id = worker.get_review_cases()['items'][0]['case_id']
    items = worker.get_review_case(case_id)['items']
    assert len(items) == 2
    assert all(item['finding_type'] == 'reconciliation_metadata' for item in items)
    assert {item['details']['changes']['musicbrainz_recording_id']['new'] for item in items} == set(mbids)


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_provider_independent_recording_identity_requires_unique_corroboration():
    from core.reconciliation.metadata import confirmed_recording
    slot = {'title': 'Home', 'artist': 'Artist', 'duration_ms': 200000}
    recording = {'mbid': '80951433-fbbf-45e8-b906-e820652b9ca5', 'title': 'Home', 'artist': 'Artist', 'score': .99}
    file = {'tags': {'duration_ms': 200000}, 'acoustid': {'status': 'ok', 'recordings': [recording]}}
    assert confirmed_recording(file, slot, 'itunes') == recording
    file['acoustid']['recordings'].append({**recording, 'mbid': 'e2bd3843-a9f1-447f-b733-f5f3d5dd3d33'})
    assert not confirmed_recording(file, slot, 'itunes')
    file['acoustid']['recordings'] = [{**recording, 'artist': 'Someone Else'}]
    assert not confirmed_recording(file, slot, 'itunes')
    file['acoustid']['recordings'] = [{**recording, 'artist': ''}]
    assert not confirmed_recording(file, slot, 'itunes')
    file['acoustid']['recordings'] = [recording]
    file['tags']['duration_ms'] = 210000
    assert not confirmed_recording(file, slot, 'itunes')


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_album_context_does_not_override_better_audio_assignment():
    plan = {'pairs': [], 'extra_files': [], 'missing_slots': []}
    linked = (1, 1, 0, 'musicbrainz', 'linked', [], plan)
    better = (2, 2, 0, 'musicbrainz', 'better', [], plan)
    scored = [better, linked]

    _promote_album_context_candidate(scored, [('musicbrainz', 'linked')])

    assert scored == [better, linked]


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_identified_bonus_audio_requires_edition_confirmation_but_duplicate_does_not():
    files = [
        {'acoustid': {'status': 'ok', 'recordings': [
            {'mbid': 'one', 'title': 'One', 'score': 0.99},
        ]}},
        {'acoustid': {'status': 'ok', 'recordings': [
            {'mbid': 'bonus', 'title': 'The Real Song for the Deaf', 'score': 0.99},
        ]}},
    ]
    slots = [{'title': 'One', 'track_number': 1, 'disc_number': 1}]
    plan = {
        'pairs': [{'file_index': 0, 'slot_index': 0, 'confidence': 'clear'}],
        'extra_files': [1], 'missing_slots': [], 'competing_files': {},
    }
    candidate = (1, 1, -1, 'musicbrainz', 'short-edition', slots, plan)

    assert _has_unexplained_identified_extras(files, candidate)

    duplicate_plan = {**plan, 'competing_files': {1: [0]}}
    duplicate_candidate = (*candidate[:6], duplicate_plan)
    assert not _has_unexplained_identified_extras(files, duplicate_candidate)


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_wrong_audio_exposes_completeness_before_assignment_is_settled(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    root.mkdir()
    path = root / '01 - Laid to Rest.flac'
    path.write_bytes(b'wrong audio')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr(module, '_read_file_tags', lambda _path: {
        'title': 'Laid to Rest', 'artist': 'Lamb of God', 'album': 'Ashes of the Wake',
        'track_number': 1, 'disc_number': 1, 'duration_ms': 200000,
    })
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *_args: [
        {'id': 'wanted-id', 'name': 'Laid to Rest', 'track_number': 1,
         'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source',
                        lambda *_args: {'name': 'Ashes of the Wake'})
    client = _Acoustid({path.name: {'mbid': 'other-audio', 'title': 'Southbound',
                                   'artist': 'Thin Lizzy', 'score': 0.99}})
    worker = RepairWorker(database=db, transfer_folder=str(root))
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda _self: client))
    monkeypatch.setattr('core.library.playlist_membership.server_playlist_membership',
                        lambda: {'1': ['My playlist']})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)
    LibraryReconciliationJob().scan(context)
    case_id = worker.get_review_cases()['items'][0]['case_id']
    findings = worker.get_review_case(case_id)['items']
    assert {finding['finding_type'] for finding in findings} == {
        'reconciliation_extra', 'reconciliation_intent'}
    assert path.exists()  # scanning never edits a file
    extra = next(finding for finding in findings if finding['finding_type'] == 'reconciliation_extra')
    assert worker.fix_finding(extra['id'], fix_action='quarantine')['success'] is False
    staged = worker.apply_review_decision(case_id, extra['id'],
        evidence_token=extra['evidence_token'], action='fix', fix_action='quarantine')
    assert staged['success'] and path.exists()
    with db._get_connection() as conn:
        conn.execute("UPDATE tracks SET file_path = '/music/another.flac' WHERE id = 1")
        conn.commit()
    changed = worker.fix_finding(extra['id'], review_approved=True)
    assert changed['success'] is False and 'another file' in changed['error']
    assert path.exists()
    with db._get_connection() as conn:
        conn.execute('UPDATE tracks SET file_path = ? WHERE id = 1', (str(path),))
        conn.commit()
    extra = next(finding for finding in worker.get_review_case(case_id)['items']
                 if finding['finding_type'] == 'reconciliation_extra')
    blocked = worker.fix_finding(extra['id'], review_approved=True)
    assert blocked['success'] is False and 'playlist' in blocked['error']
    assert path.exists()
    monkeypatch.setattr('core.library.playlist_membership.server_playlist_membership', lambda: {})
    extra = next(finding for finding in worker.get_review_case(case_id)['items']
                 if finding['finding_type'] == 'reconciliation_extra')
    result = worker.fix_finding(extra['id'], review_approved=True)
    assert result['success'], result
    assert not path.exists()
    assert (root / '.deleted' / path.name).exists()
    with db._get_connection() as conn:
        assert conn.execute('SELECT id FROM tracks WHERE id = 1').fetchone() is None


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_reconciliation_preserves_edition_title_and_complete_artist_credit(
        tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    root.mkdir()
    path = root / '01 - Laid to Rest.flac'
    path.write_bytes(b'audio fixture')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    with db._get_connection() as conn:
        conn.execute("UPDATE albums SET title = 'Ashes of the Wake (Deluxe Edition)' "
                     "WHERE id = 10")
        conn.commit()
    worker = RepairWorker(database=db)
    client = _Acoustid({path.name: {
        'mbid': 'laid-id', 'title': 'Laid to Rest',
        'artist': 'Lamb of God', 'score': 0.99,
    }})
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr(module, '_read_file_tags', lambda _path: {
        'title': 'Laid to Rest', 'artist': 'Lamb of God & Guest Artist',
        'album': 'Ashes of the Wake (Deluxe Edition)', 'track_number': 1,
        'disc_number': 1, 'duration_ms': 200000,
    })
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *_args: [
        {'id': 'laid-id', 'name': 'Laid to Rest',
         'artists': [{'name': 'Lamb of God'}],
         'track_number': 1, 'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda *_args: {
        'name': 'Ashes of the Wake',
        'artists': [{'name': 'Lamb of God'}, {'name': 'Guest Artist'}],
        'disambiguation': 'US deluxe edition',
    })
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)

    result = LibraryReconciliationJob().scan(context)

    assert result.findings_created == 0
    assert worker.get_review_cases(job_id='library_reconciliation')['total'] == 0


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_video_slots_are_excluded_without_discarding_bluray_audio(monkeypatch):
    from core.reconciliation.projection import candidate_preview
    from core.reconciliation.selection import _score_release_candidates
    items = [
        {'id': 'video', 'name': 'Concert video', 'video': True, 'disc_number': 1},
        {'id': 'audio', 'name': 'Song', 'video': False, 'disc_number': 2,
         'track_number': 1, 'duration_ms': 200000, 'medium_format': 'Blu-ray'},
        {'id': 'unknown', 'name': 'Bonus', 'disc_number': 2, 'track_number': 2},
    ]
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source',
                        lambda *args, **kwargs: {'items': items})
    row = _score_release_candidates([], [('musicbrainz', 'release')], {})[0]
    assert [slot['source_track_id'] for slot in row.slots] == ['audio', 'unknown']
    assert row.plan['missing_slots'] == [0, 1]
    assert candidate_preview([], row, {})['excluded_video_tracks'] == 1
    assert row.slots[0]['disc_number'] == 2


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_same_positioned_musicbrainz_recordings_ignore_release_title_translations():
    plan = {'pairs': [], 'missing_slots': [0], 'extra_files': []}
    left = (0, 0, 0, 'musicbrainz', 'a', [{
        'title': 'Gymnopédie No. 1 (Arr. for Harp)', 'disc_number': 1,
        'track_number': 3, 'recording_mbid': 'same-performance'}], plan)
    right = (0, 0, 0, 'musicbrainz', 'b', [{
        'title': 'Gymnopedie 1', 'disc_number': 1,
        'track_number': 3, 'recording_mbid': 'same-performance'}], plan)
    assert _candidate_shapes_equivalent(left, right)
    right[5][0]['recording_mbid'] = 'different-performance'
    assert not _candidate_shapes_equivalent(left, right)
    right[5][0]['recording_mbid'] = 'same-performance'
    right[5][0]['track_number'] = 4
    assert not _candidate_shapes_equivalent(left, right)


@pytest.mark.parametrize('weakness', ['', 'id', 'position', 'duration', 'duplicate', 'performance'])
def test_joint_evidence_distinguishes_named_versions_without_hiding_weakness(weakness):
    files, slots = version_pair()
    if weakness == 'id': files[1]['tags']['mbid'] = ''
    if weakness == 'position': files[1]['tags']['track_number'] = 3
    if weakness == 'duration': files[1]['tags']['duration_ms'] += 10000
    if weakness == 'duplicate':
        files.append(deepcopy(files[0]))
        files[-1]['path'] = '/other/01 - Song.flac'
    if weakness == 'performance': files[1]['tags']['title'] = 'Song (live)'
    plan = reconcile_folder(files, slots)
    if not weakness:
        assert [(pair['file_index'], pair['slot_index']) for pair in plan['pairs']] == [(0, 0), (1, 1)]
        assert all(pair['confidence'] == 'clear' for pair in plan['pairs'])
    else:
        assert plan['extra_files'] or any(pair['confidence'] != 'clear' for pair in plan['pairs'])


def test_competitor_explanation_names_the_file_and_slot():
    files, slots = version_pair()
    files[1]['tags']['mbid'] = ''
    plan = reconcile_folder(files, slots)
    original = next(pair for pair in plan['pairs'] if pair['slot_index'] == 0)
    assert original['competitors'][0]['file_path'] == files[1]['path']
    assert original['competitors'][0]['assigned_title'] == slots[1]['title']


def test_selected_release_can_review_credit_disagreement_without_equating_artists(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    for recording in album.context.acoustid_client.matches.values():
        recording.update(artist='Different Catalogue Credit', mbid='other-recording')
    original_bytes = [path.read_bytes() for path in album.paths]
    case = scan_case(album)
    assert case['album_plan']['state'] != 'release_conflict'
    assert len(case['album_plan']['assignments']) == 2
    assert all(item['details'].get('catalogue_disagreement') for item in case['items'])
    result = album.worker.apply_review_decision_group(case['case_id'], 'catalogue_assignments', action='fix')
    assert result['success'] and result['saved'] == 2, result
    assert [path.read_bytes() for path in album.paths] == original_bytes
    refreshed = scan_case(album)
    assert all(row['confidence'] == 'manual' for row in refreshed['album_plan']['assignments'])
    assert all(item['finding_type'] == 'reconciliation_metadata' for item in refreshed['items']), [
        (item['finding_type'], item['description'], item['details'].get('ambiguity_reasons'))
        for item in refreshed['items']]


def test_plan_refresh_uses_reviewed_inputs_without_audio_or_catalogue_calls(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as job
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    assert '_planning' not in case['album_plan']
    def unexpected(*args, **kwargs):
        raise AssertionError('Draft choice must reuse reviewed evidence')
    monkeypatch.setattr(album.context.acoustid_client, 'is_available', unexpected)
    monkeypatch.setattr(album.context.acoustid_client, 'fingerprint_file', unexpected, raising=False)
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', unexpected)
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', unexpected)
    monkeypatch.setattr(job, 'walk_library', unexpected)
    album.context.playlist_membership = unexpected
    result = LibraryReconciliationJob().scan(album.context, target_folder=str(album.folder), scope='plan')
    assert not result.errors and not result.stopped_early
    after = album.worker.get_review_case(case['case_id'])
    assert [row['details']['changes'] for row in after['items']] == [row['details']['changes'] for row in case['items']]


def test_unplugged_punctuation_and_recording_context_are_equivalent():
    file = {'path': '08 - Song - MTV Unplugged.mp3', 'tags': {'title': 'Song - MTV Unplugged', 'artist': ARTIST, 'duration_ms': 271080},
            'acoustid': {'status': 'ok', 'recordings': [{'mbid': 'recording', 'title': 'Song', 'artist': ARTIST, 'score': .98}]}}
    slot = {'title': 'Song (MTV Unplugged)', 'artist': ARTIST, 'duration_ms': 271200, 'recording_mbid': 'recording', 'recording_disambiguation': 'live'}
    assert _same_base_recording_title('Song', file['tags']['title'])
    assert not audio_contradicts_local_title(file)
    assert versions_compatible(file, slot)
    assert reconcile_folder([file], [slot])['pairs'][0]['confidence'] == 'clear'
    assert not versions_compatible(file, {**slot, 'title': 'Song (live at another venue)'})
    assert not _same_base_recording_title('Song', 'Song - Part Two')
