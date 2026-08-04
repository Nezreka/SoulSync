"""Tests for the live download-queue status surfaced on Library-v2 rows
(docs §73, I6): ``core.library2.queue_status.get_queue_status`` and the
``GET /api/library/v2/<entity>/<id>/queue-status`` endpoint.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.queue_status import get_queue_status
from core.runtime_state import (
    download_tasks,
    matched_downloads_context,
)

flask = pytest.importorskip("flask")


@pytest.fixture(autouse=True)
def _clean_runtime_state():
    """download_tasks/matched_downloads_context are process-global dicts —
    clear them before AND after each test so no test leaks state into the
    next (see soulsync-pytest-full-suite-and-isolation-quirks)."""
    download_tasks.clear()
    matched_downloads_context.clear()
    yield
    download_tasks.clear()
    matched_downloads_context.clear()


def _make_context_key(username, filename):
    return f"{username}::{filename}"


def _deps(live_transfers=None):
    live_transfers = live_transfers or {}
    return {
        "make_context_key": _make_context_key,
        "get_cached_transfer_data": lambda: live_transfers,
    }


class TestGetQueueStatus:
    def test_downloading_task_reports_live_progress(self):
        download_tasks["t1"] = {
            "status": "downloading",
            "username": "alice",
            "filename": "song.flac",
            "track_info": {"source_info": {"lib2_track_id": 10, "lib2_album_id": 5}},
        }
        live = {_make_context_key("alice", "song.flac"): {"percentComplete": 42}}

        result = get_queue_status([10], **_deps(live))

        assert result == {
            "tracks": {10: {"status": "downloading", "progress_pct": 42}},
            "albums": {5: 1},
        }

    @pytest.mark.parametrize("raw_status,bucket", [
        ("pending", "queued"),
        ("queued", "queued"),
        ("searching", "searching"),
        ("post_processing", "processing"),
    ])
    def test_status_bucket_mapping(self, raw_status, bucket):
        download_tasks["t1"] = {
            "status": raw_status,
            "track_info": {"source_info": {"lib2_track_id": 10, "lib2_album_id": 5}},
        }

        result = get_queue_status([10], **_deps())

        assert result["tracks"][10]["status"] == bucket

    def test_post_processing_reports_95_percent(self):
        download_tasks["t1"] = {
            "status": "post_processing",
            "track_info": {"source_info": {"lib2_track_id": 10, "lib2_album_id": 5}},
        }

        result = get_queue_status([10], **_deps())

        assert result["tracks"][10]["progress_pct"] == 95

    @pytest.mark.parametrize("terminal_status", [
        "completed", "failed", "cancelled", "not_found", "skipped", "already_owned",
    ])
    def test_terminal_statuses_are_omitted(self, terminal_status):
        download_tasks["t1"] = {
            "status": terminal_status,
            "track_info": {"source_info": {"lib2_track_id": 10, "lib2_album_id": 5}},
        }

        result = get_queue_status([10], **_deps())

        assert result == {"tracks": {}, "albums": {}}

    def test_track_outside_requested_scope_is_omitted(self):
        download_tasks["t1"] = {
            "status": "downloading",
            "track_info": {"source_info": {"lib2_track_id": 999, "lib2_album_id": 5}},
        }

        result = get_queue_status([10], **_deps())

        assert result == {"tracks": {}, "albums": {}}

    def test_task_with_no_lib2_track_id_is_ignored(self):
        download_tasks["t1"] = {"status": "downloading", "track_info": {}}

        result = get_queue_status([10], **_deps())

        assert result == {"tracks": {}, "albums": {}}

    def test_malformed_album_id_does_not_break_other_queue_status(self):
        download_tasks["t1"] = {
            "status": "queued",
            "track_info": {
                "source_info": {
                    "lib2_track_id": 10,
                    "lib2_album_id": "not-an-integer",
                },
            },
        }

        result = get_queue_status([10], **_deps())

        assert result == {
            "tracks": {10: {"status": "queued", "progress_pct": 0}},
            "albums": {},
        }

    def test_manual_grab_with_no_live_transfer_yet_defaults_to_queued(self):
        matched_downloads_context["ctx1"] = {
            "lib2_entity": {"track_id": 20, "album_id": 6},
            "search_result": {"username": "bob", "filename": "track.mp3"},
        }

        result = get_queue_status([20], **_deps())

        assert result == {
            "tracks": {20: {"status": "queued", "progress_pct": 0}},
            "albums": {6: 1},
        }

    def test_manual_grab_with_inprogress_transfer_reports_downloading(self):
        matched_downloads_context["ctx1"] = {
            "lib2_entity": {"track_id": 20, "album_id": 6},
            "search_result": {"username": "bob", "filename": "track.mp3"},
        }
        live = {_make_context_key("bob", "track.mp3"): {
            "state": "InProgress", "percentComplete": 77,
        }}

        result = get_queue_status([20], **_deps(live))

        assert result["tracks"][20] == {"status": "downloading", "progress_pct": 77}

    def test_batch_task_takes_priority_over_shadow_manual_context(self):
        """Some manual grabs also get a correlated download_tasks entry
        (docs §71 acquisition correlation) — the task's precise status
        machine must win, not the coarser slskd-state guess."""
        download_tasks["t1"] = {
            "status": "post_processing",
            "track_info": {"source_info": {"lib2_track_id": 20, "lib2_album_id": 6}},
        }
        matched_downloads_context["ctx1"] = {
            "lib2_entity": {"track_id": 20, "album_id": 6},
            "search_result": {"username": "bob", "filename": "track.mp3"},
        }

        result = get_queue_status([20], **_deps())

        assert result["tracks"][20]["status"] == "processing"
        assert result["albums"] == {6: 1}  # not double-counted

    @pytest.mark.parametrize("terminal_status", [
        "completed", "failed", "cancelled", "not_found", "skipped", "already_owned",
    ])
    def test_terminal_task_suppresses_stale_shadow_manual_context(self, terminal_status):
        """A leaked correlation context must not resurrect terminal work as Queued."""
        download_tasks["t1"] = {
            "status": terminal_status,
            "track_info": {"source_info": {"lib2_track_id": 20, "lib2_album_id": 6}},
        }
        matched_downloads_context["ctx1"] = {
            "lib2_entity": {"track_id": 20, "album_id": 6},
            "search_result": {"username": "bob", "filename": "track.mp3"},
        }

        result = get_queue_status([20], **_deps())

        assert result == {"tracks": {}, "albums": {}}

    def test_albums_rollup_counts_multiple_active_tracks(self):
        download_tasks["t1"] = {
            "status": "downloading",
            "track_info": {"source_info": {"lib2_track_id": 10, "lib2_album_id": 5}},
        }
        download_tasks["t2"] = {
            "status": "searching",
            "track_info": {"source_info": {"lib2_track_id": 11, "lib2_album_id": 5}},
        }

        result = get_queue_status([10, 11], **_deps())

        assert result["albums"] == {5: 2}

    def test_empty_track_ids_short_circuits(self):
        assert get_queue_status([], **_deps()) == {"tracks": {}, "albums": {}}

    def test_bridge_dispatched_task_falls_back_to_lib2_entity(self):
        """A7: core.acquisition.main_pipeline_bridge (Torrent/Usenet bundle
        match, manual grab) puts the Library-v2 identity in a top-level
        "lib2_entity" dict, not "source_info" — this download must still get
        a live badge instead of silently never showing progress."""
        download_tasks["t1"] = {
            "status": "downloading",
            "username": "alice",
            "filename": "song.flac",
            "track_info": {"lib2_entity": {"track_id": 10, "album_id": 5}},
        }
        live = {_make_context_key("alice", "song.flac"): {"percentComplete": 42}}

        result = get_queue_status([10], **_deps(live))

        assert result == {
            "tracks": {10: {"status": "downloading", "progress_pct": 42}},
            "albums": {5: 1},
        }

    @pytest.mark.parametrize("malformed_track_id", ["", "not-a-number", None])
    def test_malformed_lib2_track_id_is_skipped_not_raised(self, malformed_track_id):
        """A8: a malformed lib2_track_id must not crash the whole endpoint —
        it's skipped, other tasks still report normally."""
        download_tasks["bad"] = {
            "status": "downloading",
            "track_info": {"source_info": {"lib2_track_id": malformed_track_id}},
        }
        download_tasks["good"] = {
            "status": "downloading",
            "track_info": {"source_info": {"lib2_track_id": 10, "lib2_album_id": 5}},
        }

        result = get_queue_status([10], **_deps())

        assert result["tracks"] == {10: {"status": "downloading", "progress_pct": 0}}

    def test_malformed_lib2_track_id_in_matched_context_is_skipped_not_raised(self):
        matched_downloads_context["ctx1"] = {
            "lib2_entity": {"track_id": "garbage"},
            "search_result": {"username": "bob", "filename": "track.mp3"},
        }

        result = get_queue_status([10], **_deps())

        assert result == {"tracks": {}, "albums": {}}


