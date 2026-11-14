"""#2029 step 5: per-library automatic search and upgrades, and a library
filter on the movies / shows list, the wanted list and the activity list.
Scans attributing files to libraries is covered in the copies tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

import core.video.libraries as libs
from database.video_database import VideoDatabase


@pytest.fixture(autouse=True)
def _no_tmdb(monkeypatch):
    monkeypatch.setattr(libs, "title_tags", lambda db, kind, tmdb_id: {"genres": [], "keywords": []})


@pytest.fixture()
def db(tmp_path):
    import api.video as videoapi
    vdb = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    vdb.set_setting("movies_path", "/media/movies")
    vdb.set_setting("tv_path", "/media/tv")
    videoapi._video_db = vdb
    try:
        yield vdb
    finally:
        videoapi._video_db = None


@pytest.fixture()
def client(db, monkeypatch):
    import api.video as videoapi
    import core.video.download_monitor as mon
    monkeypatch.setattr(mon, "ensure_started", lambda *a, **k: None)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client()


def _docs(db, **opts):
    lib = libs.create_library(db, {"kind": "movie", "name": "Docs", "path": "/media/docs", **opts})
    libs.assign_title(db, "movie", 5, lib["id"])
    return lib


def test_switches_default_on_and_save(db):
    lib = _docs(db)
    assert lib["options"]["auto_search"] is True and lib["options"]["upgrades"] is True
    out = libs.update_library(db, lib["id"], {"auto_search": False, "upgrades": "0"})
    assert out["options"]["auto_search"] is False and out["options"]["upgrades"] is False
    assert libs.allows_automatic(None, True) is True
    assert libs.allows_automatic(out, False) is False
    on = libs.update_library(db, lib["id"], {"auto_search": True})
    assert libs.allows_automatic(on, False) is True and libs.allows_automatic(on, True) is False


def _drain(items):
    from core.automation.handlers.video_process_wishlist import auto_video_process_wishlist
    searched = []

    class _Deps:
        def update_progress(self, *a, **k):
            pass

    def search(item, mt):
        searched.append(item["tmdb_id"])
        return []

    auto_video_process_wishlist(
        {"_automation_id": "a", "max_concurrent": 1, "max_simultaneous_downloads": 10},
        _Deps(), media_type="movie", fetch_items=lambda mt: items, active_keys=lambda mt: set(),
        search=search, enqueue=lambda *a, **k: True,
        record_outcome=lambda *a, **k: None, record_note=lambda *a, **k: None)
    return sorted(searched)


def test_drain_skips_a_library_with_automatic_search_off(db):
    lib = _docs(db, auto_search=False)
    items = [{"tmdb_id": 5, "title": "Free Solo", "year": 2018},
             {"tmdb_id": 6, "title": "Heat", "year": 1995}]
    assert _drain([dict(i) for i in items]) == [6]
    libs.update_library(db, lib["id"], {"auto_search": True})
    assert _drain([dict(i) for i in items]) == [5, 6]


def test_drain_skips_upgrades_when_a_library_says_so(db):
    _docs(db, upgrades=False)
    items = [{"tmdb_id": 5, "title": "Free Solo", "year": 2018, "owned": 1, "owned_resolutions": "720p"},
             {"tmdb_id": 5, "title": "Free Solo", "year": 2018}]
    from core.automation.handlers.video_process_wishlist import library_allows, _default_pick_library
    assert library_allows(dict(items[0]), "movie", _default_pick_library, lambda it: "main") is False
    assert library_allows(dict(items[1]), "movie", _default_pick_library, lambda it: "main") is True


def test_drain_stamps_where_a_wish_files(db):
    from core.automation.handlers.video_process_wishlist import _default_pick_library
    lib = _docs(db)
    db.add_movie_to_wishlist(5, "Free Solo")
    db.add_movie_to_wishlist(6, "Heat")
    _default_pick_library({"tmdb_id": 6, "title": "Heat"}, "movie", lambda: "main")
    conn = db.connect()
    got = dict(conn.execute("SELECT tmdb_id, filed_library_id FROM video_wishlist").fetchall())
    conn.close()
    main = libs.choose(db, "movie")["id"]
    assert got == {5: None, 6: main}          # the drain stamped 6
    # a pick made while it's on the wishlist is stamped at once
    libs.assign_title(db, "movie", 6, lib["id"])
    conn = db.connect()
    got = dict(conn.execute("SELECT tmdb_id, filed_library_id FROM video_wishlist").fetchall())
    conn.close()
    assert got[6] == lib["id"]


def test_rss_skips_a_library_with_automatic_search_off(db, monkeypatch):
    import core.video.rss_sync as rss
    from core.automation.handlers import video_process_wishlist as vpw
    _docs(db, auto_search=False)
    db.set_setting("download_mode", "torrent")
    db.add_movie_to_wishlist(5, "Free Solo", year=2018)
    db.add_movie_to_wishlist(6, "Heat", year=1995)
    ranked = []
    monkeypatch.setattr(rss, "_prescreen", lambda hits, titles: hits)
    monkeypatch.setattr(rss, "_rank", lambda pool, item, mt: ranked.append(item.get("tmdb_id")) or [])
    monkeypatch.setattr(vpw, "_default_active_keys", lambda mt: set())
    rss._rss_pass_inner(fetch=lambda: [{"title": "x", "protocol": "torrent", "guid": "g"}],
                        log=lambda *a: None, extto=False)
    assert 5 not in ranked and 6 in ranked


def test_library_list_filter(db, client):
    docs = _docs(db)
    db.upsert_movie("plex", {"server_id": "a", "title": "Free Solo", "tmdb_id": 5,
                             "files": [{"relative_path": "/media/docs/Free Solo/f.mkv"}]})
    db.upsert_movie("plex", {"server_id": "b", "title": "Heat", "tmdb_id": 6,
                             "files": [{"relative_path": "/media/movies/Heat/h.mkv"}]})
    libs.attribute_files(db)
    titles = lambda q: [i["title"] for i in client.get("/api/video/library?kind=movies" + q).get_json()["items"]]  # noqa: E731
    assert sorted(titles("")) == ["Free Solo", "Heat"]
    assert titles("&video_library=%d" % docs["id"]) == ["Free Solo"]
    names = client.get("/api/video/library/video-libraries").get_json()["libraries"]
    assert {(n["name"], n["kind"]) for n in names} == {("Movies", "movie"), ("Docs", "movie"), ("TV", "show")}


def test_wanted_list_filter(db, client):
    docs = _docs(db)
    four_k = libs.create_library(db, {"kind": "movie", "name": "Films", "path": "/media/films"})
    db.add_movie_to_wishlist(5, "Free Solo")
    db.add_movie_to_wishlist(6, "Heat")
    libs.add_copy(db, "movie", 6, four_k["id"], title="Heat")
    db.stamp_filed_library("movie", 6, libs.choose(db, "movie")["id"])

    def titles(lib):
        q = "/api/video/wishlist?kind=movie" + ("&video_library=%d" % lib if lib else "")
        return sorted(((i["title"], i.get("video_library_name")) for i in client.get(q).get_json()["items"]),
                      key=str)
    assert titles(docs["id"]) == [("Free Solo", None)]
    assert titles(four_k["id"]) == [("Heat", "Films")]
    assert titles(libs.choose(db, "movie")["id"]) == [("Heat", None)]
    assert len(titles(0)) == 3


def test_activity_rows_name_their_library(db, client):
    docs = _docs(db)
    a = db.add_video_download({"kind": "movie", "title": "Free Solo", "target_dir": "/media/docs",
                               "status": "downloading"})
    b = db.add_video_download({"kind": "movie", "title": "Heat", "target_dir": "/media/movies",
                               "status": "downloading"})
    c = db.add_video_download({"kind": "youtube", "title": "Clip", "target_dir": "/media/yt",
                               "status": "downloading"})
    rows = {r["id"]: r for r in client.get("/api/video/downloads/active").get_json()["downloads"]}
    assert rows[a]["video_library_name"] == "Docs" and rows[a]["video_library_id"] == docs["id"]
    assert rows[b]["video_library_name"] == "Movies"
    assert rows[c]["video_library_name"] is None


def test_library_page_offers_the_filter():
    # the library page is the React manager now: its Library filter is read
    # from the manager's facets and shown when there is more than one
    src = Path(__file__).resolve().parents[2] / "webui" / "src" / "features" / "video-manager"
    filters = (src / "manager-filters.tsx").read_text(encoding="utf-8")
    assert "facets.libraries.length > 1" in filters
    assert "video_library" in filters
    api = (Path(__file__).resolve().parents[2] / "api" / "video" / "manager.py").read_text(encoding="utf-8")
    assert "list_libraries" in api

