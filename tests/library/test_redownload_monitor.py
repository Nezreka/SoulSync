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


def test_an_own_library_redownload_replaces_and_lands_in_that_library(tmp_path, monkeypatch):
    """review 5 W2-WB-1: profile 2 redownloads a track of its own library. the
    batch carried no owner, so the worker (no request) filed the new file in
    the shared library while the old one in profile 2's was deleted. and the
    old file picked was the track's primary, whichever library held it."""
    from flask import g

    import core.library_scope as LS
    from core.imports.paths import import_owner_id
    from database.music_database import MusicDatabase
    from tests import lib2_seed

    path = str(tmp_path / "m.db")
    db = MusicDatabase(path)
    conn = lib2_seed.row_conn(path)
    tid = lib2_seed.track(conn, "Artist", "Album", "Song", path="/music/Artist/Album/Song.flac")
    conn.execute("INSERT INTO lib2_track_files(track_id, path, is_primary, owner_profile_id)"
                 " VALUES(?, '/own2/Artist/Album/Song.flac', 0, 2)", (tid,))
    # the shared copy holds the track's one primary flag
    conn.execute("UPDATE lib2_track_files SET is_primary=(owner_profile_id IS NULL) WHERE track_id=?", (tid,))
    conn.commit()
    monkeypatch.setattr(LS, "own_library_ids", lambda: frozenset({2}))
    monkeypatch.setattr(LS, "library_scope_for_profile",
                        lambda pid: 2 if pid and int(pid) == 2 else "shared")
    batches, tasks = {}, {}
    monkeypatch.setattr(rd, "download_batches", batches)
    monkeypatch.setattr(rd, "download_tasks", tasks)
    monkeypatch.setattr(rd, "get_database", lambda: db)
    monkeypatch.setattr(rd, "_resolve_library_file_path", lambda p: p)
    monkeypatch.setattr(rd, "download_monitor", Mock())
    monkeypatch.setattr(rd, "missing_download_executor", Mock())

    app = Flask(__name__)
    with app.test_request_context(json={"metadata": {"name": "Song", "artist": "Artist"},
                                       "candidate": {"username": "peer", "filename": "Song.flac"},
                                       "delete_old_file": True}):
        g.profile_id = 2
        payload = rd.redownload_start(tid).get_json()
    assert payload["success"], payload
    task = tasks[payload["task_id"]]
    assert task["_redownload_context"]["old_file_path"] == "/own2/Artist/Album/Song.flac"
    batch = batches[payload["batch_id"]]
    context = {"task_id": payload["task_id"], "batch_id": payload["batch_id"],
               "track_info": task["track_info"], LS.BATCH_OWNER_KEY: batch.get(LS.BATCH_OWNER_KEY)}
    # read with no request, the way the import worker does
    assert import_owner_id(context) == 2
