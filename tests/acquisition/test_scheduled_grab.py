"""Roadmap 3 slice 2: wishlist-worker dispatches correlate as scheduled requests."""

from __future__ import annotations

pytest_plugins = ["tests.library2.conftest"]

from core.acquisition import ensure_acquisition_schema
from core.acquisition.history import list_history_events
from core.acquisition.manual_grab import (
    GRAB_MARKER,
    correlate_manual_grab,
    correlate_scheduled_grab,
    fail_stale_correlated_grabs,
    try_correlate_scheduled_grab,
)
from core.acquisition.pipeline_callback import notify_manual_grab_import_success
from core.acquisition.requests import get_request
from core.library2.importer import import_legacy_library


_CONFIG_GET = lambda key, default=None: default  # noqa: E731 - test stub


def _prepared_conn(legacy_db):
    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    ensure_acquisition_schema(conn)
    conn.commit()
    return conn


def _track_context(conn, title="One Dance"):
    row = conn.execute(
        """SELECT t.id AS track_id, t.album_id, t.quality_profile_id
             FROM lib2_tracks t
             JOIN lib2_release_tracks rt ON rt.track_id=t.id
             JOIN lib2_albums al ON al.id=t.album_id
            WHERE t.title=? AND al.title='Views'
            ORDER BY t.id LIMIT 1""",
        (title,),
    ).fetchone()
    assert row is not None
    return {
        "track_id": row["track_id"],
        "album_id": row["album_id"],
        "quality_profile_id": row["quality_profile_id"],
    }


def _search_result(**overrides):
    result = {
        "username": "peer1",
        "filename": "Music\\Drake\\01 - One Dance.flac",
        "size": 12345678,
        "title": "One Dance",
        "artist": "Drake",
        "album": "Views",
        "quality": "flac",
        "bitrate": 1000,
    }
    result.update(overrides)
    return result


def test_wishlist_dispatch_correlates_scheduled_request(legacy_db):
    conn = _prepared_conn(legacy_db)
    try:
        markers = correlate_scheduled_grab(
            conn,
            lib2_context=_track_context(conn),
            search_result=_search_result(),
            source="soulseek",
            task_id="task-77",
            batch_id="batch-9",
            config_get=_CONFIG_GET,
        )

        assert markers is not None
        assert markers["download_id"].startswith("scheduled-")
        request = get_request(conn, markers["request_id"])
        assert request.scope == "recording"
        assert request.trigger == "scheduled"
        assert request.status == "grabbing"
        assert request.search_options["content_scope"] == "recording"
        assert request.search_options["shadow_source"] == "legacy_wishlist_worker"
        assert request.search_options["legacy_task_id"] == "task-77"
        assert request.search_options["legacy_batch_id"] == "batch-9"
        grab = conn.execute(
            "SELECT * FROM acquisition_grabs WHERE download_id=?",
            (markers["download_id"],),
        ).fetchone()
        assert grab["acquisition_request_id"] == request.id
        assert grab["status"] == "downloading"
        assert grab["release_candidate_id"]
        assert grab["decision_run_id"]
        events = [event.event_type for event in list_history_events(
            conn, request_id=request.id)]
        assert events == ["request_created", "scheduled_grab_correlated"]
    finally:
        conn.close()


def test_gate_rejections_are_recorded_but_never_enforced(legacy_db):
    conn = _prepared_conn(legacy_db)
    try:
        markers = correlate_scheduled_grab(
            conn,
            lib2_context=_track_context(conn),
            search_result=_search_result(artist="Completely Different Artist"),
            source="soulseek",
            task_id="task-78",
            config_get=_CONFIG_GET,
        )

        assert markers is not None
        run = conn.execute(
            """SELECT r.accepted, r.forced FROM candidate_decision_runs r
                 JOIN acquisition_grabs g ON g.decision_run_id=r.id
                WHERE g.download_id=?""",
            (markers["download_id"],),
        ).fetchone()
        assert run["accepted"] == 0
        assert run["forced"] == 0
        request = get_request(conn, markers["request_id"])
        assert request.status == "grabbing"
        correlated = [
            event for event in list_history_events(conn, request_id=request.id)
            if event.event_type == "scheduled_grab_correlated"
        ]
        assert correlated[0].reason_code == (
            "gate_rejections_observed_not_enforced")
        assert "artist_mismatch" in correlated[0].payload["rejections"]
    finally:
        conn.close()


