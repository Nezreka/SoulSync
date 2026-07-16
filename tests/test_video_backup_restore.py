"""Backups: browse / create / staged restore (arr-parity P10).

Scheduled verified snapshots existed; the missing arr half is management and
RESTORE. Restore is staged and applied at the next startup BEFORE any
connection opens; the current database is set aside as .pre-restore-<ts> —
kept, never deleted (the house rule) — so a restore itself is undoable.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from flask import Flask, g

import core.video.backup_restore as br
from database.video_database import VideoDatabase


@pytest.fixture()
def env_db(tmp_path, monkeypatch):
    """A real tmp video DB + the env pointing at it (the module keys off env)."""
    p = tmp_path / "video_library.db"
    monkeypatch.setenv("VIDEO_DATABASE_PATH", str(p))
    db = VideoDatabase(database_path=str(p))
    db.set_setting("marker", "original")
    return db, str(p)


def test_create_list_and_prune(env_db):
    db, path = env_db
    out = br.create_now()
    assert out["ok"] and out["name"].startswith("video_library.db.backup_")
    rows = br.list_backups()
    assert len(rows) == 1 and rows[0]["size_bytes"] > 0


def test_restore_roundtrip_preserves_the_old_db(env_db, tmp_path):
    db, path = env_db
    assert br.create_now()["ok"]
    name = br.list_backups()[0]["name"]
    # the database moves on after the backup
    db.set_setting("marker", "changed-after-backup")
    staged = br.stage_restore(name)
    assert staged["ok"] and br.pending_restore()
    # apply (what startup does)
    assert br.apply_pending_restore(path) is True
    assert not br.pending_restore()
    # the restored DB carries the backup-time value…
    restored = VideoDatabase(database_path=path)
    assert restored.get_setting("marker") == "original"
    # …and the pre-restore DB still exists on disk (never deleted)
    kept = [f for f in os.listdir(tmp_path) if ".pre-restore-" in f]
    assert len(kept) == 1


def test_stage_refuses_unknown_and_traversal(env_db):
    _db, _path = env_db
    assert br.stage_restore("nope")["ok"] is False
    assert br.stage_restore("../../etc/passwd")["ok"] is False
    assert br._resolve("../../../etc/passwd") is None


def test_cancel_and_noop_apply(env_db):
    db, path = env_db
    assert br.create_now()["ok"]
    br.stage_restore(br.list_backups()[0]["name"])
    assert br.cancel_restore() is True
    assert br.cancel_restore() is False
    assert br.apply_pending_restore(path) is False   # nothing staged → no-op


def test_api_admin_gate_and_flow(env_db, monkeypatch):
    db, path = env_db
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    persona = {"profile_id": 1, "is_admin": True, "can_download": True}

    @app.before_request
    def _persona():
        for k, v in persona.items():
            setattr(g, k, v)
    try:
        c = app.test_client()
        assert c.post("/api/video/backups").get_json()["success"]
        listed = c.get("/api/video/backups").get_json()
        assert listed["backups"] and listed["pending_restore"] is False
        name = listed["backups"][0]["name"]
        assert c.post("/api/video/backups/restore", json={"name": name}).get_json()["success"]
        assert c.get("/api/video/backups").get_json()["pending_restore"] is True
        assert c.delete("/api/video/backups/restore").get_json()["success"]
        dl = c.get("/api/video/backups/%s/download" % name)
        assert dl.status_code == 200 and len(dl.data) > 0
        # members get nothing here — restore/download is the whole database
        persona.update({"profile_id": 5, "is_admin": False})
        assert c.get("/api/video/backups").status_code == 403
        assert c.post("/api/video/backups").status_code == 403
    finally:
        videoapi._video_db = None


def test_tools_card_exists():
    root = Path(__file__).resolve().parent.parent
    index = (root / "webui" / "index.html").read_text(encoding="utf-8")
    repair_js = (root / "webui" / "static" / "video" / "video-repair.js").read_text(encoding="utf-8")
    assert "data-video-backups-card" in index and "data-vbk-restore" in repair_js
    assert "showConfirmDialog" in repair_js        # restore is confirm-gated (house rule)
    assert "loadBackups()" in repair_js
