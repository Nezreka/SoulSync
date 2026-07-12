import shutil
import sqlite3
from pathlib import Path

from core.acquisition import ensure_acquisition_schema
from core.acquisition.imports import (
    get_import,
    record_inventory_result,
    record_matching_result,
)
from core.acquisition.main_pipeline_bridge import dispatch_import_to_main_pipeline
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
        context["_final_processed_path"] = str(tmp_path / "library" / "01.flac")
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
