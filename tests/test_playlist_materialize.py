"""Playlist materialization seam — a playlist folder is a derived view of links
into the real library. Locks down: symlink vs copy, the auto-fallback when
symlinks aren't supported, idempotency, stale-link pruning, collision handling,
and that nothing is ever written outside the playlists root."""

from __future__ import annotations

import os
from pathlib import Path

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


def test_symlink_mode_creates_relative_links(tmp_path: Path):
    real = _library(tmp_path)
    s = rebuild_playlist_folder(str(tmp_path / "Playlists"), "Road Trip", real, mode="symlink")
    assert s.linked == 2 and s.copied == 0 and not s.fellback
    link = Path(s.playlist_dir) / "One More Time.mp3"
    assert link.is_symlink()
    assert not os.path.isabs(os.readlink(link))          # relative for portability
    assert link.resolve() == Path(real[0]).resolve()
    assert link.read_bytes() == b"audio"


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
