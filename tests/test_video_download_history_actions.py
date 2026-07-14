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


def test_clear_hides_the_youtube_ledger_but_never_deletes_it(db):
    """The user can clear all they want — the ownership LEDGER survives. A clear
    empties the modal, but scan dedup / retention / the Channels tab keep every
    fact. Only the per-row 'Re-download' forget truly removes a ledger row."""
    db.clear_download_history()
    assert _hist_ids(db) == set()                                    # modal: empty
    assert db.download_history_counts()["total"] == 0                # badge: empty
    assert set(db.downloaded_youtube_video_ids()) == {"v1", "v2"}    # dedup: intact
    assert db.clear_download_history() == 0                          # nothing left to hide
    conn = db._get_connection()
    hid = conn.execute("SELECT id FROM video_download_history WHERE media_id='v1'").fetchone()[0]
    conn.close()
    assert db.delete_download_history(hid) is True                   # sanctioned forget works
    assert set(db.downloaded_youtube_video_ids()) == {"v2"}


def test_log_rows_roll_off_after_a_year_ledger_is_forever(db):
    conn = db._get_connection()
    conn.execute("UPDATE video_download_history SET completed_at=datetime('now','-400 days') "
                 "WHERE media_id IN ('9','v1')")                     # an old movie + old ledger row
    conn.commit(); conn.close()
    # Any new terminal download opportunistically trims the aged LOG rows.
    db.record_download_history({"id": 4, "kind": "movie", "source": "soulseek",
                                "media_id": "10", "status": "completed", "dest_path": "/n.mkv"})
    ids = _hist_ids(db)
    assert "9" not in ids and "10" in ids                            # old movie log trimmed
    assert set(db.downloaded_youtube_video_ids()) == {"v1", "v2"}    # old ledger row immortal


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
        assert r["success"] and r["removed"] == 1                   # v2 hidden from the modal
        # NEW invariant: a user clear never deletes the ownership ledger —
        # v2 stays in the dedup set (no re-download storm after tidying).
        assert set(db.downloaded_youtube_video_ids()) == {"v2"}
        assert db.query_download_history()["items"] == []           # but the modal is empty
    finally:
        videoapi._video_db = None
