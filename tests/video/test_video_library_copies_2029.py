"""#2029 step 2: a default quality profile per video library, and the same
movie wanted in two libraries (Movies 1080p and Movies 4K) as two copies,
neither replacing the other.

Covers the schema change (the movie wishlist unique per library, the old index
dropped on upgrade), the per-library profile chain, adding and removing a
copy, ownership by the library a file sits in (scan attribution), the drain
grabbing both copies, the monitor clearing only the copy that landed, the
importer looking only at the copy in its own library, and the api.
"""

from __future__ import annotations

import sqlite3

import pytest
from flask import Flask

import core.video.libraries as libs
from database.video_database import ANY_LIBRARY, VideoDatabase


@pytest.fixture(autouse=True)
def _no_tmdb(monkeypatch):
    monkeypatch.setattr(libs, "title_tags", lambda db, kind, tmdb_id: {"genres": [], "keywords": []})


@pytest.fixture()
def db(tmp_path):
    vdb = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    vdb.set_setting("movies_path", "/media/movies")
    vdb.set_setting("tv_path", "/media/tv")
    vdb.set_setting("movies_4k_enabled", "1")
    vdb.set_setting("movies_4k_path", "/media/movies-4k")
    return vdb


@pytest.fixture()
def installed(db):
    import api.video as videoapi
    videoapi._video_db = db
    try:
        yield db
    finally:
        videoapi._video_db = None


def _lib(db, builtin, kind="movie"):
    return next(x for x in libs.list_libraries(db, kind) if x["builtin"] == builtin)


def _profile(db, name, **raw):
    from core.video.quality_profile import save_named
    return save_named(db, None, name, raw)["id"]


def _movie(db, server_id, tmdb, path, res="1080p"):
    db.upsert_movie("plex", {"server_id": server_id, "title": "Heat", "tmdb_id": tmdb,
                             "files": [{"relative_path": path, "resolution": res, "size_bytes": 10}]})


def _rows(db):
    conn = db.connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT tmdb_id, video_library_id, quality_profile_id, search_attempts FROM video_wishlist "
            "WHERE kind='movie' ORDER BY COALESCE(video_library_id, 0)").fetchall()]
    finally:
        conn.close()


# ── schema ────────────────────────────────────────────────────────────────────

def test_a_movie_can_be_wished_once_per_library(db):
    four_k = _lib(db, "4k")["id"]
    assert db.add_movie_to_wishlist(9, "Heat")
    assert db.add_movie_to_wishlist(9, "Heat", video_library_id=four_k, quality_profile_id=None)
    assert db.add_movie_to_wishlist(9, "Heat again")                         # upsert, not a twin
    assert db.add_movie_to_wishlist(9, "Heat", video_library_id=four_k)
    assert [(r["tmdb_id"], r["video_library_id"]) for r in _rows(db)] == [(9, None), (9, four_k)]


def test_the_old_one_per_movie_index_is_dropped_on_upgrade(tmp_path):
    import database.video_database as vdbmod
    path = tmp_path / "old.db"
    VideoDatabase(database_path=str(path))
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE UNIQUE INDEX idx_video_wishlist_movie ON video_wishlist(profile_id, tmdb_id) "
                 "WHERE kind = 'movie'")
    conn.execute("PRAGMA user_version = 65")
    conn.commit()
    conn.close()
    vdbmod._initialized_paths.discard(str(path.resolve()))
    db = VideoDatabase(database_path=str(path))
    conn = sqlite3.connect(str(path))
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert "idx_video_wishlist_movie" not in names and "idx_video_wishlist_movie_lib" in names
    db.add_movie_to_wishlist(1, "A")
    db.add_movie_to_wishlist(1, "A", video_library_id=_lib(db, "4k")["id"])
    assert len(_rows(db)) == 2


# ── a default quality profile per library ────────────────────────────────────

