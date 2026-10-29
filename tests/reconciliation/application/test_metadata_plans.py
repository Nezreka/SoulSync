"""application / metadata plans regression contracts."""
from __future__ import annotations

import json
import time

import pytest
from mutagen.flac import FLAC

import core.reconciliation.selection as reconciliation_selection
from core.reconciliation import search as search
from core.reconciliation.evidence import file_identity
from core.reconciliation.queue import (
    get_case,
    list_cases,
    ready_batch_preview,
)
from core.reconciliation.selection import (
    _distinctive_position_conflict,
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
from tests.reconciliation.support.queue import _Database
from tests.reconciliation.support.scenarios import (
    _album,
    _metadata_recommendation_album,
)


def test_metadata_inspector_can_change_and_reset_plan_assignment_without_writing_file(tmp_path):
    path = tmp_path / 'Song.flac'
    path.write_bytes(b'original audio')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    worker = RepairWorker(database=db)
    worker._reconciliation_file_guard = lambda *_args: None
    worker._reconciliation_release_guard = lambda *_args: None
    worker._schedule_review_refresh = lambda *_args, **_kwargs: True
    details = {'album_id': 'album', 'album_folder': str(tmp_path),
               'source': 'musicbrainz', 'release_id': 'release',
               'size_bytes': path.stat().st_size, 'mtime_ns': path.stat().st_mtime_ns,
               'assignment_options': [{'key': 'id:recording:1:1', 'title': 'Song'}],
               'changes': {'title': {'old': 'Old', 'new': 'Song'}}}
    worker._create_finding(job_id='library_reconciliation', finding_type='reconciliation_metadata',
                           severity='warning', entity_type='track', entity_id='track',
                           file_path=str(path), title='Update Song', description='Review proposed title', details=details)
    case_id = worker.get_review_cases()['items'][0]['case_id']
    item = worker.get_review_case(case_id)['items'][0]
    for choice, expected in [('assign:id:recording:1:1', 1), ('assign:auto', 0)]:
        result = worker.apply_review_decision(case_id, item['id'],
            evidence_token=item['evidence_token'], action='fix', fix_action=choice)
        assert result['success'] and result['refresh_queued']
        assert path.read_bytes() == b'original audio'
        with db._get_connection() as conn:
            assert conn.execute('SELECT COUNT(*) FROM reconcile_track_assignment').fetchone()[0] == expected
            assert conn.execute('SELECT status FROM repair_findings WHERE id=?', (item['id'],)).fetchone()[0] == 'pending'


def test_version_metadata_does_not_hide_explicit_competing_version():
    from core.reconciliation.policy import supported_version_metadata
    details = {'title_difference_kind': 'version', 'confidence': 'clear',
               'recording_mbid': 'live', 'expected_title': 'Song (live)',
               'current_tags': {'duration_ms': 200000}, 'expected_duration_ms': 200000,
               'acoustid': {'status': 'ok', 'recordings': [
                   {'mbid': 'live', 'title': 'Song', 'score': 1}]}}
    assert supported_version_metadata(details)
    details['acoustid']['recordings'].append({'mbid': 'remix', 'title': 'Song (remix)', 'score': 1})
    assert not supported_version_metadata(details)


def test_pending_download_does_not_block_independent_ready_metadata():
    from core.reconciliation.queue import queue_summary
    db = _Database()
    common = {'album_folder': '/music/Waiting'}
    db.add(1, '/music/Waiting/01.flac', {**common, 'changes': {'title': {'old': 'one', 'new': 'One'}}},
           job_id='library_reconciliation', kind='reconciliation_metadata')
    db.add(2, None, {**common, 'fulfillment_state': 'wishlisted'},
           job_id='library_reconciliation', kind='reconciliation_missing')
    totals = queue_summary(db, job_id='library_reconciliation')
    assert totals['waiting'] == 0 and totals['ready'] == totals['ready_total'] == 1
    assert ready_batch_preview(db)['action_count'] == 1
    case = get_case(db, list_cases(db)['items'][0]['case_id'])
    assert case['plan']['status'] == 'ready'
    assert case['plan']['can_apply_ready']


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_distinctive_version_position_blocks_shorter_release_retag():
    files = [{'tags': {'title': 'Come Back Down', 'track_number': 16},
              'path': '/music/16 - Come Back Down.mp3'}]
    selected = (10, 10, 0, 'musicbrainz', 'short',
                [{'title': 'Come Back Down', 'track_number': 1}],
                {'pairs': [{'file_index': 0, 'slot_index': 0}]})
    alternate = (9, 9, 0, 'musicbrainz', 'long',
                 [{'title': 'Come Back Down (live)', 'track_number': 16}],
                 {'pairs': [{'file_index': 0, 'slot_index': 0}]})
    files[0]['tags']['title'] = 'Come Back Down (live)'
    assert _distinctive_position_conflict(files, selected, [alternate])


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_apply_guard_accepts_current_plan_over_older_unlocked_canonical(tmp_path):
    path = tmp_path / '01.flac'
    path.write_bytes(b'audio')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    with db._get_connection() as conn:
        conn.execute('''INSERT INTO reconcile_album_plans
            (case_id, album_id, album_folder, source, release_id,
             evidence_signature, plan_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            ('case', '10', str(tmp_path), 'musicbrainz', 'chosen-release',
             'evidence', '{}'))
        conn.commit()
    worker = RepairWorker(database=db, transfer_folder=str(tmp_path))
    details = {'album_id': 10, 'album_folder': str(tmp_path),
               'source': 'musicbrainz', 'release_id': 'chosen-release'}
    assert worker._reconciliation_release_guard(details) is None
    assert db.get_album_canonical(10)['album_id'] == 'chosen-release'
    assert not db.get_album_canonical(10)['locked']
    assert worker._reconciliation_release_guard({**details, 'release_id': 'stale'})


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_mixed_album_metadata_requires_review_and_can_clear_only_release_ids(tmp_path, monkeypatch):
    from core.reconciliation.policy import _finding_tier
    db, worker, context, paths, _ = _metadata_recommendation_album(tmp_path, monkeypatch, mixed=True)
    result = LibraryReconciliationJob().scan(context)
    assert not result.errors
    case_id = worker.get_review_cases()['items'][0]['case_id']
    case = worker.get_review_case(case_id)
    items = case['items']
    rows = [row for row in case['album_plan']['assignments'] if row.get('file_path')]
    assert len(rows) == 2
    assert all(row['confidence'] == 'clear' and row['status'] == 'change'
               and row['action_state'] == 'manual' for row in rows)
    assert not case['plan']['can_apply_ready']
    metadata = [item for item in items if item['finding_type'] == 'reconciliation_metadata']
    assert len(metadata) == 2
    for item in metadata:
        details = item['details']
        assert _finding_tier(item)[0] == 'manual'
        assert details['changes']['musicbrainz_release_id']['new'] == ''
        assert details['album_consistency']['album']
        assert _finding_tier({**item, 'details': {**details, 'plan_approved': True}})[0] == 'ready'
        applied = worker._fix_reconciliation_metadata('track', details['track_id'], item['file_path'], details)
        assert applied['success'], applied
        tags = FLAC(item['file_path'])
        assert 'musicbrainz_albumid' not in tags
        assert tags['musicbrainz_trackid']  # Track identity is retained.
        assert tags['album'] == ['Ashes of the Wake (Deluxe)']


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize('container', ['id3', 'vorbis', 'mp4'])
def test_explicit_id_clear_preserves_recording_and_unrelated_tags(container):
    from types import SimpleNamespace

    from mutagen.id3 import ID3, TXXX, UFID

    from core.tag_writer import _clear_musicbrainz_ids
    if container == 'id3':
        tags = ID3()
        tags.add(TXXX(desc='MUSICBRAINZ_RELEASEID', text=['wrong-release']))
        tags.add(TXXX(desc='MusicBrainz Album Id', text=['wrong-release']))
        tags.add(UFID(owner='http://musicbrainz.org', data=b'recording'))
        tags.add(TXXX(desc='OTHER', text=['keep']))
    else:
        prefix = '----:com.apple.iTunes:' if container == 'mp4' else ''
        tags = {prefix + 'MusicBrainz Album Id': ['wrong-release'],
                prefix + 'MusicBrainz Track Id': ['recording'], 'OTHER': ['keep']}
    audio = SimpleNamespace(tags=tags)
    _clear_musicbrainz_ids(audio, ['musicbrainz_release_id'])
    assert len(tags) == 2
    assert any('recording' in str(value) for value in tags.values())
    assert any('keep' in str(value) for value in tags.values())
    with pytest.raises(ValueError):
        _clear_musicbrainz_ids(audio, ['title'])


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_audio_backed_retag_writes_real_flac_and_track_row(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    root.mkdir()
    path = root / '01 - Laid to Rest.flac'
    _minimal_flac(path, {'title': 'Laid to Rest', 'artist': 'Lamb of God',
                         'album': 'Ashes of the Wake', 'tracknumber': '1',
                         'musicbrainz_albumid': 'old-pressing'})
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    assert db.set_album_canonical(10, 'musicbrainz', 'ashes-mb-release', 0.9, locked=True)
    worker = RepairWorker(database=db)
    client = _Acoustid({path.name: {'mbid': 'hourglass-id', 'title': 'Hourglass',
                                   'artist': 'Lamb of God', 'score': 0.99}})
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda _self: client))
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *_args: [
        {'id': 'hourglass-id', 'name': 'Hourglass',
         'disambiguation': 'alternate studio recording',
         'artists': [{'name': 'Lamb of God'}],
         'track_number': 2, 'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source',
                        lambda *_args: {'name': 'Ashes of the Wake'})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)
    result = LibraryReconciliationJob().scan(context)
    assert result.findings_created == 1
    case_id = worker.get_review_cases()['items'][0]['case_id']
    finding = worker.get_review_case(case_id)['items'][0]
    assert finding['finding_type'] == 'reconciliation_metadata'
    assert finding['details']['changes']['title']['new'] == 'Hourglass'
    assert finding['details']['changes']['musicbrainz_recording_id']['new'] == 'hourglass-id'
    assert finding['details']['changes']['library_recording_disambiguation']['new'] == \
        'alternate studio recording'
    assert finding['details']['changes']['filename']['new'] == '02 - Hourglass.flac'
    staged = worker.apply_review_decision(case_id, finding['id'],
        evidence_token=finding['evidence_token'], action='fix')
    assert staged['success']
    assert path.exists() and FLAC(str(path))['title'][0] == 'Laid to Rest'
    assert db.set_album_canonical(10, 'musicbrainz', 'different-release', 0.9, locked=True)
    stale = worker.fix_finding(finding['id'], review_approved=True)
    assert stale['success'] is False and 'rescan' in stale['error']
    assert FLAC(str(path))['title'][0] == 'Laid to Rest'
    assert db.set_album_canonical(10, 'musicbrainz', 'ashes-mb-release', 0.9, locked=True)
    finding = worker.get_review_case(case_id)['items'][0]
    with db._get_connection() as conn:
        conn.execute("UPDATE tracks SET recording_disambiguation = 'changed after scan' "
                     "WHERE id = 1")
        conn.commit()
    stale_identity = worker.fix_finding(finding['id'], review_approved=True)
    assert stale_identity['success'] is False and 'rescan' in stale_identity['error']
    with db._get_connection() as conn:
        conn.execute('UPDATE tracks SET recording_disambiguation = NULL WHERE id = 1')
        conn.commit()
    finding = worker.get_review_case(case_id)['items'][0]
    target_path = root / '02 - Hourglass.flac'
    target_path.write_bytes(b'collision')
    collision = worker.fix_finding(finding['id'], review_approved=True)
    assert collision['success'] is False and 'already exists' in collision['error']
    assert FLAC(str(path))['title'][0] == 'Laid to Rest'
    target_path.unlink()
    finding = worker.get_review_case(case_id)['items'][0]
    before = path.stat()
    with db._get_connection() as conn:
        conn.execute('''INSERT INTO reconcile_track_assignment
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            ('10', 'musicbrainz', 'ashes-mb-release', str(path), 'chosen-slot',
             before.st_size, before.st_mtime_ns, 'verified-audio'))
    applied = worker.fix_finding(finding['id'], review_approved=True)
    assert applied['success'], applied
    after = target_path.stat()
    with db._get_connection() as conn:
        assignment = conn.execute('''SELECT file_path, slot_key, size_bytes,
            mtime_ns, fingerprint_key FROM reconcile_track_assignment''').fetchone()
    assert tuple(assignment) == (str(target_path), 'chosen-slot', after.st_size,
                                 after.st_mtime_ns, 'verified-audio')
    assert FLAC(str(target_path))['musicbrainz_albumid'][0] == 'ashes-mb-release'
    assert not path.exists()
    assert FLAC(str(target_path))['title'][0] == 'Hourglass'
    with db._get_connection() as conn:
        row = conn.execute('''SELECT title, track_number, musicbrainz_recording_id,
                                     recording_disambiguation, file_path
                              FROM tracks WHERE id = 1''').fetchone()
    assert tuple(row) == ('Hourglass', 2, 'hourglass-id',
                          'alternate studio recording', str(target_path))


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_catalog_album_qualifier_is_cleaned_without_retagging_files(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    root.mkdir()
    path = root / '01 - Rio.flac'
    _minimal_flac(path, {'title': 'Rio', 'artist': 'Duran Duran',
                         'album': 'Rio', 'tracknumber': '1'})
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    with db._get_connection() as conn:
        conn.execute("UPDATE artists SET name = 'Duran Duran' WHERE id = 1")
        conn.execute("UPDATE albums SET title = 'Rio (Columbia House)', "
                     "canonical_album_id = 'rio-release' WHERE id = 10")
        conn.execute("UPDATE tracks SET title = 'Rio' WHERE id = 1")
        conn.commit()
    worker = RepairWorker(database=db)
    client = _Acoustid({path.name: {
        'mbid': 'rio-id', 'title': 'Rio', 'artist': 'Duran Duran', 'score': 0.99,
    }})
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda _self: client))
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *_args: [
        {'id': 'rio-id', 'name': 'Rio', 'artists': [{'name': 'Duran Duran'}],
         'track_number': 1, 'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda *_args: {
        'name': 'Rio (Remastered)', 'artists': [{'name': 'Duran Duran'}]})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)

    LibraryReconciliationJob().scan(context)

    case_id = worker.get_review_cases()['items'][0]['case_id']
    review_case = worker.get_review_case(case_id)
    assert review_case['album'] == 'Rio'
    assert review_case['decisions'][0]['label'] == 'Update library album title'
    assert review_case['items'][0]['details']['changes'] == {
        'library_album_title': {'old': 'Rio (Columbia House)', 'new': 'Rio'},
    }


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_filename_and_library_row_repair_when_file_tags_are_already_correct(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    root.mkdir()
    path = root / '01 - Wrong.flac'
    _minimal_flac(path, {'title': 'Hourglass', 'artist': 'Lamb of God',
                         'album': 'Ashes of the Wake', 'tracknumber': '2'})
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    worker = RepairWorker(database=db)
    client = _Acoustid({path.name: {'mbid': 'hourglass-id', 'title': 'Hourglass',
                                   'artist': 'Lamb of God', 'score': 0.99}})
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda _self: client))
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *_args: [
        {'id': 'hourglass-id', 'name': 'Hourglass', 'track_number': 2,
         'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda *_args: {
        'name': 'Ashes of the Wake'})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)
    LibraryReconciliationJob().scan(context)
    case_id = worker.get_review_cases()['items'][0]['case_id']
    finding = worker.get_review_case(case_id)['items'][0]
    assert finding['details']['db_data'] == {}
    assert finding['details']['db_track_updates']['title'] == 'Hourglass'
    result = worker.apply_review_decision(case_id, finding['id'],
                                          evidence_token=finding['evidence_token'], action='fix')
    assert result['success'], result
    assert path.exists()
    assert worker.fix_finding(finding['id'], review_approved=True)['success']
    target = root / '02 - Hourglass.flac'
    assert target.exists() and not path.exists()
    assert FLAC(str(target))['title'][0] == 'Hourglass'
    with db._get_connection() as conn:
        row = conn.execute('SELECT title, track_number, file_path FROM tracks WHERE id = 1').fetchone()
    assert tuple(row) == ('Hourglass', 2, str(target))


