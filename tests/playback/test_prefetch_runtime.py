from __future__ import annotations

import os
import tempfile

import pytest

from core.runtime_state import download_batches, download_tasks, tasks_lock


_TMP = tempfile.mkdtemp(prefix="soulsync-testdb-playback-prefetch-")
os.environ.setdefault("DATABASE_PATH", os.path.join(_TMP, "s.db"))
os.environ.setdefault("SOULSYNC_TEST_DB_READY", "1")

web_server = pytest.importorskip("web_server")


@pytest.fixture(autouse=True)
def clean_runtime_state():
    with tasks_lock:
        download_tasks.clear()
        download_batches.clear()
    yield
    with tasks_lock:
        download_tasks.clear()
        download_batches.clear()


def test_runtime_reuses_owned_files_and_deduplicates_active_downloads(monkeypatch):
    started = []
    monitored = []
    monkeypatch.setattr(
        web_server,
        "_resolve_playback_prefetch_local_file",
        lambda track: "/music/Owned.flac" if track["name"] == "Owned" else None,
    )
    monkeypatch.setattr(
        web_server.download_monitor,
        "start_monitoring",
        lambda batch_id: monitored.append(batch_id),
    )
    monkeypatch.setattr(
        web_server,
        "_start_next_batch_of_downloads",
        lambda batch_id: started.append(batch_id),
    )
    monkeypatch.setattr(web_server, "add_activity_item", lambda *args: None)

    request = [
        {"title": "Owned", "artist": "A", "_queue_request_id": "owned"},
        {"title": "Missing", "artist": "B", "_queue_request_id": "missing-1"},
        {"name": "Missing", "artists": ["B"], "_queue_request_id": "missing-2"},
    ]
    result = web_server._start_playback_queue_prefetch(request)
    assert result["queued"] == 1
    assert len(result["batch_ids"]) == 1
    assert started == result["batch_ids"]
    assert monitored == result["batch_ids"]
    ready = next(item for item in result["items"] if item["state"] == "ready")
    queued = next(item for item in result["items"] if item["state"] == "queued")
    assert ready["final_path"] == "/music/Owned.flac"
    assert queued["request_ids"] == ["missing-1", "missing-2"]
    assert len(download_tasks) == 1
    task = download_tasks[queued["task_id"]]
    assert task["track_info"]["_queue_request_ids"] == ["missing-1", "missing-2"]
    batch = download_batches[result["batch_ids"][0]]
    assert batch["max_concurrent"] == 1

    again = web_server._start_playback_queue_prefetch(
        [{"title": "Missing", "artist": "B", "_queue_request_id": "again"}]
    )
    assert again["queued"] == 0
    assert again["items"][0]["task_id"] == queued["task_id"]
    assert task["track_info"]["_queue_request_ids"] == [
        "missing-1",
        "missing-2",
        "again",
    ]
    assert started == result["batch_ids"]


def test_runtime_reuses_one_batch_and_prioritizes_current_queue_order(monkeypatch):
    started = []
    monitored = []
    monkeypatch.setattr(
        web_server,
        "_resolve_playback_prefetch_local_file",
        lambda _track: None,
    )
    monkeypatch.setattr(
        web_server.download_monitor,
        "start_monitoring",
        lambda batch_id: monitored.append(batch_id),
    )
    monkeypatch.setattr(
        web_server,
        "_start_next_batch_of_downloads",
        lambda batch_id: started.append(batch_id),
    )
    monkeypatch.setattr(web_server, "add_activity_item", lambda *args: None)

    initial = web_server._start_playback_queue_prefetch(
        [
            {"title": "First", "artist": "A", "_queue_request_id": "first"},
            {"title": "Second", "artist": "B", "_queue_request_id": "second"},
        ]
    )
    batch_id = initial["batch_ids"][0]

    updated = web_server._start_playback_queue_prefetch(
        [
            {"title": "Play Next", "artist": "C", "_queue_request_id": "next"},
            {"title": "First", "artist": "A", "_queue_request_id": "first"},
            {"title": "Second", "artist": "B", "_queue_request_id": "second"},
        ]
    )

    assert updated["queued"] == 1
    assert updated["batch_ids"] == [batch_id]
    assert monitored == [batch_id]
    assert started == [batch_id, batch_id]
    batch = download_batches[batch_id]
    assert batch["max_concurrent"] == 1
    assert [
        download_tasks[task_id]["track_info"]["name"] for task_id in batch["queue"]
    ] == ["Play Next", "First", "Second"]


def test_prefetch_routes_dispatch_and_report_status(monkeypatch):
    starts = []
    monkeypatch.setattr(web_server, "check_download_permission", lambda: None)
    monkeypatch.setattr(
        web_server,
        "_start_playback_queue_prefetch",
        lambda tracks: starts.append(tracks) or {
            "success": True,
            "queued": 1,
            "batch_ids": ["batch-1"],
            "items": [],
        },
    )
    monkeypatch.setattr(
        web_server,
        "_playback_queue_prefetch_status",
        lambda batch_ids: {
            "success": True,
            "batch_ids": batch_ids,
            "batches": {},
        },
    )

    client = web_server.app.test_client()
    response = client.post(
        "/api/playback/queue/prefetch",
        json={"tracks": [{"title": "Genesis", "artist": "Justice"}]},
    )
    assert response.status_code == 200
    assert response.get_json()["batch_ids"] == ["batch-1"]
    assert starts == [[{"title": "Genesis", "artist": "Justice"}]]

    response = client.get(
        "/api/playback/queue/prefetch/status?batch_ids=batch-1&batch_ids=batch-2"
    )
    assert response.status_code == 200
    assert response.get_json()["batch_ids"] == ["batch-1", "batch-2"]


def test_prefetch_route_rejects_empty_track_list(monkeypatch):
    monkeypatch.setattr(web_server, "check_download_permission", lambda: None)
    response = web_server.app.test_client().post(
        "/api/playback/queue/prefetch", json={"tracks": []}
    )
    assert response.status_code == 400
    assert response.get_json()["success"] is False
