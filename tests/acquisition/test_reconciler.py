"""Evidence-first repair of persistent legacy acquisition correlations."""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.acquisition import ensure_acquisition_schema
from core.acquisition.candidates import register_candidate
from core.acquisition.grabs import get_grab, record_grab, update_grab
from core.acquisition.reconciler import reconcile_persistent_grabs
from core.acquisition.requests import create_request, get_request, transition_request


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    ensure_acquisition_schema(connection)
    connection.executescript(
        """
        CREATE TABLE tracks(id INTEGER PRIMARY KEY, file_path TEXT);
        CREATE TABLE lib2_track_files(
            id INTEGER PRIMARY KEY, track_id INTEGER, path TEXT, file_state TEXT
        );
        """
    )
    yield connection
    connection.close()


def _grab(conn, download_id="scheduled-1", *, updated_at=None):
    request, _ = create_request(
        conn,
        profile_id=1,
        scope="recording",
        entity_id=10,
        quality_profile_id=1,
        trigger="scheduled",
        idempotency_key=f"scheduled-grab:{download_id}",
        search_options={"legacy_task_id": "task-1", "lib2_track_id": 99},
    )
    transition_request(conn, request.id, "searching")
    candidate, _ = register_candidate(
        conn,
        request_id=request.id,
        source="soulseek",
        protocol="p2p",
        content_scope="recording",
        server_ref=f"ref:{download_id}",
        title="Track",
    )
    transition_request(conn, request.id, "candidates_ready")
    transition_request(conn, request.id, "grabbing")
    record_grab(
        conn,
        download_id,
        "soulseek",
        acquisition_request_id=request.id,
        release_candidate_id=candidate.id,
        context={"legacy_task_id": "task-1", "legacy_download_id": "transfer-1"},
        status="downloading",
    )
    update_grab(
        conn, download_id, last_client_state="legacy_dispatched",
    )
    if updated_at:
        conn.execute(
            "UPDATE acquisition_grabs SET updated_at=? WHERE download_id=?",
            (updated_at, download_id),
        )
    conn.commit()
    return request


def test_dry_run_reports_terminal_runtime_evidence_without_mutating(conn, tmp_path):
    request = _grab(conn)
    final = tmp_path / "done.flac"
    final.write_bytes(b"audio")
    conn.execute(
        "INSERT INTO lib2_track_files(track_id,path,file_state) VALUES(99,?,'active')",
        (str(final),),
    )

    report = reconcile_persistent_grabs(
        conn,
        runtime_tasks={
            "task-1": {"status": "completed", "final_file_path": str(final)},
        },
        dry_run=True,
    )

    assert report.applied == 0
    assert report.counts["runtime_completed_indexed"] == 1
    assert get_request(conn, request.id).status == "grabbing"
    assert get_grab(conn, "scheduled-1")["status"] == "downloading"


def test_apply_completes_only_when_runtime_file_is_real_and_indexed(conn, tmp_path):
    request = _grab(conn)
    final = tmp_path / "done.flac"
    final.write_bytes(b"audio")
    conn.execute("INSERT INTO tracks(id,file_path) VALUES(1,?)", (str(final),))

    report = reconcile_persistent_grabs(
        conn,
        runtime_tasks={
            "task-1": {"status": "completed", "final_file_path": str(final)},
        },
        dry_run=False,
    )
    conn.commit()

    assert report.applied == 1
    assert get_request(conn, request.id).status == "completed"
    assert get_grab(conn, "scheduled-1")["status"] == "completed"
    event = conn.execute(
        "SELECT event_type, reason_code FROM acquisition_history WHERE download_id=?",
        ("scheduled-1",),
    ).fetchone()
    assert dict(event) == {
        "event_type": "grab_completed",
        "reason_code": "runtime_completed_indexed",
    }


def test_completed_but_unindexed_client_file_stays_open(conn, tmp_path):
    request = _grab(conn)
    final = tmp_path / "orphan.flac"
    final.write_bytes(b"audio")

    report = reconcile_persistent_grabs(
        conn,
        client_observations={
            "transfer-1": {"state": "Completed, Succeeded", "file_path": str(final)},
        },
        dry_run=False,
    )

    assert report.applied == 0
    assert report.counts["client_completed_unindexed"] == 1
    assert get_request(conn, request.id).status == "grabbing"


