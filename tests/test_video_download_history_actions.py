"""Download-history actions: forget one grab (the 'Re-download' button) + clear the whole
history. Removing a history row lets the scans re-add + re-grab the item."""

from __future__ import annotations

import pytest

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    d.record_download_history({"id": 1, "kind": "youtube", "source": "youtube",
                               "media_id": "v1", "status": "completed", "dest_path": "/a.mp4"})
    d.record_download_history({"id": 2, "kind": "youtube", "source": "youtube",
                               "media_id": "v2", "status": "completed", "dest_path": "/b.mp4"})
    d.record_download_history({"id": 3, "kind": "movie", "source": "soulseek",
                               "media_id": "9", "status": "completed", "dest_path": "/m.mkv"})
    return d


def _hist_ids(db):
    return {r["media_id"] for r in db.query_download_history()["items"]}


def test_delete_one_history_entry_frees_it_to_redownload(db):
    # the youtube dedup sees v1 + v2; forgetting v1's grab drops it from the dedup set
    assert set(db.downloaded_youtube_video_ids()) == {"v1", "v2"}
    target = next(r["id"] for r in db.query_download_history()["items"] if r["media_id"] == "v1")
    assert db.delete_download_history(target) is True
    assert set(db.downloaded_youtube_video_ids()) == {"v2"}          # v1 will re-grab
    assert db.delete_download_history(999999) is False               # missing → no-op


def test_clear_history_all_and_by_kind(db):
    assert db.clear_download_history(kind="youtube") == 2            # just the youtube grabs
    assert _hist_ids(db) == {"9"}                                    # movie survives
    assert db.clear_download_history() == 1                          # clear the rest
    assert _hist_ids(db) == set()


def test_history_action_endpoints(tmp_path):
    from flask import Flask
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    hid = db.record_download_history({"id": 1, "kind": "youtube", "source": "youtube",
                                      "media_id": "v1", "status": "completed", "dest_path": "/a.mp4"})
    db.record_download_history({"id": 2, "kind": "youtube", "source": "youtube",
                               "media_id": "v2", "status": "completed", "dest_path": "/b.mp4"})
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    try:
        assert client.delete("/api/video/downloads/history/%d" % hid).get_json()["success"] is True
        assert set(db.downloaded_youtube_video_ids()) == {"v2"}
        r = client.post("/api/video/downloads/history/clear", json={}).get_json()
        assert r["success"] and r["removed"] == 1                   # v2 left
        assert db.downloaded_youtube_video_ids() == []
    finally:
        videoapi._video_db = None
