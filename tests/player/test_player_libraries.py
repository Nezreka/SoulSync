"""libraries in the player: named groups of the folders video is kept in.
a title is in the library of the folder its file sits in; a library can be
hidden from a profile, and is then gone for it everywhere the video side
answers."""

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
MOVIE_ROOTS = ["/media/movies", "/media/kids", "/mnt/drive2/movies"]
SHOW_ROOTS = ["/media/tv", "/media/tv/anime"]


@pytest.fixture
def db(tmp_path):
    from database.player_database import PlayerDatabase
    return PlayerDatabase(str(tmp_path / "player.db"))


class _VideoDb:
    """the manager's folders, and a connection to a video db"""

    def __init__(self, path):
        self.database_path = path

    def _get_connection(self):
        return sqlite3.connect(self.database_path)


@pytest.fixture
def vdb(tmp_path, monkeypatch):
    path = str(tmp_path / "video.db")
    c = sqlite3.connect(path)
    c.executescript(SCHEMA.read_text(encoding="utf-8"))
    from database.video_database import _COLUMN_MIGRATIONS
    for table, column, decl in _COLUMN_MIGRATIONS:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except sqlite3.OperationalError:
            pass
    c.executescript("""
        INSERT INTO movies (id, title, year, content_rating, has_file)
            VALUES (1, 'Main film', 1994, 'PG', 1), (2, 'A kids film', 2010, 'G', 1),
                   (3, 'On the second drive', 2001, 'PG', 1), (4, 'Somewhere else', 2001, 'PG', 1);
        INSERT INTO media_files (movie_id, relative_path, added_at)
            VALUES (1, '/media/movies/Main film (1994)/a.mkv', '2026-01-01'),
                   (2, '/media/kids/A kids film (2010)/b.mkv', '2026-01-01'),
                   (3, '/mnt/drive2/movies/Second (2001)/c.mkv', '2026-01-01'),
                   (4, '/not/a/library/folder/d.mkv', '2026-01-01');
        INSERT INTO shows (id, title, content_rating) VALUES (10, 'A drama', 'TV-14'), (11, 'An anime', 'TV-14');
        INSERT INTO seasons (id, show_id, season_number) VALUES (100, 10, 1), (110, 11, 1);
        INSERT INTO episodes (id, show_id, season_id, season_number, episode_number, title, has_file)
            VALUES (1000, 10, 100, 1, 1, 'Pilot', 1), (1100, 11, 110, 1, 1, 'First', 1);
        INSERT INTO media_files (episode_id, relative_path, added_at)
            VALUES (1000, '/media/tv/A drama/S01/e1.mkv', '2026-03-01'),
                   (1100, '/media/tv/anime/An anime/S01/e1.mkv', '2026-03-02');
    """)
    c.commit()
    c.close()
    monkeypatch.setattr(pl, "roots", lambda _db, kind: list(MOVIE_ROOTS if kind == "movie" else SHOW_ROOTS))
    monkeypatch.setattr(pl, "_localizer", lambda _db: (lambda p: p))
    return _VideoDb(path)


def _two_defaults(extra=()):
    return [{"id": 1, "kind": "movie", "name": "Movies", "folders": []},
            {"id": 2, "kind": "show", "name": "TV Shows", "folders": []}, *extra]


# ── before anything is set up ─────────────────────────────────────────────

def test_a_server_nobody_set_up_has_one_movies_and_one_tv_library(db, vdb):
    libs = pl.libraries(db)
    assert [(x["id"], x["kind"], x["name"], x["is_default"]) for x in libs] == [
        (1, "movie", "Movies", True), (2, "show", "TV Shows", True)]
    assert not any(x["own_page"] for x in pl.visible(db, 3)), "the player looks as it always did"
    assert pl.hidden_for(db, 3) == frozenset()
    res = pl.resolver(db, vdb)
    assert res.library_of("movie", "/media/kids/x/y.mkv") == 1
    assert res.library_of("show", "/media/tv/anime/x/y.mkv") == 2
    # and reading made no table (a read-only request must not write)
    with db._get_connection() as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not ({pl.LIBS, pl.FOLDERS, pl.HIDDEN} & names)


