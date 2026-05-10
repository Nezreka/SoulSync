"""Regression test for the multi-disc auto-import matching bug.

User report (2026-05-08, Mr. Morale & The Big Steppers): an album with
multiple discs got dumped into staging — discs 1 and 2 loose in the
root, disc 3 in its own folder, every file perfectly tagged with
``disc_number`` + ``track_number`` + ``title``. Auto-import processed
only 9 tracks total instead of all 27.

Two bugs in ``AutoImportWorker._match_tracks`` caused it:

1. **Quality dedup keyed on track_number alone.** The dedup loop kept
   ``seen_track_nums[track_number] = file`` and dropped any later file
   with the same number, treating it as a quality duplicate. On a
   multi-disc release where every disc has tracks 1..N, that collapses
   the album to one disc's worth of files before matching even runs —
   half (or more) of the tracks vanish before the matcher sees them.

2. **Match scoring ignored disc_number.** The 30% track-number bonus
   fired whenever ``ft['track_number'] == track_num`` regardless of disc.
   File with tag ``(disc=2, track=6)`` (Auntie Diaries, 281s) got the
   full bonus when matched against API track ``(disc=1, track=6)`` (Rich
   Interlude, 103s) — wrong file → wrong destination → integrity
   check correctly rejected and quarantined the file.

Fix in this PR: dedup keys on ``(disc_number, track_number)`` tuples;
match scoring only awards the 30% bonus when BOTH disc and track
numbers agree, with a small consolation bonus for same-track-number
cross-disc collisions so title similarity still drives the match.

These tests pin both behaviors so multi-disc albums stay intact
through the full import pipeline.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from core import auto_import_worker as aiw


# ---------------------------------------------------------------------------
# Fixtures — tagged file fakes + worker setup
# ---------------------------------------------------------------------------


def _file_tags(*, disc: int, track: int, title: str, artist: str = 'Kendrick Lamar',
               album: str = 'Mr. Morale & The Big Steppers') -> Dict[str, Any]:
    """Build the tag dict shape ``_read_file_tags`` returns."""
    return {
        'title': title,
        'artist': artist,
        'album': album,
        'track_number': track,
        'disc_number': disc,
        'year': '2022',
    }


def _api_track(disc: int, track: int, title: str, artist: str = 'Kendrick Lamar') -> Dict[str, Any]:
    """Build a track dict matching the shape the metadata source returns."""
    return {
        'name': title,
        'track_number': track,
        'disc_number': disc,
        'artists': [{'name': artist}],
        'id': f'{disc}-{track}',
    }


@pytest.fixture
def worker():
    """A worker instance — the matching logic doesn't actually need
    most of the worker's deps so we instantiate raw."""
    w = aiw.AutoImportWorker.__new__(aiw.AutoImportWorker)
    return w


# ---------------------------------------------------------------------------
# Test 1 — dedup must NOT collapse same-track-numbers across discs
# ---------------------------------------------------------------------------


