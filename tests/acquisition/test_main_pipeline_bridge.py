import shutil
import sqlite3
from pathlib import Path

import pytest

from core.acquisition import ensure_acquisition_schema
from core.acquisition.imports import (
    get_import,
    record_inventory_result,
    record_matching_result,
)
from core.acquisition import main_pipeline_bridge as MPB
from core.acquisition.main_pipeline_bridge import (
    _stage_working_copy,
    dispatch_import_to_main_pipeline,
)
from core.runtime_state import download_tasks, tasks_lock
from tests.acquisition.test_bundle_inventory import _pending_import

def _connection_factory(path: Path):
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    return connect


def _seed_import(path: Path, source_root: Path):
    factory = _connection_factory(path)
    conn = factory()
    ensure_acquisition_schema(conn)
    conn.execute(
        "CREATE TABLE lib2_artists(id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    conn.execute(
        """CREATE TABLE lib2_albums(
               id INTEGER PRIMARY KEY, primary_artist_id INTEGER NOT NULL,
               title TEXT NOT NULL, album_type TEXT, release_date TEXT,
               spotify_id TEXT)""")
    conn.execute(
        """CREATE TABLE lib2_tracks(
               id INTEGER PRIMARY KEY, album_id INTEGER NOT NULL,
               title TEXT NOT NULL, track_number INTEGER, disc_number INTEGER,
               duration INTEGER, spotify_id TEXT)""")
    conn.execute(
        """CREATE TABLE lib2_track_files(
               id INTEGER PRIMARY KEY, track_id INTEGER NOT NULL,
               path TEXT NOT NULL, file_state TEXT DEFAULT 'active')""")
    conn.execute("INSERT INTO lib2_artists VALUES(301, 'Artist')")
    conn.execute(
        "INSERT INTO lib2_albums VALUES(201, 301, 'Album', 'album', '2024', NULL)")
    conn.execute(
        "INSERT INTO lib2_tracks VALUES(101, 201, 'Song', 1, 1, 180000, NULL)")
    pending, request, _candidate = _pending_import(
        conn, output_path=str(source_root))
    record_inventory_result(
        conn,
        pending.id,
        [{"relative_path": "01.flac", "size_bytes": 5}],
        resolved_path=str(source_root),
    )
    importing = record_matching_result(
        conn,
        pending.id,
        [{
            "relative_path": "01.flac",
            "track_id": 101,
            "track_number": 1,
            "disc_number": 1,
        }],
        [],
        decision="import_ready",
    )
    conn.commit()
    conn.close()
    return factory, importing, request


def test_dispatch_uses_main_pipeline_context_and_persistent_callback(tmp_path):
    source_root = tmp_path / "client"
    source_root.mkdir()
    (source_root / "01.flac").write_bytes(b"audio")
    factory, importing, request = _seed_import(
        tmp_path / "db.sqlite", source_root)
    transfer = tmp_path / "transfer"
    captured = {}

    def processor(context_key, context, staged_path, task_id, batch_id, runtime):
        captured.update(context)
        assert context_key.startswith("acquisition_")
        assert batch_id is None
        assert Path(staged_path).is_file()
        final_path = str(tmp_path / "library" / "01.flac")
        context["_final_processed_path"] = final_path
        # What the real pipeline does on the way out: register the published
        # file in the catalogue, then raise its own success flag last.
        conn = factory()
        conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, file_state)"
            " VALUES(101, ?, 'active')", (final_path,))
        conn.commit()
        conn.close()
        context["_pipeline_import_succeeded"] = True
        with tasks_lock:
            assert download_tasks[task_id]["track_info"]["quality_profile_id"] == 2
            assert download_tasks[task_id]["_user_manual_pick"] is True
            download_tasks[task_id]["status"] = "completed"

    result = dispatch_import_to_main_pipeline(
        factory,
        importing.id,
        config_get=lambda key, default=None: (
            str(transfer) if key == "soulseek.transfer_path" else default),
        processor=processor,
        runtime=object(),
        copier=lambda source, destination: bool(shutil.copy2(source, destination)),
    )

    assert result.dispatched == ("01.flac",)
    assert captured["lib2_entity"] == {
        "track_id": 101,
        "album_id": 201,
        "quality_profile_id": 2,
    }
    assert captured["track_info"]["lib2_entity"] == captured["lib2_entity"]
    assert captured["_acquisition_import_id"] == importing.id
    conn = factory()
    assert get_import(conn, importing.id).status == "completed"
    assert conn.execute(
        "SELECT status FROM acquisition_requests WHERE id=?",
        (request.id,),
    ).fetchone()[0] == "completed"
    conn.close()


