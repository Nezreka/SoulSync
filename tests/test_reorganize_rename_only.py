"""Rename-only reorganize (#875): move files to the current naming scheme with NO
copy / re-tag / post-processing. The headline guarantee is that it acts on exactly
what the preview computed and ONLY touches files whose path actually changes — files
the preview marked `unchanged` are left alone (the "every file got modified" bug).
"""

import os

from core.library_reorganize import (
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
