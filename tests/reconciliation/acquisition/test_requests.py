"""acquisition / requests regression contracts."""
from __future__ import annotations

import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from core.reconciliation import search as search
from core.reconciliation.fulfillment import (
    published_wishlist_track,
    record_request,
    record_request_on_connection,
    recover_published_requests,
    requested_folder,
)
from core.reconciliation.identity import review_case_key
from core.reconciliation.queue import (
    list_cases,
    ready_batch_preview,
)
from core.repair_jobs.base import JobContext
from core.repair_jobs.library_reconciliation import LibraryReconciliationJob
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase
from tests.reconciliation.support.acceptance import (
    RELEASE,
    build_album,
    scan_case,
)
from tests.reconciliation.support.acquisition import Database
from tests.reconciliation.support.audio import (
    _Acoustid,
)
from tests.reconciliation.support.queue import _Database
from tests.reconciliation.support.scenarios import (
    _album,
)


def test_complete_album_can_request_all_missing_slots_after_scope_choice():
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = _Database()
    worker._review_apply_lock = threading.RLock()
    case = {'folder': '/music/album', 'items': [
        {'id': 1, 'finding_type': 'reconciliation_missing', 'details': {}},
        {'id': 2, 'finding_type': 'reconciliation_missing', 'details': {}},
        {'id': 3, 'finding_type': 'reconciliation_metadata', 'details': {}},
    ]}
    worker.get_review_case = lambda *_args, **_kwargs: case
    calls = []
    worker._fix_reconciliation_missing = lambda *args: calls.append(args) or {'success': True}
    assert worker.start_review_missing_tracks('album-case')['requested'] == 2
    assert len(calls) == 2
    case['items'][1]['details'] = {'fulfillment_state': 'wishlisted'}
    assert worker.start_review_missing_tracks('album-case')['requested'] == 1
    assert len(calls) == 3
    case['items'].append({'id': 4, 'finding_type': 'reconciliation_intent'})
    assert not worker.start_review_missing_tracks('album-case')['started']


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
    worker._fix_reconciliation_missing = lambda *args: {'success': True, 'action': 'wishlisted'}
    worker._schedule_review_refresh = lambda *args: (_ for _ in ()).throw(AssertionError('No rescan needed'))
    case = worker.get_review_case(case_id)
    result = worker.apply_review_decision(case_id, 1, evidence_token=case['items'][0]['evidence_token'], action='fix')
    assert result['success'] and not result.get('refresh_queued')
    updated = worker.get_review_case(case_id)
    assert updated['status'] != 'completed'
    assert updated['items'][0]['details']['fulfillment_state'] == 'wishlisted'
    assert updated['decisions'][0]['category'] == 'pending_fulfillment'


