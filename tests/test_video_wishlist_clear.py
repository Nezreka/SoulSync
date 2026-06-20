"""Wishlist 'Clear all' — empties one tab (movies / TV / YouTube) in a single call.

The three tabs map to different rows in video_wishlist: movies=kind 'movie',
TV=kind 'episode', YouTube=kind 'video'. clear_wishlist(kind) removes exactly the
one tab's rows and leaves the others alone.
"""

from __future__ import annotations

import pytest

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seed(db):
    db.add_movie_to_wishlist(1, "M1")
    db.add_movie_to_wishlist(2, "M2")
    db.add_episodes_to_wishlist(9, "Show", [
        {"season_number": 1, "episode_number": 1},
        {"season_number": 1, "episode_number": 2}])
    db.add_videos_to_wishlist({"youtube_id": "ch1", "title": "Ch"},
                              [{"youtube_id": "v1", "title": "V1"},
                               {"youtube_id": "v2", "title": "V2"}])


def test_clear_each_tab_removes_only_its_own_rows(db):
    _seed(db)
    assert db.wishlist_counts()["movie"] == 2
    assert db.wishlist_counts()["episode"] == 2
    assert db.youtube_wishlist_counts()["video"] == 2

    # clear movies → TV + YouTube untouched
    assert db.clear_wishlist("movie") == 2
    assert db.wishlist_counts()["movie"] == 0
    assert db.wishlist_counts()["episode"] == 2
    assert db.youtube_wishlist_counts()["video"] == 2

    # clear TV → only episodes gone
    assert db.clear_wishlist("show") == 2
    assert db.wishlist_counts()["episode"] == 0
    assert db.youtube_wishlist_counts()["video"] == 2

    # clear YouTube
    assert db.clear_wishlist("youtube") == 2
    assert db.youtube_wishlist_counts()["video"] == 0


def test_clear_rejects_unknown_kind(db):
    _seed(db)
    assert db.clear_wishlist("nope") == 0
    assert db.wishlist_counts()["movie"] == 2     # nothing removed


def test_clear_empty_tab_is_a_noop(db):
    assert db.clear_wishlist("movie") == 0


def test_clear_endpoint(tmp_path):
    from flask import Flask
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    _seed(db)
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    try:
        r = client.post("/api/video/wishlist/clear", json={"kind": "movie"}).get_json()
        assert r["success"] and r["removed"] == 2 and r["counts"]["movie"] == 0
        assert client.post("/api/video/wishlist/clear", json={"kind": "bad"}).status_code == 400
    finally:
        videoapi._video_db = None


# ── frontend wiring ─────────────────────────────────────────────────────────
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "video" / "video-wishlist.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


def test_clear_button_wired_for_all_tabs():
    assert "data-vwsh-clear" in _INDEX
    assert "function clearAll(" in _JS
    assert "/api/video/wishlist/clear" in _JS
    assert "kind: kind" in _JS                       # clears the active tab
    # shown only when the active tab has items
    assert "function updateClearBtn(" in _JS
