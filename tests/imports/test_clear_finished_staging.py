"""clear_finished_staging: what "clear finished folders" may and may not delete."""

from __future__ import annotations

import pytest

from core.imports import file_ops
from core.imports.file_ops import clear_finished_staging


@pytest.fixture(autouse=True)
def no_configured_roots(monkeypatch):
    monkeypatch.setattr(file_ops, "protected_root_dirs", lambda: set())


@pytest.fixture
def staging(tmp_path):
    root = tmp_path / "Staging"
    root.mkdir()
    return root


def _album(root, artist="Artist", album="Album", files=("01.flac",), extras=("cover.jpg",)):
    folder = root / artist / album
    folder.mkdir(parents=True)
    for name in (*files, *extras):
        (folder / name).write_bytes(b"x")
    return folder


def test_takes_the_album_and_the_artist_folder(staging):
    album = _album(staging, extras=("cover.jpg", "rip.log", "album.cue"))
    clear_finished_staging(str(staging), [str(album / "01.flac")])
    assert not (staging / "Artist").exists()
    assert staging.is_dir()


def test_files_already_moved_still_let_the_folder_go(staging):
    album = _album(staging, files=())
    clear_finished_staging(str(staging), [str(album / "01.flac")])  # imported: gone already
    assert not album.exists()


def test_another_album_still_in_the_artist_folder_keeps_it(staging):
    album = _album(staging, album="Done")
    other = _album(staging, album="Not Yet", files=("01.mp3",))
    clear_finished_staging(str(staging), [str(album / "01.flac")])
    assert not album.exists()
    assert other.exists() and (staging / "Artist").exists()


def test_music_nobody_asked_about_keeps_the_folder(staging):
    album = _album(staging, files=("01.flac", "02.flac"))
    clear_finished_staging(str(staging), [str(album / "01.flac")])
    assert not (album / "01.flac").exists()
    assert (album / "02.flac").exists()


def test_anything_that_isnt_release_clutter_keeps_the_folder(staging):
    album = _album(staging, extras=("cover.jpg", "interview.mkv"))
    clear_finished_staging(str(staging), [str(album / "01.flac")])
    assert (album / "interview.mkv").exists()


def test_never_touches_anything_outside_staging(staging, tmp_path):
    outside = tmp_path / "Music" / "01.flac"
    outside.parent.mkdir()
    outside.write_bytes(b"library")
    removed = clear_finished_staging(str(staging), [str(outside)])
    assert removed == [] and outside.exists()


def test_loose_files_in_the_root_never_take_the_root(staging):
    (staging / "single.flac").write_bytes(b"x")
    clear_finished_staging(str(staging), [str(staging / "single.flac")])
    assert staging.is_dir() and not (staging / "single.flac").exists()


def test_a_configured_root_nested_in_staging_survives(staging, monkeypatch):
    nested = staging / "Downloads"
    album = _album(nested)
    monkeypatch.setattr(file_ops, "protected_root_dirs", lambda: {str(nested)})
    clear_finished_staging(str(staging), [str(album / "01.flac")])
    assert nested.is_dir() and not (nested / "Artist").exists()
