"""The Discover adventurousness dial is per profile, falling back to the
global default (Settings > Discovery). One person's dial must never move
anyone else's Discover."""
from __future__ import annotations

import pytest
from flask import Flask

import api.discover_routes as routes
from core.discovery import adventurousness as adv
from database.music_database import MusicDatabase


class _Cfg:
    def __init__(self, value=0.3):
        self.values = {"discover.adventurousness": value}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    other = db.create_profile("Other")
    cfg = _Cfg(0.3)
    who = {"id": 1}
    monkeypatch.setattr(routes, "get_database", lambda: db)
    monkeypatch.setattr(routes, "config_manager", cfg)
    monkeypatch.setattr(routes, "get_current_profile_id", lambda: who["id"])
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    return db, cfg, who, other, app.test_client()


def test_column_exists_and_defaults_to_following_the_global(env):
    db, _cfg, _who, other, _http = env
    assert db.get_profile_adventurousness(1) is None
    assert db.get_profile_adventurousness(other) is None


def test_a_profile_without_its_own_dial_reads_the_global_default(env):
    _db, cfg, _who, _other, http = env
    cfg.set("discover.adventurousness", 0.65)
    body = http.get("/api/discover/adventurousness").get_json()
    assert body == {"success": True, "value": 0.65, "default": 0.65, "own": False}


def test_turning_my_dial_leaves_everyone_else_and_the_default_alone(env):
    db, cfg, who, other, http = env
    who["id"] = other
    body = http.post("/api/discover/adventurousness", json={"value": 0.9}).get_json()
    assert body["value"] == 0.9 and body["own"] is True and body["default"] == 0.3
    assert cfg.get("discover.adventurousness") == 0.3       # global untouched
    assert db.get_profile_adventurousness(other) == 0.9
    who["id"] = 1
    mine = http.get("/api/discover/adventurousness").get_json()
    assert mine["value"] == 0.3 and mine["own"] is False


def test_the_dial_is_clamped_and_a_non_number_is_refused(env):
    db, _cfg, _who, _other, http = env
    assert http.post("/api/discover/adventurousness", json={"value": 4}).get_json()["value"] == 1.0
    assert http.post("/api/discover/adventurousness", json={"value": -1}).get_json()["value"] == 0.0
    resp = http.post("/api/discover/adventurousness", json={"value": "lots"})
    assert resp.status_code == 400
    assert db.get_profile_adventurousness(1) == 0.0


def test_reset_follows_the_default_again(env):
    db, cfg, _who, _other, http = env
    http.post("/api/discover/adventurousness", json={"value": 0.8})
    body = http.post("/api/discover/adventurousness", json={"reset": True}).get_json()
    assert body["own"] is False and body["value"] == 0.3
    assert db.get_profile_adventurousness(1) is None
    cfg.set("discover.adventurousness", 0.5)
    assert http.get("/api/discover/adventurousness").get_json()["value"] == 0.5


def test_zero_is_a_real_dial_not_a_fallback(env):
    db, cfg, _who, _other, _http = env
    adv.set_own_level(db, 1, 0.0)
    assert adv.profile_level(db, 1, cfg) == 0.0


def test_the_rerank_and_its_cache_key_read_the_profile_dial(env):
    db, cfg, who, other, _http = env
    adv.set_own_level(db, other, 0.95)
    who["id"] = 1
    assert routes._profile_dial() == 0.3
    key_mine = routes._discover_dial_key()
    who["id"] = other
    assert routes._profile_dial() == 0.95
    assert routes._discover_dial_key() != key_mine


def test_similar_artists_selects_with_the_profile_dial(env, monkeypatch):
    db, _cfg, who, other, http = env
    adv.set_own_level(db, other, 0.8)
    seen = {}

    def _top(**kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(db, "get_top_similar_artists", _top)
    monkeypatch.setattr(routes, "_get_active_discovery_source", lambda: "spotify")
    monkeypatch.setattr(routes, "config_manager", type("C", (_Cfg,), {
        "get_active_media_server": lambda self: "plex"})(0.3))
    who["id"] = other
    routes.invalidate_discover_shelf_cache()
    http.get("/api/discover/similar-artists")
    routes.invalidate_discover_shelf_cache()
    assert seen.get("adventurousness") == 0.8