def test_dedup_preserves_files_with_same_track_number_across_different_discs(worker, monkeypatch):
    """The bug: dedup keyed by track_number alone treated disc 1 track 6
    and disc 2 track 6 as quality duplicates, dropped one. Fix: key by
    (disc_number, track_number) tuple — both files survive dedup.

    User scenario: 18 loose files in staging root, all tagged with
    ``disc_number`` 1 or 2 and ``track_number`` 1..9. Pre-fix the
    matcher only saw 9 of them after dedup. Post-fix all 18.
    """
    # 18 fake files: discs 1 + 2, tracks 1..9 each
    files = [f"/fake/d{disc}_t{track}.flac" for disc in (1, 2) for track in range(1, 10)]
    file_tags = {
        f: _file_tags(disc=disc, track=track,
                      title=f'Track {disc}.{track}')
        for f, (disc, track) in zip(
            files, [(d, t) for d in (1, 2) for t in range(1, 10)],
        )
    }

    # Mock _read_file_tags to return our test tags
    monkeypatch.setattr(aiw, '_read_file_tags', lambda f: file_tags[f])

    # Mock the metadata client + album fetch to return 18 tracks
    api_tracks = [_api_track(disc, track, f'Track {disc}.{track}')
                  for disc in (1, 2) for track in range(1, 10)]
    fake_client = MagicMock()
    fake_client.get_album = MagicMock(return_value={
        'id': 'album-1',
        'name': 'Mr. Morale & The Big Steppers',
        'tracks': {'items': api_tracks},
    })

    candidate = aiw.FolderCandidate(
        path='/staging',
        name='staging',
        audio_files=files,
        folder_hash='hash1',
    )

    identification = {
        'source': 'spotify',
        'album_id': 'album-1',
        'album_name': 'Mr. Morale & The Big Steppers',
        'artist_name': 'Kendrick Lamar',
        'identification_confidence': 1.0,
    }

    with patch('core.metadata_service.get_client_for_source', return_value=fake_client), \
         patch('core.metadata_service.get_album_tracks_for_source', return_value=None):
        result = worker._match_tracks(candidate, identification)

    assert result is not None
    # All 18 files must end up matched — pre-fix only 9 survived dedup,
    # then 4 of those got mismatched and integrity-rejected.
    assert len(result['matches']) == 18, (
        f"Expected 18 matches across both discs, got {len(result['matches'])}. "
        f"Dedup probably collapsed same-track-numbers across discs."
    )
    # No file should be in unmatched
    assert not result['unmatched_files']


# ---------------------------------------------------------------------------
# Test 2 — match scoring respects disc_number
# ---------------------------------------------------------------------------


def test_match_scoring_pairs_files_to_correct_disc(worker, monkeypatch):
    """The bug: the 30% track-number bonus fired regardless of disc, so
    files got matched to the wrong-disc track when both shared a track
    number. Fix: bonus only when (disc, track) BOTH match.

    Pin behavior: file tagged (disc=2, track=6, title='Auntie Diaries')
    must match the API's (disc=2, track=6) track, NOT the (disc=1,
    track=6) track even though both have track_number=6.
    """
    files = [
        '/fake/disc1_06.flac',  # Rich (Interlude)
        '/fake/disc2_06.flac',  # Auntie Diaries
    ]
    file_tags = {
        '/fake/disc1_06.flac': _file_tags(disc=1, track=6, title='Rich (Interlude)'),
        '/fake/disc2_06.flac': _file_tags(disc=2, track=6, title='Auntie Diaries'),
    }
    monkeypatch.setattr(aiw, '_read_file_tags', lambda f: file_tags[f])

    api_tracks = [
        _api_track(1, 6, 'Rich (Interlude)'),
        _api_track(2, 6, 'Auntie Diaries'),
    ]
    fake_client = MagicMock()
    fake_client.get_album = MagicMock(return_value={
        'id': 'album-1', 'name': 'Mr. Morale',
        'tracks': {'items': api_tracks},
    })

    candidate = aiw.FolderCandidate(
        path='/staging', name='staging',
        audio_files=files, folder_hash='hash2',
    )
    identification = {
        'source': 'spotify', 'album_id': 'album-1',
        'album_name': 'Mr. Morale', 'artist_name': 'Kendrick Lamar',
        'identification_confidence': 1.0,
    }

    with patch('core.metadata_service.get_client_for_source', return_value=fake_client), \
         patch('core.metadata_service.get_album_tracks_for_source', return_value=None):
        result = worker._match_tracks(candidate, identification)

    assert result is not None
    assert len(result['matches']) == 2

    # Build a {track_disc: matched_file} map for assertion
    by_disc = {
        m['track']['disc_number']: m['file'] for m in result['matches']
    }
    assert by_disc[1] == '/fake/disc1_06.flac', (
        "API track (disc=1, track=6) should match the disc-1 file, "
        "not get cross-matched to the disc-2 file just because they "
        "share track_number=6."
    )
    assert by_disc[2] == '/fake/disc2_06.flac', (
        "API track (disc=2, track=6) should match the disc-2 file."
    )