def test_missing_track_subset_rejects_foreign_stale_and_invalid_selections():
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = _Database()
    worker._review_apply_lock = threading.RLock()
    case = {'items': [
        {'id': 1, 'entity_id': 'one', 'finding_type': 'reconciliation_missing', 'details': {}},
        {'id': 2, 'entity_id': 'two', 'finding_type': 'reconciliation_missing', 'details': {}},
        {'id': 3, 'finding_type': 'reconciliation_missing', 'details': {'fulfillment_state': 'wishlisted'}},
        {'id': 4, 'finding_type': 'reconciliation_metadata'},
    ]}
    worker.get_review_case = lambda *_args, **_kwargs: case
    calls = []
    worker._fix_reconciliation_missing = lambda *args: calls.append(args) or {'success': True}
    assert worker.start_review_missing_tracks('album', finding_ids=[2])['success']
    assert calls[0][1] == 'two'
    for invalid in ([], [3], [4], [99], [True], ['1'], '1'):
        assert not worker.start_review_missing_tracks('album', finding_ids=invalid)['started']
    assert len(calls) == 1
    case['items'].append({'id': 5, 'finding_type': 'reconciliation_release'})
    assert not worker.start_review_missing_tracks('album', finding_ids=[1])['started']


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_partial_album_can_mark_only_selected_missing_slots_wanted(tmp_path):
    path = tmp_path / '01 - Laid to Rest.flac'
    path.write_bytes(b'audio')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    worker = RepairWorker(database=db, transfer_folder=str(tmp_path))
    slots = [
        {'title': 'Hourglass', 'source_track_id': 'hourglass', 'disc_number': 1,
         'track_number': 2},
        {'title': 'Now You Have Something to Die For', 'source_track_id': 'now',
         'disc_number': 1, 'track_number': 3},
    ]
    result = worker._fix_reconciliation_intent('album', '10', None, {
        'album_id': 10, 'source': 'spotify', 'release_id': 'ashes-release',
        'missing_tracks': slots, '_fix_action': 'partial:["id:hourglass:1:2"]',
    })
    assert result['success'], result
    with db._get_connection() as conn:
        assert conn.execute('SELECT scope FROM reconcile_album_intent').fetchone()[0] == 'partial'
        assert json.loads(conn.execute(
            'SELECT slots_json FROM reconcile_wanted_slots').fetchone()[0]) == ['id:hourglass:1:2']
        missing = conn.execute('''SELECT details_json FROM repair_findings
                                  WHERE finding_type = 'reconciliation_missing' ''').fetchall()
    assert len(missing) == 1
    assert json.loads(missing[0][0])['missing_tracks'][0]['source_track_id'] == 'hourglass'


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize("matching_mode", ["off", "shadow", "reuse"])
def test_scan_review_intent_and_exact_missing_request(tmp_path, monkeypatch, matching_mode):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    folder = root / 'Lamb of God' / 'Ashes of the Wake'
    folder.mkdir(parents=True)
    path = folder / '01 - Laid to Rest.flac'
    path.write_bytes(b'audio fixture')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    worker = RepairWorker(database=db, transfer_folder=str(root))
    client = _Acoustid({path.name: {
        'mbid': 'laid-mbid', 'title': 'Laid to Rest',
        'artist': 'Lamb of God', 'score': 0.98,
    }})
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda _self: client))
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr(module, '_read_file_tags', lambda _path: {
        'title': 'Laid to Rest', 'artist': 'Lamb of God',
        'album': 'Ashes of the Wake', 'track_number': 1,
        'disc_number': 1, 'duration_ms': 200000,
    })
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source',
                        lambda source, release_id: [
                            {'id': 'laid-id', 'name': 'Laid to Rest',
                             'artists': [{'name': 'Lamb of God'}],
                             'track_number': 1, 'disc_number': 1,
                             'duration_ms': 200000},
                            {'id': 'hourglass-id', 'name': 'Hourglass',
                             'artists': [{'name': 'Lamb of God'}],
                             'track_number': 2, 'disc_number': 1,
                             'duration_ms': 240000},
                        ])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source',
                        lambda *_args: {'name': 'Ashes of the Wake',
                                        'artists': [{'name': 'Lamb of God'}]})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)
    scan = LibraryReconciliationJob()
    monkeypatch.setattr(scan, 'default_settings', {**scan.default_settings, 'matching_cache_mode': matching_mode})
    first = scan.scan(context)
    assert first.scanned == 1
    assert first.findings_created == 1
    case_id = worker.get_review_cases()['items'][0]['case_id']
    item = worker.get_review_case(case_id)['items'][0]
    assert item['finding_type'] == 'reconciliation_intent'
    assert client.fingerprints == client.lookups == 1

    walked = []
    original_walk = module.walk_library
    with monkeypatch.context() as narrow_patch:
        narrow_patch.setattr(module, 'walk_library',
                             lambda root: walked.append(root) or original_walk(root))
        narrow = scan.scan(context, target_folder=str(folder))
    assert narrow.scanned == 1
    assert walked == []  # Saved membership avoids even the artist/library walk.
    item = worker.get_review_case(case_id)['items'][0]
    assert item['finding_type'] == 'reconciliation_intent'

    result = worker.apply_review_decision(case_id, item['id'],
                                          evidence_token=item['evidence_token'],
                                          action='fix', fix_action='complete')
    assert result['success'], result
    second = scan.scan(context)
    assert second.scanned == 1
    if matching_mode == 'reuse':
        assert second.metrics['matching']['counts']['reused'] > 0
    elif matching_mode == 'shadow':
        assert second.metrics['matching']['counts']['shadow_compared'] > 0
        assert not second.metrics['matching']['counts'].get('shadow_mismatches')
    assert client.fingerprints == client.lookups == 1  # persistent cache
    missing = next(item for item in worker.get_review_case(case_id)['items']
                   if item['finding_type'] == 'reconciliation_missing')
    arrived = folder / '02 - Hourglass.flac'
    arrived.write_bytes(b'new download')
    stale = worker.apply_review_decision(case_id, missing['id'],
                                         evidence_token=missing['evidence_token'], action='fix')
    assert stale['success'] is False and 'rescan' in stale['error']
    arrived.unlink()
    missing = next(item for item in worker.get_review_case(case_id)['items']
                   if item['finding_type'] == 'reconciliation_missing')
    result = worker.apply_review_decision(case_id, missing['id'],
                                          evidence_token=missing['evidence_token'], action='fix')
    assert result['success'], result
    with db._get_connection() as conn:
        row = conn.execute('SELECT spotify_track_id, source_info FROM wishlist_tracks').fetchone()
    assert row['spotify_track_id'] == 'hourglass-id'
    assert json.loads(row['source_info'])['source_album_id'] == 'ashes-release'
    scan.scan(context)
    waiting_case = worker.get_review_case(case_id)
    assert waiting_case, worker.get_review_cases()
    waiting = next(item for item in waiting_case['items']
                   if item['finding_type'] == 'reconciliation_missing')
    assert waiting['details']['fulfillment_state'] == 'wishlisted'
    assert any(decision['category'] == 'pending_fulfillment'
               for decision in waiting_case['plan']['decisions'])
    assert any(row.get('fulfillment_state') == 'wishlisted'
               for row in waiting_case['album_plan']['assignments'])
    assert not worker.start_review_missing_tracks(case_id)['started']
    assert db.set_album_canonical(10, 'spotify', 'alternate-edition', 0.8, locked=True)
    scan.scan(context)
    pending = worker.get_review_case(case_id)['items']
    assert any(item['finding_type'] == 'reconciliation_intent' and
               item['details']['release_id'] == 'alternate-edition' for item in pending)


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_musicbrainz_release_group_missing_request_uses_exact_edition(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    root.mkdir()
    path = root / '01 - Laid to Rest.flac'
    path.write_bytes(b'audio fixture')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    assert db.set_album_canonical(10, 'musicbrainz', 'release-group-id', 0.9, locked=True)
    worker = RepairWorker(database=db)
    client = _Acoustid({path.name: {'mbid': 'laid-id', 'title': 'Laid to Rest',
                                   'artist': 'Lamb of God', 'score': 0.99}})
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda _self: client))
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
        'name': 'Ashes of the Wake', 'musicbrainz_release_id': 'exact-release-id'})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)
    LibraryReconciliationJob().scan(context)
    case_id = worker.get_review_cases()['items'][0]['case_id']
    intent = next(item for item in worker.get_review_case(case_id)['items']
                  if item['finding_type'] == 'reconciliation_intent')
    assert worker.apply_review_decision(case_id, intent['id'],
                                        evidence_token=intent['evidence_token'],
                                        action='fix', fix_action='complete')['success']
    missing = next(item for item in worker.get_review_case(case_id)['items']
                   if item['finding_type'] == 'reconciliation_missing')
    assert worker.apply_review_decision(case_id, missing['id'],
                                        evidence_token=missing['evidence_token'],
                                        action='fix')['success']
    with db._get_connection() as conn:
        row = conn.execute('SELECT spotify_data, source_info FROM wishlist_tracks').fetchone()
    assert json.loads(row['spotify_data'])['album']['id'] == 'exact-release-id'
    assert json.loads(row['source_info'])['source_album_id'] == 'exact-release-id'


