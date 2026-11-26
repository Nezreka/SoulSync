"""the /player progress store and its routes.

the store runs on a throwaway sqlite file; the routes on a bare flask app with
only the player blueprint, so nothing here reaches the shared session db or
starts a worker.
"""

from __future__ import annotations


import pytest
from flask import Flask

import api.player as player_api
from core.player import progress


@pytest.fixture
def db(tmp_path):
    from database.player_database import PlayerDatabase
    return PlayerDatabase(str(tmp_path / "player.db"))


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(player_api, "player_db", lambda: db)
    # home's library rows read the video side: none here (test_home_libraries.py has them)
    monkeypatch.setattr(player_api, "_home_libraries", lambda: [])
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(player_api.create_blueprint())
    return app.test_client()


def _as(client, profile_id):
    with client.session_transaction() as sess:
        sess["profile_id"] = profile_id


# ── store ────────────────────────────────────────────────────────────────

def test_save_then_get_round_trips(db):
    row = progress.save(db, 2, "movie", 42, 600, 6000, meta={"title": "Heat"})
    assert row["item_id"] == "42"
    assert row["position_seconds"] == 600
    assert row["duration_seconds"] == 6000
    assert row["finished"] is False
    assert row["meta"] == {"title": "Heat"}
    assert progress.get(db, 2, "movie", "42") == row


def test_near_the_end_counts_as_finished(db):
    assert progress.save(db, 1, "episode", "e1", 1390, 1440)["finished"] is True
    # scrubbing back reopens it
    assert progress.save(db, 1, "episode", "e1", 100)["finished"] is False


def test_long_items_are_not_finished_an_hour_early(db):
    twenty_hours = 72000
    assert progress.save(db, 1, "audiobook", "b1", twenty_hours - 3600, twenty_hours)["finished"] is False
    assert progress.save(db, 1, "audiobook", "b1", twenty_hours - 200, twenty_hours)["finished"] is True


def test_save_keeps_stored_duration_and_meta_when_omitted(db):
    progress.save(db, 1, "track", "t1", 10, 200, meta={"title": "Song"})
    row = progress.save(db, 1, "track", "t1", 20)
    assert row["duration_seconds"] == 200
    assert row["meta"] == {"title": "Song"}


def test_position_is_clamped_to_duration(db):
    assert progress.save(db, 1, "track", "t1", 999, 200)["position_seconds"] == 200


def test_image_cache_artwork_is_not_stored(db):
    row = progress.save(db, 1, "movie", "m", 5, 100, meta={
        "title": "X", "artwork": "/api/image-cache/abc?sig=1", "backdrop": "https://img/b.jpg",
        "junk": "dropped"})
    assert row["meta"] == {"title": "X", "backdrop": "https://img/b.jpg"}


def test_finish_moves_the_position_to_the_end(db):
    progress.save(db, 1, "movie", "m", 100, 5000)
    row = progress.finish(db, 1, "movie", "m")
    assert row["finished"] is True
    assert row["position_seconds"] == 5000


def test_continue_is_unfinished_started_and_newest_first(db):
    progress.save(db, 1, "movie", "old", 50, 5000, now=100)
    progress.save(db, 1, "episode", "new", 50, 1500, now=300)
    progress.save(db, 1, "movie", "mid", 50, 5000, now=200)
    progress.save(db, 1, "movie", "done", 4990, 5000, now=400)
    progress.save(db, 1, "movie", "unstarted", 0, 5000, now=500)
    assert [r["item_id"] for r in progress.continue_list(db, 1)] == ["new", "mid", "old"]
    assert [r["item_id"] for r in progress.continue_list(db, 1, ["movie"])] == ["mid", "old"]
    assert [r["item_id"] for r in progress.continue_list(db, 1, limit=1)] == ["new"]


def test_continue_limit_is_capped(db):
    assert progress.clamp_limit(10_000) == progress.CONTINUE_MAX
    assert progress.clamp_limit("nope") == progress.CONTINUE_DEFAULT
    assert progress.clamp_limit(0) == 1


@pytest.mark.parametrize("item_type", ["", "1movie", "movie; drop", "x" * 40, None])
def test_bad_item_types_are_refused(db, item_type):
    with pytest.raises(progress.ProgressError):
        progress.save(db, 1, item_type, "1", 5)


