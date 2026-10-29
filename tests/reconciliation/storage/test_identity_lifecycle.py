"""storage / identity lifecycle regression contracts."""
from __future__ import annotations

import json
import sqlite3

import pytest

from core.library.repair_files import _same_reviewed_file
from core.library_reorganize import (
    _display_relative_to_root,
    _rename_track_in_place,
)
from core.reconciliation import search as search
from core.reconciliation.fulfillment import (
    record_request,
)
from core.reconciliation.grouping import _group_logical_albums
from core.reconciliation.identity import missing_slot_key, review_case_key
from core.reconciliation.migration import migrate_album
from core.reconciliation.queue import (
    get_case,
    list_cases,
)
from core.repair_jobs.base import JobContext
from core.repair_jobs.library_reconciliation import LibraryReconciliationJob
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase
from tests.reconciliation.support.acceptance import build_album, scan_case
from tests.reconciliation.support.acquisition import Database
from tests.reconciliation.support.queue import _Database
from tests.reconciliation.support.scenarios import (
    _album,
)


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


def test_saved_choice_survives_only_a_verified_relocation(tmp_path):
    from core.reconciliation.choices import ensure_schema as ensure_choices
    db = MusicDatabase(str(tmp_path / 'music.db'))
    worker = RepairWorker(database=db)
    details = {'album_id': 'album', 'album_folder': '/old', 'fingerprint_key': 'audio',
               'size_bytes': 100, 'mtime_ns': 1, 'evidence_signature': 'old',
               'changes': {'title': {'old': 'Old', 'new': 'New'}}}
    finding = dict(job_id='library_reconciliation', finding_type='reconciliation_metadata',
                   severity='info', entity_type='track', entity_id='track',
                   title='Change title', description='Supported')
    assert worker._create_finding(**finding, file_path='/old/song.flac', details=details)
    with db._get_connection() as c:
        ensure_choices(c)
        fid, key, raw = c.execute('SELECT id,review_case_key,details_json FROM repair_findings').fetchone()
        c.execute("INSERT INTO reconcile_review_choices(finding_id,case_id,evidence_json,category,choice) VALUES (?,?,?,'metadata','fix')", (fid,key,raw))
    relocated = {**details, 'album_folder': '/new', 'mtime_ns': 2, 'evidence_signature': 'new'}
    assert not worker._create_finding(**finding, file_path='/new/song.flac', details=relocated)
    key = review_case_key(relocated, '/new/song.flac', fid)
    assert get_case(db,key)['items'][0]['details']['plan_approved']
    changed = {**relocated, 'fingerprint_key': 'different audio'}
    worker._create_finding(**finding, file_path='/new/song.flac', details=changed)
    assert not get_case(db,key)['items'][0]['details'].get('plan_approved')


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_load_rows_indexes_the_scan_mount_when_stored_mount_also_exists(tmp_path):
    server_root = tmp_path / 'server-mount'
    scan_root = tmp_path / 'scan-mount'
    suffix = ('Lamb of God', 'Ashes of the Wake', '01 - Laid to Rest.flac')
    server_path = server_root.joinpath(*suffix)
    scan_path = scan_root.joinpath(*suffix)
    server_path.parent.mkdir(parents=True)
    scan_path.parent.mkdir(parents=True)
    server_path.write_bytes(b'audio fixture')
    scan_path.hardlink_to(server_path)
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, server_path)
    context = JobContext(db=db, transfer_folder=str(scan_root), config_manager=None)

    tracks, _albums = LibraryReconciliationJob()._load_rows(context, [str(scan_root)])

    assert tracks[str(scan_path)]['id'] == '1'


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize('shared_provider', [False, True])
@pytest.mark.parametrize('conflict', ['', 'release', 'artist', 'title', 'position', 'owner', 'unlinked_provider'])
def test_same_folder_library_split_has_one_compatible_owner(conflict, shared_provider):
    folder = '/music/Ghost/Prequelle'
    paths = [f'{folder}/{number:02}.flac' for number in range(1, 11)]
    tracks = {path: {'album_id': 2 if number == 3 else 1, 'track_number': number, 'disc_number': 1}
              for number, path in enumerate(paths, 1)}
    albums = {1: {'album_title': 'Prequelle', 'artist_name': 'Ghost', 'owner_profile_id': 1},
              2: {'album_title': 'Prequelle', 'artist_name': 'Ghost', 'owner_profile_id': 1,
                  'musicbrainz_release_id': 'release'}}
    if shared_provider:
        for album in albums.values():
            album['itunes_album_id'] = 'shared-itunes'
        albums[2].update(canonical_source='musicbrainz', canonical_album_id='release', canonical_locked=True)
    if conflict == 'release': albums[1]['musicbrainz_release_id'] = 'another-release'
    if conflict == 'artist': albums[1]['artist_name'] = 'Another Artist'
    if conflict == 'title': albums[1]['album_title'] = 'Prequelle (Live)'
    if conflict == 'position': tracks[paths[0]]['track_number'] = 3
    if conflict == 'owner': albums[1]['owner_profile_id'] = 2
    if conflict == 'unlinked_provider': albums[1]['itunes_album_id'] = 'unrelated-itunes'
    _, trusted = _group_logical_albums({folder: paths}, tracks, albums)
    assert trusted == ({} if conflict else {folder: 2})


