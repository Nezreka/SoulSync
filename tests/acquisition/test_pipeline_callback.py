import sqlite3

from core.acquisition import ensure_acquisition_schema
from core.acquisition.history import list_history_events
from core.acquisition.imports import (
    get_import,
    record_inventory_result,
    record_matching_result,
    record_pipeline_file_completed,
)
from core.acquisition.requests import get_request
from core.imports.quarantine import serialize_quarantine_context
from tests.acquisition.test_bundle_inventory import _pending_import


def _importing_record(conn):
    pending, request, _candidate = _pending_import(conn)
    inventory = [
        {"relative_path": "01.flac", "size_bytes": 10},
        {"relative_path": "02.flac", "size_bytes": 20},
    ]
    record_inventory_result(
        conn, pending.id, inventory, resolved_path="/resolved")
    importing = record_matching_result(
        conn,
        pending.id,
        [
            {"relative_path": "01.flac", "track_id": 101},
            {"relative_path": "02.flac", "track_id": 102},
        ],
        [],
        decision="import_ready",
    )
    return importing, request


def test_main_pipeline_completes_import_only_after_every_match():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(conn)
    importing, request = _importing_record(conn)

    partial = record_pipeline_file_completed(
        conn,
        importing.id,
        relative_path="01.flac",
        final_path="/library/01.flac",
        track_id=101,
    )
    assert partial.status == "importing"
    assert get_request(conn, request.id).status == "grabbing"

    completed = record_pipeline_file_completed(
        conn,
        importing.id,
        relative_path="02.flac",
        final_path="/library/02.flac",
        track_id=102,
    )
    assert completed.status == "completed"
    assert get_request(conn, request.id).status == "completed"
    events = list_history_events(conn, request_id=request.id)
    assert [event.event_type for event in events].count("import_completed") == 1

    duplicate = record_pipeline_file_completed(
        conn,
        importing.id,
        relative_path="02.flac",
        final_path="/library/02.flac",
        track_id=102,
    )
    assert duplicate.status == "completed"
    conn.close()


def test_pipeline_completion_rejects_a_file_outside_persisted_matches():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(conn)
    importing, _request = _importing_record(conn)

    try:
        record_pipeline_file_completed(
            conn,
            importing.id,
            relative_path="other.flac",
            final_path="/library/other.flac",
            track_id=999,
        )
    except ValueError as exc:
        assert "persisted import plan" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unexpected completion was accepted")
    assert get_import(conn, importing.id).status == "importing"
    conn.close()


def test_quarantine_sidecar_preserves_acquisition_markers():
    context = {
        "_acquisition_import_id": "aim1-test",
        "_acquisition_relative_path": "Disc 1/01.flac",
        "_acquisition_track_id": 42,
        "track_info": {
            "quality_profile_id": 7,
            "_acquisition_import_id": "aim1-test",
        },
    }

    restored = serialize_quarantine_context(context)

    assert restored["_acquisition_import_id"] == "aim1-test"
    assert restored["_acquisition_relative_path"] == "Disc 1/01.flac"
    assert restored["_acquisition_track_id"] == 42
    assert restored["track_info"]["quality_profile_id"] == 7