# ── libraries are folders ─────────────────────────────────────────────────

def test_folders_move_into_named_libraries_and_titles_follow(db, vdb):
    saved = pl.save(db, vdb, _two_defaults([
        {"kind": "movie", "name": "Kids", "folders": ["/media/kids"]},
        {"kind": "show", "name": "Anime", "folders": ["/media/tv/anime"]},
    ]))
    by_name = {x["name"]: x for x in saved}
    kids_id, anime_id = by_name["Kids"]["id"], by_name["Anime"]["id"]
    assert by_name["Kids"]["folders"] == ["/media/kids"] and not by_name["Kids"]["is_default"]
    seen = {x["name"]: x for x in pl.visible(db, 3)}
    assert seen["Kids"]["own_page"] is True and seen["Movies"]["own_page"] is False
    assert pl.library_ids_for(vdb, db, "movie", [1, 2, 3, 4]) == {1: 1, 2: kids_id, 3: 1, 4: 1}
    # a folder inside another is its own place: the anime folder sits under the tv one
    assert pl.library_ids_for(vdb, db, "show", [10, 11]) == {10: 2, 11: anime_id}
    over = pl.folder_overview(vdb, db)
    assert [(f["path"], f["titles"], f["library_id"]) for f in over["movie"]] == [
        ("/media/movies", 1, 1), ("/media/kids", 1, kids_id), ("/mnt/drive2/movies", 1, 1), ("", 1, 1)]
    assert over["movie"][1]["sample"] == [2]
    # renamed, a drive moved in, and the folder a library lost goes back to the default
    pl.save(db, vdb, [{"id": 1, "kind": "movie", "name": "Films", "folders": []},
                     {"id": 2, "kind": "show", "name": "TV Shows", "folders": []},
                     {"id": kids_id, "kind": "movie", "name": "Family", "folders": ["/mnt/drive2/movies"]}])
    assert {x["name"] for x in pl.libraries(db)} == {"Films", "TV Shows", "Family"}
    assert pl.library_ids_for(vdb, db, "movie", [2, 3]) == {2: 1, 3: kids_id}
    assert pl.library_ids_for(vdb, db, "show", [11]) == {11: 2}, "a removed library's titles are in the default again"


@pytest.mark.parametrize("bad,why", [
    ([{"id": 2, "kind": "show", "name": "TV Shows", "folders": []}], "can be renamed, not removed"),
    (_two_defaults([{"kind": "movie", "name": "X", "folders": ["/somewhere/else"]}]), "not one of the movie folders"),
    (_two_defaults([{"kind": "movie", "name": "A", "folders": ["/media/kids"]},
                    {"kind": "movie", "name": "B", "folders": ["/media/kids"]}]), "in two libraries"),
    (_two_defaults([{"kind": "movie", "name": "movies", "folders": []}]), "two libraries are called"),
    (_two_defaults([{"kind": "book", "name": "X", "folders": []}]), "of movies, shows or home videos"),
    (_two_defaults([{"kind": "movie", "name": "  ", "folders": []}]), "1 to 60"),
    (_two_defaults([{"id": 99, "kind": "movie", "name": "X", "folders": []}]), "no such library"),
    (_two_defaults([{"kind": "show", "name": "X", "folders": ["/media/kids"]}]), "not one of the TV folders"),
    ("nope", "a list"),
])
def test_a_save_that_makes_no_sense_changes_nothing(db, vdb, bad, why):
    pl.save(db, vdb, _two_defaults([{"kind": "movie", "name": "Kids", "folders": ["/media/kids"]}]))
    before = pl.libraries(db)
    with pytest.raises(pl.LibraryError) as err:
        pl.save(db, vdb, bad)
    assert why in str(err.value)
    assert pl.libraries(db) == before


# ── hidden from a profile ─────────────────────────────────────────────────

