"""Tests for the chaotic-staging scanner improvements in
``AutoImportWorker._scan_directory``.

Two related behaviors pinned here:

1. **Loose files grouped by album tag.** When the staging root has
   loose files from multiple different albums (user moved tracks out
   of their album folders + dumped them at root), each album's tracks
   get their own candidate via the embedded `album` tag. Pre-fix:
   everything bundled into one fake album, identifier picked the
   most-common tag, other albums' tracks left unmatched.

2. **Always recurse into non-disc subfolders.** Pre-fix the scanner
   would skip subfolders entirely when loose files existed at the
   same level. So a layout like::

       Staging/
         loose1.flac           ← processed
         Disc 1/               ← attached to loose
         Album Folder/         ← IGNORED

   would silently skip "Album Folder" because root had loose files.
   Post-fix: subfolders always recursed regardless of loose files.

Tests use temp directories with real FLAC files (mutagen-written)
so the scanner's tag reads exercise the real code path.
"""

from __future__ import annotations

import os
import struct
import tempfile

import pytest

from core.auto_import_worker import AutoImportWorker


def _write_flac(path: str, *, album: str = '', track: int = 0, disc: int = 1, title: str = 'Test'):
    """Write a real FLAC with the given tags. Same minimal-FLAC
    bootstrap pattern used elsewhere in the test suite."""
    from mutagen.flac import FLAC

    fLaC = b'fLaC'
    streaminfo = bytearray(34)
    streaminfo[0:2] = struct.pack('>H', 4096)
    streaminfo[2:4] = struct.pack('>H', 4096)
    streaminfo[10] = 0x0A
    streaminfo[12] = 0x70
    block_header = bytes([0x80, 0x00, 0x00, 0x22])
    with open(path, 'wb') as f:
        f.write(fLaC + block_header + bytes(streaminfo))

    audio = FLAC(path)
    if album:
        audio['ALBUM'] = album
    if track:
        audio['TRACKNUMBER'] = str(track)
    if disc:
        audio['DISCNUMBER'] = str(disc)
    if title:
        audio['TITLE'] = title
    audio.save()


@pytest.fixture
def worker():
    """Bare worker — `_scan_directory` doesn't need full deps."""
    return AutoImportWorker.__new__(AutoImportWorker)


# ---------------------------------------------------------------------------
# Loose-file grouping by album tag
# ---------------------------------------------------------------------------


def test_loose_files_from_multiple_albums_become_multiple_candidates(worker, tmp_path):
    """Two albums' worth of tracks at root → two candidates, not one
    bundle. Validates the chaotic-staging fix."""
    # Album A: 3 tracks
    for i in range(1, 4):
        _write_flac(
            str(tmp_path / f'A_{i}.flac'),
            album='Album A', track=i, title=f'A track {i}',
        )
    # Album B: 2 tracks
    for i in range(1, 3):
        _write_flac(
            str(tmp_path / f'B_{i}.flac'),
            album='Album B', track=i, title=f'B track {i}',
        )

    candidates = []
    worker._scan_directory(str(tmp_path), candidates, staging_root=str(tmp_path))

    assert len(candidates) == 2
    album_keys = sorted(
        len(c.audio_files) for c in candidates if not c.is_single
    )
    assert album_keys == [2, 3]   # one 3-track album + one 2-track album


def test_untagged_loose_files_become_individual_singles(worker, tmp_path):
    """Files with no album tag can't be grouped — each becomes its
    own single candidate."""
    _write_flac(str(tmp_path / 'orphan_a.flac'), album='', track=0)
    _write_flac(str(tmp_path / 'orphan_b.flac'), album='', track=0)

    candidates = []
    worker._scan_directory(str(tmp_path), candidates, staging_root=str(tmp_path))

    singles = [c for c in candidates if c.is_single]
    assert len(singles) == 2


def test_single_album_loose_files_still_one_candidate(worker, tmp_path):
    """Regression guard — when all loose files share an album, behavior
    matches the previous "bundle everything into one candidate" path.
    Single-album staging shouldn't fragment into per-track singles."""
    for i in range(1, 6):
        _write_flac(
            str(tmp_path / f'track_{i}.flac'),
            album='Single Album', track=i, title=f'Song {i}',
        )

    candidates = []
    worker._scan_directory(str(tmp_path), candidates, staging_root=str(tmp_path))

    album_candidates = [c for c in candidates if not c.is_single]
    assert len(album_candidates) == 1
    assert len(album_candidates[0].audio_files) == 5


# ---------------------------------------------------------------------------
# Always-recurse-into-subfolders
# ---------------------------------------------------------------------------


