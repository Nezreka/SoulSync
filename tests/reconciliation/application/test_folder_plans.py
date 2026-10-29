"""application / folder plans regression contracts."""
from __future__ import annotations

import json
import os
import threading
import time
from types import SimpleNamespace

import pytest

from core.library.file_identity import planned_metadata, same_file
from core.library_reorganize import (
    _plan_from_tags,
)
from core.reconciliation import search as search
from core.reconciliation.display import (
    _suggest_filename,
)
from core.reconciliation.fulfillment import (
    record_request,
    retain_request_context,
)
from core.reconciliation.organization import (
    approve_organization,
    approved_organization,
    organization_context,
)
from core.reconciliation.queue import (
    ready_batch_preview,
)
from core.reorganize_queue import ReorganizeQueue
from core.repair_jobs.base import JobContext
from core.repair_jobs.library_reconciliation import LibraryReconciliationJob
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase
from tests.reconciliation.support.acceptance import build_album, scan_case
from tests.reconciliation.support.acquisition import Database
from tests.reconciliation.support.audio import (
    _Acoustid,
)
from tests.reconciliation.support.queue import _Database
from tests.reconciliation.support.scenarios import (
    _album,
)


def test_batch_selection_applies_only_selected_albums_and_organization():
    db = _Database()
    for index in (1, 2):
        db.add(index, f'/music/Album{index}/song.flac', {'album_id': index, 'album_folder': f'/music/Album{index}',
            'album_title': f'Album {index}', 'artist_name': 'Artist', 'confidence': 'clear',
            'changes': {'title': {'old': 'song', 'new': 'Song'}}},
            job_id='library_reconciliation', kind='reconciliation_metadata')
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    captured = []
    worker.start_bulk_fix = lambda **kwargs: captured.append(kwargs) or {'started': True}
    preview = ready_batch_preview(db)
    selected = preview['albums'][1]
    result = worker.start_review_safe(expected_signature=preview['signature'], case_ids=[selected['case_id']], organize_case_ids=[selected['case_id']])
    assert result['started']
    assert captured[0]['finding_ids'] == [action['id'] for action in selected['actions']]
    assert len(captured[0]['review_case_folders']) == 1
    assert len(captured[0]['organize_album_ids']) == 1
    assert not worker.start_review_safe(expected_signature=preview['signature'], case_ids=[])['started']
    assert not worker.start_review_safe(expected_signature=preview['signature'], case_ids=[selected['case_id']], organize_case_ids=[preview['albums'][0]['case_id']])['started']


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize('filename', ['11 - Ab Ovo.flac', '01 - Ab Ovo.flac',
                                     '2-01 - Ab Ovo.flac', '0201 - Ab Ovo.flac'])
