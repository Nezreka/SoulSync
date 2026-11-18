"""The video import folder (#2129).

The music side has an Import folder: drop files in, SoulSync works out what they
are and files them into the library. The video side had nothing like it, only a
list of downloads the auto-filer couldn't place. These pin the new folder end to
end: reading the folder, working out what each entry is, picking the TMDB title
and how sure that is, and importing through the SAME importer a finished download
uses (real files, a real video db, the default naming template).
"""

from __future__ import annotations

import json
import os
import time

import pytest
from flask import Flask

import api.video as videoapi
from core.video import download_monitor as mon
from core.video import import_folder as imp
from core.video import organization, season_pack
from database.video_database import VideoDatabase

_OLD = time.time() - 3600    # settled long ago


def _search(table):
    """A fake TMDB search: {(kind, title): [results]}."""
    calls = []

    def search(kind, query):
        calls.append((kind, query))
        return table.get((kind, query), [])
    search.calls = calls
    return search


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    imp._match_cache.clear()
    imp._inflight.clear()
    # tiny test files stand in for episodes; the 32 MB floor is the real one's job
    monkeypatch.setattr(season_pack, "MIN_EPISODE_BYTES", 0)
    # never ask the real TMDB for naming ids
    monkeypatch.setattr(mon, "template_ids", lambda db, dl, settings, **k: {
        "tmdb_id": int(dl["media_id"]) if dl.get("media_id") else None,
        "imdb_id": None, "tvdb_id": None, "year": dl.get("year")})
    yield
    imp._match_cache.clear()


# ── reading the folder ───────────────────────────────────────────────────────

def _fake_fs(tree, mtimes=None):
    """tree: {name: size} for a loose file, {name: {file: size}} for a folder."""
    mtimes = mtimes or {}
    root = "/in"
    paths = {}
    for name, v in tree.items():
        if isinstance(v, dict):
            for f, size in v.items():
                paths[root + "/" + name + "/" + f] = size
        else:
            paths[root + "/" + name] = v
    return dict(
        lister=lambda r: list(tree),
        isdir=lambda p: isinstance(tree.get(os.path.basename(p)), dict),
        walker=lambda p: [k for k in paths if k.startswith(p + "/")],
        stat=lambda p: (paths[p], mtimes.get(p, _OLD)),
    )


def test_scan_lists_each_entry_with_its_videos():
    fs = _fake_fs({
        "The.Matrix.1999.1080p.BluRay.x264.mkv": 5000,
        "Severance (2022)": {"Severance.S01E01.mkv": 900, "Severance.S01E02.mkv": 901, "info.nfo": 2},
        "notes.txt": 3,
        "Empty Folder": {"cover.jpg": 10},
        ".hidden.mkv": 50,
    })
    out = imp.scan("/in", now=time.time(), **fs)
    names = [e["name"] for e in out]
    assert names == ["Severance (2022)", "The.Matrix.1999.1080p.BluRay.x264.mkv"]
    show = out[0]
    assert show["is_dir"] and len(show["files"]) == 2 and show["size"] == 1801
    assert all(e["settled"] for e in out)


def test_scan_holds_back_an_entry_that_is_still_copying_in():
    fs = _fake_fs({"Dune (2021)": {"dune.mkv": 100, "dune2.mkv.part": 5},
                   "Fresh.Movie.2024.mkv": 100},
                  mtimes={"/in/Fresh.Movie.2024.mkv": time.time()})
    out = {e["name"]: e for e in imp.scan("/in", now=time.time(), **fs)}
    assert out["Dune (2021)"]["partial"] and not out["Dune (2021)"]["settled"]
    assert not out["Fresh.Movie.2024.mkv"]["settled"]      # written seconds ago


# ── working out what an entry is ─────────────────────────────────────────────

def _entry(name, files=None, is_dir=None):
    is_dir = bool(files) if is_dir is None else is_dir
    path = "/in/" + name
    fl = ([{"path": path + "/" + f, "size_bytes": s} for f, s in files.items()]
          if files else [{"path": path, "size_bytes": 4096}])
    return {"key": name, "name": name, "path": path, "is_dir": is_dir, "files": fl,
            "size": sum(f["size_bytes"] for f in fl), "newest": _OLD,
            "settled": True, "partial": False}


