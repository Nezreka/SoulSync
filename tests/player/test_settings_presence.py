"""player settings (per profile) and watching now (who plays what, live)"""

from __future__ import annotations

import pytest
from flask import Flask

import api.player as player_api
from core.player import presence, settings


@pytest.fixture(autouse=True)
def clean():
    presence._sessions.clear()
    settings._cache.clear()
    player_api._who.update({"at": 1e18, "map": {2: {"name": "Ada"}, 3: {"name": "Bo"}}})
    yield
    presence._sessions.clear()
    settings._cache.clear()
    player_api._who.update({"at": 0.0, "map": {}})


@pytest.fixture
def db(tmp_path):
    from database.player_database import PlayerDatabase
    return PlayerDatabase(str(tmp_path / "player.db"))


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(player_api, "player_db", lambda: db)
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(player_api.create_blueprint())
    return app.test_client()


def _as(client, profile_id):
    with client.session_transaction() as sess:
        sess["profile_id"] = profile_id


BEAT = {"session": "tab1", "item_type": "movie", "item_id": "7", "position": 61.5,
        "duration": 6000, "playing": True, "meta": {"title": "Heat"}}


# ── settings ─────────────────────────────────────────────────────────────

def test_settings_default_then_save_per_profile(db):
    assert settings.get(db, 2) == {"share_activity": True, "autoplay_next": True, "still_watching_after": 3}
    settings.put(db, 2, {"share_activity": False, "still_watching_after": 0})
    assert settings.get(db, 2)["share_activity"] is False
    assert settings.get(db, 3)["share_activity"] is True


@pytest.mark.parametrize("bad", [{"nope": 1}, {"share_activity": "yes"}, {"still_watching_after": 99},
                                 {"still_watching_after": True}, {}, None])
def test_bad_settings_are_refused_whole(db, bad):
    with pytest.raises(settings.SettingsError):
        settings.put(db, 2, bad)


def test_settings_routes(client):
    _as(client, 2)
    body = client.get("/api/player/settings").get_json()
    assert body["settings"]["autoplay_next"] is True and "autoplay_next" in body["about"]
    assert client.put("/api/player/settings", json={"autoplay_next": False}).get_json()["settings"][
        "autoplay_next"] is False
    assert client.put("/api/player/settings", json={"autoplay_next": "x"}).status_code == 400


# ── presence ─────────────────────────────────────────────────────────────

def test_a_beat_is_kept_until_it_goes_stale_or_stops():
    presence.beat(2, BEAT, now=1000)
    assert [s.item_id for s in presence.everyone(now=1000 + presence.STALE_SECONDS - 1)] == ["7"]
    assert presence.everyone(now=1000 + presence.STALE_SECONDS + 1) == []
    presence.beat(2, BEAT, now=2000)
    assert presence.stop(2, "tab1") and presence.everyone(now=2000) == []


def test_a_beat_needs_what_and_where():
    for bad in ({}, {**BEAT, "item_type": "game"}, {**BEAT, "position": -1}, {**BEAT, "session": ""}):
        with pytest.raises(presence.PresenceError):
            presence.beat(2, bad)


def test_viewers_see_others_who_share_and_what_they_may_see():
    presence.beat(2, BEAT, now=1000)
    presence.beat(3, {**BEAT, "session": "t2", "item_type": "track", "item_id": "9"}, now=1000)
    presence.beat(4, {**BEAT, "session": "t3"}, now=1000)
    rows = presence.visible_to(2, presence.everyone(now=1000), shares=lambda p: p != 4,
                               may_see=lambda s: s.item_type != "movie")
    assert [(r["profile_id"], r["item_id"]) for r in rows] == [(3, "9")]
    assert "session" not in rows[0]


def test_presence_routes_follow_sharing(client, monkeypatch):
    monkeypatch.setattr(player_api, "_viewer_may_see", lambda s: True)
    _as(client, 2)
    assert client.post("/api/player/presence", json=BEAT).get_json()["shared"] is True
    _as(client, 3)
    seen = client.get("/api/player/presence").get_json()["sessions"]
    assert [(r["profile"]["name"], r["item_id"], r["position"]) for r in seen] == [("Ada", "7", 61.5)]
    # ada stops sharing: gone at once, and her next beats aren't kept
    _as(client, 2)
    client.put("/api/player/settings", json={"share_activity": False})
    assert client.post("/api/player/presence", json=BEAT).get_json()["shared"] is False
    _as(client, 3)
    assert client.get("/api/player/presence").get_json()["sessions"] == []


def test_a_viewer_never_sees_itself(client, monkeypatch):
    monkeypatch.setattr(player_api, "_viewer_may_see", lambda s: True)
    _as(client, 2)
    client.post("/api/player/presence", json=BEAT)
    assert client.get("/api/player/presence").get_json()["sessions"] == []


def test_together_finds_what_you_both_started(db):
    from core.player import progress
    progress.save(db, 2, "episode", "e5", 300, 1400, meta={"title": "Ep 5", "parent_id": "77"})
    progress.save(db, 3, "episode", "e9", 900, 1400, meta={"title": "Ep 9", "parent_id": "77"})
    progress.save(db, 3, "movie", "m1", 10, 100)  # only bo
    progress.save(db, 4, "episode", "e1", 10, 100, meta={"parent_id": "77"})  # doesn't share
    rows = presence.together(db, 2, shares=lambda p: p != 4, may_see=lambda s: True)
    assert [r["key"] for r in rows] == ["show:77"]
    assert rows[0]["mine"]["item_id"] == "e5"
    assert [(o["profile_id"], o["item_id"]) for o in rows[0]["others"]] == [(3, "e9")]
    assert presence.together(db, 2, shares=lambda p: True, may_see=lambda s: False) == []


def test_together_route_names_people(client, db, monkeypatch):
    from core.player import progress
    monkeypatch.setattr(player_api, "_viewer_may_see", lambda s: True)
    progress.save(db, 2, "movie", "m1", 300, 6000)
    progress.save(db, 3, "movie", "m1", 900, 6000)
    _as(client, 2)
    rows = client.get("/api/player/presence/together").get_json()["titles"]
    assert rows[0]["others"][0]["profile"]["name"] == "Bo"
