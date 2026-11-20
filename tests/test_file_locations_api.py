"""Tools > Update File Locations endpoints (api/file_locations.py).

A small Flask app around the blueprint and a temp catalogue: the gate is the
real @admin_only, and the job runs the real path_repoint walk.
"""

from __future__ import annotations

import time
from contextlib import closing

import pytest
from flask import Flask, g

from api import file_locations
from core.library2 import path_repoint
from database.music_database import MusicDatabase

AUDIO = b"one-song"


class _Config:
    def __init__(self, transfer):
        self.values = {"soulseek.transfer_path": str(transfer), "library.music_paths": []}

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    old_root, new_root = tmp_path / "app" / "Transfer", tmp_path / "music"
    song = new_root / "Artist" / "Album" / "01 - Song.flac"
    song.parent.mkdir(parents=True)
    song.write_bytes(AUDIO)
    config = _Config(new_root)
    monkeypatch.setattr(path_repoint, "_config", lambda cm=None: cm or config)
    db = MusicDatabase(str(tmp_path / "music.db"))
    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: db)
    with closing(db._get_connection()) as conn:
        file_id = conn.execute("INSERT INTO lib2_track_files(path, size) VALUES(?,?)",
                               (str(old_root / "Artist" / "Album" / "01 - Song.flac"),
                                len(AUDIO))).lastrowid
        conn.commit()
    # every test starts from an idle job, whatever ran before it
    file_locations._state.update({"status": "idle", "action": None, "result": None,
                                  "preview": None, "error": None, "stop": False})
    file_locations.configure(get_database_=lambda: db)
    app = Flask(__name__)
    app.secret_key = "test"
    admin = {"on": True}

    @app.before_request
    def _who():
        g.is_admin = admin["on"]

    app.register_blueprint(file_locations.create_blueprint())
    return {"client": app.test_client(), "admin": admin, "db": db, "file_id": file_id,
            "old_root": str(old_root), "new_root": str(new_root)}


def _wait(client):
    for _ in range(200):
        state = client.get("/api/library/file-locations/status").get_json()
        if state["status"] != "running":
            return state
        time.sleep(0.05)
    raise AssertionError("job never finished")


def _path(db, file_id):
    with closing(db._get_connection()) as conn:
        return conn.execute("SELECT path FROM lib2_track_files WHERE id=?", (file_id,)).fetchone()[0]


ROUTES = [
    ("GET", "/api/library/file-locations/status"),
    ("POST", "/api/library/file-locations/preview"),
    ("POST", "/api/library/file-locations/apply"),
    ("POST", "/api/library/file-locations/stop"),
    ("POST", "/api/library/file-locations/runs/1/undo"),
]


@pytest.mark.parametrize("method,path", ROUTES)
def test_every_route_is_admin_only(setup, method, path):
    setup["admin"]["on"] = False
    response = setup["client"].open(path, method=method, json={})
    assert response.status_code == 403
    assert _path(setup["db"], setup["file_id"]).startswith(setup["old_root"])


def test_preview_then_apply_then_undo(setup):
    client = setup["client"]
    assert client.post("/api/library/file-locations/preview").status_code == 200
    state = _wait(client)
    assert state["status"] == "done"
    group = state["preview"]["groups"][0]
    assert (group["old_root"], group["new_root"], group["count"]) == \
        (setup["old_root"], setup["new_root"], 1)
    assert _path(setup["db"], setup["file_id"]).startswith(setup["old_root"])

    assert client.post("/api/library/file-locations/apply",
                       json={"groups": [{"old_root": group["old_root"],
                                         "new_root": group["new_root"]}]}).status_code == 200
    state = _wait(client)
    assert state["result"]["repointed"] == 1
    assert state["preview"] is None
    assert _path(setup["db"], setup["file_id"]).startswith(setup["new_root"])
    run_id = state["runs"][0]["id"]

    assert client.post(f"/api/library/file-locations/runs/{run_id}/undo").status_code == 200
    state = _wait(client)
    assert state["result"]["restored"] == 1
    assert _path(setup["db"], setup["file_id"]).startswith(setup["old_root"])
    # an undone run can't be undone twice
    assert client.post(f"/api/library/file-locations/runs/{run_id}/undo").status_code == 409


def test_a_bad_group_list_is_refused(setup):
    response = setup["client"].post("/api/library/file-locations/apply", json={"groups": "all"})
    assert response.status_code == 400
    response = setup["client"].post("/api/library/file-locations/apply", json={"groups": []})
    assert response.status_code == 400


def test_one_job_at_a_time(setup):
    file_locations._state["status"] = "running"
    assert setup["client"].post("/api/library/file-locations/preview").status_code == 409


def test_undo_of_an_unknown_run_is_404(setup):
    assert setup["client"].post("/api/library/file-locations/runs/999/undo").status_code == 404
