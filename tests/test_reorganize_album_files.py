"""Rename-only reorganize carries the album's own files and undoes cleanly.

A rename moved the tracks and left cover.jpg, the cue sheet and the playlist in
the old folder: the new folder had no cover and the old one never emptied, so
it was never pruned.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

from core.library_reorganize import reorganize_album_rename_only


def _album(tmp_path, *, extras=("cover.jpg", "album.cue", "album.m3u"), keep_audio=False):
    old = tmp_path / "music" / "Artist" / "Old Name"
    new = tmp_path / "music" / "Artist" / "New Name (2001)"
    old.mkdir(parents=True)
    tracks = []
    for n in (1, 2):
        src = old / f"0{n}.flac"
        src.write_bytes(b"audio")
        tracks.append({"track_id": f"t{n}", "title": f"Song {n}", "current_path_abs": str(src),
                       "new_path_abs": str(new / f"0{n} - Song {n}.flac"), "matched": True,
                       "unchanged": False, "collision": False, "file_exists": True})
    for name in extras:
        (old / name).write_bytes(b"extra")
    if keep_audio:
        (old / "bonus.flac").write_bytes(b"audio")
    return old, new, {"success": True, "status": "planned", "source": "deezer", "tracks": tracks}


def _run(tmp_path, preview, **kw):
    return reorganize_album_rename_only(
        album_id="a1", db=MagicMock(), transfer_dir=str(tmp_path / "music"),
        resolve_file_path_fn=lambda p: p, build_final_path_fn=lambda *a, **k: (None, True),
        update_track_path_fn=kw.pop("update", MagicMock()), preview_fn=lambda **_: preview, **kw)


def test_cover_cue_and_playlist_follow_the_album_and_the_old_folder_goes(tmp_path):
    old, new, preview = _album(tmp_path)

    assert _run(tmp_path, preview)["moved"] == 2

    assert sorted(os.listdir(new)) == sorted(
        ["01 - Song 1.flac", "02 - Song 2.flac", "cover.jpg", "album.cue", "album.m3u"])
    assert not old.exists()


def test_a_cover_already_at_the_destination_is_never_overwritten(tmp_path):
    old, new, preview = _album(tmp_path, extras=("cover.jpg",))
    new.mkdir(parents=True)
    (new / "cover.jpg").write_bytes(b"theirs")

    _run(tmp_path, preview)

    assert (new / "cover.jpg").read_bytes() == b"theirs"
    assert (old / "cover.jpg").exists()


def test_a_folder_that_still_holds_other_tracks_keeps_its_cover(tmp_path):
    old, new, preview = _album(tmp_path, extras=("cover.jpg",), keep_audio=True)

    _run(tmp_path, preview)

    assert (old / "cover.jpg").exists() and (old / "bonus.flac").exists()
    assert not (new / "cover.jpg").exists()


def test_a_failed_catalogue_update_puts_the_lyrics_back_with_the_track(tmp_path):
    old, new, preview = _album(tmp_path, extras=())
    (old / "01.lrc").write_text("[00:01] la")
    preview["tracks"] = preview["tracks"][:1]

    def fail(*_a, **_k):
        raise RuntimeError("database is locked")

    summary = _run(tmp_path, preview, update=fail)

    assert summary["failed"] == 1
    assert (old / "01.flac").exists() and (old / "01.lrc").exists()
    assert not (new / "01 - Song 1.lrc").exists()
