"""Taking failed downloads off the list without retrying them.

The only way to clear a failed row used to be Clear finished, which also
wiped every completed row (and, on the way, import_failed rows that Manual
import still needed). ``/downloads/remove`` takes one failed or cancelled row;
``/downloads/clear`` with ``scope: "failed"`` takes all of them. Neither
retries anything, and neither touches the permanent history.
"""

from __future__ import annotations

import pytest
from flask import Flask

from database.video_database import VideoDatabase


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _fake_profile():
        from flask import g
        g.profile_id = 1
        g.is_admin = True
        g.can_download = True

    retries = []
    import core.video.download_monitor as mon
    monkeypatch.setattr(mon, "ensure_started", lambda *_a, **_k: None)
    monkeypatch.setattr(mon, "retry_another_release",
                        lambda *a, **k: retries.append(a) or {"status": "searching"})
    import api.video.downloads  # noqa: F401

    try:
        yield app.test_client(), videoapi._video_db, retries
    finally:
        videoapi._video_db = None


def _row(db, status, title="Monarch: Legacy of Monsters"):
    conn = db._get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO video_downloads (kind, title, status, source, username, filename, error) "
            "VALUES ('show', ?, ?, 'usenet', 'indexer', 'Monarch.S02E10.mkv', 'x')",
            (title, status))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _statuses(db):
    conn = db._get_connection()
    try:
        return sorted(r[0] for r in conn.execute("SELECT status FROM video_downloads"))
    finally:
        conn.close()


def _history_count(db):
    conn = db._get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM video_download_history").fetchone()[0]
    finally:
        conn.close()


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_remove_takes_one_failed_row_and_nothing_else(client, status):
    c, db, retries = client
    target = _row(db, status)
    other = _row(db, "failed", title="Tulsa King")
    db.record_download_history(db.get_video_download(target))
    before = _history_count(db)

    r = c.post("/api/video/downloads/remove", json={"id": target})

    assert r.status_code == 200 and r.get_json() == {"ok": True}
    assert db.get_video_download(target) is None
    assert db.get_video_download(other) is not None
    assert _history_count(db) == before == 1
    assert retries == []


@pytest.mark.parametrize("status", ["import_failed", "completed", "downloading",
                                    "queued", "searching", "importing"])
def test_remove_refuses_rows_that_are_live_finished_or_waiting_on_import(client, status):
    c, db, _ = client
    dl_id = _row(db, status)

    r = c.post("/api/video/downloads/remove", json={"id": dl_id})

    assert r.status_code == 409
    assert r.get_json()["ok"] is False
    assert db.get_video_download(dl_id)["status"] == status


def test_remove_missing_or_bad_id(client):
    c, _, _ = client
    assert c.post("/api/video/downloads/remove", json={"id": 999}).status_code == 404
    assert c.post("/api/video/downloads/remove", json={}).status_code == 400
    assert c.post("/api/video/downloads/remove", json={"id": "x"}).status_code == 400


def test_clear_failed_scope_keeps_completed_and_import_failed(client):
    c, db, retries = client
    for s in ("failed", "failed", "cancelled", "completed", "import_failed", "downloading"):
        _row(db, s)

    r = c.post("/api/video/downloads/clear", json={"scope": "failed"})

    assert r.get_json() == {"cleared": 3}
    assert _statuses(db) == ["completed", "downloading", "import_failed"]
    assert retries == []


def test_clear_with_no_body_still_clears_finished_rows(client):
    c, db, _ = client
    for s in ("failed", "cancelled", "completed", "downloading"):
        _row(db, s)

    r = c.post("/api/video/downloads/clear")

    assert r.get_json()["cleared"] == 3
    assert _statuses(db) == ["downloading"]
