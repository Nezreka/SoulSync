"""libraries in the player: a library's own page, and a library hidden from
a profile being gone for it everywhere the video side answers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from flask import Flask

import api.player as player_api
from api.player import shape_video_catalog
from api.video import kids
from api.video.kids import VideoCap
from core.player import catalog
from core.player import libraries as pl

SCHEMA = Path(__file__).resolve().parents[2] / "database" / "video_schema.sql"
LIBS = [
    {"id": 1, "kind": "movie", "name": "Movies", "builtin": "main", "enabled": True},
    {"id": 2, "kind": "show", "name": "TV Shows", "builtin": "main", "enabled": True},
    {"id": 5, "kind": "show", "name": "Anime", "builtin": None, "enabled": True},
    {"id": 6, "kind": "movie", "name": "Kids", "builtin": None, "enabled": True},
    {"id": 7, "kind": "movie", "name": "Switched off", "builtin": None, "enabled": False},
]


@pytest.fixture
def db(tmp_path):
    from database.player_database import PlayerDatabase
    return PlayerDatabase(str(tmp_path / "player.db"))


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "video.db"))
    c.executescript(SCHEMA.read_text(encoding="utf-8"))
    from database.video_database import _COLUMN_MIGRATIONS
    for table, column, decl in _COLUMN_MIGRATIONS:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except sqlite3.OperationalError:
            pass
    c.executescript("""
        CREATE TABLE IF NOT EXISTS video_libraries (id INTEGER PRIMARY KEY, kind TEXT, name TEXT, builtin TEXT);
        INSERT INTO video_libraries (id, kind, name, builtin) VALUES
            (1, 'movie', 'Movies', 'main'), (2, 'show', 'TV Shows', 'main'), (5, 'show', 'Anime', NULL),
            (6, 'movie', 'Kids', NULL);
        INSERT INTO movies (id, title, year, content_rating, has_file, video_library_id)
            VALUES (1, 'In main by folder', 1994, 'PG', 1, 1), (2, 'In no folder', 2001, 'PG', 1, NULL),
                   (3, 'A kids film', 2010, 'G', 1, 6);
        INSERT INTO shows (id, title, content_rating, video_library_id)
            VALUES (10, 'A drama', 'TV-14', NULL), (11, 'An anime', 'TV-14', 5);
        INSERT INTO seasons (id, show_id, season_number) VALUES (100, 10, 1), (110, 11, 1);
        INSERT INTO episodes (id, show_id, season_id, season_number, episode_number, title, has_file)
            VALUES (1000, 10, 100, 1, 1, 'Pilot', 1), (1100, 11, 110, 1, 1, 'First', 1);
        INSERT INTO media_files (episode_id, relative_path, added_at)
            VALUES (1000, 'a.mkv', '2026-03-01'), (1100, 'b.mkv', '2026-03-02');
    """)
    yield c
    c.close()


# ── what the player keeps ─────────────────────────────────────────────────

def test_nothing_is_set_until_an_admin_sets_it(db):
    assert pl.hidden_for(db, 3) == frozenset()
    assert pl.own_page_ids(db) == frozenset()
    seen = pl.visible(db, LIBS, 3)
    assert [x["name"] for x in seen] == ["Movies", "TV Shows", "Anime", "Kids"]   # a switched-off library isn't listed
    assert not any(x["own_page"] for x in seen), "the player looks as it did: one Movies, one TV Shows"


def test_own_page_and_hiding(db):
    pl.set_own_page(db, 5, True)
    pl.set_own_page(db, 1, True)                    # the main library: it IS movies, no second page
    assert pl.set_hidden_from(db, 5, [3, "4", 3]) == [3, 4]
    by_name = {x["name"]: x for x in pl.visible(db, LIBS, 9)}
    assert by_name["Anime"]["own_page"] is True and by_name["Movies"]["own_page"] is False
    assert [x["name"] for x in pl.visible(db, LIBS, 3)] == ["Movies", "TV Shows", "Kids"]
    assert [x["name"] for x in pl.visible(db, LIBS, 3, is_admin=True)] == ["Movies", "TV Shows", "Anime", "Kids"]
    assert [x["name"] for x in pl.visible(db, LIBS, 9, kinds=["movie"])] == ["Movies", "Kids"]
    # replaced whole, and the answer is not a stale cached one
    assert pl.hidden_for(db, 3) == frozenset({5})
    pl.set_hidden_from(db, 5, [4])
    assert pl.hidden_for(db, 3) == frozenset() and pl.hidden_for(db, 4) == frozenset({5})
    admin = {x["name"]: x for x in pl.admin_view(db, LIBS, {5: 12})}
    assert admin["Anime"] == {"id": 5, "kind": "show", "name": "Anime", "main": False, "own_page": True,
                              "titles": 12, "hidden_from": [4]}
    pl.forget_library(db, 5)
    assert pl.hidden_for(db, 4) == frozenset() and pl.own_page_ids(db) == frozenset({1})
    with pytest.raises(pl.LibraryError):
        pl.set_hidden_from(db, 5, ["x"])


# ── the catalog ───────────────────────────────────────────────────────────

def test_every_title_carries_its_library_and_no_folder_means_main(conn):
    movies = {m[0]: m[-1] for m in catalog.owned_movies(conn)}
    shows = {s[0]: s[-1] for s in catalog.owned_shows(conn)}
    assert catalog.MOVIE_FIELDS[-1] == "library_id" and catalog.SHOW_FIELDS[-1] == "library_id"
    assert movies == {1: 1, 2: 1, 3: 6}
    assert shows == {10: 2, 11: 5}


def test_a_hidden_library_is_gone_from_the_catalog(conn):
    raw = catalog.read_all(conn, True, True, False)
    everything = shape_video_catalog(raw, True, True, False, None)
    assert {m[0] for m in everything["movies"]} == {1, 2, 3} and {s[0] for s in everything["shows"]} == {10, 11}
    out = shape_video_catalog(raw, True, True, False, VideoCap(hidden=[5]))
    assert {s[0] for s in out["shows"]} == {10}
    assert {e["show_id"] for e in out["recent_episodes"]} == {10}, "its new episodes go with it"
    assert {m[0] for m in out["movies"]} == {1, 2, 3}
    # hiding the main library hides the titles that sit in no library folder too
    out = shape_video_catalog(raw, True, True, False, VideoCap(hidden=[1]))
    assert {m[0] for m in out["movies"]} == {3}
    # and a rating cap still applies beside it
    out = shape_video_catalog(raw, True, True, False, VideoCap(10, 10, (), [6]))
    assert {m[0] for m in out["movies"]} == {1, 2} and out["shows"] == []


# ── the gate every video route asks ───────────────────────────────────────

class _VideoDb:
    """the two lookups the gate uses"""

    def __init__(self, where, ratings=None):
        self.where, self.ratings, self.asked = where, ratings or {}, 0

    def library_ids_by_id(self, kind, ids):
        self.asked += 1
        return {int(i): self.where.get((kind, int(i))) for i in ids}

    def content_ratings_by_id(self, kind, ids):
        return {int(i): self.ratings.get((kind, int(i))) for i in ids}


def test_a_title_in_a_hidden_library_is_refused_and_filtered():
    vdb = _VideoDb({("show", 11): 5, ("show", 10): 2, ("movie", 3): 6, ("movie", 1): 1})
    cap = VideoCap(hidden=[5])
    assert kids.library_title_allowed(vdb, "show", 11, cap) is False
    assert kids.library_title_allowed(vdb, "episode", 11, cap) is False
    assert kids.library_title_allowed(vdb, "show", 10, cap) is True
    assert kids.library_title_allowed(vdb, "movie", 3, cap) is True
    rows = [{"id": 10}, {"id": 11}]
    assert kids.filter_library_items(vdb, rows, cap, "show") == [{"id": 10}]
    mixed = [{"kind": "movie", "id": 3}, {"kind": "show", "id": 99, "show_id": 11}, {"kind": "show", "id": 98, "show_id": 10}]
    assert kids.filter_mixed_library_items(vdb, mixed, cap, show_id_key="show_id") == [mixed[0], mixed[2]]


def test_no_hidden_library_costs_no_lookup_and_changes_nothing():
    vdb = _VideoDb({})
    rows = [{"id": 10}, {"id": 11}]
    assert kids.filter_library_items(vdb, rows, VideoCap(None, None, ("movie",)), "show") is rows
    assert kids.library_title_allowed(vdb, "show", 11, VideoCap(None, None, ("movie",))) is True
    assert vdb.asked == 0
    assert kids.hidden_libraries(None) == frozenset() and kids.hidden_libraries(12) == frozenset()


def test_a_lookup_that_fails_hides_rather_than_shows():
    class Broken(_VideoDb):
        def library_ids_by_id(self, kind, ids):
            raise RuntimeError("db gone")
    assert kids.library_title_allowed(Broken({}), "show", 11, VideoCap(hidden=[5])) is False
    assert kids.filter_library_items(Broken({}), [{"id": 11}], VideoCap(hidden=[5]), "show") == []


def test_hidden_libraries_ride_on_whatever_cap_the_profile_has(monkeypatch):
    monkeypatch.setattr("core.player.libraries.hidden_for", lambda _db, pid: frozenset({5}) if pid == 3 else frozenset())
    monkeypatch.setattr("database.player_database.get_player_database", lambda: object())
    kid = {"profile_id": 3, "is_admin": False}
    assert kids._with_hidden_libraries(None, kid) == VideoCap(hidden=[5])
    assert kids._with_hidden_libraries(VideoCap(10, 13, ("movie",)), kid) == VideoCap(10, 13, ("movie",), [5])
    assert kids._with_hidden_libraries(12, kid) == VideoCap(12, 12, (), [5])
    assert kids._with_hidden_libraries(None, {"profile_id": 4, "is_admin": False}) is None
    assert kids._with_hidden_libraries(None, {"profile_id": 3, "is_admin": True}) is None, "an admin sees every library"


# ── routes ────────────────────────────────────────────────────────────────

def test_only_an_admin_manages_libraries(db, monkeypatch):
    monkeypatch.setattr(player_api, "player_db", lambda: db)
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(player_api.create_blueprint())
    client = app.test_client()
    monkeypatch.setattr(player_api, "_is_player_admin", lambda: False)
    assert client.get("/api/player/admin/libraries").status_code == 403
    assert client.put("/api/player/admin/libraries/5", json={"own_page": True}).status_code == 403
    assert pl.own_page_ids(db) == frozenset()
