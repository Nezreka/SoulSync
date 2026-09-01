"""#1215 — "Enhance Quality not actually enhancing".

The reporter's library is scanned on Windows (paths recorded as
``\\\\storage3\\Media\\Music\\...``) while SoulSync runs in Docker on Linux, with
the same share mounted at ``/app/Transfer``. Every upgrade downloaded fine and
then died on the move:

    Resolved path: '/app/Transfer/In This Moment/Beautiful Tragedy/\\\\storage3\\Media\\...\\01 Whispers of October.mp3'
    OSError: [Errno 22] Invalid argument   (core/imports/pipeline.py, safe_move_file)

The destination folder was RIGHT - the resolver mapped the recorded Windows
path onto the mount correctly. The filename was the whole recorded path,
because the builder took the folder from the RESOLVED path and then rebuilt the
name from the RAW one with posix ``basename``, which does not split
backslashes.

The blast radius is the whole feature, not one file: post-processing aborts, so
the wishlist entry never clears, the download sits in the download folder, and
the retry loop goes back out for another copy - the "keeps downloading from
other users" in the report. On a non-network filesystem backslashes are legal,
so the same bug silently writes a junk-named file instead of erroring.
"""

from __future__ import annotations

import os

import pytest

from core.imports import paths as paths_mod
from core.imports.paths import _reachable_original_file


WINDOWS_PATH = r"\\storage3\Media\Music\In This Moment\Beautiful Tragedy\01 Whispers of October.mp3"


class _StubConfig:
    def __init__(self, root: str):
        self._root = root

    def get(self, key, default=None):
        if key == "soulseek.transfer_path":
            return os.path.join(self._root, "Transfer")
        if key == "soulseek.download_path":
            return os.path.join(self._root, "downloads")
        if key == "library.music_paths":
            return []
        return default

    def get_active_media_server(self):
        return None


@pytest.fixture()
def mounted_album(tmp_path, monkeypatch):
    """The Windows share as this process sees it: mounted, with the real file."""
    album = tmp_path / "Transfer" / "In This Moment" / "Beautiful Tragedy"
    album.mkdir(parents=True)
    original = album / "01 Whispers of October.mp3"
    original.write_bytes(b"x")
    monkeypatch.setattr(
        paths_mod, "_get_config_manager", lambda: _StubConfig(str(tmp_path)))
    # What the real resolver does with a recorded windows path: normalize the
    # separators and find the file under a configured root.
    monkeypatch.setattr(
        "core.library.path_resolver.resolve_library_file_path",
        lambda p, **kw: str(original),
    )
    return original


def test_a_windows_recorded_path_resolves_to_the_real_file(mounted_album):
    """Pre-fix this returned only the folder, and the caller rebuilt the name
    from the raw windows string."""
    resolved = _reachable_original_file(WINDOWS_PATH)
    assert resolved == str(mounted_album)


def test_the_upgrade_destination_is_a_filename_not_a_pasted_path(mounted_album):
    """The bug, stated as the thing that broke: the destination's basename has
    to be a FILE NAME. A recorded path must never end up inside one."""
    resolved = _reachable_original_file(WINDOWS_PATH)
    destination = os.path.splitext(resolved)[0] + ".flac"

    assert os.path.basename(destination) == "01 Whispers of October.flac"
    assert "\\" not in os.path.basename(destination)
    assert os.path.dirname(destination) == str(mounted_album.parent)
    # The exact string from the report, spelled out so a regression is obvious.
    assert not destination.endswith(
        r"Beautiful Tragedy/\\storage3\Media\Music\In This Moment"
        r"\Beautiful Tragedy\01 Whispers of October.flac")


def test_posix_basename_on_a_windows_path_is_why_this_happened():
    """Pins the mechanism. If this ever stops being true the guard above can be
    relaxed; while it IS true, splitting a recorded path here is a bug."""
    assert os.path.basename(WINDOWS_PATH) == WINDOWS_PATH
    assert os.path.dirname(WINDOWS_PATH) == ""


def test_the_upgrade_replaces_the_existing_file_in_place(mounted_album, monkeypatch):
    """End to end through the real builder: same folder, same stem, new
    extension - the upgrade lands ON the file it supersedes."""
    monkeypatch.setattr(paths_mod, "_get_album_tracks_for_source", lambda *a: None)
    context = {
        "artist": {"name": "In This Moment"},
        "album": {"name": "Beautiful Tragedy", "id": "a1", "release_date": "2007-01-01",
                  "total_tracks": 11, "album_type": "album",
                  "artists": [{"name": "In This Moment"}]},
        "track_info": {
            "name": "Whispers of October", "id": "t1", "track_number": 1, "disc_number": 1,
            "artists": [{"name": "In This Moment"}],
            "source_info": {"enhance": True, "original_file_path": WINDOWS_PATH},
        },
        "original_search_result": {
            "title": "Whispers of October", "clean_title": "Whispers of October",
            "clean_album": "Beautiful Tragedy", "clean_artist": "In This Moment",
            "artists": [{"name": "In This Moment"}]},
        "source": "deezer", "is_album_download": False,
        "_no_album_folder_reuse": True,
    }

    final_path, created = paths_mod.build_final_path_for_track(
        context, {"name": "In This Moment"},
        {"is_album": True, "album_name": "Beautiful Tragedy",
         "track_number": 1, "disc_number": 1},
        ".flac",
    )

    assert created is True
    assert final_path == str(mounted_album.with_suffix(".flac"))
