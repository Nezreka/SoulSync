"""SoulSync Standalone for video (core/video/disk_source.py).

soulsync scans its own movie and TV folders, works out what each title is
(its own id, a .nfo, a folder tag, a sure TMDB match, in that order), and
feeds the same scanner and upserts the media servers do. what it isn't sure
of waits for the person, never guessed. a folder it can't reach stops the
scan before anything is pruned.
"""

from __future__ import annotations

import os

import pytest

from core.video import owned_ids
from core.video.disk_source import DiskVideoSource, ids_from_name, ids_from_nfo
from core.video.scanner import VideoLibraryScanner
from database.video_database import VideoDatabase


def _touch(root, rel, size=100, text=None):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        p.write_text(text)
    else:
        p.write_bytes(b"x" * size)
    return str(p)


def _q(db, sql, *args):
    conn = db._get_connection()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


class Search:
    """TMDB search: a fixed answer per (kind, title), and a count"""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def __call__(self, kind, query):
        self.calls.append((kind, query))
        return self.answers.get((kind, query), [])


@pytest.fixture()
def lib(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    movies, tv = tmp_path / "Movies", tmp_path / "TV"
    movies.mkdir()
    tv.mkdir()
    search = Search({
        ("movie", "The Matrix"): [{"id": 603, "title": "The Matrix", "year": 1999,
                                   "poster_url": "https://image.tmdb.org/t/p/w500/m.jpg"}],
        ("movie", "Heat"): [{"id": 949, "title": "Heat", "year": 1995},
                            {"id": 1, "title": "Heat", "year": 1972}],
        ("show", "Silo"): [{"id": 125988, "title": "Silo", "year": 2023}],
    })

    def source():
        return DiskVideoSource(db, movie_roots=[str(movies)], tv_roots=[str(tv)], search=search,
                               scanner_status=lambda: False)

    return db, movies, tv, search, source


def _scan(db, source, mode="full", media_type="all"):
    return VideoLibraryScanner(db).scan_sync(source, mode=mode, media_type=media_type)


# ── what a title is ──────────────────────────────────────────────────────────

def test_ids_from_tags_and_nfo():
    assert ids_from_name("The Matrix (1999) {tmdb-603}") == {"tmdb_id": 603}
    assert ids_from_name("Heat [imdbid-tt0113277]") == {"imdb_id": "tt0113277"}
    assert ids_from_name("Silo (2023) [tvdbid-403245]") == {"tvdb_id": 403245}
    assert ids_from_nfo('<movie><uniqueid type="tmdb" default="true">603</uniqueid>'
                        "<imdbid>tt0133093</imdbid></movie>") == {"tmdb_id": 603, "imdb_id": "tt0133093"}
    assert ids_from_nfo("https://www.themoviedb.org/movie/603-the-matrix") == {"tmdb_id": 603}
    assert ids_from_nfo("<movie><title>x</title></movie>") == {}


def test_a_folder_library_with_no_server(lib):
    db, movies, tv, search, source = lib
    m1 = _touch(movies, "The Matrix (1999)/The Matrix (1999) 1080p.mkv", 500)
    _touch(movies, "The Matrix (1999)/sample.mkv", 5)                  # junk
    _touch(movies, "The Matrix (1999)/Extras/Making Of.mkv", 50)       # extras
    _touch(movies, "Heat/Heat.mkv")                             # two Heats: waits
    _touch(movies, "Blade Runner (1982) {tmdb-78}/br.mkv")             # a tag: no search
    e1 = _touch(tv, "Silo/Season 01/Silo - S01E01 - Freedom Day.mkv")
    _touch(tv, "Silo/Season 01/Silo.S01E02E03.720p.mkv")
    _touch(tv, "Silo/Specials/Silo - S00E01.mkv")
    st = _scan(db, source)
    assert st["state"] == "done", st

    mv = {r["title"]: r for r in _q(db, "SELECT * FROM movies")}
    assert set(mv) == {"The Matrix", "Heat", "Blade Runner"}
    assert all(r["server_source"] == "soulsync" and owned_ids.is_owned(r["server_id"]) for r in mv.values())
    assert mv["The Matrix"]["tmdb_id"] == 603 and mv["The Matrix"]["year"] == 1999
    assert mv["The Matrix"]["poster_url"].startswith("https://image.tmdb.org/")
    assert mv["Blade Runner"]["tmdb_id"] == 78
    # not sure: listed, waiting for the person, never guessed
    assert mv["Heat"]["tmdb_id"] is None and mv["Heat"]["tmdb_match_status"] == "review"
    files = _q(db, "SELECT relative_path, resolution FROM media_files WHERE movie_id=?", mv["The Matrix"]["id"])
    assert files == [{"relative_path": m1, "resolution": "1080p"}]
    assert ("movie", "Blade Runner") not in search.calls

    show = _q(db, "SELECT * FROM shows")[0]
    assert show["title"] == "Silo" and show["tmdb_id"] == 125988 and show["server_source"] == "soulsync"
    eps = {(r["season_number"], r["episode_number"]): r for r in
           _q(db, "SELECT * FROM episodes WHERE show_id=?", show["id"])}
    assert set(eps) == {(1, 1), (1, 2), (1, 3), (0, 1)}
    assert all(r["has_file"] == 1 for r in eps.values())
    assert _q(db, "SELECT relative_path FROM media_files WHERE episode_id=?", eps[(1, 1)]["id"]) == \
        [{"relative_path": e1}]


def test_a_second_scan_asks_nothing_and_keeps_what_enrichment_wrote(lib):
    db, movies, tv, search, source = lib
    _touch(movies, "The Matrix (1999)/m.mkv")
    _touch(movies, "Heat/Heat.mkv")
    _touch(tv, "Silo/Season 01/Silo - S01E01.mkv")
    _scan(db, source)
    asked = len(search.calls)
    conn = db._get_connection()
    conn.execute("UPDATE movies SET title='The Matrix', overview='Neo.', "
                 "poster_url='https://image.tmdb.org/p.jpg' WHERE tmdb_id=603")
    conn.execute("UPDATE episodes SET title='Freedom Day', overview='o' WHERE season_number=1")
    conn.execute("UPDATE seasons SET title='Season One', poster_url='https://x/s.jpg'")
    conn.commit()
    conn.close()
    _scan(db, source)
    _scan(db, source, mode="deep")
    assert len(search.calls) == asked
    m = _q(db, "SELECT overview, poster_url FROM movies WHERE tmdb_id=603")[0]
    assert m == {"overview": "Neo.", "poster_url": "https://image.tmdb.org/p.jpg"}
    assert _q(db, "SELECT title, overview FROM episodes")[0] == {"title": "Freedom Day", "overview": "o"}
    assert _q(db, "SELECT title, poster_url FROM seasons")[0] == {"title": "Season One",
                                                                  "poster_url": "https://x/s.jpg"}
    assert len(_q(db, "SELECT id FROM movies")) == 2


def test_a_fix_match_stays_the_persons(lib):
    db, movies, tv, search, source = lib
    _touch(movies, "Heat/Heat.mkv")
    _scan(db, source)
    conn = db._get_connection()
    conn.execute("UPDATE movies SET tmdb_id=949, tmdb_match_status='matched' WHERE title='Heat'")
    conn.commit()
    conn.close()
    _scan(db, source)
    assert _q(db, "SELECT tmdb_id, tmdb_match_status FROM movies") == \
        [{"tmdb_id": 949, "tmdb_match_status": "matched"}]


def test_a_removed_file_goes_and_a_missing_episode_is_demoted(lib):
    db, movies, tv, search, source = lib
    m = _touch(movies, "The Matrix (1999)/m.mkv")
    _touch(movies, "Heat/Heat.mkv")
    e2 = _touch(tv, "Silo/Season 01/Silo - S01E02.mkv")
    _touch(tv, "Silo/Season 01/Silo - S01E01.mkv")
    _scan(db, source)
    conn = db._get_connection()
    conn.execute("UPDATE episodes SET air_date='2023-05-12' WHERE episode_number=2")
    conn.commit()
    conn.close()
    os.remove(m)
    os.rmdir(os.path.dirname(m))
    os.remove(e2)
    _scan(db, source, mode="deep")
    assert [r["title"] for r in _q(db, "SELECT title FROM movies")] == ["Heat"]
    ep = _q(db, "SELECT has_file, server_id FROM episodes WHERE episode_number=2")[0]
    assert ep == {"has_file": 0, "server_id": None}           # a fact, kept as missing


def test_an_unreachable_folder_stops_the_scan_before_anything_goes(lib, tmp_path):
    db, movies, tv, search, source = lib
    _touch(movies, "The Matrix (1999)/m.mkv")
    _scan(db, source)
    gone = DiskVideoSource(db, movie_roots=[str(tmp_path / "unmounted")], tv_roots=[str(tv)],
                           search=search, scanner_status=lambda: False)
    st = _scan(db, lambda: gone, mode="deep")
    assert st["state"] == "error" and "can't be reached" in st["error"]
    assert len(_q(db, "SELECT id FROM movies")) == 1


def test_no_folders_says_what_to_do(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    src = DiskVideoSource(db, movie_roots=[], tv_roots=[], scanner_status=lambda: False)
    assert "Folders" in src.scan_problem("all")


def test_a_landed_download_and_the_scan_are_one_row(lib, monkeypatch):
    db, movies, tv, search, source = lib
    import json
    from core.video import library_ingest as li
    monkeypatch.setattr(li, "library_label", lambda _db: "soulsync")
    monkeypatch.setattr("core.video.library_roots.library_roots",
                        lambda _db, kind=None: [str(movies), str(tv)])
    monkeypatch.setattr("core.video.library_manager.expire", lambda *a, **k: None)
    p = _touch(movies, "The Matrix (1999)/The Matrix (1999).mkv")
    li.ingest_landed(db, {"kind": "movie", "media_source": "tmdb", "media_id": "603", "title": "The Matrix",
                          "year": 1999, "search_ctx": json.dumps({"scope": "movie"})}, p)
    e = _touch(tv, "Silo/Season 01/Silo - S01E01.mkv")
    li.ingest_landed(db, {"kind": "show", "media_source": "tmdb", "media_id": "125988", "title": "Silo",
                          "search_ctx": json.dumps({"scope": "episode", "season": 1, "episode": 1})}, e)
    _scan(db, source)
    assert len(_q(db, "SELECT id FROM movies")) == 1
    assert len(_q(db, "SELECT id FROM shows")) == 1
    assert len(_q(db, "SELECT id FROM episodes")) == 1
    assert len(_q(db, "SELECT id FROM media_files")) == 2


def test_a_quick_scan_reads_only_what_changed_and_sees_what_dates_dont(lib):
    """the quick (incremental) scan compares each folder's listing with the
    library, not its dates: a folder moved in or renamed keeps its old dates,
    and a deleted one has none to look at."""
    db, movies, tv, search, source = lib
    import shutil
    import time
    _touch(movies, "The Matrix (1999)/m.mkv", 500)
    _touch(movies, "Blade Runner (1982) {tmdb-78}/br.mkv", 600)
    _touch(movies, "Alien (1979) {tmdb-348}/alien.mkv", 700)
    _touch(tv, "Silo {tmdb-125988}/Season 01/Silo - S01E01.mkv", 100)
    _scan(db, source)                                   # the first read is a full one
    # nothing changed: a quick scan reads no title again
    src = source()
    assert list(src.iter_movies(incremental=True)) == [] and list(src.iter_shows(incremental=True)) == []
    assert len(src.walked("movies")) == 3 and len(src.walked("shows")) == 1

    old = time.time() - 30 * 86400
    # a folder made a month ago, moved in now: its dates are a month old
    moved_in = tmp_dir = movies.parent / "elsewhere" / "Heat (1995) {tmdb-949}"
    _touch(tmp_dir.parent, "Heat (1995) {tmdb-949}/heat.mkv", 800)
    os.utime(moved_in / "heat.mkv", (old, old))
    os.utime(moved_in, (old, old))
    shutil.move(str(moved_in), str(movies / "Heat (1995) {tmdb-949}"))
    # a rename, a delete, a file replaced under the same name, an episode two folders down
    os.rename(movies / "Blade Runner (1982) {tmdb-78}", movies / "Blade Runner - Final Cut {tmdb-78}")
    shutil.rmtree(movies / "Alien (1979) {tmdb-348}")
    (movies / "The Matrix (1999)" / "m.mkv").write_bytes(b"x" * 999)
    _touch(tv, "Silo {tmdb-125988}/Season 01/Disc 1/Silo - S01E02.mkv", 100)
    br_id = _q(db, "SELECT id FROM movies WHERE tmdb_id=78")[0]["id"]

    st = _scan(db, source, mode="incremental")
    assert st["state"] == "done"
    got = {r["tmdb_id"]: r for r in _q(db, "SELECT * FROM movies")}
    assert set(got) == {603, 78, 949}                    # Heat in, Alien gone
    assert got[78]["id"] == br_id                         # the rename kept its row
    assert _q(db, "SELECT size_bytes FROM media_files WHERE movie_id=?", got[603]["id"]) == [{"size_bytes": 999}]
    assert len(_q(db, "SELECT id FROM episodes WHERE has_file=1")) == 2
    assert st["removed"] == 1


def test_a_quick_scan_never_drops_a_title_whose_folder_it_couldnt_read(lib, monkeypatch):
    db, movies, tv, search, source = lib
    _touch(movies, "The Matrix (1999)/m.mkv")
    _touch(movies, "Blade Runner (1982) {tmdb-78}/br.mkv")
    _scan(db, source)
    # one title blows up mid-read: it isn't "seen", but its file is on disk
    real = DiskVideoSource._movie

    def boom(self, folder, name, videos, nfos):
        if "Blade" in name:
            raise OSError("share hiccup")
        return real(self, folder, name, videos, nfos)

    monkeypatch.setattr(DiskVideoSource, "_movie", boom)
    (movies / "Blade Runner (1982) {tmdb-78}" / "br.mkv").write_bytes(b"x" * 321)   # so it is read
    _scan(db, source, mode="incremental")
    assert len(_q(db, "SELECT id FROM movies")) == 2


def test_a_scheduled_scan_of_soulsyncs_folders_actually_runs(lib):
    # asking a media server to index first is a no-op here. it used to start a
    # scan of its own, so the scan that followed found one running and skipped
    db, movies, tv, search, source = lib
    _touch(movies, "The Matrix (1999)/m.mkv")
    src = source()
    assert src.refresh_sections("all")["ok"] is True
    assert _q(db, "SELECT id FROM movies") == []           # nothing was started behind our back
    from core.automation.handlers import video_scan_library as handler

    class Deps:
        def update_progress(self, *a, **k):
            pass

    ran = []

    def run(mode, media_type):
        ran.append(mode)
        return VideoLibraryScanner(db).scan_sync(source, mode=mode, media_type=media_type)

    out = handler.auto_video_scan_library({"mode": "deep"}, Deps(), server_refresh=src.refresh_sections,
                                          run_video_scan=run)
    assert ran == ["deep"] and out["status"] == "completed" and out["movies"] == 1


def test_another_servers_library_doesnt_make_this_one_look_scanned(tmp_path):
    # 60 soulsync rows, then plex becomes the video server: plex's first quick
    # scan must read everything, not stop early as if the library were full
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    for i in range(60):
        db.upsert_movie("soulsync", {"server_id": f"ss:{i}", "title": f"m{i}"})

    class Plex:
        server_name = "plex"

        def __init__(self):
            self.asked = []

        def counts(self, incremental=False):
            return {}

        def iter_movies(self, incremental=False, since=None):
            self.asked.append(incremental)
            return iter([])

        def iter_shows(self, incremental=False, since=None):
            return iter([])

    plex = Plex()
    VideoLibraryScanner(db).scan_sync(lambda: plex, mode="incremental")
    assert plex.asked == [False]


def test_switching_the_video_source_forgets_the_last_scan_mark(tmp_path):
    from flask import Flask
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    db.set_setting("video_last_scan_at", "1795000000")
    videoapi._video_db = db
    try:
        app = Flask(__name__)
        app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
        c = app.test_client()
        assert c.post("/api/video/server", json={"server": "soulsync"}).status_code == 200
        assert not db.get_setting("video_last_scan_at")
        # the same pick again leaves a new mark alone
        db.set_setting("video_last_scan_at", "1795000001")
        c.post("/api/video/server", json={"server": "soulsync"})
        assert db.get_setting("video_last_scan_at") == "1795000001"
    finally:
        videoapi._video_db = None


def test_page_sync_reads_one_title_again(lib):
    db, movies, tv, search, source = lib
    _touch(movies, "The Matrix (1999)/m.mkv")
    _touch(tv, "Silo/Season 01/Silo - S01E01.mkv")
    _scan(db, source)
    m = _q(db, "SELECT server_id FROM movies")[0]["server_id"]
    s = _q(db, "SELECT server_id FROM shows")[0]["server_id"]
    src = source()
    assert src.movie_item(m)["tmdb_id"] == 603
    _touch(tv, "Silo/Season 01/Silo - S01E02.mkv")
    assert len(src.show_tree(s)["seasons"][0]["episodes"]) == 2
    assert src.movie_item("ss:nothing") is None


# ── soulsync as the video source ────────────────────────────────────────────

def _no_servers(monkeypatch):
    import core.settings as cs

    class CM:
        def get_plex_config(self):
            return {}

        def get_jellyfin_config(self):
            return {}

        def get_active_media_server(self):
            return "navidrome"
    monkeypatch.setattr(cs, "config_manager", CM())


def test_no_server_and_folders_set_is_soulsyncs_own_library(tmp_path, monkeypatch):
    from core.video import sources
    _no_servers(monkeypatch)
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    assert sources.resolve_video_server(db) is None            # nothing to scan yet
    db.set_setting("movies_path", str(tmp_path / "Movies"))
    assert sources.resolve_video_server(db) == "soulsync"
    monkeypatch.setattr(sources, "_vdb", lambda d=None: db)
    src = sources._build_source()
    assert isinstance(src, DiskVideoSource) and src.server_name == "soulsync"
    assert src.movie_roots == [str(tmp_path / "Movies")]


def test_a_library_a_server_built_is_never_switched_by_itself(tmp_path, monkeypatch):
    # the server's connection is gone for now (a wiped config, a failed
    # migration): its rows must not vanish behind a 'soulsync' filter
    from core.video import sources
    _no_servers(monkeypatch)
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    db.set_setting("movies_path", str(tmp_path / "Movies"))
    db.upsert_movie("plex", {"server_id": "1", "title": "The Matrix"})
    assert sources.resolve_video_server(db) is None
    # the person can still choose it
    db.set_setting("video_server", "soulsync")
    assert sources.resolve_video_server(db) == "soulsync"


def test_the_pick_is_saved_by_the_server_route(tmp_path):
    from flask import Flask
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    videoapi._video_db = db
    try:
        app = Flask(__name__)
        app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
        c = app.test_client()
        assert c.post("/api/video/server", json={"server": "soulsync"}).status_code == 200
        assert db.get_setting("video_server") == "soulsync"
        assert c.post("/api/video/server", json={"server": "emby"}).status_code == 400
    finally:
        videoapi._video_db = None