def test_a_loose_movie_file():
    g = imp.guess(_entry("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv"))
    assert (g["kind"], g["scope"], g["title"], g["year"]) == ("movie", "movie", "The Matrix", 1999)


def test_a_loose_episode_file():
    g = imp.guess(_entry("Silo.S02E03.1080p.WEB-DL.mkv"))
    assert (g["kind"], g["scope"], g["title"]) == ("show", "episode", "Silo")
    assert (g["season"], g["episode"]) == (2, 3)


def test_a_show_folder_is_the_whole_series():
    g = imp.guess(_entry("Severance (2022)", {"Severance.S01E01.mkv": 900, "Severance.S01E02.mkv": 900,
                                              "Severance.S02E01.mkv": 900}))
    assert (g["kind"], g["scope"], g["title"], g["year"]) == ("show", "series", "Severance", 2022)
    assert g["episodes"] == [[1, 1], [1, 2], [2, 1]]


def test_a_movie_folder_takes_the_feature_not_the_sample():
    g = imp.guess(_entry("Dune (2021)", {"dune.mkv": 9000, "dune-sample.mkv": 100}))
    assert (g["kind"], g["title"], g["year"]) == ("movie", "Dune", 2021)
    assert g["main"].endswith("/dune.mkv")


def test_a_folder_named_for_its_season_takes_the_title_from_the_files():
    g = imp.guess(_entry("Season 1", {"The.Bear.S01E01.mkv": 900}))
    assert (g["kind"], g["title"]) == ("show", "The Bear")


# ── picking the title ────────────────────────────────────────────────────────

def test_title_and_year_agreeing_is_sure():
    s = _search({("movie", "The Matrix"): [
        {"id": 1, "title": "The Matrix Resurrections", "year": 2021},
        {"id": 603, "title": "The Matrix", "year": 1999, "poster_url": "p"}]})
    m = imp.match({"kind": "movie", "title": "The Matrix", "year": 1999}, s)
    assert (m["tmdb_id"], m["confidence"]) == (603, "high")
    assert [a["tmdb_id"] for a in m["alternatives"]] == [1]


def test_a_year_that_disagrees_is_only_a_guess():
    s = _search({("movie", "Dune"): [{"id": 841, "title": "Dune", "year": 1984}]})
    m = imp.match({"kind": "movie", "title": "Dune", "year": 2021}, s)
    assert (m["tmdb_id"], m["confidence"]) == (841, "low")


def test_no_year_and_two_titles_with_that_name_needs_a_look():
    s = _search({("show", "The Office"): [{"id": 2316, "title": "The Office", "year": 2005},
                                          {"id": 2996, "title": "The Office", "year": 2001}]})
    m = imp.match({"kind": "show", "title": "The Office", "year": None}, s)
    assert m["confidence"] == "low" and m["tmdb_id"] == 2316


def test_no_year_and_one_title_with_that_name_is_sure():
    s = _search({("show", "Silo"): [{"id": 125988, "title": "Silo", "year": 2023},
                                    {"id": 9, "title": "Silos", "year": 2010}]})
    assert imp.match({"kind": "show", "title": "Silo", "year": None}, s)["confidence"] == "high"


def test_nothing_found_or_a_failed_search_is_no_match():
    assert imp.match({"kind": "movie", "title": "Zzz", "year": None}, _search({}))["confidence"] == "none"

    def boom(kind, q):
        raise RuntimeError("429")
    assert imp.match({"kind": "movie", "title": "Zzz", "year": None}, boom)["confidence"] == "none"


# ── importing, end to end on real files ──────────────────────────────────────

