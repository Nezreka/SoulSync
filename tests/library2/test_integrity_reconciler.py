"""Read-only cross-index integrity reporting (LV2-013)."""

from __future__ import annotations

import json
import sqlite3

from core.acquisition import ensure_acquisition_schema
from core.acquisition.candidates import register_candidate
from core.acquisition.grabs import record_grab
from core.acquisition.requests import create_request, transition_request
from core.library2.integrity_reconciler import build_integrity_report
from core.library2.schema import ensure_library_v2_schema


class _Config:
    def __init__(self, roots=()):
        self.roots = list(roots)

    def get(self, key, default=None):
        return self.roots if key == "library.music_paths" else default


def _connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    ensure_acquisition_schema(conn)
    conn.execute(
        "CREATE TABLE tracks(id INTEGER PRIMARY KEY, file_path TEXT, server_source TEXT)"
    )
    conn.execute(
        "CREATE TABLE track_downloads(id INTEGER PRIMARY KEY, file_path TEXT)"
    )
    conn.execute(
        "CREATE TABLE library_history(id INTEGER PRIMARY KEY, file_path TEXT)"
    )
    return conn


def _track(conn, path, state="active"):
    artist_id = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('Artist','Artist')"
    ).lastrowid
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
        "VALUES(?, 'Album', 'album')",
        (artist_id,),
    ).lastrowid
    track_id = conn.execute(
        "INSERT INTO lib2_tracks(album_id,title) VALUES(?, 'Track')", (album_id,),
    ).lastrowid
    file_id = conn.execute(
        "INSERT INTO lib2_track_files(track_id,path,file_state) VALUES(?,?,?)",
        (track_id, str(path), state),
    ).lastrowid
    return track_id, file_id


def _linked_grab(conn, download_id="grab-1"):
    request, _ = create_request(
        conn,
        profile_id=1,
        scope="recording",
        entity_id=1,
        quality_profile_id=1,
        trigger="manual",
        idempotency_key=f"integrity-{download_id}",
    )
    transition_request(conn, request.id, "searching")
    candidate, _ = register_candidate(
        conn,
        request_id=request.id,
        source="soulseek",
        protocol="p2p",
        content_scope="recording",
        server_ref=download_id,
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
        status="downloading",
    )
    return request.id


def _codes(report):
    return [finding.code for finding in report.findings]


def test_report_compares_real_legacy_and_lib2_files_without_writes(tmp_path):
    conn = _connection()
    lib2_file = tmp_path / "lib2.flac"
    lib2_file.write_bytes(b"lib2")
    legacy_file = tmp_path / "legacy.flac"
    legacy_file.write_bytes(b"legacy")
    missing_file = tmp_path / "missing.flac"
    _track(conn, lib2_file)
    _track(conn, missing_file)
    conn.execute(
        "INSERT INTO tracks(file_path,server_source) VALUES(?, 'plex')",
        (str(legacy_file),),
    )
    conn.commit()
    before = conn.total_changes

    report = build_integrity_report(
        conn, config_manager=_Config([str(tmp_path)]),
    )

    assert conn.total_changes == before
    assert report.read_only is True
    assert report.coverage["destructive_actions"] is False
    assert "lib2_active_file_missing" in _codes(report)
    assert "legacy_only_indexed_file" in _codes(report)
    assert "lib2_only_indexed_file" in _codes(report)
    assert report.counts["severity:error"] == 1


def test_unhealthy_storage_never_confirms_missing(tmp_path):
    conn = _connection()
    _, file_id = _track(conn, tmp_path / "unmounted" / "track.flac")
    conn.commit()

    report = build_integrity_report(
        conn,
        config_manager=_Config([str(tmp_path / "unmounted")]),
    )

    finding = next(item for item in report.findings if item.entity == str(file_id))
    assert finding.code == "lib2_file_unresolved"
    assert finding.details["storage_root_healthy"] is False


def test_runtime_provenance_acquisition_and_quarantine_are_correlated(tmp_path):
    conn = _connection()
    request_id = _linked_grab(conn)
    conn.execute(
        "UPDATE acquisition_grabs SET status='completed' WHERE download_id='grab-1'"
    )
    orphan = tmp_path / "orphan.flac"
    orphan.write_bytes(b"orphan")
    conn.execute("INSERT INTO track_downloads(file_path) VALUES(?)", (str(orphan),))
    conn.execute("INSERT INTO library_history(file_path) VALUES(?)", (str(orphan),))
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / "20260718_120000_file.flac.quarantined").write_bytes(b"q")
    (quarantine / "sidecar_only.json").write_text("{}")
    conn.commit()
    before = conn.total_changes
    acquisition = {
        "dry_run": True,
        "decisions": [{
            "download_id": "grab-1", "action": "complete",
            "reason": "runtime_completed_indexed", "evidence": "download_task",
        }],
    }

    report = build_integrity_report(
        conn,
        runtime_tasks={
            "task-1": {"status": "completed", "final_file_path": str(orphan)},
        },
        matched_contexts=[{
            "_acquisition_grab_download_id": "grab-1",
            "_final_processed_path": str(orphan),
        }],
        quarantine_entries=[{"id": "thin", "context": {}}],
        quarantine_dir=str(quarantine),
        acquisition_report=acquisition,
        config_manager=_Config([str(tmp_path)]),
    )

    assert conn.total_changes == before
    assert request_id
    codes = set(_codes(report))
    assert {
        "provenance_unindexed_file",
        "runtime_terminal_unindexed_file",
        "matched_context_unindexed_file",
        "stale_matched_context",
        "acquisition_lifecycle_divergence",
        "quarantine_correlation_missing",
        "quarantine_file_without_sidecar",
        "quarantine_sidecar_without_file",
        "acquisition_transition_pending",
    }.issubset(codes)


def test_completed_import_and_recovery_waiting_without_files_are_reported(tmp_path):
    conn = _connection()
    request_id = _linked_grab(conn, "grab-import")
    candidate_id = conn.execute(
        "SELECT release_candidate_id FROM acquisition_grabs "
        "WHERE download_id='grab-import'"
    ).fetchone()[0]
    conn.execute(
        """INSERT INTO acquisition_imports(
               id,download_id,request_id,candidate_id,status,output_path,
               expected_scope,expected_entity_id)
           VALUES('import-1','grab-import',?,?,'completed',?, 'recording',1)""",
        (request_id, candidate_id, str(tmp_path / "gone.flac")),
    )
    conn.execute(
        """INSERT INTO acquisition_quarantine_recoveries(
               entry_id,source_path,staged_path,status)
           VALUES('recover-1','/q/a',?,'recovered')""",
        (str(tmp_path / "staging" / "gone.flac"),),
    )
    conn.commit()

    report = build_integrity_report(
        conn, config_manager=_Config([str(tmp_path)]),
    )

    assert "completed_import_without_indexed_file" in _codes(report)
    assert "recovery_staging_file_missing" in _codes(report)


def test_findings_are_bounded_but_counts_cover_full_scan(tmp_path):
    conn = _connection()
    for index in range(3):
        path = tmp_path / f"legacy-{index}.flac"
        path.write_bytes(b"x")
        conn.execute("INSERT INTO tracks(file_path) VALUES(?)", (str(path),))
    conn.commit()

    report = build_integrity_report(conn, max_findings=1)

    assert len(report.findings) == 1
    assert report.findings_total == 3
    assert report.truncated is True
    assert report.counts["legacy_only_indexed_file"] == 3