@pytest.mark.parametrize("position", [-1, float("nan"), float("inf"), "abc", True, None])
def test_bad_positions_are_refused(db, position):
    with pytest.raises(progress.ProgressError):
        progress.save(db, 1, "movie", "1", position)


def test_remove(db):
    progress.save(db, 1, "movie", "m", 10, 100)
    assert progress.remove(db, 1, "movie", "m") is True
    assert progress.remove(db, 1, "movie", "m") is False
    assert progress.get(db, 1, "movie", "m") is None


def test_a_deleted_profile_is_swept_from_player_db(db):
    from core.player import favorites
    progress.save(db, 3, "movie", "m", 10, 100)
    progress.save(db, 4, "movie", "m", 10, 100)
    favorites.put(db, 3, ["genre-comedy"])
    from core.player import home_layout
    home_layout.put(db, 3, {"rows": []}, "tv-livingroom")
    from core.player import watchlist
    watchlist.add(db, 3, "movie", "41")
    assert db.delete_profile_data(3) == 4
    assert watchlist.list_items(db, 3) == []
    assert progress.get(db, 3, "movie", "m") is None
    assert favorites.get(db, 3) == []
    assert progress.get(db, 4, "movie", "m") is not None


def test_profile_delete_route_sweeps_player_db(monkeypatch):
    import api.profiles as profiles_api
    swept = []

    class Fake:
        def delete_profile_data(self, pid):
            swept.append(pid)
            return 0

    import database.player_database as pdb
    monkeypatch.setattr(pdb, "get_player_database", lambda: Fake())
    profiles_api._sweep_player_profile_data(9)
    assert swept == [9]


def test_player_db_sits_beside_the_music_db_unless_told(monkeypatch):
    from database.player_database import default_path
    monkeypatch.delenv("PLAYER_DATABASE_PATH", raising=False)
    monkeypatch.setenv("DATABASE_PATH", "/app/data/music_library.db")
    assert default_path().replace("\\", "/") == "/app/data/player.db"
    monkeypatch.setenv("PLAYER_DATABASE_PATH", "/x/p.db")
    assert default_path() == "/x/p.db"


# ── routes ───────────────────────────────────────────────────────────────

def test_put_get_finish_delete_over_http(client):
    _as(client, 3)
    res = client.put("/api/player/progress", json={
        "item_type": "episode", "item_id": "77", "position_seconds": 300,
        "duration_seconds": 1500, "meta": {"title": "Pilot"}})
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["progress"]["position_seconds"] == 300

    got = client.get("/api/player/progress?item_type=episode&item_id=77").get_json()
    assert got["progress"]["meta"] == {"title": "Pilot"}

    rail = client.get("/api/player/continue").get_json()["items"]
    assert [r["item_id"] for r in rail] == ["77"]

    fin = client.post("/api/player/progress/finish", json={"item_type": "episode", "item_id": "77"})
    assert fin.get_json()["progress"]["finished"] is True
    assert client.get("/api/player/continue").get_json()["items"] == []

    gone = client.delete("/api/player/progress?item_type=episode&item_id=77").get_json()
    assert gone["removed"] is True
    assert client.get("/api/player/progress?item_type=episode&item_id=77").get_json()["progress"] is None


def test_continue_filters_by_comma_separated_types(client):
    _as(client, 3)
    for item_type, item_id in (("movie", "a"), ("episode", "b"), ("track", "c")):
        client.put("/api/player/progress", json={
            "item_type": item_type, "item_id": item_id, "position_seconds": 5, "duration_seconds": 1000})
    rail = client.get("/api/player/continue?item_type=movie,episode").get_json()["items"]
    assert sorted(r["item_id"] for r in rail) == ["a", "b"]


def test_bad_input_is_a_400_with_a_message(client):
    _as(client, 3)
    res = client.put("/api/player/progress", json={"item_type": "movie", "item_id": "1",
                                                   "position_seconds": -5})
    assert res.status_code == 400
    assert "seconds" in res.get_json()["error"]
    res = client.put("/api/player/progress", json={"item_type": "movie", "item_id": "1",
                                                   "position_seconds": 5, "finished": "yes"})
    assert res.status_code == 400