class FakeDB:
    def __init__(self, path: str):
        self.database_path = path

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn


def _build_api(tmp_path, *, with_deps=True):
    db_path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)

    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name, sort_name, spotify_id, monitored) "
                "VALUES('Drake','Drake','sp-drake',0)")
    artist_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_albums(primary_artist_id, title, album_type, monitored) "
                "VALUES(?,?,?,0)", (artist_id, "Views", "album"))
    album_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                (album_id, artist_id))
    cur.execute("INSERT INTO lib2_tracks(album_id, title, track_number, spotify_id) "
                "VALUES(?,?,1,?)", (album_id, "One Dance", "sp-t1"))
    track_id = cur.lastrowid
    cur.execute("INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id) "
                "VALUES(?,?)", (track_id, artist_id))
    conn.commit()
    conn.close()

    db = FakeDB(db_path)
    db.config = {"features.library_v2": True}
    app = flask.Flask(__name__)
    from api.library_v2 import register_library_v2_routes
    kwargs = {}
    if with_deps:
        kwargs = {
            "make_context_key": _make_context_key,
            "get_cached_transfer_data": lambda: {},
        }
    register_library_v2_routes(
        app,
        get_database=lambda: db,
        config_get=lambda key, default=None: db.config.get(key, default),
        config_manager=None,
        profile_id_getter=lambda: 1,
        **kwargs,
    )
    return app.test_client(), {"artist": artist_id, "album": album_id, "track": track_id}


