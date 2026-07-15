"""Imports must carry a track's .lrc lyrics sidecar along (lilbob5769).

The import moved the audio into the library and stranded the same-stem .lrc
in the source folder. move_companion_sidecars now rides along after every
pipeline move, renaming the sidecar to the destination stem.
"""

from __future__ import annotations

from core.imports.file_ops import move_companion_sidecars, safe_move_file


def _touch(p, content=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def test_lrc_moves_and_renames_with_the_track(tmp_path):
    src = _touch(tmp_path / "in" / "01 - Song.flac")
    _touch(tmp_path / "in" / "01 - Song.lrc", b"[00:01.00]hello")
    dst = tmp_path / "lib" / "Artist" / "Album" / "01 Song.flac"

    safe_move_file(src, dst)
    moved = move_companion_sidecars(src, dst)

    assert moved == [str(tmp_path / "lib" / "Artist" / "Album" / "01 Song.lrc")]
    assert (tmp_path / "lib" / "Artist" / "Album" / "01 Song.lrc").read_bytes() == b"[00:01.00]hello"
    assert not (tmp_path / "in" / "01 - Song.lrc").exists()


def test_no_sidecar_is_a_noop(tmp_path):
    src = _touch(tmp_path / "in" / "t.flac")
    dst = tmp_path / "lib" / "t.flac"
    safe_move_file(src, dst)
    assert move_companion_sidecars(src, dst) == []


def test_uppercase_extension_also_moves(tmp_path):
    src = _touch(tmp_path / "in" / "t.mp3")
    _touch(tmp_path / "in" / "t.LRC", b"lyrics")
    dst = tmp_path / "lib" / "t.mp3"
    safe_move_file(src, dst)
    moved = move_companion_sidecars(src, dst)
    assert moved and moved[0].endswith("t.lrc")          # normalized lowercase


def test_sidecar_failure_never_raises(tmp_path, monkeypatch):
    import core.imports.file_ops as fo
    src = _touch(tmp_path / "in" / "t.flac")
    _touch(tmp_path / "in" / "t.lrc")
    dst = tmp_path / "lib" / "t.flac"
    safe_move_file(src, dst)

    def boom(*a, **k):
        raise OSError("disk says no")

    monkeypatch.setattr(fo, "safe_move_file", boom)
    assert fo.move_companion_sidecars(src, dst) == []    # logged, not raised
