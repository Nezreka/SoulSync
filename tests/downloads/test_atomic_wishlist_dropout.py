"""The wishlist dropout that atomic album publishing opened (#1289).

#999 moved the moment a track becomes visible in the library from per-track
post-processing to batch completion, and left the wishlist removal at per-track
post-processing. Between the two the track was in neither the library nor the
wishlist, and that gap lasts until the LAST track of the album finishes — so a
restart, a failed publish or a cancel inside it destroyed both the file's
visibility and the only durable record that anyone had asked for it.

The tests that would have caught it are the first and the fourth here: a staged
track whose process never reaches the publish, and a batch force-errored before
any publish attempt. Everything else guards a path that fed the same drain.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import core.downloads.lifecycle as lc
import core.imports.pipeline as pl
from core.downloads import atomic_manifest as manifest
from core.downloads.atomic_album_publish import (
    contains_staging_segment,
    should_discard_staging,
    split_staged_path,
)
from core.wishlist import removal_guard as guard


class _Cfg:
    def __init__(self, vals):
        self.vals = vals

    def get(self, key, default=None):
        return self.vals.get(key, default)


def _stage_a_track(monkeypatch, tmp_path, batch, *, name="05 - Never There.mp3"):
    """Drive the real stage redirect, then put a real file at the staged path."""
    transfer = str(tmp_path / "library")
    monkeypatch.setattr(pl, "config_manager", _Cfg({
        "album_downloads.atomic_publish": True,
        "soulseek.transfer_path": transfer,
    }))
    monkeypatch.setattr(pl, "docker_resolve_path", lambda p: p)
    monkeypatch.setattr(pl, "download_batches", {"B": batch})
    final = os.path.join(transfer, "Hoobastank", "The Reason", name)
    staged = pl._maybe_stage_album_track({"batch_id": "B"}, final)
    Path(staged).parent.mkdir(parents=True, exist_ok=True)
    Path(staged).write_bytes(b"AUDIO")
    return transfer, staged, final


def _context():
    return {
        "source": "spotify",
        "batch_id": "B",
        "track_info": {
            "id": "257U69MNzHAYAaQQpsfksD",
            "name": "Never There",
            "artists": [{"name": "Hoobastank"}],
        },
        "original_search_result": {},
        "search_result": {},
    }


# --- 1. the Hoobastank case: staged, then the process goes away -------------

def test_a_staged_track_defers_its_wishlist_removal(monkeypatch, tmp_path):
    """The bug, in one assertion: post-processing a track into atomic staging
    must not delete its wishlist row. The album has not published; the file is
    in a dot-directory no scanner reads; killing the process here used to leave
    the track absent from the library AND absent from the wishlist."""
    removed = []
    monkeypatch.setattr(pl, "check_and_remove_from_wishlist",
                        lambda *a, **k: removed.append((a, k)))

    batch = {"is_album_download": True}
    _transfer, staged, final = _stage_a_track(monkeypatch, tmp_path, batch)

    deferred = pl._settle_wishlist_for_completed_track(_context(), staged)

    assert deferred is True
    assert removed == [], "a staged track is not a satisfied request"

    pending = batch["_wishlist_pending"]
    assert len(pending) == 1
    assert pending[0]["staged_path"] == os.path.normpath(staged)
    assert pending[0]["final_path"] == os.path.normpath(final)


def test_the_manifest_survives_the_process(monkeypatch, tmp_path):
    """Everything that knew what a staging tree was lived in download_batches,
    a dict in the web process. The manifest is the half that has to outlive it,
    so a later process can map the files back to the requests they satisfy."""
    batch = {"is_album_download": True}
    _transfer, staged, final = _stage_a_track(monkeypatch, tmp_path, batch)
    pl._settle_wishlist_for_completed_track(_context(), staged)

    root = batch["_atomic_staging_root"]
    data = manifest.read_manifest(root)
    assert data["batch_id"] == "B"
    entry = manifest.manifest_tracks(data)[os.path.normpath(staged)]
    assert entry["source_ids"]["track_id"] == "257U69MNzHAYAaQQpsfksD"
    assert entry["final_path"] == os.path.normpath(final)


def test_the_manifest_is_never_published_into_the_library(monkeypatch, tmp_path):
    """It lives inside the staging root, so publish has to skip it — otherwise
    a JSON file lands in the user's album folder and the prune can never empty
    the tree behind it."""
    from core.downloads.atomic_album_publish import iter_staged_files, publish_album_batch
    from core.imports.file_ops import safe_move_file

    batch = {"is_album_download": True}
    transfer, staged, final = _stage_a_track(monkeypatch, tmp_path, batch)
    pl._settle_wishlist_for_completed_track(_context(), staged)
    root = batch["_atomic_staging_root"]

    assert manifest.manifest_path(root) not in iter_staged_files(root)

    result = publish_album_batch(root, transfer, safe_move_file)
    assert result["success"] is True
    assert os.path.isfile(final)
    assert not os.path.exists(root), "the manifest must not keep the tree alive"
    assert not os.path.exists(os.path.join(os.path.dirname(final), manifest.MANIFEST_NAME))


# --- 2. the publish settles what it made live -------------------------------

def test_publish_clears_only_entries_with_a_real_final_file(monkeypatch, tmp_path):
    """The roster says what the batch meant to publish; the filesystem says what
    it did. An entry the publish silently skipped keeps its wishlist row."""
    from core.wishlist.resolution import remove_published_wishlist_entries

    live = tmp_path / "library" / "Hoobastank" / "The Reason"
    live.mkdir(parents=True)
    landed = live / "05 - Never There.mp3"
    landed.write_bytes(b"AUDIO")
    vanished = str(live / "06 - Ghost.mp3")

    calls = []
    monkeypatch.setattr("core.wishlist.resolution.check_and_remove_from_wishlist",
                        lambda ctx, **kw: calls.append(kw.get("published_path")) or True)

    pending = [
        {"staged_path": "/staged/05.mp3", "final_path": str(landed), "track_name": "Never There",
         "context": {}},
        {"staged_path": "/staged/06.mp3", "final_path": vanished, "track_name": "Ghost",
         "context": {}},
    ]
    cleared = remove_published_wishlist_entries(
        pending, {"/staged/05.mp3": str(landed)}, batch_id="B")

    assert cleared == 1
    assert calls == [str(landed)]


def test_a_failed_publish_keeps_every_wishlist_row(monkeypatch, tmp_path):
    """L2-002 rolls a partial publish back into staging. The requests have to
    roll back with it — which they do by never having been removed."""
    settled = []
    monkeypatch.setattr(lc, "_settle_deferred_wishlist",
                        lambda *a, **k: settled.append(a))
    monkeypatch.setattr("core.downloads.atomic_album_publish.publish_album_batch",
                        lambda *a, **k: {"success": False, "published": [],
                                         "failed": [("x", "disk full")], "rollback_failed": []})

    batch = {"is_album_download": True}
    transfer, staged, _final = _stage_a_track(monkeypatch, tmp_path, batch)
    pl._settle_wishlist_for_completed_track(_context(), staged)

    assert lc._publish_atomic_album("B", batch) is False
    assert settled == [], "nothing may be removed when nothing was published"
    assert batch["_wishlist_pending"], "the roster stays for the retry"


# --- 3. the guard itself ----------------------------------------------------

@pytest.mark.parametrize("state,path_kind", [
    (guard.STAGED, "staged"),
    (guard.MISSING, "absent"),
    (guard.UNKNOWN, "none"),
    (guard.PUBLISHED, "real"),
])
def test_only_a_real_unstaged_file_permits_removal(tmp_path, state, path_kind):
    real = tmp_path / "01.mp3"
    real.write_bytes(b"A")
    path = {
        "staged": str(tmp_path / ".soulsync_atomic_staging" / "B" / "01.mp3"),
        "absent": str(tmp_path / "nope.mp3"),
        "none": None,
        "real": str(real),
    }[path_kind]

    allowed, got = guard.may_remove(path)
    assert got == state
    assert allowed is (state == guard.PUBLISHED)


def test_ungated_reasons_pass_through():
    """A user deleting their own entry, or the already-owned cleanup, proves
    itself a different way and must not be blocked by a missing file path."""
    allowed, _ = guard.may_remove(None, reason=guard.REASON_ALREADY_OWNED)
    assert allowed is True


def test_no_context_completion_cannot_remove_from_the_wishlist():
    """post_processing's no-matched-context branch reports success for a file
    still sitting in the DOWNLOADS folder, never imported. It records no
    final_file_path, so the completion callback has no proof and keeps the
    track retryable."""
    task_without_import = {}
    allowed, state = guard.may_remove(task_without_import.get("final_file_path"))
    assert (allowed, state) == (False, guard.UNKNOWN)


# --- 4. staged audio is not scratch space -----------------------------------

@pytest.mark.parametrize("phase,attempts,expected", [
    ("cancelled", 0, True),      # the user asked for it to go away
    ("error", 0, False),         # the 600s stuck-heal: never even tried to publish
    ("error", 3, False),         # publish retries exhausted
    ("complete", 0, False),      # stale cleanup of something that did not publish
    ("failed", 0, False),
])
def test_only_an_explicit_cancel_discards_staged_audio(phase, attempts, expected):
    """The old guard preserved staging ONLY after an exhausted publish, so the
    batch force-errored by the stuck healer (which never attempts a publish, so
    _atomic_publish_attempts stays 0) had its finished audio rmtree'd five
    minutes later — with its wishlist rows already deleted."""
    batch = {
        "_atomic_active": True,
        "_atomic_staging_root": "/library/.soulsync_atomic_staging/B",
        "phase": phase,
        "_atomic_publish_attempts": attempts,
    }
    discard, _why = should_discard_staging(batch)
    assert discard is expected


def test_a_non_staged_batch_is_never_touched():
    assert should_discard_staging({"phase": "cancelled"}) == (False, "not a staged batch")


# --- 5. startup reconciliation ----------------------------------------------

def _orphan_tree(tmp_path, *, with_manifest=True):
    transfer = tmp_path / "library"
    root = transfer / ".soulsync_atomic_staging" / "6cf3b636"
    album = root / "Hoobastank" / "The Reason"
    album.mkdir(parents=True)
    for name in ("05 - Never There.mp3", "06 - Same Direction.mp3"):
        (album / name).write_bytes(b"AUDIO")
    if with_manifest:
        manifest.begin_batch(str(root), batch_id="6cf3b636",
                             transfer_dir=str(transfer), profile_id=1)
        manifest.record_staged_track(
            str(root),
            staged_path=str(album / "05 - Never There.mp3"),
            final_path=str(transfer / "Hoobastank" / "The Reason" / "05 - Never There.mp3"),
            source="spotify",
            source_ids={"track_id": "257U69MNzHAYAaQQpsfksD"},
            track_name="Never There", artist_name="Hoobastank", profile_id=1)
    return transfer, root


def test_startup_recovery_publishes_an_abandoned_tree(tmp_path):
    """The audio was never lost — it was in a dot-directory that SoulSync's
    scanner, the repair jobs and every media server skip by design, with the
    only thing that knew what it was gone with the process."""
    from core.downloads.atomic_recovery import recover_orphan_staging

    transfer, root = _orphan_tree(tmp_path)
    cleared = []
    stats = recover_orphan_staging(
        [str(transfer)],
        remove_from_wishlist=lambda pending, pubmap, **kw: cleared.extend(pending) or len(pending))

    assert stats["published"] == 1
    assert stats["files"] == 2
    assert os.path.isfile(transfer / "Hoobastank" / "The Reason" / "05 - Never There.mp3")
    assert not os.path.exists(root)
    assert [c["context"]["track_info"]["id"] for c in cleared] == ["257U69MNzHAYAaQQpsfksD"]


def test_recovery_works_without_a_manifest(tmp_path):
    """The staging layout is self-describing, so a tree from before this fix —
    or one whose manifest write failed — is still recoverable. Its wishlist rows
    are still there to be cleared by the already-owned cleanup."""
    from core.downloads.atomic_recovery import recover_orphan_staging

    transfer, root = _orphan_tree(tmp_path, with_manifest=False)
    stats = recover_orphan_staging([str(transfer)], remove_from_wishlist=lambda *a, **k: 0)

    assert stats["published"] == 1
    assert os.path.isfile(transfer / "Hoobastank" / "The Reason" / "06 - Same Direction.mp3")


def test_recovery_never_overwrites_a_file_the_library_already_has(tmp_path):
    """A live batch cannot collide — album_folder_is_fresh guarantees an empty
    album folder. A tree recovered weeks later has no such guarantee: the user
    has re-requested the stranded tracks and some have landed. safe_move_file
    publishes with os.replace, which overwrites, so a straight publish would put
    a months-old staged copy on top of a freshly imported, verified file."""
    from core.downloads.atomic_recovery import recover_orphan_staging

    transfer, root = _orphan_tree(tmp_path)
    # the user already re-downloaded one of the two stranded tracks
    live_album = transfer / "Hoobastank" / "The Reason"
    live_album.mkdir(parents=True)
    kept = live_album / "05 - Never There.mp3"
    kept.write_bytes(b"THE NEWER LIBRARY COPY")

    stats = recover_orphan_staging([str(transfer)], remove_from_wishlist=lambda *a, **k: 0)

    assert stats["superseded"] == 1
    assert stats["published"] == 1
    assert kept.read_bytes() == b"THE NEWER LIBRARY COPY", "the library copy wins"
    # the genuinely-missing track still recovers
    assert os.path.isfile(live_album / "06 - Same Direction.mp3")
    # and the superseded copy is restorable, not destroyed
    superseded = list((transfer / ".deleted" / "atomic_superseded").rglob("*.mp3"))
    assert len(superseded) == 1


def test_recovery_of_a_fully_superseded_tree_publishes_nothing(tmp_path):
    from core.downloads.atomic_recovery import recover_orphan_staging

    transfer, root = _orphan_tree(tmp_path)
    live_album = transfer / "Hoobastank" / "The Reason"
    live_album.mkdir(parents=True)
    for name in ("05 - Never There.mp3", "06 - Same Direction.mp3"):
        (live_album / name).write_bytes(b"LIBRARY")

    stats = recover_orphan_staging([str(transfer)], remove_from_wishlist=lambda *a, **k: 0)

    assert stats["superseded"] == 2
    assert stats["published"] == 0
    assert not os.path.exists(root)
    for name in ("05 - Never There.mp3", "06 - Same Direction.mp3"):
        assert (live_album / name).read_bytes() == b"LIBRARY"


def test_recovery_ignores_a_dot_directory_in_the_staging_tree(tmp_path):
    """Batch ids are never dot-prefixed. Treating a hand-made dot-directory as a
    batch would publish its contents into the user's library."""
    from core.downloads.atomic_recovery import recover_orphan_staging

    transfer = tmp_path / "library"
    stray = transfer / ".soulsync_atomic_staging" / ".scratch" / "Artist" / "Album"
    stray.mkdir(parents=True)
    (stray / "notes.mp3").write_bytes(b"NOT AN ALBUM")

    stats = recover_orphan_staging([str(transfer)], remove_from_wishlist=lambda *a, **k: 0)

    assert stats["scanned"] == 0
    assert os.path.isfile(stray / "notes.mp3")