def test_filename_numbering_can_differ_from_per_disc_metadata(filename):
    slot = {'title': 'Ab Ovo', 'disc_number': 2, 'track_number': 1}
    assert _suggest_filename('/album/' + filename, slot, absolute_number=11) == ''
    proposed = _suggest_filename('/album/' + filename, {**slot, 'title': 'Ab Ovo (live)'},
                                 absolute_number=11)
    assert proposed == '/album/' + filename.replace('Ab Ovo', 'Ab Ovo (live)')


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize(('disc', 'track'), [(1, 121), (10, 1)])
def test_packed_filename_does_not_encode_another_position_on_overflow(disc, track):
    assert _suggest_filename('/album/0101 - Ab Ovo.flac', {
        'title': 'Ab Ovo', 'disc_number': disc, 'track_number': track}) == ''


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_filename_plan_preserves_numbering_and_harmless_title_flavor():
    assert _suggest_filename('/music/0201 - Wrong.flac', {
        'title': 'Hourglass', 'disc_number': 2, 'track_number': 2,
    }) == '/music/0202 - Hourglass.flac'
    assert _suggest_filename('/music/01 - Song (Serenade From the Stars).flac', {
        'title': 'Song', 'disc_number': 1, 'track_number': 1,
    }) == ''
    assert _suggest_filename('/music/2026 - Wrong.flac', {
        'title': 'Hourglass', 'disc_number': 1, 'track_number': 2,
    }) == '/music/2026 - Hourglass.flac'


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_targeted_reanalysis_follows_organized_album_and_retires_old_case(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module
    root = tmp_path / 'music'
    old = root / 'Lamb of God' / 'Old folder'
    old.mkdir(parents=True)
    path = old / '01 - Laid to Rest.flac'
    path.write_bytes(b'fixture')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    worker = RepairWorker(database=db, transfer_folder=str(root))
    client = _Acoustid({path.name: {'mbid': 'laid', 'title': 'Laid to Rest', 'artist': 'Lamb of God', 'score': .98}})
    monkeypatch.setattr(module, 'all_library_roots', lambda _: [str(root)])
    monkeypatch.setattr(module, '_read_file_tags', lambda _: {'title': 'Laid to Rest', 'artist': 'Lamb of God', 'album': 'Ashes of the Wake', 'track_number': 1, 'disc_number': 1, 'duration_ms': 200000})
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *a, **kw: [
        {'id': 'laid', 'name': 'Laid to Rest', 'artists': [{'name': 'Lamb of God'}], 'track_number': 1, 'duration_ms': 200000},
        {'id': 'missing', 'name': 'Hourglass', 'artists': [{'name': 'Lamb of God'}], 'track_number': 2, 'duration_ms': 240000}])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda *a, **kw: {'name': 'Ashes of the Wake'})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None, acoustid_client=client, create_finding=worker._create_finding)
    job = LibraryReconciliationJob()
    assert not job.scan(context).stopped_early
    old_case = worker.get_review_cases()['items'][0]['case_id']
    new = old.with_name('Ashes of the Wake')
    old.rename(new)
    with db._get_connection() as conn:
        conn.execute('UPDATE tracks SET file_path=? WHERE id=1', (str(new / path.name),))
        conn.commit()
    result = job.scan(context, target_folder=str(old), scope='discovery')
    assert not result.stopped_early
    assert result.scanned == 1
    current = worker.get_review_case(old_case)
    assert current['case_id'] != old_case
    assert current['folder'] == str(new)
    assert current['status'] != 'completed'
    assert worker.get_review_cases()['total'] == 1


def test_organization_completion_waits_for_final_inventory():
    queue = ReorganizeQueue()
    entered, finish = threading.Event(), threading.Event()
    def move(_item):
        entered.set()
        assert finish.wait(3)
        return {'status': 'completed', 'moved': 13}
    queue.set_runner(move)
    try:
        task = queue.enqueue(album_id='album', album_title='Album', artist_id='artist',
                             artist_name='Artist', source=None)
        assert entered.wait(3)
        assert queue.wait_for(task['queue_id'], timeout=0)['status'] == 'running'
        finish.set()
        result = queue.wait_for(task['queue_id'], timeout=3)
        assert result['status'] == 'done'
        assert result['moved'] == 13
    finally:
        finish.set()
        queue.stop()


def test_filename_uses_the_organizer_compatibility_policy():
    slot = {'title': 'u + me = <3', 'track_number': 5, 'disc_number': 1}
    assert _suggest_filename('/music/05 - u + me = _3.flac', slot) == ''
    assert _suggest_filename('/music/05 - wrong.flac', slot) == '/music/05 - u + me = _3.flac'


def test_mixed_formats_share_album_folder_metadata(monkeypatch):
    from core.library_reorganize import _plan_from_tags
    base = {'name': 'Collection', 'album_artist': 'Artist', 'release_date': '2013',
            'release_mbid': 'release', 'total_tracks': 57}
    def tags(path):
        album = {**base, 'album_type': 'album' if path.endswith('.flac') else '',
                 'secondary_types': ['compilation'] if path.endswith('.flac') else []}
        return album, {'name': path, 'disc_number': 1, 'artists': [{'name': 'Artist'}]}, None
    monkeypatch.setattr('core.library.reorganize_tag_source.read_album_track_from_file', tags)
    plan = _plan_from_tags({'artist_name': 'Artist'},
                          [{'file_path': '/track.mp3'}, {'file_path': '/track.flac'}], lambda p: p)
    contexts = [item['api_album'] for item in plan['items']]
    assert contexts[0] == contexts[1]
    assert contexts[0]['secondary_types'] == ['compilation']
    assert contexts[0]['album_type'] == 'album'


