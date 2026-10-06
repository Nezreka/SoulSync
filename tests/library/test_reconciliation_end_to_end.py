"""Exercise the opt-in scan, persisted cases, intent, and exact wishlist request.

The local test-data database contains no library rows, so the fixtures model
albums the user previously investigated with real files on disk and a real
SoulSync SQLite schema. Only remote AcoustID/tracklist responses are stubbed.
"""

from __future__ import annotations

import json
import struct

from mutagen.flac import FLAC

from core.library.reconciliation_evidence import ReconciliationEvidence
from core.repair_jobs.base import JobContext
from core.repair_jobs.library_reconciliation import (
    LibraryReconciliationJob, _candidate_evidence, _candidate_shape, _group_disc_folders,
    _normalize_slot, _same_recording_copies, _suggest_filename,
)
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


class _Acoustid:
    def __init__(self, matches):
        self.matches = matches
        self.fingerprints = 0
        self.lookups = 0

    def is_available(self):
        return True, ''

    def fingerprint_audio_file(self, path):
        self.fingerprints += 1
        return {'status': 'ok', 'duration_s': 200,
                'fingerprint': path.rsplit('/', 1)[-1]}

    def lookup_fingerprint_with_status(self, fingerprint, duration):
        self.lookups += 1
        return {'status': 'ok', 'recordings': [self.matches[fingerprint]]}


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

    pair = _candidate_evidence(
        files, (1, 1, 0, 'musicbrainz', 'release-id', slots, plan))['pairs'][0]

    assert pair['audio_title'] == 'Tautou'
    assert pair['audio_alternatives'] == [
        {'mbid': 'play-id', 'title': 'Play Crack the Sky',
         'artist': 'Brand New', 'score': 0.999, 'recording_count': 2},
        {'mbid': 'play-live-id', 'title': 'Play Crack the Sky (Live)',
         'artist': 'Brand New', 'score': 0.999, 'recording_count': 1},
    ]
    assert pair['audio_recording_count'] == 1


def test_candidate_shape_sorts_numbered_and_unnumbered_missing_slots():
    slots = [
        {'title': 'Numbered', 'disc_number': 1, 'track_number': 2},
        {'title': 'Unnumbered', 'disc_number': 1, 'track_number': None},
    ]
    plan = {'pairs': [], 'missing_slots': [1, 0], 'extra_files': []}

    shape = _candidate_shape((0, 0, 0, 'musicbrainz', 'release', slots, plan))

    assert shape[2] == (
        (1, False, 2, 'numbered', ''),
        (1, True, 0, 'unnumbered', ''),
    )


def test_musicbrainz_slot_keeps_recording_disambiguation():
    slot = _normalize_slot({
        'id': 'recording-id', 'name': 'Song',
        'disambiguation': 'Connect Sets acoustic',
    }, 'musicbrainz')

    assert slot['recording_disambiguation'] == 'Connect Sets acoustic'


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


def _album(db, path):
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES (1, 'Lamb of God', 'test')")
        conn.execute("""INSERT INTO albums
            (id, title, artist_id, server_source, canonical_source, canonical_album_id)
            VALUES (10, 'Ashes of the Wake', 1, 'test', 'spotify', 'ashes-release')""")
        conn.execute("""INSERT INTO tracks
            (id, album_id, artist_id, title, track_number, file_path)
            VALUES (1, 10, 1, 'Laid to Rest', 1, ?)""", (str(path),))
        conn.commit()


def _minimal_flac(path, tags):
    streaminfo = bytearray(34)
    streaminfo[0:2] = struct.pack('>H', 4096)
    streaminfo[2:4] = struct.pack('>H', 4096)
    streaminfo[10] = 0x0A
    streaminfo[12] = 0x70
    path.write_bytes(b'fLaC' + bytes([0x80, 0, 0, 34]) + bytes(streaminfo))
    audio = FLAC(str(path))
    for key, value in tags.items():
        audio[key] = [value]
    audio.save()


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


