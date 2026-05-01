"""Regression tests for slskd dedup-suffix orphan cleanup.

Discord-reported (Shdjfgatdif): the downloads folder fills up with
files like ``Song_639067852665564677.flac`` over time. slskd appends
``_<19-digit unix-nanosecond timestamp>`` to a filename when the
destination already contains a same-named file (concurrent downloads
of the same track, partial-file retries after a connection drop,
cancelled-then-redownloaded files, the same track surfacing in
multiple synced playlists, etc.).

The file-finder code already RECOGNIZES the suffix when matching a
download to its source. But after the canonical file is moved into
the library, the leftover ``_<timestamp>`` siblings sat orphaned in
the downloads folder forever. ``cleanup_slskd_dedup_siblings`` runs
at the end of each successful import and prunes them.
"""

import os
from pathlib import Path

import pytest

from core.imports.file_ops import (
    _strip_slskd_dedup_suffix,
    cleanup_slskd_dedup_siblings,
)


# ---------------------------------------------------------------------------
# Suffix-strip primitive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stem,expected",
    [
        ("Song", "Song"),
        ("Song_639067852665564677", "Song"),                  # 18 digits → match
        ("Song_6390678526655646777", "Song"),                 # 19 digits → match
        ("Song_63906785266556467770", "Song"),                # 20 digits → match
        ("Song_12345", "Song_12345"),                          # short → leave alone
        ("Track 5", "Track 5"),                                # legitimate trailing digits → leave alone
        ("Album 1995", "Album 1995"),                          # year suffix → leave alone
        ("Mix_2024_639067852665564677", "Mix_2024"),           # only the slskd suffix is stripped
    ],
)
def test_strip_slskd_dedup_suffix(stem, expected) -> None:
    assert _strip_slskd_dedup_suffix(stem) == expected


# ---------------------------------------------------------------------------
# Orphan cleanup
# ---------------------------------------------------------------------------


def test_removes_orphan_siblings_after_canonical_imported(tmp_path: Path) -> None:
    """The reported scenario: canonical ``Song.flac`` was just imported
    (moved out of the downloads folder), and ``Song_<timestamp>.flac``
    siblings should be deleted."""
    canonical = tmp_path / "Song.flac"
    # Canonical file is GONE — caller invokes us after the move
    sibling_a = tmp_path / "Song_639067852665564677.flac"
    sibling_b = tmp_path / "Song_639067852665564999.flac"
    sibling_a.write_bytes(b"orphan a")
    sibling_b.write_bytes(b"orphan b")

    deleted = cleanup_slskd_dedup_siblings(canonical)

    assert len(deleted) == 2
    assert not sibling_a.exists()
    assert not sibling_b.exists()


def test_does_not_touch_files_with_different_canonical_stem(tmp_path: Path) -> None:
    """A sibling that strips down to a DIFFERENT canonical stem belongs
    to a different track and must not be deleted."""
    canonical = tmp_path / "Song.flac"
    other_track = tmp_path / "OtherSong_639067852665564677.flac"
    other_track.write_bytes(b"different track")

    deleted = cleanup_slskd_dedup_siblings(canonical)
    assert deleted == []
    assert other_track.exists()


def test_does_not_touch_files_with_different_extension(tmp_path: Path) -> None:
    """Same canonical stem but different extension is a different
    file (e.g. an .mp3 next to a .flac). Don't cross-delete."""
    canonical = tmp_path / "Song.flac"
    different_ext = tmp_path / "Song_639067852665564677.mp3"
    different_ext.write_bytes(b"different format")

    deleted = cleanup_slskd_dedup_siblings(canonical)
    assert deleted == []
    assert different_ext.exists()


def test_does_not_touch_files_without_dedup_suffix(tmp_path: Path) -> None:
    """A neighbouring file that happens to share the canonical stem
    but doesn't have a slskd dedup suffix is a legitimate user file —
    leave it alone, even though stripping it would match."""
    canonical = tmp_path / "Song.flac"
    legit = tmp_path / "Song.flac"  # If it existed, the canonical wouldn't be here
    # Actually use a different shape — a file that strips to the same
    # canonical stem but has no suffix at all
    legit = tmp_path / "Song.flac"  # Same as canonical name
    # We're running cleanup AFTER the move so the canonical itself is gone.
    # But guard against any case where it's still on disk for some reason.
    legit.write_bytes(b"still here")

    deleted = cleanup_slskd_dedup_siblings(canonical)
    assert deleted == []
    assert legit.exists()


def test_handles_canonical_file_that_itself_had_suffix(tmp_path: Path) -> None:
    """If the imported file ITSELF had a slskd dedup suffix (because
    slskd renamed our preferred copy when an earlier download landed
    first), the cleanup must still find sibling orphans by stripping
    suffixes on both sides for comparison."""
    canonical = tmp_path / "Song_639000000000000000.flac"  # The one we imported
    other = tmp_path / "Song_639067852665564677.flac"
    other.write_bytes(b"other orphan")

    deleted = cleanup_slskd_dedup_siblings(canonical)
    assert len(deleted) == 1
    assert not other.exists()


def test_returns_empty_list_when_directory_does_not_exist(tmp_path: Path) -> None:
    """Defensive: caller passes a path whose parent dir was already
    cleaned up (e.g. another worker pruned the empty folder). Must
    not raise."""
    missing = tmp_path / "no" / "such" / "dir" / "Song.flac"
    deleted = cleanup_slskd_dedup_siblings(missing)
    assert deleted == []


def test_returns_empty_list_when_no_orphans_exist(tmp_path: Path) -> None:
    """The common case after most imports: nothing to clean up. Must
    return [] without errors."""
    canonical = tmp_path / "Song.flac"
    # Pre-existing unrelated files in the same directory
    (tmp_path / "TotallyDifferent.flac").write_bytes(b"unrelated")
    (tmp_path / "AnotherTrack.mp3").write_bytes(b"unrelated")

    deleted = cleanup_slskd_dedup_siblings(canonical)
    assert deleted == []


def test_skips_subdirectories(tmp_path: Path) -> None:
    """A subdirectory whose name happens to match the dedup pattern
    must not be deleted — only files."""
    canonical = tmp_path / "Song.flac"
    subdir = tmp_path / "Song_639067852665564677.flac"
    subdir.mkdir()  # Subdirectory matching the orphan filename pattern

    deleted = cleanup_slskd_dedup_siblings(canonical)
    assert deleted == []
    assert subdir.exists()
    assert subdir.is_dir()


def test_continues_after_individual_unlink_failure(tmp_path: Path, monkeypatch) -> None:
    """A locked file must not block cleanup of the rest. Replace
    Path.unlink with a function that fails on a specific path and
    succeeds otherwise."""
    canonical = tmp_path / "Song.flac"
    locked = tmp_path / "Song_639067852665564677.flac"
    cleanable = tmp_path / "Song_639067852665564999.flac"
    locked.write_bytes(b"locked")
    cleanable.write_bytes(b"cleanable")

    real_unlink = Path.unlink

    def fake_unlink(self):
        if self.name == "Song_639067852665564677.flac":
            raise PermissionError("locked")
        return real_unlink(self)

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    deleted = cleanup_slskd_dedup_siblings(canonical)

    assert len(deleted) == 1
    assert "Song_639067852665564999.flac" in deleted[0]
    # The locked one stays
    assert locked.exists()
