"""Rename-only reorganize (#875): move files to the current naming scheme with NO
copy / re-tag / post-processing. The headline guarantee is that it acts on exactly
what the preview computed and ONLY touches files whose path actually changes — files
the preview marked `unchanged` are left alone (the "every file got modified" bug).
"""

import os

from core.library_reorganize import (
    _move_album_sidecars,
    _rename_track_in_place,
    reorganize_album_rename_only,
)


# ── _rename_track_in_place ──

def test_rename_moves_file_and_creates_dest_dir(tmp_path):
    src = tmp_path / "old" / "01 - Song.flac"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio")
    dst = tmp_path / "new" / "Song - Artist.flac"

    ok, err = _rename_track_in_place(str(src), str(dst))
    assert ok and err is None
    assert dst.exists() and dst.read_bytes() == b"audio"
    assert not src.exists()


def test_rename_refuses_to_overwrite_a_different_file(tmp_path):
    src = tmp_path / "a.flac"
    src.write_bytes(b"source")
    dst = tmp_path / "b.flac"
    dst.write_bytes(b"someone else")   # a DIFFERENT existing file

    ok, err = _rename_track_in_place(str(src), str(dst))
    assert not ok and "exists" in err
    assert src.exists() and dst.read_bytes() == b"someone else"   # nothing destroyed


def test_rename_missing_source_errors(tmp_path):
    ok, err = _rename_track_in_place(str(tmp_path / "gone.flac"), str(tmp_path / "x.flac"))
    assert not ok and "no longer on disk" in err


def test_rename_same_path_is_noop_ok(tmp_path):
    f = tmp_path / "x.flac"
    f.write_bytes(b"a")
    ok, err = _rename_track_in_place(str(f), str(f))
    assert ok and f.exists()


def test_rename_carries_sibling_format_file(tmp_path):
    # lossy-copy pair: canonical .flac + sibling .opus in the same folder
    src = tmp_path / "old" / "01 - Song.flac"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"flac")
    sib = tmp_path / "old" / "01 - Song.opus"
    sib.write_bytes(b"opus")
    dst = tmp_path / "new" / "Song.flac"

    ok, _ = _rename_track_in_place(str(src), str(dst))
    assert ok
    assert dst.exists()
    assert (tmp_path / "new" / "Song.opus").exists()    # sibling came along, renamed stem


# ── reorganize_album_rename_only (fake preview injected) ──

def _fake_preview(tracks, *, success=True, status="planned", source="deezer"):
    def _preview(**_kw):
        return {"success": success, "status": status, "source": source, "tracks": tracks}
    return _preview


def _run(tracks, *, update=None, cleanup=None, stop=None, **preview_kw):
    return reorganize_album_rename_only(
        album_id="A1", db=None, transfer_dir="/x",
        resolve_file_path_fn=lambda p: p,
        build_final_path_fn=lambda *a, **k: (None, True),
        update_track_path_fn=update,
        cleanup_empty_dir_fn=cleanup,
        stop_check=stop,
        preview_fn=_fake_preview(tracks, **preview_kw),
    )


def test_moves_changed_and_skips_unchanged(tmp_path):
    """THE regression: a changed track moves + DB updates; an `unchanged` track is
    left completely alone (not re-touched). This is the #875 fix in one test."""
    src = tmp_path / "old" / "01 - A.flac"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"a")
    new = tmp_path / "new" / "A - Artist.flac"
    keep = tmp_path / "keep" / "B.flac"
    keep.parent.mkdir(parents=True)
    keep.write_bytes(b"b")

    updates = []
    summary = _run(
        [
            {"track_id": "t1", "title": "A", "matched": True, "unchanged": False,
             "collision": False, "current_path_abs": str(src), "new_path_abs": str(new)},
            {"track_id": "t2", "title": "B", "matched": True, "unchanged": True,
             "collision": False, "current_path_abs": str(keep), "new_path_abs": str(keep)},
        ],
        update=lambda tid, path: updates.append((tid, path)),
    )

    assert summary["moved"] == 1 and summary["skipped"] == 1 and summary["failed"] == 0
    assert new.exists() and not src.exists()       # changed track moved
    assert keep.exists() and keep.read_bytes() == b"b"   # unchanged: untouched
    assert updates == [("t1", str(new))]           # DB updated ONLY for the moved one


def test_collision_and_unmatched_are_skipped(tmp_path):
    summary = _run([
        {"track_id": "c", "title": "C", "matched": True, "unchanged": False,
         "collision": True, "current_path_abs": "/a", "new_path_abs": "/b"},
        {"track_id": "u", "title": "U", "matched": False, "unchanged": False,
         "collision": False, "current_path_abs": "/a", "new_path_abs": "/b"},
    ])
    assert summary["skipped"] == 2 and summary["moved"] == 0 and summary["failed"] == 0


