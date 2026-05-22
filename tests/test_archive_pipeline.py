"""Tests for ``core/archive_pipeline.py``.

Covers the audio-file walker, archive detector, and zip / tar
extraction (rar / 7z paths use optional deps so they're only
exercised when the libs are present in the test environment).
Path-traversal protection gets explicit coverage — a malicious
archive must not escape the extraction directory.
"""

from __future__ import annotations

import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from core.archive_pipeline import (
    AUDIO_EXTENSIONS,
    ARCHIVE_EXTENSIONS,
    collect_audio_after_extraction,
    extract_archive,
    extract_all_in_dir,
    find_archives_in_dir,
    is_archive,
    walk_audio_files,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_audio_extensions_cover_common_formats() -> None:
    for ext in ('.flac', '.mp3', '.m4a', '.ogg', '.opus', '.wav', '.aac', '.wma'):
        assert ext in AUDIO_EXTENSIONS


def test_archive_extensions_cover_common_formats() -> None:
    for ext in ('.zip', '.rar', '.tar', '.7z'):
        assert ext in ARCHIVE_EXTENSIONS


# ---------------------------------------------------------------------------
# is_archive
# ---------------------------------------------------------------------------


def test_is_archive_detects_simple_extensions(tmp_path: Path) -> None:
    zip_path = tmp_path / 'x.zip'
    zip_path.write_bytes(b'PK\x03\x04')  # minimal — is_archive doesn't validate content
    assert is_archive(zip_path) is True


def test_is_archive_detects_compound_tar_extensions(tmp_path: Path) -> None:
    """``.tar.gz`` etc. — Path.suffix only catches the last suffix,
    so the detector has to special-case compound extensions."""
    targz = tmp_path / 'x.tar.gz'
    targz.write_bytes(b'\x1f\x8b')
    assert is_archive(targz) is True


def test_is_archive_returns_false_for_audio(tmp_path: Path) -> None:
    flac = tmp_path / 'song.flac'
    flac.write_bytes(b'fLaC')
    assert is_archive(flac) is False


def test_is_archive_returns_false_for_missing_file(tmp_path: Path) -> None:
    assert is_archive(tmp_path / 'does-not-exist.zip') is False


# ---------------------------------------------------------------------------
# walk_audio_files
# ---------------------------------------------------------------------------


def test_walk_audio_files_finds_nested(tmp_path: Path) -> None:
    # Layout: root/album/disc1/track.flac + root/album/disc2/track.mp3
    (tmp_path / 'album' / 'disc1').mkdir(parents=True)
    (tmp_path / 'album' / 'disc2').mkdir(parents=True)
    (tmp_path / 'album' / 'disc1' / 'track1.flac').write_bytes(b'fLaC')
    (tmp_path / 'album' / 'disc2' / 'track1.mp3').write_bytes(b'ID3')
    (tmp_path / 'album' / 'cover.jpg').write_bytes(b'\xff\xd8')
    found = walk_audio_files(tmp_path)
    names = sorted(p.name for p in found)
    assert names == ['track1.flac', 'track1.mp3']


def test_walk_audio_files_returns_empty_for_missing(tmp_path: Path) -> None:
    assert walk_audio_files(tmp_path / 'does-not-exist') == []


def test_walk_audio_files_ignores_non_audio(tmp_path: Path) -> None:
    (tmp_path / 'readme.txt').write_text('hi')
    (tmp_path / 'cover.png').write_bytes(b'\x89PNG')
    assert walk_audio_files(tmp_path) == []


def test_walk_audio_files_case_insensitive_extension(tmp_path: Path) -> None:
    """Lots of torrents have uppercase extensions (.MP3, .FLAC) —
    the walker must catch those too."""
    (tmp_path / 'TRACK.MP3').write_bytes(b'ID3')
    (tmp_path / 'TRACK.FLAC').write_bytes(b'fLaC')
    found = walk_audio_files(tmp_path)
    assert len(found) == 2


# ---------------------------------------------------------------------------
# find_archives_in_dir
# ---------------------------------------------------------------------------


def test_find_archives_in_dir_only_top_level(tmp_path: Path) -> None:
    """find_archives doesn't recurse — torrents put the archive at
    the top of the dir; deeper search risks extracting unrelated
    archives that ship inside a sample folder, etc."""
    (tmp_path / 'album.zip').write_bytes(b'PK\x03\x04')
    nested = tmp_path / 'subdir'
    nested.mkdir()
    (nested / 'nested.zip').write_bytes(b'PK\x03\x04')
    found = find_archives_in_dir(tmp_path)
    assert [p.name for p in found] == ['album.zip']


def test_find_archives_in_dir_empty(tmp_path: Path) -> None:
    assert find_archives_in_dir(tmp_path) == []
    assert find_archives_in_dir(tmp_path / 'missing') == []


# ---------------------------------------------------------------------------
# extract_archive — zip
# ---------------------------------------------------------------------------


def test_extract_zip_writes_files(tmp_path: Path) -> None:
    zip_path = tmp_path / 'album.zip'
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr('track1.mp3', b'ID3 track1 data')
        zf.writestr('track2.flac', b'fLaC data')
    result = extract_archive(zip_path)
    assert result == zip_path.parent
    assert (tmp_path / 'track1.mp3').exists()
    assert (tmp_path / 'track2.flac').exists()


def test_extract_zip_rejects_path_traversal(tmp_path: Path) -> None:
    """A malicious archive trying to write ``../../etc/passwd`` must
    be refused without extracting anything."""
    zip_path = tmp_path / 'evil.zip'
    extract_dest = tmp_path / 'staging'
    extract_dest.mkdir()
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr('../escaped.txt', b'evil')
        zf.writestr('safe.mp3', b'ID3')
    extract_archive(zip_path, extract_to=extract_dest)
    # Neither file should have landed — extraction aborts on the first
    # traversal attempt.
    assert not (extract_dest / 'safe.mp3').exists()
    assert not (tmp_path / 'escaped.txt').exists()


def test_extract_zip_returns_none_for_bad_zip(tmp_path: Path) -> None:
    bad = tmp_path / 'not-a-zip.zip'
    bad.write_bytes(b'this is not a zip')
    assert extract_archive(bad) is None


def test_extract_archive_missing_file_returns_none(tmp_path: Path) -> None:
    assert extract_archive(tmp_path / 'does-not-exist.zip') is None


# ---------------------------------------------------------------------------
# extract_archive — tar
# ---------------------------------------------------------------------------


def test_extract_tar_gz_writes_files(tmp_path: Path) -> None:
    payload = tmp_path / 'track.mp3'
    payload.write_bytes(b'ID3 track')
    tar_path = tmp_path / 'album.tar.gz'
    with tarfile.open(tar_path, 'w:gz') as tf:
        tf.add(payload, arcname='track.mp3')
    payload.unlink()  # remove the source so we can verify the extract recreated it
    extract_archive(tar_path)
    assert (tmp_path / 'track.mp3').exists()


def test_extract_tar_rejects_path_traversal(tmp_path: Path) -> None:
    extract_dest = tmp_path / 'staging'
    extract_dest.mkdir()
    tar_path = tmp_path / 'evil.tar'
    payload = tmp_path / 'src.txt'
    payload.write_bytes(b'evil')
    with tarfile.open(tar_path, 'w') as tf:
        info = tf.gettarinfo(str(payload), arcname='../escaped.txt')
        with payload.open('rb') as fh:
            tf.addfile(info, fh)
    extract_archive(tar_path, extract_to=extract_dest)
    assert not (tmp_path / 'escaped.txt').exists()


# ---------------------------------------------------------------------------
# extract_all_in_dir + collect_audio_after_extraction
# ---------------------------------------------------------------------------


def test_extract_all_in_dir_handles_multiple_archives(tmp_path: Path) -> None:
    (tmp_path / 'one.zip')  # placeholder
    z1 = tmp_path / 'one.zip'
    z2 = tmp_path / 'two.zip'
    with zipfile.ZipFile(z1, 'w') as zf:
        zf.writestr('a.mp3', b'a')
    with zipfile.ZipFile(z2, 'w') as zf:
        zf.writestr('b.mp3', b'b')
    out = extract_all_in_dir(tmp_path)
    assert len(out) == 2
    assert (tmp_path / 'a.mp3').exists()
    assert (tmp_path / 'b.mp3').exists()


def test_collect_audio_after_extraction_combines_loose_and_extracted(tmp_path: Path) -> None:
    """The typical mixed case: torrent dropped a .zip and also some
    loose .mp3 files alongside it. The collector returns BOTH."""
    (tmp_path / 'bonus.mp3').write_bytes(b'ID3')
    zip_path = tmp_path / 'main.zip'
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr('track1.flac', b'fLaC')
        zf.writestr('track2.flac', b'fLaC')
    found = collect_audio_after_extraction(tmp_path)
    names = sorted(p.name for p in found)
    assert names == ['bonus.mp3', 'track1.flac', 'track2.flac']


def test_collect_audio_after_extraction_no_archives_no_audio(tmp_path: Path) -> None:
    assert collect_audio_after_extraction(tmp_path) == []
