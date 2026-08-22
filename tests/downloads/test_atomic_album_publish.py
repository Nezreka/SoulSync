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

def test_staging_root_is_hidden_and_inside_transfer(tmp_path):
    """INSIDE the transfer dir, not beside it.

    A sibling was chosen for same-filesystem atomic renames, but for a Docker
    user the transfer dir IS a bind mount — D:/Music:/app/Transfer makes the
    sibling /app, the container's own layer: different filesystem, usually not
    writable, discarded on recreate. It failed hard rather than degrading
    (PermissionError on mkdir → the track never left downloads → "File
    verification failed ... not found after processing").

    Inside the transfer dir is same-filesystem and writable by construction:
    if the library is not writable there is nothing to publish into anyway.
    """
    transfer = tmp_path / "media" / "music"
    root = ap.staging_root_for_batch(str(transfer), "batch-123")
    assert os.path.basename(os.path.dirname(root)) == ap._STAGING_DIRNAME
    assert root.endswith(os.path.join(ap._STAGING_DIRNAME, "batch-123"))
    assert os.path.normpath(root).startswith(os.path.normpath(str(transfer)) + os.sep)
    # Dot-prefixed so media servers (and SoulSync's own scan) skip it.
    assert ap._STAGING_DIRNAME.startswith('.')


def test_an_already_staged_path_is_not_staged_again(tmp_path):
    """Only reachable since staging moved under the transfer dir: without the
    guard, a staged file maps into a staging mirror of itself, one level
    deeper every pass."""
    transfer = str(tmp_path / "music")
    root = ap.staging_root_for_batch(transfer, "b1")
    staged = os.path.join(root, "Artist", "Album", "01.flac")
    assert ap.to_staging_path(staged, transfer, root) is None
    assert ap.is_staged_path(staged, transfer)
    assert not ap.is_staged_path(os.path.join(transfer, "Artist", "Album", "01.flac"), transfer)


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


def test_a_failed_move_rolls_the_whole_album_back(tmp_path):
    """L2-002: "the album appears at once" is the point of atomic publishing.
    Leaving the files that did move live produced an album half in the library
    and half in staging, which the batch then reported as Complete with no
    retryable task for the missing half."""
    transfer = str(tmp_path / "music")
    staging = ap.staging_root_for_batch(transfer, "b2")
    good = _mk(Path(staging) / "A" / "Al" / "01.flac")
    bad = _mk(Path(staging) / "A" / "Al" / "02.flac")

    def _move_one_fails(src, dst):
        if src.endswith("02.flac"):
            raise OSError("disk full")
        _move(src, dst)

    res = ap.publish_album_batch(staging, transfer, _move_one_fails)

    assert res["success"] is False
    assert res["published"] == []
    assert len(res["failed"]) == 1 and res["failed"][0][0].endswith("02.flac")
    assert res["rollback_failed"] == []
    # Nothing is live, both files are back in staging, the tree is NOT pruned.
    assert not os.path.isfile(os.path.join(transfer, "A", "Al", "01.flac"))
    assert os.path.isfile(good)
    assert os.path.isfile(bad)
    assert os.path.isdir(staging)


def test_a_db_repoint_exception_fails_the_publish(tmp_path):
    transfer = str(tmp_path / "music")
    staging = ap.staging_root_for_batch(transfer, "b3")
    track = _mk(Path(staging) / "A" / "Al" / "01.flac")

    def _boom(_staged, _final):
        raise RuntimeError("database is locked")

    res = ap.publish_album_batch(staging, transfer, _move, _boom)

    assert res["success"] is False
    assert os.path.isfile(track), "the file must go back where a retry can find it"
    assert not os.path.isfile(os.path.join(transfer, "A", "Al", "01.flac"))


def test_a_db_repoint_that_matches_no_row_fails_the_publish(tmp_path):
    """The move worked, but the library still points at a staging path this
    publish is about to remove — the track would read as missing."""
    transfer = str(tmp_path / "music")
    staging = ap.staging_root_for_batch(transfer, "b4")
    track = _mk(Path(staging) / "A" / "Al" / "01.flac")

    res = ap.publish_album_batch(staging, transfer, _move,
                                 lambda _s, _f: 0)

    assert res["success"] is False
    assert os.path.isfile(track)