@pytest.mark.parametrize('live_alias', [False, True])
def test_organization_reuses_audio_when_database_and_scan_use_different_mount_paths(tmp_path, monkeypatch, live_alias):
    from types import SimpleNamespace

    from core.reconciliation.evidence import ReconciliationEvidence
    from core.reorganize_runner import build_runner
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    for item in case['items']:
        assert album.worker._apply_reviewed_finding(item['id'])['success']
    client = album.context.acoustid_client
    decoded = client.fingerprints
    alias = tmp_path / 'other-mount'
    if live_alias:
        alias.symlink_to(album.root, target_is_directory=True)
    aliases = {str(alias / path.relative_to(album.root)): str(path) for path in album.paths}
    with album.db._get_connection() as conn:
        for old, actual in aliases.items():
            conn.execute('UPDATE tracks SET file_path=? WHERE file_path=?', (old, actual))
    target = album.root / 'Moved'
    monkeypatch.setattr('core.imports.paths.library_root_for_profile', lambda *a, **k: str(album.root))
    monkeypatch.setattr('core.imports.paths.library_containing', lambda path: str(album.root))
    monkeypatch.setattr('core.reorganize_queue.get_queue', lambda: SimpleNamespace(update_active_progress=lambda **k: None))
    runner = build_runner(
        get_database=lambda: album.db,
        resolve_file_path_fn=lambda path: path if live_alias else aliases.get(path, path),
        post_process_fn=lambda *a: None,
        cleanup_empty_directories_fn=lambda *a: None,
        is_shutting_down_fn=lambda: False,
        get_download_path=lambda: str(tmp_path / 'downloads'),
        get_transfer_path=lambda: str(album.root),
        build_final_path_fn=lambda context, *a, **kw: (str(target / f"{context['track_info']['track_number']}.flac"), True),
    )
    outcome = runner(SimpleNamespace(album_id='10', queue_id='test', source=None,
                                     rename_only=True, metadata_source='tags'))
    assert outcome['moved'] == 2, outcome
    cache = ReconciliationEvidence(album.db, client)
    try:
        for path in target.glob('*.flac'):
            assert cache.identify(str(path))['status'] == 'ok'
        assert client.fingerprints == decoded
        assert cache.metrics['fingerprint_hits'] == 2
    finally:
        cache.close()


