"""SoulSync Standalone reads a library as well as a media server does.

titles as they're written, two folders of one show as one show, anime and
daily numbering, editions, the folder's own poster, a renamed folder keeping
its row, a file's real resolution read from the file, a fingerprint for a
name that says nothing, and a folder watched so a dropped file shows up.
"""

from __future__ import annotations

import os

import pytest

from core.video import disk_source as ds
from core.video.disk_source import DiskVideoSource
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


def _x(db, sql, *args):
    conn = db._get_connection()
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


class Search:
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
    search = Search()
    box = {"hash": None}

    def source():
        return DiskVideoSource(db, movie_roots=[str(movies)], tv_roots=[str(tv)], search=search,
                               scanner_status=lambda: False, hash_lookup=box["hash"])

    return db, movies, tv, search, source, box


def _scan(db, source, mode="full"):
    st = VideoLibraryScanner(db).scan_sync(source, mode=mode)
    assert st["state"] == "done", st
    return st


# ── titles as they're written ────────────────────────────────────────────────

def test_a_title_is_taken_as_written():
    assert ds.title_from_folder("9-1-1") == ("9-1-1", None)
    assert ds.title_from_folder("The Matrix (1999) [1080p] [WEBRip] [YTS]") == ("The Matrix", 1999)
    assert ds.title_from_folder("Spider-Man (2002) {tmdb-557}") == ("Spider-Man", 2002)
    assert ds.title_from_folder("The.Matrix.1999.1080p.BluRay.x264-GRP") == ("The Matrix", 1999)
    assert ds.meta_from_nfo("<movie><title>WALL&amp;E</title><year>2008</year></movie>") == \
        {"title": "WALL&E", "year": 2008}


def test_the_nfo_names_the_title(lib):
    db, movies, tv, search, source, _ = lib
    _touch(tv, "9-1-1/Season 01/9-1-1 - S01E01.mkv")
    _touch(tv, "9-1-1/tvshow.nfo", text="<tvshow><title>9-1-1</title><year>2018</year>"
                                        '<uniqueid type="tmdb">75219</uniqueid></tvshow>')
    _scan(db, source)
    assert _q(db, "SELECT title, year, tmdb_id FROM shows") == [{"title": "9-1-1", "year": 2018, "tmdb_id": 75219}]
    assert search.calls == []


# ── two folders, one show ────────────────────────────────────────────────────

def test_two_folders_of_one_show_are_one_show(lib):
    db, movies, tv, search, source, _ = lib
    _touch(tv, "9-1-1 Nashville {tmdb-284838}/Season 01/Nash - S01E01.mkv")
    _touch(tv, "9-1-1 - Nashville {tmdb-284838}/Season 01/Nash - S01E02.mkv")
    _touch(tv, "9-1-1 - Nashville {tmdb-284838}/Season 01/Nash - S01E03.mkv")
    _scan(db, source)
    shows = _q(db, "SELECT id FROM shows")
    assert len(shows) == 1
    assert [r["episode_number"] for r in _q(db, "SELECT episode_number FROM episodes ORDER BY 1")] == [1, 2, 3]
    # again, and deep: still one show, nothing pruned
    _scan(db, source, mode="deep")
    assert _q(db, "SELECT id FROM shows") == shows
    assert len(_q(db, "SELECT id FROM media_files")) == 3


