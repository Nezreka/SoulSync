"""Playlist materialization seam — a playlist folder is a derived view of links
into the real library. Locks down: symlink vs copy, the auto-fallback when
symlinks aren't supported, idempotency, stale-link pruning, collision handling,
and that nothing is ever written outside the playlists root."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.conftest import requires_symlinks
from core.playlists.materialize import (
    DEFAULT_MODE,
    materialize_one,
    normalize_mode,
    playlist_dir_for,
    rebuild_playlist_folder,
)


def _library(tmp_path: Path) -> list[str]:
    a = tmp_path / "Music" / "Daft Punk" / "Discovery" / "One More Time.mp3"
    b = tmp_path / "Music" / "Queen" / "A Night at the Opera" / "Bohemian Rhapsody.mp3"
    for f in (a, b):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"audio")
    return [str(a), str(b)]


def test_normalize_mode():
    assert normalize_mode("symlink") == "symlink"
    assert normalize_mode("COPY") == "copy"
    assert normalize_mode("") == DEFAULT_MODE
    assert normalize_mode(None) == DEFAULT_MODE
    assert normalize_mode("nonsense") == DEFAULT_MODE


@requires_symlinks
def test_symlink_mode_creates_relative_links(tmp_path: Path):
    real = _library(tmp_path)
    s = rebuild_playlist_folder(str(tmp_path / "Playlists"), "Road Trip", real, mode="symlink")
    assert s.linked == 2 and s.copied == 0 and not s.fellback
    link = Path(s.playlist_dir) / "One More Time.mp3"
    assert link.is_symlink()
    assert not os.path.isabs(os.readlink(link))          # relative for portability
    assert link.resolve() == Path(real[0]).resolve()
    assert link.read_bytes() == b"audio"


@requires_symlinks
def test_symlink_mode_idempotent(tmp_path: Path):
    real = _library(tmp_path)
    root = str(tmp_path / "Playlists")
    rebuild_playlist_folder(root, "Mix", real, mode="symlink")
    s2 = rebuild_playlist_folder(root, "Mix", real, mode="symlink")
    assert s2.unchanged == 2 and s2.linked == 0 and s2.removed_stale == 0


def test_copy_mode_duplicates_real_files(tmp_path: Path):
    real = _library(tmp_path)
    s = rebuild_playlist_folder(str(tmp_path / "Playlists"), "USB", real, mode="copy")
    assert s.copied == 2 and s.linked == 0
    f = Path(s.playlist_dir) / "Bohemian Rhapsody.mp3"
    assert f.is_file() and not f.is_symlink() and f.read_bytes() == b"audio"


def test_copy_mode_idempotent(tmp_path: Path):
    real = _library(tmp_path)
    root = str(tmp_path / "Playlists")
    rebuild_playlist_folder(root, "Mix", real, mode="copy")
    s2 = rebuild_playlist_folder(root, "Mix", real, mode="copy")
    assert s2.unchanged == 2 and s2.copied == 0


def test_falls_back_to_copy_when_symlinks_unsupported(tmp_path: Path):
    real = _library(tmp_path)

    def _no_symlinks(target, link):
        raise OSError("symlinks not supported here")

    s = rebuild_playlist_folder(str(tmp_path / "Playlists"), "Car", real,
                                mode="symlink", symlink_fn=_no_symlinks)
    assert s.fellback is True and s.copied == 2 and s.linked == 0
    f = Path(s.playlist_dir) / "One More Time.mp3"
    assert f.is_file() and not f.is_symlink() and f.read_bytes() == b"audio"


def test_rebuild_prunes_entries_no_longer_in_playlist(tmp_path: Path):
    real = _library(tmp_path)
    root = str(tmp_path / "Playlists")
    rebuild_playlist_folder(root, "Mix", real, mode="symlink")        # 2 entries
    s = rebuild_playlist_folder(root, "Mix", real[:1], mode="symlink")  # drop one
    assert s.removed_stale == 1
    pdir = Path(s.playlist_dir)
    assert (pdir / "One More Time.mp3").exists()
    assert not (pdir / "Bohemian Rhapsody.mp3").exists()


def test_prune_stale_can_be_disabled(tmp_path: Path):
    real = _library(tmp_path)
    root = str(tmp_path / "Playlists")
    rebuild_playlist_folder(root, "Mix", real, mode="symlink")
    s = rebuild_playlist_folder(root, "Mix", real[:1], mode="symlink", prune_stale=False)
    assert s.removed_stale == 0
    assert (Path(s.playlist_dir) / "Bohemian Rhapsody.mp3").exists()


@requires_symlinks
def test_switching_mode_replaces_links_with_copies(tmp_path: Path):
    real = _library(tmp_path)
    root = str(tmp_path / "Playlists")
    rebuild_playlist_folder(root, "Mix", real, mode="symlink")
    s = rebuild_playlist_folder(root, "Mix", real, mode="copy")
    f = Path(s.playlist_dir) / "One More Time.mp3"
    assert f.is_file() and not f.is_symlink() and s.copied == 2


def test_basename_collision_is_disambiguated_not_overwritten(tmp_path: Path):
    p1 = tmp_path / "Music" / "A" / "AlbumA" / "01 - Intro.mp3"
    p2 = tmp_path / "Music" / "B" / "AlbumB" / "01 - Intro.mp3"
    for f, data in ((p1, b"one"), (p2, b"two")):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    s = rebuild_playlist_folder(str(tmp_path / "Playlists"), "Dup", [str(p1), str(p2)], mode="copy")
    pdir = Path(s.playlist_dir)
    assert (pdir / "01 - Intro.mp3").read_bytes() == b"one"
    assert (pdir / "01 - Intro (2).mp3").read_bytes() == b"two"   # second kept, not lost
    assert s.copied == 2


@requires_symlinks
def test_missing_source_is_counted_not_fatal(tmp_path: Path):
    real = _library(tmp_path)
    s = rebuild_playlist_folder(str(tmp_path / "Playlists"), "Mix",
                                real + [str(tmp_path / "gone.mp3")], mode="symlink")
    assert s.linked == 2 and s.missing_source == 1


def test_playlist_name_cannot_escape_root(tmp_path: Path):
    root = tmp_path / "Playlists"
    nasty = playlist_dir_for(str(root), "../../etc/evil")
    assert os.path.abspath(nasty).startswith(os.path.abspath(str(root)) + os.sep)


def test_materialize_one_missing_source(tmp_path: Path):
    dest = tmp_path / "Playlists" / "X" / "x.mp3"
    assert materialize_one(str(tmp_path / "nope.mp3"), str(dest), "symlink") == "missing"
    assert not dest.exists()


def test_copy_mode_recopies_when_source_changed(tmp_path: Path):
    """M15: copy mode must not report 'unchanged' for a stale copy — if the
    library file changed (retagged/replaced) at the same path, the existing
    copy is stale and must be re-copied."""
    src = tmp_path / "Music" / "song.mp3"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"ORIGINAL-TAGS-v1")

    dest = Path(rebuild_playlist_folder(
        str(tmp_path / "Playlists"), "USB", [str(src)], mode="copy").playlist_dir) / "song.mp3"
    assert dest.read_bytes() == b"ORIGINAL-TAGS-v1"

    # library file retagged at the same path (same name, new bytes)
    src.write_bytes(b"RETAGGED-v2-DIFFERENT-SIZE")

    s2 = rebuild_playlist_folder(str(tmp_path / "Playlists"), "USB", [str(src)], mode="copy")
    assert s2.copied == 1 and s2.unchanged == 0
    assert dest.read_bytes() == b"RETAGGED-v2-DIFFERENT-SIZE"


def test_copy_mode_still_idempotent_when_source_untouched(tmp_path: Path):
    """M15: an up-to-date copy must still be left alone (no churn)."""
    real = _library(tmp_path)
    root = str(tmp_path / "Playlists")
    rebuild_playlist_folder(root, "USB", real, mode="copy")
    s2 = rebuild_playlist_folder(root, "USB", real, mode="copy")
    assert s2.unchanged == 2 and s2.copied == 0


def _coarse_copy(src, dst):
    """shutil.copy2 on a filesystem that keeps whole-second timestamps
    (FAT keeps 2 s, WSL's drvfs and some network shares 1 s)"""
    import shutil
    shutil.copy2(src, dst)
    mtime = int(os.stat(src).st_mtime)
    os.utime(dst, (mtime, mtime))


def test_copy_mode_stays_idempotent_on_a_coarse_timestamp_filesystem(tmp_path: Path):
    """found on E: (drvfs): the exact-nanosecond check never held where the
    filesystem rounds the copied time, so every rebuild re-copied every file"""
    real = _library(tmp_path)
    for path in real:
        os.utime(path, ns=(1_700_000_000_123_456_789, 1_700_000_000_123_456_789))
    root = str(tmp_path / "Playlists")
    rebuild_playlist_folder(root, "Mix", real, mode="copy", copy_fn=_coarse_copy)
    s2 = rebuild_playlist_folder(root, "Mix", real, mode="copy", copy_fn=_coarse_copy)
    assert s2.unchanged == 2 and s2.copied == 0


def test_a_retagged_source_of_the_same_size_is_still_recopied(tmp_path: Path):
    real = _library(tmp_path)
    root = str(tmp_path / "Playlists")
    rebuild_playlist_folder(root, "Mix", real, mode="copy")
    later = os.stat(real[0]).st_mtime + 60  # a retag rewrites the file a minute on
    os.utime(real[0], (later, later))
    s2 = rebuild_playlist_folder(root, "Mix", real, mode="copy")
    assert s2.copied == 1 and s2.unchanged == 1


@requires_symlinks
def test_a_3_6_1_folder_is_relinked_and_pruned_on_its_first_rebuild(tmp_path: Path):
    """third #1062 review G13-1: entries 3.6.1 wrote have no manifest record.
    a link to a moved file failed every rebuild ("unmanaged destination") and
    a link for a song that left the playlist stayed for good; 3.6.1 relinked
    and pruned them"""
    real = _library(tmp_path)
    root = str(tmp_path / "Playlists")
    folder = Path(root) / "Old Mix"
    folder.mkdir(parents=True)
    name = os.path.basename(real[0])
    # 3.6.1's link to where the file used to be, and a link for a dropped song
    os.symlink(str(tmp_path / "gone" / name), folder / name)
    os.symlink(real[1], folder / "dropped.flac")
    (folder / "notes.txt").write_text("mine")

    s = rebuild_playlist_folder(root, "Old Mix", [real[0]], mode="symlink")

    assert s.failed == 0, s.errors
    assert os.path.realpath(folder / name) == os.path.realpath(real[0])
    assert not os.path.lexists(folder / "dropped.flac")
    assert (folder / "notes.txt").read_text() == "mine"   # never the user's own file


@requires_symlinks
def test_a_users_own_link_in_a_3_6_1_folder_is_never_adopted(tmp_path: Path):
    """review 4 R4-D-8: every symlink was adopted and pruned, the user's own
    link to a cover or another folder included"""
    real = _library(tmp_path)
    root = str(tmp_path / "Playlists")
    folder = Path(root) / "Old Mix"
    folder.mkdir(parents=True)
    cover = tmp_path / "art" / "cover.jpg"
    cover.parent.mkdir()
    cover.write_bytes(b"jpg")
    os.symlink(str(cover), folder / "cover.jpg")
    os.symlink(str(tmp_path / "art"), folder / "Artwork")
    os.symlink(real[1], folder / "dropped.flac")      # 3.6.1's, still pruned

    s = rebuild_playlist_folder(root, "Old Mix", [real[0]], mode="symlink")

    assert s.failed == 0, s.errors
    assert os.path.islink(folder / "cover.jpg") and os.path.islink(folder / "Artwork")
    assert not os.path.lexists(folder / "dropped.flac")


def test_a_3_6_1_copy_is_adopted_only_when_its_bytes_are_the_members(tmp_path: Path):
    """G13-1 safety: in copy mode a same-named file is adopted (and so may be
    replaced or pruned later) only when it is byte for byte the member. a file
    with other bytes may be the user's only copy: left alone, reported"""
    from core.playlists.materialize import rebuild_playlist_folder
    music = tmp_path / "Music"
    music.mkdir()
    ours, theirs = music / "ours.flac", music / "theirs.flac"
    ours.write_bytes(b"our audio")
    theirs.write_bytes(b"new audio")
    folder = tmp_path / "Playlists" / "Mix"
    folder.mkdir(parents=True)
    (folder / "ours.flac").write_bytes(b"our audio")           # 3.6.1's copy
    (folder / "theirs.flac").write_bytes(b"user's own master")  # not ours
    summary = rebuild_playlist_folder(str(tmp_path / "Playlists"), "Mix",
                                      [str(ours), str(theirs)], "copy")
    assert (folder / "theirs.flac").read_bytes() == b"user's own master"
    assert summary.failed == 1
    # the adopted copy goes when its song leaves the playlist
    rebuild_playlist_folder(str(tmp_path / "Playlists"), "Mix", [str(theirs)], "copy")
    assert not (folder / "ours.flac").exists()
    assert (folder / "theirs.flac").read_bytes() == b"user's own master"
