"""acquisition / file handoffs regression contracts."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from mutagen.flac import FLAC

from core.library import playlist_membership
from core.reconciliation.fulfillment import (
    published_wishlist_track,
    record_request,
)
from core.reconciliation.grouping import _group_logical_albums
from core.reconciliation.queue import (
    classify_case,
    list_cases,
)
from core.repair_jobs.base import JobContext
from core.repair_jobs.library_reconciliation import LibraryReconciliationJob
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase
from tests.reconciliation.support.acceptance import (
    build_album,
    scan_case,
)
from tests.reconciliation.support.acquisition import Database
from tests.reconciliation.support.audio import (
    _Acoustid,
)
from tests.reconciliation.support.queue import _Database, _ready_selection
from tests.reconciliation.support.scenarios import (
    _album,
    _metadata_recommendation_album,
)


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
    assert _ready_selection(db)[0] == []
    assert list_cases(db, job_id='library_reconciliation')['ready_total'] == 0


def test_unassigned_album_file_can_use_the_existing_quarantine_guard():
    worker = RepairWorker.__new__(RepairWorker)
    captured = []
    worker._fix_reconciliation_extra = lambda *args: captured.append(args) or {'success': True}
    details = {'_fix_action': 'quarantine', 'track_id': 7, 'album_id': 10}

    assert worker._fix_reconciliation_assignment('track', '7', '/music/extra.flac', details)['success']
    assert captured == [('track', '7', '/music/extra.flac', details)]
    assert not worker._fix_reconciliation_assignment('track', '7', '/music/extra.flac',
                                                     {**details, 'version_conflict': True})['success']


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_scan_ignores_watchlist_removal_snapshots(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    archive = root / '.watchlist-removal-20261005-deadbeef' / 'Artist' / 'Album'
    archive.mkdir(parents=True)
    path = archive / '01 - Archived.flac'
    path.write_bytes(b'archived audio')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    client = _Acoustid({path.name: {
        'mbid': 'archived-id', 'title': 'Archived', 'artist': 'Artist', 'score': 0.99,
    }})
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client)

    result = LibraryReconciliationJob().scan(context)

    assert result.scanned == 0
    assert client.fingerprints == client.lookups == 0


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize(('local_title', 'catalogue_title'), [
    ('Gnossienne: No. 1', 'Gnossienne No. 1'),
    ('Cello Suite No. 1 in G Major, BWV 1007: II. Allemande',
     'Unaccompanied Cello Suite no. 1 in G major, BWV 1007: II. Allemande'),
])
def test_recording_id_removal_preserves_composer_credits_and_catalogue_aliases(local_title, catalogue_title):
    from types import SimpleNamespace

    from core.reconciliation.metadata import recording_id_conflict
    mbid = '61a45c1a-a717-4c58-abba-0563000f8a1d'
    file = {'tags': {'mbid': mbid}, 'acoustid': {'status': 'no_match'}}
    slot = {'title': local_title, 'artist': 'Alexandre Tharaud'}
    record = {'id': mbid, 'title': catalogue_title,
              'artist-credit': [{'name': 'Erik Satie'}]}
    client = SimpleNamespace(get_recording=lambda *a, **k: record)
    assert not recording_id_conflict(file, slot, client, {})
    record.update(title='別の表記', aliases=[{'name': local_title}])
    assert not recording_id_conflict(file, slot, client, {})
    record.update(title='Winter Dies', aliases=[])
    assert recording_id_conflict(file, slot, client, {})


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_ambiguous_replacement_ids_offer_reviewed_removal_after_catalogue_check(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from core.reconciliation.policy import _finding_tier
    db, worker, context, paths, _ = _metadata_recommendation_album(tmp_path, monkeypatch)
    first = context.acoustid_client.matches[paths[0].name]
    context.acoustid_client.matches[paths[0].name] = [first, {**first, 'mbid': '9a43591f-aa6c-45a1-b7fd-b85e79a1f2d3'}]
    context.mb_client = SimpleNamespace(get_recording=lambda mbid, **kwargs: {
        'id': mbid, 'title': 'Different Song', 'artist-credit': [{'name': 'Other Artist'}]})
    assert not LibraryReconciliationJob().scan(context).errors
    case_id = worker.get_review_cases()['items'][0]['case_id']
    item = next(item for item in worker.get_review_case(case_id)['items'] if item['file_path'] == str(paths[0]))
    assert _finding_tier(item)[0] == 'manual'
    assert item['details']['changes']['musicbrainz_recording_id']['new'] == ''
    assert item['details']['recording_id_conflict']['title'] == 'Different Song'
    applied = worker._fix_reconciliation_metadata('track', '1', str(paths[0]), item['details'])
    assert applied['success'], applied
    assert 'musicbrainz_trackid' not in FLAC(str(paths[0]))
    assert FLAC(str(paths[0]))['musicbrainz_albumid'] == ['second-release']


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize('intent', ['inferred', 'watched', 'complete', 'partial'])
def test_lone_playlist_track_infers_partial_unless_explicit_or_watched(tmp_path, monkeypatch, intent):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    folder = root / 'Lamb of God' / 'Ashes of the Wake'
    folder.mkdir(parents=True)
    path = folder / '01 - Laid to Rest.flac'
    path.write_bytes(b'audio fixture')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    worker = RepairWorker(database=db)
    client = _Acoustid({path.name: {
        'mbid': 'laid-id', 'title': 'Laid to Rest',
        'artist': 'Lamb of God', 'score': 0.99,
    }})
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr(module, '_read_file_tags', lambda _path: {
        'title': 'Laid to Rest', 'artist': 'Lamb of God',
        'album': 'Ashes of the Wake', 'track_number': 1,
        'disc_number': 1, 'duration_ms': 200000,
    })
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *_args: [
        {'id': 'laid-id', 'name': 'Laid to Rest',
         'artists': [{'name': 'Lamb of God'}],
         'track_number': 1, 'disc_number': 1, 'duration_ms': 200000},
        {'id': 'hourglass-id', 'name': 'Hourglass',
         'artists': [{'name': 'Lamb of God'}],
         'track_number': 2, 'disc_number': 1, 'duration_ms': 240000},
    ])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda *_args: {
        'name': 'Ashes of the Wake', 'artists': [{'name': 'Lamb of God'}]})
    context = JobContext(
        db=db, transfer_folder=str(root), config_manager=None,
        acoustid_client=client, create_finding=worker._create_finding,
        playlist_membership=lambda: {'1': ['Imported playlist']},
    )
    if intent == 'watched':
        monkeypatch.setattr(module, 'watchlist_artist_names', lambda _: {'lamb of god'})
    if intent in ('complete', 'partial'):
        monkeypatch.setattr(LibraryReconciliationJob, '_intents',
                            lambda *_: {'10': (intent, 'spotify', 'ashes-release', [])})

    result = LibraryReconciliationJob().scan(context)

    expected_findings = int(intent in ('watched', 'complete'))
    assert result.findings_created == expected_findings
    assert worker.get_review_cases(job_id='library_reconciliation')['total'] == expected_findings
    with db._get_connection() as conn:
        saved = conn.execute(
            'SELECT intent_scope, state, confirmed_count, missing_count, plan_json '
            'FROM reconcile_album_plans'
        ).fetchone()
    assert saved['intent_scope'] == ('' if intent == 'watched' else 'complete' if intent == 'complete' else 'partial')
    assert saved['confirmed_count'] == 1
    document = json.loads(saved['plan_json'])
    assert document['intent_inferred'] == (intent == 'inferred')
    assert document['assignments'][0]['playlists'] == ['Imported playlist']
    assert {row['status'] for row in document['assignments']} == {
        'confirmed', 'missing' if expected_findings else 'not_owned'}
    # An inferred default is not persisted as a user decision. Removing the
    # playlist evidence must restore the explicit completeness question.
    if intent == 'inferred':
        context.playlist_membership = lambda: {}
        LibraryReconciliationJob().scan(context)
        assert worker.get_review_cases()['total'] == 1


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_mixed_album_folder_is_reviewed_without_quarantine_proposals(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    root.mkdir()
    first = root / '01 - Segue.flac'
    second = root / '02 - Segue.flac'
    first.write_bytes(b'one')
    second.write_bytes(b'two')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, first)
    with db._get_connection() as conn:
        conn.execute("""INSERT INTO albums (id, title, artist_id, server_source)
                        VALUES (11, 'Fantastic Planet Bonus', 1, 'test')""")
        conn.execute("""INSERT INTO tracks
                        (id, album_id, artist_id, title, track_number, file_path)
                        VALUES (2, 11, 1, 'Segue', 2, ?)""", (str(second),))
        conn.commit()
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr(module, '_read_file_tags', lambda _path: {'title': 'Segue'})
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source',
                        lambda *_args: (_ for _ in ()).throw(AssertionError('mixed folder')))
    client = _Acoustid({path.name: {'mbid': path.name, 'title': 'Segue',
                                    'artist': 'Failure', 'score': 0.99}
                        for path in (first, second)})
    findings = []
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client,
                         create_finding=lambda **finding: findings.append(finding) or True)
    result = LibraryReconciliationJob().scan(context)
    assert result.scanned == 2
    assert [finding['finding_type'] for finding in findings] == ['reconciliation_unresolved']


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_replacement_fragment_joins_same_release_in_sibling_folder():
    original = '/music/Ghost/[2018] Prequelle'
    downloaded = '/music/Ghost/[2018]Album Ghost - Prequelle'
    folders = {
        original: [f'{original}/{number:02}.flac' for number in (1, 2, 3, 5)],
        downloaded: [f'{downloaded}/04 - See the Light.mp3'],
    }
    tracks = {path: {'album_id': 10, 'track_number': number, 'disc_number': 1}
              for number, path in zip((1, 2, 3, 5), folders[original])}
    tracks[folders[downloaded][0]] = {
        'album_id': 11, 'track_number': 4, 'disc_number': 1}
    albums = {
        10: {'album_title': 'Prequelle (FYE exclusive metallic vinyl)',
             'artist_name': 'Ghost', 'owner_profile_id': 1,
             'canonical_source': 'musicbrainz', 'canonical_album_id': 'same-release'},
        11: {'album_title': 'Prequelle', 'artist_name': 'Ghost', 'owner_profile_id': 1,
             },
    }
    grouped, trusted = _group_logical_albums(folders, tracks, albums)
    assert list(grouped) == [original]
    assert len(grouped[original]) == 5
    assert trusted == {original: 10}

    albums[11]['canonical_source'] = 'musicbrainz'
    albums[11]['canonical_album_id'] = 'different-edition'
    grouped, trusted = _group_logical_albums(folders, tracks, albums)
    assert len(grouped) == 2
    assert trusted == {}

    # A replacement downloaded from another edition can carry a different
    # release MBID while both album rows still share a catalog album ID.
    albums[10]['itunes_album_id'] = '1368196699'
    albums[11]['itunes_album_id'] = '1368196699'
    grouped, trusted = _group_logical_albums(folders, tracks, albums)
    assert list(grouped) == [original]
    assert len(grouped[original]) == 5
    assert trusted == {original: 10}


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_wrong_audio_can_request_exact_replacement_before_quarantine(tmp_path, monkeypatch):
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
    client = _Acoustid({path.name: [
        {'mbid': 'wrong-one', 'title': 'Southbound',
         'artist': 'Thin Lizzy', 'score': 0.99},
        {'mbid': 'wrong-two', 'title': 'Jailbreak',
         'artist': 'Thin Lizzy', 'score': 0.99},
    ]})
    worker = RepairWorker(database=db, transfer_folder=str(root))
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda _self: client))
    monkeypatch.setattr('core.library.playlist_membership.server_playlist_membership', lambda: {})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)

    LibraryReconciliationJob().scan(context)

    case_id = worker.get_review_cases()['items'][0]['case_id']
    extra = next(item for item in worker.get_review_case(case_id)['items']
                 if item['finding_type'] == 'reconciliation_extra')
    assert extra['finding_type'] == 'reconciliation_extra'
    assert extra['details']['audio_conflict'] == 'different_recording'
    assert extra['details']['replacement_track']['source_track_id'] == 'wanted-id'
    assert extra['category'] == 'audio_conflicts'
    assert extra['review_reason'] == (
        'The fingerprint identifies different audio than the expected release track')

    result = worker.apply_review_decision(
        case_id, extra['id'], evidence_token=extra['evidence_token'],
        action='fix', fix_action='replace')

    assert result['success'] and path.exists()
    with db._get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM wishlist_tracks').fetchone()[0] == 0
    result = worker.fix_finding(extra['id'], review_approved=True)
    assert not result['success'] and path.exists()
    case = worker.get_review_case(case_id)
    intent = next((item for item in case['items'] if item['finding_type'] == 'reconciliation_intent'), None)
    if intent:
        assert worker.apply_review_decision(case_id, intent['id'], evidence_token=intent['evidence_token'], action='fix', fix_action='partial')['success']
    assert worker.start_review_missing_tracks(case_id)['success']
    assert path.exists()
    result = worker.fix_finding(extra['id'], review_approved=True)
    assert result['success'], result
    assert path.exists()
    assert not (root / '.deleted' / path.name).exists()
    with db._get_connection() as conn:
        assert conn.execute('SELECT id FROM tracks WHERE id = 1').fetchone() is not None
        assert conn.execute('SELECT state FROM reconcile_replacements').fetchone()[0] == 'waiting'
        row = conn.execute(
            'SELECT spotify_track_id, source_info FROM wishlist_tracks').fetchone()
    assert row['spotify_track_id'] == 'wanted-id'
    source_info = json.loads(row['source_info'])
    assert source_info['source_album_id'] == 'ashes-release'
    assert source_info['reason'] == 'library_reconciliation_wrong_audio'


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_version_request_keeps_local_file_and_playlist_membership(tmp_path, monkeypatch):
    path = tmp_path / '17 - Jamie (live acoustic).flac'
    path.write_bytes(b'local version')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    worker = RepairWorker(database=db, transfer_folder=str(tmp_path))
    monkeypatch.setattr('core.library.playlist_membership.server_playlist_membership',
                        lambda: {'1': ['Keep this version']})
    stat = path.stat()

    result = worker._fix_reconciliation_extra('track', '1', str(path), {
        '_fix_action': 'request_missing', 'album_id': 10, 'track_id': 1,
        'source': 'spotify', 'release_id': 'ashes-release',
        'album_title': 'Ashes of the Wake', 'artist_name': 'Lamb of God',
        'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
        'version_conflict': True,
        'replacement_track': {
            'source_track_id': 'release-version', 'name': 'Jamie (take 6 - live at LMU)',
            'artist': 'Lamb of God', 'track_number': 4, 'disc_number': 2,
        },
    })

    assert result['success'], result
    assert path.exists()
    with db._get_connection() as conn:
        assert conn.execute('SELECT id FROM tracks WHERE id = 1').fetchone() is not None
        requested = conn.execute(
            'SELECT spotify_track_id FROM wishlist_tracks').fetchone()
    assert requested['spotify_track_id'] == 'release-version'


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_ambiguous_assignment_persists_reasons_and_is_not_offered_for_quarantine(
        tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    root.mkdir()
    path = root / '01 - Laid to Rest.flac'
    path.write_bytes(b'audio fixture')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    worker = RepairWorker(database=db)
    client = _Acoustid({})
    client.lookup_fingerprint_with_status = lambda *_args: {
        'status': 'ok', 'recordings': [
            {'mbid': 'laid-id', 'title': 'Laid to Rest',
             'artist': 'Lamb of God', 'score': 0.99},
            {'mbid': 'two', 'title': 'Another Song', 'artist': 'Other', 'score': 0.99},
        ],
    }
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr(module, '_read_file_tags', lambda _path: {
        'title': 'Laid to Rest', 'artist': 'Lamb of God',
        'album': 'Ashes of the Wake', 'track_number': 1,
        'disc_number': 1, 'duration_ms': 200000,
    })
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *_args: [
        {'id': 'laid-id', 'name': 'Laid to Rest', 'track_number': 1,
         'disc_number': 1, 'duration_ms': 204000},
    ])
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)
    LibraryReconciliationJob().scan(context)
    case_id = worker.get_review_cases()['items'][0]['case_id']
    findings = worker.get_review_case(case_id)['items']
    unresolved = next(item for item in findings
                      if item['finding_type'] == 'reconciliation_unresolved'
                      and item['title'].startswith('Confirm:'))
    assert unresolved['details']['ambiguity_reasons'] == [
        'AcoustID returned competing identities not resolved by the other evidence']
    assert all(item['finding_type'] != 'reconciliation_extra' for item in findings)


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_competing_audio_copies_become_one_keeper_decision(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    root.mkdir()
    first = root / '01 - Hourglass.flac'
    second = root / 'copy - Hourglass.flac'
    third = root / 'single - Hourglass.flac'
    first.write_bytes(b'first fixture')
    second.write_bytes(b'second fixture')
    third.write_bytes(b'third fixture')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, first)
    with db._get_connection() as conn:
        conn.execute("""INSERT INTO tracks
            (id, album_id, artist_id, title, track_number, file_path)
            VALUES (2, 10, 1, 'Hourglass', 1, ?)""", (str(second),))
        conn.execute("""INSERT INTO tracks
            (id, album_id, artist_id, title, track_number, file_path)
            VALUES (3, 10, 1, 'Hourglass', 1, ?)""", (str(third),))
        conn.commit()
    worker = RepairWorker(database=db, transfer_folder=str(root))
    client = _Acoustid({path.name: {'mbid': 'hourglass-id', 'title': 'Hourglass',
                                   'artist': 'Lamb of God', 'score': 0.99}
                        for path in (first, second, third)})
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda _self: client))
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr(module, '_read_file_tags', lambda _path: {
        'title': 'Hourglass', 'artist': 'Lamb of God',
        'album': 'Ashes of the Wake', 'track_number': 1,
        'disc_number': 1, 'duration_ms': 200000,
    })
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *_args: [
        {'id': 'hourglass-id', 'name': 'Hourglass', 'track_number': 1,
         'disc_number': 1, 'duration_ms': 200000},
    ])
    context = JobContext(
        db=db, transfer_folder=str(root), config_manager=None,
        acoustid_client=client, create_finding=worker._create_finding,
        playlist_membership=lambda: {'2': ['Singles queue']},
    )
    LibraryReconciliationJob().scan(context)
    case_id = worker.get_review_cases()['items'][0]['case_id']
    findings = worker.get_review_case(case_id)['items']
    assert [item['finding_type'] for item in findings] == ['reconciliation_duplicate']
    copies = findings[0]['details']['duplicate_copies']
    assert {copy['track_id'] for copy in copies} == {'1', '2', '3'}
    assert next(copy for copy in copies if copy['track_id'] == '2')['playlists'] == [
        'Singles queue']

    outcome = worker.apply_review_decision(
        case_id, findings[0]['id'], evidence_token=findings[0]['evidence_token'],
        action='fix', fix_action='1')

    assert outcome['success'] is True
    assert first.exists() and second.exists() and third.exists()
    outcome = worker.fix_finding(findings[0]['id'], review_approved=True)
    assert outcome['success'], outcome
    assert first.exists()
    assert not second.exists()
    assert not third.exists()
    with db._get_connection() as conn:
        assert [row[0] for row in conn.execute(
            'SELECT id FROM tracks ORDER BY id').fetchall()] == ['1']


def test_requested_replacement_stays_visible_when_the_old_file_is_already_matched(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    item = next(row for row in case['items'] if row.get('file_path') == str(album.paths[0]))
    result = album.worker._fix_reconciliation_extra('track', 1, str(album.paths[0]),
        {**item['details'], '_fix_action': 'request_missing'})
    assert result['success'], result
    case = scan_case(album)
    waiting = [row for row in case['items'] if row['details'].get('replacement_request')]
    assert len(waiting) == 1
    assert waiting[0]['category'] == 'pending_fulfillment'
    assert waiting[0]['details']['fulfillment_state'] == 'wishlisted'
    assert case['album_plan']['counts'].get('missing', 0) == 0
    with album.db._get_connection() as conn:
        assert conn.execute('SELECT state FROM reconcile_fulfillment').fetchone()[0] == 'requested'


@pytest.mark.parametrize('mode', ['keep', 'quarantine'])
def test_existing_file_choice_is_staged_then_applied_with_both_files_guarded(tmp_path, monkeypatch, mode):
    album = build_album(tmp_path, monkeypatch, 'supported')
    from tests.reconciliation.support.audio import _minimal_flac
    path = album.folder / 'unidentified.flac'
    _minimal_flac(path, {'title': 'Unidentified', 'artist': 'Unknown', 'album': 'Northbound'})
    album.durations[str(path)] = 200000
    album.context.acoustid_client.matches[path.name] = []
    with album.db._get_connection() as conn:
        conn.execute("INSERT INTO tracks(id,album_id,artist_id,title,file_path) VALUES(3,10,1,'Unidentified',?)", (str(path),))
    case = scan_case(album)
    first = next(item for item in case['items'] if item['details'].get('track_id') == '1' or item['details'].get('track_id') == 1)
    alternative = first['details']['replacement_files'][0]
    choice = 'existing:' + mode + ':' + alternative['track_id']
    result = album.worker.apply_review_decision(case['case_id'], first['id'], evidence_token=first['evidence_token'],
        action='fix', fix_action=choice, schedule_refresh=False)
    assert result['success'], result
    assert all(path.exists() for path in album.paths)
    monkeypatch.setattr('core.library.playlist_membership.server_playlist_membership', lambda: {})
    result = album.worker._apply_reviewed_finding(first['id'])
    assert result['success'], result
    assert Path(alternative['file_path']).exists()
    assert album.paths[0].exists() == (mode == 'keep')
    with album.db._get_connection() as conn:
        assert conn.execute('SELECT file_path FROM reconcile_track_assignment').fetchone()[0] == alternative['file_path']


def test_exact_target_allows_small_duration_conflict_replacement(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    item = case['items'][0]
    details = item['details']
    details.update(file_duration_ms=162190, expected_duration_ms=156000)
    # Exercise server capability validation, independent of a severity threshold.
    with album.db._get_connection() as conn:
        conn.execute('UPDATE repair_findings SET finding_type=?, details_json=? WHERE id=?',
                     ('reconciliation_unresolved', json.dumps(details), item['id']))
    case = album.worker.get_review_case(case['case_id'])
    item = next(row for row in case['items'] if row['id'] == item['id'])
    result = album.worker.apply_review_decision(case['case_id'], item['id'], evidence_token=item['evidence_token'],
        action='fix', fix_action='replace', schedule_refresh=False)
    assert result['success'], result
    assert all(path.exists() for path in album.paths)


def test_duplicate_duration_annotation_preserves_preexisting_keeper_choice(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'duplicate')
    case = scan_case(album)
    item = next(row for row in case['items'] if row['finding_type'] == 'reconciliation_duplicate')
    with album.db._get_connection() as conn:
        details = json.loads(conn.execute('SELECT details_json FROM repair_findings WHERE id=?', (item['id'],)).fetchone()[0])
        for key in ('expected_title', 'expected_duration_ms', 'expected_track_number', 'expected_disc_number'):
            details.pop(key)
        conn.execute('UPDATE repair_findings SET details_json=? WHERE id=?', (json.dumps(details), item['id']))
    current = album.worker.get_review_case(case['case_id'])
    item = next(row for row in current['items'] if row['id'] == item['id'])
    assert album.worker.apply_review_decision(case['case_id'], item['id'], evidence_token=item['evidence_token'], action='fix', fix_action=item['details']['recommended_keeper'])['success']
    after = scan_case(album)
    item = next(row for row in after['items'] if row['id'] == item['id'])
    assert item['details']['plan_approved']
    assert item['details']['expected_duration_ms'] == 200000


def test_full_scan_restores_request_step_for_saved_replacement_from_previous_build(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'recording_conflict')
    case = scan_case(album)
    item = next(item for item in case['items'] if item['file_path'] == str(album.paths[0]))
    assert album.worker.apply_review_decision(case['case_id'], item['id'], evidence_token=item['evidence_token'], action='fix', fix_action='replace')['success']
    with album.db._get_connection() as conn:
        conn.execute("DELETE FROM repair_findings WHERE finding_type='reconciliation_missing'")
    album.worker._sync_all_planned_replacement_requests()
    after = album.worker.get_review_case(case['case_id'])
    assert any(item['details'].get('replacement_for_finding') for item in after['items'])
    assert not after['plan']['can_apply_ready']


def test_playlist_override_is_scoped_to_saved_action_and_current_references(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'recording_conflict')
    case = scan_case(album)
    finding = next(row for row in case['items'] if row.get('file_path') == str(album.paths[0]))
    assert album.worker.apply_review_decision(case['case_id'], finding['id'], evidence_token=finding['evidence_token'], action='fix', fix_action='quarantine')['success']
    case = album.worker.get_review_case(case['case_id'])
    finding = next(row for row in case['items'] if row['id'] == finding['id'])
    refs = {str(finding['details']['track_id']): [{'id': 'playlist', 'title': 'My playlist'}]}
    monkeypatch.setattr(playlist_membership, 'server_playlist_references', lambda **kwargs: refs)
    assert not album.worker.acknowledge_review_playlists(case['case_id'], finding['id'], evidence_token=finding['evidence_token'], playlist_ids=['old'])['success']
    result = album.worker.acknowledge_review_playlists(case['case_id'], finding['id'], evidence_token=finding['evidence_token'], playlist_ids=['playlist'])
    assert result['success'], result
    after = album.worker.get_review_case(case['case_id'])
    details = next(row for row in after['items'] if row['id'] == finding['id'])['details']
    assert not album.worker._playlist_removal_blocked(details)
    refs[str(details['track_id'])].append({'id': 'new', 'title': 'New playlist'})
    assert album.worker._playlist_removal_blocked(details)
    assert album.paths[0].exists()


def test_playlist_override_is_consumed_by_guarded_apply(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'recording_conflict')
    case = scan_case(album)
    finding = next(row for row in case['items'] if row.get('file_path') == str(album.paths[0]))
    assert album.worker.apply_review_decision(case['case_id'], finding['id'], evidence_token=finding['evidence_token'], action='fix', fix_action='quarantine')['success']
    case = album.worker.get_review_case(case['case_id'])
    finding = next(row for row in case['items'] if row['id'] == finding['id'])
    monkeypatch.setattr(playlist_membership, 'server_playlist_references', lambda **kwargs: {str(finding['details']['track_id']): [{'id': 'p', 'title': 'Playlist'}]})
    assert album.worker.acknowledge_review_playlists(case['case_id'], finding['id'], evidence_token=finding['evidence_token'], playlist_ids=['p'])['success']
    result = album.worker._apply_reviewed_finding(finding['id'])
    assert result['success'], result
    assert not album.paths[0].exists()
    assert album.paths[1].exists()


def test_search_uses_names_and_ranks_album_matches_without_playlist_noise():
    db = _Database()
    for fid, album, artist, extra in (
        (1, 'North', 'Matchbox Twenty', {'playlist_tracks': [{'title': 'Bittersweet Electronic'}]}),
        (2, 'TRON: Legacy', 'Daft Punk', {}),
        (3, 'Other album', 'Artist', {'expected_title': 'Tron theme'}),
        (4, 'Études, No. 5', 'Philip Glass', {}),
        (5, 'Forty Licks', 'Rolling Stones', {'expected_title': 'Love Is Strong'}),
    ):
        db.add(fid, f'/music/{fid}/track.flac', {'album_id': fid, 'album_title': album, 'artist_name': artist, **extra})
    result = list_cases(db, q='tron', limit=1)
    assert result['total'] == 2
    assert result['items'][0]['album'] == 'TRON: Legacy'
    assert len(result['case_ids']) == 2
    assert list_cases(db, q='tron legacy daft')['total'] == 1
    assert list_cases(db, q='etudes no. 5')['items'][0]['album'] == 'Études, No. 5'
    assert list_cases(db, q='bittersweet')['total'] == 0


def test_saved_failed_removal_is_an_execution_blocker_not_an_audio_decision():
    plan = classify_case([{'id': 1, 'finding_type': 'reconciliation_extra',
        'last_error': 'This copy is in a playlist; review that reference first',
        'details': {'plan_approved': True, 'audio_conflict': 'different_recording',
                    'planned_choice': {'action': 'fix', 'fix_action': 'quarantine'}}}])
    assert not plan['can_apply_ready']
    assert plan['decisions'][0]['category'] == 'apply_blockers'
    assert plan['decisions'][0]['label'] == 'Resolve blocked actions'


def test_new_replacement_of_delivered_file_resets_receipt_but_retry_does_not(tmp_path):
    db = Database(tmp_path / 'request.db')
    path = tmp_path / 'delivered.flac'; path.write_bytes(b'audio')
    details = {'album_id': 2, 'profile_id': 1, 'album_folder': str(tmp_path),
               'source': 'musicbrainz', 'release_id': 'release', 'replacement_request': True}
    slot = {'source_track_id': 'slot'}
    record_request(db, details, slot)
    with db._get_connection() as conn:
        conn.execute('INSERT INTO wishlist_tracks VALUES (1, ?, ?)', ('slot', json.dumps({'id': 'slot', 'provider': 'musicbrainz', 'album': {'id': 'release'}})))
    published_wishlist_track(db, 'slot', [1], str(path))
    record_request(db, details, slot)
    with db._get_connection() as conn:
        assert conn.execute('SELECT state FROM reconcile_fulfillment').fetchone()[0] == 'published'
    record_request(db, {**details, 'replacement_for_path': str(path)}, slot)
    with db._get_connection() as conn:
        assert conn.execute('SELECT state,published_path FROM reconcile_fulfillment').fetchone() == ('requested', '')


def test_playlist_references_have_real_ids_and_recheck_keeps_saved_choice(monkeypatch):
    playlist_membership.clear_cache()
    tracks = [SimpleNamespace(ratingKey='track')]
    client = SimpleNamespace(base_url='https://music.example', ensure_connection=lambda: True,
        _make_request=lambda path, params=None: {'playlists': {'playlist': [{'id': 'list-1', 'name': '88 Keys'}]}}
        if path == 'getPlaylists' else {'playlist': {'entry': [{'id': track.ratingKey} for track in tracks]}})
    monkeypatch.setattr(playlist_membership, '_active_server_and_client', lambda: ('navidrome', client))
    db = _Database()
    db.add(1, '/music/file.flac', {'track_id': 'track'})
    worker = RepairWorker.__new__(RepairWorker); worker.db = db
    worker.get_review_case = lambda *a, **k: {'items': [{'id': 1, 'file_path': '/music/file.flac', 'details': {'track_id': 'track'}, 'last_error': 'This copy is in a playlist'}]}
    refs = worker.review_playlist_references('case', '/music/file.flac')['playlists']
    assert refs == [{'id': 'list-1', 'title': '88 Keys', 'server': 'navidrome', 'url': 'https://music.example/app/#/playlist/list-1/show'}]
    raw = db.conn.execute('SELECT details_json FROM repair_findings').fetchone()[0]
    db.conn.execute("UPDATE repair_findings SET last_error='This copy is in a playlist'")
    tracks.clear()
    assert worker.review_playlist_references('case', '/music/file.flac', refresh=True)['playlists'] == []
    assert db.conn.execute('SELECT details_json,last_error FROM repair_findings').fetchone() == (raw, None)
    with pytest.raises(ValueError):
        worker.review_playlist_references('case', '/elsewhere.flac', refresh=True)
    playlist_membership.clear_cache()


@pytest.mark.parametrize('failed_endpoint', ['getPlaylists', 'getPlaylist'])
def test_failed_playlist_recheck_does_not_erase_cached_membership(failed_endpoint):
    playlist_membership.clear_cache()
    client = SimpleNamespace(base_url='https://music.example', ensure_connection=lambda: True,
        _make_request=lambda path, params=None: None if path == failed_endpoint else
        {'playlists': {'playlist': [{'id': 'list-1', 'name': '88 Keys'}]}})
    playlist_membership._cache.update(at=time.monotonic(), server='navidrome', map={'track': ['88 Keys']})
    with pytest.raises(ValueError, match='complete playlist information'):
        playlist_membership.server_playlist_references(refresh=True, resolve=lambda: ('navidrome', client))
    assert playlist_membership._cache['map'] == {'track': ['88 Keys']}
    playlist_membership.clear_cache()


def test_jellyfin_reference_check_uses_playlist_entries_and_reads_all_pages():
    calls = []
    def request(path, params):
        calls.append((path, params))
        if path == '/Users/user/Items':
            return {'Items': [{'Id': 'list-1', 'Name': 'Long playlist'}]}
        assert path == '/Playlists/list-1/Items'
        assert params['UserId'] == 'user'
        return {'Items': [{'Id': str(i)} for i in range(1000)] if params['StartIndex'] == 0 else [{'Id': 'last'}]}
    client = SimpleNamespace(user_id='user', ensure_connection=lambda: True, _make_request=request)
    rows = list(playlist_membership._read_reference_rows('jellyfin', client))
    assert rows[0][:2] == ('list-1', 'Long playlist')
    assert len(rows[0][2]) == 1001 and rows[0][2][-1] == 'last'
    assert len(calls) == 3