def test_an_unnamed_second_folder_joins_the_show_the_library_already_has(lib):
    # TMDB has several shows called 60 Minutes, so a search alone can't say.
    # the library's own identified '60 Minutes' can.
    db, movies, tv, search, source, _ = lib
    search.answers[("show", "60 Minutes")] = [{"id": 651, "title": "60 Minutes", "year": 1968},
                                              {"id": 2, "title": "60 Minutes", "year": 1979}]
    _touch(tv, "60 Minutes/Season 58/60 Minutes - S58E01.mkv")
    _touch(tv, "60 Minutes/tvshow.nfo", text="<tvshow><title>60 Minutes</title><tmdbid>651</tmdbid></tvshow>")
    _touch(tv, "60.Minutes/Season 59/60.Minutes.S59E01.mkv")
    # two identified shows share another title: that one stays apart, for review
    _touch(tv, "The Office {tmdb-2316}/Season 1/The Office - S01E01.mkv")
    _touch(tv, "The Office UK {tmdb-2996}/Season 1/The Office - S01E01.mkv")
    _touch(tv, "The Office UK {tmdb-2996}/tvshow.nfo", text="<tvshow><title>The Office</title></tvshow>")
    _touch(tv, "The.Office/Season 2/The.Office.S02E01.mkv")
    search.answers[("show", "The Office")] = [{"id": 2316, "title": "The Office", "year": 2005},
                                              {"id": 2996, "title": "The Office", "year": 2001}]
    _scan(db, source)
    rows = {r["tmdb_id"]: r["n"] for r in _q(
        db, "SELECT s.tmdb_id, COUNT(e.id) n FROM shows s LEFT JOIN episodes e ON e.show_id=s.id GROUP BY s.id")}
    assert rows == {651: 2, 2316: 1, 2996: 1, None: 1}
    assert ("show", "60 Minutes") not in search.calls


def test_a_second_folder_found_by_search_is_not_searched_again(lib):
    db, movies, tv, search, source, _ = lib
    search.answers[("show", "Silo")] = [{"id": 1, "title": "Silo", "year": 2023}]
    _touch(tv, "Silo (2023)/Season 01/Silo - S01E01.mkv")
    _touch(tv, "Silo (2023) part two/Season 01/Silo - S01E02.mkv")
    search.answers[("show", "Silo (2023) part two")] = []
    _touch(tv, "Silo/Season 02/Silo - S02E01.mkv")
    search.answers[("show", "Silo")] = [{"id": 1, "title": "Silo", "year": 2023}]
    _scan(db, source)
    asked = len(search.calls)
    _scan(db, source)
    assert len(search.calls) == asked


# ── anime and daily numbering ────────────────────────────────────────────────