def test_library_default_profile(db):
    from core.video.quality_profile import load_for_item
    uhd = _profile(db, "UHD", cutoff_resolution="2160p")
    own = _profile(db, "Own", cutoff_resolution="720p")
    docs = libs.create_library(db, {"kind": "movie", "name": "Docs", "path": "/media/docs",
                                    "quality_profile_id": uhd})
    assert docs["quality_profile_id"] == uhd
    with pytest.raises(ValueError, match="quality profile"):
        libs.update_library(db, docs["id"], {"quality_profile_id": 999})
    # a title in no library with a profile: the defaults, exactly as before
    assert libs.default_profile_for(db, "movie", tmdb_id=1) is None
    assert load_for_item(db, {"tmdb_id": 1})["cutoff_resolution"] == "1080p"
    # a title filed in Docs: its default
    libs.assign_title(db, "movie", 2, docs["id"])
    assert libs.default_profile_for(db, "movie", tmdb_id=2) == uhd
    assert load_for_item(db, {"tmdb_id": 2})["cutoff_resolution"] == "2160p"
    # the title's own profile wins
    assert load_for_item(db, {"tmdb_id": 2, "quality_profile_id": own})["cutoff_resolution"] == "720p"
    # cleared: back to the defaults
    libs.update_library(db, docs["id"], {"quality_profile_id": 0})
    assert libs.default_profile_for(db, "movie", tmdb_id=2) is None