def test_recovery_disabled_reports_and_never_deletes(tmp_path):
    from core.downloads.atomic_recovery import recover_orphan_staging

    transfer, root = _orphan_tree(tmp_path)
    stats = recover_orphan_staging([str(transfer)], enabled=False,
                                   remove_from_wishlist=lambda *a, **k: 0)

    assert stats == {"scanned": 1, "published": 0, "files": 0, "failed": 0,
                     "empty": 0, "reported": 1, "wishlist_cleared": 0, "superseded": 0}
    assert os.path.isfile(root / "Hoobastank" / "The Reason" / "05 - Never There.mp3")


def test_recovery_skips_a_live_batch(tmp_path):
    from core.downloads.atomic_recovery import recover_orphan_staging

    transfer, root = _orphan_tree(tmp_path)
    stats = recover_orphan_staging([str(transfer)], live_batch_ids={"6cf3b636"},
                                   remove_from_wishlist=lambda *a, **k: 0)

    assert stats["scanned"] == 0
    assert os.path.isfile(root / "Hoobastank" / "The Reason" / "05 - Never There.mp3")


def test_recovery_clears_an_empty_tree(tmp_path):
    from core.downloads.atomic_recovery import recover_orphan_staging

    transfer = tmp_path / "library"
    root = transfer / ".soulsync_atomic_staging" / "empty"
    root.mkdir(parents=True)
    stats = recover_orphan_staging([str(transfer)], remove_from_wishlist=lambda *a, **k: 0)

    assert stats["empty"] == 1
    assert not os.path.exists(root)