def test_bundle_scope_sources_are_not_correlated(legacy_db):
    conn = _prepared_conn(legacy_db)
    try:
        markers = correlate_scheduled_grab(
            conn,
            lib2_context=_track_context(conn),
            search_result=_search_result(),
            source="usenet",
            task_id="task-79",
            config_get=_CONFIG_GET,
        )
        assert markers is None
        assert conn.execute(
            "SELECT COUNT(*) FROM acquisition_requests").fetchone()[0] == 0
    finally:
        conn.close()


def test_success_callback_completes_scheduled_grab(legacy_db):
    conn = _prepared_conn(legacy_db)
    try:
        markers = correlate_scheduled_grab(
            conn,
            lib2_context=_track_context(conn),
            search_result=_search_result(),
            source="soulseek",
            task_id="task-80",
            config_get=_CONFIG_GET,
        )
        conn.commit()

        context = {
            GRAB_MARKER: markers["download_id"],
            "_final_processed_path": "/music/Drake/Views/01 - One Dance.flac",
        }
        assert notify_manual_grab_import_success(
            context, connection_factory=legacy_db._get_connection) is True

        assert get_request(conn, markers["request_id"]).status == "completed"
        grab = conn.execute(
            "SELECT status FROM acquisition_grabs WHERE download_id=?",
            (markers["download_id"],),
        ).fetchone()
        assert grab["status"] == "completed"
    finally:
        conn.close()


def test_stale_sweep_covers_manual_and_scheduled_grabs(legacy_db):
    conn = _prepared_conn(legacy_db)
    try:
        stale_manual = correlate_manual_grab(
            conn,
            lib2_context=_track_context(conn),
            search_result=_search_result(),
            source="soulseek",
            config_get=_CONFIG_GET,
        )
        stale_scheduled = correlate_scheduled_grab(
            conn,
            lib2_context=_track_context(conn, title="Hotline Bling"),
            search_result=_search_result(title="Hotline Bling"),
            source="soulseek",
            task_id="task-81",
            config_get=_CONFIG_GET,
        )
        fresh = correlate_scheduled_grab(
            conn,
            lib2_context=_track_context(conn),
            search_result=_search_result(),
            source="soulseek",
            task_id="task-82",
            config_get=_CONFIG_GET,
        )
        for markers in (stale_manual, stale_scheduled):
            conn.execute(
                "UPDATE acquisition_requests SET updated_at='2020-01-01 00:00:00' WHERE id=?",
                (markers["request_id"],),
            )

        assert fail_stale_correlated_grabs(conn) == 2

        assert get_request(conn, stale_manual["request_id"]).status == "failed"
        assert get_request(conn, stale_scheduled["request_id"]).status == "failed"
        assert get_request(conn, fresh["request_id"]).status == "grabbing"
        # runtime failures must never blocklist the release itself
        assert conn.execute(
            "SELECT COUNT(*) FROM release_blocklist").fetchone()[0] == 0
    finally:
        conn.close()


def test_try_wrapper_fails_open(legacy_db):
    def _broken_factory():
        raise RuntimeError("db unavailable")

    assert try_correlate_scheduled_grab(
        lib2_context={"track_id": 1, "album_id": 1, "quality_profile_id": 1},
        search_result=_search_result(),
        source="soulseek",
        task_id="task-83",
        connection_factory=_broken_factory,
    ) is None
    assert try_correlate_scheduled_grab(
        lib2_context=None,
        search_result=_search_result(),
        source="soulseek",
        task_id="task-83",
        connection_factory=_broken_factory,
    ) is None