def test_hiding_is_saved_with_the_libraries_and_follows_a_new_one(db, vdb):
    saved = pl.save(db, vdb, _two_defaults([{"kind": "show", "name": "Anime", "folders": ["/media/tv/anime"]}]),
                    hidden={"new:2": [3, 4], "1": [4]})
    anime = next(x["id"] for x in saved if x["name"] == "Anime")
    assert pl.hidden_for(db, 3) == frozenset({anime}) and pl.hidden_for(db, 4) == frozenset({anime, 1})
    assert [x["name"] for x in pl.visible(db, 3)] == ["Movies", "TV Shows"]
    assert [x["name"] for x in pl.visible(db, 3, is_admin=True)] == ["Movies", "TV Shows", "Anime"]
    assert [x["name"] for x in pl.visible(db, 9, kinds=["movie"])] == ["Movies"]
    assert pl.hidden_map(db) == {anime: [3, 4], 1: [4]}
    # the answer is not a stale cached one, and a removed library takes its hiding with it
    pl.save(db, vdb, _two_defaults(), hidden={"1": [4]})
    assert pl.hidden_for(db, 3) == frozenset() and pl.hidden_map(db) == {1: [4]}


def test_a_hidden_library_is_gone_from_the_catalog(db, vdb):
    saved = pl.save(db, vdb, _two_defaults([
        {"kind": "movie", "name": "Kids", "folders": ["/media/kids"]},
        {"kind": "show", "name": "Anime", "folders": ["/media/tv/anime"]}]))
    kids_id = next(x["id"] for x in saved if x["name"] == "Kids")
    anime = next(x["id"] for x in saved if x["name"] == "Anime")
    conn = vdb._get_connection()
    conn.row_factory = sqlite3.Row
    raw = catalog.read_all(conn, True, True, False, pl.resolver(db, vdb).library_of)
    conn.close()
    assert catalog.MOVIE_FIELDS[-1] == "library_id" and catalog.SHOW_FIELDS[-1] == "library_id"
    assert {m[0]: m[-1] for m in raw["movies"]} == {1: 1, 2: kids_id, 3: 1, 4: 1}
    assert {s[0]: s[-1] for s in raw["shows"]} == {10: 2, 11: anime}
    everything = shape_video_catalog(raw, True, True, False, None)
    assert {m[0] for m in everything["movies"]} == {1, 2, 3, 4}
    out = shape_video_catalog(raw, True, True, False, VideoCap(hidden=[anime]))
    assert {s[0] for s in out["shows"]} == {10}
    assert {e["show_id"] for e in out["recent_episodes"]} == {10}, "its new episodes go with it"
    # hiding the default library hides the titles that sit in none of the folders too
    out = shape_video_catalog(raw, True, True, False, VideoCap(hidden=[1]))
    assert {m[0] for m in out["movies"]} == {2}
    # and a rating cap still applies beside it
    out = shape_video_catalog(raw, True, True, False, VideoCap(10, 10, (), [kids_id]))
    assert {m[0] for m in out["movies"]} == {1, 3, 4} and out["shows"] == []


# ── the gate every video route asks ───────────────────────────────────────

@pytest.fixture
def where(monkeypatch):
    """title -> library, as the gate asks for it"""
    table = {("show", 11): 5, ("show", 10): 2, ("movie", 3): 6, ("movie", 1): 1}
    asked = {"n": 0}

    def library_ids_for(_vdb, _pdb, kind, ids):
        asked["n"] += 1
        return {int(i): table.get((kind, int(i))) for i in ids}

    monkeypatch.setattr(pl, "library_ids_for", library_ids_for)
    monkeypatch.setattr("database.player_database.get_player_database", lambda: object())
    return asked


class _Ratings:
    def content_ratings_by_id(self, kind, ids):
        return {}