def test_a_profile_never_sees_or_touches_another_profiles_rows(client):
    _as(client, 2)
    client.put("/api/player/progress", json={
        "item_type": "movie", "item_id": "shared", "position_seconds": 900, "duration_seconds": 6000})

    _as(client, 5)
    # same item, nothing stored for this profile
    assert client.get("/api/player/progress?item_type=movie&item_id=shared").get_json()["progress"] is None
    assert client.get("/api/player/continue").get_json()["items"] == []
    # a profile_id in the body is ignored: the session decides
    client.put("/api/player/progress", json={
        "profile_id": 2, "item_type": "movie", "item_id": "shared", "position_seconds": 10,
        "duration_seconds": 6000})
    assert client.delete("/api/player/progress?item_type=movie&item_id=shared&profile_id=2").get_json()["removed"] is True
    client.post("/api/player/progress/finish", json={"profile_id": 2, "item_type": "movie", "item_id": "shared"})

    _as(client, 2)
    mine = client.get("/api/player/progress?item_type=movie&item_id=shared").get_json()["progress"]
    assert mine["position_seconds"] == 900
    assert mine["finished"] is False
    assert [r["item_id"] for r in client.get("/api/player/continue").get_json()["items"]] == ["shared"]


def test_lookup_returns_only_this_profiles_rows_by_id(client):
    _as(client, 2)
    for item_id in ("a", "b"):
        client.put("/api/player/progress", json={
            "item_type": "podcast_episode", "item_id": item_id, "position_seconds": 30,
            "duration_seconds": 3000})
    _as(client, 3)
    client.put("/api/player/progress", json={
        "item_type": "podcast_episode", "item_id": "c", "position_seconds": 30, "duration_seconds": 3000})
    _as(client, 2)
    res = client.post("/api/player/progress/lookup", json={
        "item_type": "podcast_episode", "item_ids": ["a", "b", "c", "zzz"]}).get_json()
    assert sorted(res["progress"]) == ["a", "b"]


def test_lookup_refuses_bad_lists(client):
    _as(client, 2)
    assert client.post("/api/player/progress/lookup", json={
        "item_type": "track", "item_ids": "a"}).status_code == 400
    assert client.post("/api/player/progress/lookup", json={
        "item_type": "track", "item_ids": [str(i) for i in range(501)]}).status_code == 400


def test_signed_out_is_refused(client, monkeypatch):
    monkeypatch.setattr(player_api, "get_current_profile_id", lambda: None)
    assert client.get("/api/player/continue").status_code == 401
    assert client.put("/api/player/progress", json={}).status_code == 401


def test_every_player_route_declares_its_access():
    from core.permissions import declared_access

    app = Flask(__name__)
    app.register_blueprint(player_api.create_blueprint())
    rules = [r for r in app.url_map.iter_rules() if r.rule.startswith("/api/player/")]
    own = [r for r in rules if "/progress" in r.rule or r.rule.endswith("/continue")
           or "/favorites" in r.rule or r.rule.endswith("/home-layout")
           or "/page-layout/" in r.rule
           or r.rule.endswith("/watchlist") or r.rule == "/api/player/watched"
           or r.rule.endswith("/settings") or "/presence" in r.rule
           or "/note" in r.rule or "/guide/channels" in r.rule or "/guide/lineup" in r.rule
           or "/guide/reminders" in r.rule or "/parties" in r.rule
           or "/downloads" in r.rule or "/admin/users" in r.rule or r.rule.endswith("/player/setup")
           or "/commercials" in r.rule or "/issues" in r.rule]
    music = [r for r in rules if r.rule.endswith("/music-channels") or r.rule.endswith("/music/listening")]
    discover = [r for r in rules if "/discover-playlists" in r.rule]
    playlists = [r for r in rules if r not in discover and ("/playlists" in r.rule or r.rule.endswith("/top") or r.rule.endswith("/request-track"))]
    library = [r for r in rules if r not in own and r not in music and r not in playlists and r not in discover]
    assert len(own) == 63 and len(library) == 13 and len(music) == 2 and len(playlists) == 4
    # soulsync's discovery playlists: the manager's discover gate, the same as its discover page
    assert len(discover) == 3
    for rule in discover:
        assert declared_access(app.view_functions[rule.endpoint]) == ("perm", "music.discover"), rule.rule
    for rule in music:
        assert declared_access(app.view_functions[rule.endpoint]) == ("perm", "music.library"), rule.rule
    # playlists and an artist's top songs: the music side's own gate
    for rule in playlists:
        assert declared_access(app.view_functions[rule.endpoint]) == ("perm", "music.access"), rule.rule
    # your own rows: self only
    for rule in own:
        assert declared_access(app.view_functions[rule.endpoint]) == ("self", None), rule.rule
    # library reads ride the video side's own access rows
    for rule in library:
        assert declared_access(app.view_functions[rule.endpoint]) == (
            "perm_any", ("movies.access", "tv.access")), rule.rule


