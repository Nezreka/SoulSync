"""Rename-only reorganize carries the album's own files and undoes cleanly.

A rename moved the tracks and left cover.jpg and the cue sheet in the old folder: the new folder had no cover and the old one never emptied, so
it was never pruned.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

from core.library_reorganize import reorganize_album_rename_only


def _album(tmp_path, *, extras=("cover.jpg", "album.cue", "rip.log"), keep_audio=False):
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


def test_cover_cue_and_log_follow_the_album_and_the_old_folder_goes(tmp_path):
    old, new, preview = _album(tmp_path)

    assert _run(tmp_path, preview)["moved"] == 2

    assert sorted(os.listdir(new)) == sorted(
        ["01 - Song 1.flac", "02 - Song 2.flac", "cover.jpg", "album.cue", "rip.log"])
    assert not old.exists()


def test_a_playlist_stays_where_it_is(tmp_path):
    """an .m3u is a playlist the reorganize rewrites in place, not album art"""
    old, _new, preview = _album(tmp_path, extras=("cover.jpg", "Mix.m3u"))

    _run(tmp_path, preview)

    assert (old / "Mix.m3u").exists()


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


def _move(src, dst):
    return {"track_id": os.path.basename(src), "title": os.path.basename(src),
            "current_path_abs": str(src), "new_path_abs": str(dst), "matched": True,
            "unchanged": False, "collision": False, "file_exists": True}


def _preview(moves):
    return {"success": True, "status": "planned", "source": "deezer",
            "tracks": [_move(s, d) for s, d in moves]}


def test_plain_lyrics_go_with_their_track_and_notes_with_the_album(tmp_path):
    """the .txt soulsync writes for unsynced lyrics stayed behind, the moved
    track lost its lyrics and the old folder never emptied"""
    old, new, preview = _album(tmp_path, extras=("cover.jpg", "info.txt"))
    (old / "01.txt").write_text("plain lyrics")

    _run(tmp_path, preview)

    assert (new / "01 - Song 1.txt").read_text() == "plain lyrics"
    assert (new / "info.txt").exists()
    assert not old.exists()


def test_the_cover_above_disc_folders_follows_and_the_album_folder_goes(tmp_path):
    old = tmp_path / "music" / "Artist" / "Old"
    new = tmp_path / "music" / "Artist" / "New"
    moves = []
    for disc in (1, 2):
        (old / f"Disc {disc}").mkdir(parents=True)
        src = old / f"Disc {disc}" / "01.flac"
        src.write_bytes(b"audio")
        moves.append((src, new / f"Disc {disc}" / "01 - Song.flac"))
    (old / "cover.jpg").write_bytes(b"art")
    (old / "album.cue").write_bytes(b"cue")

    _run(tmp_path, _preview(moves))

    assert (new / "cover.jpg").exists() and (new / "album.cue").exists()
    assert not old.exists()


def test_a_flat_album_filed_into_disc_folders_keeps_its_cover(tmp_path):
    old = tmp_path / "music" / "Artist" / "Old"
    new = tmp_path / "music" / "Artist" / "New"
    old.mkdir(parents=True)
    moves = []
    for disc in (1, 2):
        src = old / f"{disc}-01.flac"
        src.write_bytes(b"audio")
        moves.append((src, new / f"Disc {disc}" / "01 - Song.flac"))
    (old / "cover.jpg").write_bytes(b"art")

    _run(tmp_path, _preview(moves))

    assert (new / "cover.jpg").exists()
    assert not old.exists()


def test_os_caches_dont_keep_an_emptied_folder(tmp_path):
    old, new, preview = _album(tmp_path, extras=("Thumbs.db", "desktop.ini", ".DS_Store"))

    _run(tmp_path, preview)

    assert not old.exists()
    assert sorted(os.listdir(new)) == ["01 - Song 1.flac", "02 - Song 2.flac"]


def test_a_wavpack_track_left_behind_keeps_its_cover(tmp_path):
    """a second, shorter audio list replaced the first, so a .wv / .ape / .dsf
    left behind didn't count and the cover was carried off without it"""
    old, new, preview = _album(tmp_path, extras=("cover.jpg",))
    (old / "bonus.wv").write_bytes(b"audio")

    _run(tmp_path, preview)

    assert (old / "cover.jpg").exists()


def _artist_tree(tmp_path, monkeypatch, *, other_album=False):
    music = tmp_path / "music"
    monkeypatch.setattr("core.library2.file_delete._library_roots",
                        lambda *a, **k: [os.path.realpath(str(music))])
    old_album = music / "Old Artist" / "Album"
    old_album.mkdir(parents=True)
    src = old_album / "01.flac"
    src.write_bytes(b"audio")
    (music / "Old Artist" / "artist.jpg").write_bytes(b"photo")
    (music / "Old Artist" / "artist.nfo").write_text("<artist/>")
    if other_album:
        (music / "Old Artist" / "Other Album").mkdir()
        (music / "Old Artist" / "Other Album" / "01.flac").write_bytes(b"audio")
    new_album = music / "New Artist" / "Album"
    return music, _preview([(src, new_album / "01 - Song.flac")])


def test_the_old_artist_folder_follows_its_last_album(tmp_path, monkeypatch):
    music, preview = _artist_tree(tmp_path, monkeypatch)

    _run(tmp_path, preview)

    assert not (music / "Old Artist").exists()
    assert (music / "New Artist" / "artist.jpg").exists()
    assert (music / "New Artist" / "artist.nfo").exists()


def test_an_artist_folder_with_another_album_stays(tmp_path, monkeypatch):
    music, preview = _artist_tree(tmp_path, monkeypatch, other_album=True)

    _run(tmp_path, preview)

    assert (music / "Old Artist" / "artist.jpg").exists()
    assert (music / "Old Artist" / "Other Album" / "01.flac").exists()


def test_a_library_root_is_never_removed(tmp_path, monkeypatch):
    """albums straight in the root: the folder above the album is the root"""
    music = tmp_path / "music"
    monkeypatch.setattr("core.library2.file_delete._library_roots",
                        lambda *a, **k: [os.path.realpath(str(music))])
    (music / "Old").mkdir(parents=True)
    src = music / "Old" / "01.flac"
    src.write_bytes(b"audio")

    _run(tmp_path, _preview([(src, music / "New" / "01 - Song.flac")]))

    assert music.is_dir() and (music / "New" / "01 - Song.flac").exists()


def test_no_known_root_means_the_artist_folder_stays(tmp_path, monkeypatch):
    music, preview = _artist_tree(tmp_path, monkeypatch)
    monkeypatch.setattr("core.library2.file_delete._library_roots", lambda *a, **k: [])

    _run(tmp_path, preview)

    assert (music / "Old Artist" / "artist.jpg").exists()
