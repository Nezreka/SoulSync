"""matching / release policy regression contracts."""
from __future__ import annotations

import pytest

from core.reconciliation import search as search
from core.reconciliation.candidates import ScoredCandidate
from core.reconciliation.discovery import (
    search_release_candidates as _search_release_candidates,
)
from core.reconciliation.display import (
    _album_titles_compatible,
    _display_album_title,
)
from core.reconciliation.plan import reconcile_folder
from core.reconciliation.projection import _candidate_evidence
from core.reconciliation.selection import (
    _candidate_rank,
    _candidate_shape,
    _candidate_shapes_equivalent,
    _full_coverage_digital_alternative,
    _normalize_slot,
    _promote_album_context_candidate,
    _single_file_album_context_conflict,
    _supported_current_edition,
)
from core.repair_jobs.base import JobContext
from core.repair_jobs.library_reconciliation import LibraryReconciliationJob
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase
from tests.reconciliation.support.audio import (
    _Acoustid,
    version_pair,
)
from tests.reconciliation.support.scenarios import (
    _album,
    _metadata_recommendation_album,
)


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_preferred_source_breaks_equal_coverage_but_not_a_better_match():
    files = [{'path': '/a'}, {'path': '/b'}]
    slots = [{'title': 'A'}, {'title': 'B'}]
    plan = {'pairs': [{'file_index': 0, 'slot_index': 0},
                      {'file_index': 1, 'slot_index': 1}],
            'missing_slots': [], 'extra_files': []}
    itunes = (2, 2, 0, 'itunes', 'i', slots, plan)
    musicbrainz = (2, 2, 0, 'musicbrainz', 'm', slots, plan)
    assert _candidate_rank(files, musicbrainz, 'musicbrainz') > \
        _candidate_rank(files, itunes, 'musicbrainz')
    weaker = (1, 2, 0, 'musicbrainz', 'm', slots, plan)
    assert _candidate_rank(files, itunes, 'musicbrainz') > \
        _candidate_rank(files, weaker, 'musicbrainz')
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


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
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


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
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


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
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


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
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


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize(('locked', 'both_digital'), [(False, False), (True, False), (False, True)])
def test_digital_tie_break_resolves_medium_split_without_overriding_pin(tmp_path, monkeypatch, locked, both_digital):
    db, worker, context, paths, mbids = _metadata_recommendation_album(tmp_path, monkeypatch)
    db.set_album_canonical(10, 'musicbrainz', 'physical', .99, locked=locked)
    monkeypatch.setattr(LibraryReconciliationJob, '_release_candidates',
                        lambda *_args, **_kwargs: [('musicbrainz', 'physical'), ('musicbrainz', 'digital')])
    monkeypatch.setattr('core.reconciliation.selection._release_group_candidates', lambda *_args, **_kwargs: [])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda _source, release_id, **_kwargs: {
        'name': 'Ashes of the Wake (Deluxe)', 'artists': [{'name': 'Lamb of God'}],
        'format': 'Digital Media' if release_id == 'digital' or both_digital else 'CD'})
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda _source, release_id, **_kwargs: [
        {'id': mbids[index], 'name': title, 'artists': [{'name': 'Lamb of God'}],
         'track_number': index + 1 if release_id == 'digital' else 1,
         'disc_number': 1 if release_id == 'digital' else index + 1, 'duration_ms': 200000}
        for index, title in enumerate(['Laid to Rest', 'Hourglass'])])
    assert not LibraryReconciliationJob().scan(context).errors
    with db._get_connection() as conn:
        assert conn.execute('SELECT release_id FROM reconcile_album_plans').fetchone()[0] == (
            'physical' if locked or both_digital else 'digital')
        conflict = conn.execute("SELECT 1 FROM repair_findings WHERE finding_type='reconciliation_release'").fetchone()
        assert bool(conflict) == (both_digital and not locked)


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize(('album_format', 'locked'), [('CD', False), ('Digital Media', False), ('CD', True)])
def test_lone_track_keeps_album_context_ahead_of_format_and_country(tmp_path, monkeypatch, album_format, locked):
    db, worker, context, paths, mbids = _metadata_recommendation_album(tmp_path, monkeypatch)
    paths[1].unlink()
    with db._get_connection() as conn:
        conn.execute('DELETE FROM tracks WHERE id=2')
        conn.commit()
    db.set_album_canonical(10, 'musicbrainz', 'compilation' if locked else 'album', .99, locked=locked)
    monkeypatch.setattr(LibraryReconciliationJob, '_release_candidates',
                        lambda *_args, **_kwargs: [('musicbrainz', 'compilation'), ('musicbrainz', 'album')])
    monkeypatch.setattr('core.reconciliation.selection._release_group_candidates', lambda *_args, **_kwargs: [])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda _source, release_id, **_kwargs: {
        'name': 'Ashes of the Wake (Deluxe)' if release_id == 'album' else 'Metal Compilation',
        'format': album_format if release_id == 'album' else 'Digital Media',
        'country': 'US' if release_id == 'album' else 'XW'})
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda _source, release_id, **_kwargs: [
        {'id': mbids[0], 'name': 'Laid to Rest', 'artists': [{'name': 'Lamb of God'}],
         'track_number': 1, 'disc_number': 1, 'duration_ms': 200000},
        {'id': f'{release_id}-absent', 'name': 'Hourglass' if release_id == 'album' else 'Other Song',
         'artists': [{'name': 'Lamb of God'}], 'track_number': 2, 'disc_number': 1, 'duration_ms': 200000}])
    assert not LibraryReconciliationJob().scan(context).errors
    with db._get_connection() as conn:
        assert conn.execute('SELECT release_id FROM reconcile_album_plans').fetchone()[0] == (
            'compilation' if locked else 'album')
        # Album context orders the recommendation; it does not prove an edition.
        conflict = conn.execute("SELECT 1 FROM repair_findings WHERE finding_type='reconciliation_release'").fetchone()
        assert bool(conflict) is not locked


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize(('digital', 'locked', 'expected_source'), [
    (False, False, 'spotify'), (True, False, 'musicbrainz'), (True, True, 'spotify'),
])
def test_stored_release_and_digital_tie_break_respect_explicit_pin(
        tmp_path, monkeypatch, digital, locked, expected_source):
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
        conn.execute('UPDATE albums SET canonical_locked=? WHERE id=10', (int(locked),))
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
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *_args, **_kwargs: [
        {'id': 'laid-id', 'name': 'Laid to Rest',
         'artists': [{'name': 'Lamb of God'}],
         'track_number': 1, 'disc_number': 1, 'duration_ms': 200000},
    ])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda source, *_args, **_kwargs: {
        'name': 'Ashes of the Wake', 'artists': [{'name': 'Lamb of God'}],
        'format': 'Digital Media' if digital and source == 'musicbrainz' else 'Cassette'})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)

    result = LibraryReconciliationJob().scan(context)

    cases = worker.get_review_cases(job_id='library_reconciliation')
    if expected_source == 'musicbrainz':
        # The digital tie winner also supplies the missing recording ID.
        assert result.findings_created == 1
        assert cases['items'][0]['plan']['can_apply_ready']
    else:
        assert result.findings_created == 0
        assert cases['total'] == 0
    with db._get_connection() as conn:
        assert conn.execute('SELECT source FROM reconcile_album_plans').fetchone()[0] == expected_source


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
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

    assert _candidate_rank(files, complete) > _candidate_rank(files, deluxe)


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
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


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_lone_compilation_track_does_not_become_original_soundtrack_album():
    album = {'album_title': 'Philip Glass Soundtracks Vol. II'}

    assert _single_file_album_context_conflict(album, {
        'name': 'The Truman Show: Music From the Motion Picture',
    })


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_release_slot_numbers_are_normalized_before_db_comparison():
    slot = _normalize_slot({'id': 'hourglass-id', 'name': 'Hourglass',
                            'track_number': '02', 'disc_number': '1',
                            'duration_ms': '200000'}, 'spotify')
    assert (slot['track_number'], slot['disc_number'], slot['duration_ms']) == (2, 1, 200000)


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
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


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
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


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_complete_digital_edition_stays_reviewable_when_one_track_is_ambiguous():
    complete = {'extra_files': [], 'missing_slots': []}
    physical = (14, 14, 0, 'musicbrainz', 'cd', [], complete)
    digital = (13, 14, 0, 'musicbrainz', 'digital', [], complete)
    metadata = lambda row: {'format': 'Digital Media' if row[4] == 'digital' else 'CD'}

    assert _full_coverage_digital_alternative(
        physical, [physical, digital], 14, metadata)
    assert not _full_coverage_digital_alternative(
        physical, [physical, (*digital[:6], {**complete, 'missing_slots': [14]})],
        14, metadata)


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
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
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda _source, release: [
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
    candidates = finding['details']['candidates']
    assert candidates[0]['release_id'] == 'ashes-release'
    better = next(row for row in candidates if row['release_id'] == 'better-edition')
    assert better['difference_summary']
    assert better['pairs'][0]['file'] == path.name
    assert better['pairs'][0]['audio_title'] == 'Hourglass'
    assert better['pairs'][0]['slot_title'] == 'Hourglass'
    assert better['pairs'][0]['confidence'] == 'clear'
    assert better['pairs'][0]['file_duration_ms'] == 200000
    assert better['pairs'][0]['slot_duration_ms'] == 200000
    assert better['pairs'][0]['duration_delta_ms'] == 0
    assert better['pairs'][0]['ambiguity_reasons'] == []


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
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
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *_args, **_kwargs: [{
        'id': 'raising-id', 'name': 'Raising the Sail',
        'artists': [{'name': 'Philip Glass'}], 'track_number': 17,
        'disc_number': 1, 'duration_ms': 200000,
    }])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda *_args, **_kwargs: {
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


def test_catalogue_disagreement_never_becomes_an_automatic_alias():
    files, slots = version_pair()
    files, slots = files[:1], slots[:1]
    files[0]['acoustid']['recordings'][0].update(artist='Someone Else', mbid='another')
    assert not reconcile_folder(files, slots)['pairs']
    reviewed = reconcile_folder(files, slots, catalogue_review=True)
    assert reviewed['pairs'][0]['confidence'] == 'ambiguous'
    assert reviewed['pairs'][0]['catalogue_disagreement']
    files[0]['acoustid']['recordings'][0]['title'] = 'Unrelated Song'
    assert not reconcile_folder(files, slots, catalogue_review=True)['pairs']


def test_current_edition_requires_consistent_tags_and_complete_supported_coverage():
    plan = {'extra_files': [], 'missing_slots': []}
    current = ScoredCandidate(2, 2, 0, 'musicbrainz', 'current', [], plan)
    alternative = ScoredCandidate(2, 2, 0, 'musicbrainz', 'digital', [], plan)
    files = [{'tags': {'musicbrainz_release_id': 'current'}} for _ in range(2)]
    album = {'canonical_source': 'musicbrainz', 'canonical_album_id': 'current'}
    assert _supported_current_edition(files, album, [alternative, current]) == current
    files[1]['tags']['musicbrainz_release_id'] = 'other'
    assert _supported_current_edition(files, album, [alternative, current]) is None
