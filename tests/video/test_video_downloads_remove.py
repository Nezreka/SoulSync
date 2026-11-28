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


def test_clear_finished_takes_completed_rows_only(client):
    """Clear finished used to take failed and cancelled rows too, so the only
    way to keep a failed row in view was never to clear anything."""
    c, db, retries = client
    for s in ("failed", "cancelled", "completed", "completed", "downloading"):
        _row(db, s)

    r = c.post("/api/video/downloads/clear")

    assert r.get_json()["cleared"] == 2
    assert _statuses(db) == ["cancelled", "downloading", "failed"]
    assert retries == []


def test_clear_everything_keeps_import_failed_and_live_rows(client):
    """Clear everything takes completed, failed and cancelled rows together,
    and still leaves import_failed rows for Manual import."""
    c, db, retries = client
    kept = _row(db, "import_failed")
    for s in ("completed", "failed", "cancelled", "downloading", "queued"):
        _row(db, s)

    r = c.post("/api/video/downloads/clear", json={"scope": "everything"})

    assert r.get_json() == {"cleared": 3}
    assert _statuses(db) == ["downloading", "import_failed", "queued"]
    assert retries == []
    items = c.get("/api/video/import/failed").get_json()["items"]
    assert [item["id"] for item in items] == [kept]


def test_clear_finished_leaves_import_failed_for_manual_import(client):
    """Clear finished used to delete import_failed rows too, stranding their
    files in the download area with nothing left pointing at them."""
    c, db, _ = client
    kept = _row(db, "import_failed")
    _row(db, "completed")
    _row(db, "failed")

    r = c.post("/api/video/downloads/clear")

    assert r.get_json()["cleared"] == 1
    assert _statuses(db) == ["failed", "import_failed"]
    items = c.get("/api/video/import/failed").get_json()["items"]
    assert [item["id"] for item in items] == [kept]


def test_remove_import_failed_with_flag_deletes_file_and_blocks_2337(client, tmp_path):
    """#2337: an admin can dismiss an import_failed row, optionally deleting
    its downloaded file and blocking the release, without touching history."""
    c, db, retries = client
    dl_file = tmp_path / "The.Office.S01E01.mkv"
    dl_file.write_bytes(b"fake-video")
    dl_id = _row(db, "import_failed", title="The Office (2005)")
    db.update_video_download(dl_id, dest_path=str(dl_file))
    db.record_download_history(db.get_video_download(dl_id))
    hist_before = _history_count(db)

    r = c.post("/api/video/downloads/remove", json={
        "id": dl_id,
        "include_import_failed": True,
        "delete_files": True,
        "block_release": True,
    })

    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "removed": 1, "files_deleted": 1, "blocked": 1}
    assert db.get_video_download(dl_id) is None
    assert not dl_file.exists()
    assert len(db.list_video_blocklist()) == 1
    assert _history_count(db) == hist_before == 1
    assert retries == []


def test_bulk_remove_and_clear_all_failed_include_import_failed_2337(client):
    """#2337: bulk ids and clear-all-failed with include_import_failed remove
    failed/cancelled/import_failed rows while leaving active and completed alone."""
    c, db, retries = client
    a = _row(db, "import_failed", title="The Office S01E01")
    b = _row(db, "failed", title="The Office S01E02")
    live = _row(db, "downloading", title="Running")
    done = _row(db, "completed", title="Done")

    r = c.post("/api/video/downloads/remove", json={"ids": [a, b, live, done]})
    assert r.status_code == 200
    assert r.get_json()["removed"] == 2
    assert _statuses(db) == ["completed", "downloading"]
    assert retries == []

    _row(db, "import_failed", title="The Office S01E03")
    _row(db, "failed", title="The Office S01E04")
    r2 = c.post("/api/video/downloads/clear", json={
        "scope": "failed",
        "include_import_failed": True,
    })
    assert r2.status_code == 200
    assert r2.get_json()["cleared"] == 2
    assert _statuses(db) == ["completed", "downloading"]