# ── favorites ────────────────────────────────────────────────────────────

def test_favorites_round_trip_per_profile_in_order(client):
    _as(client, 2)
    res = client.put("/api/player/guide/favorites", json={"channels": ["genre-comedy", "kids", "Genre-Comedy"]})
    assert res.get_json()["channels"] == ["genre-comedy", "kids"]
    assert client.get("/api/player/guide/favorites").get_json()["channels"] == ["genre-comedy", "kids"]
    _as(client, 3)
    assert client.get("/api/player/guide/favorites").get_json()["channels"] == []
    client.put("/api/player/guide/favorites", json={"channels": ["network-hbo"]})
    _as(client, 2)
    assert client.get("/api/player/guide/favorites").get_json()["channels"] == ["genre-comedy", "kids"]


def test_favorites_refuse_junk(client):
    _as(client, 2)
    assert client.put("/api/player/guide/favorites", json={"channels": "kids"}).status_code == 400
    assert client.put("/api/player/guide/favorites", json={"channels": ["x y"]}).status_code == 400
    assert client.put("/api/player/guide/favorites",
                      json={"channels": [f"c{i}" for i in range(101)]}).status_code == 400


# ── home layout ──────────────────────────────────────────────────────────

def test_home_layout_is_per_profile_and_checked(client):
    _as(client, 3)
    first = client.get("/api/player/home-layout").get_json()
    assert first["scope"] is None and first["layout"]["rows"][0]["id"] == "continue"
    assert first["catalog"]["version"] == first["layout"]["version"] == 1
    layout = {"billboard": False, "rows": [{"id": "top-movies", "style": "ranked", "on": True},
                                           {"id": "genre:movie:Sci-Fi & Fantasy", "style": "large"}]}
    saved = client.put("/api/player/home-layout", json={"layout": layout, "device": "*"}).get_json()
    assert saved["layout"]["rows"][1] == {"id": "genre:movie:Sci-Fi & Fantasy", "style": "large", "on": True}
    # the rows it left out come back after, off
    assert all(not r["on"] for r in saved["layout"]["rows"][2:])
    assert saved["scope"] == "all"
    got = client.get("/api/player/home-layout?device=tv-livingroom").get_json()
    assert got["scope"] == "all" and got["layout"]["billboard"] is False
    # another profile has its own
    _as(client, 4)
    assert client.get("/api/player/home-layout").get_json()["scope"] is None
    # bad shapes are refused
    bad = [{"layout": {"rows": [{"id": "continue", "style": "huge"}]}},
           {"layout": {"rows": [{"id": "not-a-row"}]}},
           {"layout": {"rows": [{"id": "A B"}]}},
           {"layout": {"rows": [{"id": "continue"}] * 61}},
           {"layout": {"rows": []}, "device": "../etc"}]
    for body in bad:
        assert client.put("/api/player/home-layout", json=body).status_code == 400, body


def test_home_layout_per_screen(client):
    _as(client, 3)
    shared = {"billboard": True, "rows": [{"id": "continue", "style": "wide"}]}
    tv = {"billboard": False, "rows": [{"id": "top-movies", "style": "ranked"}]}
    client.put("/api/player/home-layout", json={"layout": shared, "device": "*"})
    client.put("/api/player/home-layout", json={"layout": tv, "device": "tv-livingroom"})
    on_tv = client.get("/api/player/home-layout?device=tv-livingroom").get_json()
    on_phone = client.get("/api/player/home-layout?device=phone-abcdefgh").get_json()
    assert on_tv["scope"] == "device" and on_tv["layout"]["billboard"] is False
    assert on_phone["scope"] == "all" and on_phone["layout"]["rows"][0]["id"] == "continue"
    # the tv shares its layout with every screen: it follows the shared one after
    client.put("/api/player/home-layout",
               json={"layout": tv, "device": "*", "from_device": "tv-livingroom"})
    assert client.get("/api/player/home-layout?device=tv-livingroom").get_json()["scope"] == "all"
    assert client.get("/api/player/home-layout?device=phone-abcdefgh").get_json()["layout"]["billboard"] is False
    # this screen back to shared; then everything back to the default
    client.put("/api/player/home-layout", json={"layout": shared, "device": "tv-livingroom"})
    client.delete("/api/player/home-layout?device=tv-livingroom")
    assert client.get("/api/player/home-layout?device=tv-livingroom").get_json()["scope"] == "all"
    client.delete("/api/player/home-layout")
    assert client.get("/api/player/home-layout?device=tv-livingroom").get_json()["scope"] is None