def test_tag_writer_reuses_the_open_file_for_placeholder_guards(tmp_path, monkeypatch):
    import core.tag_writer as writer
    from tests.reconciliation.support.audio import _minimal_flac
    path = tmp_path / 'song.flac'
    _minimal_flac(path, {'title': 'Song', 'artist': 'Artist', 'album': 'Album'})
    original, opens = writer.MutagenFile, []
    def open_audio(path):
        opens.append(path)
        return original(path)
    monkeypatch.setattr(writer, 'MutagenFile', open_audio)
    result = writer.write_tags_to_file(str(path), {'title': 'Corrected'}, embed_cover=False)
    assert result['success'], result
    assert opens.count(str(path)) == 1
    assert len(opens) == 2  # The atomic writer separately validates its new temporary file.


def test_reviewed_audio_is_reused_through_tag_write_and_followup_scan(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    client = album.context.acoustid_client
    decoded = client.fingerprints
    for item in case['items']:
        assert item['details']['file_identity']
        result = album.worker._apply_reviewed_finding(item['id'])
        assert result['success'], result
    assert client.fingerprints == decoded
    assert not LibraryReconciliationJob().scan(album.context).errors
    assert client.fingerprints == decoded
    assert album.worker.get_review_cases()['items'] == []


def test_artist_rewrite_removes_stale_separate_list(tmp_path):
    from mutagen.flac import FLAC

    from core.library.release_identity import read_release_evidence
    from core.tag_writer import write_tags_to_file
    from tests.reconciliation.support.audio import _minimal_flac
    path=tmp_path/'song.flac'; _minimal_flac(path, {})
    tags=FLAC(path);tags['artist']='No Doubt';tags['artists']=['Fred the Godson','KIDD'];tags.save()
    assert read_release_evidence(str(path))['artists_list'] == ['Fred the Godson','KIDD']
    assert write_tags_to_file(str(path),{'track_artist':'No Doubt'},embed_cover=False)['success']
    tags=FLAC(path)
    assert tags['artist'] == ['No Doubt']
    assert not tags.get('artists')


@pytest.mark.parametrize('stored,changed', [('The Steve Miller Band',False),('Fred the Godson',True)])
def test_secondary_artist_list_ignores_article_spelling_but_corrects_other_artists(stored,changed):
    from core.library.retag_planner import plan_track
    plan=plan_track({'artist':'Steve Miller Band','artists_list':[stored]},
        {'artists':[{'name':'Steve Miller Band'}]}, {})
    assert ('artists_list' in plan['changes']) is changed


def test_remove_all_files_offers_stop_collecting_and_retires_only_after_apply(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    assert not case.get('can_stop_collecting')
    for item in case['items']:
        result = album.worker.apply_review_decision(case['case_id'], item['id'], evidence_token=item['evidence_token'], action='fix', fix_action='quarantine')
        assert result['success'], result
    case = album.worker.get_review_case(case['case_id'])
    assert case['can_stop_collecting']
    outcome = album.worker.stop_collecting_review_album(case['case_id'], case['plan_signature'])
    assert outcome['success'], outcome
    assert all(path.exists() for path in album.paths)
    assert not album.worker._retire_removed_review_album(case['case_id'])
    for item in case['items']:
        assert album.worker._apply_reviewed_finding(item['id'])['success']
    assert not any(path.exists() for path in album.paths)
    assert album.worker._retire_removed_review_album(case['case_id'])
    assert album.worker.get_review_case(case['case_id'])['status'] == 'completed'


def test_mixed_release_ids_are_ready_when_one_edition_clearly_fits_every_file(tmp_path, monkeypatch):
    from mutagen.flac import FLAC

    from tests.reconciliation.support.acceptance import OTHER_RELEASE
    album = build_album(tmp_path, monkeypatch, 'supported')
    tags = FLAC(str(album.paths[1]))
    tags['musicbrainz_albumid'] = OTHER_RELEASE
    tags.save()
    case = scan_case(album)
    assert case['plan']['can_apply_ready']
    assert any(row['details'].get('album_consistency', {}).get('musicbrainz_release_id') for row in case['items'])
    assert all(not row['details'].get('metadata_review_reason') for row in case['items'])


def test_blocked_apply_distinguishes_blocker_from_unattempted_dependent_actions(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    attempted = []
    def blocked(fid, **kwargs):
        attempted.append(fid)
        return {'success': False, 'blocked': True, 'error': 'This copy is in a playlist'}
    monkeypatch.setattr(album.worker, '_apply_reviewed_finding', blocked)
    result = album.worker.start_review_safe(case_id=case['case_id'])
    assert result['started'], result
    deadline = time.monotonic() + 5
    while album.worker._bulk_fix_state['running'] and time.monotonic() < deadline:
        time.sleep(.01)
    state = album.worker.get_bulk_fix_status()
    assert not state['running']
    assert state['failed'] == 0 and state['fixed'] == 0
    assert state['blocked'] == 1 and state['not_attempted'] == len(case['items']) - 1
    assert len(attempted) == 1


def test_stale_apply_names_file_and_changed_attribute(tmp_path):
    path = tmp_path / 'Song.flac'
    path.write_bytes(b'audio')
    stat = path.stat()
    details = {'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'file_identity': file_identity(stat)}
    worker = RepairWorker.__new__(RepairWorker)
    assert worker._reconciliation_file_guard(str(path), details) is None
    path.write_bytes(b'changed audio')
    error = worker._reconciliation_file_guard(str(path), details)
    assert 'Song.flac changed after the scan' in error
    assert 'file size' in error and 'Recheck this album' in error


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_source_without_all_track_numbers_does_not_invent_disc_totals(tmp_path, monkeypatch):
    db, worker, context, paths, _ = _metadata_recommendation_album(tmp_path, monkeypatch)
    read_tracks = reconciliation_selection.get_album_tracks_for_source
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *args, **kwargs: [
        {**track, 'track_number': None if index else 1}
        for index, track in enumerate(read_tracks(*args, **kwargs))])
    result = LibraryReconciliationJob().scan(context)
    assert not result.errors
    with db._get_connection() as conn:
        items = [json.loads(row[0]) for row in conn.execute(
            "SELECT details_json FROM repair_findings WHERE finding_type='reconciliation_metadata'")]
    assert items
    assert all(not item['db_data'].get('track_count') for item in items)


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize('source', ['itunes', 'deezer'])
def test_missing_release_id_does_not_invalidate_another_files_identity(tmp_path, monkeypatch, source):
    db, worker, context, paths, _ = _metadata_recommendation_album(tmp_path, monkeypatch)
    with db._get_connection() as conn:
        conn.execute('UPDATE albums SET canonical_source=?', (source,))
        conn.commit()
    audio = FLAC(str(paths[1]))
    del audio['musicbrainz_albumid']
    audio.save()
    assert not LibraryReconciliationJob().scan(context).errors
    case = worker.get_review_case(worker.get_review_cases()['items'][0]['case_id'])
    metadata = [item for item in case['items'] if item['finding_type'] == 'reconciliation_metadata']
    assert len(metadata) == 2
    for item in metadata:
        assert 'musicbrainz_release_id' not in item['details']['album_consistency']
        assert 'musicbrainz_release_id' not in item['details']['changes']
        assert not item['details']['db_data'].get('clear_musicbrainz_ids')
        assert item['review_tier'] == 'ready'
        # Applying unrelated supported edits must also preserve the stored ID.
        result = worker._fix_reconciliation_metadata('track', item['details']['track_id'],
                                                     item['file_path'], item['details'])
        assert result['success'], result
    assert FLAC(str(paths[0]))['musicbrainz_albumid'] == ['second-release']
    assert 'musicbrainz_albumid' not in FLAC(str(paths[1]))


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize(('release_id', 'locked', 'expected'), [
    ('second-release', False, ['second-release', 'second-release']),
    ('new-release', False, ['second-release', None]),
    ('new-release', True, ['new-release', 'new-release']),
])
def test_sparse_release_tags_respect_selected_identity_and_explicit_pin(
        tmp_path, monkeypatch, release_id, locked, expected):
    db, worker, context, paths, _ = _metadata_recommendation_album(tmp_path, monkeypatch)
    assert db.set_album_canonical(10, 'musicbrainz', release_id, score=1, locked=locked)
    audio = FLAC(str(paths[1]))
    del audio['musicbrainz_albumid']
    audio.save()
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda *_args, **_kwargs: {
        'name': 'Ashes of the Wake (Deluxe)', 'artists': [{'name': 'Lamb of God'}],
        'musicbrainz_release_id': release_id})
    assert not LibraryReconciliationJob().scan(context).errors
    cases = worker.get_review_cases()['items']
    if release_id == 'new-release' and not locked:
        assert cases == []  # No tag repair is justified by the other pressing.
        items = []
    else:
        case = worker.get_review_case(cases[0]['case_id'])
        items = case['items']
        assert not any(item['finding_type'] == 'reconciliation_release' for item in items)
    for item in items:
        if item['finding_type'] == 'reconciliation_metadata':
            assert 'musicbrainz_release_id' not in item['details']['album_consistency']
            result = worker._fix_reconciliation_metadata('track', item['details']['track_id'],
                                                         item['file_path'], item['details'])
            assert result['success'], result
    assert [FLAC(str(path)).get('musicbrainz_albumid', [None])[0] for path in paths] == expected


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_album_consistency_distinguishes_missing_values_from_conflicts():
    from core.reconciliation.metadata import album_consistency
    files = [{'tags': {'album': 'Album', 'musicbrainz_release_id': 'first'}},
             {'tags': {'album': '  Album  ', 'musicbrainz_release_id': ''}},
             {'tags': {}}]
    assert album_consistency(files) == {}
    files.append({'tags': {'album': 'Different album', 'musicbrainz_release_id': 'second'}})
    assert album_consistency(files) == {
        'album': [{'value': 'Album', 'files': 2}, {'value': 'Different album', 'files': 1}],
        'musicbrainz_release_id': [{'value': 'first', 'files': 1}, {'value': 'second', 'files': 1}],
    }


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_reviewed_soundtrack_fragment_can_join_the_anchor_album(tmp_path):
    root = tmp_path / 'music'
    root.mkdir()
    path = root / '12 - Living Waters.flac'
    path.write_bytes(b'audio fixture')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    with db._get_connection() as conn:
        conn.execute("""INSERT INTO albums
            (id, title, artist_id, server_source)
            VALUES (11, 'The Truman Show', 1, 'test')""")
        conn.execute('UPDATE tracks SET album_id = 11 WHERE id = 1')
        conn.commit()
    worker = RepairWorker(database=db, transfer_folder=str(root))
    stat = path.stat()
    details = {
        'album_id': '10',
        'source': 'spotify',
        'release_id': 'ashes-release',
        'track_id': 1,
        'size_bytes': stat.st_size,
        'mtime_ns': stat.st_mtime_ns,
        'current_library_metadata': {'album_id': '11'},
        'db_track_updates': {'album_id': '10'},
    }

    result = worker._fix_reconciliation_metadata('track', '1', str(path), details)

    assert result['success'], result
    with db._get_connection() as conn:
        assert conn.execute('SELECT album_id FROM tracks WHERE id = 1').fetchone()[0] == '10'


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_exact_release_title_repairs_rated_r_album_and_file_tags(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    root.mkdir()
    path = root / '01 - Feel Good Hit of the Summer.flac'
    _minimal_flac(path, {'title': 'Feel Good Hit of the Summer',
                         'artist': 'Queens of the Stone Age', 'album': 'R',
                         'tracknumber': '1'})
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    with db._get_connection() as conn:
        conn.execute("UPDATE artists SET name = 'Queens of the Stone Age' WHERE id = 1")
        conn.execute("UPDATE albums SET title = 'R', canonical_album_id = 'rated-r-release' "
                     "WHERE id = 10")
        conn.execute("UPDATE tracks SET title = 'Feel Good Hit of the Summer' WHERE id = 1")
        conn.commit()
    worker = RepairWorker(database=db)
    client = _Acoustid({path.name: {'mbid': 'feel-good-id',
                                   'title': 'Feel Good Hit of the Summer',
                                   'artist': 'Queens of the Stone Age', 'score': 0.99}})
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda _self: client))
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *_args: [
        {'id': 'feel-good-id', 'name': 'Feel Good Hit of the Summer',
         'artists': [{'name': 'Queens of the Stone Age'}],
         'track_number': 1, 'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda *_args: {
        'name': 'Rated R', 'artists': [{'name': 'Queens of the Stone Age'}]})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)
    LibraryReconciliationJob().scan(context)
    case_id = worker.get_review_cases()['items'][0]['case_id']
    finding = worker.get_review_case(case_id)['items'][0]
    assert finding['details']['changes']['library_album_title']['new'] == 'Rated R'
    applied = worker.apply_review_decision(case_id, finding['id'],
                                           evidence_token=finding['evidence_token'], action='fix')
    assert applied['success'], applied
    assert FLAC(str(path))['album'][0] == 'R'
    assert worker.fix_finding(finding['id'], review_approved=True)['success']
    assert FLAC(str(path))['album'][0] == 'Rated R'
    with db._get_connection() as conn:
        title = conn.execute('SELECT title FROM albums WHERE id = 10').fetchone()[0]
    assert title == 'Rated R'


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_release_identity_repairs_mixed_tags_without_rewriting_coherent_pressings():
    from core.reconciliation.file_evidence import _release_identity_target
    file = lambda release: {'tags': {'musicbrainz_release_id': release}}
    assert _release_identity_target([file('old'), file('old')], {}, 'musicbrainz', 'new') == ''
    assert _release_identity_target([file('old'), file('other')], {}, 'musicbrainz', 'new') == 'new'
    assert _release_identity_target([file('old'), file('')], {}, 'musicbrainz', 'new') == ''
    assert _release_identity_target([file('old'), file('')], {}, 'musicbrainz', 'old') == 'old'
    assert _release_identity_target([file(''), file('')], {}, 'musicbrainz', 'new') == ''
    assert _release_identity_target([file('old')], {'canonical_locked': True}, 'musicbrainz', 'new') == 'new'
    assert _release_identity_target([file('old'), file('')], {'canonical_locked': True}, 'musicbrainz', 'new') == 'new'
    assert _release_identity_target([file('old'), file('other')], {}, 'deezer', '123') == ''