def _episodes(db, show_id, rows):
    conn = db._get_connection()
    try:
        for s, e, air in rows:
            conn.execute("INSERT OR IGNORE INTO seasons (show_id, season_number) VALUES (?, ?)", (show_id, s))
            sid = conn.execute("SELECT id FROM seasons WHERE show_id=? AND season_number=?",
                               (show_id, s)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO episodes (show_id, season_id, season_number, episode_number, "
                         "air_date) VALUES (?, ?, ?, ?, ?)", (show_id, sid, s, e, air))
        conn.commit()
    finally:
        conn.close()


def test_absolute_and_dated_files_are_placed_once_the_episode_list_is_known(lib):
    db, movies, tv, search, source, _ = lib
    _touch(tv, "Frieren {tmdb-209867}/[Grp] Frieren - 01 [1080p].mkv")
    _touch(tv, "Frieren {tmdb-209867}/[Grp] Frieren - 03v2 [1080p].mkv")
    _touch(tv, "The Daily Show {tmdb-2224}/The.Daily.Show.2026.11.20.1080p.WEB.mkv")
    _scan(db, source)
    # no list yet: the shows are there, their files wait (nothing guessed)
    assert len(_q(db, "SELECT id FROM shows")) == 2 and _q(db, "SELECT id FROM episodes") == []
    by = {r["tmdb_id"]: r["id"] for r in _q(db, "SELECT id, tmdb_id FROM shows")}
    # enrichment brings the episode lists
    _episodes(db, by[209867], [(0, 1, None), (1, 1, "2023-09-29"), (1, 2, "2023-09-29"), (2, 1, "2026-01-01")])
    _episodes(db, by[2224], [(31, 20, "2026-11-20"), (31, 21, "2026-11-21")])
    _scan(db, source)
    owned = {(r["tmdb_id"], r["season_number"], r["episode_number"]) for r in _q(
        db, "SELECT s.tmdb_id, e.season_number, e.episode_number FROM episodes e "
            "JOIN shows s ON s.id=e.show_id WHERE e.has_file=1")}
    # 03 is the third episode overall: season 2's first. specials don't count
    assert owned == {(209867, 1, 1), (209867, 2, 1), (2224, 31, 20)}


def test_an_absolute_number_inside_a_season_folder_is_still_absolute(lib):
    # '[Grp] Show - 03' filed under 'Season 1' of a show whose season 1 has two
    # episodes can't be S01E03: it's the third overall
    db, movies, tv, search, source, _ = lib
    _touch(tv, "Frieren {tmdb-209867}/Season 1/[Grp] Frieren - 02.mkv")
    _scan(db, source)
    show_id = _q(db, "SELECT id FROM shows")[0]["id"]
    _episodes(db, show_id, [(1, 1, None), (1, 2, None), (2, 1, None)])
    _touch(tv, "Frieren {tmdb-209867}/Season 1/[Grp] Frieren - 03.mkv")
    _scan(db, source)
    assert {(r["season_number"], r["episode_number"]) for r in _q(
        db, "SELECT season_number, episode_number FROM episodes WHERE has_file=1")} == {(1, 2), (2, 1)}


def test_a_bare_number_in_a_season_folder_is_that_seasons(lib):
    db, movies, tv, search, source, _ = lib
    _touch(tv, "Bluey {tmdb-82728}/Season 2/Bluey - 05.mkv")
    _touch(tv, "Bluey {tmdb-82728}/Specials/Bluey - 01.mkv")
    _scan(db, source)
    assert {(r["season_number"], r["episode_number"]) for r in
            _q(db, "SELECT season_number, episode_number FROM episodes")} == {(2, 5), (0, 1)}


# ── editions, extras, the folder's own poster ───────────────────────────────

def test_editions():
    assert ds.edition_of("Blade Runner (1982) {edition-Final Cut} 2160p.mkv") == "Final Cut"
    assert ds.edition_of("Aliens.1986.Directors.Cut.1080p.mkv") == "Director's Cut"
    assert ds.edition_of("Aliens (1986) Extended Edition.mkv") == "Extended"
    assert ds.edition_of("Aliens (1986) 1080p.mkv") is None
    # the title is not an edition
    assert ds.edition_of("Extended Family (2023).mkv", "Extended Family") is None
    assert ds.edition_of("The Final Cut (2004) 1080p.mkv", "The Final Cut") is None


def test_a_movies_versions_carry_their_edition_and_extras_are_not_versions(lib):
    db, movies, tv, search, source, _ = lib
    _touch(movies, "Aliens (1986) {tmdb-679}/Aliens (1986) 1080p.mkv", 300)
    _touch(movies, "Aliens (1986) {tmdb-679}/Aliens (1986) {edition-Director's Cut} 1080p.mkv", 400)
    _touch(movies, "Aliens (1986) {tmdb-679}/Aliens (1986)-trailer.mkv", 20)
    _touch(movies, "Aliens (1986) {tmdb-679}/Aliens (1986)-interview.mkv", 20)
    _touch(movies, "Aliens (1986) {tmdb-679}/Aliens (1986)-deleted.mkv", 20)
    _touch(movies, "Aliens (1986) {tmdb-679}/Featurettes/Making Of.mkv", 20)
    _scan(db, source)
    files = {os.path.basename(f["relative_path"]): f["edition"] for f in _q(db, "SELECT * FROM media_files")}
    assert files == {"Aliens (1986) 1080p.mkv": None,
                     "Aliens (1986) {edition-Director's Cut} 1080p.mkv": "Director's Cut"}


def test_the_folders_poster_is_the_poster_until_a_chosen_one(lib):
    db, movies, tv, search, source, _ = lib
    _touch(movies, "Aliens (1986) {tmdb-679}/Aliens.mkv")
    art = _touch(movies, "Aliens (1986) {tmdb-679}/poster.jpg", 10)
    _scan(db, source)
    assert _q(db, "SELECT poster_url FROM movies")[0]["poster_url"] == ds.LOCAL_ART + art
    # the person (or enrichment) picks one: a scan leaves it
    _x(db, "UPDATE movies SET poster_url='https://image.tmdb.org/t/p/w500/a.jpg'")
    _scan(db, source)
    assert _q(db, "SELECT poster_url FROM movies")[0]["poster_url"].startswith("https://")


def test_the_poster_route_serves_a_local_poster_only_from_a_library_folder(lib, tmp_path, monkeypatch):
    from flask import Flask
    import api.video as videoapi
    db, movies, tv, search, source, _ = lib
    _touch(movies, "Aliens (1986) {tmdb-679}/Aliens.mkv")
    art = movies / "Aliens (1986) {tmdb-679}" / "poster.jpg"
    art.write_bytes(b"\xff\xd8\xff\xe0jpegbytes")
    _scan(db, source)
    monkeypatch.setattr("core.video.library_roots.library_roots",
                        lambda _db, kind=None: [str(movies), str(tv)])
    videoapi._video_db = db
    try:
        app = Flask(__name__)
        app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
        c = app.test_client()
        mid = _q(db, "SELECT id FROM movies")[0]["id"]
        r = c.get(f"/api/video/poster/movie/{mid}")
        assert r.status_code == 200 and r.data.startswith(b"\xff\xd8")
        # a 'local' poster pointing outside the library is never served
        secret = tmp_path / "secret.jpg"
        secret.write_bytes(b"\xff\xd8secret")
        _x(db, "UPDATE movies SET poster_url=?", ds.LOCAL_ART + str(secret))
        assert c.get(f"/api/video/poster/movie/{mid}").status_code == 404
        _x(db, "UPDATE movies SET poster_url=?", ds.LOCAL_ART + str(movies / "Aliens (1986) {tmdb-679}" / "Aliens.mkv"))
        assert c.get(f"/api/video/poster/movie/{mid}").status_code == 404
    finally:
        videoapi._video_db = None


# ── renames and moves ───────────────────────────────────────────────────────

def test_a_renamed_folder_keeps_its_row_and_a_renamed_file_keeps_its_id(lib):
    db, movies, tv, search, source, _ = lib
    _touch(movies, "Aliens {tmdb-679}/aliens.mkv", 777)
    _touch(tv, "Silo {tmdb-1}/Season 01/Silo - S01E01.mkv", 555)
    _scan(db, source)
    m0 = _q(db, "SELECT id FROM movies")[0]["id"]
    s0 = _q(db, "SELECT id FROM shows")[0]["id"]
    f0 = {r["size_bytes"]: r["id"] for r in _q(db, "SELECT id, size_bytes FROM media_files")}
    _x(db, "UPDATE movies SET monitored=0")          # a choice the person made
    os.rename(movies / "Aliens {tmdb-679}", movies / "Aliens (1986) {tmdb-679}")
    os.rename(movies / "Aliens (1986) {tmdb-679}" / "aliens.mkv",
              movies / "Aliens (1986) {tmdb-679}" / "Aliens (1986).mkv")
    os.rename(tv / "Silo {tmdb-1}", tv / "Silo (2023) {tmdb-1}")
    _scan(db, source, mode="deep")
    assert _q(db, "SELECT id, monitored FROM movies") == [{"id": m0, "monitored": 0}]
    assert _q(db, "SELECT id FROM shows") == [{"id": s0}]
    assert {r["size_bytes"]: r["id"] for r in _q(db, "SELECT id, size_bytes FROM media_files")} == f0
    assert all("(1986)" in r["relative_path"] or "(2023)" in r["relative_path"]
               for r in _q(db, "SELECT relative_path FROM media_files"))


def test_a_second_copy_in_another_folder_is_not_a_rename(lib):
    db, movies, tv, search, source, _ = lib
    _touch(movies, "Aliens {tmdb-679}/aliens.mkv")
    _scan(db, source)
    _touch(movies, "Aliens 4K {tmdb-679}/aliens.2160p.mkv")
    _scan(db, source, mode="deep")
    assert len(_q(db, "SELECT id FROM movies")) == 2


# ── a name that says nothing ────────────────────────────────────────────────

def test_the_main_files_name_then_its_fingerprint(lib):
    db, movies, tv, search, source, box = lib
    search.answers[("movie", "The Matrix")] = [{"id": 603, "title": "The Matrix", "year": 1999}]
    _touch(movies, "new folder (2)/The.Matrix.1999.1080p.BluRay.x264-GRP.mkv")
    _touch(movies, "rips/a1b2c3.mkv")
    box["hash"] = lambda path: {"tmdb_id": 949, "imdb_id": "tt0113277"} if path.endswith("a1b2c3.mkv") else None
    _scan(db, source)
    got = {r["tmdb_id"]: r for r in _q(db, "SELECT * FROM movies")}
    assert set(got) == {603, 949}
    assert got[949]["imdb_id"] == "tt0113277" and got[949]["tmdb_match_status"] is None


def test_fingerprint_answers():
    from core.video.file_identity import hash_lookup, identity_from_search

    def row(match, tmdb, imdb, kind="Movie"):
        return {"attributes": {"moviehash_match": match,
                               "feature_details": {"feature_type": kind, "tmdb_id": tmdb, "imdb_id": imdb}}}
    assert identity_from_search({"data": [row(True, 949, 113277), row(True, 949, 113277), row(False, 1, 2)]}) == \
        {"tmdb_id": 949, "imdb_id": "tt0113277"}
    # two films claim the fingerprint, or none matched by it: no answer
    assert identity_from_search({"data": [row(True, 949, 113277), row(True, 603, 133093)]}) is None
    assert identity_from_search({"data": [row(False, 949, 113277)]}) is None
    assert identity_from_search({"data": [row(True, 5, 6, "Episode")]}) is None

    class DB:
        def __init__(self, key):
            self.key = key

        def get_setting(self, k):
            return self.key
    assert hash_lookup(DB("")) is None
    asked = []
    look = hash_lookup(DB("k"), hasher=lambda p: "abc123",
                       get_json=lambda url, params, headers: (asked.append(params), {"data": [row(True, 949, 113277)]})[1])
    assert look("/x.mkv")["tmdb_id"] == 949 and asked == [{"moviehash": "abc123"}]


# ── a file's facts, read from the file ──────────────────────────────────────

def test_ffprobe_reads_hdr_and_atmos():
    from core.video.mediainfo import parse_ffprobe
    info = parse_ffprobe({"format": {"duration": "7200.5"}, "streams": [
        {"codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 1600,
         "color_transfer": "smpte2084",
         "side_data_list": [{"side_data_type": "DOVI configuration record"}]},
        {"codec_type": "audio", "codec_name": "truehd", "channels": 8, "profile": "Dolby TrueHD + Dolby Atmos"}]})
    assert info["resolution"] == "2160p" and info["dynamic_range"] == "DV HDR10" and info["atmos"] is True
    sdr = parse_ffprobe({"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
                                      "color_transfer": "bt709"}]})
    assert sdr["dynamic_range"] is None and sdr["atmos"] is False
    hlg = parse_ffprobe({"streams": [{"codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 2160,
                                      "color_transfer": "arib-std-b67"}]})
    assert hlg["dynamic_range"] == "HLG"


