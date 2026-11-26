"""more than one movie library: besides the main movies pick, other movie
libraries on the server can be switched on to scan (``movies_more``), the
way tv_more works for TV. a scan of plex or jellyfin reads every picked one,
full or quick; their folders are listed under your library's folders; one
switched off stops being read and nothing is deleted on the way."""

from __future__ import annotations

from datetime import datetime

import pytest
from flask import Flask

import core.video.sources as vs
from core.video import drive_map
from core.video.scanner import VideoLibraryScanner
from core.video.sources import JellyfinVideoSource, PlexVideoSource
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


@pytest.fixture(autouse=True)
def _fresh_folders():
    drive_map._folders_cache.update(at=0.0, folders=None)
    drive_map._counts_cache.clear()
    yield
    drive_map._folders_cache.update(at=0.0, folders=None)
    drive_map._counts_cache.clear()


# ── storage ──────────────────────────────────────────────────────────────────

def test_selection_keeps_the_extra_movie_libraries(db):
    assert db.get_library_selection("plex")["movies_more"] == []
    db.set_library_selection("plex", "Movies", "TV", movies_more=["4K Movies", " Kids ", "", "Movies", "4K Movies"])
    sel = db.get_library_selection("plex")
    # trimmed, deduped, the main pick left out
    assert sel["movies"] == "Movies" and sel["movies_more"] == ["4K Movies", "Kids"]
    # an older caller saving only movies + tv keeps them, tv_more too
    db.set_library_selection("plex", "Movies", "TV", tv_more=["Anime"])
    db.set_library_selection("plex", "Movies", "TV")
    sel = db.get_library_selection("plex")
    assert sel["movies_more"] == ["4K Movies", "Kids"] and sel["tv_more"] == ["Anime"]
    # each server keeps its own
    assert db.get_library_selection("jellyfin")["movies_more"] == []
    # a junk value saves as none
    db.set_library_selection("plex", "Movies", "TV", movies_more="4K Movies")
    assert db.get_library_selection("plex")["movies_more"] == []


def test_an_old_save_without_the_key_reads_as_none(db):
    db.set_setting("plex.movies_library", "Movies")
    db.set_setting("plex.tv_library", "TV")
    assert db.get_library_selection("plex")["movies_more"] == []
    db.set_setting("plex.movies_libraries_more", "not json")
    assert db.get_library_selection("plex")["movies_more"] == []


@pytest.fixture()
def api(tmp_path, monkeypatch):
    import api.video as videoapi
    vdb = VideoDatabase(database_path=str(tmp_path / "video_api.db"))
    videoapi._video_db = vdb
    monkeypatch.setattr(vs, "resolve_video_server", lambda *a, **k: "plex")
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client(), vdb
    finally:
        videoapi._video_db = None


def test_the_endpoint_saves_the_list_and_an_older_client_leaves_it(api):
    client, vdb = api
    drive_map._folders_cache.update(at=1.0, folders=[{"server": "/old"}])
    r = client.post("/api/video/libraries", json={"movies": "Movies", "movies_more": ["4K Movies"], "tv": "TV"})
    assert r.status_code == 200, r.get_json()
    assert vdb.get_library_selection("plex")["movies_more"] == ["4K Movies"]
    # the folders list is asked again after a change of picks
    assert drive_map._folders_cache["at"] == 0.0
    client.post("/api/video/libraries", json={"movies": "Movies", "tv": "TV", "tv_more": []})
    assert vdb.get_library_selection("plex")["movies_more"] == ["4K Movies"]
    client.post("/api/video/libraries", json={"movies": "Movies", "movies_more": [], "tv": "TV"})
    assert vdb.get_library_selection("plex")["movies_more"] == []


def test_movie_pick_shapes():
    from core.video.sources import _movie_pick
    assert _movie_pick({}) is None
    assert _movie_pick({"movies": ""}) is None
    assert _movie_pick({"movies": "Movies"}) == ["Movies"]
    assert _movie_pick({"movies": "Movies", "movies_more": ["4K Movies"]}) == ["Movies", "4K Movies"]
    assert _movie_pick({"movies": "", "movies_more": ["4K Movies"]}) == ["4K Movies"]
    assert _movie_pick({"movies": "Movies", "movies_more": ["Movies"]}) == ["Movies"]


# ── plex ─────────────────────────────────────────────────────────────────────

class _Movie:
    def __init__(self, key, title, path):
        self.ratingKey, self.title, self.path = key, title, path


class _Sec:
    def __init__(self, kind, title, items=(), locations=()):
        self.type, self.title, self._items = kind, title, list(items)
        self.locations, self.updated, self.searched = list(locations), 0, []
        self.totalSize = len(self._items)

    def all(self):
        return list(self._items)

    def search(self, **kw):
        self.searched.append(kw)
        return list(self._items)

    def update(self):
        self.updated += 1


class _Lib:
    def __init__(self, secs):
        self._secs = secs

    def sections(self):
        return list(self._secs)


class _Server:
    def __init__(self, secs):
        self.library = _Lib(secs)


