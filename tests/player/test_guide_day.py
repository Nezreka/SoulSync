"""the guide's pool is frozen for the day, per view: a library change mid-day
must not re-seed everyone's shared timeline."""

from __future__ import annotations

import pytest

from core.player import guide_day


@pytest.fixture
def db(tmp_path):
    from database.player_database import PlayerDatabase
    return PlayerDatabase(str(tmp_path / "player.db"))


def test_the_first_pool_of_the_day_holds_all_day(db):
    calls = []
    first = guide_day.frozen(db, "v", lambda: calls.append(1) or {"movies": [1]}, day="2026-11-24")
    later = guide_day.frozen(db, "v", lambda: calls.append(1) or {"movies": [1, 2]}, day="2026-11-24")
    assert first == later == {"movies": [1]}
    assert len(calls) == 1


def test_tomorrow_takes_a_new_one_and_drops_yesterday(db):
    guide_day.frozen(db, "v", lambda: {"movies": [1]}, day="2026-11-24")
    assert guide_day.frozen(db, "v", lambda: {"movies": [1, 2]}, day="2026-11-25") == {"movies": [1, 2]}
    with db._get_connection() as conn:
        days = [r[0] for r in conn.execute("SELECT day FROM player_guide_day")]
    assert days == ["2026-11-25"]


def test_each_view_has_its_own(db):
    guide_day.frozen(db, "adult", lambda: {"movies": ["R"]}, day="d")
    assert guide_day.frozen(db, "kids", lambda: {"movies": ["G"]}, day="d") == {"movies": ["G"]}


def test_an_empty_pool_is_not_frozen(db):
    assert guide_day.frozen(db, "v", lambda: {"movies": []}, day="d",
                            keep=lambda c: bool(c["movies"])) == {"movies": []}
    assert guide_day.frozen(db, "v", lambda: {"movies": [1]}, day="d",
                            keep=lambda c: bool(c["movies"])) == {"movies": [1]}


def test_the_route_freezes_only_when_asked(db, monkeypatch):
    from flask import Flask

    import api.player as player_api
    monkeypatch.setattr(player_api, "player_db", lambda: db)
    monkeypatch.setattr(player_api, "_video_view", lambda: (True, True, True, None))
    pool = {"movies": [{"id": 1}], "shows": [], "youtube": []}
    monkeypatch.setattr(player_api, "_read_catalog", lambda *a, **k: None)
    monkeypatch.setattr(player_api, "shape_video_catalog", lambda *a, **k: dict(pool))
    app = Flask(__name__)
    app.secret_key = "t"
    app.register_blueprint(player_api.create_blueprint())
    c = app.test_client()
    with c.session_transaction() as s:
        s["profile_id"] = 1
    assert c.get("/api/player/video-catalog?guide=1").get_json()["movies"] == [{"id": 1}]
    pool["movies"] = [{"id": 1}, {"id": 2}]
    # the guide keeps today's; everything else sees the new movie
    assert c.get("/api/player/video-catalog?guide=1").get_json()["movies"] == [{"id": 1}]
    assert c.get("/api/player/video-catalog").get_json()["movies"] == [{"id": 1}, {"id": 2}]
