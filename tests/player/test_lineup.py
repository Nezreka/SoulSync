"""the guide's channels (the server's and a profile's own) and each profile's lineup"""

from __future__ import annotations

import pytest
from flask import Flask, g

import api.player as player_api
from core.player import lineup


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

    @app.before_request
    def _admin():
        from flask import session
        g.is_admin = session.get("profile_id") == 1
    return app.test_client()


def _as(client, profile_id):
    with client.session_transaction() as sess:
        sess["profile_id"] = profile_id


SPEC = {"name": "  Late   night  ", "kinds": ["movies", "games"], "genres": ["Horror", "Horror"],
        "decades": [1980, 1985], "min_score": 7}


def test_a_spec_is_cleaned():
    spec = lineup.clean_spec(SPEC)
    assert spec["name"] == "Late night"
    assert spec["kinds"] == ["movies"] and spec["genres"] == ["Horror"]
    assert spec["decades"] == [1980] and spec["order"] == "shuffle"
    marathon = lineup.clean_spec({"name": "All of it", "show": 77})
    assert marathon["order"] == "in_order" and marathon["kinds"] == []


@pytest.mark.parametrize("bad", [None, {}, {"name": "x"}, {"name": "x", "kinds": ["movies"], "min_score": 11},
                                 {"name": "x", "kinds": ["movies"], "order": "random"},
                                 {"name": "x", "kinds": ["movies"], "genres": "Horror"}])
def test_a_bad_spec_is_refused(bad):
    with pytest.raises(lineup.LineupError):
        lineup.clean_spec(bad)


def test_server_channels_for_everyone_own_ones_for_you(db):
    lineup.add_channel(db, None, {"name": "Saturday matinee", "kinds": ["movies"]})
    lineup.add_channel(db, 2, {"name": "Mine", "kinds": ["tv"]})
    assert [c["spec"]["name"] for c in lineup.list_channels(db, 2)["mine"]] == ["Mine"]
    assert [c["spec"]["name"] for c in lineup.list_channels(db, 3)["server"]] == ["Saturday matinee"]
    assert lineup.list_channels(db, 3)["mine"] == []


def test_only_yours_to_change_a_servers_only_an_admins(db):
    server = lineup.add_channel(db, None, {"name": "S", "kinds": ["movies"]})
    mine = lineup.add_channel(db, 2, {"name": "M", "kinds": ["tv"]})
    assert lineup.edit_channel(db, 3, mine["id"], {"name": "x", "kinds": ["tv"]}) is None
    assert lineup.edit_channel(db, 2, server["id"], {"name": "x", "kinds": ["tv"]}) is None
    assert lineup.edit_channel(db, 1, server["id"], {"name": "x", "kinds": ["tv"]}, admin=True)["spec"]["name"] == "x"
    assert not lineup.remove_channel(db, 3, mine["id"])
    lineup.put_lineup(db, 2, [{"channel_id": mine["id"]}])
    assert lineup.remove_channel(db, 2, mine["id"])
    assert lineup.get_lineup(db, 2) == []


def test_a_lineup_is_kept_in_order_per_profile(db):
    out = lineup.put_lineup(db, 2, [{"channel_id": "genre-comedy", "name": " Laughs "},
                                    {"channel_id": "kids", "hidden": True},
                                    {"channel_id": "genre-comedy"}])
    assert [(e["channel_id"], e["position"], e["hidden"], e["name"]) for e in out] == [
        ("genre-comedy", 0, False, "Laughs"), ("kids", 1, True, None)]
    assert lineup.get_lineup(db, 3) == []


def test_routes(client):
    _as(client, 2)
    assert client.post("/api/player/guide/channels", json={"scope": "server", "spec": {"name": "x", "kinds": ["tv"]}}
                       ).status_code == 403
    made = client.post("/api/player/guide/channels", json={"scope": "mine", "spec": {"name": "Mine", "kinds": ["tv"]}})
    assert made.status_code == 200
    _as(client, 1)
    assert client.post("/api/player/guide/channels", json={"scope": "server", "spec": {"name": "All", "kinds": ["tv"]}}
                       ).status_code == 200
    body = client.get("/api/player/guide/channels").get_json()
    assert body["can_edit_server"] is True and [c["spec"]["name"] for c in body["server"]] == ["All"]
    assert body["mine"] == []
    _as(client, 2)
    assert client.put("/api/player/guide/lineup", json={"entries": [{"channel_id": "kids", "hidden": True}]}
                      ).get_json()["entries"][0]["hidden"] is True
    assert client.put("/api/player/guide/lineup", json={"entries": "x"}).status_code == 400