@pytest.fixture()
def env(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    movies, tv, inbox = tmp_path / "Movies", tmp_path / "TV", tmp_path / "Import"
    for d in (movies, tv, inbox):
        d.mkdir()
    db.set_setting("movies_path", str(movies))
    db.set_setting("tv_path", str(tv))
    organization.save(db, {"verify_with_ffprobe": False, "save_artwork": False,
                           "write_nfo": False, "download_subtitles": False})
    imp.save_settings(db, {"path": str(inbox), "auto": False})
    search = _search({
        ("movie", "The Matrix"): [{"id": 603, "title": "The Matrix", "year": 1999}],
        ("show", "Severance"): [{"id": 95396, "title": "Severance", "year": 2022}],
        ("movie", "Mystery Film"): [{"id": 7, "title": "Mystery Film", "year": 1990},
                                    {"id": 8, "title": "Mystery Film", "year": 2010}],
    })
    target_for = (lambda db_, kind, body: str(movies if kind == "movie" else tv))

    def old(path):
        os.utime(path, (_OLD, _OLD))

    return {"db": db, "movies": movies, "tv": tv, "inbox": inbox, "search": search,
            "seams": {"target_for": target_for}, "old": old}


def _put(path, data=b"x" * 4096, old=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if old:
        old(path)
    return path


def _entry_for(env, name):
    return next(e for e in imp.scan(str(env["inbox"])) if e["name"] == name)


def test_a_movie_dropped_in_lands_in_the_library_like_a_download(env):
    """#2129: the loose file goes through the download importer: renamed by the
    template into Movies, the inbox copy gone, a completed row in history."""
    _put(env["inbox"] / "The.Matrix.1999.1080p.BluRay.x264.mkv", old=env["old"])
    e = _entry_for(env, "The.Matrix.1999.1080p.BluRay.x264.mkv")
    r = imp.run_one(env["db"], e, None, search=env["search"], **env["seams"])
    assert r["status"] == "completed", r
    final = env["movies"] / "The Matrix (1999)" / "The Matrix (1999) Bluray-1080p.mkv"
    assert final.exists()
    assert not (env["inbox"] / "The.Matrix.1999.1080p.BluRay.x264.mkv").exists()
    row = env["db"].get_video_download(r["id"])
    assert row["status"] == "completed" and row["source"] == "import"
    assert row["media_id"] == "603" and row["media_source"] == "tmdb"
    assert imp.listing(env["db"], search=env["search"])["items"] == []


def test_a_show_folder_imports_every_episode_through_the_pack_importer(env):
    folder = env["inbox"] / "Severance (2022)"
    for ep in ("S01E01", "S01E02"):
        _put(folder / ("Severance.%s.1080p.WEB-DL.mkv" % ep), old=env["old"])
    _put(folder / "Severance.S01E01.1080p.WEB-DL.en.srt")
    e = _entry_for(env, "Severance (2022)")
    r = imp.run_one(env["db"], e, None, search=env["search"], **env["seams"])
    assert r["status"] == "completed" and r["imported"] == 2, r
    season = env["tv"] / "Severance" / "Season 01"
    got = sorted(p.name for p in season.iterdir())
    assert any("S01E01" in n and n.endswith(".mkv") for n in got)
    assert any("S01E02" in n and n.endswith(".mkv") for n in got)
    assert any(n.endswith(".en.srt") for n in got)            # subtitles carried
    # each episode has its own completed row, like a season pack download
    rows = [d for d in env["db"].list_video_downloads(50) if d["source"] == "import"]
    eps = [json.loads(d["search_ctx"]).get("episode") for d in rows if d["status"] == "completed"]
    assert sorted(x for x in eps if x) == [1, 2]


def test_a_refused_file_goes_under_needs_a_hand_and_is_not_retried(env):
    """a file the importer won't take (here a sample) is parked like a refused
    download, and the automatic pass doesn't keep trying it."""
    name = "The.Matrix.1999.1080p.BluRay.x264-sample.mkv"
    _put(env["inbox"] / name, old=env["old"])
    r = imp.run_one(env["db"], _entry_for(env, name), None, search=env["search"], **env["seams"])
    assert r["status"] == "import_failed"
    assert (env["inbox"] / name).exists()                     # left where it was
    failed = env["db"].get_import_failed_video_downloads()
    assert failed and failed[0]["dest_path"] == str(env["inbox"] / name)
    assert imp.listing(env["db"], search=env["search"])["items"] == []     # not listed twice
    # dismissed from Needs a hand: it shows in the folder again, marked as tried
    env["db"].update_video_download(failed[0]["id"], status="cancelled")
    items = imp.listing(env["db"], search=env["search"])["items"]
    assert len(items) == 1 and items[0]["tried"]
    imp.save_settings(env["db"], {"auto": True})
    before = len(env["db"].list_video_downloads(50))
    assert imp.auto_pass(env["db"], search=env["search"], **env["seams"]) == 0
    assert len(env["db"].list_video_downloads(50)) == before      # not even tried again


def test_the_automatic_pass_imports_only_sure_settled_matches(env):
    _put(env["inbox"] / "The.Matrix.1999.1080p.BluRay.mkv", old=env["old"])
    _put(env["inbox"] / "Mystery.Film.1080p.mkv", old=env["old"])        # two titles share it
    _put(env["inbox"] / "Severance.S01E05.1080p.WEB.mkv")                # copied in just now
    imp.save_settings(env["db"], {"auto": False})
    assert imp.auto_pass(env["db"], search=env["search"], **env["seams"]) == 0   # off
    imp.save_settings(env["db"], {"auto": True})
    assert imp.auto_pass(env["db"], search=env["search"], **env["seams"]) == 1
    left = sorted(p.name for p in env["inbox"].iterdir())
    assert left == ["Mystery.Film.1080p.mkv", "Severance.S01E05.1080p.WEB.mkv"]


def test_a_picked_title_overrides_the_match(env):
    name = "Mystery.Film.1080p.mkv"
    _put(env["inbox"] / name, old=env["old"])
    r = imp.run_one(env["db"], _entry_for(env, name),
                    {"tmdb_id": 8, "kind": "movie", "title": "Mystery Film", "year": 2010},
                    search=env["search"], **env["seams"])
    assert r["status"] == "completed", r
    assert (env["movies"] / "Mystery Film (2010)").is_dir()


def test_an_episode_needs_its_numbers_before_it_can_go(env):
    _put(env["inbox"] / "home.video.mkv", old=env["old"])
    e = _entry_for(env, "home.video.mkv")
    r = imp.run_one(env["db"], e, {"tmdb_id": 95396, "kind": "show", "title": "Severance"},
                    search=env["search"], **env["seams"])
    assert r["status"] == "refused" and "season" in r["error"].lower()


def test_the_download_monitor_leaves_import_rows_alone(monkeypatch):
    """an 'importing' import row has no transfer; the monitor's slskd path would
    count it missing and fail it under the import thread."""
    seen = []
    monkeypatch.setattr(mon, "process_download", lambda dl, *a, **k: seen.append(dl) or None)
    monkeypatch.setattr(mon, "list_downloads", lambda: [])

    class _DB:
        def get_active_video_downloads(self):
            return [{"id": 1, "status": "importing", "source": "import", "kind": "movie"}]
    assert mon._tick(_DB()) is True
    assert seen == []


def test_a_restart_mid_import_closes_the_row(env):
    db = env["db"]
    stuck = db.add_video_download({"kind": "movie", "title": "x", "source": "import",
                                   "status": "importing"})
    other = db.add_video_download({"kind": "movie", "title": "y", "source": "torrent",
                                   "status": "downloading"})
    assert imp.recover_interrupted(db) == 1
    assert db.get_video_download(stuck)["status"] == "failed"
    assert db.get_video_download(other)["status"] == "downloading"


# ── the routes ───────────────────────────────────────────────────────────────

@pytest.fixture()
def client(env, monkeypatch):
    monkeypatch.setattr(imp, "_default_search", env["search"])
    monkeypatch.setattr(imp, "_default_target_for", env["seams"]["target_for"])
    monkeypatch.setattr(imp, "_spawn", lambda fn: fn())          # run inline
    videoapi._video_db = env["db"]
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client()
    finally:
        videoapi._video_db = None


def test_the_page_lists_what_it_found_and_imports_on_approval(env, client):
    _put(env["inbox"] / "Mystery.Film.1080p.mkv", old=env["old"])
    d = client.get("/api/video/import/folder").get_json()
    assert d["path"] == str(env["inbox"]) and len(d["items"]) == 1
    it = d["items"][0]
    assert it["match"]["confidence"] == "low" and it["guess"]["kind"] == "movie"
    r = client.post("/api/video/import/folder/import",
                    json={"key": it["key"], "tmdb_id": 7, "kind": "movie",
                          "title": "Mystery Film", "year": 1990}).get_json()
    assert r["success"], r
    assert (env["movies"] / "Mystery Film (1990)").is_dir()
    assert client.get("/api/video/import/folder").get_json()["items"] == []


def test_the_settings_round_trip_and_a_bad_key_is_refused(env, client):
    s = client.post("/api/video/import/folder/settings", json={"auto": True}).get_json()
    assert s["auto"] is True and s["path"] == str(env["inbox"])
    assert client.get("/api/video/import/folder/settings").get_json()["auto"] is True
    assert client.post("/api/video/import/folder/import", json={"key": "../etc"}).status_code == 404