def test_subfolders_processed_even_when_root_has_loose_files(worker, tmp_path):
    """The original bug — root has loose files AND a non-disc
    subfolder. Pre-fix: subfolder ignored. Post-fix: subfolder
    recursed."""
    # Loose file at root
    _write_flac(
        str(tmp_path / 'loose.flac'),
        album='Loose Album', track=1, title='Loose Song',
    )

    # Subfolder with its own album
    sub = tmp_path / 'Other Album'
    sub.mkdir()
    for i in range(1, 4):
        _write_flac(
            str(sub / f't{i}.flac'),
            album='Other Album', track=i, title=f'Other {i}',
        )

    candidates = []
    worker._scan_directory(str(tmp_path), candidates, staging_root=str(tmp_path))

    # 1 candidate from loose + 1 candidate from subfolder = 2
    assert len(candidates) == 2
    paths = {c.path for c in candidates}
    assert any('Other Album' in p for p in paths), (
        f"Subfolder candidate missing — paths: {paths}. Pre-fix "
        f"behavior: scanner ignored the subfolder when root had "
        f"loose files."
    )


# ---------------------------------------------------------------------------
# Disc folder attachment to loose-file groups
# ---------------------------------------------------------------------------


def test_disc_folder_attaches_to_matching_loose_group(worker, tmp_path):
    """Loose Mr. Morale tracks at root + Disc 2 folder also tagged
    Mr. Morale → disc folder merges into the Mr. Morale loose
    candidate. Mirrors the user's typical multi-disc layout."""
    # Loose disc 1 tracks
    for i in range(1, 4):
        _write_flac(
            str(tmp_path / f'disc1_{i}.flac'),
            album='Mr. Morale', track=i, disc=1, title=f'D1 {i}',
        )

    # Disc 2 folder, same album
    disc2 = tmp_path / 'Disc 2'
    disc2.mkdir()
    for i in range(1, 4):
        _write_flac(
            str(disc2 / f'd2_{i}.flac'),
            album='Mr. Morale', track=i, disc=2, title=f'D2 {i}',
        )

    candidates = []
    worker._scan_directory(str(tmp_path), candidates, staging_root=str(tmp_path))

    assert len(candidates) == 1
    candidate = candidates[0]
    # All 6 files (3 loose + 3 disc 2) merged into one candidate
    assert len(candidate.audio_files) == 6
    # Disc structure carries both disc 0 (loose) + disc 2
    assert 0 in candidate.disc_structure
    assert 2 in candidate.disc_structure


def test_disc_folder_with_no_matching_loose_group_becomes_standalone(worker, tmp_path):
    """Loose Album A tracks at root + Disc 2 folder tagged Album B →
    disc folder doesn't merge into A; becomes its own candidate."""
    _write_flac(
        str(tmp_path / 'a1.flac'),
        album='Album A', track=1, title='A1',
    )
    _write_flac(
        str(tmp_path / 'a2.flac'),
        album='Album A', track=2, title='A2',
    )

    disc2 = tmp_path / 'Disc 2'
    disc2.mkdir()
    _write_flac(
        str(disc2 / 'b1.flac'),
        album='Album B', track=1, disc=2, title='B1',
    )

    candidates = []
    worker._scan_directory(str(tmp_path), candidates, staging_root=str(tmp_path))

    # 1 candidate for Album A loose + 1 candidate for the standalone
    # disc folder = 2 total
    assert len(candidates) == 2
    a_candidate = next(c for c in candidates if len(c.audio_files) == 2)
    standalone = next(c for c in candidates if c is not a_candidate)
    assert len(a_candidate.audio_files) == 2  # only Album A loose, no disc
    assert len(standalone.audio_files) == 1   # Album B disc 2 alone


# ---------------------------------------------------------------------------
# Disc-only directory (regression guard)
# ---------------------------------------------------------------------------


def test_disc_only_directory_still_works(worker, tmp_path):
    """No loose files, only Disc 1/Disc 2 subfolders → treat parent
    directory as the album candidate. Pre-existing behavior preserved."""
    for disc_num in (1, 2):
        disc_dir = tmp_path / f'Disc {disc_num}'
        disc_dir.mkdir()
        for i in range(1, 4):
            _write_flac(
                str(disc_dir / f'd{disc_num}_t{i}.flac'),
                album='Disc Only Album', track=i, disc=disc_num,
                title=f'D{disc_num}T{i}',
            )

    candidates = []
    worker._scan_directory(str(tmp_path), candidates, staging_root=str(tmp_path))

    assert len(candidates) == 1
    assert len(candidates[0].audio_files) == 6
    assert candidates[0].disc_structure == {
        1: candidates[0].disc_structure[1],
        2: candidates[0].disc_structure[2],
    }
