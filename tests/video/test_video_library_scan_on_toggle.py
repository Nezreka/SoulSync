"""another movie or tv library switched on is read at once, just that one
(core/video/library_scan.py), the way home videos' "Add it" is: no waiting
for the next whole scan. the rest of the library is never read, pruned or
reset on the way, and switching one off deletes nothing. a finished scan
makes your library's folders ask again, and a new folder that follows the
saved folders' pattern is offered where it likely is, checked first."""

from __future__ import annotations

import threading
import time

import pytest
from flask import Flask

import core.video.sources as vs
from core.video import drive_map, library_scan
from core.video.scanner import VideoLibraryScanner
from core.video.sources import PlexVideoSource
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


@pytest.fixture(autouse=True)
def _fresh():
    library_scan.reset()
    drive_map.forget()
    drive_map.forget_server_folders()
    yield
    library_scan.reset()
    drive_map.forget()
    drive_map.forget_server_folders()


# ── a fake plex ──────────────────────────────────────────────────────────────

class _Movie:
    def __init__(self, key, title, path):
        self.ratingKey, self.title, self.path = key, title, path


class _Sec:
    def __init__(self, kind, title, items=(), locations=()):
        self.type, self.title, self._items = kind, title, list(items)
        self.locations, self.read = list(locations), 0
        self.totalSize = len(self._items)

    def all(self):
        self.read += 1
        return list(self._items)

    def search(self, **kw):
        self.read += 1
        return list(self._items)


class _Server:
    def __init__(self, secs):
        self.library = type("L", (), {"sections": lambda _self: list(secs)})()


def _secs():
    return [
        _Sec("movie", "Movies", [_Movie(1, "Heat", "/mnt/sdb/PLEX/MOVIES/Heat (1995)/Heat.mkv")],
             ["/mnt/sdb/PLEX/MOVIES"]),
        _Sec("movie", "4K Movies", [_Movie(2, "Heat", "/mnt/easystore3/PLEX/4K MOVIES/Heat (1995)/Heat.2160p.mkv"),
                                    _Movie(3, "Ran", "/mnt/easystore3/PLEX/4K MOVIES/Ran (1985)/Ran.2160p.mkv")],
             ["/mnt/easystore3/PLEX/4K MOVIES"]),
        _Sec("show", "TV", [], ["/mnt/sdb/PLEX/TV"]),
        _Sec("show", "Anime", [], ["/mnt/sdc/ANIME"]),
    ]


def _plex(secs, movies=None, tv=None):
    src = PlexVideoSource(_Server(secs), movies_lib=movies, tv_lib=tv)
    src._movie = lambda m: {"server_id": str(m.ratingKey), "title": m.title,
                            "tmdb_id": 949 if m.title == "Heat" else 500 + m.ratingKey,
                            "file": {"relative_path": m.path, "size_bytes": 10 + m.ratingKey}}
    return src


def _build(secs):
    return lambda kind, title: _plex(secs, [title] if kind == "movie" else None,
                                     [title] if kind == "show" else None)


