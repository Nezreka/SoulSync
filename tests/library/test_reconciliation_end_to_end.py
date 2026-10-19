"""Exercise the opt-in scan, persisted cases, intent, and exact wishlist request.

The local test-data database contains no library rows, so the fixtures model
albums the user previously investigated with real files on disk and a real
SoulSync SQLite schema. Only remote AcoustID/tracklist responses are stubbed.
"""

from __future__ import annotations

import json
import struct

import pytest
from mutagen.flac import FLAC

from core.library.reconciliation_evidence import ReconciliationEvidence
from core.repair_jobs.base import JobContext
from core.repair_jobs.library_reconciliation import (
    LibraryReconciliationJob, _album_titles_compatible, _candidate_evidence,
    _candidate_rank, _candidate_shape, _candidate_shapes_equivalent,
    _display_album_title, _distinctive_position_conflict,
    _full_coverage_digital_alternative,
    _group_disc_folders, _group_logical_albums,
    _has_unexplained_identified_extras, _normalize_slot, _numbered_slot,
    _promote_album_context_candidate,
    _release_group_candidates, _same_recording_copies, _search_release_candidates,
    _single_file_album_context_conflict, _suggest_filename,
)
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


@pytest.fixture(autouse=True)
def defer_background_refresh_in_synchronous_scan_tests(monkeypatch):
    # These fixtures rescan explicitly with controlled catalog and fingerprint
    # responses. A daemon refresh would outlive the fixture and race its database.
    monkeypatch.setattr(RepairWorker, '_schedule_album_refresh',
                        lambda self, case_id, folder, **kwargs: True)


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
        match = self.matches[fingerprint]
        return {'status': 'ok', 'recordings': match if isinstance(match, list) else [match]}


def test_album_title_keeps_clean_local_name_when_source_repeats_its_qualifier():
    assert _album_titles_compatible(
        'Katy Kirby on Audiotree Live',
        'Katy Kirby on Audiotree Live (Audiotree Live)',
    )
    assert not _album_titles_compatible('Album', 'Album (Deluxe Edition)')
    assert not _album_titles_compatible('Olive', 'Olive (live)')


def test_album_title_keeps_clean_name_when_source_adds_catalog_qualifier():
    assert _album_titles_compatible('Rio', 'Rio (Columbia House)')
    assert _album_titles_compatible('Album', 'Album (EDC Blackburn Ltd.)')
    assert not _album_titles_compatible('Album', 'Album (Deluxe Edition)')
    assert _display_album_title(
        [{'tags': {'album': 'Rio'}}, {'tags': {'album': 'Rio'}}],
        'Rio (Columbia House)', 'Rio (Columbia House)',
    ) == 'Rio'


def test_album_title_policy_keeps_clean_display_but_can_follow_exact_release():
    files = [{'tags': {'album': 'Greatest Hits'}} for _ in range(3)]
    assert _display_album_title(files, 'Greatest Hits',
                                'Greatest Hits (Deluxe Edition)') == 'Greatest Hits'
    assert _display_album_title(files, 'Greatest Hits',
                                'Greatest Hits (Deluxe Edition)',
                                'exact_release') == 'Greatest Hits (Deluxe Edition)'


def test_preferred_source_breaks_equal_coverage_but_not_a_better_match():
    files = [{'path': '/a'}, {'path': '/b'}]
    slots = [{'title': 'A'}, {'title': 'B'}]
    plan = {'pairs': [{'file_index': 0, 'slot_index': 0},
                      {'file_index': 1, 'slot_index': 1}],
            'missing_slots': [], 'extra_files': []}
    itunes = (2, 2, 0, 'itunes', 'i', slots, plan)
    musicbrainz = (2, 2, 0, 'musicbrainz', 'm', slots, plan)
    assert _candidate_rank(files, musicbrainz, 'release', 'musicbrainz') > \
        _candidate_rank(files, itunes, 'release', 'musicbrainz')
    weaker = (1, 2, 0, 'musicbrainz', 'm', slots, plan)
    assert _candidate_rank(files, itunes, 'release', 'musicbrainz') > \
        _candidate_rank(files, weaker, 'release', 'musicbrainz')
    # A current provider title can carry real edition detail while the stored
    # library title still carries an obsolete catalog label. Unanimous file
    # tags remain the clean display title; the exact provider ID preserves the
    # selected release identity separately.
    assert not _album_titles_compatible(
        'Rio (Columbia House)', 'Rio (Remastered)')
    assert _display_album_title(
        [{'tags': {'album': 'Rio'}}, {'tags': {'album': 'Rio'}}],
        'Rio (Columbia House)', 'Rio (Remastered)',
    ) == 'Rio'


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


def test_candidate_evidence_keeps_release_context_for_human_comparison():
    files = [{
        'path': '/album/01.flac',
        'tags': {'title': 'Song', 'duration_ms': 100000},
        'acoustid': {'recordings': []},
    }]
    slots = [{'title': 'Song', 'track_number': 1, 'disc_number': 1}]
    plan = {'pairs': [], 'missing_slots': [0], 'extra_files': [0]}

    evidence = _candidate_evidence(
        files, (0, 0, -1, 'musicbrainz', 'release-id', slots, plan),
        {'name': 'Album', 'country': 'US', 'disambiguation': 'deluxe',
         'date': '2024-01-02', 'media': [{'format': 'CD'}, {'format': 'CD'}],
         'image_url': 'https://example.org/deluxe-cover.jpg'},
    )

    assert evidence['release_title'] == 'Album'
    assert evidence['country'] == 'US'
    assert evidence['disambiguation'] == 'deluxe'
    assert evidence['date'] == '2024-01-02'
    assert evidence['format'] == 'CD'
    assert evidence['image_url'] == 'https://example.org/deluxe-cover.jpg'


