"""Storm mounts one music folder twice, as /app/Transfer (the output folder)
and /music (what navidrome reports). a reorganize preview showed
'/music/x/y/z.mp3' beside 'x/y/z.mp3', one file under two names."""

import os

import pytest

from core.library_reorganize import _trim_to_transfer


@pytest.fixture
def mounts(tmp_path):
    transfer = tmp_path / "Transfer"
    track = transfer / "Artist" / "Album" / "01 - Song.mp3"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"x")
    music = tmp_path / "music"
    try:
        os.symlink(transfer, music, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available here")
    return transfer, music


def test_a_file_seen_through_the_second_mount_shows_inside_the_output_folder(mounts):
    transfer, music = mounts
    via_music = str(music / "Artist" / "Album" / "01 - Song.mp3")
    shown = _trim_to_transfer(via_music, via_music, str(transfer))
    assert shown.replace("\\\\", "/") == "Artist/Album/01 - Song.mp3"


def test_a_file_really_outside_the_output_folder_still_shows_whole(mounts, tmp_path):
    transfer, _ = mounts
    other = tmp_path / "elsewhere" / "Artist" / "Album" / "01 - Song.mp3"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"y")
    assert _trim_to_transfer(str(other), str(other), str(transfer)) == str(other)
