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