# --- 6. profile scoping -----------------------------------------------------

def test_success_delete_is_scoped_to_the_profiles_that_own_the_file(tmp_path):
    """One profile's download used to empty every other profile's wishlist for
    the same track, including own-library profiles that cannot see the file."""
    import database.music_database as mdb_mod

    db = mdb_mod.MusicDatabase(database_path=str(tmp_path / "music.db"))
    conn = db._get_connection()
    for pid in (1, 2):
        conn.execute(
            "INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, profile_id) "
            "VALUES (?, '{}', ?)", ("sp-1", pid))
    conn.commit()
    conn.close()

    assert db.update_wishlist_retry("sp-1", True, profile_ids=[1]) is True

    conn = db._get_connection()
    left = [r[0] for r in conn.execute(
        "SELECT profile_id FROM wishlist_tracks WHERE spotify_track_id = 'sp-1'")]
    audit = conn.execute(
        "SELECT reason, rows_removed FROM wishlist_removals").fetchall()
    conn.close()

    assert left == [2]
    assert audit and audit[0][1] == 1


def test_unscoped_delete_still_sweeps_every_profile(tmp_path):
    """The default has to stay as it was: a caller that cannot tell who owns the
    file leaves no stale row behind, because a stale row means a re-download."""
    import database.music_database as mdb_mod

    db = mdb_mod.MusicDatabase(database_path=str(tmp_path / "music.db"))
    conn = db._get_connection()
    for pid in (1, 2):
        conn.execute(
            "INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, profile_id) "
            "VALUES (?, '{}', ?)", ("sp-1", pid))
    conn.commit()
    conn.close()

    assert db.update_wishlist_retry("sp-1", True) is True

    conn = db._get_connection()
    left = list(conn.execute(
        "SELECT profile_id FROM wishlist_tracks WHERE spotify_track_id = 'sp-1'"))
    conn.close()
    assert left == []


