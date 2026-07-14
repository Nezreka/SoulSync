"""Roadmap 3: legacy interactive grabs correlate into the acquisition contract."""

from __future__ import annotations

pytest_plugins = ["tests.library2.conftest"]

from core.acquisition import ensure_acquisition_schema
from core.acquisition.history import list_history_events
from core.acquisition.manual_grab import (
    GRAB_MARKER,
    correlate_manual_grab,
    fail_stale_manual_grabs,
    try_correlate_manual_grab,
)
from core.acquisition.pipeline_callback import (
    notify_manual_grab_import_success,
    notify_manual_grab_quarantined,
)
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


def test_track_grab_correlates_recording_request(legacy_db):
    conn = _prepared_conn(legacy_db)
    try:
        markers = correlate_manual_grab(
            conn,
            lib2_context=_track_context(conn),
            search_result=_search_result(),
            source="soulseek",
            config_get=_CONFIG_GET,
        )

        assert markers is not None
        assert markers["download_id"].startswith("manual-")
        request = get_request(conn, markers["request_id"])
        assert request.scope == "recording"
        assert request.trigger == "manual"
        assert request.status == "grabbing"
        assert request.search_options["content_scope"] == "recording"
        assert request.search_options["shadow_source"] == "legacy_interactive"
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
        assert events == ["request_created", "manual_grab_correlated"]
    finally:
        conn.close()


def test_matching_manual_pick_is_accepted_by_the_gate(legacy_db):
    conn = _prepared_conn(legacy_db)
    try:
        markers = correlate_manual_grab(
            conn,
            lib2_context=_track_context(conn),
            search_result=_search_result(),
            source="soulseek",
            config_get=_CONFIG_GET,
        )
        run = conn.execute(
            """SELECT r.accepted FROM candidate_decision_runs r
                 JOIN acquisition_grabs g ON g.decision_run_id=r.id
                WHERE g.download_id=?""",
            (markers["download_id"],),
        ).fetchone()
        assert run["accepted"] == 1
    finally:
        conn.close()


def test_gate_rejections_do_not_block_a_manual_pick(legacy_db):
    conn = _prepared_conn(legacy_db)
    try:
        markers = correlate_manual_grab(
            conn,
            lib2_context=_track_context(conn),
            search_result=_search_result(artist="Completely Different Artist"),
            source="soulseek",
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
            if event.event_type == "manual_grab_correlated"
        ]
        assert correlated[0].reason_code == (
            "gate_rejections_overridden_by_manual_pick")
        assert "artist_mismatch" in correlated[0].payload["rejections"]
    finally:
        conn.close()


def test_album_only_context_uses_release_group_scope(legacy_db):
    conn = _prepared_conn(legacy_db)
    try:
        album_id = _track_context(conn)["album_id"]
        markers = correlate_manual_grab(
            conn,
            lib2_context={"album_id": album_id, "quality_profile_id": 1},
            search_result=_search_result(),
            source="soulseek",
            batch_id="batch-1",
            config_get=_CONFIG_GET,
        )

        request = get_request(conn, markers["request_id"])
        assert request.scope == "release_group"
        assert request.entity_id == album_id
        assert request.search_options["manual_batch_id"] == "batch-1"
        assert request.search_options["content_scope"] == "recording"
    finally:
        conn.close()


def test_bundle_scope_sources_are_not_correlated(legacy_db):
    conn = _prepared_conn(legacy_db)
    try:
        markers = correlate_manual_grab(
            conn,
            lib2_context=_track_context(conn),
            search_result=_search_result(),
            source="usenet",
            config_get=_CONFIG_GET,
        )
        assert markers is None
        assert conn.execute(
            "SELECT COUNT(*) FROM acquisition_requests").fetchone()[0] == 0
    finally:
        conn.close()