@pytest.mark.parametrize('case', ['album:1:old', 'folder:1:old:abcdef', 'legacy-stable-key'])
def test_album_id_migration_preserves_all_review_state(tmp_path, case):
    db = Database(tmp_path / 'migration.db')
    details = {'album_id': 'old', 'profile_id': 1, 'source': 'musicbrainz', 'release_id': 'release'}
    record_request(db, details, {'source_track_id': 'recording', 'disc_number': 1, 'track_number': 2})
    with db._get_connection() as conn:
        conn.execute('UPDATE reconcile_fulfillment SET case_id=?, state="published", published_path="/new.flac"', (case,))
        conn.execute('CREATE TABLE reconcile_album_plans(case_id TEXT PRIMARY KEY, album_id TEXT, plan_json TEXT)')
        conn.execute('INSERT INTO reconcile_album_plans VALUES(?,?,?)', (case, 'old', json.dumps({'album_id':'old','case_id':case,'inventory':{'track_ids':['t1']}})))
        conn.execute('CREATE TABLE reconcile_album_intent(album_id TEXT PRIMARY KEY,scope TEXT)')
        conn.execute('INSERT INTO reconcile_album_intent VALUES("old","partial")')
        conn.execute('CREATE TABLE reconcile_review_choices(finding_id INTEGER PRIMARY KEY,case_id TEXT,evidence_json TEXT)')
        conn.execute('INSERT INTO reconcile_review_choices VALUES(1,?,?)',(case,json.dumps(details)))
        migrate_album(conn, 'old', 'new')
        assert conn.execute('SELECT album_id,scope FROM reconcile_album_intent').fetchone() == ('new','partial')
        assert conn.execute('SELECT album_id,state,published_path FROM reconcile_fulfillment').fetchone() == ('new','published','/new.flac')
        updated, raw = conn.execute('SELECT case_id,plan_json FROM reconcile_album_plans').fetchone()
        assert updated == case.replace(':old', ':new')
        assert json.loads(raw)['album_id'] == 'new'
        assert conn.execute('SELECT album_id FROM reconcile_wanted_slots').fetchone()[0] == 'new'
        assert json.loads(conn.execute('SELECT evidence_json FROM reconcile_review_choices').fetchone()[0])['album_id'] == 'new'
        migrate_album(conn, 'old', 'new')  # a replay cannot delete the migrated records
        assert conn.execute('SELECT count(*) FROM reconcile_fulfillment').fetchone()[0] == 1


@pytest.mark.parametrize('owner,expected', [(1,'new'),(2,'old')])
def test_legacy_album_and_track_id_changes_require_same_physical_file_and_profile(tmp_path, owner, expected):
    from core.reconciliation.migration import repair_legacy_ids
    db=Database(tmp_path/'legacy.db')
    path=tmp_path/'01 - 100%_Rolling.flac';path.write_bytes(b'audio')
    alias=tmp_path/'alias';alias.symlink_to(tmp_path,target_is_directory=True)
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE albums(id TEXT,owner_profile_id INTEGER)')
        conn.execute('INSERT INTO albums VALUES("new",?)',(owner,))
        conn.execute('CREATE TABLE tracks(id TEXT,album_id TEXT,file_path TEXT)')
        conn.execute('INSERT INTO tracks VALUES("new-track","new",?)',(str(alias/path.name),))
        conn.execute('CREATE TABLE reconcile_album_plans(case_id TEXT PRIMARY KEY,album_id TEXT,profile_id INTEGER,plan_json TEXT)')
        conn.execute('INSERT INTO reconcile_album_plans VALUES("album:1:old","old",1,?)',
            (json.dumps({'inventory':{'track_ids':['old-track'],'paths':[str(path)]}}),))
        conn.execute('CREATE TABLE reconcile_album_intent(album_id TEXT PRIMARY KEY,scope TEXT)')
        conn.execute('INSERT INTO reconcile_album_intent VALUES("old","partial")')
    repair_legacy_ids(db)
    with db._get_connection() as conn:
        assert conn.execute('SELECT album_id FROM reconcile_album_intent').fetchone()[0] == expected
        assert conn.execute('SELECT count(*) FROM reconcile_album_plans').fetchone()[0] == 1


@pytest.mark.parametrize('new_scope,expected', [(None,'partial'),('complete','complete')])
def test_album_id_migration_retains_inferred_scope_without_overriding_new_choice(tmp_path,new_scope,expected):
    from core.reconciliation.migration import migrate_album
    db=Database(tmp_path/'scope.db')
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE reconcile_album_plans(case_id TEXT PRIMARY KEY,album_id TEXT,plan_json TEXT)')
        conn.execute('INSERT INTO reconcile_album_plans VALUES("album:1:old","old",?)',
            (json.dumps({'source':'musicbrainz','release_id':'release','intent_scope':'partial'}),))
        conn.execute('CREATE TABLE reconcile_album_intent(album_id TEXT PRIMARY KEY,scope TEXT,source TEXT,release_id TEXT)')
        if new_scope:
            conn.execute('INSERT INTO reconcile_album_intent VALUES("new",?,"musicbrainz","release")',(new_scope,))
        migrate_album(conn,'old','new')
        assert conn.execute('SELECT scope FROM reconcile_album_intent WHERE album_id="new"').fetchone()[0]==expected