def _plex_secs():
    return [
        _Sec("movie", "Movies", [_Movie(1, "Heat", "/mnt/a/MOVIES/Heat (1995)/Heat.mkv")], ["/mnt/a/MOVIES"]),
        _Sec("movie", "4K Movies", [_Movie(2, "Heat", "/mnt/b/4K/Heat (1995)/Heat.2160p.mkv"),
                                    _Movie(3, "Ran", "/mnt/b/4K/Ran (1985)/Ran.2160p.mkv")], ["/mnt/b/4K"]),
        _Sec("movie", "Home Videos", [_Movie(4, "Party", "/mnt/c/HOME/party.mkv")], ["/mnt/c/HOME"]),
        _Sec("show", "TV", [], ["/mnt/a/TV"]),
    ]


def _plex(secs, movies, tv="TV"):
    src = PlexVideoSource(_Server(secs), movies_lib=movies, tv_lib=tv)
    # heat is in both libraries: same tmdb id, two plex items, two files
    src._movie = lambda m: {"server_id": str(m.ratingKey), "title": m.title, "tmdb_id": 949 if m.title == "Heat" else 500 + m.ratingKey,
                            "file": {"relative_path": m.path, "size_bytes": 10 + m.ratingKey}}
    return src


def _wire(monkeypatch, db, server, build):
    monkeypatch.setattr(vs, "_vdb", lambda d=None: db)
    monkeypatch.setattr(vs, "_load_selection", lambda: db.get_library_selection(server))
    monkeypatch.setattr(vs, "_build_source", lambda movies_lib=None, tv_lib=None, **kw: build(movies_lib, tv_lib))


def test_plex_reads_every_picked_movie_library(db, monkeypatch):
    secs = _plex_secs()
    db.set_library_selection("plex", "Movies", "TV", movies_more=["4K Movies"])
    _wire(monkeypatch, db, "plex", lambda m, t: _plex(secs, m, t))
    src = vs.scan_video_source()
    assert {d["server_id"] for d in src.iter_movies()} == {"1", "2", "3"}       # Home Videos isn't picked
    assert src.counts() == {"movies": 3, "shows": 0}
    assert src.scan_problem("movie") is None
    # the quick scan reads both too
    since = datetime(2026, 1, 1)
    assert {d["server_id"] for d in src.iter_movies(incremental=True, since=since)} == {"1", "2", "3"}
    assert secs[1].searched and not secs[2].searched
    # the post-download refresh nudges both movie libraries
    assert src.refresh_sections("movie") == {"ok": True, "sections": 2}
    assert [s.updated for s in secs] == [1, 1, 0, 0]
    # and the getter every other lookup uses reads the same picks
    assert vs.get_active_video_source()._movies_lib == ["Movies", "4K Movies"]


def test_plex_names_a_missing_extra_movie_library(db):
    problem = _plex([_Sec("movie", "Movies")], ["Movies", "4K Movies"]).scan_problem("movie")
    assert problem and "4K Movies" in problem


def test_the_extra_library_folders_are_listed(db, monkeypatch):
    secs = _plex_secs()
    db.set_library_selection("plex", "Movies", "TV", movies_more=["4K Movies"])
    _wire(monkeypatch, db, "plex", lambda m, t: _plex(secs, m, t))
    got = vs.server_library_folders()
    assert {(f["server"], f["kind"], f["library"]) for f in got} == {
        ("/mnt/a/MOVIES", "movie", "Movies"), ("/mnt/b/4K", "movie", "4K Movies"), ("/mnt/a/TV", "episode", "TV")}
    folders = {f["server"]: f for f in drive_map.server_folders(db, fetch=vs.server_library_folders)}
    assert folders["/mnt/b/4K"]["kind"] == "movie" and folders["/mnt/b/4K"]["library"] == "4K Movies"
    assert "/mnt/c/HOME" not in folders


def test_a_film_in_both_libraries_is_two_steady_copies(db, monkeypatch):
    secs = _plex_secs()
    db.set_library_selection("plex", "Movies", "TV", movies_more=["4K Movies"])
    _wire(monkeypatch, db, "plex", lambda m, t: _plex(secs, m, t))
    scanner = VideoLibraryScanner(db)
    for mode in ("full", "full", "deep"):
        st = scanner.scan_sync(vs.scan_video_source, mode=mode, media_type="movie")
        assert st["state"] == "done", st
    conn = db._get_connection()
    try:
        rows = conn.execute("SELECT m.id, m.server_id, f.relative_path FROM movies m "
                            "JOIN media_files f ON f.movie_id = m.id WHERE m.tmdb_id = 949 ORDER BY m.server_id").fetchall()
    finally:
        conn.close()
    # one row per plex item, each keeping its own file: never swapped between scans
    assert [(r["server_id"], r["relative_path"].split("/")[2]) for r in rows] == [("1", "a"), ("2", "b")]
    # owned once, and the title's delete takes both copies
    assert 949 in db.owned_movie_tmdb_ids("plex")
    assert sorted(db.title_delete_rows("movie", rows[0]["id"])) == sorted(r["id"] for r in rows)


