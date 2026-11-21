"""studio and network pages: a name to tmdb's id, and everything it made,
through the video side's discover, with the profile's kinds and cap."""

from __future__ import annotations

from flask import Flask

import api.player as player_api
from core.player import makers


def test_pick_company_prefers_the_exact_name():
    results = [{"tmdb_id": 1, "title": "A24 Films"}, {"tmdb_id": 2, "title": "a24"}]
    assert makers.pick_company(results, "A24") == 2
    assert makers.pick_company([{"tmdb_id": 9, "title": "Pixar Animation"}], "Pixar") == 9
    assert makers.pick_company([], "Pixar") is None


def test_pick_network_matches_the_name_only():
    nets = [{"id": 49, "name": "HBO"}, {"id": 3186, "name": "HBO Max"}]
    assert makers.pick_network(nets, "hbo max") == 3186
    assert makers.pick_network(nets, "AMC") is None


class Engine:
    def __init__(self):
        self.calls = []

    def company_search(self, name):
        return [{"tmdb_id": 41077, "title": "A24"}]

    def company_detail(self, cid):
        return {"tmdb_id": cid, "name": "A24"}

    def discover_filter(self, kind, **kw):
        self.calls.append((kind, kw))
        return [{"tmdb_id": 1, "title": "Kept", "library_id": 5}, {"tmdb_id": 2, "title": "Capped"}]


def _client(monkeypatch, view, engine):
    import core.video.enrichment.engine as engine_mod
    from api.video import kids
    monkeypatch.setattr(engine_mod, "get_video_enrichment_engine", lambda: engine)
    monkeypatch.setattr(player_api, "_video_view", lambda: view)
    monkeypatch.setattr(player_api, "get_video_db", lambda: None)
    monkeypatch.setattr(kids, "filter_tmdb_items",
                        lambda db, items, cap: [i for i in items if i["title"] != "Capped"])
    makers._ids.clear()
    app = Flask(__name__)
    app.secret_key = "t"
    app.register_blueprint(player_api.create_blueprint())
    return app.test_client()


def test_studio_titles_come_from_discover_by_company(monkeypatch):
    engine = Engine()
    client = _client(monkeypatch, (True, True, True, None), engine)
    body = client.get("/api/player/maker?kind=studio&name=A24&sort=rated&page=2").get_json()
    assert body["found"] and body["card"]["name"] == "A24"
    assert [t["title"] for t in body["titles"]] == ["Kept", "Capped"]
    assert body["titles"][0]["kind"] == "movie" and body["titles"][0]["library_id"] == 5
    kind, kw = engine.calls[0]
    assert kind == "movie" and kw["companies"] == "41077" and kw["page"] == 2
    assert kw["sort_by"] == "vote_average.desc" and kw["vote_count_min"] == 200


def test_the_rating_cap_and_the_kinds_hold(monkeypatch):
    from api.video.kids import VideoCap
    client = _client(monkeypatch, (True, False, True, VideoCap(movie=13)), Engine())
    body = client.get("/api/player/maker?kind=studio&name=A24").get_json()
    assert [t["title"] for t in body["titles"]] == ["Kept"]
    # no tv on this profile: no network page
    assert client.get("/api/player/maker?kind=network&name=HBO").status_code == 403
    assert client.get("/api/player/maker?kind=label&name=x").status_code == 400


def test_logos_route_takes_a_short_clean_list(monkeypatch):
    class Logos:
        def title_logo(self, kind, tmdb_id):
            return f"https://img/{kind}{tmdb_id}.png" if tmdb_id != 2 else ""

    client = _client(monkeypatch, (True, True, True, None), Logos())
    body = client.get("/api/player/logos?items=movie:1,show:2,bad:3,movie:x").get_json()
    assert body["logos"] == {"movie:1": "https://img/movie1.png", "show:2": None}
    many = ",".join(f"movie:{i}" for i in range(1, 30))
    assert len(client.get(f"/api/player/logos?items={many}").get_json()["logos"]) == 12