def test_quarantined_dispatch_stays_open_for_existing_approve_flow(tmp_path):
    source_root = tmp_path / "client"
    source_root.mkdir()
    (source_root / "01.flac").write_bytes(b"audio")
    factory, importing, _request = _seed_import(
        tmp_path / "db.sqlite", source_root)
    task_ids = []

    def processor(_key, context, _path, task_id, _batch_id, _runtime):
        context["_acoustid_quarantined"] = True
        task_ids.append(task_id)
        with tasks_lock:
            download_tasks[task_id]["status"] = "failed"

    result = dispatch_import_to_main_pipeline(
        factory,
        importing.id,
        config_get=lambda key, default=None: (
            str(tmp_path / "transfer")
            if key == "soulseek.transfer_path" else default),
        processor=processor,
        runtime=object(),
        copier=lambda source, destination: bool(shutil.copy2(source, destination)),
    )

    assert result.waiting == ("01.flac",)
    conn = factory()
    assert get_import(conn, importing.id).status == "importing"
    conn.close()
    with tasks_lock:
        for task_id in task_ids:
            download_tasks.pop(task_id, None)


def test_existing_working_copy_is_reused_only_when_content_matches(tmp_path):
    source = tmp_path / "source" / "same.flac"
    source.parent.mkdir()
    source.write_bytes(b"same-content")
    transfer = tmp_path / "transfer"
    transfer.mkdir()
    destination = transfer / "import-1_101_same.flac"
    destination.write_bytes(b"same-content")

    staged = _stage_working_copy(
        source,
        transfer_dir=str(transfer),
        import_id="import-1",
        track_id=101,
        copier=lambda *_args: (_ for _ in ()).throw(
            AssertionError("matching content must not be copied again")
        ),
    )

    assert staged == str(destination)


def test_existing_same_size_working_copy_with_other_content_is_rejected(tmp_path):
    source = tmp_path / "source" / "collision.flac"
    source.parent.mkdir()
    source.write_bytes(b"track-one")
    transfer = tmp_path / "transfer"
    transfer.mkdir()
    destination = transfer / "import-2_202_collision.flac"
    destination.write_bytes(b"track-two")

    with pytest.raises(ValueError, match="different content"):
        _stage_working_copy(
            source,
            transfer_dir=str(transfer),
            import_id="import-2",
            track_id=202,
            copier=lambda *_args: True,
        )


def test_a_rejection_after_path_planning_is_not_journalled_as_success(tmp_path):
    """FI-01: the pipeline writes `_final_processed_path` while it is still
    only *planning* where the file will go. An upgrade rejected after that
    point quarantines the working file and fails the task — but the bridge used
    to read the planned path as proof of publication and close the import, the
    acquisition request and the retry state as `completed`, dropping the file
    from `result.quarantined`. Nothing at the destination, and no way back in:
    `advance_import` prefers that persisted completion over `waiting`."""
    source_root = tmp_path / "client"
    source_root.mkdir()
    (source_root / "01.flac").write_bytes(b"audio")
    factory, importing, request = _seed_import(tmp_path / "db.sqlite", source_root)

    def processor(_key, context, _path, task_id, _batch_id, _runtime):
        # Path planning happened; the upgrade decision then rejected the file.
        context["_final_processed_path"] = str(tmp_path / "library" / "01.flac")
        context["_upgrade_rejected"] = True
        context["_quarantine_entry_id"] = "quarantine-1"
        with tasks_lock:
            download_tasks[task_id]["status"] = "failed"

    result = dispatch_import_to_main_pipeline(
        factory,
        importing.id,
        config_get=lambda key, default=None: (
            str(tmp_path / "transfer") if key == "soulseek.transfer_path" else default),
        processor=processor,
        runtime=object(),
        copier=lambda source, destination: bool(shutil.copy2(source, destination)),
    )

    assert result.dispatched == ()
    assert result.waiting == ("01.flac",)
    conn = factory()
    assert get_import(conn, importing.id).status == "importing"
    assert conn.execute(
        "SELECT status FROM acquisition_requests WHERE id=?", (request.id,),
    ).fetchone()[0] != "completed"
    conn.close()


def test_a_move_that_never_registered_the_file_is_not_a_completion(tmp_path):
    """The other half of FI-01: no rejection markers and the task even reports
    completed, but the catalogue has no active row for the destination. That is
    a move that did not publish, and it must not close the import."""
    source_root = tmp_path / "client"
    source_root.mkdir()
    (source_root / "01.flac").write_bytes(b"audio")
    factory, importing, _request = _seed_import(tmp_path / "db.sqlite", source_root)

    def processor(_key, context, _path, task_id, _batch_id, _runtime):
        context["_final_processed_path"] = str(tmp_path / "library" / "01.flac")
        context["_pipeline_import_succeeded"] = True
        with tasks_lock:
            download_tasks[task_id]["status"] = "completed"

    dispatch_import_to_main_pipeline(
        factory,
        importing.id,
        config_get=lambda key, default=None: (
            str(tmp_path / "transfer") if key == "soulseek.transfer_path" else default),
        processor=processor,
        runtime=object(),
        copier=lambda source, destination: bool(shutil.copy2(source, destination)),
    )

    conn = factory()
    assert get_import(conn, importing.id).status == "importing"
    conn.close()