def test_the_audit_answers_where_did_this_track_go(tmp_path):
    import database.music_database as mdb_mod

    db = mdb_mod.MusicDatabase(database_path=str(tmp_path / "music.db"))
    conn = db._get_connection()
    conn.execute("INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, profile_id) "
                 "VALUES ('sp-9', '{}', 1)")
    conn.commit()
    conn.close()

    db.update_wishlist_retry("sp-9", True, profile_ids=[1], audit={
        "reason": "atomic_published", "final_path": "/library/a/b/01.mp3",
        "batch_id": "B", "source": "spotify"})

    rows = db.get_wishlist_removals("sp-9")
    assert rows[0]["reason"] == "atomic_published"
    assert rows[0]["final_path"] == "/library/a/b/01.mp3"
    assert rows[0]["batch_id"] == "B"


# --- 7. a staged library row is not ownership -------------------------------

def test_already_owned_ignores_a_row_still_pointing_into_staging():
    """record_soulsync_library_entry writes a tracks row for a STAGED file — the
    row the publish later repoints. Matching a wishlist entry against one and
    deleting it is the same dropout arriving through the cleanup door."""
    from types import SimpleNamespace

    from core.wishlist.library_match import find_owned_match

    staged_row = SimpleNamespace(
        file_path="/library/.soulsync_atomic_staging/B/A/Album/01.mp3")
    db = SimpleNamespace(check_track_exists=lambda *a, **k: (staged_row, 0.95))

    assert find_owned_match(db, "Song", [{"name": "Artist"}], "Album", "soulsync") is None