def test_a_probe_fills_what_the_name_didnt_say_and_never_overrides(lib):
    db, movies, tv, search, source, _ = lib
    plain = _touch(movies, "Aliens (1986) {tmdb-679}/Aliens (1986).mkv", 4000)
    named = _touch(movies, "Heat (1995) {tmdb-949}/Heat (1995) 720p.mkv", 5000)
    _scan(db, source)
    # the name gave no resolution: the file is still to be read, though its
    # languages may already be
    _x(db, "UPDATE media_files SET audio_languages='en'")
    todo = {os.path.basename(r["relative_path"]) for r in db.media_files_unprobed(50)}
    assert todo == {"Aliens (1986).mkv"} and db.media_files_unprobed_count() == 1
    probe = {"ok": True, "resolution": "2160p", "video_codec": "hevc", "audio_codec": "eac3",
             "audio_channels": 6, "dynamic_range": "HDR10", "atmos": True, "duration_sec": 8220.4,
             "aspect": "2.40:1", "audio_languages": ["en"], "subtitle_languages": []}
    assert db.record_file_probe(plain, 4000, probe)
    assert db.record_file_probe(named, 5000, probe)
    by = {os.path.basename(r["relative_path"]): r for r in _q(db, "SELECT * FROM media_files")}
    a = by["Aliens (1986).mkv"]
    assert (a["resolution"], a["video_codec"], a["dynamic_range"], a["atmos"], a["runtime_seconds"]) == \
        ("2160p", "hevc", "HDR10", 1, 8220)
    assert by["Heat (1995) 720p.mkv"]["resolution"] == "720p"       # what was known stays
    assert db.media_files_unprobed_count() == 0
    # a scan rewrites the file rows: what was read comes back
    _scan(db, source)
    assert _q(db, "SELECT resolution FROM media_files WHERE relative_path=?", plain)[0]["resolution"] == "2160p"