def test_failed_rename_is_counted_not_fatal(tmp_path):
    src = tmp_path / "a.flac"
    src.write_bytes(b"src")
    dst = tmp_path / "taken.flac"
    dst.write_bytes(b"occupied")   # forces "destination already exists"
    summary = _run([
        {"track_id": "t", "title": "T", "matched": True, "unchanged": False,
         "collision": False, "current_path_abs": str(src), "new_path_abs": str(dst)},
    ])
    assert summary["failed"] == 1 and summary["moved"] == 0
    assert summary["errors"] and summary["errors"][0]["track_id"] == "t"
    assert src.exists() and dst.read_bytes() == b"occupied"   # nothing lost


def test_preview_failure_returns_its_status():
    summary = _run([], success=False, status="no_source_id")
    assert summary["status"] == "no_source_id"
    assert summary["moved"] == 0


def test_stop_check_aborts_early(tmp_path):
    src = tmp_path / "a.flac"
    src.write_bytes(b"a")
    summary = _run(
        [{"track_id": "t", "title": "T", "matched": True, "unchanged": False,
          "collision": False, "current_path_abs": str(src), "new_path_abs": str(tmp_path / "b.flac")}],
        stop=lambda: True,
    )
    assert summary["moved"] == 0          # aborted before processing
    assert src.exists()


def test_cleanup_called_for_emptied_source_dirs(tmp_path):
    src = tmp_path / "old" / "01 - A.flac"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"a")
    cleaned = []
    _run(
        [{"track_id": "t1", "title": "A", "matched": True, "unchanged": False,
          "collision": False, "current_path_abs": str(src),
          "new_path_abs": str(tmp_path / "new" / "A.flac")}],
        cleanup=lambda d: cleaned.append(d),
    )
    assert str(tmp_path / "old") in cleaned


# ── the file and the catalogue must not drift apart ──────────────────────────
#
# A rename MOVES the only copy. If the catalogue update then fails, the old
# "log it and carry on" left the file at a path nothing points at — the track
# reads as MISSING and gets re-downloaded later. There is no second copy to
# recover from, so the move must be undone.

def test_a_failed_db_update_rolls_the_file_back(tmp_path):
    src = tmp_path / "old" / "01 - A.flac"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"a")
    new = tmp_path / "new" / "A - Artist.flac"

    def _boom(track_id, path):
        raise RuntimeError("database is locked")

    out = _run([{
        "track_id": "t1", "title": "A", "matched": True, "unchanged": False,
        "collision": False, "file_exists": True,
        "current_path_abs": str(src), "new_path_abs": str(new),
    }], update=_boom)

    assert src.exists(), "the only copy was left at a path the catalogue never learned"
    assert not new.exists()
    assert out["moved"] == 0 and out["failed"] == 1
    assert "database is locked" in out["errors"][0]["error"]


def test_a_successful_db_update_keeps_the_move(tmp_path):
    src = tmp_path / "old" / "01 - A.flac"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"a")
    new = tmp_path / "new" / "A - Artist.flac"

    out = _run([{
        "track_id": "t1", "title": "A", "matched": True, "unchanged": False,
        "collision": False, "file_exists": True,
        "current_path_abs": str(src), "new_path_abs": str(new),
    }], update=lambda *_a: None)

    assert new.exists() and not src.exists()
    assert out["moved"] == 1 and out["failed"] == 0


# ── a track whose file could not be resolved is not a rename candidate ───────
#
# The preview sets current_path_abs='' when the stored path resolves to nothing
# on disk. Feeding that to os.rename fails — but only AFTER os.makedirs has
# already built the destination tree, littering the library with empty folders
# (the #767 complaint the preview avoids with create_dirs=False).

def test_an_unresolvable_source_is_skipped_without_creating_folders(tmp_path):
    new = tmp_path / "new" / "Artist" / "Album" / "01 - A.flac"

    out = _run([{
        "track_id": "t1", "title": "A", "matched": True, "unchanged": False,
        "collision": False, "file_exists": False,
        "current_path_abs": "", "new_path_abs": str(new),
    }], update=lambda *_a: None)

    assert not new.parent.exists(), "an empty destination tree was created"
    assert out["moved"] == 0 and out["failed"] == 0 and out["skipped"] == 1


# ── sidecars travel with the file they belong to ──

def test_lyrics_sidecar_follows_the_track(tmp_path):
    """A reorganize that leaves the .lrc behind has lost it.

    The full-mode reorganize could DELETE per-track sidecars at the source
    because post-processing re-created them at the destination. A move has no
    such second half, so it has to carry them.
    """
    src = tmp_path / "old" / "01 - Song.flac"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio")
    (tmp_path / "old" / "01 - Song.lrc").write_text("[00:01.00] a line")
    dst = tmp_path / "new" / "Song - Artist.flac"

    ok, err = _rename_track_in_place(str(src), str(dst))

    assert ok and err is None
    assert (tmp_path / "new" / "Song - Artist.lrc").read_text() == "[00:01.00] a line"
    assert not (tmp_path / "old" / "01 - Song.lrc").exists()


def test_other_per_track_sidecars_follow_too(tmp_path):
    src = tmp_path / "old" / "01 - Song.flac"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio")
    for ext in ('.nfo', '.cue'):
        (tmp_path / "old" / f"01 - Song{ext}").write_text(ext)
    dst = tmp_path / "new" / "Song - Artist.flac"

    ok, _ = _rename_track_in_place(str(src), str(dst))

    assert ok
    for ext in ('.nfo', '.cue'):
        assert (tmp_path / "new" / f"Song - Artist{ext}").read_text() == ext