def test_quarantine_sidecar_wins_over_age_and_keeps_reviewable_state(conn):
    request = _grab(conn, updated_at="2020-01-01 00:00:00")

    report = reconcile_persistent_grabs(
        conn,
        quarantine_entries=[{
            "id": "q-1",
            "context": {"_acquisition_grab_download_id": "scheduled-1"},
        }],
        dry_run=False,
        now=2_000_000_000,
        evidence_ttl_seconds=3600,
    )
    conn.commit()

    assert report.counts["quarantine_review_pending"] == 1
    assert get_request(conn, request.id).status == "grabbing"
    grab = get_grab(conn, "scheduled-1")
    assert grab["status"] == "downloading"
    assert grab["last_client_state"] == "quarantined"
    assert grab["context"]["quarantine_entry_id"] == "q-1"


def test_evidence_less_expiry_is_runtime_failure_without_blocklist(conn):
    request = _grab(conn, updated_at="2020-01-01 00:00:00")

    report = reconcile_persistent_grabs(
        conn,
        dry_run=False,
        now=2_000_000_000,
        evidence_ttl_seconds=3600,
    )
    conn.commit()

    assert report.counts["evidence_ttl_expired"] == 1
    assert get_request(conn, request.id).status == "failed"
    assert get_grab(conn, "scheduled-1")["status"] == "failed"
    assert conn.execute("SELECT COUNT(*) FROM release_blocklist").fetchone()[0] == 0
    history = conn.execute(
        "SELECT reason_code, payload_json FROM acquisition_history WHERE event_type='grab_failed'"
    ).fetchone()
    assert history["reason_code"] == "runtime_failure"
    assert json.loads(history["payload_json"])["failure_kind"] == "runtime"


def test_active_client_snapshot_refreshes_business_state(conn):
    request = _grab(conn)

    report = reconcile_persistent_grabs(
        conn,
        client_observations={"transfer-1": {"state": "InProgress, Downloading"}},
        dry_run=False,
    )
    conn.commit()

    assert report.counts["client_active"] == 1
    assert get_request(conn, request.id).status == "grabbing"
    assert get_grab(conn, "scheduled-1")["last_client_state"] == (
        "inprogress, downloading"
    )

    unchanged = reconcile_persistent_grabs(
        conn,
        client_observations={"transfer-1": {"state": "InProgress, Downloading"}},
        dry_run=False,
    )
    assert unchanged.applied == 0
    assert unchanged.decisions[0].action == "none"


def test_path_mapped_index_is_valid_completion_evidence(conn, tmp_path, monkeypatch):
    request = _grab(conn)
    real = tmp_path / "mapped.flac"
    real.write_bytes(b"audio")
    stored = "/media-server/Artist/mapped.flac"
    conn.execute(
        "INSERT INTO lib2_track_files(track_id,path,file_state) VALUES(99,?,'active')",
        (stored,),
    )
    monkeypatch.setattr(
        "core.library2.paths.resolve_lib2_path",
        lambda path, config_manager=None: str(real) if path == stored else None,
    )

    report = reconcile_persistent_grabs(
        conn,
        runtime_tasks={
            "task-1": {"status": "completed", "final_file_path": stored},
        },
        dry_run=False,
    )

    assert report.applied == 1
    assert get_request(conn, request.id).status == "completed"


def test_completed_import_requires_real_indexed_processed_files(conn, tmp_path):
    request = _grab(conn)
    grab = get_grab(conn, "scheduled-1")
    missing = tmp_path / "gone.flac"
    conn.execute(
        """INSERT INTO acquisition_imports(
               id,download_id,request_id,candidate_id,status,output_path,
               expected_scope,expected_entity_id,result_json)
           VALUES('import-1','scheduled-1',?,?,'completed',?, 'recording',10,?)""",
        (
            request.id,
            grab["release_candidate_id"],
            str(tmp_path),
            json.dumps({"processed": [{"final_path": str(missing)}]}),
        ),
    )

    report = reconcile_persistent_grabs(conn, dry_run=False)

    assert report.applied == 0
    assert report.counts["acquisition_import_completed_unindexed"] == 1
    assert get_request(conn, request.id).status == "grabbing"

    missing.write_bytes(b"audio")
    conn.execute(
        "INSERT INTO lib2_track_files(track_id,path,file_state) VALUES(99,?,'active')",
        (str(missing),),
    )
    report = reconcile_persistent_grabs(conn, dry_run=False)

    assert report.applied == 1
    assert report.counts["acquisition_import_completed_indexed"] == 1
    assert get_request(conn, request.id).status == "completed"