def test_success_callback_completes_grab_and_request(legacy_db):
    conn = _prepared_conn(legacy_db)
    try:
        markers = correlate_manual_grab(
            conn,
            lib2_context=_track_context(conn),
            search_result=_search_result(),
            source="soulseek",
            config_get=_CONFIG_GET,
        )
        conn.commit()

        context = {
            GRAB_MARKER: markers["download_id"],
            "_final_processed_path": "/music/Drake/Views/01 - One Dance.flac",
        }
        assert notify_manual_grab_import_success(
            context, connection_factory=legacy_db._get_connection) is True

        request = get_request(conn, markers["request_id"])
        assert request.status == "completed"
        grab = conn.execute(
            "SELECT status, output_path FROM acquisition_grabs WHERE download_id=?",
            (markers["download_id"],),
        ).fetchone()
        assert grab["status"] == "completed"
        assert grab["output_path"].endswith("One Dance.flac")
        events = [event.event_type for event in list_history_events(
            conn, request_id=request.id)]
        assert "grab_completed" in events
    finally:
        conn.close()


def test_quarantine_callback_journals_without_closing(legacy_db):
    conn = _prepared_conn(legacy_db)
    try:
        markers = correlate_manual_grab(
            conn,
            lib2_context=_track_context(conn),
            search_result=_search_result(),
            source="soulseek",
            config_get=_CONFIG_GET,
        )
        conn.commit()

        assert notify_manual_grab_quarantined(
            {GRAB_MARKER: markers["download_id"]},
            trigger="quality",
            reason="below profile",
            connection_factory=legacy_db._get_connection) is True

        request = get_request(conn, markers["request_id"])
        assert request.status == "grabbing"
        quarantined = [
            event for event in list_history_events(conn, request_id=request.id)
            if event.event_type == "import_file_quarantined"
        ]
        assert quarantined[0].reason_code == "quality"
        assert quarantined[0].payload["manual_grab"] is True
    finally:
        conn.close()


def test_unmarked_context_is_a_noop_for_both_callbacks(legacy_db):
    conn = _prepared_conn(legacy_db)
    conn.close()
    assert notify_manual_grab_import_success(
        {"_final_processed_path": "/music/x.flac"},
        connection_factory=legacy_db._get_connection) is False
    assert notify_manual_grab_quarantined(
        {}, trigger="quality", reason="below profile",
        connection_factory=legacy_db._get_connection) is False


def test_stale_sweep_fails_only_expired_manual_grabs(legacy_db):
    conn = _prepared_conn(legacy_db)
    try:
        stale = correlate_manual_grab(
            conn,
            lib2_context=_track_context(conn),
            search_result=_search_result(),
            source="soulseek",
            config_get=_CONFIG_GET,
        )
        fresh = correlate_manual_grab(
            conn,
            lib2_context=_track_context(conn, title="Hotline Bling"),
            search_result=_search_result(title="Hotline Bling"),
            source="soulseek",
            config_get=_CONFIG_GET,
        )
        conn.execute(
            "UPDATE acquisition_requests SET updated_at='2020-01-01 00:00:00' WHERE id=?",
            (stale["request_id"],),
        )

        assert fail_stale_manual_grabs(conn) == 1

        assert get_request(conn, stale["request_id"]).status == "failed"
        assert get_request(conn, fresh["request_id"]).status == "grabbing"
        stale_grab = conn.execute(
            "SELECT status FROM acquisition_grabs WHERE download_id=?",
            (stale["download_id"],),
        ).fetchone()
        assert stale_grab["status"] == "failed"
        # runtime failures must never blocklist the release itself
        assert conn.execute(
            "SELECT COUNT(*) FROM release_blocklist").fetchone()[0] == 0
    finally:
        conn.close()


def test_try_wrapper_fails_open(legacy_db):
    def _broken_factory():
        raise RuntimeError("db unavailable")

    assert try_correlate_manual_grab(
        lib2_context={"track_id": 1, "album_id": 1, "quality_profile_id": 1},
        search_result=_search_result(),
        source="soulseek",
        connection_factory=_broken_factory,
    ) is None
    assert try_correlate_manual_grab(
        lib2_context=None,
        search_result=_search_result(),
        source="soulseek",
        connection_factory=_broken_factory,
    ) is None