def test_scan_review_intent_and_exact_missing_request(tmp_path, monkeypatch):
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
    monkeypatch.setattr(module, 'get_album_tracks_for_source',
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
    monkeypatch.setattr(module, 'get_album_for_source',
                        lambda *_args: {'name': 'Ashes of the Wake',
                                        'artists': [{'name': 'Lamb of God'}]})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)
    scan = LibraryReconciliationJob()
    first = scan.scan(context)
    assert first.scanned == 1
    assert first.findings_created == 1
    case_id = worker.get_review_cases()['items'][0]['case_id']
    item = worker.get_review_case(case_id)['items'][0]
    assert item['finding_type'] == 'reconciliation_intent'
    assert client.fingerprints == client.lookups == 1

    result = worker.apply_review_decision(case_id, item['id'],
                                          evidence_token=item['evidence_token'],
                                          action='fix', fix_action='complete')
    assert result['success'], result
    second = scan.scan(context)
    assert second.scanned == 1
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
    assert db.set_album_canonical(10, 'spotify', 'alternate-edition', 0.8, locked=True)
    scan.scan(context)
    pending = worker.get_review_case(case_id)['items']
    assert any(item['finding_type'] == 'reconciliation_intent' and
               item['details']['release_id'] == 'alternate-edition' for item in pending)


def test_stored_release_breaks_an_equal_fit_without_human_review(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    folder = root / 'Lamb of God' / 'Ashes of the Wake'
    folder.mkdir(parents=True)
    path = folder / '01 - Laid to Rest.flac'
    path.write_bytes(b'audio fixture')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    with db._get_connection() as conn:
        conn.execute("UPDATE albums SET musicbrainz_release_id = 'other-release' WHERE id = 10")
        conn.commit()
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
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args: [
        {'id': 'laid-id', 'name': 'Laid to Rest',
         'artists': [{'name': 'Lamb of God'}],
         'track_number': 1, 'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr(module, 'get_album_for_source', lambda *_args: {
        'name': 'Ashes of the Wake', 'artists': [{'name': 'Lamb of God'}]})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)

    result = LibraryReconciliationJob().scan(context)

    assert result.findings_created == 0
    assert worker.get_review_cases(job_id='library_reconciliation')['total'] == 0


