"""deleting a song takes its lyrics and an emptied album folder with it, as in 3.6.1.

Library v2's delete moved the audio to the deleted-files folder and left the
song's .lrc / .txt and the empty album folder behind.
"""

from __future__ import annotations

import os

from core.library2.file_delete import _clean_after_delete


def _moved(into):
    def unlink(path):
        into.append(os.path.basename(path))
        os.unlink(path)
    return unlink


def test_the_last_song_takes_its_lyrics_and_the_folder(tmp_path):
    album = tmp_path / "music" / "Artist" / "Album"
    album.mkdir(parents=True)
    (album / "01 - Song.lrc").write_text("[00:01] la")
    (album / "01 - Song.txt").write_text("la")
    moved = []
    _clean_after_delete(str(album / "01 - Song.flac"), _moved(moved), [str(tmp_path / "music")])
    assert sorted(moved) == ["01 - Song.lrc", "01 - Song.txt"]
    assert not album.exists()
    assert (tmp_path / "music" / "Artist").exists()  # only the emptied folder goes


def test_lyrics_another_copy_still_uses_stay(tmp_path):
    album = tmp_path / "Album"
    album.mkdir()
    (album / "01 - Song.mp3").write_bytes(b"mp3")   # the other copy of the song
    (album / "01 - Song.lrc").write_text("[00:01] la")
    moved = []
    _clean_after_delete(str(album / "01 - Song.flac"), _moved(moved), [])
    assert moved == []
    assert (album / "01 - Song.lrc").exists() and album.exists()


def test_a_library_root_is_never_removed(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    _clean_after_delete(str(root / "loose.flac"), _moved([]), [str(root)])
    assert root.exists()
