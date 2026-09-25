"""A quality upgrade stays in the library it is for (#1199).

The primary flag is one per track across every library, so each step of an
upgrade -- is there a file, is it good enough, which one is replaced, where the
new one goes -- has to ask in the library the upgrade fills.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import types

import pytest

from core import library_scope
from tests.library2.test_two_libraries import lib  # noqa: F401 - the fixture
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track


@pytest.fixture
def pipeline_db(lib, monkeypatch):
    import core.imports.pipeline as pipeline
    monkeypatch.setattr(pipeline, "get_database", lambda: lib.db)
    return lib


def _track(db, *files, bitrate=None):
    """One track with a file at each path (mp3, the given bitrate)."""
    with db._get_connection() as conn:
        ar = seed_artist(conn, server_id="ar", name="A", server_source="soulsync")
        al = seed_album(conn, server_id="al", title="B", artist_id=ar, server_source="soulsync")
        tid = seed_track(conn, server_id="t", title="One", album_id=al, artist_id=ar,
                         server_source="soulsync")
        for path, rate in files:
            conn.execute(
                "INSERT INTO lib2_track_files(track_id, path, bitrate, format, server_source,"
                " file_state, import_status) VALUES(?,?,?,'mp3','soulsync','active','imported')",
                (tid, path, rate))
        conn.execute("UPDATE lib2_track_files SET is_primary = (id = (SELECT MIN(id)"
                     " FROM lib2_track_files WHERE track_id=?)) WHERE track_id=?", (tid, tid))
        conn.execute("UPDATE lib2_tracks SET quality_profile_id=2, quality_profile_explicit=1"
                     " WHERE id=?", (tid,))
        conn.commit()
    return tid


def test_kims_upgrade_is_measured_against_her_copy(pipeline_db):
    lib = pipeline_db
    import core.imports.pipeline as pipeline
    from core.library2.wishlist_mirror import track_wishlist_payload
    shared = os.path.join(lib.shared, "A", "B", "01.mp3")
    kims = os.path.join(lib.kim_root, "A", "B", "01.mp3")
    tid = _track(lib.db, (shared, 320), (kims, 128))  # the house's copy is primary
    with library_scope.library_scope(lib.kim):
        snap = pipeline._load_upgrade_snapshot(tid)
        with lib.db._get_connection() as conn:
            payload = track_wishlist_payload(conn, tid)
    assert snap.primary_path == kims
    assert payload["_source_info"]["original_file_path"] == kims
    # the profile row's JSON reaches every later stage parsed
    assert isinstance(snap.profile["ranked_targets"], list)


def test_a_track_only_the_house_has_is_missing_in_kims_library(pipeline_db):
    lib = pipeline_db
    from core.library2.wishlist_mirror import track_wishlist_payload
    tid = _track(lib.db, (os.path.join(lib.shared, "A", "B", "01.mp3"), 320))
    with lib.db._get_connection() as conn:
        with library_scope.library_scope(lib.kim):
            kims = track_wishlist_payload(conn, tid)
        with library_scope.library_scope("shared"):
            houses = track_wishlist_payload(conn, tid)
    assert kims["_has_file"] is False and kims["_should_queue"] is True
    assert not kims["_source_info"].get("upgrade_check")  # a download, not an upgrade
    assert houses["_has_file"] is True


def test_the_houses_wish_for_a_track_only_kim_has_replaces_nothing_of_hers(pipeline_db):
    lib = pipeline_db
    from core.library2.wishlist_mirror import track_wishlist_payload
    kims = os.path.join(lib.kim_root, "A", "B", "01.mp3")
    tid = _track(lib.db, (kims, 128))
    with lib.db._get_connection() as conn:
        with library_scope.library_scope("shared"):
            payload = track_wishlist_payload(conn, tid)
    assert payload["_has_file"] is False
    assert payload["_source_info"].get("original_file_path") != kims


def test_a_batched_import_keeps_its_library_past_the_wrapper(lib, monkeypatch):
    """The verification wrapper pops batch_id before the import asks where
    the file goes; the batch's library must still reach it."""
    import core.imports.paths as paths
    import core.imports.pipeline as import_pipeline
    from core.runtime_state import download_batches

    download_batches["kim-batch"] = {"profile_id": lib.kim, "queue": []}
    try:
        context = {"task_id": "t1", "batch_id": "kim-batch",
                   "track_info": {"wishlist_id": 7, "name": "One", "source_info": {}},
                   "spotify_artist": {"name": "A"}, "spotify_album": {"name": "B"}}
        seen = {}

        def inner(context_key, ctx, file_path, runtime, metadata_runtime=None):
            seen["owner"] = paths.import_owner_id(ctx)
            seen["root"] = paths.transfer_root_for_context(ctx)
        monkeypatch.setattr(import_pipeline, "post_process_matched_download", inner)
        runtime = types.SimpleNamespace(automation_engine=None, on_download_completed=None,
                                        web_scan_manager=None, repair_worker=None)
        try:
            import_pipeline.post_process_matched_download_with_verification(
                "k", context, "/nonexistent.flac", "t1", "kim-batch", runtime)
        except Exception:  # noqa: BLE001, S110 - only the inner call is under test
            pass
        assert seen["owner"] == lib.kim
        assert seen["root"] == lib.kim_root
    finally:
        download_batches.pop("kim-batch", None)


