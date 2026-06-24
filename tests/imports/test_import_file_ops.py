import sys
import types

from core.imports.file_ops import (
    cleanup_empty_directories,
    safe_move_file,
)
from core.imports.filename import (
    extract_explicit_track_number,
    extract_track_number_from_filename,
)
from core.imports.staging import read_staging_file_metadata


def test_extract_track_number_from_filename_handles_common_patterns():
    assert extract_track_number_from_filename("01 - Song.mp3") == 1
    assert extract_track_number_from_filename("1-03 - Song.mp3") == 3
    # Bare filename keeps the auto-import-friendly default of 1 — there's
    # no upstream metadata to recover from in that flow.
    assert extract_track_number_from_filename("Artist - Song.mp3") == 1


def test_extract_explicit_track_number_returns_zero_when_no_prefix():
    """Staging readers need to distinguish 'track 1' from 'unknown'.

    Pinned because:
    - the legacy extractor defaults to 1 (auto-import semantics),
    - staging file scanners that conflate the two end up writing every
      file in an untagged album bundle to track_number=1.
    """
    # Bare titles with no numeric prefix → 0 (unknown).
    assert extract_explicit_track_number("Artist - Song.mp3") == 0
    assert extract_explicit_track_number("Cha-La Head-Cha-La.flac") == 0
    assert extract_explicit_track_number("") == 0
    # Real prefixes still parse correctly.
    assert extract_explicit_track_number("01 - Song.mp3") == 1
    assert extract_explicit_track_number("(03) Song.mp3") == 3
    # Disc-track format requires a separator after the track number.
    assert extract_explicit_track_number("1-07 - Song.mp3") == 7


def test_safe_move_file_replaces_existing_destination(tmp_path):
    src = tmp_path / "source.flac"
    dst_dir = tmp_path / "dest"
    dst_dir.mkdir()
    dst = dst_dir / "track.flac"

    src.write_text("new")
    dst.write_text("old")

    safe_move_file(src, dst)

    assert not src.exists()
    assert dst.read_text() == "new"


def test_cleanup_empty_directories_removes_nested_empty_paths(tmp_path):
    download_root = tmp_path / "downloads"
    nested_dir = download_root / "Artist" / "Album"
    nested_dir.mkdir(parents=True)
    moved_file_path = nested_dir / "track.flac"

    cleanup_empty_directories(str(download_root), str(moved_file_path))

    assert not nested_dir.exists()
    assert not (download_root / "Artist").exists()
    assert download_root.exists()


def test_read_staging_file_metadata_reads_tags(monkeypatch, tmp_path):
    file_path = tmp_path / "Song One.flac"
    file_path.write_text("fake")

    class DummyTags:
        def __init__(self):
            self.values = {
                "title": ["Song One"],
                "artist": ["Artist One"],
                "albumartist": ["Album Artist"],
                "album": ["Album One"],
                "tracknumber": ["03/12"],
                "discnumber": ["2/3"],
            }

        def get(self, key, default=None):
            return self.values.get(key, default)

    fake_mutagen = types.ModuleType("mutagen")
    fake_mutagen.File = lambda path, easy=True: DummyTags()
    monkeypatch.setitem(sys.modules, "mutagen", fake_mutagen)

    metadata = read_staging_file_metadata(str(file_path), file_path.name)

    assert metadata == {
        "title": "Song One",
        "artist": "Artist One",
        "albumartist": "Album Artist",
        "album": "Album One",
        "track_number": 3,
        "disc_number": 2,
    }


def test_read_staging_file_metadata_falls_back_to_filename_track_number(monkeypatch, tmp_path):
    file_path = tmp_path / "07 - Song Two.flac"
    file_path.write_text("fake")

    fake_mutagen = types.ModuleType("mutagen")
    fake_mutagen.File = lambda path, easy=True: None
    monkeypatch.setitem(sys.modules, "mutagen", fake_mutagen)

    metadata = read_staging_file_metadata(str(file_path), file_path.name)

    assert metadata["title"] == "07 - Song Two"
    assert metadata["track_number"] == 7
    assert metadata["disc_number"] == 1