def test_lone_playlist_track_is_treated_as_partial_album_intent(tmp_path, monkeypatch):
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
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args: [
        {'id': 'laid-id', 'name': 'Laid to Rest',
         'artists': [{'name': 'Lamb of God'}],
         'track_number': 1, 'disc_number': 1, 'duration_ms': 200000},
        {'id': 'hourglass-id', 'name': 'Hourglass',
         'artists': [{'name': 'Lamb of God'}],
         'track_number': 2, 'disc_number': 1, 'duration_ms': 240000},
    ])
    monkeypatch.setattr(module, 'get_album_for_source', lambda *_args: {
        'name': 'Ashes of the Wake', 'artists': [{'name': 'Lamb of God'}]})
    context = JobContext(
        db=db, transfer_folder=str(root), config_manager=None,
        acoustid_client=client, create_finding=worker._create_finding,
        playlist_membership=lambda: {'1': ['Imported playlist']},
    )

    result = LibraryReconciliationJob().scan(context)

    assert result.findings_created == 0
    assert worker.get_review_cases(job_id='library_reconciliation')['total'] == 0


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
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args: [
        {'id': 'laid-id', 'name': 'Laid to Rest', 'track_number': 1,
         'disc_number': 1, 'duration_ms': 200000},
        {'id': 'hourglass-id', 'name': 'Hourglass', 'track_number': 2,
         'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr(module, 'get_album_for_source', lambda *_args: {
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


def test_audio_lookup_error_is_retried_after_cache_reopen(tmp_path, monkeypatch):
    monkeypatch.setattr('core.library.reconciliation_evidence.time.sleep', lambda _delay: None)
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


def test_transient_audio_lookup_error_recovers_in_same_scan(tmp_path, monkeypatch):
    monkeypatch.setattr('core.library.reconciliation_evidence.time.sleep', lambda _delay: None)
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


def test_exhausted_audio_lookup_opens_circuit_but_cached_evidence_remains_available(
        tmp_path, monkeypatch):
    monkeypatch.setattr('core.library.reconciliation_evidence.time.sleep', lambda _delay: None)
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
        for path in failed:
            result = cache.identify(str(path))
            assert result['status'] == 'error'
            assert 'deferred' not in result
        deferred_result = cache.identify(str(deferred))
        assert deferred_result['status'] == 'error'
        assert deferred_result['deferred'] is True
        assert calls == 15
        assert cache.identify(str(cached))['status'] == 'ok'
        assert calls == 15
    finally:
        cache.close()


def test_api_error_skips_affected_folder_without_repeating_failed_requests(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module
    monkeypatch.setattr('core.library.reconciliation_evidence.time.sleep', lambda _delay: None)

    root = tmp_path / 'music'
    root.mkdir()
    for name in ('01.flac', '02.flac', '03.flac'):
        (root / name).write_bytes(b'fixture')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    client = _Acoustid({})
    client.lookup_fingerprint_with_status = lambda *_args: {
        'status': 'error', 'recordings': [], 'error': 'AcoustID rate limited'}
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    findings = []
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client,
                         create_finding=lambda **finding: findings.append(finding) or True)
    result = LibraryReconciliationJob().scan(context)
    assert result.errors == 1
    assert not result.stopped_early
    assert client.fingerprints == 1
    assert findings == []


def test_api_error_preserves_affected_folder_but_retires_other_stale_findings(
        tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module
    monkeypatch.setattr('core.library.reconciliation_evidence.time.sleep', lambda _delay: None)

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
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args: [
        {'id': 'laid-id', 'name': 'Laid to Rest', 'track_number': 1,
         'disc_number': 1, 'duration_ms': 200000},
        {'id': 'hourglass-id', 'name': 'Hourglass', 'track_number': 2,
         'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr(module, 'get_album_for_source', lambda *_args: {
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
    monkeypatch.setattr(module, 'get_album_tracks_for_source',
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


def test_release_slot_numbers_are_normalized_before_db_comparison():
    slot = _normalize_slot({'id': 'hourglass-id', 'name': 'Hourglass',
                            'track_number': '02', 'disc_number': '1',
                            'duration_ms': '200000'}, 'spotify')
    assert (slot['track_number'], slot['disc_number'], slot['duration_ms']) == (2, 1, 200000)


def test_release_discovery_samples_distinct_files_not_three_ids_from_first_file():
    calls = []
    class MusicBrainz:
        def get_recording(self, mbid, includes):
            calls.append(mbid)
            assert includes == ['releases']
            return {'releases': [{'id': 'shared-release'}]}

    files = [{'acoustid': {'recordings': [
        {'mbid': f'best-{index}', 'title': f'Song {index}', 'score': 0.99},
        {'mbid': f'other-{index}', 'title': f'Other {index}', 'score': 0.50},
    ]}} for index in range(3)]
    context = JobContext(db=None, transfer_folder='', config_manager=None,
                         mb_client=MusicBrainz())
    candidates = LibraryReconciliationJob()._release_candidates(
        files, None, context, include_audio=True)
    assert calls == ['best-0', 'best-1', 'best-2']
    assert candidates == [('musicbrainz', 'shared-release')]


def test_wrong_audio_defers_completion_intent_until_assignment_is_settled(tmp_path, monkeypatch):
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
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args: [
        {'id': 'wanted-id', 'name': 'Laid to Rest', 'track_number': 1,
         'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr(module, 'get_album_for_source',
                        lambda *_args: {'name': 'Ashes of the Wake'})
    client = _Acoustid({path.name: {'mbid': 'other-audio', 'title': 'Southbound',
                                   'artist': 'Thin Lizzy', 'score': 0.99}})
    worker = RepairWorker(database=db, transfer_folder=str(root))
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda _self: client))
    monkeypatch.setattr('core.repair_worker._server_playlist_membership',
                        lambda: {'1': ['My playlist']})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)
    LibraryReconciliationJob().scan(context)
    case_id = worker.get_review_cases()['items'][0]['case_id']
    findings = worker.get_review_case(case_id)['items']
    assert {finding['finding_type'] for finding in findings} == {
        'reconciliation_extra'}
    assert path.exists()  # scanning never edits a file
    extra = next(finding for finding in findings if finding['finding_type'] == 'reconciliation_extra')
    assert worker.fix_finding(extra['id'], fix_action='quarantine')['success'] is False
    with db._get_connection() as conn:
        conn.execute("UPDATE tracks SET file_path = '/music/another.flac' WHERE id = 1")
        conn.commit()
    changed = worker.apply_review_decision(case_id, extra['id'],
                                           evidence_token=extra['evidence_token'],
                                           action='fix', fix_action='quarantine')
    assert changed['success'] is False and 'another file' in changed['error']
    assert path.exists()
    with db._get_connection() as conn:
        conn.execute('UPDATE tracks SET file_path = ? WHERE id = 1', (str(path),))
        conn.commit()
    extra = next(finding for finding in worker.get_review_case(case_id)['items']
                 if finding['finding_type'] == 'reconciliation_extra')
    blocked = worker.apply_review_decision(case_id, extra['id'],
                                           evidence_token=extra['evidence_token'],
                                           action='fix', fix_action='quarantine')
    assert blocked['success'] is False and 'playlist' in blocked['error']
    assert path.exists()
    monkeypatch.setattr('core.repair_worker._server_playlist_membership', lambda: {})
    extra = next(finding for finding in worker.get_review_case(case_id)['items']
                 if finding['finding_type'] == 'reconciliation_extra')
    result = worker.apply_review_decision(case_id, extra['id'],
                                          evidence_token=extra['evidence_token'],
                                          action='fix', fix_action='quarantine')
    assert result['success'], result
    assert not path.exists()
    assert (root / '.deleted' / path.name).exists()
    with db._get_connection() as conn:
        assert conn.execute('SELECT id FROM tracks WHERE id = 1').fetchone() is None


def test_locked_release_with_better_fitting_edition_requires_release_review(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    root.mkdir()
    path = root / '01 - Hourglass.flac'
    path.write_bytes(b'audio fixture')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    with db._get_connection() as conn:
        conn.execute("UPDATE albums SET spotify_album_id = 'better-edition' WHERE id = 10")
        conn.commit()
    assert db.set_album_canonical(10, 'spotify', 'ashes-release', 0.9, locked=True)
    worker = RepairWorker(database=db)
    client = _Acoustid({path.name: {'mbid': 'hourglass-id', 'title': 'Hourglass',
                                   'artist': 'Lamb of God', 'score': 0.99}})
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr(module, '_read_file_tags', lambda _path: {
        'title': 'Hourglass', 'artist': 'Lamb of God', 'album': 'Ashes of the Wake',
        'track_number': 1, 'disc_number': 1, 'duration_ms': 200000,
    })
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda _source, release: [
        {'id': 'hourglass-id' if release == 'better-edition' else 'other-id',
         'name': 'Hourglass' if release == 'better-edition' else 'Other Song',
         'track_number': 1, 'disc_number': 1, 'duration_ms': 200000},
    ])
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)
    LibraryReconciliationJob().scan(context)
    case_id = worker.get_review_cases()['items'][0]['case_id']
    finding = worker.get_review_case(case_id)['items'][0]
    assert finding['finding_type'] == 'reconciliation_release'
    assert finding['details']['locked_release'] == {
        'source': 'spotify', 'release_id': 'ashes-release'}
    better = finding['details']['candidates'][0]
    assert better['release_id'] == 'better-edition'
    assert better['pairs'][0]['file'] == path.name
    assert better['pairs'][0]['audio_title'] == 'Hourglass'
    assert better['pairs'][0]['slot_title'] == 'Hourglass'
    assert better['pairs'][0]['confidence'] == 'clear'
    assert better['pairs'][0]['file_duration_ms'] == 200000
    assert better['pairs'][0]['slot_duration_ms'] == 200000
    assert better['pairs'][0]['duration_delta_ms'] == 0
    assert better['pairs'][0]['ambiguity_reasons'] == []


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
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args: [
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
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args: [
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
    assert first.exists()
    assert not second.exists()
    assert not third.exists()
    with db._get_connection() as conn:
        assert [row[0] for row in conn.execute(
            'SELECT id FROM tracks ORDER BY id').fetchall()] == ['1']


def test_audio_backed_retag_writes_real_flac_and_track_row(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module

    root = tmp_path / 'music'
    root.mkdir()
    path = root / '01 - Laid to Rest.flac'
    _minimal_flac(path, {'title': 'Laid to Rest', 'artist': 'Lamb of God',
                         'album': 'Ashes of the Wake', 'tracknumber': '1'})
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    assert db.set_album_canonical(10, 'musicbrainz', 'ashes-mb-release', 0.9, locked=True)
    worker = RepairWorker(database=db)
    client = _Acoustid({path.name: {'mbid': 'hourglass-id', 'title': 'Hourglass',
                                   'artist': 'Lamb of God', 'score': 0.99}})
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda _self: client))
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args: [
        {'id': 'hourglass-id', 'name': 'Hourglass',
         'disambiguation': 'alternate studio recording',
         'artists': [{'name': 'Lamb of God'}],
         'track_number': 2, 'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr(module, 'get_album_for_source',
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
    assert db.set_album_canonical(10, 'musicbrainz', 'different-release', 0.9, locked=True)
    stale = worker.apply_review_decision(case_id, finding['id'],
                                         evidence_token=finding['evidence_token'], action='fix')
    assert stale['success'] is False and 'rescan' in stale['error']
    assert FLAC(str(path))['title'][0] == 'Laid to Rest'
    assert db.set_album_canonical(10, 'musicbrainz', 'ashes-mb-release', 0.9, locked=True)
    finding = worker.get_review_case(case_id)['items'][0]
    with db._get_connection() as conn:
        conn.execute("UPDATE tracks SET recording_disambiguation = 'changed after scan' "
                     "WHERE id = 1")
        conn.commit()
    stale_identity = worker.apply_review_decision(
        case_id, finding['id'], evidence_token=finding['evidence_token'], action='fix')
    assert stale_identity['success'] is False and 'rescan' in stale_identity['error']
    with db._get_connection() as conn:
        conn.execute('UPDATE tracks SET recording_disambiguation = NULL WHERE id = 1')
        conn.commit()
    finding = worker.get_review_case(case_id)['items'][0]
    target_path = root / '02 - Hourglass.flac'
    target_path.write_bytes(b'collision')
    collision = worker.apply_review_decision(case_id, finding['id'],
                                             evidence_token=finding['evidence_token'], action='fix')
    assert collision['success'] is False and 'already exists' in collision['error']
    assert FLAC(str(path))['title'][0] == 'Laid to Rest'
    target_path.unlink()
    finding = worker.get_review_case(case_id)['items'][0]
    applied = worker.apply_review_decision(case_id, finding['id'],
                                           evidence_token=finding['evidence_token'], action='fix')
    assert applied['success'], applied
    assert not path.exists()
    assert FLAC(str(target_path))['title'][0] == 'Hourglass'
    with db._get_connection() as conn:
        row = conn.execute('''SELECT title, track_number, musicbrainz_recording_id,
                                     recording_disambiguation, file_path
                              FROM tracks WHERE id = 1''').fetchone()
    assert tuple(row) == ('Hourglass', 2, 'hourglass-id',
                          'alternate studio recording', str(target_path))


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
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args: [
        {'id': 'feel-good-id', 'name': 'Feel Good Hit of the Summer',
         'artists': [{'name': 'Queens of the Stone Age'}],
         'track_number': 1, 'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr(module, 'get_album_for_source', lambda *_args: {
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
    assert FLAC(str(path))['album'][0] == 'Rated R'
    with db._get_connection() as conn:
        title = conn.execute('SELECT title FROM albums WHERE id = 10').fetchone()[0]
    assert title == 'Rated R'


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
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args: [
        {'id': 'hourglass-id', 'name': 'Hourglass', 'track_number': 2,
         'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr(module, 'get_album_for_source', lambda *_args: {
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
    target = root / '02 - Hourglass.flac'
    assert target.exists() and not path.exists()
    assert FLAC(str(target))['title'][0] == 'Hourglass'
    with db._get_connection() as conn:
        row = conn.execute('SELECT title, track_number, file_path FROM tracks WHERE id = 1').fetchone()
    assert tuple(row) == ('Hourglass', 2, str(target))