def test_candidate_shape_sorts_numbered_and_unnumbered_missing_slots():
    slots = [
        {'title': 'Numbered', 'disc_number': 1, 'track_number': 2},
        {'title': 'Unnumbered', 'disc_number': 1, 'track_number': None},
    ]
    plan = {'pairs': [], 'missing_slots': [1, 0], 'extra_files': []}

    shape = _candidate_shape((0, 0, 0, 'musicbrainz', 'release', slots, plan))

    assert shape[2] == (
        (1, False, 2, ('numbered', ()), ''),
        (1, True, 0, ('unnumbered', ()), ''),
    )


def test_release_shapes_treat_missing_provider_disambiguation_as_unknown():
    plan = {'pairs': [
        {'file_index': 0, 'slot_index': 0, 'confidence': 'clear'},
        {'file_index': 1, 'slot_index': 1, 'confidence': 'clear'},
    ], 'missing_slots': [], 'extra_files': []}
    musicbrainz_slots = [
        {'title': 'The Bronze', 'track_number': 1, 'disc_number': 1,
         'recording_disambiguation': 'remastered album version'},
        {'title': 'How to Handle a Rope', 'track_number': 2, 'disc_number': 1,
         'recording_disambiguation': 'album version'},
    ]
    provider_slots = [
        {'title': 'The Bronze (Bonus Track)', 'track_number': 1, 'disc_number': 1},
        {'title': 'How to Handle a Rope (A Lesson in the Lariat)',
         'track_number': 2, 'disc_number': 1},
    ]
    mb = (2, 2, 0, 'musicbrainz', 'mb', musicbrainz_slots, plan)
    provider = (2, 2, 0, 'itunes', 'itunes', provider_slots, plan)

    assert _candidate_shapes_equivalent(mb, provider)


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


def test_release_shapes_ignore_minor_provider_spelling_differences():
    plan = {'pairs': [{'file_index': 0, 'slot_index': 0, 'confidence': 'clear'}],
            'missing_slots': [], 'extra_files': []}
    itunes = (1, 1, 0, 'itunes', 'a', [{
        'title': 'Bathyalpelagic III: Disequilibrated (Instrumental)',
        'track_number': 3, 'disc_number': 2,
    }], plan)
    deezer = (1, 1, 0, 'deezer', 'b', [{
        'title': 'Bathyalpelagic III: Disequillibrated (Instrumental Version)',
        'track_number': 3, 'disc_number': 2,
    }], plan)

    assert _candidate_shapes_equivalent(itunes, deezer)


def test_musicbrainz_slot_keeps_recording_disambiguation():
    slot = _normalize_slot({
        'id': 'recording-id', 'name': 'Song',
        'disambiguation': 'Connect Sets acoustic',
    }, 'musicbrainz')

    assert slot['recording_disambiguation'] == 'Connect Sets acoustic'


def test_filename_keeps_version_text_confirmed_by_recording_disambiguation():
    slot = {'title': 'Home', 'track_number': 1, 'disc_number': 1,
            'recording_disambiguation': 'live'}

    assert _suggest_filename('/album/01 - Home (Live).flac', slot) == ''


def test_filename_does_not_keep_a_different_recording_version():
    slot = {'title': 'Home', 'track_number': 1, 'disc_number': 1,
            'recording_disambiguation': 'demo'}

    assert _suggest_filename('/album/01 - Home (Live).flac', slot) == \
        '/album/01 - Home.flac'


def test_strong_audio_conflict_uses_the_release_slot_as_well_as_local_title():
    from core.repair_jobs.library_reconciliation import _audio_conflicts_with_slot

    file = {'tags': {'title': 'Misnamed'}, 'acoustid': {'recordings': [
        {'title': 'Correct Song', 'artist': 'Artist', 'score': 0.99},
    ]}}
    assert not _audio_conflicts_with_slot(file, {'title': 'Correct Song', 'artist': 'Artist'})
    assert _audio_conflicts_with_slot(file, {'title': 'Other Song', 'artist': 'Artist'})


def test_album_title_compatibility_preserves_edition_detail_only_for_same_album():
    assert _album_titles_compatible(
        'New Miserable Experience (Deluxe Edition)', 'New Miserable Experience')
    assert _album_titles_compatible('The Courage of Others [Deluxe]',
                                    'The Courage of Others')
    assert _album_titles_compatible('Symbolic - Dutch Mediamotion pressing', 'Symbolic')
    assert _album_titles_compatible('Album - 24 bit/96 kHz', 'Album')
    assert not _album_titles_compatible('New Miserable Experience',
                                        'New Miserable Experience (Deluxe Edition)')
    assert not _album_titles_compatible('R', 'Rated R')


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