def test_default_profile_for_skips_routing_when_no_library_has_one(db, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("routed a title nobody needs routed")
    monkeypatch.setattr(libs, "choose", boom)
    assert libs.default_profile_for(db, "movie", tmdb_id=5) is None


# ── copies ────────────────────────────────────────────────────────────────────

def test_add_and_remove_a_copy(db):
    uhd = _profile(db, "UHD", cutoff_resolution="2160p")
    four_k = _lib(db, "4k")
    libs.update_library(db, four_k["id"], {"quality_profile_id": uhd})
    db.add_movie_to_wishlist(9, "Heat")
    got = libs.add_copy(db, "movie", 9, four_k["id"], title="Heat", year=1995)
    assert got["id"] == four_k["id"]
    assert libs.title_copies(db, "movie", 9) == [four_k["id"]]
    rows = _rows(db)
    assert rows[1]["video_library_id"] == four_k["id"] and rows[1]["quality_profile_id"] == uhd
    with pytest.raises(ValueError, match="movies"):
        libs.add_copy(db, "show", 9, four_k["id"], title="Heat")
    tv = _lib(db, "main", "show")
    with pytest.raises(LookupError):
        libs.add_copy(db, "movie", 9, tv["id"], title="Heat")
    db.set_setting("movies_4k_enabled", "0")
    with pytest.raises(ValueError, match="switched on"):
        libs.add_copy(db, "movie", 10, four_k["id"], title="Other")
    db.set_setting("movies_4k_enabled", "1")
    assert libs.remove_copy(db, "movie", 9, four_k["id"]) is True
    assert libs.title_copies(db, "movie", 9) == []
    assert [r["video_library_id"] for r in _rows(db)] == [None]
    assert libs.remove_copy(db, "movie", 9, four_k["id"]) is False


def test_removing_a_library_drops_its_copies(db):
    films = libs.create_library(db, {"kind": "movie", "name": "Films", "path": "/media/films"})
    db.add_movie_to_wishlist(9, "Heat")
    libs.add_copy(db, "movie", 9, films["id"], title="Heat")
    libs.delete_library(db, films["id"])
    assert libs.title_copies(db, "movie", 9) == []
    assert [r["video_library_id"] for r in _rows(db)] == [None]


# ── which library a file sits in ──────────────────────────────────────────────

def test_attribute_files_by_folder_and_drive_map(db):
    _movie(db, "a", 9, "/media/movies/Heat (1995)/heat.mkv")
    _movie(db, "b", 9, "/media/movies-4k/Heat (1995)/heat.mkv", "2160p")
    _movie(db, "c", 7, "/srv/plex/films4k/Ronin (1998)/ronin.mkv", "2160p")
    _movie(db, "d", 8, "/nowhere/x.mkv")
    from core.video import drive_map
    drive_map.save(db, [{"server": "/srv/plex/films4k", "local": "/media/movies-4k", "kind": "movie"}])
    drive_map.forget()
    assert libs.attribute_files(db) == 3
    conn = db.connect()
    got = dict(conn.execute("SELECT server_id, video_library_id FROM movies").fetchall())
    conn.close()
    main, four_k = _lib(db, "main")["id"], _lib(db, "4k")["id"]
    assert got == {"a": main, "b": four_k, "c": four_k, "d": None}
    assert libs.attribute_files(db) == 0          # nothing moved


# ── the drain query: each copy owned only by its own library ─────────────────

def test_each_copy_is_owned_only_by_its_library(db):
    four_k = _lib(db, "4k")["id"]
    db.add_movie_to_wishlist(9, "Heat")
    libs.add_copy(db, "movie", 9, four_k, title="Heat")
    _movie(db, "a", 9, "/media/movies/Heat (1995)/heat.mkv", "1080p")
    libs.attribute_files(db)
    rows = {r["video_library_id"]: r for r in db.movie_wishlist_to_download(due_only=False)}
    assert set(rows) == {None, four_k}                # both copies are hunted
    assert rows[None]["owned"] == 1 and rows[None]["owned_resolutions"] == "1080p"
    assert rows[four_k]["owned"] == 0 and not rows[four_k]["owned_resolutions"]
    # the 4K copy lands: it owns the copy row, never the title's own
    _movie(db, "b", 9, "/media/movies-4k/Heat (1995)/heat.mkv", "2160p")
    libs.attribute_files(db)
    rows = {r["video_library_id"]: r for r in db.movie_wishlist_to_download(due_only=False)}
    assert rows[None]["owned_resolutions"] == "1080p"
    assert rows[four_k]["owned_resolutions"] == "2160p"


def test_a_title_with_no_copy_is_owned_by_any_file_as_before(db):
    db.add_movie_to_wishlist(9, "Heat")
    _movie(db, "b", 9, "/media/movies-4k/Heat (1995)/heat.mkv", "2160p")
    libs.attribute_files(db)
    (row,) = db.movie_wishlist_to_download(due_only=False)
    assert row["owned"] == 1 and row["owned_resolutions"] == "2160p" and row["video_library_id"] is None


def test_a_copy_keeps_its_own_profile(db):
    uhd = _profile(db, "UHD")
    hd = _profile(db, "HD")
    four_k = _lib(db, "4k")["id"]
    db.add_movie_to_wishlist(9, "Heat")
    libs.add_copy(db, "movie", 9, four_k, title="Heat", quality_profile_id=uhd)
    # picking a profile for the title changes the title's row, not the copy's
    db.set_title_profile_choice("movie", 9, hd)
    db.set_wishlist_quality_for_tmdb(9, hd)
    rows = {r["video_library_id"]: r for r in db.movie_wishlist_to_download(due_only=False)}
    assert rows[None]["quality_profile_id"] == hd and rows[four_k]["quality_profile_id"] == uhd


def test_stored_file_path_looks_at_the_right_copy(db):
    four_k = _lib(db, "4k")["id"]
    _movie(db, "a", 9, "/media/movies/Heat (1995)/heat.mkv")
    db.upsert_movie("plex", {"server_id": "b", "title": "Heat", "tmdb_id": 9, "files": [
        {"relative_path": "/media/movies-4k/Heat (1995)/heat.mkv", "resolution": "2160p",
         "size_bytes": 99}]})
    libs.attribute_files(db)
    biggest = db.video_stored_file_path("movie", tmdb_id=9)
    assert biggest["path"].startswith("/media/movies-4k")          # as before: the largest
    db.add_movie_to_wishlist(9, "Heat")
    libs.add_copy(db, "movie", 9, four_k, title="Heat")
    assert db.video_stored_file_path("movie", tmdb_id=9, video_library_id=None)["path"].startswith(
        "/media/movies/")
    assert db.video_stored_file_path("movie", tmdb_id=9, video_library_id=four_k)["path"].startswith(
        "/media/movies-4k/")


# ── the drain and the monitor ─────────────────────────────────────────────────

def test_drain_grabs_both_copies_into_their_libraries(installed):
    from core.automation.handlers.video_process_wishlist import (
        auto_video_process_wishlist, build_download_record, item_key)
    db = installed
    uhd = _profile(db, "UHD")
    four_k = _lib(db, "4k")["id"]
    libs.update_library(db, four_k, {"quality_profile_id": uhd})
    db.add_movie_to_wishlist(9, "Heat", year=1995)
    libs.add_copy(db, "movie", 9, four_k, title="Heat", year=1995)
    grabbed = {}

    class _Deps:
        def update_progress(self, *a, **k):
            pass

    def search(item, mt):
        return [{"filename": "heat.%s.mkv" % item.get("video_library_id"), "title": "Heat 1995",
                 "username": "u", "size_bytes": 1000, "quality_label": "WEBDL-1080p",
                 "accepted": True, "score": 10}]

    def enqueue(item, best, cands, mt, target):
        grabbed[item.get("video_library_id")] = (target, build_download_record(
            item, best, cands, media_type=mt, target_dir=target, query="q"))
        return True

    items = db.movie_wishlist_to_download(due_only=False)
    assert len({item_key(it, "movie") for it in items}) == 2
    auto_video_process_wishlist(
        {"_automation_id": "a", "max_concurrent": 2, "max_simultaneous_downloads": 10},
        _Deps(), media_type="movie", fetch_items=lambda mt: items,
        active_keys=lambda mt: set(), search=search, enqueue=enqueue,
        record_outcome=lambda *a, **k: None, record_note=lambda *a, **k: None)
    assert grabbed[None][0] == "/media/movies"
    assert grabbed[four_k][0] == "/media/movies-4k"
    assert grabbed[None][1]["video_library_id"] is None
    rec = grabbed[four_k][1]
    assert rec["video_library_id"] == four_k and rec["quality_profile_id"] == uhd


def test_an_in_flight_copy_does_not_block_the_other(installed):
    from core.automation.handlers.video_process_wishlist import active_download_keys, item_key
    keys = active_download_keys([{"kind": "movie", "media_id": "9", "video_library_id": 4}])
    assert item_key({"tmdb_id": 9, "video_library_id": 4}, "movie") in keys
    assert item_key({"tmdb_id": 9}, "movie") not in keys
    keys = active_download_keys([{"kind": "movie", "media_id": "9"}])
    assert item_key({"tmdb_id": 9}, "movie") in keys


def test_drain_outcomes_touch_only_their_own_row(installed):
    from core.automation.handlers.video_process_wishlist import _default_record_outcome
    db = installed
    four_k = _lib(db, "4k")["id"]
    db.add_movie_to_wishlist(9, "Heat")
    libs.add_copy(db, "movie", 9, four_k, title="Heat")
    _default_record_outcome({"tmdb_id": 9, "video_library_id": four_k}, "movie", False)
    _default_record_outcome({"tmdb_id": 9, "video_library_id": four_k}, "movie", False)
    _default_record_outcome({"tmdb_id": 9}, "movie", False)
    assert [r["search_attempts"] for r in _rows(db)] == [1, 2]


def test_a_landed_copy_clears_only_its_own_wish(db):
    import core.video.download_monitor as dm
    four_k = _lib(db, "4k")["id"]
    db.add_movie_to_wishlist(9, "Heat")
    libs.add_copy(db, "movie", 9, four_k, title="Heat")
    dm._wishlist_obtained(db, {"kind": "movie", "media_id": "9", "media_source": "tmdb",
                               "quality_label": "2160p", "video_library_id": four_k})
    assert [r["video_library_id"] for r in _rows(db)] == [None]
    # the copy stays wanted in that library: its file never owns the title's row
    assert libs.title_copies(db, "movie", 9) == [four_k]
    libs.add_copy(db, "movie", 9, four_k, title="Heat")
    dm._wishlist_obtained(db, {"kind": "movie", "media_id": "9", "media_source": "tmdb",
                               "quality_label": "1080p"})
    assert [r["video_library_id"] for r in _rows(db)] == [four_k]


def test_a_failed_copy_comes_back_as_that_copy(db):
    import core.video.download_monitor as dm
    four_k = _lib(db, "4k")["id"]
    db.add_movie_to_wishlist(9, "Heat")
    dm._wishlist_failed(db, {"kind": "movie", "media_id": "9", "media_source": "tmdb",
                             "title": "Heat", "video_library_id": four_k, "quality_profile_id": None})
    assert sorted(r["video_library_id"] or 0 for r in _rows(db)) == [0, four_k]


def test_import_lands_beside_the_copy_in_its_own_library(installed, monkeypatch):
    import core.video.download_monitor as dm
    import core.video.path_resolver as pr
    db = installed
    four_k = _lib(db, "4k")["id"]
    _movie(db, "a", 9, "/media/movies/Heat (1995)/heat.mkv")
    db.upsert_movie("plex", {"server_id": "b", "title": "Heat", "tmdb_id": 9, "files": [
        {"relative_path": "/media/movies-4k/Heat (1995)/heat.mkv", "resolution": "2160p",
         "size_bytes": 99}]})
    libs.attribute_files(db)
    db.add_movie_to_wishlist(9, "Heat")
    libs.add_copy(db, "movie", 9, four_k, title="Heat")
    monkeypatch.setattr(pr, "resolve_video_file_path", lambda p, *a, **k: p)
    copy = dm._owned_library_dir(db, {"kind": "movie", "media_id": "9", "media_source": "tmdb",
                                      "video_library_id": four_k})
    own = dm._owned_library_dir(db, {"kind": "movie", "media_id": "9", "media_source": "tmdb"})
    assert copy == "/media/movies-4k/Heat (1995)" and own == "/media/movies/Heat (1995)"


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


def test_api_copies(api):
    client, db = api
    four_k = _lib(db, "4k")["id"]
    main = _lib(db, "main")["id"]
    body = {"media_id": 9, "media_source": "tmdb", "title": "Heat", "year": 1995}
    same = client.post("/api/video/downloads/quality/copies", json={**body, "library_id": main})
    assert same.status_code == 400
    out = client.post("/api/video/downloads/quality/copies", json={**body, "library_id": four_k}).get_json()
    assert out["success"] and out["copies"] == [
        {"library_id": four_k, "name": "Movies 4K", "wished": True, "owned": False}]
    info = client.get("/api/video/downloads/quality/for?kind=movie&media_id=9&media_source=tmdb").get_json()
    assert info["copies"][0]["library_id"] == four_k
    # the wishlist shows the copy as its own card
    items = client.get("/api/video/wishlist?kind=movie").get_json()
    lst = items.get("items") or items.get("movies") or []
    names = sorted(str(i.get("video_library_name")) for i in lst)
    assert names == ["Movies 4K"]
    # a grab for the copy files there and is marked as that copy
    dl = client.post("/api/video/downloads/grab", json={
        "kind": "movie", "title": "Heat", "source": "soulseek", "username": "u", "filename": "h.mkv",
        "size_bytes": 8, "media_id": 9, "media_source": "tmdb", "video_library_id": four_k}).get_json()
    row = db.get_video_download(dl["id"])
    assert row["target_dir"] == "/media/movies-4k" and row["video_library_id"] == four_k
    gone = client.delete("/api/video/downloads/quality/copies", json={**body, "library_id": four_k})
    assert gone.get_json()["copies"] == []
    assert client.delete("/api/video/downloads/quality/copies",
                         json={**body, "library_id": four_k}).status_code == 404


def test_api_wishlist_remove_one_library_row(api):
    client, db = api
    four_k = _lib(db, "4k")["id"]
    db.add_movie_to_wishlist(9, "Heat")
    libs.add_copy(db, "movie", 9, four_k, title="Heat")
    # the title's own card: only its row
    client.post("/api/video/wishlist/remove", json={"scope": "movie", "tmdb_id": 9, "video_library_id": 0})
    assert [r["video_library_id"] for r in _rows(db)] == [four_k]
    # the copy's card: its row and the copy
    client.post("/api/video/wishlist/remove", json={"scope": "movie", "tmdb_id": 9,
                                                    "video_library_id": four_k})
    assert _rows(db) == [] and libs.title_copies(db, "movie", 9) == []
    # no library named: every row, as before
    db.add_movie_to_wishlist(9, "Heat")
    libs.add_copy(db, "movie", 9, four_k, title="Heat")
    client.post("/api/video/wishlist/remove", json={"scope": "movie", "tmdb_id": 9})
    assert _rows(db) == []


def test_api_profile_for_a_title_in_a_library_with_a_default(api):
    client, db = api
    uhd = _profile(db, "UHD")
    docs = libs.create_library(db, {"kind": "movie", "name": "Docs", "path": "/media/docs",
                                    "quality_profile_id": uhd})
    libs.assign_title(db, "movie", 5, docs["id"])
    info = client.get("/api/video/downloads/quality/for?kind=movie&media_id=5&media_source=tmdb").get_json()
    assert info["profile_id"] == uhd and info["assigned"] is False
    dl = client.post("/api/video/downloads/grab", json={
        "kind": "movie", "title": "T", "source": "soulseek", "username": "u", "filename": "t.mkv",
        "size_bytes": 8, "media_id": 5, "media_source": "tmdb"}).get_json()
    assert db.get_video_download(dl["id"])["quality_profile_id"] == uhd


def test_wishlist_writers_default_to_every_row(db):
    four_k = _lib(db, "4k")["id"]
    db.add_movie_to_wishlist(9, "Heat")
    libs.add_copy(db, "movie", 9, four_k, title="Heat")
    db.record_wishlist_search_outcome("movie", 9, False)
    assert [r["search_attempts"] for r in _rows(db)] == [1, 1]
    assert len(db.get_movie_wishlist_profiles(9)) == 2
    assert len(db.get_movie_wishlist_profiles(9, video_library_id=None)) == 1
    assert db.wishlist_profiles_for_media("movie", 9, video_library_id=four_k) == [1]
    assert ANY_LIBRARY is not None


def test_retry_of_a_copy_searches_that_copy(db, monkeypatch):
    import core.video.download_monitor as dm
    import core.video.wishlist_search as ws
    four_k = _lib(db, "4k")["id"]
    db.add_movie_to_wishlist(9, "Heat")
    libs.add_copy(db, "movie", 9, four_k, title="Heat")
    seen = []
    monkeypatch.setattr(ws, "manual_search", lambda *a, **k: seen.append(k) or {"queued": 1})
    dm._search_again_now(db, {"kind": "movie", "media_id": "9", "media_source": "tmdb",
                              "video_library_id": four_k})
    dm._search_again_now(db, {"kind": "movie", "media_id": "9", "media_source": "tmdb"})
    assert [k.get("video_library_id") for k in seen] == [four_k, 0]
    # a title with no copy: searched exactly as before (no library named)
    seen.clear()
    db.add_movie_to_wishlist(5, "Ronin")
    dm._search_again_now(db, {"kind": "movie", "media_id": "5", "media_source": "tmdb"})
    assert "video_library_id" not in seen[0]
