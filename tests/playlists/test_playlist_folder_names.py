"""organize-by-playlist folders keep the playlist's name and their 3.6.1 contents.

Library v2 named every playlist folder `profile-<owner>-<source>-<hash>`. a
media server's folder view showed those, and every 3.6.1 folder stayed beside
its hashed twin, so a server pointed at ./Playlists saw every song twice. the
folder is the playlist's name again; the stable identity lives in the
manifest, and 3.6.1's entries are taken over when they are exactly what a
rebuild would write.
"""

from __future__ import annotations

import os

from core.playlists.managed_artifacts import load_manifest
from core.playlists.materialize import rebuild_playlist_folder


def _audio(path, data=b"audio"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def _identity(ref, name="Mix"):
    return {"profile_id": 1, "source": "spotify", "identity_kind": "mirror",
            "identity_ref": str(ref), "playlist_name": name, "identity_digest": f"{ref:0>6}abcdef"}


def test_a_361_folder_is_taken_over_and_managed(tmp_path):
    one = _audio(tmp_path / "Music" / "one.flac")
    two = _audio(tmp_path / "Music" / "two.flac")
    root = tmp_path / "Playlists"
    legacy = root / "Mix"
    legacy.mkdir(parents=True)
    for src in (one, two):  # what 3.6.1 left: relative links, no manifest
        os.symlink(os.path.relpath(src, legacy), legacy / os.path.basename(src))

    summary = rebuild_playlist_folder(str(root), "Mix", [one, two], "symlink",
                                      playlist_identity=_identity(7))
    assert summary.playlist_dir == str(legacy)
    assert summary.failed == 0 and summary.unchanged == 2
    assert set(load_manifest(str(root), str(legacy))["entries"]) == {"one.flac", "two.flac"}
    assert sorted(os.listdir(root)) == [".soulsync-playlist-manifests", "Mix"]  # no twin

    # managed now: a song leaving the playlist leaves the folder
    summary = rebuild_playlist_folder(str(root), "Mix", [one], "symlink",
                                      playlist_identity=_identity(7))
    assert summary.removed_stale == 1
    assert os.listdir(legacy) == ["one.flac"]


def test_a_hashed_folder_from_before_takes_the_playlists_name(tmp_path):
    one = _audio(tmp_path / "Music" / "one.flac")
    root = tmp_path / "Playlists"
    hashed = rebuild_playlist_folder(str(root), "profile-1-spotify-0000007abcdef", [one],
                                     "symlink", playlist_identity=_identity(7))
    assert os.path.basename(hashed.playlist_dir).startswith("profile-")
    again = rebuild_playlist_folder(str(root), "Mix", [one], "symlink",
                                    playlist_identity=_identity(7))
    assert again.playlist_dir == str(root / "Mix")
    assert not os.path.exists(hashed.playlist_dir)
    assert again.failed == 0 and again.unchanged == 1


def test_two_playlists_with_one_name_get_two_folders(tmp_path):
    one = _audio(tmp_path / "Music" / "one.flac")
    two = _audio(tmp_path / "Music" / "two.flac")
    root = tmp_path / "Playlists"
    first = rebuild_playlist_folder(str(root), "Mix", [one], "symlink", playlist_identity=_identity(7))
    second = rebuild_playlist_folder(str(root), "Mix", [two], "symlink", playlist_identity=_identity(8))
    assert first.playlist_dir == str(root / "Mix")
    assert second.playlist_dir != first.playlist_dir
    assert os.path.basename(second.playlist_dir).startswith("Mix (")
    # and each keeps its own folder on the next rebuild
    assert rebuild_playlist_folder(str(root), "Mix", [one], "symlink",
                                   playlist_identity=_identity(7)).playlist_dir == first.playlist_dir
    assert rebuild_playlist_folder(str(root), "Mix", [two], "symlink",
                                   playlist_identity=_identity(8)).playlist_dir == second.playlist_dir
