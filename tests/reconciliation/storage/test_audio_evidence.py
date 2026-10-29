"""storage / audio evidence regression contracts."""
from __future__ import annotations

import os
import sqlite3
from types import SimpleNamespace

import pytest
from mutagen.flac import FLAC

from core.reconciliation import search as search
from core.reconciliation.evidence import ReconciliationEvidence, file_identity
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
from tests.reconciliation.support.scenarios import (
    _album,
)


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_audio_evidence_cache_survives_rename_but_retries_errors(tmp_path):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    first = tmp_path / '01 - Lucky.flac'
    second = tmp_path / '11 - Lucky.flac'
    first.write_bytes(b'fixture')
    client = _Acoustid({first.name: {'mbid': 'lucky', 'title': 'Lucky',
                                    'artist': 'Radiohead', 'score': 0.99}})
    cache = ReconciliationEvidence(db, client)
    try:
        assert cache.identify(str(first))['status'] == 'ok'
        assert cache.identify(str(first))['status'] == 'ok'
        first.rename(second)
        # The fake fingerprint is filename-based, so make the renamed audio
        # produce the same fingerprint as before, like real Chromaprint.
        client.fingerprint_audio_file = lambda _path: {
            'status': 'ok', 'duration_s': 200, 'fingerprint': first.name}
        assert cache.identify(str(second))['status'] == 'ok'
        assert client.lookups == 1
    finally:
        cache.close()


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_malformed_cached_fingerprint_is_regenerated(tmp_path):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    path = tmp_path / 'Sweet Hitch-Hiker.flac'
    path.write_bytes(b'fixture')
    client = _Acoustid({path.name: {
        'mbid': 'sweet-id', 'title': 'Sweet Hitch-Hiker',
        'artist': 'Creedence Clearwater Revival', 'score': 0.99,
    }})
    stat = path.stat()
    with db._get_connection() as conn:
        conn.execute('''CREATE TABLE reconcile_file_fingerprints (
            file_path TEXT PRIMARY KEY, size_bytes INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL, duration_s REAL NOT NULL,
            fingerprint TEXT NOT NULL)''')
        conn.execute('''INSERT INTO reconcile_file_fingerprints
            VALUES (?, ?, ?, ?, ?)''',
                     (str(path), stat.st_size, stat.st_mtime_ns, 177.4, 'AQAAAA'))
        conn.commit()

    cache = ReconciliationEvidence(db, client)
    try:
        result = cache.identify(str(path))
        assert result['status'] == 'ok'
        assert client.fingerprints == 1
        assert cache.metrics['fingerprint_hits'] == 0
        stored = cache.conn.execute(
            'SELECT fingerprint FROM reconcile_file_fingerprints WHERE file_path = ?',
            (str(path),)).fetchone()[0]
        assert stored != 'AQAAAA'
    finally:
        cache.close()


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_new_malformed_fingerprint_is_a_retryable_file_error(tmp_path):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    path = tmp_path / 'Sweet Hitch-Hiker.flac'
    path.write_bytes(b'fixture')
    client = _Acoustid({})
    client.fingerprint_audio_file = lambda _path: {
        'status': 'ok', 'duration_s': 177.4, 'fingerprint': 'AQAAAA'}
    cache = ReconciliationEvidence(db, client)
    try:
        result = cache.identify(str(path))
        assert result['status'] == 'error'
        assert 'invalid fingerprint' in result['error']
        assert 'fingerprint_key' not in result
        assert cache.conn.execute(
            'SELECT 1 FROM reconcile_file_fingerprints WHERE file_path = ?',
            (str(path),)).fetchone() is None
    finally:
        cache.close()


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_tag_cache_reuses_unchanged_file_and_invalidates_on_write(tmp_path):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    path = tmp_path / 'Lucky.flac'
    path.write_bytes(b'one')
    client = _Acoustid({})
    calls = 0

    def loader(_path):
        nonlocal calls
        calls += 1
        return {'title': f'Lucky {calls}'}

    cache = ReconciliationEvidence(db, client)
    try:
        assert cache.read_tags(str(path), loader)['title'] == 'Lucky 1'
        assert cache.read_tags(str(path), loader)['title'] == 'Lucky 1'
        path.write_bytes(b'changed contents')
        assert cache.read_tags(str(path), loader)['title'] == 'Lucky 2'
        assert cache.metrics['tag_reads'] == 2
        assert cache.metrics['tag_hits'] == 1
    finally:
        cache.close()


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_audio_lookup_error_is_retried_after_cache_reopen(tmp_path, monkeypatch):
    monkeypatch.setattr('core.reconciliation.evidence.time.sleep', lambda _delay: None)
    db = MusicDatabase(str(tmp_path / 'music.db'))
    path = tmp_path / 'Southbound.flac'
    path.write_bytes(b'fixture')
    client = _Acoustid({path.name: {'mbid': 'southbound', 'title': 'Southbound',
                                   'artist': 'Thin Lizzy', 'score': 0.99}})
    original = client.lookup_fingerprint_with_status
    client.lookup_fingerprint_with_status = lambda *_args: {
        'status': 'error', 'recordings': [], 'error': 'rate limited'}
    cache = ReconciliationEvidence(db, client)
    assert cache.identify(str(path))['status'] == 'error'
    cache.close()
    client.lookup_fingerprint_with_status = original
    cache = ReconciliationEvidence(db, client)
    try:
        assert cache.identify(str(path))['status'] == 'ok'
        assert client.fingerprints == 1
        assert client.lookups == 1
    finally:
        cache.close()


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_transient_audio_lookup_error_recovers_in_same_scan(tmp_path, monkeypatch):
    monkeypatch.setattr('core.reconciliation.evidence.time.sleep', lambda _delay: None)
    db = MusicDatabase(str(tmp_path / 'music.db'))
    path = tmp_path / 'Lucky.flac'
    path.write_bytes(b'fixture')
    client = _Acoustid({path.name: {'mbid': 'lucky', 'title': 'Lucky',
                                   'artist': 'Radiohead', 'score': 0.99}})
    original = client.lookup_fingerprint_with_status
    attempts = 0
    def flaky(fingerprint, duration):
        nonlocal attempts
        attempts += 1
        return ({'status': 'error', 'error': 'status: error'} if attempts == 1
                else original(fingerprint, duration))
    client.lookup_fingerprint_with_status = flaky
    cache = ReconciliationEvidence(db, client)
    try:
        assert cache.identify(str(path))['status'] == 'ok'
        assert attempts == 2
        assert cache.identify(str(path))['status'] == 'ok'
        assert attempts == 2
    finally:
        cache.close()


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_exhausted_audio_lookup_opens_circuit_but_cached_evidence_remains_available(
        tmp_path, monkeypatch):
    monkeypatch.setattr('core.reconciliation.evidence.time.sleep', lambda _delay: None)
    db = MusicDatabase(str(tmp_path / 'music.db'))
    failed = [tmp_path / f'Failed {index}.flac' for index in range(3)]
    deferred = tmp_path / 'Deferred.flac'
    cached = tmp_path / 'Cached.flac'
    for path in (*failed, deferred, cached):
        path.write_bytes(b'fixture')
    client = _Acoustid({cached.name: {'mbid': 'cached', 'title': 'Cached',
                                     'artist': 'Artist', 'score': 0.99}})
    cache = ReconciliationEvidence(db, client)
    try:
        assert cache.identify(str(cached))['status'] == 'ok'
        calls = 0

        def unavailable(*_args):
            nonlocal calls
            calls += 1
            return {'status': 'error', 'recordings': [], 'error': 'AcoustID unavailable'}

        client.lookup_fingerprint_with_status = unavailable
        for index, path in enumerate(failed):
            result = cache.identify(str(path))
            assert result['status'] == 'error'
            assert bool(result.get('deferred')) is (index == 2)
        deferred_result = cache.identify(str(deferred))
        assert deferred_result['status'] == 'error'
        assert deferred_result['deferred'] is True
        assert calls == 15
        assert cache.identify(str(cached))['status'] == 'ok'
        assert calls == 15
    finally:
        cache.close()


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_isolated_api_errors_are_scoped_to_files_until_circuit_opens(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module
    monkeypatch.setattr('core.reconciliation.evidence.time.sleep', lambda _delay: None)

    root = tmp_path / 'music'
    root.mkdir()
    for name in ('01.flac', '02.flac', '03.flac'):
        (root / name).write_bytes(b'fixture')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    client = _Acoustid({})
    lookup_attempts = 0

    def unavailable(*_args):
        nonlocal lookup_attempts
        lookup_attempts += 1
        return {'status': 'error', 'recordings': [], 'error': 'AcoustID rate limited'}

    client.lookup_fingerprint_with_status = unavailable
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    findings = []
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client,
                         create_finding=lambda **finding: findings.append(finding) or True)
    result = LibraryReconciliationJob().scan(context)
    assert result.errors == 1
    assert not result.stopped_early
    assert result.scanned == 2
    assert client.fingerprints == 3
    assert lookup_attempts == 15
    # The third exhausted lookup opens the service circuit and preserves the
    # folder. The first two failures did not abort sibling collection.
    assert findings == []


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_file_fingerprint_error_does_not_hide_the_rest_of_its_album(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    root.mkdir()
    failed = root / '01 - Failed.flac'
    good = root / '02 - Good.flac'
    failed.write_bytes(b'failed fixture')
    good.write_bytes(b'good fixture')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, failed)
    with db._get_connection() as conn:
        conn.execute("""INSERT INTO tracks
            (id, album_id, artist_id, title, track_number, file_path)
            VALUES (2, 10, 1, 'Good', 2, ?)""", (str(good),))
        conn.commit()
    client = _Acoustid({good.name: {
        'mbid': 'good-id', 'title': 'Good', 'artist': 'Artist', 'score': 0.99}})
    original_fingerprint = client.fingerprint_audio_file
    client.fingerprint_audio_file = lambda path: (
        {'status': 'error', 'error': 'fpcalc could not decode this file'}
        if path == str(failed) else original_fingerprint(path))
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr(module, '_read_file_tags', lambda path: {
        'title': 'Failed' if path == str(failed) else 'Good',
        'artist': 'Lamb of God', 'album': 'Ashes of the Wake',
        'track_number': 1 if path == str(failed) else 2,
        'disc_number': 1, 'duration_ms': 200000,
    })
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *_args: [
        {'id': 'failed-id', 'name': 'Failed', 'track_number': 1,
         'disc_number': 1, 'duration_ms': 200000},
        {'id': 'good-id', 'name': 'Good', 'track_number': 2,
         'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda *_args: {
        'name': 'Ashes of the Wake'})
    findings = []
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client,
                         create_finding=lambda **finding: findings.append(finding) or True)

    result = LibraryReconciliationJob().scan(context)

    assert result.scanned == 2
    assert any(finding['title'] == 'Confirm: Failed' for finding in findings)


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_api_error_preserves_affected_folder_but_retires_other_stale_findings(
        tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module
    monkeypatch.setattr('core.reconciliation.evidence.time.sleep', lambda _delay: None)

    root = tmp_path / 'music'
    failed = root / 'A failed'
    clean = root / 'B clean'
    failed.mkdir(parents=True)
    clean.mkdir()
    failed_file = failed / '01.flac'
    failed_file.write_bytes(b'failed')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    worker = RepairWorker(database=db)
    for folder, entity in ((failed, 'failed'), (clean, 'clean')):
        worker._create_finding(
            job_id='library_reconciliation', finding_type='reconciliation_unresolved',
            severity='info', entity_type='folder', entity_id=entity,
            file_path=None, title=entity, description=entity,
            details={'album_folder': str(folder), 'evidence_signature': entity})
    client = _Acoustid({})
    client.lookup_fingerprint_with_status = lambda *_args: {
        'status': 'error', 'recordings': [], 'error': 'AcoustID unavailable'}
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)

    result = LibraryReconciliationJob().scan(context)

    assert result.errors == 1
    pending = worker.get_review_cases(job_id='library_reconciliation')['items']
    assert len(pending) == 1
    detail = worker.get_review_case(pending[0]['case_id'], job_id='library_reconciliation')
    assert detail['folder'] == str(failed)


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_embedded_release_identity_refreshes_old_tag_cache(tmp_path):
    from core.repair_jobs.library_reconciliation import _read_reconciliation_tags
    path = tmp_path / 'track.flac'
    _minimal_flac(path, {'title': 'Song'})
    audio = FLAC(path)
    audio['musicbrainz_albumid'] = 'file-release'
    audio.save()
    db = MusicDatabase(str(tmp_path / 'music.db'))
    cache = ReconciliationEvidence(db, None)
    try:
        # The old reader cached no release ID; the new reader must re-read it.
        cache.read_tags(str(path), lambda _: {'title': 'Song'})
        tags = cache.read_tags(str(path), _read_reconciliation_tags)
        assert tags['musicbrainz_release_id'] == 'file-release'
    finally:
        cache.close()


