"""The calendar says where each episode stands, not just when it airs.

Before this the feed carried the air date and `has_file`. That draws a grid and
nothing more: an episode that aired on Tuesday and is still missing looked
exactly like one that aired on Tuesday and is deliberately unmonitored, and both
looked exactly like one that was downloading at that moment.

Also covered: a calendar that has not refreshed is not empty, it is confidently
wrong — showing last month's idea of next week — so the feed reports its own
freshness and a never-refreshed install reads as stale rather than as fine.
"""

from __future__ import annotations

import json

import pytest
from flask import Flask

from database.video_database import VideoDatabase


@pytest.fixture()
def client(tmp_path):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    yield app.test_client(), videoapi._video_db
    videoapi._video_db = None


def _seed(db, air_date="2026-06-20", episodes=None):
    eps = episodes or [{"episode_number": 1, "title": "E1", "air_date": air_date}]
    return db.upsert_show_tree("plex", {
        "server_id": "s1", "title": "Show", "tmdb_id": 1396,
        "seasons": [{"season_number": 1, "episodes": eps}]})


def _cal(c, start="2026-06-01", days=30):
    return c.get("/api/video/calendar?start=%s&days=%d&scope=all" % (start, days)).get_json()


def _states(body):
    return {(e["season_number"], e["episode_number"]): e["acq"] for e in body["episodes"]}


def test_every_episode_carries_a_state_and_the_window_counts_them(client, monkeypatch):
    c, db = client
    _seed(db, episodes=[
        {"episode_number": 1, "title": "Owned", "air_date": "2026-06-10",
         "file": {"relative_path": "e1.mkv", "size_bytes": 10}},
        {"episode_number": 2, "title": "Missing", "air_date": "2026-06-11"},
        {"episode_number": 3, "title": "Ignored", "air_date": "2026-06-12"},
        {"episode_number": 4, "title": "Wanted", "air_date": "2026-06-13"},
    ])
    conn = db._get_connection()
    conn.execute("UPDATE episodes SET monitored=0 WHERE episode_number=3")
    conn.execute("INSERT INTO video_wishlist (kind, tmdb_id, title, season_number, "
                 "episode_number, status) VALUES ('episode',1396,'Show',1,4,'wanted')")
    conn.commit(); conn.close()
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda: "plex")

    body = _cal(c)
    assert _states(body) == {(1, 1): "owned", (1, 2): "missing",
                             (1, 3): "ignored", (1, 4): "wanted"}
    # Counts come back in reading order so the header strip cannot reshuffle.
    assert body["acq_counts"] == [{"state": "owned", "count": 1},
                                  {"state": "wanted", "count": 1},
                                  {"state": "ignored", "count": 1},
                                  {"state": "missing", "count": 1}]
    # Only the row nothing is handling wants a human.
    assert body["needs_action"] == 1


def test_a_live_download_shows_on_the_card(client, monkeypatch):
    """Otherwise an episode being fetched right now reads as abandoned."""
    c, db = client
    _seed(db, episodes=[{"episode_number": 1, "title": "E1", "air_date": "2026-06-10"}])
    conn = db._get_connection()
    conn.execute("INSERT INTO video_downloads (kind, media_source, media_id, status, search_ctx) "
                 "VALUES ('episode','tmdb','1396','downloading',?)",
                 (json.dumps({"scope": "episode", "season": 1, "episode": 1}),))
    conn.commit(); conn.close()
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda: "plex")

    body = _cal(c)
    assert _states(body)[(1, 1)] == "downloading"
    assert body["needs_action"] == 0, "something in flight is not waiting on you"


def test_a_season_pack_marks_the_episodes_it_covers(client, monkeypatch):
    """A pack is one download row for many episodes; matching only per-episode
    identities would leave all of them looking abandoned mid-transfer."""
    c, db = client
    _seed(db, episodes=[{"episode_number": n, "title": "E%d" % n,
                         "air_date": "2026-06-1%d" % n} for n in (1, 2, 3)])
    conn = db._get_connection()
    conn.execute("INSERT INTO video_downloads (kind, media_source, media_id, status, search_ctx) "
                 "VALUES ('season','tmdb','1396','downloading',?)",
                 (json.dumps({"scope": "season", "season": 1}),))
    conn.commit(); conn.close()
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda: "plex")

    assert set(_states(_cal(c)).values()) == {"downloading"}


def test_an_unrefreshed_schedule_says_so(client, monkeypatch):
    c, db = client
    _seed(db)
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda: "plex")

    # Never refreshed reads as STALE, not as fine: nothing has ever confirmed
    # that this window reflects what is actually airing.
    sched = _cal(c)["schedule"]
    assert sched["stale"] is True and sched["refreshed_at"] is None

    db.mark_airing_schedule_refreshed()
    sched = _cal(c)["schedule"]
    assert sched["stale"] is False and sched["refreshed_at"]
    assert sched["age_days"] < 1


def test_a_schedule_refreshed_long_ago_goes_stale_again(client, monkeypatch):
    c, db = client
    _seed(db)
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda: "plex")
    db.set_setting("airing_schedule_refreshed_at", "2026-01-01 00:00:00")
    sched = _cal(c)["schedule"]
    assert sched["stale"] is True
    assert sched["age_days"] > db.SCHEDULE_STALE_DAYS
