"""workflows / inventory scan regression contracts."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest
from mutagen.flac import FLAC

import core.reconciliation.selection as reconciliation_selection
from core.reconciliation import search as search
from core.reconciliation.evidence import ReconciliationEvidence
from core.reconciliation.grouping import _group_disc_folders, _group_logical_albums
from core.reconciliation.organization import organization_context
from core.reconciliation.queue import (
    classify_case,
    get_case,
    list_cases,
)
from core.reconciliation.selection import (
    _release_group_candidates,
)
from core.reconciliation.store import (
    ensure_schema,
    retire_unseen_plans,
)
from core.repair_jobs.base import JobContext
from core.repair_jobs.library_reconciliation import LibraryReconciliationJob
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase
from tests.reconciliation.support.acceptance import build_album, scan_case
from tests.reconciliation.support.audio import (
    _Acoustid,
    _minimal_flac,
)
from tests.reconciliation.support.queue import _Database
from tests.reconciliation.support.scenarios import (
    _album,
    _metadata_recommendation_album,
)


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


def test_one_album_row_in_two_physical_folders_is_two_scan_cases():
    db = _Database()
    db.add(1, '/music/Disc One/01.flac',
           {'album_id': 10, 'album_folder': '/music/Disc One'})
    db.add(2, '/music/Disc Two/01.flac',
           {'album_id': 10, 'album_folder': '/music/Disc Two'})
    assert list_cases(db)['total'] == 2


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


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize(('multidisc', 'reversed_source'), [(True, False), (True, True), (False, False)])
def test_completed_flattening_is_recovered_and_apply_rescan_is_stable(tmp_path, monkeypatch, multidisc, reversed_source):
    from core.reconciliation import choices as choices
    db, worker, context, paths, _ = _metadata_recommendation_album(tmp_path, monkeypatch)
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *_args, **_kwargs: [
        {'id': str(index + 1), 'name': title, 'artists': [{'name': 'Lamb of God'}],
         'track_number': 1 if multidisc else index + 1,
         'disc_number': index + 1 if multidisc else 1, 'duration_ms': 200000}
        for index, title in enumerate(['Laid to Rest', 'Hourglass'])][::(-1 if reversed_source else 1)])
    assert db.set_album_canonical(10, 'itunes', 'ashes-release', .99, locked=True)
    # Model the actual legacy decision: source disc 2 track 1 was flattened,
    # approved and applied. The inverse repair must reopen even though the
    # completed album is no longer an outstanding queue item.
    cache = ReconciliationEvidence(db, context.acoustid_client)
    fingerprint = cache.identify(str(paths[1]))['fingerprint_key']
    cache.close()
    legacy = {'album_id': 10, 'album_folder': str(paths[0].parent),
              'source': 'itunes', 'release_id': 'ashes-release', 'numbering_mode': 'digital',
              'release_disc_count': 2, 'fingerprint_key': fingerprint,
              'changes': {'track_number': {'old': 1, 'new': 2},
                          'disc_number': {'old': 2, 'new': 1}}}
    worker._create_finding('library_reconciliation', 'reconciliation_metadata', 'warning',
                           'track', '2', str(paths[1]), 'Flatten numbering', '', legacy)
    with db._get_connection() as conn:
        choices.ensure_schema(conn)
        fid, key, raw = conn.execute('SELECT id,review_case_key,details_json FROM repair_findings').fetchone()
        conn.execute("INSERT INTO reconcile_review_choices(finding_id,case_id,evidence_json,category,choice) VALUES (?,?,?,'numbering','fix')", (fid, key, raw))
        conn.execute("UPDATE repair_findings SET status='resolved', user_action='reconciliation_metadata'")
    assert not worker.get_review_cases()['items']
    # Stored digital mode is intentionally ignored, without changing settings
    # in the imports, downloader or folder organizer.
    class Config:
        def get(self, key, default=None):
            return {'numbering_mode': 'digital'} if key.endswith('.settings') else default
    context.config_manager = Config()
    result = LibraryReconciliationJob().scan(context)
    assert not result.errors
    case = worker.get_review_case(key)
    second = next(item for item in case['items'] if item['entity_id'] == '2')
    assert not second['details'].get('plan_approved')
    assert case['album_plan']['numbering_mode'] == 'release'
    assert 'filename' not in second['details']['changes']
    if multidisc:
        assert second['category'] == 'numbering'
        assert str(second['details']['changes']['disc_number']['new']) == '2'
        assert str(second['details']['changes']['track_number']['new']) == '1'
        assert worker.apply_review_decision_group(key, 'numbering', action='fix')['success']
    else:
        assert 'disc_number' not in second['details']['changes']
        assert 'track_number' not in second['details']['changes']
    for item in worker.get_review_case(key)['items']:
        assert item['review_tier'] == 'ready', item
        assert worker.fix_finding(item['id'], review_approved=True)['success']
    assert paths[1].exists()  # compatible absolute filename remains unchanged
    audio = FLAC(str(paths[1]))
    assert audio['discnumber'] == ['2' if multidisc else '1']
    assert audio['tracknumber'][0].split('/')[0] == ('1' if multidisc else '2')
    if multidisc:
        assert audio['tracktotal'] == ['1']
    with db._get_connection() as conn:
        row = conn.execute('SELECT disc_number,track_number FROM tracks WHERE id=2').fetchone()
    assert tuple(row) == ((2, 1) if multidisc else (1, 2))
    result = LibraryReconciliationJob().scan(context)
    assert not result.errors
    assert not worker.get_review_cases()['items']


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_unindexed_album_member_gets_guarded_file_tag_proposal(tmp_path, monkeypatch):
    db, worker, context, paths, mbids = _metadata_recommendation_album(tmp_path, monkeypatch, mixed=True)
    with db._get_connection() as conn:
        conn.execute('DELETE FROM tracks WHERE id = 2')
        conn.commit()
    assert not LibraryReconciliationJob().scan(context).errors
    case_id = worker.get_review_cases()['items'][0]['case_id']
    items = worker.get_review_case(case_id)['items']
    item = next(item for item in items if item['file_path'] == str(paths[1]))
    assert item['finding_type'] == 'reconciliation_metadata'
    assert item['entity_type'] == 'file'
    assert item['details']['file_only']
    assert item['details']['track_id'] is None
    assert item['details']['changes']['musicbrainz_release_id']['new'] == ''
    assert item['details']['db_track_updates'] == {}
    saved = worker.apply_review_decision(case_id, item['id'],
        evidence_token=item['evidence_token'], action='fix')
    assert saved['success'], saved
    result = worker.fix_finding(item['id'], review_approved=True)
    assert result['success'], result
    tags = FLAC(str(paths[1]))
    assert 'musicbrainz_albumid' not in tags
    assert tags['musicbrainz_trackid'] == [mbids[1]]
    with db._get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0] == 1


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize('previous_identity', ['file', 'track'])
def test_indexing_keeps_pending_proposal_visible_on_the_first_rescan(tmp_path, monkeypatch, previous_identity):
    db, worker, context, paths, _ = _metadata_recommendation_album(tmp_path, monkeypatch, mixed=True)
    if previous_identity == 'file':
        with db._get_connection() as conn:
            conn.execute('DELETE FROM tracks WHERE id=2')
            conn.commit()
    scan = LibraryReconciliationJob()
    assert not scan.scan(context).errors
    case_id = worker.get_review_cases()['items'][0]['case_id']
    previous = next(item for item in worker.get_review_case(case_id)['items']
                    if item['file_path'] == str(paths[1]))
    assert previous['entity_type'] == previous_identity
    assert worker.apply_review_decision(case_id, previous['id'],
        evidence_token=previous['evidence_token'], action='fix')['success']
    with db._get_connection() as conn:
        if previous_identity == 'file':
            conn.execute("INSERT INTO tracks (id,album_id,artist_id,title,track_number,file_path) VALUES (22,10,1,'Hourglass',2,?)", (str(paths[1]),))
        else:
            conn.execute('UPDATE tracks SET id=22 WHERE id=2')
        conn.commit()
    for _ in range(2):
        assert not scan.scan(context).errors
        current = [item for item in worker.get_review_case(case_id)['items']
                   if item['file_path'] == str(paths[1])]
        assert len(current) == 1
        assert (current[0]['id'], current[0]['entity_type'], current[0]['entity_id']) == (previous['id'], 'track', '22')
        assert current[0]['details']['track_id'] == '22'
        assert not current[0]['details'].get('file_only')
        # The database target changed, so the old approval is not transferable.
        assert not current[0]['details'].get('plan_approved')


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize('changed', ['indexed', 'indexed_alias', 'file_changed'])
def test_unindexed_metadata_apply_rejects_changed_evidence(tmp_path, monkeypatch, changed):
    db, worker, context, paths, _ = _metadata_recommendation_album(tmp_path, monkeypatch, mixed=True)
    with db._get_connection() as conn:
        conn.execute('DELETE FROM tracks WHERE id = 2')
        conn.commit()
    assert not LibraryReconciliationJob().scan(context).errors
    case_id = worker.get_review_cases()['items'][0]['case_id']
    item = next(item for item in worker.get_review_case(case_id)['items']
                if item['file_path'] == str(paths[1]))
    if changed == 'file_changed':
        tags = FLAC(str(paths[1]))
        tags['comment'] = ['Changed after review']
        tags.save()
    else:
        stored = paths[1]
        if changed == 'indexed_alias':
            stored = tmp_path / 'alias.flac'
            stored.hardlink_to(paths[1])
        with db._get_connection() as conn:
            conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path, metadata_locked) VALUES (2,10,1,'Hourglass',?,1)", (str(stored),))
            conn.commit()
    before = paths[1].read_bytes()
    result = worker._fix_reconciliation_metadata(item['entity_type'], item['entity_id'],
                                                str(paths[1]), item['details'])
    assert not result['success']
    # Adding a hardlink also changes ctime, so the file identity guard can
    # reject the stale evidence before the indexed-row guard is reached.
    assert ('indexed' in result['error'] or 'changed' in result['error']) if changed.startswith('indexed') else 'changed' in result['error']
    assert paths[1].read_bytes() == before


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_tool_findings_are_rechecked_before_retirement(tmp_path, monkeypatch):
    db, worker, context, paths, _ = _metadata_recommendation_album(tmp_path, monkeypatch)
    current_id = 'feb76071-8125-4e98-8841-7b45a8d37feb'
    for index, mbid in enumerate(['obsolete-id', current_id]):
        worker._create_finding(job_id='mbid_mismatch_detector', finding_type='mbid_mismatch',
            severity='warning', entity_type='track', entity_id=str(index + 1), file_path=str(paths[index]),
            title='Old ID warning', description='Earlier finding', details={'mbid': mbid})
    assert not LibraryReconciliationJob().scan(context).errors
    with db._get_connection() as conn:
        rows = conn.execute("SELECT entity_id,status,user_action FROM repair_findings WHERE job_id='mbid_mismatch_detector' ORDER BY entity_id").fetchall()
    assert tuple(rows[0]) == ('1', 'resolved', 'obsolete')
    assert rows[1][1] == 'pending'  # A proposed correction has not changed the file yet.
    case_id = worker.get_review_cases()['items'][0]['case_id']
    item = next(item for item in worker.get_review_case(case_id)['items'] if item['file_path'] == str(paths[1]))
    assert item['details']['tool_checks'][0]['state'] == 'open'


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize('assignment', ['unmatched', 'source', 'companion', 'both'])
def test_companion_projection_preserves_every_local_file_once(assignment):
    from core.reconciliation.projection import _album_plan_assignments
    from core.reconciliation.selection import _match_release
    files = [
        {'path': '/album/Home.flac', 'tags': {'title': 'Home', 'artist': 'Artist'}, 'acoustid': {}},
        {'path': '/album/Home.mp3', 'tags': {'title': 'Home', 'artist': 'Artist'}, 'acoustid': {},
         'companion_of': '/album/Home.flac'},
    ]
    slots = [{'title': 'Different Song', 'artist': 'Other Artist', 'track_number': 1},
             {'title': 'Another Song', 'artist': 'Other Artist', 'track_number': 2}]
    forced = {'unmatched': {}, 'source': {0: 0}, 'companion': {1: 0}, 'both': {0: 0, 1: 1}}[assignment]
    plan = _match_release(files, slots, source='musicbrainz', release_id='release', forced_pairs=forced)
    rows, _ = _album_plan_assignments(files, slots, plan)
    local = [row for row in rows if row.get('file_path')]
    assert sorted(row['file_path'] for row in local) == sorted(file['path'] for file in files)
    companion = next(row for row in local if row['file_path'].endswith('.mp3'))
    assert companion['companion_of'] == '/album/Home.flac'
    assert len(plan['pairs']) == len(forced)  # Inherited assignments never consume another slot.
    assert plan['extra_files'] == {
        'unmatched': [0, 1], 'source': [], 'companion': [0], 'both': [],
    }[assignment]


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_unmatched_companions_remain_visible_in_persisted_review(tmp_path, monkeypatch):
    from types import SimpleNamespace

    db, worker, context, paths, _ = _metadata_recommendation_album(tmp_path, monkeypatch)
    companion = paths[0].with_suffix('.mp3')
    companion.write_bytes(paths[0].read_bytes())
    context.acoustid_client.matches[companion.name] = context.acoustid_client.matches[paths[0].name]
    context.config_manager = SimpleNamespace(get=lambda key, default=None:
        True if key == 'lossy_copy.enabled' else default)
    read_tracks = reconciliation_selection.get_album_tracks_for_source
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *args, **kwargs: [
        {**track, 'name': 'Completely Different Song'} if index == 0 else track
        for index, track in enumerate(read_tracks(*args, **kwargs))])
    assert not LibraryReconciliationJob().scan(context).errors
    case = worker.get_review_case(worker.get_review_cases()['items'][0]['case_id'])
    assignments = case['album_plan']['assignments']
    assert {row['file_path'] for row in assignments if row.get('file_path')} == {
        str(paths[0]), str(paths[1]), str(companion)}
    assert next(row for row in assignments if row.get('file_path') == str(companion))['status'] == 'extra'
    # The album release question precedes individual extra-file decisions.
    # All files must remain inspectable while that question is unresolved.
    row = next(row for row in assignments if row.get('file_path') == str(companion))
    assert row['action_state'] == 'preview'
    assert any(item['finding_type'] == 'reconciliation_release' for item in case['items'])


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_load_rows_does_not_join_distinct_libraries_by_relative_path(tmp_path):
    server_root = tmp_path / 'server-mount'
    scan_root = tmp_path / 'other-library'
    suffix = ('Lamb of God', 'Ashes of the Wake', '01 - Laid to Rest.flac')
    server_path = server_root.joinpath(*suffix)
    scan_path = scan_root.joinpath(*suffix)
    server_path.parent.mkdir(parents=True)
    scan_path.parent.mkdir(parents=True)
    server_path.write_bytes(b'server audio')
    scan_path.write_bytes(b'other audio!')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, server_path)
    context = JobContext(db=db, transfer_folder=str(scan_root), config_manager=None)

    tracks, _albums = LibraryReconciliationJob()._load_rows(context, [str(scan_root)])

    assert str(server_path) in tracks
    assert str(scan_path) not in tracks


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_completed_scan_retires_absent_folder_findings_but_stopped_scan_keeps_them(
        tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    root.mkdir()
    path = root / '01 - Laid to Rest.flac'
    path.write_bytes(b'audio fixture')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    worker = RepairWorker(database=db)
    client = _Acoustid({path.name: {'mbid': 'laid-id', 'title': 'Laid to Rest',
                                   'artist': 'Lamb of God', 'score': 0.99}})
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr(module, '_read_file_tags', lambda _path: {
        'title': 'Laid to Rest', 'artist': 'Lamb of God',
        'album': 'Ashes of the Wake', 'track_number': 1,
        'disc_number': 1, 'duration_ms': 200000,
    })
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *_args: [
        {'id': 'laid-id', 'name': 'Laid to Rest', 'track_number': 1,
         'disc_number': 1, 'duration_ms': 200000},
        {'id': 'hourglass-id', 'name': 'Hourglass', 'track_number': 2,
         'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda *_args: {
        'name': 'Ashes of the Wake'})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)
    job = LibraryReconciliationJob()
    job.scan(context)
    assert worker.get_review_cases()['total'] == 1

    path.unlink()
    context.should_stop = lambda: True
    job.scan(context)
    assert worker.get_review_cases()['total'] == 1

    context.should_stop = None
    job.scan(context)
    assert worker.get_review_cases()['total'] == 0


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_disc_subfolders_are_one_release_audit_without_merging_unrelated_folders():
    folders = {
        '/music/Artist/Album/Disc 1': ['/music/Artist/Album/Disc 1/a.flac'],
        '/music/Artist/Album/Disc 2': ['/music/Artist/Album/Disc 2/b.flac'],
        '/music/Other/Disc 1': ['/music/Other/Disc 1/c.flac'],
    }
    tracks = {path: {'album_id': album_id} for path, album_id in (
        (folders['/music/Artist/Album/Disc 1'][0], 10),
        (folders['/music/Artist/Album/Disc 2'][0], 10),
        (folders['/music/Other/Disc 1'][0], 11),
    )}
    grouped = _group_disc_folders(folders, tracks, ['/music'])
    assert set(grouped['/music/Artist/Album']) == set(folders['/music/Artist/Album/Disc 1'] +
                                                     folders['/music/Artist/Album/Disc 2'])
    assert grouped['/music/Other/Disc 1'] == folders['/music/Other/Disc 1']


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_logical_album_groups_same_db_album_across_arbitrary_folders():
    folders = {
        '/music/Artist/Album': ['/music/Artist/Album/01.flac'],
        '/music/Artist/Album extra': ['/music/Artist/Album extra/02.flac'],
    }
    tracks = {path: {'album_id': 10, 'track_number': index, 'disc_number': 1}
              for index, path in enumerate(
                  (folders['/music/Artist/Album'][0],
                   folders['/music/Artist/Album extra'][0]), start=1)}

    grouped, trusted = _group_logical_albums(folders, tracks, {
        10: {'album_title': 'Album', 'owner_profile_id': 1}})

    assert len(grouped) == 1
    assert set(next(iter(grouped.values()))) == set(path for paths in folders.values()
                                                    for path in paths)
    assert trusted == {}


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_digital_edition_discovery_accepts_a_release_group_seed():
    class MusicBrainz:
        def get_release(self, _mbid, includes):
            assert includes == ['release-groups']
            return None

        def get_release_group(self, mbid):
            return {'id': mbid}

        def browse_release_group_releases(self, mbid, limit):
            assert (mbid, limit) == ('group-id', 100)
            return [
                {'id': 'cd', 'media': [
                    {'format': 'CD', 'track-count': 5},
                    {'format': 'CD', 'track-count': 5},
                ]},
                {'id': 'digital', 'status': 'Official', 'media': [
                    {'format': 'Digital Media', 'track-count': 10},
                ]},
            ]

    context = JobContext(db=None, transfer_folder='', config_manager=None,
                         mb_client=MusicBrainz())

    assert _release_group_candidates(
        'group-id', [{} for _ in range(10)], context, {}) == [
            ('musicbrainz', 'digital'), ('musicbrainz', 'cd')]


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_unassigned_download_grouping_requires_one_exact_album_owner():
    from core.reconciliation.grouping import _group_unassigned_downloads
    folders = {'/artist/deluxe': ['/artist/deluxe/a'], '/artist/download': ['/artist/download/b']}
    tracks = {'/artist/deluxe/a': {'album_id': 'deluxe'}}
    albums = {'deluxe': {'album_title': 'motionblur (DELUXE)', 'artist_name': 'Split Chain',
                          'musicbrainz_release_id': 'release'}}
    tags = {'album': 'motionblur (DELUXE)', 'artist': 'Split Chain'}
    trusted = {}
    result = _group_unassigned_downloads(folders, tracks, albums, trusted, lambda _: tags)
    assert result == {'/artist/deluxe': ['/artist/deluxe/a', '/artist/download/b']}
    assert trusted == {'/artist/deluxe': 'deluxe'}
    assert _group_unassigned_downloads(folders, tracks, albums, {},
                                      lambda _: {**tags, 'artist': 'Other'}) == folders
    assert _group_unassigned_downloads(folders, tracks, albums, {},
                                      lambda _: {**tags, 'album': 'motionblur'}) == folders
    folders['/artist/another'] = ['/artist/another/c']
    tracks['/artist/another/c'] = {'album_id': 'other'}
    albums['other'] = dict(albums['deluxe'])
    assert _group_unassigned_downloads(folders, tracks, albums, {}, lambda _: tags) == folders


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_failed_catalogue_reanalysis_preserves_plan_and_findings(tmp_path, monkeypatch):
    import requests

    import core.repair_jobs.library_reconciliation as module
    from core.reconciliation.catalogue import current

    root = tmp_path / 'music'
    root.mkdir()
    path = root / '01 - Laid to Rest.flac'
    _minimal_flac(path, {'title': 'Laid to Rest', 'artist': 'Lamb of God',
                         'album': 'Ashes of the Wake', 'tracknumber': '1'})
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    worker = RepairWorker(database=db)
    client = _Acoustid({path.name: {'mbid': 'laid', 'title': 'Laid to Rest',
                                   'artist': 'Lamb of God', 'score': .99}})
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *args, **kw: [
        {'id': 'laid', 'name': 'Laid to Rest', 'track_number': 1,
         'duration_ms': 200000, 'artists': [{'name': 'Lamb of God'}]}])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda *args, **kw: {'name': 'Ashes of the Wake'})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)
    first = LibraryReconciliationJob().scan(context)
    assert not first.stopped_early

    def state():
        with db._get_connection() as conn:
            return (conn.execute('SELECT case_id,plan_json FROM reconcile_album_plans').fetchall(),
                    conn.execute('SELECT id,status,details_json FROM repair_findings').fetchall())

    before = state()
    assert before[0]

    def failed(*args, **kwargs):
        def transport(*args, **kwargs):
            raise requests.Timeout('temporary provider outage')
        try:
            current.get().request('http://mirror', '/release/release', {}, transport)
        except requests.Timeout:
            return None  # The existing provider adapter's failure contract.

    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', failed)
    result = LibraryReconciliationJob().scan(context, refresh_catalogue=True)
    assert result.stopped_early and result.metrics['catalogue_incomplete']
    assert state() == before
    assert result.findings_created == 0


def test_other_album_planning_does_not_wait_for_folder_moves():
    from core.library.maintenance_lock import MaintenanceLock
    lock = MaintenanceLock()
    moving, finish, planned = threading.Event(), threading.Event(), threading.Event()
    def organize():
        with lock.for_album('moving'):
            moving.set()
            assert finish.wait(3)
    def plan():
        with lock.planning('other'):
            planned.set()
    thread = threading.Thread(target=organize)
    thread.start()
    try:
        assert moving.wait(3)
        planner = threading.Thread(target=plan)
        planner.start()
        assert planned.wait(1)
        planner.join(1)
        assert thread.is_alive()
    finally:
        finish.set()
        thread.join(3)


@pytest.mark.parametrize('with_mount_alias', [False, True])
def test_discovery_retains_known_members_when_moved_folders_require_a_full_walk(tmp_path, monkeypatch, with_mount_alias):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    for i, path in enumerate(album.paths):
        target = album.root / f'New parent {i}' / 'Album'
        target.mkdir(parents=True)
        moved = target / path.name
        path.rename(moved)
        album.durations[str(moved)] = album.durations[str(path)]
        with album.db._get_connection() as conn:
            conn.execute('UPDATE tracks SET file_path=? WHERE file_path=?', (str(moved), str(path)))
    album.folder.rmdir()
    if with_mount_alias:
        alias = tmp_path / 'other-mount'
        alias.symlink_to(album.root, target_is_directory=True)
        load_rows = LibraryReconciliationJob._load_rows
        def rows_with_aliases(job, *args, **kwargs):
            tracks, albums = load_rows(job, *args, **kwargs)
            tracks.update({str(alias / Path(path).relative_to(album.root)): row
                           for path, row in list(tracks.items())})
            return tracks, albums
        monkeypatch.setattr(LibraryReconciliationJob, '_load_rows', rows_with_aliases)
    result = LibraryReconciliationJob().scan(album.context, target_folder=str(album.folder), scope='discovery')
    assert not result.stopped_early, result.stopped_early
    refreshed = album.worker.get_review_case(case['case_id'])
    assert refreshed['case_id'] != case['case_id']
    assert len(refreshed['album_plan']['assignments']) == 2
    assert refreshed['album_plan']['counts']['missing'] == 0


def test_kept_metadata_does_not_influence_folder_name(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    context = organization_context(album.db, '10', [{'plan_keep': True, 'db_album_title': 'Wrong collection', 'track_id': '1', 'changes': {'filename': {'new': 'wrong.flac'}}}])
    assert context['album_title'] != 'Wrong collection'
    assert context['filenames']['1'] != 'wrong.flac'


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


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize('deluxe_numbers', [(12, 13, 14, 25, 26), tuple(range(12, 27))])
def test_deluxe_addendum_joins_disjoint_regular_album_for_review(deluxe_numbers):
    regular = '/music/Split Chain/[2025] motionblur'
    deluxe = '/music/Split Chain/[2026] motionblur (DELUXE)'
    regular_paths = [f'{regular}/{number:02}.flac' for number in range(1, 12)]
    deluxe_paths = [f'{deluxe}/{number:02}.mp3' for number in deluxe_numbers]
    folders = {regular: regular_paths, deluxe: deluxe_paths}
    tracks = {
        path: {'album_id': 10, 'disc_number': 1, 'track_number': number}
        for number, path in enumerate(regular_paths, start=1)
    }
    tracks.update({
        path: {'album_id': 11, 'disc_number': 1, 'track_number': number}
        for number, path in zip(deluxe_numbers, deluxe_paths)
    })
    albums = {
        10: {'album_title': 'motionblur', 'artist_name': 'Split Chain',
             'owner_profile_id': 1, 'musicbrainz_release_id': 'regular'},
        11: {'album_title': 'motionblur (DELUXE)', 'artist_name': 'Split Chain',
             'owner_profile_id': 1, 'musicbrainz_release_id': 'deluxe'},
    }

    grouped, trusted = _group_logical_albums(folders, tracks, albums)
    assert set(grouped[deluxe]) == set(regular_paths + deluxe_paths)
    assert trusted == {deluxe: 11}

    tracks[deluxe_paths[0]]['track_number'] = 11
    grouped, trusted = _group_logical_albums(folders, tracks, albums)
    assert len(grouped) == 2
    assert trusted == {}

    tracks[deluxe_paths[0]]['track_number'] = 12
    albums[11]['artist_name'] = 'Different Artist'
    grouped, trusted = _group_logical_albums(folders, tracks, albums)
    assert len(grouped) == 2
    assert trusted == {}


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_logical_album_joins_parent_and_disc_child_with_different_album_rows():
    parent = [f'/music/Matchbox Twenty/The Matchbox Twenty Collection/{n:02}.flac'
              for n in range(1, 41)]
    disc_one = [f'/music/Matchbox Twenty/The Matchbox Twenty Collection/Disc 1/{n:02}.flac'
                for n in range(1, 9)]
    disc_four = [f'/music/Matchbox Twenty/The Matchbox Twenty Collection/Disc 4/{n:02}.flac'
                 for n in range(1, 10)]
    folders = {
        '/music/Matchbox Twenty/The Matchbox Twenty Collection': parent,
        '/music/Matchbox Twenty/The Matchbox Twenty Collection/Disc 1': disc_one,
        '/music/Matchbox Twenty/The Matchbox Twenty Collection/Disc 4': disc_four,
    }
    tracks = {
        path: {'album_id': 10, 'track_number': index, 'disc_number': 1}
        for index, path in enumerate(parent, start=1)
    }
    tracks.update({path: {'album_id': 11, 'track_number': index, 'disc_number': 1}
                   for index, path in enumerate(disc_one, start=1)})
    tracks.update({path: {'album_id': 11, 'track_number': index, 'disc_number': 4}
                   for index, path in enumerate(disc_four, start=1)})
    albums = {
        10: {'album_title': 'The Matchbox Twenty Collection', 'owner_profile_id': 1,
             'musicbrainz_release_id': 'collection-release'},
        11: {'album_title': 'The Matchbox Twenty Collection', 'owner_profile_id': 1},
    }

    grouped, trusted = _group_logical_albums(folders, tracks, albums)

    anchor = '/music/Matchbox Twenty/The Matchbox Twenty Collection'
    assert list(grouped) == [anchor]
    assert len(grouped[anchor]) == 57
    assert trusted == {anchor: 10}


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_logical_album_absorbs_only_unlinked_nonoverlapping_soundtrack_fragments():
    main = [f'/music/Various Artists/The Truman Show/{index:02}.flac'
            for index in (*range(1, 7), *range(8, 11))]
    soundtrack_folder_track = (
        '/music/Various Artists/The Truman Show (Original Motion Picture Soundtrack)/11.flac')
    main.append(soundtrack_folder_track)
    living = '/music/Philip Glass/The Truman Show/12.flac'
    beginning = '/music/Chopin/The Truman Show/07.flac'
    other = '/music/Other/The Truman Show/01.flac'
    folders = {
        '/music/Various Artists/The Truman Show': main[:-1],
        '/music/Various Artists/The Truman Show (Original Motion Picture Soundtrack)':
            [soundtrack_folder_track],
        '/music/Philip Glass/The Truman Show': [living],
        '/music/Chopin/The Truman Show': [beginning],
        '/music/Other/The Truman Show': [other],
    }
    tracks = {}
    for path in main:
        tracks[path] = {'album_id': 10, 'track_number': int(path[-7:-5]),
                        'disc_number': 1}
    tracks[living] = {'album_id': 11, 'track_number': 12, 'disc_number': 1}
    tracks[beginning] = {'album_id': 12, 'track_number': 7, 'disc_number': 1}
    tracks[other] = {'album_id': 13, 'track_number': 1, 'disc_number': 1}
    albums = {
        10: {'album_title': 'The Truman Show',
             'owner_profile_id': 1, 'musicbrainz_release_id': 'release'},
        11: {'album_title': 'The Truman Show', 'owner_profile_id': 1},
        12: {'album_title': 'The Truman Show', 'owner_profile_id': 1},
        # An explicit source ID and overlapping position make this a separate
        # legitimate release even though its display title is identical.
        13: {'album_title': 'The Truman Show', 'owner_profile_id': 1,
             'spotify_album_id': 'other-release'},
    }

    grouped, trusted = _group_logical_albums(folders, tracks, albums)

    anchor = '/music/Various Artists/The Truman Show'
    assert set(grouped[anchor]) == set([*main, living, beginning])
    assert grouped['/music/Other/The Truman Show'] == [other]
    assert trusted == {anchor: 10}