def test_destination_preview_includes_planned_tags_without_writing_files(tmp_path, monkeypatch):
    from core.library_reorganize import _plan_from_tags
    from core.tag_writer import write_tags_to_file
    album = build_album(tmp_path, monkeypatch, 'supported')
    paths = [str(path) for path in album.paths]
    updates = {path: {'album_title': 'New album', 'artist_name': 'New artist',
                      'disc_number': 2, 'track_number': i, 'track_count': 2}
               for i, path in enumerate(paths, 1)}
    tracks = [{'file_path': path} for path in paths]
    original = [path.read_bytes() for path in album.paths]
    preview = _plan_from_tags({}, tracks, lambda path: path, updates)
    assert [path.read_bytes() for path in album.paths] == original
    assert preview['api_album']['name'] == 'New album'
    assert preview['api_album']['album_artist'] == 'New artist'
    assert preview['total_discs'] == 2
    for path in paths:
        assert write_tags_to_file(path, updates[path], embed_cover=False)['success']
    actual = _plan_from_tags({}, tracks, lambda path: path)
    assert preview == actual


def test_apply_waits_for_all_moves_then_publishes_one_complete_scan(tmp_path, monkeypatch):
    schedule = RepairWorker._schedule_album_refresh
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    monkeypatch.setattr(RepairWorker, '_schedule_album_refresh', schedule)
    monkeypatch.setattr(album.worker, '_job_context', lambda **kwargs: album.context)
    queue = ReorganizeQueue()
    monkeypatch.setattr('core.reorganize_queue.get_queue', lambda: queue)
    halfway, finish = threading.Event(), threading.Event()
    scans = []
    original_scan = LibraryReconciliationJob.scan
    def observe_scan(job, *args, **kwargs):
        scans.append(finish.is_set())
        return original_scan(job, *args, **kwargs)
    monkeypatch.setattr(LibraryReconciliationJob, 'scan', observe_scan)
    target = album.root / 'Acceptance Artist' / 'Reorganized'
    target.mkdir()
    def move(item):
        from core.reconciliation.evidence import preserve_audio_evidence
        for i, old in enumerate(album.paths, 1):
            stat = old.stat()
            new = target / old.name
            old.rename(new)
            album.durations[str(new)] = album.durations[str(old)]
            with album.db._get_connection() as conn:
                conn.execute('UPDATE tracks SET file_path=? WHERE id=?', (str(new), i))
                preserve_audio_evidence(conn.cursor(), str(old), str(new),
                                        size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
            if i == 1:
                halfway.set()
                assert finish.wait(5)
        album.worker.refresh_moved_review_album(item.album_id)
        return {'status': 'completed', 'moved': 2}
    queue.set_runner(move)
    try:
        started = album.worker.start_review_safe(case_id=case['case_id'], organize=True)
        assert started['started'], started
        assert halfway.wait(5)
        assert album.worker._bulk_fix_state['running']
        assert not scans
        retained = album.worker.get_review_case(case['case_id'])
        assert len(retained['album_plan']['assignments']) == 2
        finish.set()
        deadline = time.monotonic() + 8
        while album.worker._bulk_fix_state['running'] and time.monotonic() < deadline:
            time.sleep(.01)
        state = album.worker._bulk_fix_state
        assert not state['running'], state
        assert not state.get('error'), state
        assert state['fixed'] == 2
        assert scans == [True]
        assert len(list(target.glob('*.flac'))) == 2
        refreshed = album.worker.get_review_case(case['case_id'])
        assert len(refreshed['album_plan']['assignments']) == 2
        assert not refreshed['album_plan']['counts']['missing']
    finally:
        finish.set()
        queue.stop()


def test_organization_context_preserves_unedited_names_and_approved_title(tmp_path):
    db = Database(tmp_path/'organization.db')
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE albums(id TEXT,title TEXT)')
        conn.execute('INSERT INTO albums VALUES("album","Old deluxe, pressing information")')
        conn.execute('CREATE TABLE tracks(id TEXT,album_id TEXT,file_path TEXT)')
        conn.executemany('INSERT INTO tracks VALUES(?,?,?)',[('1','album','/old/01 - First.flac'),('2','album','/old/02 - Legacy Title.flac')])
    context=organization_context(db,'album',[{'db_album_title':'New deluxe'}, {'track_id':'1','changes':{'filename':{'new':'01 - Approved.flac'}}}])
    assert context == {'album_title':'New deluxe','filenames':{'1':'01 - Approved.flac','2':'02 - Legacy Title.flac'}}
    with pytest.raises(ValueError):
        organization_context(db,'album',[{'track_id':'1','changes':{'filename':{'new':'../escape.flac'}}}])


def test_review_organization_preview_uses_final_folder_and_preserves_filenames(tmp_path, monkeypatch):
    import core.library_reorganize as reorganize
    from core.library_reorganize import preview_album_reorganize
    path=tmp_path/'old'/'01 - Old Name.flac';path.parent.mkdir();path.write_bytes(b'audio')
    track={'id':'1','file_path':str(path),'title':'Old Name','track_number':1,'disc_number':1}
    release={'name':'Old Album','disambiguation':'Old pressing','artists':[{'name':'Artist'}]}
    monkeypatch.setattr(reorganize,'load_album_and_tracks',lambda *a: ({'title':'Old Album','artist_name':'Artist'},[track]))
    monkeypatch.setattr(reorganize,'plan_album_reorganize',lambda *a,**kw: {'status':'planned','source':'tags','api_album':release,'total_discs':1,'items':[{'matched':True,'track':track,'api_track':{'name':'Old Name','track_number':1},'api_album':release}]})
    contexts=[]
    def build(context,artist,album,extension,**kwargs):
        contexts.append(context)
        return str(tmp_path/context['spotify_album']['name']/('generated'+extension)),True
    result=preview_album_reorganize(album_id='a',db=None,transfer_dir=str(tmp_path),resolve_file_path_fn=lambda p:p,
        build_final_path_fn=build,review_context={'album_title':'Approved Album','filenames':{'1':'01 - Approved.flac'}})
    assert result['success'],result
    assert result['tracks'][0]['new_path_abs'] == str(tmp_path/'Approved Album'/'01 - Approved.flac')
    assert contexts[0]['spotify_album']['disambiguation'] == ''
    assert path.read_bytes() == b'audio'


def test_disc_overlay_follows_verified_alias_and_stable_id(tmp_path, monkeypatch):
    path = tmp_path / 'song.flac'
    path.write_bytes(b'audio')
    alias = tmp_path / 'stored.flac'
    os.link(path, alias)
    seen = []
    def read(path, **kwargs):
        seen.append(kwargs['planned_metadata'])
        return {'name': 'Album'}, {'disc_number': kwargs['planned_metadata']['disc_number'], 'name': 'Song'}, None
    monkeypatch.setattr('core.library.reorganize_tag_source.read_album_track_from_file', read)
    plan = _plan_from_tags({}, [{'id': 'track', 'file_path': str(alias)}], lambda _: str(alias), {str(path): {'disc_number': 2}})
    assert plan['items'][0]['api_track']['disc_number'] == 2
    assert plan['total_discs'] == 2
    assert planned_metadata({'track:track': {'disc_number': 3}}, {'id': 'track'}, str(path)) == {'disc_number': 3}
    assert not same_file(path, tmp_path / 'missing')


def test_folder_retry_preserves_approved_title_and_current_filenames(tmp_path, monkeypatch):
    from core.reconciliation.organization import retry_organization_context
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    finding = case['items'][0]
    details = {**finding['details'], 'db_album_title': 'Approved album',
               'changes': {'filename': {'new': 'obsolete-filename.flac'}}}
    report = {'review_case_ids': [case['case_id']]}
    with album.db._get_connection() as conn:
        conn.execute("UPDATE repair_findings SET details_json=?,status='resolved',user_action='reconciliation_metadata' WHERE id=?",
                     (json.dumps(details), finding['id']))
    legacy = retry_organization_context(album.db, '10', report)
    assert legacy['album_title'] == 'Approved album'
    assert 'obsolete-filename.flac' not in legacy['filenames'].values()
    durable = retry_organization_context(album.db, '10', {**report, 'organization_titles': {'10': 'Saved operation title'}})
    assert durable['album_title'] == 'Saved operation title'
    with album.db._get_connection() as conn:
        conn.execute("UPDATE repair_findings SET user_action='plan_keep' WHERE id=?", (finding['id'],))
    assert retry_organization_context(album.db, '10', report)['album_title'] != 'Approved album'


def test_leave_album_also_closes_old_folder_retry_without_erasing_failure(tmp_path, monkeypatch):
    from core.repair_operation_reports import load_report, save_report
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    for item in case['items']:
        assert album.worker._apply_reviewed_finding(item['id'])['success']
    save_report(album.db, {'operation_id': 'failed-folder', 'review_case_ids': [case['case_id']],
                          'running': False, 'fixed': 2, 'organize_failed': 1, 'error': 'collision'})
    case = album.worker.get_review_case(case['case_id'])
    assert case['organization_retry_signature']
    assert album.worker.leave_review_album(case['case_id'], case['plan_signature'])['success']
    after = album.worker.get_review_case(case['case_id'])
    assert not after.get('organization_retry_signature')
    assert not after.get('last_operation')
    assert load_report(album.db, 'failed-folder')['error'] == 'collision'


def test_review_organization_handoff_is_bound_to_completed_actions_and_current_inventory(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    context = organization_context(album.db, '10', [row['details'] for row in case['items']])
    with pytest.raises(ValueError, match='remaining album actions'):
        approve_organization(album.db, '10', context)
    for row in case['items']:
        assert album.worker._apply_reviewed_finding(row['id'])['success']
    album.worker._create_finding(job_id='library_reconciliation', finding_type='reconciliation_missing',
        severity='info', entity_type='album_slot', entity_id='wanted', file_path=None,
        title='Waiting for a bonus track', description='Requested',
        details={**case['items'][0]['details'], 'fulfillment_state': 'wishlisted', 'missing_tracks': []})
    from core.reconciliation.authority import conflict
    assert conflict(album.db, 'path_mismatch', entity_type='album', entity_id='10')
    context = approve_organization(album.db, '10', context)
    item = SimpleNamespace(album_id='10', rename_only=True, metadata_source='tags', review_context=context)
    assert approved_organization(album.db, item)
    item.album_id = 'other'
    assert not approved_organization(album.db, item)

    item.album_id = '10'
    item.review_context = {**context, 'album_title': 'Unapproved name'}
    assert not approved_organization(album.db, item)
    item.review_context = context
    with album.db._get_connection() as conn:
        conn.execute("UPDATE tracks SET file_path=file_path || '.changed' WHERE album_id='10'")
    assert not approved_organization(album.db, item)


def test_folder_preview_uses_planned_total_and_preserves_audio_disc_two():
    from core.library.reorganize_tag_source import read_album_track_from_file
    loader = lambda _: {'available': True, 'tags': {'title': 'Song', 'artist': 'Artist', 'album': 'Album', 'discnumber': '2/2', 'totaldiscs': '2', 'tracknumber': '13'}}
    album, track, error = read_album_track_from_file('song.flac', read_embedded_tags_fn=loader,
        planned_metadata={'total_discs': 1, 'disc_number': 1})
    assert error is None
    assert album['total_discs'] == 1 and track['disc_number'] == 1
    album, track, error = read_album_track_from_file('song.flac', read_embedded_tags_fn=loader,
        planned_metadata={'total_discs': 2, 'disc_number': 2})
    assert error is None
    assert album['total_discs'] == 2 and track['disc_number'] == 2


def test_retry_keeps_review_release_and_profile_boundary(tmp_path):
    db = Database(tmp_path / 'retry.db')
    record_request(db, {'album_id':'album','profile_id':1,'source':'musicbrainz','release_id':'exact',
                       'replacement_request':True}, {'source_track_id':'recording'})
    payload = {'id':'recording','album':{'id':'exact','name':'Album'}}
    with db._get_connection() as conn:
        upgraded, info = retain_request_context(conn,payload,{'reason':'download_failed','attempt':2},1)
        assert upgraded['album']['musicbrainz_release_id'] == 'exact'
        assert upgraded['provider'] == 'musicbrainz'
        assert info['reason'] == 'library_reconciliation_wrong_audio'
        assert info['attempt'] == 2
        assert retain_request_context(conn,payload,{'reason':'download_failed'},2) == (payload,{'reason':'download_failed'})
        assert 'musicbrainz_release_id' not in payload['album']