def test_read_staging_file_metadata_returns_zero_track_when_unknown(monkeypatch, tmp_path):
    """Bare filename + no tags → track_number=0, not 1.

    Pre-fix this returned 1 because the filename extractor's default
    was 1. The bug caused every untagged file in an album-bundle
    download to land in the staging cache with track_number=1, which
    then short-circuited the downstream resolution chain that should
    have picked up the real number from track_info.
    """
    file_path = tmp_path / "Cha-La Head-Cha-La.flac"
    file_path.write_text("fake")

    fake_mutagen = types.ModuleType("mutagen")
    fake_mutagen.File = lambda path, easy=True: None
    monkeypatch.setitem(sys.modules, "mutagen", fake_mutagen)

    metadata = read_staging_file_metadata(str(file_path), file_path.name)

    assert metadata["track_number"] == 0


def test_read_staging_file_metadata_uses_filename_fallbacks_when_tags_are_invalid(monkeypatch, tmp_path):
    file_path = tmp_path / "02 - Song Three.flac"
    file_path.write_text("fake")

    class DummyTags:
        def __init__(self):
            self.values = {
                "title": [""],
                "artist": "Artist One",
                "albumartist": "",
                "album": ["Album One"],
                "tracknumber": ["not-a-number"],
                "discnumber": ["bad/disc"],
            }

        def get(self, key, default=None):
            return self.values.get(key, default)

    fake_mutagen = types.ModuleType("mutagen")
    fake_mutagen.File = lambda path, easy=True: DummyTags()
    monkeypatch.setitem(sys.modules, "mutagen", fake_mutagen)

    metadata = read_staging_file_metadata(str(file_path), file_path.name)

    assert metadata == {
        "title": "02 - Song Three",
        "artist": "Artist One",
        "albumartist": "Artist One",
        "album": "Album One",
        "track_number": 2,
        "disc_number": 1,
    }


# ── atomic cross-filesystem move (Jellyfin null-disc mitigation) ──────────────
import errno  # noqa: E402
import os  # noqa: E402

import pytest  # noqa: E402

from core.imports import file_ops as _fo  # noqa: E402
from core.imports.file_ops import _atomic_cross_device_move  # noqa: E402


def test_same_fs_move_moves_and_removes_source(tmp_path):
    src = tmp_path / "s.flac"
    src.write_text("hello")
    dst = tmp_path / "lib" / "t.flac"          # parent created by safe_move_file
    safe_move_file(src, dst)
    assert dst.read_text() == "hello"
    assert not src.exists()


def test_cross_device_move_routes_to_atomic_and_completes(tmp_path, monkeypatch):
    # Simulate a cross-filesystem move: the same-fs os.replace raises EXDEV, and the
    # atomic helper's temp->dst replace (same fs) succeeds. The file must complete and
    # no partial temp file may be left at the final name's directory.
    src = tmp_path / "s.flac"
    src.write_text("payload")
    dstdir = tmp_path / "lib"
    dstdir.mkdir()
    dst = dstdir / "t.flac"

    real_replace = os.replace

    def fake_replace(a, b):
        if str(a) == str(src):                  # the cross-fs move attempt
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(a, b)               # the temp -> dst rename (same fs)

    monkeypatch.setattr(_fo.os, "replace", fake_replace)
    safe_move_file(src, dst)

    assert dst.read_text() == "payload"
    assert not src.exists()
    assert not list(dstdir.glob(".*ssync-tmp"))   # complete file only, no leftover temp


def test_atomic_helper_completes_and_cleans_temp(tmp_path):
    src = tmp_path / "s.flac"
    src.write_text("payload")
    dstdir = tmp_path / "d"
    dstdir.mkdir()
    dst = dstdir / "t.flac"
    _atomic_cross_device_move(src, dst)
    assert dst.read_text() == "payload"
    assert not src.exists()
    assert not list(dstdir.glob(".*ssync-tmp"))


def test_atomic_helper_cleans_temp_and_keeps_source_on_failure(tmp_path, monkeypatch):
    src = tmp_path / "s.flac"
    src.write_text("payload")
    dstdir = tmp_path / "d"
    dstdir.mkdir()
    dst = dstdir / "t.flac"

    def boom(_a, _b):
        raise OSError("replace failed")

    monkeypatch.setattr(_fo.os, "replace", boom)
    with pytest.raises(OSError):
        _atomic_cross_device_move(src, dst)

    assert src.exists()                           # source preserved on failure
    assert not dst.exists()                       # no partial final file
    assert not list(dstdir.glob(".*ssync-tmp"))   # temp cleaned up