def test_already_owned_accepts_a_published_row():
    from types import SimpleNamespace

    from core.wishlist.library_match import find_owned_match

    row = SimpleNamespace(file_path="/library/A/Album/01.mp3")
    db = SimpleNamespace(check_track_exists=lambda *a, **k: (row, 0.95))

    match = find_owned_match(db, "Song", [{"name": "Artist"}], "Album", "navidrome")
    assert match is not None and match[0] is row


def test_a_media_server_row_with_an_unreachable_path_still_counts():
    """Plex/Jellyfin/Navidrome rows carry THEIR paths, which routinely do not
    resolve inside SoulSync's container. Treating absence as disproof would
    reject every legitimate match and re-download the library."""
    from types import SimpleNamespace

    from core.wishlist.library_match import find_owned_match

    row = SimpleNamespace(file_path="/mnt/plex-only/Artist/Album/01.mp3")
    db = SimpleNamespace(check_track_exists=lambda *a, **k: (row, 0.9))

    assert find_owned_match(db, "Song", [{"name": "Artist"}], "Album", "plex") is not None


# --- 8. the ordinary paths stay exactly as they were ------------------------

def test_a_direct_published_track_is_removed_immediately(monkeypatch, tmp_path):
    """With atomic publishing off — the default — nothing changes: the file is
    already at its library path when post-processing finishes, so the removal
    happens right there, as it always did."""
    seen = {}
    monkeypatch.setattr(pl, "check_and_remove_from_wishlist",
                        lambda ctx, **kw: seen.update(kw))

    final = tmp_path / "library" / "Artist" / "Album" / "01.mp3"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"AUDIO")

    assert pl._settle_wishlist_for_completed_track(_context(), str(final)) is False
    assert seen["published_path"] == str(final)