def test_wishlist_and_exact_wanted_slot_commit_together(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'partial')
    case = scan_case(album)
    intent = next(item for item in case['items'] if item['finding_type'] == 'reconciliation_intent')
    slot = intent['details']['missing_tracks'][0]
    monkeypatch.setattr('core.library.manual_library_match.get_match_for_track', lambda *a: {'existing': True})
    payload = {'id': slot['source_track_id'], 'name': slot['title'], 'provider': 'musicbrainz',
               'artists': [{'name': 'Acceptance Artist'}], 'album': {'id': RELEASE, 'name': 'Northbound'}}
    result = album.db.add_to_wishlist_detailed(payload, user_initiated=True,
        reconciliation_request={'details': intent['details'], 'slot': slot})
    assert result['applied'], result
    with album.db._get_connection() as conn:
        assert json.loads(conn.execute('SELECT slots_json FROM reconcile_wanted_slots').fetchone()[0]) == [slot['wanted_key']]
    case = scan_case(album)
    # Only the other genuinely undecided missing slot is asked about.
    question = next(item for item in case['items'] if item['finding_type'] == 'reconciliation_intent')
    assert [item['title'] for item in question['details']['missing_tracks']] == ['Afterglow']
    missing = next(item for item in case['items'] if item['finding_type'] == 'reconciliation_missing')
    assert missing['details']['fulfillment_state'] == 'wishlisted'
    with album.db._get_connection() as conn:
        record_request_on_connection(conn, intent['details'], intent['details']['missing_tracks'][1])
    case = scan_case(album)
    assert not any(item['finding_type'] == 'reconciliation_intent' for item in case['items'])
    # A restart reads the exact same choices, independent of the album's old path.
    assert LibraryReconciliationJob()._intents(album.context)['10'][3]


