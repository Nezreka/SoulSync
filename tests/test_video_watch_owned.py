"""The watch-together seams: ownership probe + hydrated grab (/api/video/watch/*).

The chat page's movie-night ballot is shared state (a fold over the room's
protocol bus), but ownership is personal — each client asks its OWN video
library "can this box play these". The probe answers per nomination key
("m:603", "t:1399:1x1"), keyed exactly like chat-protocol.js reduceWatch, and
"owned" means a playable file (video_stored_file_path), not library presence.

The GRAB hydrates server-side: the bus only carries id/title/poster, but a
wishlist row rendered without year/poster/detail blob (movies) or episode
title/still/air date/season poster (episodes) shows up art-less on the
wishlist page — so the endpoint fetches the full TMDB context itself and the
bus fields are merely the offline fallback.
"""

from __future__ import annotations

import sys
import types

from flask import Flask


class _StubDb:
    """Only what the routes touch — fixed ownership + recorded wishlist writes."""

    OWNED = {("movie", 603, None, None), ("episode", 1399, 1, 1)}
    LIBRARY = {("show", 1399): 77}          # the show exists in the library

    def __init__(self):
        self.calls = []
        self.movie_adds = []
        self.episode_adds = []

    def video_stored_file_path(self, kind, *, tmdb_id=None, season=None, episode=None):
        self.calls.append((kind, tmdb_id, season, episode))
        if (kind, tmdb_id, season, episode) in self.OWNED:
            return {"path": "Movies/x.mkv", "size_bytes": 1}
        return None

    def library_id_for_tmdb(self, kind, tmdb_id, server_source=None):
        return self.LIBRARY.get((kind, int(tmdb_id)))

    def add_movie_to_wishlist(self, tmdb_id, title, **kw):
        self.movie_adds.append({"tmdb_id": tmdb_id, "title": title, **kw})
        return True

    def add_episodes_to_wishlist(self, show_tmdb_id, show_title, episodes, **kw):
        self.episode_adds.append({"tmdb_id": show_tmdb_id, "title": show_title,
                                  "episodes": episodes, **kw})
        return len(episodes)


def _client(stub):
    import api.video as videoapi
    videoapi._video_db = stub
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client()


def _stub_video_world(monkeypatch, *, engine=None, searches=None):
    """Swap the heavy lazy imports (engine / server resolver / manual search)
    for in-memory stubs — the routes import these INSIDE the request."""
    eng_mod = types.ModuleType("core.video.enrichment.engine")
    eng_mod.get_video_enrichment_engine = lambda: engine
    src_mod = types.ModuleType("core.video.sources")
    src_mod.resolve_video_server = lambda: "plex"
    ws_mod = types.ModuleType("core.video.wishlist_search")
    ws_mod.manual_search = lambda scope, tmdb_id, **kw: (
        (searches if searches is not None else []).append((scope, tmdb_id, kw)) or {})
    monkeypatch.setitem(sys.modules, "core.video.enrichment.engine", eng_mod)
    monkeypatch.setitem(sys.modules, "core.video.sources", src_mod)
    monkeypatch.setitem(sys.modules, "core.video.wishlist_search", ws_mod)


class _StubEngine:
    def tmdb_detail(self, kind, tmdb_id):
        if kind == "movie":
            return {"title": "The Matrix", "year": 1999, "overview": "whoa",
                    "poster_url": "https://img/matrix.jpg", "backdrop_url": "https://img/bd.jpg",
                    "genres": ["Sci-Fi"], "rating": 8.2, "runtime_minutes": 136,
                    "cast": [{"name": "Keanu"}],
                    "crew": [{"name": "Lana Wachowski", "job": "Director"}]}
        return {"title": "Game of Thrones", "poster_url": "https://img/got.jpg"}

    def tmdb_season(self, tv_id, season_number):
        return {"season_number": season_number, "poster_url": "https://img/s1.jpg",
                "episodes": [{"episode_number": 1, "title": "Winter Is Coming",
                              "overview": "ep1", "air_date": "2011-04-17",
                              "still_url": "https://img/e1.jpg"}]}


