"""Regression coverage for reviewed file identity and completed album inventory."""
import os
import threading
import time

from core.library.reconciliation_evidence import file_identity
from core.repair_jobs.library_reconciliation import LibraryReconciliationJob, _suggest_filename
from core.repair_worker import RepairWorker
from core.reorganize_queue import ReorganizeQueue
from tests.library.test_review_acceptance import build_album, scan_case


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


def test_same_size_and_mtime_cannot_hide_a_replaced_file(tmp_path):
    path = tmp_path / 'track.flac'
    path.write_bytes(b'original')
    stat = path.stat()
    details = {'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
               'file_identity': file_identity(stat), 'fingerprint_key': 'reviewed'}
    worker = object.__new__(RepairWorker)
    assert worker._reconciliation_file_guard(str(path), details) is None
    replacement = tmp_path / 'replacement'
    replacement.write_bytes(b'changed!')
    os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    replacement.replace(path)
    assert 'changed after the scan' in worker._reconciliation_file_guard(str(path), details)


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


def test_same_album_planning_waits_and_next_mutation_cannot_overtake_a_plan():
    from core.library.maintenance_lock import MaintenanceLock
    lock = MaintenanceLock()
    entered, finished = threading.Event(), threading.Event()
    def mutate():
        entered.set()
        with lock:
            finished.set()
    with lock.planning('other'):
        thread = threading.Thread(target=mutate)
        thread.start()
        assert entered.wait(1)
        assert not finished.wait(.05)
    thread.join(1)
    assert finished.is_set()
    blocked = threading.Event()
    def plan():
        with lock.planning('same'):
            blocked.set()
    with lock.for_album('same'):
        thread = threading.Thread(target=plan)
        thread.start()
        assert not blocked.wait(.05)
    thread.join(1)
    assert blocked.is_set()


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
        from core.library.reconciliation_evidence import preserve_audio_evidence
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