def test_manual_assignment_is_saved_only_for_current_release_and_file(tmp_path):
    path = tmp_path / '01 - Laid to Rest.flac'
    path.write_bytes(b'audio')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    worker = RepairWorker(database=db, transfer_folder=str(tmp_path))
    stat = path.stat()
    details = {
        'album_id': 10, 'source': 'spotify', 'release_id': 'ashes-release',
        'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
        'assignment_options': [{'key': 'id:alternate', 'title': 'Alternate'}],
        '_fix_action': 'assign:id:alternate',
    }
    result = worker._fix_reconciliation_assignment('track', '1', str(path), details)
    assert result['success'], result
    with db._get_connection() as conn:
        row = conn.execute('SELECT slot_key, release_id FROM reconcile_track_assignment').fetchone()
    assert tuple(row) == ('id:alternate', 'ashes-release')
    path.write_bytes(b'changed audio')
    assert not worker._fix_reconciliation_assignment('track', '1', str(path), details)['success']


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


def _metadata_recommendation_album(tmp_path, monkeypatch, *, mixed=False, recordings=None):
    import core.repair_jobs.library_reconciliation as module
    root = tmp_path / 'music'
    folder = root / 'Artist' / 'Album'
    folder.mkdir(parents=True)
    db = MusicDatabase(str(tmp_path / 'music.db'))
    paths = [folder / '01 - Laid to Rest.flac', folder / '02 - Hourglass.flac']
    _album(db, paths[0])
    titles = ['Laid to Rest', 'Hourglass']
    mbids = ['80951433-fbbf-45e8-b906-e820652b9ca5', 'e2bd3843-a9f1-447f-b733-f5f3d5dd3d33']
    with db._get_connection() as conn:
        conn.execute("UPDATE albums SET canonical_source='itunes', title='Ashes of the Wake (Deluxe)'")
        conn.execute("UPDATE artists SET aliases=?", (json.dumps(['ラム・オブ・ゴッド']),))
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path) VALUES (2,10,1,'Hourglass',2,?)", (str(paths[1]),))
        conn.commit()
    for index, path in enumerate(paths):
        _minimal_flac(path, {'title': titles[index], 'artist': 'Lamb of God',
            'albumartist': 'Lamb of God', 'tracknumber': str(index + 1), 'discnumber': '1',
            'album': 'Ashes of the Wake' if mixed and index == 0 else 'Ashes of the Wake (Deluxe)',
            'musicbrainz_trackid': 'feb76071-8125-4e98-8841-7b45a8d37feb',
            'musicbrainz_albumid': 'first-release' if mixed and index == 0 else 'second-release'})
    client = _Acoustid({path.name: (recordings if recordings is not None else
        {'mbid': mbids[index], 'title': titles[index], 'artist': 'Lamb of God', 'score': .99})
        for index, path in enumerate(paths)})
    worker = RepairWorker(database=db, transfer_folder=str(root))
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda _self: client))
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    read_tags = module._read_file_tags
    monkeypatch.setattr(module, '_read_file_tags', lambda path: {**read_tags(path), 'duration_ms': 200000})
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args, **_kwargs: [
        {'id': str(index + 1), 'name': title, 'artists': [{'name': 'Lamb of God'}],
         'track_number': index + 1, 'disc_number': 1, 'duration_ms': 200000}
        for index, title in enumerate(titles)])
    monkeypatch.setattr(module, 'get_album_for_source', lambda *_args, **_kwargs: {
        'name': 'Ashes of the Wake (Deluxe)', 'artists': [{'name': 'Lamb of God'}]})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)
    return db, worker, context, paths, mbids


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


def test_mixed_album_metadata_requires_review_and_can_clear_only_release_ids(tmp_path, monkeypatch):
    from core.repair_review_policy import _finding_tier
    db, worker, context, paths, _ = _metadata_recommendation_album(tmp_path, monkeypatch, mixed=True)
    result = LibraryReconciliationJob().scan(context)
    assert not result.errors
    case_id = worker.get_review_cases()['items'][0]['case_id']
    items = worker.get_review_case(case_id)['items']
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
    assert 'indexed' in result['error'] if changed.startswith('indexed') else 'changed' in result['error']
    assert paths[1].read_bytes() == before