def test_999s_guarantee_survives_the_fix(monkeypatch, tmp_path):
    """The whole point of #999: nothing of a staged album is visible to the
    media server until the batch publishes. Deferring the wishlist removal must
    not have moved a single byte earlier — the library album folder stays empty
    while tracks finish, and the album appears all at once."""
    from core.downloads.atomic_album_publish import publish_album_batch
    from core.imports.file_ops import safe_move_file

    batch = {"is_album_download": True}
    transfer, staged_a, final_a = _stage_a_track(
        monkeypatch, tmp_path, batch, name="05 - Never There.mp3")
    _t, staged_b, final_b = _stage_a_track(
        monkeypatch, tmp_path, batch, name="06 - Same Direction.mp3")

    pl._settle_wishlist_for_completed_track(_context(), staged_a)
    pl._settle_wishlist_for_completed_track(_context(), staged_b)

    album_folder = os.path.dirname(final_a)
    assert not os.path.isdir(album_folder) or not os.listdir(album_folder), (
        "a staged album must be invisible to the media server until it publishes")

    publish_album_batch(batch["_atomic_staging_root"], transfer, safe_move_file)
    assert sorted(os.listdir(album_folder)) == [
        "05 - Never There.mp3", "06 - Same Direction.mp3"]


def test_staging_paths_are_recognised_without_any_batch_state(tmp_path):
    """Everything above defers on path shape alone, because batch state is
    exactly what does not survive the failures this guards against."""
    staged = "/library/.soulsync_atomic_staging/B/Artist/Album/01.mp3"
    assert contains_staging_segment(staged) is True
    assert split_staged_path(staged) == (
        "/library", "/library/.soulsync_atomic_staging/B", "Artist/Album/01.mp3")
    assert contains_staging_segment("/library/Artist/Album/01.mp3") is False


# --- 9. review follow-ups (#1289 review pass) ------------------------------

def test_metadata_fallback_survives_the_same_track_on_two_profiles():
    """The sweep returns one row PER PROFILE, so a track two profiles both want
    is two rows and one request. Counting rows made the fallback call every such
    match ambiguous, so it cleared nothing and the track was re-downloaded on
    every wishlist cycle."""
    from core.wishlist.resolution import _match_by_metadata

    rows = [
        {"name": "Song A", "artists": [{"name": "Band"}], "track_id": "sp-1", "_profile_id": 1},
        {"name": "Song A", "artists": [{"name": "Band"}], "track_id": "sp-1", "_profile_id": 2},
    ]
    assert _match_by_metadata(rows, "Song A", "Band", "") == "sp-1"


def test_metadata_fallback_does_not_veto_a_lone_candidate_on_album():
    """A wishlist row stored from one release against a download resolved from
    another is ordinary. With a single candidate there is no wrong request to
    pick, so the album cannot justify refusing."""
    from core.wishlist.resolution import _match_by_metadata

    rows = [{"name": "Song A", "artists": [{"name": "Band"}], "track_id": "sp-1",
             "album": {"name": "Album"}}]
    assert _match_by_metadata(rows, "Song A", "Band", "Album (Deluxe Edition)") == "sp-1"


def test_metadata_fallback_uses_the_album_to_break_a_real_tie():
    from core.wishlist.resolution import _match_by_metadata

    rows = [
        {"name": "S", "artists": [{"name": "B"}], "track_id": "a", "album": {"name": "One"}},
        {"name": "S", "artists": [{"name": "B"}], "track_id": "b", "album": {"name": "Two"}},
    ]
    assert _match_by_metadata(rows, "S", "B", "Two") == "b"
    # ...and still refuses when the album cannot separate them
    assert _match_by_metadata(rows, "S", "B", "") is None
    assert _match_by_metadata(rows, "S", "B", "Three") is None


