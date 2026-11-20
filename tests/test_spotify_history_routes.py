"""the spotify history routes on a bare app: each profile imports into its own
pile, a member without Listening stats is refused, and remove takes only the
caller's import."""

from __future__ import annotations

import pytest
from flask import Flask, g

import api.stats as stats_api
from database.music_database import MusicDatabase
from tests.permissions_fixtures import member_perms


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "music.db"))
    state = {"profile": 1, "perms": None, "rebuilt": []}

    class Worker:
        def _build_stats_cache(self, owners=None):
            state["rebuilt"].append(owners)

    monkeypatch.setattr(stats_api, "get_database", lambda: db)
    monkeypatch.setattr(stats_api, "_listening_stats_worker", lambda: Worker())
    monkeypatch.setattr(stats_api, "get_current_profile_id", lambda: state["profile"])

    class _Sync:
        def __init__(self, target, **_kw):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(stats_api.threading, "Thread", _Sync)
    app = Flask(__name__)
    app.register_blueprint(stats_api.bp)

    @app.before_request
    def _perms():
        if state["perms"] is not None:
            g.perms = state["perms"]

    return app.test_client(), db, state


def _play(track="Song", artist="Band", ended="2024-01-15T08:23:45Z", **extra):
    return {"ended_at": ended, "ms_played": 200000, "track": track, "artist": artist,
            "uri": "spotify:track:4uLU6hMCjMI75M1A2tKUQC", **extra}


def test_import_finish_and_summary(env):
    client, _db, state = env

    r = client.post("/api/listening-import/spotify/batch",
                    json={"rows": [_play(), _play("Short", ms_played=1000)], "include_in_stats": True})
    body = r.get_json()
    assert r.status_code == 200 and body["added"] == 1
    assert body["dropped"]["short"] == 1

    done = client.post("/api/listening-import/spotify/finish").get_json()
    assert done["imported"] is True and done["plays"] == 1
    assert state["rebuilt"] == [[1]]
    assert client.get("/api/listening-import/spotify").get_json()["plays"] == 1


def test_a_member_imports_into_their_own_pile(env):
    client, db, state = env
    kim = db.create_profile(name="kim")
    state["profile"] = kim

    client.post("/api/listening-import/spotify/batch", json={"rows": [_play()]})

    assert client.get("/api/listening-import/spotify").get_json()["plays"] == 1
    state["profile"] = 1
    assert client.get("/api/listening-import/spotify").get_json()["imported"] is False


def test_remove_takes_only_the_callers_import(env):
    client, db, state = env
    kim = db.create_profile(name="kim")
    client.post("/api/listening-import/spotify/batch", json={"rows": [_play()]})
    state["profile"] = kim
    client.post("/api/listening-import/spotify/batch", json={"rows": [_play()]})

    r = client.delete("/api/listening-import/spotify")

    assert r.get_json() == {"success": True, "removed": 1}
    state["profile"] = 1
    assert client.get("/api/listening-import/spotify").get_json()["plays"] == 1


def test_settings_switch_taste_only(env):
    client, _db, _state = env
    assert client.post("/api/listening-import/spotify/settings",
                       json={"include_in_stats": False}).status_code == 404
    client.post("/api/listening-import/spotify/batch", json={"rows": [_play()]})

    body = client.post("/api/listening-import/spotify/settings",
                       json={"include_in_stats": False}).get_json()

    assert body["include_in_stats"] is False
    assert client.post("/api/listening-import/spotify/settings", json={}).status_code == 400


def test_bad_bodies_are_refused(env):
    client, _db, _state = env
    assert client.post("/api/listening-import/spotify/batch", json={"rows": "x"}).status_code == 400
    too_many = [_play()] * 5001
    assert client.post("/api/listening-import/spotify/batch",
                       json={"rows": too_many}).status_code == 400


def test_a_member_without_listening_stats_is_refused(env):
    client, db, state = env
    state["profile"] = db.create_profile(name="kid")
    state["perms"] = member_perms("adult", **{"music.stats": False})

    for method, path in (("get", "/api/listening-import/spotify"),
                         ("post", "/api/listening-import/spotify/batch"),
                         ("post", "/api/listening-import/spotify/finish"),
                         ("post", "/api/listening-import/spotify/settings"),
                         ("delete", "/api/listening-import/spotify")):
        r = getattr(client, method)(path, json={"rows": [_play()]})
        assert r.status_code == 403, path
    assert db.get_top_artists("all", 10, profile_id=state["profile"]) == []