def test_watch_library_search_is_owned_only_and_bus_safe(monkeypatch):
    """The picker searches the user's LIBRARY (status=owned), and the poster
    field that can ride the chat bus is TMDB-CDN-or-nothing — a raw library
    artwork path can be a tokened Plex/Jellyfin URL and must never leak into
    a public Soulseek room."""
    class _LibDb(_StubDb):
        def query_library(self, kind, **kw):
            assert kw["status"] == "owned"          # never nominate what nobody can play
            if kind == "movies":
                return {"items": [
                    {"id": 5, "tmdb_id": 603, "title": "The Matrix", "year": 1999,
                     "rating": 8.2, "has_poster": 1},
                    {"id": 6, "tmdb_id": None, "title": "Unmatched", "year": None,
                     "rating": None, "has_poster": 0},   # no tmdb id → not nominate-able
                ]}
            return {"items": [
                {"id": 9, "tmdb_id": 1399, "title": "Game of Thrones", "year": 2011,
                 "rating": 9.2, "has_poster": 1, "owned_count": 50, "episode_count": 73},
            ]}

        def owned_by_tmdb_ids(self, media_type, tmdb_ids):
            if media_type == "movie":
                return [{"tmdb_id": 603, "poster_url": "/library/metadata/1/thumb/2"}]
            return [{"tmdb_id": 1399, "poster_url": "https://image.tmdb.org/t/p/w300/got.jpg"}]

    _stub_video_world(monkeypatch)
    c = _client(_LibDb())
    r = c.get("/api/video/watch/library?q=ma")
    assert r.status_code == 200
    rows = r.get_json()["results"]
    assert [x["tmdb_id"] for x in rows] == [603, 1399]   # the tmdb-less row dropped
    movie, show = rows
    assert movie["art"] == "/api/video/poster/movie/5?w=185"   # local proxy for MY ui
    assert movie["po"] is None                                 # Plex path never on the bus
    assert show["po"] == "https://image.tmdb.org/t/p/w300/got.jpg"
    assert show["owned_count"] == 50 and show["episode_count"] == 73
    # Sub-2-char queries don't touch the db at all.
    assert c.get("/api/video/watch/library?q=m").get_json()["results"] == []


def test_grab_movie_hydrates_the_full_wishlist_row(monkeypatch):
    db = _StubDb()
    searches = []
    _stub_video_world(monkeypatch, engine=_StubEngine(), searches=searches)
    c = _client(db)
    r = c.post("/api/video/watch/grab", json={"kd": "m", "id": "603", "ti": "bus title"})
    assert r.status_code == 200 and r.get_json()["success"] is True
    add = db.movie_adds[0]
    # TMDB detail outranks the bus fallback on every field.
    assert add["title"] == "The Matrix"
    assert add["year"] == 1999
    assert add["poster_url"] == "https://img/matrix.jpg"
    assert add["server_source"] == "plex"
    blob = add["detail_json"]
    assert blob["director"] == "Lana Wachowski"
    assert blob["added_via"] == {"source": "movie-night"}
    # No similar/recommendation rails in the blob — lean card fields only.
    assert "similar" not in blob and "recommendations" not in blob
    assert searches == [("movie", 603, {})]


def test_grab_movie_degrades_to_bus_context_when_tmdb_is_down(monkeypatch):
    class _DeadEngine:
        def tmdb_detail(self, kind, tmdb_id):
            raise RuntimeError("tmdb outage")

    db = _StubDb()
    searches = []
    _stub_video_world(monkeypatch, engine=_DeadEngine(), searches=searches)
    c = _client(db)
    r = c.post("/api/video/watch/grab", json={
        "kd": "m", "id": 550, "ti": "Fight Club", "y": "1999", "po": "https://img/fc.jpg"})
    assert r.status_code == 200 and r.get_json()["success"] is True
    add = db.movie_adds[0]
    assert add["title"] == "Fight Club"
    assert add["year"] == 1999
    assert add["poster_url"] == "https://img/fc.jpg"
    assert add["detail_json"] is None       # honest bare row, filled in later
    assert searches == [("movie", 550, {})]
    # No title from ANY source → the grab is refused, not a nameless row.
    r2 = c.post("/api/video/watch/grab", json={"kd": "m", "id": 551})
    assert r2.status_code == 400
    assert len(db.movie_adds) == 1