# ---------------------------------------------------------------------------
# Test 3 — single-disc albums still work (regression guard)
# ---------------------------------------------------------------------------


def test_single_disc_albums_still_match_normally(worker, monkeypatch):
    """The disc-aware fix mustn't break single-disc albums where every
    file has disc_number=1 (or no disc tag at all → defaults to 1)."""
    files = [f'/fake/track_{i:02d}.flac' for i in range(1, 6)]
    file_tags = {
        f'/fake/track_{i:02d}.flac': _file_tags(disc=1, track=i, title=f'Song {i}')
        for i in range(1, 6)
    }
    monkeypatch.setattr(aiw, '_read_file_tags', lambda f: file_tags[f])

    api_tracks = [_api_track(1, i, f'Song {i}') for i in range(1, 6)]
    fake_client = MagicMock()
    fake_client.get_album = MagicMock(return_value={
        'id': 'album-1', 'name': 'Test Album',
        'tracks': {'items': api_tracks},
    })

    candidate = aiw.FolderCandidate(
        path='/staging', name='Album',
        audio_files=files, folder_hash='hash3',
    )
    identification = {
        'source': 'spotify', 'album_id': 'album-1',
        'album_name': 'Test Album', 'artist_name': 'Test Artist',
        'identification_confidence': 1.0,
    }

    with patch('core.metadata_service.get_client_for_source', return_value=fake_client), \
         patch('core.metadata_service.get_album_tracks_for_source', return_value=None):
        result = worker._match_tracks(candidate, identification)

    assert result is not None
    assert len(result['matches']) == 5
    # Each track i matched to track_0i.flac
    for m in result['matches']:
        track_num = m['track']['track_number']
        assert m['file'] == f'/fake/track_{track_num:02d}.flac'


# ---------------------------------------------------------------------------
# Test 4 — quality dedup still works WITHIN a single (disc, track) position
# ---------------------------------------------------------------------------


def test_quality_dedup_still_picks_higher_quality_for_same_position(worker, monkeypatch):
    """Two files at (disc=1, track=1) — one .mp3, one .flac. Dedup must
    keep the .flac. The fix only changed the dedup KEY (added disc_number
    to the tuple), not the quality-comparison logic — pin the quality
    behavior."""
    files = ['/fake/disc1_track1.mp3', '/fake/disc1_track1.flac']
    file_tags = {
        '/fake/disc1_track1.mp3': _file_tags(disc=1, track=1, title='Song 1'),
        '/fake/disc1_track1.flac': _file_tags(disc=1, track=1, title='Song 1'),
    }
    monkeypatch.setattr(aiw, '_read_file_tags', lambda f: file_tags[f])

    api_tracks = [_api_track(1, 1, 'Song 1')]
    fake_client = MagicMock()
    fake_client.get_album = MagicMock(return_value={
        'id': 'album-1', 'name': 'Test Album',
        'tracks': {'items': api_tracks},
    })

    candidate = aiw.FolderCandidate(
        path='/staging', name='Album',
        audio_files=files, folder_hash='hash4',
    )
    identification = {
        'source': 'spotify', 'album_id': 'album-1',
        'album_name': 'Test Album', 'artist_name': 'Test Artist',
        'identification_confidence': 1.0,
    }

    with patch('core.metadata_service.get_client_for_source', return_value=fake_client), \
         patch('core.metadata_service.get_album_tracks_for_source', return_value=None):
        result = worker._match_tracks(candidate, identification)

    assert result is not None
    assert len(result['matches']) == 1
    # FLAC must win
    assert result['matches'][0]['file'].endswith('.flac')
