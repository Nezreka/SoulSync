"""ADR-02 shadow projection from wanted tracks to acquisition requests."""

from __future__ import annotations

from datetime import datetime, timezone

pytest_plugins = ["tests.library2.conftest"]

from core.acquisition import ensure_acquisition_schema
from core.acquisition.requests import transition_request
from core.acquisition.wanted_adapter import materialize_wanted_requests
from core.library2.importer import import_legacy_library
from core.library2.wanted import recompute_wanted


def _wanted_missing_track(legacy_db):
    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    track = conn.execute(
        "SELECT id FROM lib2_tracks ORDER BY id LIMIT 1"
    ).fetchone()[0]
    conn.execute("DELETE FROM lib2_track_files WHERE track_id=?", (track,))
    conn.execute(
        """INSERT INTO lib2_monitor_rules(
               entity_type, entity_id, profile_id, monitored, provenance)
           VALUES('track', ?, 1, 1, 'user_explicit')
           ON CONFLICT(entity_type, entity_id, profile_id) DO UPDATE SET
               monitored=1, provenance='user_explicit'""",
        (track,),
    )
    recompute_wanted(conn, profile_id=1, track_ids=[track])
    ensure_acquisition_schema(conn)
    conn.commit()
    return conn, track


def test_wanted_missing_track_creates_recording_request(legacy_db):
    conn, track_id = _wanted_missing_track(legacy_db)
    try:
        results = materialize_wanted_requests(conn, track_ids=[track_id])

        assert len(results) == 1
        item = results[0]
        assert item.track_id == track_id
        assert item.created is True
        assert item.request.scope == "recording"
        assert item.request.status == "searching"
        assert item.request.profile_id == 1
        assert item.request.search_options["content_scope"] == "recording"
        assert item.request.search_options["shadow_source"] == "lib2_wanted_tracks"
    finally:
        conn.close()


def test_unchanged_wanted_projection_is_idempotent(legacy_db):
    conn, track_id = _wanted_missing_track(legacy_db)
    try:
        first = materialize_wanted_requests(conn, track_ids=[track_id])[0]
        second = materialize_wanted_requests(conn, track_ids=[track_id])[0]

        assert second.created is False
        assert second.request.id == first.request.id
        assert conn.execute(
            "SELECT COUNT(*) FROM acquisition_requests"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_present_track_does_not_create_request(legacy_db):
    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    try:
        track = conn.execute(
            "SELECT id FROM lib2_tracks ORDER BY id LIMIT 1"
        ).fetchone()[0]
        recompute_wanted(conn, profile_id=1, track_ids=[track])
        ensure_acquisition_schema(conn)

        assert materialize_wanted_requests(conn, track_ids=[track]) == ()
    finally:
        conn.close()


def test_due_no_candidate_request_retries_same_identity(legacy_db):
    conn, track_id = _wanted_missing_track(legacy_db)
    try:
        first = materialize_wanted_requests(conn, track_ids=[track_id])[0]
        transition_request(conn, first.request.id, "no_candidate")

        retried = materialize_wanted_requests(
            conn,
            track_ids=[track_id],
            now=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )[0]

        assert retried.created is False
        assert retried.request.id == first.request.id
        assert retried.request.status == "searching"
        assert retried.request.attempts == 2
    finally:
        conn.close()


def test_new_missing_file_lifecycle_creates_new_request_identity(legacy_db):
    conn, track_id = _wanted_missing_track(legacy_db)
    try:
        first = materialize_wanted_requests(conn, track_ids=[track_id])[0]
        transition_request(conn, first.request.id, "candidates_ready")
        transition_request(conn, first.request.id, "grabbing")
        transition_request(conn, first.request.id, "completed")
        conn.execute(
            """INSERT INTO lib2_track_files(
                   track_id, path, file_state, is_primary, updated_at)
               VALUES(?, '/music/recovered.flac', 'active', 1,
                      '2030-01-01 00:00:00')""",
            (track_id,),
        )
        assert materialize_wanted_requests(conn, track_ids=[track_id]) == ()
        conn.execute(
            """UPDATE lib2_track_files
                  SET file_state='missing_confirmed',
                      updated_at='2030-01-02 00:00:00'
                WHERE track_id=?""",
            (track_id,),
        )

        second = materialize_wanted_requests(conn, track_ids=[track_id])[0]

        assert second.created is True
        assert second.request.id != first.request.id
    finally:
        conn.close()


def test_non_admin_projection_is_rejected(legacy_db):
    conn, _track_id = _wanted_missing_track(legacy_db)
    try:
        try:
            materialize_wanted_requests(conn, profile_id=2)
        except ValueError as exc:
            assert "admin-profile only" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("non-admin wanted request unexpectedly accepted")
    finally:
        conn.close()