@pytest.mark.parametrize('alias_in_catalogue', [False, True])
def test_stored_artist_aliases_support_cross_script_audio_identity(tmp_path, monkeypatch, alias_in_catalogue):
    import core.repair_jobs.library_reconciliation as module
    db, worker, context, paths, mbids = _metadata_recommendation_album(tmp_path, monkeypatch)
    if alias_in_catalogue:
        get_tracks = module.get_album_tracks_for_source
        monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *a, **k: [
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


def test_provider_independent_recording_identity_requires_unique_corroboration():
    from core.library.reconciliation_metadata import confirmed_recording
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


def test_recording_id_removal_preserves_composer_credits_and_catalogue_aliases():
    from types import SimpleNamespace
    from core.library.reconciliation_metadata import recording_id_conflict
    mbid = '61a45c1a-a717-4c58-abba-0563000f8a1d'
    file = {'tags': {'mbid': mbid}, 'acoustid': {'status': 'no_match'}}
    slot = {'title': 'Gnossienne: No. 1', 'artist': 'Alexandre Tharaud'}
    record = {'id': mbid, 'title': 'Gnossienne No. 1',
              'artist-credit': [{'name': 'Erik Satie'}]}
    client = SimpleNamespace(get_recording=lambda *a, **k: record)
    assert not recording_id_conflict(file, slot, client, {})
    record.update(title='別の表記', aliases=[{'name': 'Gnossienne No. 1'}])
    assert not recording_id_conflict(file, slot, client, {})
    record.update(title='Winter Dies', aliases=[])
    assert recording_id_conflict(file, slot, client, {})


def test_ambiguous_replacement_ids_offer_reviewed_removal_after_catalogue_check(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from core.repair_review_policy import _finding_tier
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


@pytest.mark.parametrize('previous_annotation_scan', [False, True])
def test_explanatory_annotations_keep_saved_choice_but_new_conflict_reopens_it(tmp_path, monkeypatch, previous_annotation_scan):
    db, worker, context, paths, _ = _metadata_recommendation_album(tmp_path, monkeypatch)
    assert not LibraryReconciliationJob().scan(context).errors
    case_id = worker.get_review_cases()['items'][0]['case_id']
    item = worker.get_review_case(case_id)['items'][0]
    result = worker.apply_review_decision(case_id, item['id'], evidence_token=item['evidence_token'], action='fix')
    assert result['success'], result
    details = {**item['details'], 'assignment_evidence': 'audio', 'tool_checks': [{'title': 'Additional context'}]}
    details['match_reasons'] = ['AcoustID recording title matches release slot']
    if previous_annotation_scan:
        with db._get_connection() as conn:
            conn.execute('UPDATE repair_findings SET details_json=? WHERE id=?',
                         (json.dumps(details), item['id']))
            conn.commit()
    def refresh():
        worker._create_finding(job_id='library_reconciliation', finding_type=item['finding_type'],
            severity='warning', entity_type=item['entity_type'], entity_id=item['entity_id'],
            file_path=item['file_path'], title=item['title'], description=item['description'], details=details)
        return next(row for row in worker.get_review_case(case_id)['items'] if row['id'] == item['id'])
    assert refresh()['details']['plan_approved']
    details['metadata_review_reason'] = 'New conflicting recording evidence needs review'
    changed = refresh()
    assert not changed['details'].get('plan_approved')
    assert changed['review_tier'] == 'manual'


def test_configured_lossy_companion_shares_assignment_without_duplicate_decision(tmp_path, monkeypatch):
    from types import SimpleNamespace
    db, worker, context, paths, _ = _metadata_recommendation_album(tmp_path, monkeypatch)
    companion = paths[0].with_suffix('.mp3')
    companion.write_bytes(paths[0].read_bytes())
    context.acoustid_client.matches[companion.name] = context.acoustid_client.matches[paths[0].name]
    context.config_manager = SimpleNamespace(get=lambda key, default=None:
        True if key == 'lossy_copy.enabled' else default)
    with db._get_connection() as conn:
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path) VALUES (3,10,1,'Laid to Rest',1,?)", (str(companion),))
        conn.commit()
    scan = LibraryReconciliationJob()
    for _ in range(2):  # The protected role is also part of the matching cache key.
        result = scan.scan(context)
        assert not result.errors
        case_id = worker.get_review_cases()['items'][0]['case_id']
        case = worker.get_review_case(case_id)
        assert {item['finding_type'] for item in case['items']} == {'reconciliation_metadata'}
        plan = case['album_plan']
        assert plan['matched'] == plan['release_track_count'] == 2
        assert len(plan['assignments']) == 3
        row = next(row for row in plan['assignments'] if row['file_path'] == str(companion))
        assert row['companion_of'] == str(paths[0])


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
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args, **_kwargs: [
        {'id': 'laid-id', 'name': 'Laid to Rest',
         'artists': [{'name': 'Lamb of God'}],
         'track_number': 1, 'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr(module, 'get_album_for_source', lambda *_args, **_kwargs: {
        'name': 'Ashes of the Wake', 'artists': [{'name': 'Lamb of God'}]})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)

    result = LibraryReconciliationJob().scan(context)

    assert result.findings_created == 0
    assert worker.get_review_cases(job_id='library_reconciliation')['total'] == 0


def test_lone_playlist_track_requires_explicit_album_intent(tmp_path, monkeypatch):
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

    assert result.findings_created == 1
    assert worker.get_review_cases(job_id='library_reconciliation')['total'] == 1
    with db._get_connection() as conn:
        saved = conn.execute(
            'SELECT intent_scope, state, confirmed_count, missing_count, plan_json '
            'FROM reconcile_album_plans'
        ).fetchone()
    assert saved['intent_scope'] == ''
    assert saved['state'] == 'review'
    assert saved['confirmed_count'] == 1
    assert saved['missing_count'] == 1
    document = json.loads(saved['plan_json'])
    assert document['counts']['missing'] == 1
    assert document['assignments'][0]['playlists'] == ['Imported playlist']
    assert {row['status'] for row in document['assignments']} == {
        'confirmed', 'missing'}


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