# ── a folder watched ────────────────────────────────────────────────────────

def test_only_real_library_changes_count():
    from core.video.folder_watch import is_library_change
    assert is_library_change("/m/Aliens (1986)/Aliens.mkv")
    assert is_library_change("/m/Aliens (1986)")                  # a folder
    assert is_library_change("/m/Aliens (1986)/movie.nfo")
    assert not is_library_change("/m/Aliens (1986)/Aliens.mkv.part")
    assert not is_library_change("/m/Aliens (1986)/poster.jpg")
    assert not is_library_change("/m/Aliens (1986)/Aliens.en.srt")
    assert not is_library_change("/m/.hidden")


def test_a_change_scans_once_things_go_quiet(tmp_path, monkeypatch):
    from core.video import folder_watch as fw
    now = [1000.0]
    scans = []
    w = fw.VideoFolderWatch(lambda: None, request_scan=lambda: scans.append(now[0]), clock=lambda: now[0])
    w.changed("/m/Aliens/Aliens.mkv")
    now[0] += 5
    w.changed("/m/Aliens/Aliens.mkv")           # still copying
    now[0] += fw.QUIET_SEC - 1
    assert w.tick() is False
    now[0] += 2
    assert w.tick() is True and len(scans) == 1
    assert w.tick() is False                    # once