def test_a_title_in_a_hidden_library_is_refused_and_filtered(where):
    vdb, cap = _Ratings(), VideoCap(hidden=[5])
    assert kids.library_title_allowed(vdb, "show", 11, cap) is False
    assert kids.library_title_allowed(vdb, "episode", 11, cap) is False
    assert kids.library_title_allowed(vdb, "show", 10, cap) is True
    assert kids.library_title_allowed(vdb, "movie", 3, cap) is True
    assert kids.filter_library_items(vdb, [{"id": 10}, {"id": 11}], cap, "show") == [{"id": 10}]
    mixed = [{"kind": "movie", "id": 3}, {"kind": "show", "id": 99, "show_id": 11}, {"kind": "show", "id": 98, "show_id": 10}]
    assert kids.filter_mixed_library_items(vdb, mixed, cap, show_id_key="show_id") == [mixed[0], mixed[2]]


def test_no_hidden_library_costs_no_lookup_and_changes_nothing(where):
    vdb, rows = _Ratings(), [{"id": 10}, {"id": 11}]
    assert kids.filter_library_items(vdb, rows, VideoCap(None, None, ("movie",)), "show") is rows
    assert kids.library_title_allowed(vdb, "show", 11, VideoCap(None, None, ("movie",))) is True
    assert where["n"] == 0
    assert kids.hidden_libraries(None) == frozenset() and kids.hidden_libraries(12) == frozenset()