def test_isolated_api_errors_are_scoped_to_files_until_circuit_opens(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module
    monkeypatch.setattr('core.library.reconciliation_evidence.time.sleep', lambda _delay: None)

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
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args: [
        {'id': 'failed-id', 'name': 'Failed', 'track_number': 1,
         'disc_number': 1, 'duration_ms': 200000},
        {'id': 'good-id', 'name': 'Good', 'track_number': 2,
         'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr(module, 'get_album_for_source', lambda *_args: {
        'name': 'Ashes of the Wake'})
    findings = []
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client,
                         create_finding=lambda **finding: findings.append(finding) or True)

    result = LibraryReconciliationJob().scan(context)

    assert result.scanned == 2
    assert any(finding['title'] == 'Confirm: Failed' for finding in findings)


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


def test_deluxe_addendum_joins_disjoint_regular_album_for_review():
    regular = '/music/Split Chain/[2025] motionblur'
    deluxe = '/music/Split Chain/[2026] motionblur (DELUXE)'
    regular_paths = [f'{regular}/{number:02}.flac' for number in range(1, 12)]
    deluxe_numbers = (12, 13, 14, 25, 26)
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


def test_digital_numbering_rank_prefers_continuous_track_numbers():
    files = [
        {'tags': {'track_number': number, 'disc_number': 1}}
        for number in range(1, 5)
    ]
    pairs = [{'file_index': index, 'slot_index': index, 'confidence': 'clear'}
             for index in range(4)]
    plan = {'pairs': pairs, 'extra_files': [], 'missing_slots': []}
    continuous_slots = [
        {'track_number': number, 'disc_number': 1} for number in range(1, 5)
    ]
    cd_slots = [
        {'track_number': 1, 'disc_number': 1},
        {'track_number': 2, 'disc_number': 1},
        {'track_number': 1, 'disc_number': 2},
        {'track_number': 2, 'disc_number': 2},
    ]
    continuous = (4, 4, 0, 'musicbrainz', 'digital', continuous_slots, plan)
    compact_disc = (4, 4, 0, 'musicbrainz', 'cd', cd_slots, plan)

    assert _candidate_rank(files, continuous, 'digital') > \
        _candidate_rank(files, compact_disc, 'digital')
    assert _candidate_rank(files, continuous, 'release') == \
        _candidate_rank(files, compact_disc, 'release')


def test_complete_tracklist_breaks_tie_with_deluxe_release():
    files = [{'tags': {'track_number': number, 'disc_number': 1}}
             for number in range(1, 12)]
    pairs = [{'file_index': index, 'slot_index': index, 'confidence': 'clear'}
             for index in range(11)]
    complete_plan = {'pairs': pairs, 'extra_files': [], 'missing_slots': []}
    deluxe_plan = {'pairs': pairs, 'extra_files': [], 'missing_slots': [11, 12, 13]}
    complete = (11, 11, 0, 'musicbrainz', 'standard',
                [{'track_number': number, 'disc_number': 1}
                 for number in range(1, 12)], complete_plan)
    deluxe = (11, 11, 0, 'itunes', 'deluxe',
              [{'track_number': number, 'disc_number': 1}
               for number in range(1, 15)], deluxe_plan)

    assert _candidate_rank(files, complete, 'digital') > \
        _candidate_rank(files, deluxe, 'digital')


def test_digital_numbering_flattens_physical_release_without_changing_identity():
    slot = {'title': 'Ab Ovo', 'disc_number': 2, 'track_number': 12,
            'recording_mbid': 'recording-id'}

    slots = [{'disc_number': 1}, slot]
    numbered = _numbered_slot(slot, 12, 'digital', slots)

    assert numbered == {**slot, 'disc_number': 1, 'track_number': 13}
    assert _numbered_slot(slot, 12, 'release', slots) is slot
    assert _numbered_slot(slot, 12, 'digital', [slot]) is slot


def test_title_searched_compilation_beats_equally_fitting_original_album():
    plan = {'pairs': [{'file_index': 0, 'slot_index': 0, 'confidence': 'clear'}],
            'extra_files': [], 'missing_slots': []}
    compilation = (1, 1, 0, 'musicbrainz', 'soundtracks-box',
                   [{'track_number': 1, 'disc_number': 2}], plan)
    original_album = (1, 1, 0, 'musicbrainz', 'truman-show',
                      [{'track_number': 17, 'disc_number': 1}], plan)
    scored = [original_album, compilation]

    _promote_album_context_candidate(scored, [('musicbrainz', 'soundtracks-box')])

    assert scored == [compilation, original_album]


def test_album_context_does_not_override_better_audio_assignment():
    plan = {'pairs': [], 'extra_files': [], 'missing_slots': []}
    linked = (1, 1, 0, 'musicbrainz', 'linked', [], plan)
    better = (2, 2, 0, 'musicbrainz', 'better', [], plan)
    scored = [better, linked]

    _promote_album_context_candidate(scored, [('musicbrainz', 'linked')])

    assert scored == [better, linked]


def test_lone_compilation_track_does_not_become_original_soundtrack_album():
    album = {'album_title': 'Philip Glass Soundtracks Vol. II'}

    assert _single_file_album_context_conflict(album, {
        'name': 'The Truman Show: Music From the Motion Picture',
    })