def test_alias_rename_is_noop_but_distinct_collision_is_preserved(tmp_path):
    root = tmp_path / 'real'
    root.mkdir()
    path = root / 'song.flac'
    path.write_bytes(b'audio')
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    before = path.stat()
    assert _rename_track_in_place(str(alias / path.name), str(path)) == (True, None)
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert path.read_bytes() == b'audio'
    assert _display_relative_to_root(str(alias / path.name), str(root)) == path.name
    other = root / 'other.flac'
    other.write_bytes(b'different')
    assert _rename_track_in_place(str(path), str(other)) == (False, 'destination already exists')
    assert other.read_bytes() == b'different'


def test_failed_organization_report_survives_relocation_and_allows_only_folder_retry(tmp_path, monkeypatch):
    from core.repair_operation_reports import save_report
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    for item in case['items']:
        assert album.worker._apply_reviewed_finding(item['id'])['success']
    with album.db._get_connection() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS reconcile_case_redirects (old_case_id TEXT PRIMARY KEY,new_case_id TEXT NOT NULL)')
        conn.execute('INSERT INTO reconcile_case_redirects VALUES (?,?)', ('previous-case', case['case_id']))
    save_report(album.db, {'operation_id': 'failed-organization', 'review_case_ids': ['previous-case'], 'running': False, 'fixed': 2, 'organize_failed': 1, 'error': 'destination already exists'})
    completed = album.worker.get_review_case(case['case_id'])
    assert completed['last_operation']['organize_failed'] == 1
    assert completed['organization_retry_signature']
    calls = []
    monkeypatch.setattr(album.worker, 'start_bulk_fix', lambda **kwargs: calls.append(kwargs) or {'started': True})
    assert not album.worker.retry_review_organization(case['case_id'], 'stale')['started']
    assert album.worker.retry_review_organization(case['case_id'], completed['organization_retry_signature'])['started']
    assert calls[0]['finding_ids'] == [] and calls[0]['organization_retry']
    # A changed file invalidates the previously reviewed folder retry.
    album.paths[0].write_bytes(album.paths[0].read_bytes() + b'changed')
    assert not album.worker.retry_review_organization(case['case_id'], completed['organization_retry_signature'])['started']


def test_same_title_different_release_and_owner_never_merge():
    db = _Database()
    for finding_id, album_id, profile in ((1, 10, 1), (2, 11, 1), (3, 10, 2)):
        db.add(finding_id, f'/music/{finding_id}.flac',
               {'album_id': album_id, 'profile_id': profile,
                'album_title': 'Rated R', 'artist_name': 'Queens of the Stone Age'})
    cases = list_cases(db)
    assert cases['total'] == 3
    assert all(case['pending'] == 1 for case in cases['items'])


def test_missing_slot_identity_includes_source_track_when_positions_repeat():
    a = {'disc_number': 1, 'track_number': 1, 'source_track_id': 'intro-a'}
    b = {'disc_number': 1, 'track_number': 1, 'source_track_id': 'intro-b'}
    assert missing_slot_key(10, 'spotify', 'release', a) != missing_slot_key(
        10, 'spotify', 'release', b)


def test_existing_findings_are_backfilled_into_exact_cases(tmp_path):
    conn = sqlite3.connect(tmp_path / 'old-repair.db')
    try:
        conn.execute('''CREATE TABLE repair_findings (
            id INTEGER PRIMARY KEY, job_id TEXT, finding_type TEXT, status TEXT,
            entity_type TEXT, entity_id TEXT, file_path TEXT, details_json TEXT,
            resolved_at TEXT, user_action TEXT
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