def test_the_dispatched_task_carries_the_sealed_upgrade_intent(tmp_path):
    """ACQ-02: `_pipeline_context` seals the upgrade intent onto the context,
    but the candidate dispatch and staging consumers read it off the TASK. The
    first pipeline call had it and the quarantine-retry task did not, so a
    replacement import ran without the track lock, the profile snapshot and the
    comparison against the existing primary file — and a real FLAC→FLAC upgrade
    met the ordinary same-format overwrite guard and was discarded."""
    from core.imports.upgrade_intent import CONTEXT_KEY, get_upgrade_intent

    source_root = tmp_path / "client"
    source_root.mkdir()
    (source_root / "01.flac").write_bytes(b"audio")
    factory, importing, _request = _seed_import(tmp_path / "db.sqlite", source_root)
    seen = {}

    def processor(_key, context, _path, task_id, _batch_id, _runtime):
        with tasks_lock:
            seen["task"] = dict(download_tasks[task_id])
        seen["context_intent"] = get_upgrade_intent(context)

    dispatch_import_to_main_pipeline(
        factory,
        importing.id,
        config_get=lambda key, default=None: (
            str(tmp_path / "transfer") if key == "soulseek.transfer_path" else default),
        processor=processor,
        runtime=object(),
        copier=lambda source, destination: bool(shutil.copy2(source, destination)),
    )

    assert seen["context_intent"] is not None
    assert seen["task"].get(CONTEXT_KEY) is seen["context_intent"]
    assert seen["task"][CONTEXT_KEY].track_id == 101


def test_our_own_retagged_working_copy_is_replaced_instead_of_blocking_forever(tmp_path):
    """FI-03: the pipeline tags this working copy in place. A crash between
    tagging and the move left a copy that no longer matched the untouched
    download original, and the reuse check compared it against nothing else —
    so every resumed dispatch raised "different content" forever while the
    complete original sat right there. The import stayed open with no way out."""
    import shutil as _shutil

    source = tmp_path / "source" / "song.flac"
    source.parent.mkdir()
    source.write_bytes(b"original-bytes")
    transfer = tmp_path / "transfer"

    first = _stage_working_copy(
        source, transfer_dir=str(transfer), import_id="import-3", track_id=303,
        copier=lambda src, dst: bool(_shutil.copy2(src, dst)))
    # The pipeline enhances metadata on its own copy.
    Path(first).write_bytes(b"retagged-bytes!")

    second = _stage_working_copy(
        source, transfer_dir=str(transfer), import_id="import-3", track_id=303,
        copier=lambda src, dst: bool(_shutil.copy2(src, dst)))

    assert second == first
    assert Path(second).read_bytes() == b"original-bytes"


def test_a_foreign_file_on_the_working_path_still_collides(tmp_path):
    """The collision check is not relaxed: only a copy this import provably
    wrote may be discarded and re-staged."""
    source = tmp_path / "source" / "song.flac"
    source.parent.mkdir()
    source.write_bytes(b"original-bytes")
    transfer = tmp_path / "transfer"
    transfer.mkdir()
    foreign = transfer / "import-4_404_song.flac"
    foreign.write_bytes(b"somebody-elses")

    with pytest.raises(ValueError, match="different content"):
        _stage_working_copy(
            source, transfer_dir=str(transfer), import_id="import-4",
            track_id=404, copier=lambda *_args: True)
    assert foreign.read_bytes() == b"somebody-elses"


def test_another_imports_marker_does_not_authorise_a_replacement(tmp_path):
    """A provenance marker only speaks for the import and track that wrote it."""
    import json

    source = tmp_path / "source" / "song.flac"
    source.parent.mkdir()
    source.write_bytes(b"original-bytes")
    transfer = tmp_path / "transfer"
    transfer.mkdir()
    destination = transfer / "import-5_505_song.flac"
    destination.write_bytes(b"someone-elses")
    (transfer / ("import-5_505_song.flac" + MPB.PROVENANCE_SUFFIX)).write_text(
        json.dumps({"import_id": "a-different-import", "track_id": 505}))

    with pytest.raises(ValueError, match="different content"):
        _stage_working_copy(
            source, transfer_dir=str(transfer), import_id="import-5",
            track_id=505, copier=lambda *_args: True)
