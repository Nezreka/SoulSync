"""the /player progress store and its routes.

the store runs on a throwaway sqlite file; the routes on a bare flask app with
only the player blueprint, so nothing here reaches the shared session db or
starts a worker.
"""

from __future__ import annotations

import sqlite3

import pytest
from flask import Flask

import api.player as player_api
from core.player import progress


class _FileDb:
    """the slice of MusicDatabase the store uses."""

    def __init__(self, path):
        self.database_path = path

    def _get_connection(self):
        conn = sqlite3.connect(str(self.database_path))
        conn.row_factory = sqlite3.Row
        return conn


@pytest.fixture
def db(tmp_path):
    return _FileDb(tmp_path / "player.db")


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(player_api, "get_database", lambda: db)
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


def test_the_table_has_a_profile_id_column_so_profile_delete_sweeps_it(db):
    # MusicDatabase.delete_profile deletes from every table with this column
    progress.ensure_schema(db)
    with db._get_connection() as conn:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({progress.TABLE})")}
    assert "profile_id" in cols


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
           or "/favorites" in r.rule]
    music = [r for r in rules if r.rule.endswith("/music-channels")]
    library = [r for r in rules if r not in own and r not in music]
    assert len(own) == 8 and len(library) == 4 and len(music) == 1
    assert declared_access(app.view_functions[music[0].endpoint]) == ("perm", "music.library")
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