def test_lone_track_accepts_compatible_album_title_flavor():
    album = {'album_title': 'The Truman Show (Original Motion Picture Soundtrack)'}

    assert not _single_file_album_context_conflict(album, {
        'name': 'The Truman Show: Music From the Motion Picture',
    })


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


def test_release_title_search_is_bounded_and_rejects_fuzzy_title_hits():
    calls = []

    class MusicBrainz:
        def search_release(self, title, artist_name, limit):
            calls.append((title, artist_name, limit))
            return [
                {'id': 'exact', 'title': 'Unknown Album', 'score': 100},
                {'id': 'fuzzy', 'title': 'Unknown Album Live', 'score': 99},
                {'id': 'weak', 'title': 'Unknown Album', 'score': 89},
            ]

    context = JobContext(db=None, transfer_folder='', config_manager=None,
                         mb_client=MusicBrainz())
    album = {'album_title': 'Unknown Album', 'artist_name': 'Known Artist'}
    cache = {}

    assert _search_release_candidates(album, context, cache) == [
        ('musicbrainz', 'exact')]
    assert _search_release_candidates(album, context, cache) == [
        ('musicbrainz', 'exact')]
    assert calls == [('Unknown Album', 'Known Artist', 4)]


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


def test_complete_digital_edition_stays_reviewable_when_one_track_is_ambiguous():
    complete = {'extra_files': [], 'missing_slots': []}
    physical = (14, 14, 0, 'musicbrainz', 'cd', [], complete)
    digital = (13, 14, 0, 'musicbrainz', 'digital', [], complete)
    metadata = lambda row: {'format': 'Digital Media' if row[4] == 'digital' else 'CD'}

    assert _full_coverage_digital_alternative(
        physical, [physical, digital], 'digital', 14, metadata)
    assert not _full_coverage_digital_alternative(
        physical, [physical, digital], 'release', 14, metadata)
    assert not _full_coverage_digital_alternative(
        physical, [physical, (*digital[:6], {**complete, 'missing_slots': [14]})],
        'digital', 14, metadata)


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
    monkeypatch.setattr('core.repair_worker._server_playlist_membership', lambda: {})
    extra = next(finding for finding in worker.get_review_case(case_id)['items']
                 if finding['finding_type'] == 'reconciliation_extra')
    result = worker.fix_finding(extra['id'], review_approved=True)
    assert result['success'], result
    assert not path.exists()
    assert (root / '.deleted' / path.name).exists()
    with db._get_connection() as conn:
        assert conn.execute('SELECT id FROM tracks WHERE id = 1').fetchone() is None


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
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args: [
        {'id': 'wanted-id', 'name': 'Laid to Rest', 'track_number': 1,
         'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr(module, 'get_album_for_source',
                        lambda *_args: {'name': 'Ashes of the Wake'})
    client = _Acoustid({path.name: [
        {'mbid': 'wrong-one', 'title': 'Southbound',
         'artist': 'Thin Lizzy', 'score': 0.99},
        {'mbid': 'wrong-two', 'title': 'Jailbreak',
         'artist': 'Thin Lizzy', 'score': 0.99},
    ]})
    worker = RepairWorker(database=db, transfer_folder=str(root))
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda _self: client))
    monkeypatch.setattr('core.repair_worker._server_playlist_membership', lambda: {})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)

    LibraryReconciliationJob().scan(context)

    case_id = worker.get_review_cases()['items'][0]['case_id']
    extra = worker.get_review_case(case_id)['items'][0]
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
    assert result['success'], result
    assert not path.exists()
    assert (root / '.deleted' / path.name).exists()
    with db._get_connection() as conn:
        assert conn.execute('SELECT id FROM tracks WHERE id = 1').fetchone() is None
        row = conn.execute(
            'SELECT spotify_track_id, source_info FROM wishlist_tracks').fetchone()
    assert row['spotify_track_id'] == 'wanted-id'
    source_info = json.loads(row['source_info'])
    assert source_info['source_album_id'] == 'ashes-release'
    assert source_info['reason'] == 'library_reconciliation_wrong_audio'