def test_a_sidecar_that_matches_no_row_is_fine(tmp_path):
    """Cover art and lyrics have no track row by design, so a zero rowcount for
    them says nothing about the publish."""
    transfer = str(tmp_path / "music")
    staging = ap.staging_root_for_batch(transfer, "b5")
    _mk(Path(staging) / "A" / "Al" / "01.flac")
    _mk(Path(staging) / "A" / "Al" / "folder.jpg")

    seen = []

    def _update(staged, _final):
        seen.append(staged)
        return 0 if staged.endswith(".jpg") else 1

    res = ap.publish_album_batch(staging, transfer, _move, _update)

    assert res["success"] is True
    assert len(seen) == 2
    assert os.path.isfile(os.path.join(transfer, "A", "Al", "folder.jpg"))


def test_an_unknown_rowcount_does_not_fail_the_publish(tmp_path):
    """A driver that cannot report a rowcount gives "unknown", not "zero"."""
    transfer = str(tmp_path / "music")
    staging = ap.staging_root_for_batch(transfer, "b6")
    _mk(Path(staging) / "A" / "Al" / "01.flac")

    res = ap.publish_album_batch(staging, transfer, _move, lambda _s, _f: None)

    assert res["success"] is True


def test_rollback_takes_the_db_pointer_back_with_the_file(tmp_path):
    """The half the all-or-nothing change forgot.

    Track 1 moves and repoints cleanly, track 2's move then fails, so track 1
    goes back to staging. Its library row stayed on the final path, and that
    path no longer exists: the track reads as missing until somebody retries,
    and forever if the batch is cancelled. Same split state the rollback exists
    to prevent, pointing the other way.
    """
    transfer = str(tmp_path / "music")
    staging = ap.staging_root_for_batch(transfer, "b10")
    one = _mk(Path(staging) / "A" / "Al" / "01.flac")
    _mk(Path(staging) / "A" / "Al" / "02.flac")

    db = {one: one}

    def _move_second_fails(src, dst):
        if src.endswith("02.flac"):
            raise OSError("disk full")
        _move(src, dst)

    def _repoint(staged, final):
        hits = [k for k, v in db.items() if v == staged]
        for k in hits:
            db[k] = final
        return len(hits)

    res = ap.publish_album_batch(staging, transfer, _move_second_fails, _repoint)

    assert res["success"] is False
    assert res["rollback_failed"] == []
    assert os.path.isfile(one), "the file went back to staging"
    assert db[one] == one, "and the library row went back with it"
    assert os.path.isfile(db[one]), "no row may point at a file that is not there"


def test_a_file_that_will_not_roll_back_keeps_its_db_row(tmp_path):
    """Move first, repoint only if the move worked.

    If the file is stuck at its final path, the row has to keep saying final.
    Repointing it to staging anyway would strand a file the library can no
    longer find, which is worse than the state we were rolling back from.
    """
    transfer = str(tmp_path / "music")
    staging = ap.staging_root_for_batch(transfer, "b11")
    one = _mk(Path(staging) / "A" / "Al" / "01.flac")
    _mk(Path(staging) / "A" / "Al" / "02.flac")
    final_one = os.path.join(transfer, "A", "Al", "01.flac")

    db = {one: one}

    def _move_out_ok_back_stuck(src, dst):
        if src.endswith("02.flac"):
            raise OSError("disk full")
        if src == final_one:
            raise OSError("file is locked")  # the rollback move
        _move(src, dst)

    def _repoint(staged, final):
        hits = [k for k, v in db.items() if v == staged]
        for k in hits:
            db[k] = final
        return len(hits)

    res = ap.publish_album_batch(staging, transfer, _move_out_ok_back_stuck, _repoint)

    assert res["success"] is False
    assert len(res["rollback_failed"]) == 1
    assert db[one] == final_one, "the file is still there, so the row must say so"
    assert os.path.isfile(db[one])