def test_artist_names_handles_a_bare_string():
    """`artists` is sometimes a plain string; indexing it yields a single
    character, which is what used to land in the manifest."""
    from core.wishlist.library_match import artist_names

    assert artist_names("Band") == ["Band"]
    assert artist_names([{"name": "Band"}]) == ["Band"]
    assert artist_names(None) == []


def test_a_leftover_manifest_temp_file_is_never_published(tmp_path):
    """The manifest is replaced through a sibling temp file. A hard kill between
    mkstemp and os.replace leaves one behind, and matching only the exact
    manifest name would publish it into the user's library root."""
    import tempfile

    from core.downloads.atomic_album_publish import MANIFEST_NAME, iter_staged_files

    root = tmp_path / ".soulsync_atomic_staging" / "b"
    root.mkdir(parents=True)
    fd, _tmp = tempfile.mkstemp(prefix=MANIFEST_NAME, suffix=".tmp", dir=str(root))
    os.close(fd)
    (root / "01.mp3").write_bytes(b"AUDIO")

    assert [os.path.basename(p) for p in iter_staged_files(str(root))] == ["01.mp3"]


def test_publish_removes_manifest_leftovers_so_the_tree_can_prune(tmp_path):
    import tempfile

    from core.downloads.atomic_album_publish import MANIFEST_NAME, publish_album_batch
    from core.imports.file_ops import safe_move_file

    transfer = tmp_path / "library"
    root = transfer / ".soulsync_atomic_staging" / "b"
    (root / "Artist" / "Album").mkdir(parents=True)
    (root / "Artist" / "Album" / "01.mp3").write_bytes(b"AUDIO")
    fd, _tmp = tempfile.mkstemp(prefix=MANIFEST_NAME, suffix=".tmp", dir=str(root))
    os.close(fd)

    assert publish_album_batch(str(root), str(transfer), safe_move_file)["success"] is True
    assert not os.path.exists(root), "a leftover temp must not keep the tree alive"
    assert os.path.isfile(transfer / "Artist" / "Album" / "01.mp3")


def test_publish_repoints_each_task_final_file_path(monkeypatch, tmp_path):
    """Tasks record where their import landed, which for a staged batch is the
    path this publish just emptied. Playlist materialization, the downloads API
    and the stuck-task resolver all read that field."""
    from core.downloads.atomic_album_publish import publish_album_batch
    from core.imports.file_ops import safe_move_file

    batch = {"is_album_download": True}
    transfer, staged, final = _stage_a_track(monkeypatch, tmp_path, batch)
    tasks = {"t1": {"status": "completed", "final_file_path": staged}}
    monkeypatch.setattr(lc, "download_tasks", tasks)
    monkeypatch.setattr(lc, "safe_move_file", safe_move_file, raising=False)
    batch["queue"] = ["t1"]

    monkeypatch.setattr("core.downloads.atomic_album_publish.publish_album_batch",
                        publish_album_batch)
    assert lc._publish_atomic_album("B", batch) is True
    assert tasks["t1"]["final_file_path"] == final
    assert os.path.isfile(tasks["t1"]["final_file_path"])


def test_split_staged_path_refuses_a_path_with_no_library_above_it():
    """Reporting os.sep as the library would map every file into the filesystem
    root."""
    assert split_staged_path(".soulsync_atomic_staging/b/A/01.mp3") is None


def test_transfer_dir_for_returns_none_not_the_string_none():
    from core.downloads.atomic_recovery import _transfer_dir_for

    assert _transfer_dir_for("/nope", None, []) is None


def test_a_superseded_track_still_clears_its_wishlist_row(tmp_path):
    """Its staged copy went to the recycle bin, but the library holds the track
    at the final path, so the request really is satisfied."""
    from core.wishlist.resolution import remove_published_wishlist_entries

    live = tmp_path / "library" / "A" / "Album"
    live.mkdir(parents=True)
    kept = live / "01.mp3"
    kept.write_bytes(b"LIBRARY COPY")

    cleared = []
    import core.wishlist.resolution as res
    orig = res.check_and_remove_from_wishlist
    res.check_and_remove_from_wishlist = lambda ctx, **kw: cleared.append(kw["published_path"]) or True
    try:
        n = remove_published_wishlist_entries(
            [{"staged_path": "/staged/01.mp3", "final_path": str(kept),
              "track_name": "T", "context": {}}],
            {},  # empty pubmap: this file was superseded, not published
            batch_id="B")
    finally:
        res.check_and_remove_from_wishlist = orig

    assert n == 1 and cleared == [str(kept)]