def test_a_sidecar_already_at_the_destination_is_not_clobbered(tmp_path):
    src = tmp_path / "old" / "01 - Song.flac"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio")
    (tmp_path / "old" / "01 - Song.lrc").write_text("mine")
    dst_dir = tmp_path / "new"; dst_dir.mkdir()
    (dst_dir / "Song - Artist.lrc").write_text("already there")

    ok, _ = _rename_track_in_place(str(src), str(dst_dir / "Song - Artist.flac"))

    assert ok
    assert (dst_dir / "Song - Artist.lrc").read_text() == "already there"


# ── album-level artwork follows once the source album is empty ─────────────

def test_album_art_and_album_sidecars_follow_the_album(tmp_path):
    src = tmp_path / "old album"
    dst = tmp_path / "new album"
    src.mkdir(); dst.mkdir()
    for name in ("cover.jpg", "folder.png", "album.nfo", "playlist.m3u"):
        (src / name).write_text(name)

    moved = _move_album_sidecars(str(src), str(dst))

    assert moved == 4
    for name in ("cover.jpg", "folder.png", "album.nfo", "playlist.m3u"):
        assert (dst / name).read_text() == name
        assert not (src / name).exists()


def test_album_sidecars_wait_until_no_audio_remains(tmp_path):
    src = tmp_path / "old album"
    dst = tmp_path / "new album"
    src.mkdir(); dst.mkdir()
    (src / "cover.jpg").write_bytes(b"cover")
    (src / "track-still-here.flac").write_bytes(b"audio")

    assert _move_album_sidecars(str(src), str(dst)) == 0
    assert (src / "cover.jpg").read_bytes() == b"cover"
    assert not (dst / "cover.jpg").exists()


def test_album_sidecars_never_overwrite_destination_files(tmp_path):
    src = tmp_path / "old album"
    dst = tmp_path / "new album"
    src.mkdir(); dst.mkdir()
    (src / "cover.jpg").write_bytes(b"old")
    (dst / "cover.jpg").write_bytes(b"new")

    assert _move_album_sidecars(str(src), str(dst)) == 0
    assert (src / "cover.jpg").read_bytes() == b"old"
    assert (dst / "cover.jpg").read_bytes() == b"new"


def test_album_sidecar_move_keeps_real_documents_at_source(tmp_path):
    src = tmp_path / "old album"
    dst = tmp_path / "new album"
    src.mkdir(); dst.mkdir()
    (src / "cover.jpg").write_bytes(b"cover")
    (src / "booklet.pdf").write_bytes(b"booklet")

    assert _move_album_sidecars(str(src), str(dst)) == 1
    assert (dst / "cover.jpg").exists()
    assert (src / "booklet.pdf").read_bytes() == b"booklet"


# ── a sibling-format file is part of the track, not a separate move ──

def test_a_sibling_never_overwrites_a_file_already_at_the_destination(tmp_path):
    """The canonical move refuses a destination that already holds a different
    file ('destination already exists'). The sibling move used `shutil.move`
    with no check at all, which on one filesystem is `os.rename` and clobbers.
    """
    src = tmp_path / "old" / "01 - Song.flac"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio")
    (tmp_path / "old" / "01 - Song.opus").write_bytes(b"lossy copy")
    dst_dir = tmp_path / "new"; dst_dir.mkdir()
    (dst_dir / "Song - Artist.opus").write_bytes(b"someone else")

    ok, _ = _rename_track_in_place(str(src), str(dst_dir / "Song - Artist.flac"))

    assert ok
    assert (dst_dir / "Song - Artist.opus").read_bytes() == b"someone else"
    assert (tmp_path / "old" / "01 - Song.opus").read_bytes() == b"lossy copy"


def test_a_failed_move_leaves_the_sibling_where_it_was(tmp_path, monkeypatch):
    """Siblings were carried BEFORE the canonical rename. When that rename then
    failed the track was reported failed, but the `.opus` was already at the new
    location while the `.flac` and the catalogue row still named the old one.

    Same rule the sidecar helper states for itself: a failed move must leave the
    whole track where it was.
    """
    src = tmp_path / "old" / "01 - Song.flac"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio")
    sibling = tmp_path / "old" / "01 - Song.opus"
    sibling.write_bytes(b"lossy copy")
    dst = tmp_path / "new" / "Song - Artist.flac"

    real_rename = os.rename

    def _rename(a, b, *args, **kwargs):
        if str(a) == str(src):
            raise OSError(13, "Permission denied")
        return real_rename(a, b, *args, **kwargs)

    monkeypatch.setattr(os, "rename", _rename)

    ok, err = _rename_track_in_place(str(src), str(dst))

    assert not ok and err
    assert src.exists()
    assert sibling.exists()                       # not carried ahead of the audio
    assert not (tmp_path / "new" / "Song - Artist.opus").exists()
