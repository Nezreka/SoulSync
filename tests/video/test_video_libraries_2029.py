"""Video libraries (#2029): named movie / TV libraries with their own folders.

The main and 4K folders became libraries (builtin rows whose folder stays in
the settings it always lived in), so an existing install routes exactly as
before. Covers the migration, the CRUD rules, the routing order (picked >
assigned > 4K profile > rules > default > main, anime kept), the grab and
drain routes, the roots lists and the api.
"""

from __future__ import annotations

import sqlite3

import pytest
from flask import Flask

import core.video.libraries as libs
from database.video_database import VideoDatabase


@pytest.fixture(autouse=True)
def _no_tmdb(monkeypatch):
    # the rules read a title's genres/keywords; tests hand them in, never TMDB
    monkeypatch.setattr(libs, "title_tags", lambda db, kind, tmdb_id: {"genres": [], "keywords": []})


@pytest.fixture()
def db(tmp_path):
    vdb = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    vdb.set_setting("movies_path", "/media/movies")
    vdb.set_setting("tv_path", "/media/tv")
    return vdb


def _add(db, kind, name, path, **extra):
    return libs.create_library(db, {"kind": kind, "name": name, "path": path, **extra})


# ── migration ─────────────────────────────────────────────────────────────────

def test_a_fresh_database_has_the_main_and_4k_libraries(db):
    rows = libs.list_libraries(db)
    assert [(r["kind"], r["builtin"], r["name"]) for r in rows] == [
        ("movie", "main", "Movies"), ("movie", "4k", "Movies 4K"),
        ("show", "main", "TV"), ("show", "4k", "TV 4K")]
    main = rows[0]
    assert main["is_default"] and main["usable"] and main["path"] == "/media/movies"
    four_k = rows[1]
    assert not four_k["enabled"] and not four_k["usable"] and not four_k["is_default"]


def _reopen(path):
    import database.video_database as vdbmod
    vdbmod._initialized_paths.discard(str(__import__("pathlib").Path(path).resolve()))
    return VideoDatabase(database_path=str(path))


def test_an_existing_install_is_migrated_with_no_change(tmp_path):
    path = tmp_path / "old.db"
    first = VideoDatabase(database_path=str(path))
    # an install from before libraries: no tables, schema v64, its folders set
    conn = sqlite3.connect(str(path))
    conn.execute("DROP TABLE video_title_libraries")
    conn.execute("DROP TABLE video_libraries")
    conn.execute("PRAGMA user_version = 64")
    conn.commit()
    conn.close()
    first.set_setting("movies_path", "/m")
    first.set_setting("tv_path", "/t")
    first.set_setting("movies_4k_enabled", "1")
    first.set_setting("movies_4k_path", "/m4k")
    db = _reopen(path)
    got = {(r["kind"], r["builtin"]): r for r in libs.list_libraries(db)}
    assert got[("movie", "main")]["path"] == "/m" and got[("movie", "main")]["is_default"]
    assert got[("movie", "4k")]["path"] == "/m4k" and got[("movie", "4k")]["usable"]
    assert got[("show", "4k")]["usable"] is False
    # routing is what it was: main for everything, 4K by profile
    assert libs.choose(db, "movie", tmdb_id=1)["builtin"] == "main"
    assert libs.choose(db, "movie", tmdb_id=1, profile_library="4k")["builtin"] == "4k"
    assert libs.choose(db, "show", tmdb_id=1, profile_library="4k")["builtin"] == "main"
    # a restart seeds nothing twice
    db = _reopen(path)
    assert len(libs.list_libraries(db)) == 4


