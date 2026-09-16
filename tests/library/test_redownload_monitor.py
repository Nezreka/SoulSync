"""Redownloads register with the monitor without relying on browser status polls."""
from unittest.mock import Mock
import pytest
from flask import Flask
from core.library import redownload as rd


@pytest.mark.parametrize("source", ["soundcloud", "youtube", "tidal", "soulseek"])
def test_manual_redownload_registers_before_worker_dispatch(monkeypatch, source):
    batches, tasks, events = {}, {}, []
    monkeypatch.setattr(rd, "download_batches", batches)
    monkeypatch.setattr(rd, "download_tasks", tasks)
    database = Mock()
    database._get_connection.return_value.cursor.return_value.fetchone.return_value = {"file_path": "/library/old.flac"}
    monkeypatch.setattr(rd, "get_database", lambda: database)
    monkeypatch.setattr(rd, "_resolve_library_file_path", lambda path: path)
    monitor = Mock()
    def register(batch_id):
        assert batch_id in batches
        task = tasks[batches[batch_id]["queue"][0]]
        assert task["_user_manual_pick"] is True
        assert task["_redownload_context"]["old_file_path"] == "/library/old.flac"
        events.append("registered")
    monitor.start_monitoring.side_effect = register
    monkeypatch.setattr(rd, "download_monitor", monitor)
    executor = Mock()
    executor.submit.side_effect = lambda worker: events.append("submitted")
    monkeypatch.setattr(rd, "missing_download_executor", executor)
    app = Flask(__name__)
    with app.test_request_context(json={"metadata": {"name": "Song", "artist": "Artist"},
                                       "candidate": {"username": source, "filename": "song.m4a"}}):
        response = rd.redownload_start(123)
    assert response.get_json()["success"] is True
    assert events == ["registered", "submitted"]
