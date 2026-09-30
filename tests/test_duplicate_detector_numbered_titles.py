"""Numbered songs on one release must not become duplicate findings."""

from types import SimpleNamespace

import pytest

from core.repair_jobs.duplicate_detector import DuplicateDetectorJob, _conflicting_title_numbers, _normalize
from tests.test_duplicate_detector_slskd_dedup import _FakeContext, _make_track


def _findings(tracks, *, metadata_match=True):
    context = _FakeContext()
    result = SimpleNamespace(scanned=0, findings_created=0, errors=0)
    DuplicateDetectorJob()._scan_bucket(
        bucket_tracks=tracks,
        require_metadata_match=metadata_match,
        title_threshold=0.85,
        artist_threshold=0.80,
        ignore_cross_album=False,
        found_groups=set(),
        processed_holder={'count': 0},
        total=len(tracks),
        result=result,
        context=context,
    )
    return context.findings


@pytest.mark.parametrize(('first', 'second'), [
    ('Ultima Esperanza, Pt. I', 'Ultima Esperanza, Pt. II'),
    ('Simulacra, Pt. II', 'Simulacra, Pt. III'),
    ('Encom, Part I', 'Encom, Part II'),
    ('Segue 1', 'Segue 2'),
    ('Riddle of Steel Pt.1', 'Riddle of Steel Pt.2'),
])
def test_different_numbered_tracks_on_an_album_are_not_duplicates(first, second):
    tracks = [
        _make_track(1, title=first, album='Album', file_path='/music/Album/01.flac'),
        _make_track(2, title=second, album='Album', file_path='/music/Album/02.flac'),
    ]
    assert _findings(tracks) == []


def test_copies_of_the_same_numbered_track_are_still_found():
    tracks = [
        _make_track(1, title='Segue 1', album='Album', file_path='/music/Album/Segue 1.flac'),
        _make_track(2, title='Segue 2', album='Album', file_path='/music/Album/Segue 2.flac'),
        _make_track(3, title='Segue 1', album='Album', file_path='/downloads/Segue 1.flac'),
    ]
    findings = _findings(tracks)
    assert len(findings) == 1
    assert {track['id'] for track in findings[0]['details']['tracks']} == {1, 3}


def test_roman_numbered_tracks_match_the_same_part_across_albums():
    tracks = [
        _make_track(1, title='Ultima Esperanza, Pt. I', album='Ultima Esperanza', file_path='/music/ep/part1.flac'),
        _make_track(2, title='Ultima Esperanza, Pt. II', album='Ultima Esperanza', file_path='/music/ep/part2.flac'),
        _make_track(3, title='Ultima Esperanza, Pt. I', album='Solaris', file_path='/music/album/part1.flac'),
        _make_track(4, title='Ultima Esperanza, Pt. II', album='Solaris', file_path='/music/album/part2.flac'),
    ]
    findings = _findings(tracks)
    assert len(findings) == 2
    assert [{track['id'] for track in finding['details']['tracks']} for finding in findings] == [{1, 3}, {2, 4}]


def test_conflicting_tag_numbers_override_shared_filename():
    tracks = [
        _make_track(1, title='Segue 1', file_path='/music/album/Segue.flac'),
        _make_track(2, title='Segue 2', file_path='/downloads/Segue.flac'),
    ]
    assert _findings(tracks, metadata_match=False) == []


def test_numberless_or_provenance_number_does_not_create_conflict():
    assert not _conflicting_title_numbers(_normalize('Segue'), _normalize('Segue 1'))
    assert not _conflicting_title_numbers(_normalize('Segue 01'), _normalize('Segue 1'))
    assert not _conflicting_title_numbers(_normalize('Ultima Esperanza, Pt. I'), _normalize('Ultima Esperanza, Pt. 1'))
    assert not _conflicting_title_numbers(
        _normalize('Rabbit Run'), _normalize('Rabbit Run - From "8 Mile" Soundtrack'))


@pytest.mark.parametrize('metadata_match', [True, False])
def test_different_remaster_years_still_produce_a_duplicate_finding(metadata_match):
    tracks = [
        _make_track(1, title="Can't Buy Me Love (Remastered 2015)", artist='The Beatles',
                    album='1', file_path='/music/1/song.flac'),
        _make_track(2, title="Can't Buy Me Love (Remastered 2009)", artist='The Beatles',
                    album="A Hard Day's Night", file_path='/music/ahdn/song.flac'),
    ]
    findings = _findings(tracks, metadata_match=metadata_match)
    assert len(findings) == 1
    assert {t['id'] for t in findings[0]['details']['tracks']} == {1, 2}


@pytest.mark.parametrize('qualifier', [
    '(2011 Remaster)', '(Remastered 2011)', '[2011 Mix]', '- Edition 2011',
    '(2011 Version)', '(Remaster 2011)', '(2011 Remastered)', '(Mix 2011)',
    '(2011 Edition)', '(Version 2011)',
    '(Remastered-2011)', '(2011-Remaster)',
])
def test_edition_year_does_not_conflict_with_part_number(qualifier):
    assert not _conflicting_title_numbers(_normalize(f'Pt. 1 {qualifier}'), _normalize('Pt. 1'))
    assert _conflicting_title_numbers(_normalize(f'Pt. 1 {qualifier}'), _normalize('Pt. 2'))


@pytest.mark.parametrize(('first', 'second'), [
    ('Song (Live 1977)', 'Song (Live 1978)'),
    ('Song (Live 1977) (2011 Remaster)', 'Song (Live 1978) (2011 Remaster)'),
    ('Song (Live Version 1977)', 'Song (Live Version 1978)'),
    ('Song (Live 1977 Mix)', 'Song (Live 1978 Mix)'),
    ('Song 1977', 'Song 1978'),
    ('1999 (2011 Remaster)', '1984 (2011 Remaster)'),
    ('Song (Mix 1)', 'Song (Mix 2)'),
])
def test_performance_years_and_non_year_numbers_still_conflict(first, second):
    assert _conflicting_title_numbers(_normalize(first), _normalize(second))


@pytest.mark.parametrize('metadata_match', [True, False])
def test_numberless_track_cannot_bridge_conflicting_parts(metadata_match):
    tracks = [
        _make_track(1, title='A Long Segue', file_path='/music/a/Segue.flac'),
        _make_track(2, title='A Long Segue 1', file_path='/music/b/Segue.flac'),
        _make_track(3, title='A Long Segue 2', file_path='/music/c/Segue.flac'),
    ]
    findings = _findings(tracks, metadata_match=metadata_match)
    assert len(findings) == 1
    assert {t['id'] for t in findings[0]['details']['tracks']} == {1, 2}