def test_a_lookup_that_fails_hides_rather_than_shows(monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(pl, "library_ids_for", broken)
    monkeypatch.setattr("database.player_database.get_player_database", lambda: object())
    assert kids.library_title_allowed(_Ratings(), "show", 11, VideoCap(hidden=[5])) is False
    assert kids.filter_library_items(_Ratings(), [{"id": 11}], VideoCap(hidden=[5]), "show") == []


def test_hidden_libraries_ride_on_whatever_cap_the_profile_has(monkeypatch):
    monkeypatch.setattr("core.player.libraries.hidden_for", lambda _db, pid: frozenset({5}) if pid == 3 else frozenset())
    monkeypatch.setattr("database.player_database.get_player_database", lambda: object())
    kid = {"profile_id": 3, "is_admin": False}
    assert kids._with_hidden_libraries(None, kid) == VideoCap(hidden=[5])
    assert kids._with_hidden_libraries(VideoCap(10, 13, ("movie",)), kid) == VideoCap(10, 13, ("movie",), [5])
    assert kids._with_hidden_libraries(12, kid) == VideoCap(12, 12, (), [5])
    assert kids._with_hidden_libraries(None, {"profile_id": 4, "is_admin": False}) is None
    assert kids._with_hidden_libraries(None, {"profile_id": 3, "is_admin": True}) is None, "an admin sees every library"


# ── player setup ──────────────────────────────────────────────────────────

def test_setup_is_not_done_until_an_admin_says_so_and_is_checked(db):
    from core.player import setup as ps
    assert ps.get(db) == {"done": False, "server_name": "", "sidebar_order": [], "sidebar_hidden": [],
                          "home_editors": "admins"}
    out = ps.put(db, {"server_name": "  The   Den ", "done": True, "sidebar_hidden": ["/player/podcasts"],
                      "sidebar_order": ["/player/tv", "/player/movies"]})
    assert out == {"done": True, "server_name": "The Den", "sidebar_order": ["/player/tv", "/player/movies"],
                   "sidebar_hidden": ["/player/podcasts"], "home_editors": "admins"}
    for bad in ({"done": "yes"}, {"server_name": "x" * 61}, {"sidebar_hidden": ["/player"]},
                {"sidebar_order": ["nope"]}, {"colour": "red"}, {"home_editors": "kids"}, "x"):
        with pytest.raises(ps.SetupError):
            ps.put(db, bad)
    assert ps.get(db)["server_name"] == "The Den"
    # a profile's own order wins; what the server hides, nobody gets back
    assert ps.sidebar_for(db, [], []) == {"sidebar_order": ["/player/tv", "/player/movies"],
                                          "sidebar_hidden": ["/player/podcasts"]}
    assert ps.sidebar_for(db, ["/player/music"], ["/player/youtube"]) == {
        "sidebar_order": ["/player/music"], "sidebar_hidden": ["/player/podcasts", "/player/youtube"]}


def test_only_an_admin_saves_setup_and_everyone_can_ask_if_it_is_done(db, monkeypatch):
    monkeypatch.setattr(player_api, "player_db", lambda: db)
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(player_api.create_blueprint())
    client = app.test_client()
    monkeypatch.setattr(player_api, "_is_player_admin", lambda: False)
    seen = client.get("/api/player/setup").get_json()
    assert seen == {"success": True, "done": False, "server_name": "", "is_admin": False}
    assert client.put("/api/player/setup", json={"done": True}).status_code == 403
    monkeypatch.setattr(player_api, "_is_player_admin", lambda: True)
    assert client.put("/api/player/setup", json={"server_name": "x" * 99}).status_code == 400


# ── the sidebar's order, per profile ──────────────────────────────────────

def test_sidebar_order_and_hidden_are_saved_and_checked(db):
    from core.player import settings as ps
    assert ps.get(db, 3)["sidebar_order"] == [] and ps.get(db, 3)["sidebar_hidden"] == []
    saved = ps.put(db, 3, {"sidebar_order": ["/player/tv", "lib:5", "/player/movies", "lib:5"],
                           "sidebar_hidden": ["/player/podcasts"]})
    assert saved["sidebar_order"] == ["/player/tv", "lib:5", "/player/movies"]
    assert saved["sidebar_hidden"] == ["/player/podcasts"]
    assert ps.get(db, 4)["sidebar_order"] == [], "another profile keeps its own"
    # a default that is a list is never shared between profiles
    ps.get(db, 4)["sidebar_order"].append("/player/tv")
    assert ps.get(db, 4)["sidebar_order"] == [] and ps.defaults()["sidebar_order"] == []
    for bad in ({"sidebar_order": "x"}, {"sidebar_order": ["../etc"]}, {"sidebar_order": ["lib:x"]},
                {"sidebar_order": [5]}, {"sidebar_hidden": ["/player"]},
                {"sidebar_order": ["/player/tv"] * 0 + [f"lib:{i}" for i in range(200)]}):
        with pytest.raises(ps.SettingsError):
            ps.put(db, 3, bad)


# a library made in setup can be placed in the sidebar in the same save

def test_setup_save_gives_a_new_library_its_place_in_the_sidebar(tmp_path, monkeypatch):
    import api.player as P
    from core.player import libraries as L
    from core.player import setup as S

    saved = {}
    monkeypatch.setattr(P, "_profile", lambda: 1)
    monkeypatch.setattr(P, "_is_player_admin", lambda: True)
    monkeypatch.setattr(P, "player_db", lambda: object())
    monkeypatch.setattr(P, "get_video_db", lambda: object())
    monkeypatch.setattr(P, "_forget_catalog_views", lambda: None)
    monkeypatch.setattr(P, "player_setup_get", lambda: ("ok", 200))
    monkeypatch.setattr(L, "save", lambda db, vdb, wanted, hidden: [
        {"id": 1, "kind": "movie", "name": "Movies"}, {"id": 2, "kind": "show", "name": "TV Shows"},
        {"id": 7, "kind": "movie", "name": "Cam Movies"}])
    monkeypatch.setattr(S, "put", lambda db, values: saved.update(values))

    from flask import Flask
    app = Flask(__name__)
    body = {"libraries": [{"id": 1, "kind": "movie", "name": "Movies", "folders": []},
                          {"id": 2, "kind": "show", "name": "TV Shows", "folders": []},
                          {"kind": "movie", "name": "  cam   movies ", "folders": ["/m/cam"]}],
            "sidebar_order": ["/player", "lib:new:2", "/player/movies", "lib:new:9"],
            "sidebar_hidden": ["lib:new:2"]}
    with app.test_request_context("/api/player/setup", method="PUT", json=body):
        fn = P.player_setup_put
        while hasattr(fn, "__wrapped__"):
            fn = fn.__wrapped__
        assert fn() == ("ok", 200)
    # the new library by its real id; a place that names no library in the save is dropped
    assert saved["sidebar_order"] == ["/player", "lib:7", "/player/movies"]
    assert saved["sidebar_hidden"] == ["lib:7"]