def test_switching_a_library_off_stops_reading_it_and_deletes_nothing(db, monkeypatch):
    secs = _plex_secs()
    db.set_library_selection("plex", "Movies", "TV", movies_more=["4K Movies"])
    _wire(monkeypatch, db, "plex", lambda m, t: _plex(secs, m, t))
    scanner = VideoLibraryScanner(db)
    assert scanner.scan_sync(vs.scan_video_source, mode="full", media_type="movie")["movies"] == 3
    db.set_library_selection("plex", "Movies", "TV", movies_more=[])
    assert vs.scan_video_source()._movies_lib == ["Movies"]
    for mode in ("full", "incremental"):
        st = scanner.scan_sync(vs.scan_video_source, mode=mode, media_type="movie")
        assert st["state"] == "done", st
        # the 4K copies stay as they were, files and all
        assert db.server_ids("movies", "plex") == {"1", "2", "3"}
    assert len(db.library_title_files("movie", next(
        r["id"] for r in db.list_movies() if r["title"] == "Ran"))) == 1


# ── jellyfin ─────────────────────────────────────────────────────────────────

VIEWS = {"Items": [{"Id": "mv", "Name": "Movies", "CollectionType": "movies"},
                   {"Id": "mv4", "Name": "4K Movies", "CollectionType": "movies"},
                   {"Id": "home", "Name": "Home Videos", "CollectionType": "movies"},
                   {"Id": "tv", "Name": "Shows", "CollectionType": "tvshows"}]}
VIRTUAL = [{"Name": "Movies", "Locations": ["/data/movies"]},
           {"Name": "4K Movies", "Locations": ["/data/movies4k"]},
           {"Name": "Home Videos", "Locations": ["/data/home"]},
           {"Name": "Shows", "Locations": ["/data/tv"]}]


class FakeJellyfin:
    def __init__(self, routes):
        self.routes, self.user_id, self.base_url, self.api_key = routes, "u1", "http://jf", "k"
        self.last_error, self.calls = None, []

    def _make_request(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        for key, value in self.routes.items():
            if path.endswith(key):
                return value(params or {}) if callable(value) else value
        return None


def _items(params):
    if params.get("IncludeItemTypes") != "Movie":
        return {"Items": [], "TotalRecordCount": 0}
    ids = {"mv": ["a"], "mv4": ["b"], "home": ["c"]}.get(params.get("ParentId"), [])
    return {"Items": [{"Id": i, "Name": "Movie " + i, "Type": "Movie"} for i in ids], "TotalRecordCount": len(ids)}


def _jf():
    return FakeJellyfin({"/Views": VIEWS, "/Items": _items, "/VirtualFolders": VIRTUAL})


def test_jellyfin_reads_every_picked_movie_library_full_and_quick(db, monkeypatch):
    jf = _jf()
    db.set_library_selection("jellyfin", "Movies", "Shows", movies_more=["4K Movies"])
    _wire(monkeypatch, db, "jellyfin", lambda m, t: JellyfinVideoSource(jf, m, t))
    src = vs.scan_video_source()
    assert src.scan_problem("movie") is None
    assert {d["server_id"] for d in src.iter_movies()} == {"a", "b"}
    assert {d["server_id"] for d in src.iter_movies(incremental=True, since=datetime(2026, 1, 1))} == {"a", "b"}
    parents = {p.get("ParentId") for path, p in jf.calls if path.endswith("/Items")}
    assert "home" not in parents
    got = {(f["server"], f["kind"]) for f in src.library_folders()}
    assert got == {("/data/movies", "movie"), ("/data/movies4k", "movie"), ("/data/tv", "episode")}


def test_jellyfin_scan_then_switch_off(db, monkeypatch):
    jf = _jf()
    db.set_library_selection("jellyfin", "Movies", "Shows", movies_more=["4K Movies"])
    _wire(monkeypatch, db, "jellyfin", lambda m, t: JellyfinVideoSource(jf, m, t))
    scanner = VideoLibraryScanner(db)
    st = scanner.scan_sync(vs.scan_video_source, mode="full", media_type="movie")
    assert (st["state"], st["movies"]) == ("done", 2), st
    db.set_library_selection("jellyfin", "Movies", "Shows", movies_more=[])
    st = scanner.scan_sync(vs.scan_video_source, mode="full", media_type="movie")
    assert st["state"] == "done", st
    assert db.server_ids("movies", "jellyfin") == {"a", "b"}


def test_source_for_server_reads_the_extra_movie_libraries(db, monkeypatch):
    db.set_library_selection("jellyfin", "Movies", "Shows", movies_more=["4K Movies"])
    seen = {}
    monkeypatch.setattr(vs, "_vdb", lambda d=None: db)
    monkeypatch.setattr(vs, "_build_source", lambda movies_lib=None, tv_lib=None, **kw: seen.update(m=movies_lib) or "src")
    assert vs.source_for_server("jellyfin") == "src"
    assert seen["m"] == ["Movies", "4K Movies"]