def test_byte_preserving_move_retains_tag_cache_but_tag_writes_invalidate_it(tmp_path, monkeypatch):
    from core.reconciliation.evidence import (
        evidence_path_before_move,
        preserve_audio_evidence,
    )
    album = build_album(tmp_path, monkeypatch, 'supported')
    scan_case(album)
    old = album.paths[0]
    alias = tmp_path / 'alias'
    alias.symlink_to(album.root, target_is_directory=True)
    alternate = alias / old.relative_to(album.root)
    new = old.with_name('Moved.flac')
    stat = old.stat()
    with album.db._get_connection() as conn:
        cache_path = evidence_path_before_move(conn.cursor(), str(alternate))
        assert cache_path == str(old)
        old.rename(new)
        preserve_audio_evidence(conn.cursor(), cache_path, str(new), size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns, preserve_tags=True)
        assert conn.execute('SELECT tags_json FROM reconcile_file_tags WHERE file_path=?', (str(new),)).fetchone()
        preserve_audio_evidence(conn.cursor(), str(new), str(new), size_bytes=new.stat().st_size, mtime_ns=new.stat().st_mtime_ns)
        assert not conn.execute('SELECT 1 FROM reconcile_file_tags WHERE file_path=?', (str(new),)).fetchone()


def test_severe_length_is_red_review_even_with_high_fingerprint_score(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    album.durations[str(album.paths[0])] = 501000
    case = scan_case(album)
    item = next(row for row in case['items'] if row['file_path'] == str(album.paths[0]))
    assert item['review_tier'] == 'destructive'
    assert not case['plan']['can_apply_ready']


def test_disc_totals_write_all_containers_and_keep_matching_cache_reusable():
    from mutagen.id3 import ID3

    from core.reconciliation.match_cache import input_key
    from core.tag_writer import _write_id3, _write_mp4, _write_vorbis
    arguments = [None] * 8 + [1, None]
    id3 = SimpleNamespace(tags=ID3())
    vorbis, mp4 = {}, {}
    _write_id3(id3, *arguments, total_discs=1)
    _write_vorbis(vorbis, *arguments, total_discs=1)
    _write_mp4(mp4, *arguments, total_discs=1)
    assert str(id3.tags['TPOS']) == '1/1'
    assert vorbis['totaldiscs'] == vorbis['disctotal'] == ['1']
    assert mp4['disk'] == [(1, 1)]
    arguments[-2] = 2
    _write_mp4(mp4, *arguments, total_discs=2)
    assert mp4['disk'] == [(2, 2)]
    before = input_key('policy', [{'tags': {'title': 'Song'}}], [{'title': 'Song'}], {}, {})
    after = input_key('policy', [{'tags': {'title': 'Song', 'total_discs': 1}}], [{'title': 'Song', 'audio_copy_of_video': False}], {}, {})
    assert before == after


def test_scan_refreshes_legacy_tag_cache_without_repeating_audio_lookup(tmp_path, monkeypatch):
    from core.library import release_identity
    from core.reconciliation.evidence import ReconciliationEvidence
    from core.repair_jobs import library_reconciliation as job
    db = SimpleNamespace(_get_connection=lambda: sqlite3.connect(str(tmp_path / 'evidence.db')))
    path = tmp_path / 'Song.flac'
    path.write_bytes(b'audio')
    cache = ReconciliationEvidence(db, None)
    old = {'title': 'Song', 'musicbrainz_release_id': 'release', 'artists_list': ['Artist']}
    cache.read_tags(str(path), lambda _: old)
    reads = []
    monkeypatch.setattr(job, '_read_file_tags', lambda p: reads.append(p) or {**old, 'total_discs': 2})
    monkeypatch.setattr(release_identity, 'read_release_evidence', lambda _: {})
    try:
        assert cache.read_tags(str(path), job._read_reconciliation_tags)['total_discs'] == 2
        assert cache.read_tags(str(path), job._read_reconciliation_tags)['total_discs'] == 2
        assert reads == [str(path)]
        assert cache.metrics['lookups'] == cache.metrics['fingerprints'] == 0
    finally:
        cache.close()


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