def test_version_request_keeps_local_file_and_playlist_membership(tmp_path, monkeypatch):
    path = tmp_path / '17 - Jamie (live acoustic).flac'
    path.write_bytes(b'local version')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    worker = RepairWorker(database=db, transfer_folder=str(tmp_path))
    monkeypatch.setattr('core.repair_worker._server_playlist_membership',
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
    assert first.exists() and second.exists() and third.exists()
    outcome = worker.fix_finding(findings[0]['id'], review_approved=True)
    assert outcome['success'], outcome
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
    assert FLAC(str(path))['album'][0] == 'R'
    assert worker.fix_finding(finding['id'], review_approved=True)['success']
    assert FLAC(str(path))['album'][0] == 'Rated R'
    with db._get_connection() as conn:
        title = conn.execute('SELECT title FROM albums WHERE id = 10').fetchone()[0]
    assert title == 'Rated R'


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
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args: [
        {'id': 'rio-id', 'name': 'Rio', 'artists': [{'name': 'Duran Duran'}],
         'track_number': 1, 'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr(module, 'get_album_for_source', lambda *_args: {
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


def test_lone_compilation_track_does_not_spawn_original_album_plan(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module

    class _MB:
        def get_recording(self, _mbid, includes=None):
            return {'releases': [{'id': 'truman-release'}]}

        def search_release(self, *_args, **_kwargs):
            return []

    root = tmp_path / 'music'
    root.mkdir()
    path = root / '01 - The Truman Show - Raising the Sail.flac'
    path.write_bytes(b'audio fixture')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    with db._get_connection() as conn:
        conn.execute("UPDATE artists SET name = 'Philip Glass' WHERE id = 1")
        conn.execute("UPDATE albums SET title = 'Philip Glass Soundtracks Vol. II', "
                     "canonical_source = NULL, canonical_album_id = NULL WHERE id = 10")
        conn.execute("UPDATE tracks SET title = 'The Truman Show - Raising the Sail' "
                     "WHERE id = 1")
        conn.commit()
    worker = RepairWorker(database=db)
    client = _Acoustid({path.name: {
        'mbid': 'raising-id', 'title': 'Raising the Sail',
        'artist': 'Philip Glass', 'score': 0.99,
    }})
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr(module, '_read_file_tags', lambda _path: {
        'title': 'The Truman Show - Raising the Sail', 'artist': 'Philip Glass',
        'album': 'Philip Glass Soundtracks Vol. II', 'track_number': 1,
        'disc_number': 1, 'duration_ms': 200000,
    })
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args, **_kwargs: [{
        'id': 'raising-id', 'name': 'Raising the Sail',
        'artists': [{'name': 'Philip Glass'}], 'track_number': 17,
        'disc_number': 1, 'duration_ms': 200000,
    }])
    monkeypatch.setattr(module, 'get_album_for_source', lambda *_args, **_kwargs: {
        'name': 'The Truman Show: Music From the Motion Picture',
        'artists': [{'name': 'Philip Glass'}],
    })
    context = JobContext(
        db=db, transfer_folder=str(root), config_manager=None,
        acoustid_client=client, mb_client=_MB(),
        create_finding=worker._create_finding,
    )

    result = LibraryReconciliationJob().scan(context)
    queue = worker.get_review_cases(job_id='library_reconciliation')
    finding = worker.get_review_case(
        queue['items'][0]['case_id'], job_id='library_reconciliation')['items'][0]

    assert result.findings_created == 1
    assert finding['finding_type'] == 'reconciliation_unresolved'
    assert finding['title'] == 'Confirm album: Philip Glass Soundtracks Vol. II'
    assert 'changes' not in finding['details']


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
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args: [
        {'id': 'laid-id', 'name': 'Laid to Rest',
         'artists': [{'name': 'Lamb of God'}],
         'track_number': 1, 'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr(module, 'get_album_for_source', lambda *_args: {
        'name': 'Ashes of the Wake',
        'artists': [{'name': 'Lamb of God'}, {'name': 'Guest Artist'}],
        'disambiguation': 'US deluxe edition',
    })
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)

    result = LibraryReconciliationJob().scan(context)

    assert result.findings_created == 0
    assert worker.get_review_cases(job_id='library_reconciliation')['total'] == 0


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
    assert path.exists()
    assert worker.fix_finding(finding['id'], review_approved=True)['success']
    target = root / '02 - Hourglass.flac'
    assert target.exists() and not path.exists()
    assert FLAC(str(target))['title'][0] == 'Hourglass'
    with db._get_connection() as conn:
        row = conn.execute('SELECT title, track_number, file_path FROM tracks WHERE id = 1').fetchone()
    assert tuple(row) == ('Hourglass', 2, str(target))


def test_video_slots_are_excluded_without_discarding_bluray_audio(monkeypatch):
    from core.repair_jobs.library_reconciliation import _score_release_candidates, candidate_preview
    items = [
        {'id': 'video', 'name': 'Concert video', 'video': True, 'disc_number': 1},
        {'id': 'audio', 'name': 'Song', 'video': False, 'disc_number': 2,
         'track_number': 1, 'duration_ms': 200000, 'medium_format': 'Blu-ray'},
        {'id': 'unknown', 'name': 'Bonus', 'disc_number': 2, 'track_number': 2},
    ]
    monkeypatch.setattr('core.repair_jobs.library_reconciliation.get_album_tracks_for_source',
                        lambda *args, **kwargs: {'items': items})
    row = _score_release_candidates([], [('musicbrainz', 'release')], {})[0]
    assert [slot['source_track_id'] for slot in row.slots] == ['audio', 'unknown']
    assert row.plan['missing_slots'] == [0, 1]
    assert candidate_preview([], row, {})['excluded_video_tracks'] == 1
    assert _numbered_slot(row.slots[0], 0, 'digital', row.slots)['disc_number'] == 1
    assert _numbered_slot(row.slots[0], 0, 'release', row.slots)['disc_number'] == 2


def test_unassigned_download_grouping_requires_one_exact_album_owner():
    from core.repair_jobs.library_reconciliation import _group_unassigned_downloads
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


def test_candidate_preview_keeps_missing_slot_positions():
    from core.repair_jobs.library_reconciliation import candidate_preview
    slots = [{'title': 'Mrs. Rita', 'disc_number': 2, 'track_number': 13}]
    preview = candidate_preview([], (0, 0, 0, 'musicbrainz', 'release', slots,
        {'pairs': [], 'missing_slots': [0], 'extra_files': []}), {})
    assert preview['missing_slots'][0] == {
        'slot_title': 'Mrs. Rita', 'disc_number': 2, 'track_number': 13,
        'display_track_number': 13, 'missing': True}


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


def test_release_identity_repairs_mixed_tags_without_rewriting_coherent_pressings():
    from core.repair_jobs.library_reconciliation import _release_identity_target
    file = lambda release: {'tags': {'musicbrainz_release_id': release}}
    assert _release_identity_target([file('old'), file('old')], {}, 'musicbrainz', 'new') == ''
    assert _release_identity_target([file('old'), file('other')], {}, 'musicbrainz', 'new') == 'new'
    assert _release_identity_target([file('old'), file('')], {}, 'musicbrainz', 'new') == 'new'
    assert _release_identity_target([file(''), file('')], {}, 'musicbrainz', 'new') == ''
    assert _release_identity_target([file('old')], {'canonical_locked': True}, 'musicbrainz', 'new') == 'new'
    assert _release_identity_target([file('old'), file('other')], {}, 'deezer', '123') == ''


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


def test_kept_numbering_survives_other_metadata_apply_and_can_be_reopened(tmp_path, monkeypatch):
    import core.repair_jobs.library_reconciliation as module
    from mutagen.flac import FLAC

    root = tmp_path / 'music'
    root.mkdir()
    path = root / '09 - Laid to Rest.flac'
    _minimal_flac(path, {'title': 'Laid to Rest', 'artist': 'Lamb of God',
                         'album': 'Ashes of the Wake', 'tracknumber': '9',
                         'musicbrainz_albumid': 'old-pressing'})
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _album(db, path)
    db.set_album_canonical(10, 'musicbrainz', 'ashes-mb-release', 0.9, locked=True)
    worker = RepairWorker(database=db)
    client = _Acoustid({path.name: {'mbid': 'laid-id', 'title': 'Laid to Rest',
                                   'artist': 'Lamb of God', 'score': 0.99}})
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda _self: client))
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *_args: [
        {'id': 'laid-id', 'name': 'Laid to Rest', 'artists': [{'name': 'Lamb of God'}],
         'track_number': 1, 'disc_number': 1, 'duration_ms': 200000}])
    monkeypatch.setattr(module, 'get_album_for_source', lambda *_args: {'name': 'Ashes of the Wake'})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)
    LibraryReconciliationJob().scan(context)
    key = worker.get_review_cases()['items'][0]['case_id']
    finding = worker.get_review_case(key)['items'][0]
    assert finding['category'] == 'numbering'
    assert worker.apply_review_decision_group(key, 'numbering', action='dismiss')['success']
    assert worker.fix_finding(finding['id'], review_approved=True)['success']
    assert FLAC(str(path))['tracknumber'][0] == '9'
    assert FLAC(str(path))['musicbrainz_albumid'][0] == 'ashes-mb-release'
    LibraryReconciliationJob().scan(context)
    case = worker.get_review_case(key)
    assert not any('track_number' in item['details'].get('changes', {}) for item in case['items'])
    assert worker.reopen_review_assignment(key, finding['id'])['success']
    LibraryReconciliationJob().scan(context)
    case = worker.get_review_case(key)
    assert any('track_number' in item['details'].get('changes', {}) for item in case['items'])


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
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *a, **kw: [
        {'id': 'laid', 'name': 'Laid to Rest', 'artists': [{'name': 'Lamb of God'}], 'track_number': 1, 'duration_ms': 200000},
        {'id': 'missing', 'name': 'Hourglass', 'artists': [{'name': 'Lamb of God'}], 'track_number': 2, 'duration_ms': 240000}])
    monkeypatch.setattr(module, 'get_album_for_source', lambda *a, **kw: {'name': 'Ashes of the Wake'})
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