def test_completed_sweep_refuses_a_staged_path():
    """'completed' is a task state, not a library state."""
    from core.wishlist import processing

    staged = "/lib/.soulsync_atomic_staging/b/A/01.mp3"
    calls = []
    processing.remove_completed_tracks_from_wishlist(
        {"queue": ["a"]},
        {"a": {"status": "completed", "track_info": {"name": "S"},
               "final_file_path": staged}},
        lambda ctx, **kw: calls.append(kw["published_path"]),
        logger=_SilentLogger(),
    )
    # It hands the guard the path it recorded, and that path is staged — so the
    # sweep can no longer clear a row for a file the media server cannot see.
    assert calls == [staged]
    assert guard.may_remove(staged) == (False, guard.STAGED)


class _SilentLogger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


def test_removal_is_scoped_to_the_profiles_whose_library_holds_the_file(monkeypatch, tmp_path):
    """One profile on the shared library, one with its own. A download into the
    shared folder must not clear the own-library profile's request — it cannot
    see that file (#1199)."""
    from types import SimpleNamespace

    import core.imports.paths as paths
    from core.wishlist import resolution

    shared = tmp_path / "shared"
    own = tmp_path / "own"
    for d in (shared, own):
        d.mkdir()
    published = shared / "A" / "Album" / "01.mp3"
    published.parent.mkdir(parents=True)
    published.write_bytes(b"AUDIO")

    monkeypatch.setattr(paths, "shared_transfer_root", lambda: str(shared))
    announce_flags = []

    def _root(pid, **kw):
        announce_flags.append(kw.get("announce", True))
        return str(own) if pid == 2 else None

    monkeypatch.setattr(paths, "library_root_for_profile", _root)

    seen = {}

    class _Svc:
        def mark_track_download_result(self, track_id, success, **kw):
            seen.update(kw)
            return True

    resolution.check_and_remove_from_wishlist(
        {"source": "spotify", "track_info": {"id": "sp-1", "name": "S",
                                             "artists": [{"name": "B"}]}},
        wishlist_service=_Svc(),
        database=SimpleNamespace(get_all_profiles=lambda: [{"id": 1}, {"id": 2}]),
        published_path=str(published),
    )

    assert seen["profile_ids"] == [1], "only the shared-library profile owns this file"
    # a wishlist lookup isn't routing a download, so it must not fire the
    # "own library inactive" warning + notification on every removal
    assert announce_flags and not any(announce_flags)


class _RecordingLogger:
    def __init__(self):
        self.calls = []

    def debug(self, msg, *args, **kw):
        self.calls.append(("debug", msg % args if args else msg))

    def info(self, msg, *args, **kw):
        self.calls.append(("info", msg % args if args else msg))

    def warning(self, msg, *args, **kw):
        self.calls.append(("warning", msg % args if args else msg))

    def error(self, msg, *args, **kw):
        self.calls.append(("error", msg % args if args else msg))


@pytest.mark.parametrize("quiet, level", [(True, "debug"), (False, "warning")])
def test_backstop_refusals_log_quietly(monkeypatch, quiet, level):
    """batch completion re-checks every finished track after the import already
    settled it. its refusals are expected, so they log at debug, while a direct
    refusal still warns."""
    from core.wishlist import resolution

    rec = _RecordingLogger()
    monkeypatch.setattr(resolution, "logger", rec)

    class _Svc:
        def mark_track_download_result(self, *a, **kw):
            raise AssertionError("a refused removal must not touch the wishlist")

    removed = resolution.check_and_remove_from_wishlist(
        {"track_info": {"id": "sp-1", "name": "S", "artists": [{"name": "B"}]}},
        wishlist_service=_Svc(),
        published_path=None,
        quiet_refusal=quiet,
    )

    assert removed is False
    refusals = [lvl for lvl, msg in rec.calls if "removal refused" in msg]
    assert refusals == [level]

