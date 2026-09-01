"""The YouTube tab's missing bulk action.

"Search all missing" was hidden on the YouTube tab - the comment said "YouTube
has its own drain", and searching genuinely means nothing there since the video
IS the release. But that left the tab with no bulk action at all, and once
videos could be waiting out a retry backoff there was no way to say "never mind
the wait, fetch these" short of clicking every row.

This is the YouTube twin of search-all: the user's click out-ranks the backoff,
exactly as a manual search out-ranks the movie/TV release gate. What it does
NOT override is a deleted video - re-requesting those on every click is a
treadmill, and the per-row button covers the one case where the user thinks a
video is back.
"""

from __future__ import annotations

import pytest
from flask import Flask

from core.automation.handlers.video_process_youtube_wishlist import (
    videos_for_manual_run, videos_to_enqueue)


def _v(vid):
    return {"video_id": vid, "video_title": "Video " + vid}


STATE = {"gone": {"permanent": True},
         "waiting": {"strikes": 3, "hours_since_last": 0.1},
         "ready": {"strikes": 1, "hours_since_last": 0.1}}
WANTED = [_v("gone"), _v("waiting"), _v("ready"), _v("busy")]


# ── the selection ────────────────────────────────────────────────────────────

def test_a_click_overrides_the_retry_wait():
    """The difference from the hourly tick, and the whole point of the button."""
    auto = [v["video_id"] for v in videos_to_enqueue(WANTED, ["busy"], STATE)]
    manual = [v["video_id"] for v in videos_for_manual_run(WANTED, ["busy"], STATE)]
    assert auto == ["ready"]
    assert manual == ["waiting", "ready"]


def test_a_deleted_video_is_still_skipped():
    """An override of the wait, not of reality."""
    assert "gone" not in [v["video_id"] for v in videos_for_manual_run(WANTED, [], STATE)]


def test_already_downloading_is_never_double_queued():
    assert "busy" not in [v["video_id"] for v in videos_for_manual_run(WANTED, ["busy"], STATE)]


def test_videos_without_an_id_are_dropped():
    assert videos_for_manual_run([{"video_title": "no id"}], [], {}) == []


def test_no_history_means_everything_goes():
    assert len(videos_for_manual_run(WANTED, [], {})) == 4


# ── the endpoint ─────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.video as videoapi
    from database.video_database import VideoDatabase
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        from flask import g
        g.is_admin = True
        g.can_download = True

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client()


def test_it_refuses_politely_without_a_youtube_folder(client):
    """The commonest setup miss, and the one the drain skips silently."""
    r = client.post("/api/video/wishlist/youtube/download-all")
    assert r.status_code == 400
    assert "YouTube library folder" in r.get_json()["error"]


def test_an_empty_wishlist_is_not_an_error(client):
    import api.video as videoapi
    videoapi._video_db.set_setting("youtube_path", "/yt")
    body = client.post("/api/video/wishlist/youtube/download-all").get_json()
    assert body["success"] is True
    assert body["queued"] == 0 and body["total"] == 0


def test_the_route_is_registered():
    from api.video import create_video_blueprint
    app = Flask(__name__)
    app.register_blueprint(create_video_blueprint(), url_prefix="/api/video")
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/video/wishlist/youtube/download-all" in rules


def test_a_profile_without_download_rights_is_refused(tmp_path):
    """It spends the same disk and bandwidth as the per-row grab, so it takes the
    same permission — download rights, not admin. The rest of the wishlist is
    open to any video profile and this must not quietly be stricter or looser."""
    import api.video as videoapi
    from database.video_database import VideoDatabase
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "v2.db"))
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        from flask import g
        g.is_admin = False
        g.can_download = False

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    assert app.test_client().post("/api/video/wishlist/youtube/download-all").status_code == 403


def test_a_non_admin_who_can_download_is_allowed(tmp_path):
    import api.video as videoapi
    from database.video_database import VideoDatabase
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "v3.db"))
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        from flask import g
        g.is_admin = False
        g.can_download = True

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    # 400 = "no YouTube folder set", i.e. it got past the gate.
    assert app.test_client().post("/api/video/wishlist/youtube/download-all").status_code == 400