def test_failed_intent_write_rolls_back_wishlist(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    def fail(*args):
        raise RuntimeError('intent write failed')
    monkeypatch.setattr('core.reconciliation.fulfillment.record_request_on_connection', fail)
    result = album.db.add_to_wishlist_detailed({'id': 'wanted', 'name': 'New', 'artists': [{'name': 'Artist'}],
        'album': {'id': RELEASE, 'name': 'Northbound'}}, user_initiated=True,
        reconciliation_request={'details': {}, 'slot': {}})
    assert not result['applied']
    with album.db._get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM wishlist_tracks').fetchone()[0] == 0


def test_requested_copy_returns_to_reviewed_disc_and_never_crosses_root(tmp_path):
    db = Database(tmp_path / 'routing.db')
    root=tmp_path/'library'; root.mkdir()
    first=root/'Compilations'/'Album'/'Disc 1'; first.mkdir(parents=True)
    second=first.parent/'Disc 2'; second.mkdir()
    one=first/'01.flac'; one.write_bytes(b'audio1')
    two=second/'01.flac'; two.write_bytes(b'audio2')
    record_request(db, {'album_id':'album','profile_id':1,'source':'musicbrainz','release_id':'exact','album_folder':str(first.parent)}, {'source_track_id':'recording'})
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE tracks(id TEXT,album_id TEXT,file_path TEXT,disc_number INTEGER)')
        conn.executemany('INSERT INTO tracks VALUES(?,?,?,?)',[('1','album',str(one),1),('2','album',str(two),2)])
    args=dict(profile_id=1,source='musicbrainz',release_id='exact',track_id='recording',disc_number=2,root=str(root))
    assert requested_folder(db,**args) == str(second)
    assert requested_folder(db,**{**args,'profile_id':2}) is None
    assert requested_folder(db,**{**args,'release_id':'different-edition'}) is None
    assert requested_folder(db,**{**args,'root':str(first)}) is None


@pytest.mark.parametrize('profile,release,reason,receipt_date,expected',[
    (1,'release','download_complete','2099-01-01','published'),
    (2,'release','download_complete','2099-01-01','requested'),
    (1,'different','download_complete','2099-01-01','requested'),
    (1,'release','removed_by_user','2099-01-01','requested'),
    (1,'release','download_complete','2000-01-01','requested')])
def test_missed_publication_requires_matching_delivery_profile_and_embedded_identity(tmp_path,profile,release,reason,receipt_date,expected):
    from core.reconciliation.fulfillment import (
        recover_published_requests,
        settle_assignments,
    )
    from core.reconciliation.identity import review_case_key
    from tests.reconciliation.support.audio import _minimal_flac
    db=Database(tmp_path/'receipt.db');path=tmp_path/'published.flac'
    _minimal_flac(path,{'musicbrainz_albumid':release,'musicbrainz_trackid':'recording'})
    details={'album_id':'album','profile_id':1,'source':'musicbrainz','release_id':'release','replacement_request':True}
    record_request(db,details,{'source_track_id':'recording'})
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE wishlist_removals(spotify_track_id TEXT,reason TEXT,final_path TEXT,profile_ids TEXT,removed_at TEXT)')
        conn.execute('INSERT INTO wishlist_removals VALUES("recording",?,?,?,?)',(reason,str(path),json.dumps([profile]),receipt_date))
        recover_published_requests(conn)
        assert conn.execute('SELECT state FROM reconcile_fulfillment').fetchone()[0] == expected
    if expected=='published':
        # Publication is not completion; only a subsequent supported assignment settles it.
        settle_assignments(db,review_case_key(details,None),[{'source_track_id':'recording','status':'confirmed','file_path':str(path)}],'release','musicbrainz')
        with db._get_connection() as conn:
            assert conn.execute('SELECT state FROM reconcile_fulfillment').fetchone()[0] == 'fulfilled'


@pytest.mark.parametrize('other_request,same_path,expected', [(False,True,'published'),(True,True,'requested'),(False,False,'requested')])
def test_retagged_delivery_requires_unique_request_and_same_file_in_exact_album_plan(tmp_path,other_request,same_path,expected):
    from core.reconciliation.fulfillment import recover_published_requests
    from core.reconciliation.identity import review_case_key
    from tests.reconciliation.support.audio import _minimal_flac
    db=Database(tmp_path/'retagged.db');path=tmp_path/'published.flac'
    _minimal_flac(path,{'musicbrainz_albumid':'another-pressing','musicbrainz_trackid':'recording'})
    details={'album_id':'album','profile_id':1,'source':'musicbrainz','release_id':'requested-release','replacement_request':True}
    record_request(db,details,{'source_track_id':'recording'})
    key=review_case_key(details,None)
    if other_request:
        record_request(db,{**details,'album_id':'another-album','release_id':'another-release'},{'source_track_id':'recording'})
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE wishlist_removals(spotify_track_id TEXT,reason TEXT,final_path TEXT,profile_ids TEXT,removed_at TEXT)')
        conn.execute('INSERT INTO wishlist_removals VALUES("recording","download_complete",?,"[1]","2099-01-01")',(str(path),))
        conn.execute('CREATE TABLE reconcile_album_plans(case_id TEXT PRIMARY KEY,plan_json TEXT)')
        plan={'source':'musicbrainz','release_id':'requested-release','assignments':[
            {'source_track_id':'recording','file_path':str(path if same_path else tmp_path/'another.flac'),'status':'uncertain'}]}
        conn.execute('INSERT INTO reconcile_album_plans VALUES(?,?)',(key,json.dumps(plan)))
        recover_published_requests(conn)
        assert conn.execute('SELECT state FROM reconcile_fulfillment WHERE case_id=?',(key,)).fetchone()[0] == expected


@pytest.mark.parametrize('profile,indexed_elsewhere,ambiguous,merged',[
    (1,False,False,True),(2,False,False,False),(1,True,False,False),(1,False,True,False)])
def test_delivered_disc_fragment_joins_only_its_requested_album(tmp_path,profile,indexed_elsewhere,ambiguous,merged):
    from core.reconciliation.fulfillment import group_delivered_files
    db=Database(tmp_path/'fragment.db')
    old=str(tmp_path/'Original album'/'01.flac');new=str(tmp_path/'New pressing'/'Disc 2'/'07.flac')
    details={'album_id':'album','profile_id':profile,'source':'musicbrainz','release_id':'release'}
    record_request(db,details,{'source_track_id':'recording'})
    if ambiguous:
        record_request(db,{**details,'album_id':'another'},{'source_track_id':'recording'})
    with db._get_connection() as conn:
        conn.execute('UPDATE reconcile_fulfillment SET state="published",published_path=?',(new,))
    folders={str(tmp_path/'Original album'):[old],str(tmp_path/'New pressing'/'Disc 2'):[new]}
    tracks={old:{'id':'old','album_id':'album'}}
    if indexed_elsewhere:tracks[new]={'id':'new','album_id':'another'}
    trusted={};albums={'album':{'owner_profile_id':1,'album_title':'Grace (Legacy Edition)'}}
    result=group_delivered_files(db,folders,tracks,albums,trusted)
    assert (new in result[str(tmp_path/'Original album')]) is merged
    assert sum(len(paths)for paths in result.values())==2
    with db._get_connection() as conn:
        assert all(row[0]=='published'for row in conn.execute('SELECT state FROM reconcile_fulfillment'))


def test_request_step_preserves_file_decision_and_allows_ready_edits_while_waiting(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'recording_conflict')
    case = scan_case(album)
    item = next(item for item in case['items'] if item['file_path'] == str(album.paths[0]))
    result = album.worker.apply_review_decision(case['case_id'], item['id'], evidence_token=item['evidence_token'], action='fix', fix_action='replace')
    assert result['success'], result
    case = album.worker.get_review_case(case['case_id'])
    intent = next((item for item in case['items'] if item['finding_type'] == 'reconciliation_intent'), None)
    if intent:
        assert album.worker.apply_review_decision(case['case_id'], intent['id'], evidence_token=intent['evidence_token'], action='fix', fix_action='complete')['success']
    case = album.worker.get_review_case(case['case_id'])
    requests = [row for row in case['items'] if row['finding_type'] == 'reconciliation_missing']
    assert requests
    assert not case['plan']['can_apply_ready']
    original = album.paths[0].read_bytes()
    outcome = album.worker.start_review_missing_tracks(case['case_id'])
    assert outcome['success'], outcome
    after = album.worker.get_review_case(case['case_id'])
    assert album.paths[0].read_bytes() == original
    assert next(row for row in after['items'] if row['id'] == item['id'])['details']['plan_approved']
    assert after['plan']['can_apply_ready']
    preview = ready_batch_preview(album.db, case_id=case['case_id'])
    assert not {row['id'] for row in requests} & {action['id'] for row in preview['albums'] for action in row['actions']}
    with album.db._get_connection() as conn:
        count = conn.execute('SELECT COUNT(*) FROM wishlist_tracks').fetchone()[0]
    assert count == len(requests)


def test_requested_tracks_allow_folder_review_but_unsubmitted_requests_block_it(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'partial')
    case = scan_case(album)
    intent = next(item for item in case['items'] if item['finding_type'] == 'reconciliation_intent')
    assert album.worker.apply_review_decision(case['case_id'], intent['id'],
        evidence_token=intent['evidence_token'], action='fix', fix_action='complete')['success']
    assert not album.worker.get_review_case(case['case_id'])['plan']['can_organize']
    assert album.worker.start_review_missing_tracks(case['case_id'])['success']
    assert album.worker.get_review_case(case['case_id'])['plan']['can_organize']


def test_reopened_scope_retains_requests_and_removes_only_unsubmitted_slots(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'partial')
    case = scan_case(album)
    intent = next(row for row in case['items'] if row['finding_type'] == 'reconciliation_intent')
    assert album.worker.apply_review_decision(case['case_id'], intent['id'], evidence_token=intent['evidence_token'], action='fix', fix_action='complete')['success']
    current = album.worker.get_review_case(case['case_id'])
    missing = [row for row in current['items'] if row['finding_type'] == 'reconciliation_missing']
    assert album.worker.start_review_missing_tracks(case['case_id'], finding_ids=[missing[0]['id']])['success']
    assert album.worker._fix_reconciliation_intent('album', '10', None, {**intent['details'], '_fix_action': 'partial'})['success']
    after = album.worker.get_review_case(case['case_id'])
    remaining = [row for row in after['items'] if row['finding_type'] == 'reconciliation_missing']
    assert [row['id'] for row in remaining] == [missing[0]['id']]
    assert remaining[0]['details']['fulfillment_state'] == 'wishlisted'


def test_completed_case_does_not_restore_historical_request_state(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    for row in case['items']:
        assert album.worker._apply_reviewed_finding(row['id'])['success']
    import core.reconciliation.queue as queue
    original = queue.get_case
    def history(*args, **kwargs):
        result = original(*args, **kwargs)
        if result:
            result['album_plan']['assignments'][0]['fulfillment_state'] = 'downloaded'
        return result
    monkeypatch.setattr(queue, 'get_case', history)
    terminal = album.worker.get_review_case(case['case_id'])
    assert terminal['status'] == 'completed'
    assert not any(row.get('fulfillment_state') for row in terminal['album_plan']['assignments'])


def test_cross_provider_publication_keeps_original_review_request(tmp_path):
    database = str(tmp_path / 'state.db')
    db = SimpleNamespace(_get_connection=lambda: sqlite3.connect(database))
    details = {'album_id': 1, 'album_folder': str(tmp_path), 'profile_id': 1, 'source': 'musicbrainz', 'release_id': 'mb-release'}
    slot = {'source_track_id': 'mb-recording'}
    record_request(db, details, slot)
    path = tmp_path / 'Stolen.flac'
    path.write_bytes(b'audio')
    identity = {'case_id': review_case_key(details, None), 'source': 'musicbrainz', 'release_id': 'mb-release', 'source_track_id': 'mb-recording'}
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE wishlist_tracks(profile_id INTEGER,spotify_track_id TEXT,spotify_data TEXT,source_info TEXT)')
        conn.execute('INSERT INTO wishlist_tracks VALUES(1,?,?,?)', ('itunes-track', json.dumps({'provider': 'itunes', 'id': 'itunes-track', 'album': {'id': 'itunes-album'}}), json.dumps({'review_request': identity})))
    published_wishlist_track(db, 'itunes-track', [2], str(path))
    with db._get_connection() as conn:
        assert conn.execute('SELECT state FROM reconcile_fulfillment').fetchone()[0] == 'requested'
    published_wishlist_track(db, 'itunes-track', [1], str(path))
    with db._get_connection() as conn:
        assert conn.execute('SELECT state,published_path FROM reconcile_fulfillment').fetchone() == ('published', str(path))


def test_legacy_cross_provider_recovery_needs_exact_verified_assignment(tmp_path, monkeypatch):
    import mutagen
    db = SimpleNamespace(_get_connection=lambda: sqlite3.connect(str(tmp_path / 'state.db')))
    path = tmp_path / 'Stolen.flac'
    path.write_bytes(b'audio')
    details = {'album_id': 1, 'album_folder': str(tmp_path), 'profile_id': 1, 'source': 'musicbrainz', 'release_id': 'release'}
    record_request(db, details, {'source_track_id': 'recording'})
    case = review_case_key(details, None)
    monkeypatch.setattr(mutagen, 'File', lambda *a, **kw: {'musicbrainz_trackid': ['recording'], 'musicbrainz_albumid': ['release']})
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE reconcile_album_plans(case_id TEXT,plan_json TEXT)')
        plan = {'source': 'musicbrainz', 'release_id': 'release', 'assignments': [{'source_track_id': 'recording', 'file_path': str(path), 'status': 'uncertain'}]}
        conn.execute('INSERT INTO reconcile_album_plans VALUES(?,?)', (case, json.dumps(plan)))
        conn.execute("INSERT INTO reconcile_fulfillment(case_id,profile_id,album_id,album_folder,source,release_id,source_track_id,state,published_path) VALUES(?,1,'1',?,'itunes','album','track','published',?)", (case, str(tmp_path), str(path)))
        recover_published_requests(conn)
        assert conn.execute("SELECT state FROM reconcile_fulfillment WHERE source='musicbrainz'").fetchone()[0] == 'requested'
        plan['assignments'][0]['status'] = 'confirmed'
        conn.execute('UPDATE reconcile_album_plans SET plan_json=?', (json.dumps(plan),))
        recover_published_requests(conn)
        assert conn.execute("SELECT state,published_path FROM reconcile_fulfillment WHERE source='musicbrainz'").fetchone() == ('published', str(path))
