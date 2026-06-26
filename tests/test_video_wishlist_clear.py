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


def test_counts_endpoint_total_includes_youtube_videos(tmp_path):
    # regression: the header/sidebar wishlist badge reads this 'total'. It used to be
    # movies+episodes only, so a wishlist of only YouTube videos showed NO badge.
    from flask import Flask
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    _seed(db)   # 2 movies, 2 episodes (1 show), 2 youtube videos
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    try:
        r = client.get("/api/video/wishlist/counts").get_json()
        assert r["success"]
        assert r["movie"] == 2 and r["episode"] == 2 and r["video"] == 2
        assert r["total"] == 6                     # movies + episodes + YouTube videos (was 4)
    finally:
        videoapi._video_db = None


def test_counts_endpoint_youtube_only_shows_a_total(tmp_path):
    # the exact reported case: only YouTube videos wished → badge must still show a number
    from flask import Flask
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.add_videos_to_wishlist({"youtube_id": "UC1", "title": "Ch"},
                              [{"youtube_id": "v1", "title": "A"}, {"youtube_id": "v2", "title": "B"}])
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    try:
        r = client.get("/api/video/wishlist/counts").get_json()
        assert r["total"] == 2 and r["video"] == 2 and r["movie"] == 0
    finally:
        videoapi._video_db = None


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


def test_nav_badge_uses_the_endpoint_total_not_a_partial_sum():
    # regression: movie/episode and YouTube counts load separately, so neither setter may
    # compute the nav badge from its partial state (it 'switches' to the TV-only number).
    # Both must defer to the authoritative /wishlist/counts endpoint via refreshBadge().
    import re
    sc = _JS[_JS.index("function setCounts("):_JS.index("function setYtCounts(")]
    syc = _JS[_JS.index("function setYtCounts("):]
    syc = syc[:syc.index("function ", 1)]
    assert "refreshBadge();" in sc and "refreshBadge();" in syc
    assert "state.counts.movie + state.counts.episode" not in _JS   # old partial-total math gone
    assert "/api/video/wishlist/counts" in _JS                       # the endpoint is the source


def test_nav_badge_polls_for_server_side_wishlist_changes():
    # a finished download removes its wishlist item server-side and fires no frontend event,
    # so the badge must poll the count to stay honest (faster while downloads are active).
    assert "function scheduleBadgePoll(" in _JS
    assert "_vdpgAnyActive" in _JS                  # polls quicker while downloads run
    assert "document.hidden" in _JS                 # paused when the tab is hidden
    assert "scheduleBadgePoll();" in _JS            # started from init