def test_an_own_folder_inside_the_shared_one_settles_only_its_wishlist(lib):
    from core.library2 import library_roots
    from core.wishlist import resolution
    nested = os.path.join(lib.shared, "kim")
    os.makedirs(nested)
    assert lib.db.set_profile_library(lib.kim, "own", nested)
    library_scope.invalidate_library_scope_cache()
    library_roots.sync_library_roots(lib.db)
    owners = resolution._profiles_owning_path(os.path.join(nested, "A", "01.flac"),
                                              database=lib.db)
    assert owners == [lib.kim]
    shared_owners = resolution._profiles_owning_path(
        os.path.join(lib.shared, "A", "01.flac"), database=lib.db)
    assert lib.kim not in shared_owners and 1 in shared_owners


def _tone(path, *args):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=4", *args, path], check=True)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_kims_upgrade_retires_her_copy_and_leaves_the_houses(pipeline_db, tmp_path, monkeypatch):
    """End to end through the real import with real audio: the verified
    upgrade runs (it used to die on the profile's JSON), lands in Kim's folder
    and replaces Kim's file -- not the house's, which was the global primary."""
    lib = pipeline_db
    import core.imports.pipeline as import_pipeline
    from core.imports.upgrade_intent import attach_upgrade_intent, issue_upgrade_intent
    from tests.imports.test_import_pipeline import _wire_post_process_common

    shared = os.path.join(lib.shared, "A", "B", "01.mp3")
    kims = os.path.join(lib.kim_root, "A", "B", "01.mp3")
    _tone(shared, "-b:a", "320k")
    _tone(kims, "-b:a", "128k")
    incoming = str(tmp_path / "downloads" / "in.flac")
    _tone(incoming, "-sample_fmt", "s32", "-ar", "192000")
    tid = _track(lib.db, (shared, 320), (kims, 128))
    target = os.path.join(lib.kim_root, "A", "B", "01.flac")
    _wire_post_process_common(monkeypatch, tmp_path, target, track_number=1,
                              is_album_download=False)
    monkeypatch.setattr(import_pipeline.time, "sleep", lambda _s: None)

    def move(src, dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.replace(src, dst)
    monkeypatch.setattr(import_pipeline, "safe_move_file", move)
    context = {
        "track_info": {"source_info": {"upgrade_check": True, "lib2_track_id": tid}},
        "original_search_result": {"title": "One", "album": "B"},
        "is_album_download": False,
        "_skip_quarantine_check": "all",
        "library_owner_id": lib.kim,
        "profile_id": lib.kim,
    }
    attach_upgrade_intent(context, issue_upgrade_intent(tid, origin="scoped_automatic_search"))
    runtime = types.SimpleNamespace(automation_engine=None, on_download_completed=None,
                                    web_scan_manager=None, repair_worker=None)
    with library_scope.library_scope(lib.kim):
        import_pipeline.post_process_matched_download("kim-upgrade", context, incoming, runtime)
    assert os.path.exists(target), (context.get("_context_failure_msg"),
                                    context.get("_upgrade_failure_msg"))
    assert context.get("_replaced_file_paths") == [kims]
    assert os.path.exists(shared)


def test_a_hand_tagged_file_is_never_queued_as_an_upgrade(pipeline_db):
    """ "tag it yourself": the user's bootleg is not the service's release; an
    upgrade would fetch the studio cut and replace it."""
    lib = pipeline_db
    from core.library2.wishlist_mirror import track_wishlist_payload
    path = os.path.join(lib.shared, "A", "B", "01.mp3")
    tid = _track(lib.db, (path, 128))
    lib.db.record_manual_metadata_file(path, "B", "A")
    with lib.db._get_connection() as conn:
        with library_scope.library_scope("shared"):
            payload = track_wishlist_payload(conn, tid)
    assert payload["_should_queue"] is False
    assert payload["_source_info"]["quality_evaluation"] == "hand_tagged"