def test_grab_episode_carries_still_air_date_and_season_poster(monkeypatch):
    db = _StubDb()
    searches = []
    _stub_video_world(monkeypatch, engine=_StubEngine(), searches=searches)
    c = _client(db)
    r = c.post("/api/video/watch/grab", json={"kd": "t", "id": 1399, "s": 1, "e": 1})
    assert r.status_code == 200 and r.get_json()["added"] == 1
    add = db.episode_adds[0]
    assert add["title"] == "Game of Thrones"
    assert add["poster_url"] == "https://img/got.jpg"
    assert add["library_id"] == 77          # linked to the owned show row
    ep = add["episodes"][0]
    assert ep["season_number"] == 1 and ep["episode_number"] == 1
    assert ep["title"] == "Winter Is Coming"
    assert ep["air_date"] == "2011-04-17"
    assert ep["still_url"] == "https://img/e1.jpg"
    assert ep["season_poster_url"] == "https://img/s1.jpg"
    assert searches == [("episode", 1399, {"season_number": 1, "episode_number": 1})]
    # Episodes without S+E are refused.
    assert c.post("/api/video/watch/grab",
                  json={"kd": "t", "id": 1399, "ti": "GoT"}).status_code == 400


def test_grab_respects_the_download_permission_gate(monkeypatch):
    _stub_video_world(monkeypatch, engine=_StubEngine())
    import api.video as videoapi
    videoapi._video_db = _StubDb()
    app = Flask(__name__)

    @app.before_request
    def _stamp_g():
        from flask import g
        g.is_admin = False
        g.can_download = False

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    c = app.test_client()
    r = c.post("/api/video/watch/grab", json={"kd": "m", "id": 603, "ti": "x"})
    assert r.status_code == 403


def test_owned_map_uses_reducer_keys():
    c = _client(_StubDb())
    r = c.post("/api/video/watch/owned", json={"items": [
        {"kd": "m", "id": 603},
        {"kd": "m", "id": "604"},                       # string ids fine (protocol strings)
        {"kd": "t", "id": 1399, "s": 1, "e": 1},
        {"kd": "t", "id": 1399, "s": 1, "e": 2},
    ]})
    assert r.status_code == 200
    assert r.get_json()["owned"] == {
        "m:603": True,
        "m:604": False,
        "t:1399:1x1": True,
        "t:1399:1x2": False,
    }


def test_garbage_items_are_skipped_not_fatal():
    c = _client(_StubDb())
    r = c.post("/api/video/watch/owned", json={"items": [
        "not-a-dict",
        {"kd": "m", "id": "abc"},                       # non-numeric id
        {"kd": "m", "id": -5},                          # negative id
        {"kd": "z", "id": 603},                         # unknown kind
        {"kd": "t", "id": 1399, "s": 1},                # episode missing e
        {"kd": "t", "id": 1399, "s": -1, "e": 2},       # negative season
        {"kd": "m", "id": 603},                         # the one sane item
    ]})
    assert r.status_code == 200
    assert r.get_json()["owned"] == {"m:603": True}


def test_items_must_be_a_list():
    c = _client(_StubDb())
    assert c.post("/api/video/watch/owned", json={"items": "m:603"}).status_code == 400
    assert c.post("/api/video/watch/owned", json={}).status_code == 400


def test_request_is_capped_at_32_probes():
    stub = _StubDb()
    c = _client(stub)
    items = [{"kd": "m", "id": i + 1} for i in range(50)]
    r = c.post("/api/video/watch/owned", json={"items": items})
    assert r.status_code == 200
    assert len(stub.calls) == 32
    assert len(r.get_json()["owned"]) == 32
