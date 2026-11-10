"""review 5 W3-TB-3: popular-picks / hidden-gems / discovery-shuffle gained the
30-minute shelf cache, keyed by (handler, path, profile) only. Their
service reads the active metadata source, so a source switch keeps serving the
old source's tracks (ids the new source can't resolve) until the entry expires.
BYLT's key carries the source for exactly this reason (_discover_bylt_key)."""
from __future__ import annotations

import pytest
from flask import Flask

import api.discover_routes as routes

STATE = {"source": "spotify", "draws": 0}


class _Service:
    def get_popular_picks(self, limit=50):
        STATE["draws"] += 1
        return [{"track_name": "x", "source": STATE["source"]}]

    def get_hidden_gems(self, limit=50, taste_profile=None):
        STATE["draws"] += 1
        return [{"track_name": "x", "source": STATE["source"]}]

    def get_discovery_shuffle(self, limit=50):
        STATE["draws"] += 1
        return [{"track_name": "x", "source": STATE["source"]}]


@pytest.fixture()
def http(monkeypatch):
    monkeypatch.setattr("core.personalized_playlists.get_personalized_playlists_service",
                        lambda db, sp=None: _Service())
    monkeypatch.setattr(routes, "get_database", lambda: object())
    monkeypatch.setattr(routes, "get_current_profile_id", lambda: 1)
    monkeypatch.setattr(routes, "_spotify_client", lambda: None)
    monkeypatch.setattr(routes, "_discover_genre_taste", lambda db, pid: {})
    monkeypatch.setattr(routes, "_get_active_discovery_source", lambda: STATE["source"])
    # nothing blocked: the outer filter is a no-op
    monkeypatch.setattr("core.discovery.blocked.BlockedArtists.load",
                        classmethod(lambda cls, db, pid: type("B", (), {"is_empty": True})()))
    routes.invalidate_discover_shelf_cache()
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    yield app.test_client()
    routes.invalidate_discover_shelf_cache()


@pytest.mark.parametrize("path", [
    "/api/discover/personalized/popular-picks",
    "/api/discover/personalized/hidden-gems",
    "/api/discover/personalized/discovery-shuffle?limit=50",
])
def test_a_source_switch_reaches_the_shelf(http, path):
    STATE["source"] = "spotify"
    assert http.get(path).get_json()["tracks"][0]["source"] == "spotify"
    STATE["source"] = "deezer"
    assert http.get(path).get_json()["tracks"][0]["source"] == "deezer"


@pytest.mark.parametrize("path", [
    "/api/discover/personalized/popular-picks",
    "/api/discover/personalized/hidden-gems",
    "/api/discover/personalized/discovery-shuffle?limit=50",
])
def test_the_same_source_still_reads_the_cache(http, path):
    """the source rides the key; it must not turn the cache off"""
    STATE["source"] = "spotify"
    STATE["draws"] = 0
    http.get(path)
    http.get(path)
    assert STATE["draws"] == 1