@pytest.fixture
def api(tmp_path):
    yield _build_api(tmp_path)


class TestQueueStatusEndpoint:
    def test_unknown_entity_is_400(self, api):
        client, ids = api
        response = client.get(f"/api/library/v2/widgets/{ids['album']}/queue-status")
        assert response.status_code == 400

    def test_unknown_id_is_404(self, api):
        client, ids = api
        response = client.get("/api/library/v2/albums/999999/queue-status")
        assert response.status_code == 404

    def test_no_active_downloads_returns_empty_maps(self, api):
        client, ids = api
        response = client.get(f"/api/library/v2/albums/{ids['album']}/queue-status")
        assert response.status_code == 200
        assert response.get_json() == {"tracks": {}, "albums": {}}

    def test_active_download_surfaces_on_album_scope(self, api):
        client, ids = api
        download_tasks["t1"] = {
            "status": "downloading",
            "username": "alice",
            "filename": "song.flac",
            "track_info": {"source_info": {
                "lib2_track_id": ids["track"], "lib2_album_id": ids["album"],
            }},
        }

        response = client.get(f"/api/library/v2/albums/{ids['album']}/queue-status")

        assert response.status_code == 200
        body = response.get_json()
        assert body["tracks"] == {str(ids["track"]): {"status": "downloading", "progress_pct": 0}}
        assert body["albums"] == {str(ids["album"]): 1}

    def test_active_download_surfaces_on_artist_scope(self, api):
        client, ids = api
        download_tasks["t1"] = {
            "status": "searching",
            "track_info": {"source_info": {
                "lib2_track_id": ids["track"], "lib2_album_id": ids["album"],
            }},
        }

        response = client.get(f"/api/library/v2/artists/{ids['artist']}/queue-status")

        assert response.status_code == 200
        body = response.get_json()
        assert body["tracks"] == {str(ids["track"]): {"status": "searching", "progress_pct": 0}}

    def test_missing_deps_degrades_to_empty_maps_not_error(self, tmp_path):
        client, ids = _build_api(tmp_path, with_deps=False)
        download_tasks["t1"] = {
            "status": "downloading",
            "track_info": {"source_info": {
                "lib2_track_id": ids["track"], "lib2_album_id": ids["album"],
            }},
        }

        response = client.get(f"/api/library/v2/albums/{ids['album']}/queue-status")

        assert response.status_code == 200
        assert response.get_json() == {"tracks": {}, "albums": {}}