def test_discard_removes_genuine_staging_root(tmp_path):
    transfer = str(tmp_path / "music")
    staging = ap.staging_root_for_batch(transfer, "b9")
    _mk(Path(staging) / "A" / "01.flac")
    assert os.path.isdir(staging)
    assert ap.discard_staging_root(staging) is True
    assert not os.path.exists(staging)


def test_discard_refuses_non_staging_path(tmp_path):
    # SAFETY: a path whose parent isn't the dedicated staging dir must never be
    # removed, even if it exists — guards against a blank/misconfigured value.
    victim = tmp_path / "music" / "Artist" / "Album"
    _mk(victim / "01 - precious.flac")
    assert ap.discard_staging_root(str(victim)) is False
    assert os.path.isfile(str(victim / "01 - precious.flac"))  # untouched
    assert ap.discard_staging_root("") is False
    assert ap.discard_staging_root(None) is False


def test_discard_noop_when_absent(tmp_path):
    transfer = str(tmp_path / "music")
    staging = ap.staging_root_for_batch(transfer, "gone")  # never created
    assert ap.discard_staging_root(staging) is False


def test_iter_staged_files_finds_everything(tmp_path):
    staging = str(tmp_path / "stage")
    _mk(Path(staging) / "x" / "1.flac")
    _mk(Path(staging) / "x" / "2.flac")
    _mk(Path(staging) / "cover.png")
    assert len(ap.iter_staged_files(staging)) == 3
    assert ap.iter_staged_files(str(tmp_path / "absent")) == []


def test_publish_order_does_not_depend_on_the_filesystem(tmp_path):
    """os.walk hands back DIRECTORY order, which differs between machines: the
    tracks below come out 02-then-01 on btrfs and 01-then-02 on ext4.

    Publish order decides which files are already live when a later one fails,
    so it decides what the rollback has to undo — an all-or-nothing publish
    whose behaviour under failure depends on the host filesystem cannot be
    reasoned about. It also silently disarmed the two rollback tests above: on a
    box that walks 02 first the failing track is the FIRST one, nothing has
    published yet, and the rollback never runs, so
    test_rollback_takes_the_db_pointer_back_with_the_file passed with the
    rollback deleted.
    """
    staging = str(tmp_path / "stage")
    for name in ("02.flac", "01.flac", "10.flac", "cover.png"):
        _mk(Path(staging) / "A" / "Al" / name)

    found = ap.iter_staged_files(staging)

    assert [os.path.basename(p) for p in found] == \
        ["01.flac", "02.flac", "10.flac", "cover.png"]


def test_publish_order_reads_track_numbers_as_numbers(tmp_path):
    """Ordering is by track NUMBER, not by the spelling of the number.

    post_processing zero-pads track numbers to a minimum of two digits, so an
    album that runs past 99 gets "100 - Title.flac" alongside "09 - …", and a
    plain string sort files 100 in among the ones. Rollback survives that (it
    replays the files it really moved), but the publish log of a partial failure
    is a human-readable record of what went live before what, and it has to
    match the order the tracks are actually in.
    """
    staging = str(tmp_path / "stage")
    for name in ("100 - c.flac", "09 - a.flac", "10 - b.flac", "99 - z.flac"):
        _mk(Path(staging) / "A" / "Boxset" / name)

    found = [os.path.basename(p) for p in ap.iter_staged_files(staging)]

    assert found == ["09 - a.flac", "10 - b.flac", "99 - z.flac", "100 - c.flac"]
    # ... which is exactly where a plain string sort disagrees.
    assert found != sorted(found)


def test_publish_order_is_total_even_for_names_that_only_differ_in_case(tmp_path):
    """No pair of files may be left to os.walk to order. Folding case for the
    numeric comparison introduces ties, so the raw path breaks them."""
    staging = str(tmp_path / "stage")
    for name in ("Track.flac", "track.flac"):
        _mk(Path(staging) / "A" / "Al" / name)

    first = ap.iter_staged_files(staging)
    second = ap.iter_staged_files(staging)

    assert first == second
    assert len(first) == 2
