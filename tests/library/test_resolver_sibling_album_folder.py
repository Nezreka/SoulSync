"""#1127 — "Tools don't respect custom file organization templates".

The reporter uses `$albumartist/$albumartist - $album/$track - $title`. Files
are written correctly and the Library tab reads them fine, but Dead File Cleaner
reported every file at an "unreachable path", Album Completeness refused to fix
anything, and the error named a DB path that didn't match disk:

    Example DB-recorded path: Beck/Guero/01-01 - E-Pro.flac
    (the file is really at    Beck/Beck - Guero/01-01 - E-Pro.flac)

Note what differs: the artist folder matches, the filename matches exactly, and
only the ALBUM folder is wrong. That's Navidrome's Subsonic API, which reports a
song's `path` synthesized as `<AlbumArtist>/<Album>/<filename>` instead of the
true relative path — so the stored path is wrong for anyone whose album folders
aren't named exactly `<Album>`.

`resolve_library_file_path` walked progressively shorter SUFFIXES, which can
only repair a wrong prefix — a wrong interior segment is unreachable no matter
how many leading parts you drop. Hence the sibling-album fallback.
"""

from __future__ import annotations

import os

import pytest

from core.library.path_resolver import resolve_library_file_path


class _Config:
    def __init__(self, music_root: str):
        self._music_root = music_root

    def get(self, key, default=None):
        if key == "library.music_paths":
            return [self._music_root]
        return default


@pytest.fixture()
def library(tmp_path):
    """The reporter's layout: $albumartist/$albumartist - $album/<file>."""
    root = tmp_path / "music"
    album = root / "Beck" / "Beck - Guero"
    album.mkdir(parents=True)
    track = album / "01-01 - E-Pro.flac"
    track.write_bytes(b"audio")
    return root, track


def test_resolves_when_only_the_album_folder_differs(library):
    """The exact #1127 case."""
    root, track = library
    resolved = resolve_library_file_path(
        "Beck/Guero/01-01 - E-Pro.flac", config_manager=_Config(str(root)))
    assert resolved == str(track)


def test_resolves_an_absolute_media_server_path_too(library):
    """Navidrome/Plex may report an absolute path under their own root."""
    root, track = library
    resolved = resolve_library_file_path(
        "/music/Beck/Guero/01-01 - E-Pro.flac", config_manager=_Config(str(root)))
    assert resolved == str(track)


def test_refuses_to_guess_when_two_albums_hold_the_same_filename(library):
    """Dead File Cleaner DELETES what this resolves. An ambiguous match must
    fail closed rather than pick the wrong album."""
    root, _ = library
    other = root / "Beck" / "Beck - Odelay"
    other.mkdir(parents=True)
    (other / "01-01 - E-Pro.flac").write_bytes(b"audio")

    assert resolve_library_file_path(
        "Beck/Guero/01-01 - E-Pro.flac", config_manager=_Config(str(root))) is None


def test_same_album_reachable_from_two_base_dirs_still_resolves(tmp_path):
    """Ambiguity means two different ALBUMS, not two paths to one album.

    A library is routinely reachable through several configured base dirs — a
    transfer path plus a music path, duplicate mounts, a symlink. Each yields
    the same album folder NAME. Comparing full paths treated that as a conflict
    and refused a perfectly resolvable file.
    """
    for mount in ("mountA", "mountB"):
        album = tmp_path / mount / "Beck" / "Beck - Guero"
        album.mkdir(parents=True)
        (album / "01-01 - E-Pro.flac").write_bytes(b"audio")

    class _TwoMounts:
        def get(self, key, default=None):
            if key == "library.music_paths":
                return [str(tmp_path / "mountA"), str(tmp_path / "mountB")]
            return default

    resolved = resolve_library_file_path(
        "Beck/Guero/01-01 - E-Pro.flac", config_manager=_TwoMounts())
    assert resolved is not None
    assert resolved.endswith(os.path.join("Beck - Guero", "01-01 - E-Pro.flac"))


def test_two_different_album_folders_are_still_refused_across_mounts(tmp_path):
    """The safety rule survives: genuinely different album names still refuse,
    even when they come from different base dirs."""
    (tmp_path / "mountA" / "Beck" / "Beck - Guero").mkdir(parents=True)
    (tmp_path / "mountA" / "Beck" / "Beck - Guero" / "01 - Intro.flac").write_bytes(b"a")
    (tmp_path / "mountB" / "Beck" / "Beck - Odelay").mkdir(parents=True)
    (tmp_path / "mountB" / "Beck" / "Beck - Odelay" / "01 - Intro.flac").write_bytes(b"a")

    class _TwoMounts:
        def get(self, key, default=None):
            if key == "library.music_paths":
                return [str(tmp_path / "mountA"), str(tmp_path / "mountB")]
            return default

    assert resolve_library_file_path(
        "Beck/Guero/01 - Intro.flac", config_manager=_TwoMounts()) is None


def test_does_not_reach_into_a_different_artist(library):
    """Only album folders UNDER THE REPORTED ARTIST are considered."""
    root, _ = library
    other_artist = root / "Blur" / "Blur - Parklife"
    other_artist.mkdir(parents=True)
    (other_artist / "99 - Only Here.flac").write_bytes(b"audio")

    assert resolve_library_file_path(
        "Beck/Guero/99 - Only Here.flac", config_manager=_Config(str(root))) is None


def test_missing_file_still_returns_none(library):
    root, _ = library
    assert resolve_library_file_path(
        "Beck/Guero/does-not-exist.flac", config_manager=_Config(str(root))) is None


def test_unknown_artist_folder_returns_none(library):
    root, _ = library
    assert resolve_library_file_path(
        "Nobody/Whatever/01-01 - E-Pro.flac", config_manager=_Config(str(root))) is None


def test_exact_path_still_wins_without_scanning(library, monkeypatch):
    """The happy path must not regress into a directory scan."""
    root, track = library

    def _boom(*a, **k):
        raise AssertionError("scandir called for an already-correct path")

    monkeypatch.setattr(os, "scandir", _boom)
    assert resolve_library_file_path(
        "Beck/Beck - Guero/01-01 - E-Pro.flac",
        config_manager=_Config(str(root))) == str(track)


def test_too_few_segments_is_a_no_op(library):
    """A bare filename carries no artist segment to scope the search to."""
    root, _ = library
    assert resolve_library_file_path(
        "01-01 - E-Pro.flac", config_manager=_Config(str(root))) is None


def test_loose_files_directly_under_the_artist_are_not_matched(tmp_path):
    """The fallback looks one level DOWN (album folders), not at the artist
    folder itself — a file sitting loose there is a different layout."""
    root = tmp_path / "music"
    (root / "Beck").mkdir(parents=True)
    (root / "Beck" / "01-01 - E-Pro.flac").write_bytes(b"audio")

    assert resolve_library_file_path(
        "Beck/Guero/01-01 - E-Pro.flac", config_manager=_Config(str(root))) is None