def test_a_legacy_transfer_folder_is_the_main_movie_library(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    db.set_setting("transfer_path", "/legacy")
    assert libs.choose(db, "movie")["path"] == "/legacy"


# ── managing libraries ────────────────────────────────────────────────────────

def test_create_validates_name_kind_and_folder(db):
    with pytest.raises(ValueError, match="name"):
        _add(db, "movie", "  ", "/x")
    with pytest.raises(ValueError, match="movies or TV"):
        _add(db, "youtube", "Y", "/x")
    with pytest.raises(ValueError, match="folder"):
        _add(db, "movie", "Docs", "")
    with pytest.raises(ValueError, match="Movies already uses"):
        _add(db, "movie", "Docs", "/media/movies/")
    db.set_setting("anime_path", "/media/anime")
    with pytest.raises(ValueError, match="anime folder"):
        _add(db, "show", "Kids", "/media/anime")
    lib = _add(db, "movie", "Stand-Up", "/media/standup",
               rules={"genres": ["Comedy", "comedy", ""], "keywords": "stand-up, special"})
    assert lib["usable"] and not lib["builtin"]
    assert lib["rules"] == {"genres": ["Comedy"], "keywords": ["stand-up", "special"]}
    with pytest.raises(ValueError, match="Stand-Up already uses"):
        _add(db, "show", "Other", "/media/standup")


def test_builtins_cannot_be_removed_or_moved_here(db):
    main = libs.choose(db, "movie")
    with pytest.raises(ValueError, match="can't be removed"):
        libs.delete_library(db, main["id"])
    with pytest.raises(ValueError, match="own setting"):
        libs.update_library(db, main["id"], {"path": "/elsewhere"})
    # a rename and rules are fine, and the same folder is not a move
    out = libs.update_library(db, main["id"], {"name": "Movies 1080p", "path": "/media/movies",
                                               "rules": {"genres": ["Drama"]}})
    assert out["name"] == "Movies 1080p" and out["rules"]["genres"] == ["Drama"]
    assert out["path"] == "/media/movies"


def test_default_moves_and_falls_back_to_main(db):
    docs = _add(db, "movie", "Docs", "/media/docs")
    libs.set_default(db, docs["id"])
    assert libs.choose(db, "movie")["id"] == docs["id"]
    assert sum(1 for x in libs.list_libraries(db, "movie") if x["is_default"]) == 1
    # switched off: the main one is the default again
    libs.update_library(db, docs["id"], {"enabled": False})
    assert libs.choose(db, "movie")["builtin"] == "main"
    off = libs.get_library(db, docs["id"])
    with pytest.raises(ValueError, match="switched on"):
        libs.set_default(db, off["id"])
    libs.update_library(db, docs["id"], {"enabled": True, "is_default": True})
    assert libs.choose(db, "movie")["id"] == docs["id"]
    # removed: the main one again, and its title picks go with it
    libs.assign_title(db, "movie", 9, docs["id"])
    assert libs.delete_library(db, docs["id"]) is True
    assert libs.choose(db, "movie")["builtin"] == "main"
    assert libs.title_assignment(db, "movie", 9) is None
    assert libs.delete_library(db, 9999) is False


def test_a_4k_library_off_cannot_be_the_default(db):
    four_k = next(x for x in libs.list_libraries(db, "movie") if x["builtin"] == "4k")
    with pytest.raises(ValueError):
        libs.set_default(db, four_k["id"])


def test_assign_title_checks_the_kind(db):
    tv = _add(db, "show", "Kids TV", "/media/kids-tv")
    with pytest.raises(LookupError):
        libs.assign_title(db, "movie", 5, tv["id"])
    with pytest.raises(ValueError):
        libs.assign_title(db, "movie", None, tv["id"])
    assert libs.assign_title(db, "episode", 5, tv["id"]) == tv["id"]
    assert libs.title_assignment(db, "show", 5) == tv["id"]
    assert libs.assign_title(db, "show", 5, 0) is None
    assert libs.title_assignment(db, "show", 5) is None


# ── the decision ──────────────────────────────────────────────────────────────

def test_rule_match():
    rules = {"genres": ["Documentary"], "keywords": ["stand-up"]}
    assert libs.rule_match(rules, {"genres": ["documentary"]}) == "genre Documentary"
    assert libs.rule_match(rules, {"keywords": ["stand-up comedy"]}) == "keyword stand-up"
    assert libs.rule_match(rules, {}, "Dave Chappelle: Stand-Up Special") == "keyword stand-up"
    assert libs.rule_match(rules, {"genres": ["Comedy"]}, "Heat") is None
    assert libs.rule_match({}, {"genres": ["Comedy"]}) is None


def test_choose_order(db):
    db.set_setting("movies_4k_enabled", "1")
    db.set_setting("movies_4k_path", "/media/movies-4k")
    standup = _add(db, "movie", "Stand-Up", "/media/standup", rules={"keywords": ["stand-up"]})
    docs = _add(db, "movie", "Docs", "/media/docs", rules={"genres": ["Documentary"]})
    kids = _add(db, "movie", "Kids", "/media/kids")
    tags = {"genres": ["Documentary"], "keywords": ["stand-up comedy"]}
    # rules, in the libraries' order
    got = libs.choose(db, "movie", tmdb_id=1, tags=tags)
    assert got["id"] == standup["id"] and got["why"] == "rule: keyword stand-up"
    assert libs.choose(db, "movie", tmdb_id=1, tags={"genres": ["Documentary"]})["id"] == docs["id"]
    # the 4K profile beats the rules (the routing that existed before)
    assert libs.choose(db, "movie", tmdb_id=1, tags=tags, profile_library="4k")["builtin"] == "4k"
    # a title's own pick beats the profile
    libs.assign_title(db, "movie", 1, kids["id"])
    got = libs.choose(db, "movie", tmdb_id=1, tags=tags, profile_library="4k")
    assert got["id"] == kids["id"] and got["why"] == "assigned"
    # a row's pick beats the title's
    got = libs.choose(db, "movie", tmdb_id=1, explicit_id=docs["id"], tags=tags)
    assert got["id"] == docs["id"] and got["why"] == "picked"
    # a switched-off library is skipped at every step
    libs.update_library(db, kids["id"], {"enabled": False})
    libs.update_library(db, docs["id"], {"enabled": False})
    got = libs.choose(db, "movie", tmdb_id=1, explicit_id=docs["id"], tags={"genres": ["Documentary"]})
    assert got["builtin"] == "main" and got["why"] == "default"


def test_choose_reads_profile_and_tags_only_when_needed(db):
    def boom():
        raise AssertionError("asked for something no library needs")
    assert libs.choose(db, "movie", tmdb_id=1, profile_library=boom, tags=boom)["builtin"] == "main"
    assert libs.choose(db, "youtube") is None


# ── routing a wished item ─────────────────────────────────────────────────────

@pytest.fixture()
def installed(db):
    import api.video as videoapi
    videoapi._video_db = db
    try:
        yield db
    finally:
        videoapi._video_db = None


def _route(item, media_type="movie", *, anime="/anime", four_k=""):
    from core.automation.handlers.video_process_wishlist import (
        _default_pick_library, item_target_dir)
    root = "/media/movies" if media_type == "movie" else "/media/tv"
    return item_target_dir(item, media_type, root, anime if media_type == "episode" else "",
                           four_k, lambda it: it.get("route_series_type"),
                           lambda it: it.get("lib") or "main", _default_pick_library)


def test_item_routing_with_libraries(installed, monkeypatch):
    db = installed
    kids = _add(db, "show", "Kids TV", "/media/kids-tv", rules={"genres": ["Kids"]})
    monkeypatch.setattr(libs, "title_tags", lambda d, k, t: {"genres": ["Kids"] if t == 2 else []})
    ep = {"show_tmdb_id": 1, "show_title": "S", "season_number": 1, "episode_number": 1}
    assert _route(dict(ep), "episode") == "/media/tv"
    # a rule match goes to its library, but anime keeps its folder
    assert _route(dict(ep, show_tmdb_id=2), "episode") == "/media/kids-tv"
    assert _route(dict(ep, show_tmdb_id=2, route_series_type="anime"), "episode") == "/anime"
    # a library picked by hand beats the anime folder
    libs.assign_title(db, "show", 3, kids["id"])
    assert _route(dict(ep, show_tmdb_id=3, route_series_type="anime"), "episode") == "/media/kids-tv"
    # the 4K profile keeps routing by the caller's 4K folder
    db.set_setting("movies_4k_enabled", "1")
    db.set_setting("movies_4k_path", "/media/movies-4k")
    assert _route({"tmdb_id": 7, "lib": "4k"}, four_k="/media/movies-4k") == "/media/movies-4k"
    assert _route({"tmdb_id": 7, "lib": "main"}, four_k="/media/movies-4k") == "/media/movies"


def test_item_routing_falls_back_when_libraries_cannot_be_read(monkeypatch):
    import api.video as videoapi
    from core.automation.handlers.video_process_wishlist import _default_pick_library
    monkeypatch.setattr(videoapi, "get_video_db",
                        lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    assert _default_pick_library({"tmdb_id": 1}, "movie", lambda: "main") is None
    assert _route({"tmdb_id": 1, "lib": "4k"}, four_k="/4k") == "/4k"


def test_drain_files_by_library(installed):
    from core.automation.handlers.video_process_wishlist import (
        auto_video_process_wishlist, item_key)
    db = installed
    docs = _add(db, "movie", "Docs", "/media/docs")
    libs.assign_title(db, "movie", 2, docs["id"])
    enq = {}

    class _Deps:
        def update_progress(self, *a, **k):
            pass

    def search(item, mt):
        return [{"filename": "rel-%s" % item_key(item, mt)[1], "title": "r", "username": "u",
                 "size_bytes": 1000, "quality_label": "WEBDL-1080p", "accepted": True, "score": 10}]

    def enqueue(item, best, cands, mt, target):
        enq[item["tmdb_id"]] = target
        return True

    auto_video_process_wishlist(
        {"_automation_id": "a", "max_concurrent": 2, "max_simultaneous_downloads": 10},
        _Deps(), media_type="movie",
        fetch_items=lambda mt: [{"tmdb_id": 1, "title": "A", "year": "2020"},
                                {"tmdb_id": 2, "title": "B", "year": "2020"}],
        active_keys=lambda mt: set(), search=search, enqueue=enqueue,
        record_outcome=lambda *a, **k: None, record_note=lambda *a, **k: None)
    assert enq == {1: "/media/movies", 2: "/media/docs"}


def test_default_target_falls_back_to_a_custom_default(installed):
    from core.automation.handlers.video_process_wishlist import _default_target_dir
    db = installed
    db.set_setting("movies_path", "")
    assert _default_target_dir("movie") == ""
    lib = _add(db, "movie", "Films", "/media/films", is_default=True)
    assert lib["is_default"]
    assert _default_target_dir("movie") == "/media/films"


# ── roots: a custom library's folder is a library folder ─────────────────────

def test_custom_library_folders_are_library_roots(db):
    from core.video.library_roots import library_roots
    lib = _add(db, "movie", "Docs", "/media/docs")
    _add(db, "show", "Kids TV", "/media/kids-tv")
    assert "/media/docs" in library_roots(db, "movie")
    assert "/media/docs" not in library_roots(db, "episode")
    assert "/media/kids-tv" in library_roots(db, "episode")
    libs.update_library(db, lib["id"], {"enabled": False})
    assert "/media/docs" not in library_roots(db, "movie")


def test_library_for_path(db):
    docs = _add(db, "movie", "Docs", "/media/movies/docs")
    got = libs.library_for_path(db, "/media/movies/docs/Free Solo (2018)/f.mkv")
    assert got["id"] == docs["id"]          # the deepest folder wins
    assert libs.library_for_path(db, "/media/movies/Heat (1995)/h.mkv")["builtin"] == "main"
    assert libs.library_for_path(db, "/elsewhere/x.mkv") is None


# ── api ───────────────────────────────────────────────────────────────────────

@pytest.fixture()
def api(installed, monkeypatch):
    import api.video as videoapi
    import core.video.download_monitor as mon
    import core.video.slskd_download as slskd
    monkeypatch.setattr(slskd, "start_download", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(mon, "ensure_started", lambda *a, **k: None)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), installed


def _grab(client, **extra):
    body = {"kind": "movie", "title": "T", "source": "soulseek", "username": "u",
            "size_bytes": 8, "quality_label": "1080p"}
    body.update(extra)
    out = client.post("/api/video/downloads/grab", json=body).get_json()
    assert out["ok"] is True, out
    return out["id"]


def test_api_library_crud(api):
    client, _db = api
    out = client.get("/api/video/libraries/folders").get_json()
    assert len(out["libraries"]) == 4 and "genres" in out
    bad = client.post("/api/video/libraries/folders", json={"kind": "movie", "name": "X"})
    assert bad.status_code == 400 and "folder" in bad.get_json()["error"]
    made = client.post("/api/video/libraries/folders",
                       json={"kind": "movie", "name": "Stand-Up", "path": "/media/standup",
                             "rules": {"keywords": ["stand-up"]}}).get_json()
    lid = made["library"]["id"]
    assert made["success"] and made["library"]["rules"]["keywords"] == ["stand-up"]
    put = client.put("/api/video/libraries/folders/%d" % lid, json={"name": "Comedy"}).get_json()
    assert put["library"]["name"] == "Comedy"
    assert client.post("/api/video/libraries/folders/%d/default" % lid).get_json()["library"]["is_default"]
    assert client.put("/api/video/libraries/folders/9999", json={"name": "x"}).status_code == 404
    main = next(x for x in out["libraries"] if x["builtin"] == "main")
    assert client.delete("/api/video/libraries/folders/%d" % main["id"]).status_code == 400
    assert client.delete("/api/video/libraries/folders/%d" % lid).get_json()["success"]
    assert client.delete("/api/video/libraries/folders/%d" % lid).status_code == 404


def test_api_library_routes_are_admin_only(api):
    client, _db = api
    from flask import g
    app = client.application

    @app.before_request
    def _not_admin():
        g.is_admin = False
        g.profile_id = 2

    assert client.get("/api/video/libraries/folders").status_code == 403
    assert client.post("/api/video/libraries/folders", json={}).status_code == 403
    assert client.post("/api/video/downloads/quality/library", json={}).status_code == 403
    # the picker's read stays open to the get modal
    assert client.get("/api/video/downloads/quality/for?kind=movie&tmdb_id=5").status_code == 200


def test_api_title_pick_routes_the_grab(api):
    client, db = api
    docs = _add(db, "movie", "Docs", "/media/docs")
    info = client.get("/api/video/downloads/quality/for?kind=movie&media_id=77&media_source=tmdb").get_json()
    assert [x["name"] for x in info["libraries"]] == ["Movies", "Docs"]
    assert info["library"]["name"] == "Movies" and info["library_assigned"] == 0
    target = lambda i: db.get_video_download(i)["target_dir"]   # noqa: E731
    assert target(_grab(client, filename="a.mkv", media_id=77, media_source="tmdb")) == "/media/movies"
    res = client.post("/api/video/downloads/quality/library",
                      json={"kind": "movie", "media_id": 77, "media_source": "tmdb",
                            "library_id": docs["id"]}).get_json()
    assert res["success"] and res["library"] == {"id": docs["id"], "name": "Docs", "why": "assigned"}
    assert res["library_assigned"] == docs["id"]
    assert target(_grab(client, filename="b.mkv", media_id=77, media_source="tmdb")) == "/media/docs"
    # a pick for this grab alone
    main = libs.choose(db, "movie")["id"]
    assert target(_grab(client, filename="c.mkv", media_id=77, media_source="tmdb",
                        video_library_id=main)) == "/media/movies"
    # cleared
    client.post("/api/video/downloads/quality/library",
                json={"kind": "movie", "tmdb_id": 77, "library_id": 0})
    assert target(_grab(client, filename="d.mkv", media_id=77, media_source="tmdb")) == "/media/movies"
    bad = client.post("/api/video/downloads/quality/library", json={"kind": "movie", "library_id": 1})
    assert bad.status_code == 400
    tv = _add(db, "show", "Kids TV", "/media/kids-tv")
    wrong = client.post("/api/video/downloads/quality/library",
                        json={"kind": "movie", "tmdb_id": 77, "library_id": tv["id"]})
    assert wrong.status_code == 404


def test_api_grab_follows_rules(api, monkeypatch):
    client, db = api
    _add(db, "movie", "Stand-Up", "/media/standup", rules={"keywords": ["stand-up"]})
    monkeypatch.setattr(libs, "title_tags",
                        lambda d, k, t: {"keywords": ["stand-up comedy"]} if t == 5 else {})
    target = lambda i: db.get_video_download(i)["target_dir"]   # noqa: E731
    assert target(_grab(client, filename="a.mkv", media_id=5, media_source="tmdb")) == "/media/standup"
    assert target(_grab(client, filename="b.mkv", media_id=6, media_source="tmdb")) == "/media/movies"


def test_health_checks_a_custom_library_folder(db, tmp_path):
    from core.video.health import collect
    gone = tmp_path / "gone"
    _add(db, "movie", "Docs", str(gone))
    hit = [c for c in collect(db)["checks"] if str(c["id"]).startswith("library_")]
    assert hit and hit[0]["status"] == "error" and "unreachable" in hit[0]["detail"]
    assert hit[0]["label"] == "Docs library"


def test_drive_map_knows_a_custom_library_folder(db):
    from core.video.drive_map import _configured_roles
    _add(db, "show", "Kids TV", "/media/kids-tv")
    assert ("/media/kids-tv", "your Kids TV library") in _configured_roles(db)