def _wait(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


# ── one library read on its own ──────────────────────────────────────────────

def test_one_library_is_read_and_nothing_else_is_touched(db):
    secs = _secs()
    # the main library was read before, plus a title the server no longer lists
    db.upsert_movie("plex", {"server_id": "1", "title": "Heat", "tmdb_id": 949,
                             "file": {"relative_path": "/mnt/sdb/PLEX/MOVIES/Heat (1995)/Heat.mkv"}})
    db.upsert_movie("plex", {"server_id": "9", "title": "Gone", "tmdb_id": 9,
                             "file": {"relative_path": "/mnt/sdb/PLEX/MOVIES/Gone/Gone.mkv"}})
    scanner = VideoLibraryScanner(db)
    got = library_scan.read_one(db, "movie", "4K Movies", build=_build(secs), scanner=scanner)
    assert (got["state"], got["movies"], got["library"]) == ("done", 2, "4K Movies"), got
    # only the 4K library was asked
    assert [s.read for s in secs] == [0, 1, 0, 0]
    # its titles are in; the others stay, the one no longer listed too (no prune)
    assert db.server_ids("movies", "plex") == {"1", "2", "3", "9"}
    st = library_scan.status()
    assert st == {} or st.get("movie|4K Movies", {}).get("state") in (None, "done")
    # the whole-scan baseline is left for a whole scan
    assert not db.get_setting("video_last_scan_at")


def test_a_one_library_scan_keeps_enrichment_a_full_scan_would_reset(db, monkeypatch):
    secs = _secs()
    calls = []
    real = db.upsert_movie
    monkeypatch.setattr(db, "upsert_movie",
                        lambda server, item, preserve_enrichment=True: calls.append(preserve_enrichment)
                        or real(server, item, preserve_enrichment=preserve_enrichment))
    VideoLibraryScanner(db).scan_library_sync(lambda: _build(secs)("movie", "4K Movies"), "movie", "4K Movies")
    assert calls == [True, True]


def test_a_tv_library_switched_on_reads_only_shows(db):
    secs = _secs()
    secs[3]._items = []
    got = library_scan.read_one(db, "show", "Anime", build=_build(secs), scanner=VideoLibraryScanner(db))
    assert got["state"] == "done", got
    assert [s.read for s in secs] == [0, 0, 0, 1]


def test_a_one_library_scan_refuses_both_kinds_and_standalone(db):
    sc = VideoLibraryScanner(db)
    with pytest.raises(ValueError):
        sc.scan_library_sync(lambda: None, "all", "x")
    disk = type("D", (), {"server_name": "soulsync"})()
    assert sc.scan_library_sync(lambda: disk, "movie", "x")["state"] == "error"


def test_it_waits_for_a_scan_already_running(db):
    secs = _secs()
    scanner = VideoLibraryScanner(db)
    scanner._status = {"state": "scanning"}
    seen = []

    def finish():
        time.sleep(0.2)
        seen.append(library_scan.status().get("movie|4K Movies", {}).get("state"))
        scanner._set(state="done")
    threading.Thread(target=finish, daemon=True).start()
    library_scan._state["movie|4K Movies"] = {"state": "queued"}
    got = library_scan.read_one(db, "movie", "4K Movies", build=_build(secs), scanner=scanner,
                                wait_step=0.05)
    assert got["state"] == "done" and seen == ["queued"]
    assert library_scan.status()["movie|4K Movies"]["state"] == "done"
    assert library_scan.status()["movie|4K Movies"]["found"] == 2


def test_start_runs_in_the_background_and_a_library_switched_off_is_dropped(db):
    secs = _secs()
    scanner = VideoLibraryScanner(db)
    gate = threading.Event()
    build = _build(secs)

    def slow(kind, title):
        gate.wait(5)
        return build(kind, title)
    assert library_scan.start(db, [("movie", "4K Movies"), ("show", "Anime")], build=slow, scanner=scanner)
    # never blocks the caller: the read is still waiting on the gate
    assert library_scan.status()["movie|4K Movies"]["state"] in ("queued", "running")
    # Anime switched off before it was read: dropped, nothing else changes
    assert not library_scan.start(db, [], keep=[("movie", "Movies"), ("movie", "4K Movies")],
                                  build=slow, scanner=scanner)
    assert "show|Anime" not in library_scan.status()
    gate.set()
    assert _wait(lambda: library_scan.status()["movie|4K Movies"]["state"] == "done")
    assert library_scan.status()["movie|4K Movies"]["found"] == 2
    assert _wait(lambda: library_scan._worker is None)
    assert secs[3].read == 0


def test_added_names_only_the_new_libraries():
    before = {"movies": "Movies", "movies_more": [], "tv": "TV", "tv_more": []}
    after = {"movies": "Movies", "movies_more": ["4K Movies"], "tv": "TV", "tv_more": ["Anime"]}
    assert library_scan.added(before, after) == [("movie", "4K Movies"), ("show", "Anime")]
    assert library_scan.added(after, before) == []
    assert library_scan.added(after, after) == []


# ── the endpoint ─────────────────────────────────────────────────────────────

@pytest.fixture()
def api(tmp_path, monkeypatch):
    import api.video as videoapi
    vdb = VideoDatabase(database_path=str(tmp_path / "video_api.db"))
    videoapi._video_db = vdb
    monkeypatch.setattr(vs, "resolve_video_server", lambda *a, **k: "plex")
    monkeypatch.setattr(vs, "video_plex_config", lambda db=None: {"base_url": "http://plex", "token": "t"})
    started = []
    import api.video.libraries as libraries_api
    monkeypatch.setattr(libraries_api, "_asked", set())
    monkeypatch.setattr(library_scan, "start", lambda db, libs, keep=None, **kw: started.append(
        (list(libs), sorted(keep or []))) or bool(libs))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client(), vdb, started
    finally:
        videoapi._video_db = None


def test_switching_a_library_on_starts_its_scan_and_off_deletes_nothing(api):
    client, vdb, started = api
    vdb.set_library_selection("plex", "Movies", "TV", movies_more=[], tv_more=[])
    vdb.upsert_movie("plex", {"server_id": "2", "title": "Heat", "file": {"relative_path": "/mnt/b/Heat.mkv"}})
    r = client.post("/api/video/libraries", json={"movies": "Movies", "movies_more": ["4K Movies"],
                                                   "tv": "TV", "tv_more": ["Anime"], "anime": ["Anime"]})
    body = r.get_json()
    assert r.status_code == 200 and body["started"] == ["4K Movies", "Anime"], body
    assert started[-1][0] == [("movie", "4K Movies"), ("show", "Anime")]
    # switched off: nothing new to read, and the queue is told what is still on
    r = client.post("/api/video/libraries", json={"movies": "Movies", "movies_more": [], "tv": "TV",
                                                   "tv_more": ["Anime"], "anime": ["Anime"]})
    assert r.get_json()["started"] == []
    assert started[-1] == ([], [("movie", "Movies"), ("show", "Anime"), ("show", "TV")])
    assert vdb.server_ids("movies", "plex") == {"2"}
    # the rows' progress is asked here
    assert client.get("/api/video/libraries/scan").get_json() == {"scans": {}}


def test_no_server_connection_starts_nothing(api, monkeypatch):
    client, vdb, started = api
    monkeypatch.setattr(vs, "video_plex_config", lambda db=None: {})
    r = client.post("/api/video/libraries", json={"movies": "Movies", "movies_more": ["4K Movies"], "tv": "TV"})
    assert r.status_code == 200 and r.get_json()["started"] == []
    assert started == []


# ── the folders list after a scan ────────────────────────────────────────────

def test_a_finished_scan_clears_the_folder_caches(db):
    secs = _secs()
    drive_map._folders_cache.update(at=1.0, folders=[{"server": "/old"}])
    drive_map._counts_cache[("x", ())] = (time.monotonic(), {}, {})
    drive_map._suggest_cache[("x", "y", "z")] = (time.monotonic(), {"ok": False})
    VideoLibraryScanner(db).scan_library_sync(lambda: _build(secs)("movie", "4K Movies"), "movie", "4K Movies")
    assert drive_map._folders_cache["at"] == 0.0 and drive_map._folders_cache["folders"] is None
    assert not drive_map._counts_cache and not drive_map._suggest_cache


def test_library_sizes_come_with_the_list():
    libs = _plex(_secs()).available_libraries()
    assert {x["title"]: x["count"] for x in libs["movies"]} == {"Movies": 1, "4K Movies": 2}
    assert {x["title"]: x["count"] for x in libs["tv"]} == {"TV": 0, "Anime": 0}


# ── where a new folder likely is ─────────────────────────────────────────────

SAVED = [{"server": "/mnt/sdb/PLEX/MOVIES", "local": "\\\\192.168.86.36\\sdb\\PLEX\\MOVIES"},
         {"server": "/mnt/sdc/PLEX/TV", "local": "\\\\192.168.86.36\\sdc\\PLEX\\TV"}]
NEW = "/mnt/easystore3/PLEX/4K MOVIES"
WANT = "\\\\192.168.86.36\\easystore3\\PLEX\\4K MOVIES"


def _files(db, root, n=3):
    for i in range(n):
        db.upsert_movie("plex", {"server_id": f"{root}{i}", "title": f"T{i}",
                                 "file": {"relative_path": f"{root}/T{i} (2000)/T{i}.mkv"}})


def test_the_saved_folders_pattern_is_read():
    rules = drive_map.prefix_rules(SAVED + [{"server": "/data/tv", "local": "/media/nas/data/tv"},
                                            {"server": "/mnt/x/MOVIES", "local": "E:\\Films"}])
    # the share rule two folders agree on comes first; a folder with nothing
    # in common makes none
    assert rules[0] == ("/mnt", "\\\\192.168.86.36", "\\", 2)
    assert ("/", "/media/nas", "/", 1) not in rules        # never the bare root
    assert any(r[:3] == ("/data", "/media/nas/data", "/") for r in rules)
    assert all(r[1] != "E:" for r in rules)


def test_a_new_share_folder_is_suggested_when_its_files_are_there(db):
    _files(db, NEW)
    looked = []
    hint = drive_map.suggest(db, NEW, drive_map.normalize(SAVED),
                             exists=lambda p: looked.append(p) or True, isdir=lambda p: p == WANT)
    assert hint and hint["local"] == WANT
    assert looked[0] == WANT + "\\T0 (2000)\\T0.mkv"


def test_no_suggestion_when_the_check_fails(db):
    _files(db, NEW)
    # the folder is there but the files aren't
    assert drive_map.suggest(db, NEW, drive_map.normalize(SAVED),
                             exists=lambda p: False, isdir=lambda p: True) is None
    drive_map.forget_server_folders()
    # the folder isn't there at all
    assert drive_map.suggest(db, NEW, drive_map.normalize(SAVED),
                             exists=lambda p: True, isdir=lambda p: False) is None


def test_no_suggestion_without_library_files_to_prove_it(db):
    assert drive_map.suggest(db, NEW, drive_map.normalize(SAVED),
                             exists=lambda p: True, isdir=lambda p: True) is None


def test_a_posix_mount_and_a_drive_letter_follow_their_own_pattern(db):
    saved = drive_map.normalize([{"server": "/mnt/sdb/PLEX/MOVIES", "local": "/media/sdb/PLEX/MOVIES"},
                                 {"server": "/srv/z/TV", "local": "Z:\\TV"}])
    _files(db, "/mnt/easystore3/PLEX/4K MOVIES")
    _files(db, "/srv/z/ANIME")
    hint = drive_map.suggest(db, "/mnt/easystore3/PLEX/4K MOVIES", saved,
                             exists=lambda p: True, isdir=lambda p: True)
    assert hint["local"] == "/media/easystore3/PLEX/4K MOVIES"
    # a drive letter rule only covers its own drive
    hint = drive_map.suggest(db, "/srv/z/ANIME", saved, exists=lambda p: True, isdir=lambda p: True)
    assert hint["local"] == "Z:\\ANIME"
    assert drive_map.suggest(db, "/srv/other/ANIME", saved, exists=lambda p: True, isdir=lambda p: True) is None


def test_the_folders_list_offers_it_and_never_saves_it(db):
    _files(db, NEW)
    _files(db, "/mnt/sdb/PLEX/MOVIES", 1)
    drive_map.save(db, SAVED)
    listed = [{"server": "/mnt/sdb/PLEX/MOVIES", "kind": "movie", "library": "Movies"},
              {"server": NEW, "kind": "movie", "library": "4K Movies"}]
    rows = {r["server"]: r for r in drive_map.server_folders(
        db, fetch=lambda: listed, exists=lambda p: True, isdir=lambda p: True)}
    row = rows[NEW]
    assert row["suggest"]["local"] == WANT and row["local"] == "" and not row["auto"]
    assert "suggest" not in rows["/mnt/sdb/PLEX/MOVIES"]
    assert NEW not in {e["server"] for e in drive_map.load(db)}
    # a check that fails offers nothing
    drive_map.forget_server_folders()
    rows = {r["server"]: r for r in drive_map.server_folders(
        db, fetch=lambda: listed, exists=lambda p: False, isdir=lambda p: True)}
    assert "suggest" not in rows[NEW]


class _Settings:
    def __init__(self):
        self.d = {}

    def get_setting(self, k, default=None):
        return self.d.get(k, default)

    def set_setting(self, k, v):
        self.d[k] = v


def test_an_extra_library_on_before_reading_on_switch_on_is_read_once():
    from core.video import library_scan as L
    db = _Settings()
    sel = {"movies": "Movies", "tv": "TV Shows", "movies_more": ["4K Movies"], "tv_more": []}
    # the main picks are a full scan's job; the extra one was never read
    assert L.unread_extras(db, sel) == [("movie", "4K Movies")]
    L.mark_read(db, "movie", "4K Movies")
    assert L.unread_extras(db, sel) == []
