"""Atomic album publishing helpers (#999). Pure path math + publish move.

These pin the mechanics the pipeline/lifecycle wiring depends on:
* staging lives OUTSIDE the transfer (library) tree, one root per batch;
* final<->staging path mapping round-trips and refuses paths outside its tree;
* freshness gate (new-album-only) so completeness-fills aren't re-staged;
* publish moves every staged file into the library, repoints the DB, prunes the
  staging tree, and on a per-file failure keeps that file staged (never a
  partial library publish).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from core.downloads import atomic_album_publish as ap


def _mk(path: Path, data: bytes = b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def _move(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)


# --- path math --------------------------------------------------------------

def test_staging_root_is_hidden_sibling_of_transfer(tmp_path):
    transfer = tmp_path / "media" / "music"
    root = ap.staging_root_for_batch(str(transfer), "batch-123")
    # Sibling of the transfer dir (shares its parent → same filesystem), hidden,
    # and NOT under the transfer dir Plex scans.
    assert os.path.basename(os.path.dirname(root)) == ap._STAGING_DIRNAME
    assert root.endswith(os.path.join(ap._STAGING_DIRNAME, "batch-123"))
    assert not os.path.normpath(root).startswith(os.path.normpath(str(transfer)) + os.sep)


def test_to_staging_maps_relative_structure(tmp_path):
    transfer = str(tmp_path / "music")
    staging = str(tmp_path / "stage")
    final = os.path.join(transfer, "Artist", "Album [2003]", "01 - Song.flac")
    staged = ap.to_staging_path(final, transfer, staging)
    assert staged == os.path.join(staging, "Artist", "Album [2003]", "01 - Song.flac")


def test_to_staging_rejects_path_outside_transfer(tmp_path):
    transfer = str(tmp_path / "music")
    staging = str(tmp_path / "stage")
    assert ap.to_staging_path("/somewhere/else/x.flac", transfer, staging) is None
    assert ap.to_staging_path(transfer, transfer, staging) is None  # equals root


def test_staging_final_roundtrip(tmp_path):
    transfer = str(tmp_path / "music")
    staging = str(tmp_path / "stage")
    final = os.path.join(transfer, "A", "B", "03 - T.flac")
    staged = ap.to_staging_path(final, transfer, staging)
    back = ap.to_final_path(staged, staging, transfer)
    assert os.path.normpath(back) == os.path.normpath(final)


def test_to_final_rejects_path_outside_staging(tmp_path):
    transfer = str(tmp_path / "music")
    staging = str(tmp_path / "stage")
    assert ap.to_final_path("/not/staged/x.flac", staging, transfer) is None


# --- freshness gate ---------------------------------------------------------

def test_fresh_when_absent_or_empty(tmp_path):
    assert ap.album_folder_is_fresh(str(tmp_path / "nope")) is True
    empty = tmp_path / "empty"; empty.mkdir()
    assert ap.album_folder_is_fresh(str(empty)) is True


def test_not_fresh_when_audio_present(tmp_path):
    d = tmp_path / "album"
    _mk(d / "01 - existing.flac")
    assert ap.album_folder_is_fresh(str(d)) is False


def test_fresh_when_only_non_audio_present(tmp_path):
    d = tmp_path / "album"
    _mk(d / "folder.jpg")
    _mk(d / "album.nfo")
    assert ap.album_folder_is_fresh(str(d)) is True


# --- publish ----------------------------------------------------------------

def test_publish_moves_all_files_updates_db_and_prunes(tmp_path):
    transfer = str(tmp_path / "music")
    staging = ap.staging_root_for_batch(transfer, "b1")
    # A staged album: 2 tracks + cover art + a lyric sidecar.
    t1 = _mk(Path(staging) / "Artist" / "Album" / "01 - One.flac")
    t2 = _mk(Path(staging) / "Artist" / "Album" / "02 - Two.flac")
    art = _mk(Path(staging) / "Artist" / "Album" / "folder.jpg")
    lrc = _mk(Path(staging) / "Artist" / "Album" / "01 - One.lrc")

    db_updates = []
    res = ap.publish_album_batch(staging, transfer, _move,
                                 db_path_update_fn=lambda s, f: db_updates.append((s, f)))

    assert len(res["published"]) == 4 and res["failed"] == []
    # All four now live under the library, none left in staging.
    for rel in ("Artist/Album/01 - One.flac", "Artist/Album/02 - Two.flac",
                "Artist/Album/folder.jpg", "Artist/Album/01 - One.lrc"):
        assert os.path.isfile(os.path.join(transfer, *rel.split("/")))
    assert not os.path.exists(staging)  # staging tree pruned
    # DB repointed for every file that moved.
    assert len(db_updates) == 4
    for s, f in db_updates:
        assert s.startswith(staging) and f.startswith(transfer)


def test_publish_keeps_file_staged_on_move_failure(tmp_path):
    transfer = str(tmp_path / "music")
    staging = ap.staging_root_for_batch(transfer, "b2")
    good = _mk(Path(staging) / "A" / "Al" / "01.flac")
    bad = _mk(Path(staging) / "A" / "Al" / "02.flac")

    def _move_one_fails(src, dst):
        if src.endswith("02.flac"):
            raise OSError("disk full")
        _move(src, dst)

    res = ap.publish_album_batch(staging, transfer, _move_one_fails)

    assert len(res["published"]) == 1
    assert len(res["failed"]) == 1 and res["failed"][0][0].endswith("02.flac")
    # The good one published; the failed one is STILL in staging (never lost,
    # never a partial-library orphan), and the tree is NOT pruned.
    assert os.path.isfile(os.path.join(transfer, "A", "Al", "01.flac"))
    assert os.path.isfile(bad)
    assert os.path.isdir(staging)


def test_iter_staged_files_finds_everything(tmp_path):
    staging = str(tmp_path / "stage")
    _mk(Path(staging) / "x" / "1.flac")
    _mk(Path(staging) / "x" / "2.flac")
    _mk(Path(staging) / "cover.png")
    assert len(ap.iter_staged_files(staging)) == 3
    assert ap.iter_staged_files(str(tmp_path / "absent")) == []