def test_it_watches_only_while_soulsync_is_the_library_and_polls_what_it_cant_watch(tmp_path, monkeypatch):
    from core.video import folder_watch as fw
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    server = ["plex"]
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda _db=None: server[0])
    monkeypatch.setattr("core.video.library_roots.library_roots",
                        lambda _db, kind=None: ["/m"] if kind == "movie" else ["//nas/tv"])

    class Obs:
        def __init__(self):
            self.roots, self.started, self.stopped = [], False, False

        def schedule(self, handler, root, recursive=False):
            if root.startswith("//"):
                raise OSError("can't watch a share")
            self.roots.append((root, recursive, handler))

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

    made = []
    now = [0.0]
    scans = []
    w = fw.VideoFolderWatch(lambda: db, request_scan=lambda: scans.append(1), clock=lambda: now[0],
                            observer_factory=lambda: (made.append(Obs()), made[-1])[1])
    w.sync()
    assert made == []                                   # plex notices its own files
    server[0] = "soulsync"
    w.sync()
    assert [r[:2] for r in made[0].roots] == [("/m", True)] and made[0].started
    # the watcher's events reach the debounce
    class Ev:
        src_path, dest_path = "/m/Aliens/Aliens.mkv", ""
    made[0].roots[0][2].dispatch(Ev())
    now[0] += fw.QUIET_SEC + 1
    assert w.tick() is True
    # the share is checked on a timer
    now[0] += fw.POLL_SEC + 1
    assert w.tick() is True and len(scans) == 2
    server[0] = "plex"
    w.sync()
    assert made[0].stopped