def test_failed_catalogue_reanalysis_preserves_plan_and_findings(tmp_path, monkeypatch):
    import requests
    import core.repair_jobs.library_reconciliation as module
    from core.library.reconciliation_catalogue import current

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
    monkeypatch.setattr(module, 'get_album_tracks_for_source', lambda *args, **kw: [
        {'id': 'laid', 'name': 'Laid to Rest', 'track_number': 1,
         'duration_ms': 200000, 'artists': [{'name': 'Lamb of God'}]}])
    monkeypatch.setattr(module, 'get_album_for_source', lambda *args, **kw: {'name': 'Ashes of the Wake'})
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

    monkeypatch.setattr(module, 'get_album_for_source', failed)
    result = LibraryReconciliationJob().scan(context, refresh_catalogue=True)
    assert result.stopped_early and result.metrics['catalogue_incomplete']
    assert state() == before
    assert result.findings_created == 0


def test_release_catalog_details_preserves_label_catalog_associations():
    from core.repair_jobs.library_reconciliation import release_catalog_details
    result = release_catalog_details({'label-info': [
        {'label': {'name': 'First label'}, 'catalog-number': 'A-1'},
        {'label': {'name': 'Second label'}, 'catalog-number': 'B-2'},
    ]})
    assert result['label_catalogs'] == [
        {'label': 'First label', 'catalog_number': 'A-1'},
        {'label': 'Second label', 'catalog_number': 'B-2'},
    ]