def test_the_layout_contract_cleans_what_it_hands_out():
    from core.player import home_layout as hl
    out = hl.normalize({"billboard": False, "rows": [
        {"id": "continue", "style": "ranked"},        # continue can't be ranked
        {"id": "top-movies", "style": "wide", "on": True},
        {"id": "top-movies", "style": "large"},        # a duplicate
        {"id": "gone-row"},                            # a row this build doesn't have
        {"id": "genre:show:Comedy", "style": "ranked", "on": False}]})
    assert out["rows"][:3] == [{"id": "continue", "style": "wide", "on": True},
                               {"id": "top-movies", "style": "wide", "on": True},
                               {"id": "genre:show:Comedy", "style": "ranked", "on": False}]
    assert len(out["rows"]) == len(hl.ROWS) + 1
    assert hl.normalize("junk") == hl.default_layout()
    cat = hl.catalog()
    assert [r["id"] for r in cat["rows"]] == [r[0] for r in hl.ROWS]
    assert {st["id"] for st in cat["styles"]} == {"standard", "large", "wide", "ranked"}
    assert all(r["style"] in r["styles"] for r in cat["rows"])


# ── watchlist ────────────────────────────────────────────────────────────

def test_watchlist_store(db, monkeypatch):
    from core.player import watchlist
    # only THIS thread's clock is scripted: watchlist.time is the time module
    # itself, and a worker another test left running that asks the time would
    # use one of the three values up (StopIteration, in whichever test ran next)
    import threading
    real_time, me = watchlist.time.time, threading.get_ident()
    times = iter([100.0, 200.0, 300.0])
    monkeypatch.setattr(watchlist.time, "time",
                        lambda: next(times) if threading.get_ident() == me else real_time())
    watchlist.add(db, 1, "movie", "41")
    watchlist.add(db, 1, "youtube", "dQw4w9WgXcQ",
                  {"title": "A video", "artwork": "https://i.ytimg.com/vi/x/hq.jpg",
                   "backdrop": "/api/image-cache/abc"})
    # adding again keeps its place, refreshes the meta
    again = watchlist.add(db, 1, "movie", "41", {"title": "Alien"})
    assert again["added_at"] == 100.0 and again["meta"] == {"title": "Alien"}
    items = watchlist.list_items(db, 1)
    assert [(i["item_type"], i["item_id"]) for i in items] == [("youtube", "dQw4w9WgXcQ"), ("movie", "41")]
    # image-cache links expire: never kept
    assert "backdrop" not in items[0]["meta"]
    assert watchlist.list_items(db, 2) == []
    assert watchlist.remove(db, 1, "movie", "41") is True
    assert watchlist.remove(db, 1, "movie", "41") is False
    with pytest.raises(watchlist.WatchlistError):
        watchlist.add(db, 1, "track", "1")


def test_watchlist_is_capped(db, monkeypatch):
    from core.player import watchlist
    monkeypatch.setattr(watchlist, "MAX_ITEMS", 2)
    watchlist.add(db, 1, "movie", "1")
    watchlist.add(db, 1, "movie", "2")
    with pytest.raises(watchlist.WatchlistError):
        watchlist.add(db, 1, "movie", "3")
    # one already on it still saves
    watchlist.add(db, 1, "movie", "2", {"title": "x"})


def test_watchlist_over_http_is_per_profile(client):
    _as(client, 3)
    assert client.put("/api/player/watchlist", json={"item_type": "show", "item_id": 9}).status_code == 200
    assert [i["item_id"] for i in client.get("/api/player/watchlist").get_json()["items"]] == ["9"]
    assert client.put("/api/player/watchlist", json={"item_type": "album", "item_id": 1}).status_code == 400
    _as(client, 4)
    assert client.get("/api/player/watchlist").get_json()["items"] == []
    assert client.delete("/api/player/watchlist?item_type=show&item_id=9").get_json()["removed"] is False
    _as(client, 3)
    assert client.delete("/api/player/watchlist?item_type=show&item_id=9").get_json()["removed"] is True
