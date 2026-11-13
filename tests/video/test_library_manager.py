"""The video library manager's reads: titles and files as rows a person can
sort and filter, the way the music library has always been.

The library page was a wall of posters with five filters. These are the facts
behind the manager's table, its Files view and its "needs a look" filters.
"""

from __future__ import annotations

import pytest

from core.video import library_manager as lm
from database.video_database import VideoDatabase


def _file(path, res="1080", codec="h264", size=1000, audio="en", subs="en", **more):
    return {"relative_path": path, "resolution": res, "video_codec": codec,
            "audio_codec": "aac", "size_bytes": size, "audio_languages": audio,
            "subtitle_languages": subs, **more}


@pytest.fixture()
def db(tmp_path):
    lm.forget()
    database = VideoDatabase(database_path=str(tmp_path / "v.db"))
    movies = [
        ("m1", "The Matrix", 1999, 603, [_file("/mnt/a/Movies/The Matrix (1999)/m.mkv", "4k", "hevc",
                                               9000, "en,ja", "en,fr", dynamic_range="HDR10")]),
        ("m2", "Alien", 1979, 348, [_file("/mnt/a/Movies/Alien/a.mkv", "720", "h264", 3000),
                                    _file("/mnt/b/Movies/Alien/a.1080.mkv", "1080", "h264", 5000)]),
        ("m3", "Zodiac", 2007, None, [_file("/mnt/b/Movies/Zodiac/z.avi", "sd", "mpeg4", 700,
                                            audio=None, subs=None)]),
        ("m4", "Brazil", 1985, 68, [_file("/mnt/b/Movies/Brazil/b.mkv", "1080", "h264", 4000,
                                          "en", "")]),
        ("m5", "Wanted Film", 2024, 999, []),
    ]
    ids = {}
    for sid, title, year, tmdb, files in movies:
        ids[title] = database.upsert_movie("plex", {
            "server_id": sid, "title": title, "year": year, "tmdb_id": tmdb,
            "files": files, "play_count": 2 if title == "Alien" else 0})
    show = database.upsert_show_tree("plex", {"server_id": "s1", "title": "Severance", "seasons": [
        {"season_number": 1, "episodes": [
            {"server_id": "e1", "episode_number": 1, "title": "One",
             "files": [_file("/mnt/a/TV/Severance/S01E01.mkv", "1080", "hevc", 2000)]},
            {"server_id": "e2", "episode_number": 2, "title": "Two",
             "files": [_file("/mnt/a/TV/Severance/S01E02.mkv", "720", "h264", 1500),
                       _file("/mnt/a/TV/Severance/S01E02.copy.mkv", "720", "h264", 1400)]},
        ]}]})
    database.backfill_episodes(show, 1, [
        {"episode_number": 3, "title": "Three", "air_date": "2022-03-01"},
        {"episode_number": 4, "title": "Later", "air_date": "2999-01-01"}])
    conn = database._get_connection()
    conn.execute("UPDATE shows SET tmdb_id=95396 WHERE id=?", (show,))
    conn.execute("UPDATE movies SET monitored=0 WHERE title='Brazil'")
    conn.commit()
    conn.close()
    ids["Severance"] = show
    yield database, ids
    lm.forget()


def _titles(database, kind="movies", **args):
    return [r["title"] for r in lm.titles(database, kind, args)["items"]]


def test_a_movie_row_carries_what_its_files_add_up_to(db):
    database, _ids = db
    out = lm.titles(database, "movies", {})
    assert out["totals"] == {"titles": 5, "size_bytes": 21700, "files": 5}
    matrix = next(r for r in out["items"] if r["title"] == "The Matrix")
    assert matrix["resolution"] == "4K" and matrix["video_codecs"] == ["hevc"]
    assert matrix["dynamic_range"] == ["HDR10"] and matrix["drive"] == "/mnt/a"
    assert matrix["audio_languages"] == ["en", "ja"] and matrix["subtitle_languages"] == ["en", "fr"]
    assert matrix["file_count"] == 1 and matrix["extra_copies"] == 0 and matrix["owned"] is True
    alien = next(r for r in out["items"] if r["title"] == "Alien")
    # two copies: the best one names the row, both are counted
    assert alien["resolution"] == "1080p" and alien["extra_copies"] == 1
    assert alien["resolution_mix"] == {"1080p": 1, "720p": 1} and alien["size_bytes"] == 8000
    wanted = next(r for r in out["items"] if r["title"] == "Wanted Film")
    assert wanted["owned"] is False and wanted["file_count"] == 0 and wanted["resolution"] == ""


def test_a_show_row_counts_episodes_and_only_aired_ones_as_missing(db):
    database, _ids = db
    row = lm.titles(database, "shows", {})["items"][0]
    assert (row["episodes_total"], row["episodes_owned"], row["episodes_missing"]) == (4, 2, 1)
    assert row["file_count"] == 3 and row["extra_copies"] == 1 and row["seasons"] == 1
    assert row["resolution_mix"] == {"1080p": 1, "720p": 2} and row["video_codecs"] == ["hevc", "h264"]


@pytest.mark.parametrize("args, expected", [
    ({"resolution": "4k"}, ["The Matrix"]),
    ({"resolution": "720p"}, ["Alien"]),
    ({"codec": "mpeg4"}, ["Zodiac"]),
    ({"hdr": "1"}, ["The Matrix"]),
    ({"audio_language": "ja"}, ["The Matrix"]),
    ({"subtitle_language": "fr"}, ["The Matrix"]),
    ({"drive": "/mnt/b"}, ["Brazil", "Zodiac"]),
    ({"status": "not_owned"}, ["Wanted Film"]),
    ({"monitored": "0"}, ["Brazil"]),
    ({"search": "ali"}, ["Alien"]),
    ({"letter": "m"}, ["The Matrix"]),          # filed under M, not T
    ({"year_from": "1990", "year_to": "2010"}, ["The Matrix", "Zodiac"]),
    ({"needs": "copies"}, ["Alien"]),
    ({"needs": "unmatched"}, ["Zodiac"]),
    ({"needs": "unprobed"}, ["Zodiac"]),
    ({"needs": "no_subtitles"}, ["Brazil"]),     # probed, and no subtitle track
    ({"needs": "unmonitored"}, ["Brazil"]),
    ({"needs": "missing"}, ["Wanted Film"]),
    ({"needs": "unwatched"}, ["Brazil", "The Matrix", "Zodiac"]),
    ({"needs": "copies,unwatched"}, []),         # needs add up: both must hold
])
def test_filters(db, args, expected):
    assert _titles(db[0], **args) == expected


def test_sorts_put_a_title_with_no_value_last_either_way(db):
    database, _ids = db
    assert _titles(database, sort="size") == ["The Matrix", "Alien", "Brazil", "Zodiac", "Wanted Film"]
    assert _titles(database, sort="size", dir="asc")[0] == "Wanted Film"   # 0 is a value
    assert _titles(database, sort="resolution")[:2] == ["The Matrix", "Alien"]
    assert _titles(database, sort="year") == ["Wanted Film", "Zodiac", "The Matrix", "Brazil", "Alien"]
    assert _titles(database, sort="title") == ["Alien", "Brazil", "The Matrix", "Wanted Film", "Zodiac"]
    conn = database._get_connection()
    conn.execute("UPDATE movies SET year=NULL WHERE title='Alien'")
    conn.commit()
    conn.close()
    lm.forget()
    assert _titles(database, sort="year")[-1] == "Alien"
    assert _titles(database, sort="year", dir="asc")[-1] == "Alien"


def test_pages_and_select_all(db):
    database, _ids = db
    out = lm.titles(database, "movies", {"limit": 2, "page": 2})
    assert [r["title"] for r in out["items"]] == ["The Matrix", "Wanted Film"]
    assert out["pagination"]["total_pages"] == 3 and out["pagination"]["has_next"] is True
    assert len(lm.title_ids(database, "movies", {"status": "owned"})) == 4


def test_below_cutoff_reads_the_titles_quality_profile(db, monkeypatch):
    database, _ids = db
    monkeypatch.setattr(lm, "_cutoff_ranks", lambda _db, _kind: (3, {}))   # 1080p for everyone
    lm.forget()
    assert _titles(database, needs="below_cutoff") == ["Zodiac"]
    # a show is held to its worst file: one 720p episode is below a 1080p cutoff
    assert _titles(database, "shows", needs="below_cutoff") == ["Severance"]
    monkeypatch.setattr(lm, "_cutoff_ranks", lambda _db, _kind: (0, {}))   # no cutoff set
    lm.forget()
    assert _titles(database, needs="below_cutoff") == []


def test_a_write_refreshes_just_its_titles_and_a_gone_title_leaves(db):
    database, ids = db
    assert _titles(database, needs="unmonitored") == ["Brazil"]
    conn = database._get_connection()
    conn.execute("UPDATE movies SET monitored=0 WHERE title='Alien'")
    conn.execute("DELETE FROM movies WHERE title='Zodiac'")
    conn.commit()
    conn.close()
    # the summary is not re-read on its own
    assert _titles(database, needs="unmonitored") == ["Brazil"]
    assert lm.refresh(database, "movie", [ids["Alien"], ids["Zodiac"]]) == 1
    assert _titles(database, needs="unmonitored") == ["Alien", "Brazil"]
    assert "Zodiac" not in _titles(database)


def test_a_profiles_own_watch_state_decides_unwatched(db):
    database, ids = db
    conn = database._get_connection()
    conn.execute("INSERT INTO video_watch_state(profile_id, kind, item_id, play_count) "
                 "VALUES(3, 'movie', ?, 1)", (ids["Brazil"],))
    conn.commit()
    conn.close()
    assert _titles(database, needs="unwatched") == ["Brazil", "The Matrix", "Zodiac"]
    out = lm.titles(database, "movies", {"needs": "unwatched"}, watcher=3)
    assert [r["title"] for r in out["items"]] == ["Alien", "The Matrix", "Zodiac"]
    brazil = next(r for r in lm.titles(database, "movies", {}, watcher=3)["items"]
                  if r["title"] == "Brazil")
    assert brazil["watched"] is True


def test_facets_count_the_titles_each_filter_would_show(db):
    database, _ids = db
    f = lm.facets(database, "movies")
    assert f["titles"] == 5 and f["owned"] == 4 and f["files"] == 5
    assert [x["value"] for x in f["resolutions"]] == ["4K", "1080p", "720p", "SD"]
    assert {x["value"]: x["count"] for x in f["codecs"]} == {"h264": 2, "hevc": 1, "mpeg4": 1}
    assert {x["value"]: x["count"] for x in f["drives"]} == {"/mnt/a": 2, "/mnt/b": 2}
    assert f["needs"]["copies"] == 1 and f["needs"]["unmatched"] == 1
    for need, count in f["needs"].items():
        assert count == len(_titles(database, needs=need)), need


def test_files_are_rows_of_their_own(db):
    database, ids = db
    out = lm.files(database, {"kind": "movies", "sort": "size"})
    assert out["totals"] == {"files": 5, "size_bytes": 21700}
    first = out["items"][0]
    assert (first["title"], first["filename"], first["resolution"]) == ("The Matrix", "m.mkv", "4K")
    assert first["dynamic_range"] == "HDR10" and first["drive"] == "/mnt/a" and first["probed"]
    names = lambda **a: [r["filename"] for r in lm.files(database, {"kind": "movies", **a})["items"]]  # noqa: E731
    assert names(resolution="720p") == ["a.mkv"]
    assert names(codec="mpeg4") == ["z.avi"]
    assert names(drive="/mnt/b", sort="path") == ["a.1080.mkv", "b.mkv", "z.avi"]
    assert sorted(names(needs="copies")) == ["a.1080.mkv", "a.mkv"]
    assert names(needs="unprobed") == ["z.avi"]
    assert names(needs="no_subtitles") == ["b.mkv"]
    assert names(search="zodiac") == ["z.avi"]
    assert names(title_id=str(ids["Alien"]), sort="size") == ["a.1080.mkv", "a.mkv"]

    shows = lm.files(database, {"kind": "shows"})
    assert [(r["season_number"], r["episode_number"]) for r in shows["items"]] == [(1, 1), (1, 2), (1, 2)]
    assert shows["items"][0]["episode_title"] == "One" and shows["items"][0]["title"] == "Severance"
    copies = lm.files(database, {"kind": "shows", "needs": "copies"})["items"]
    assert [r["filename"] for r in copies] == ["S01E02.mkv", "S01E02.copy.mkv"]


def test_a_drive_filter_is_a_prefix_not_a_pattern(db):
    database, _ids = db
    assert lm.files(database, {"kind": "movies", "drive": "/mnt/%"})["totals"]["files"] == 0
    assert _titles(database, drive="/mnt/%") == []


# ── over http ────────────────────────────────────────────────────────────────

def _client(database, *, is_admin=True):
    import api.video as videoapi
    from flask import Flask
    videoapi._video_db = database
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        from flask import g
        g.is_admin = is_admin
        g.profile_id = 1 if is_admin else 2

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client()


def test_the_manager_api_serves_rows_filters_and_ids(db, monkeypatch):
    database, ids = db
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda: "plex")
    client = _client(database)
    out = client.get("/api/video/manager/titles?kind=movies&needs=copies").get_json()
    assert out["success"] and [r["title"] for r in out["items"]] == ["Alien"]
    files = client.get("/api/video/manager/files?kind=movies&codec=mpeg4").get_json()
    assert [r["filename"] for r in files["items"]] == ["z.avi"]
    facets = client.get("/api/video/manager/facets?kind=movies").get_json()
    assert facets["needs"]["copies"] == 1 and "quality_profiles" in facets and "libraries" in facets
    picked = client.get("/api/video/manager/ids?kind=movies&status=owned").get_json()
    assert sorted(picked["ids"]) == sorted(ids[t] for t in ("The Matrix", "Alien", "Zodiac", "Brazil"))
    assert picked["truncated"] is False


def test_the_manager_is_the_admins(db, monkeypatch):
    database, _ids = db
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda: "plex")
    client = _client(database, is_admin=False)
    for path in ("/api/video/manager/titles", "/api/video/manager/files",
                 "/api/video/manager/facets", "/api/video/manager/ids",
                 "/api/video/manager/rename-preview?kind=movie&ids=1",
                 "/api/video/manager/delete-preview?kind=movie&ids=1"):
        assert client.get(path).status_code == 403, path


def test_rename_preview_lists_only_the_files_that_would_change(db, monkeypatch):
    from core.video import file_ops
    database, ids = db
    plans = {"a.mkv": {"current": "a.mkv", "expected": "Alien (1979).mkv", "conforms": False},
             "a.1080.mkv": {"current": "a.1080.mkv", "expected": "a.1080.mkv", "conforms": True}}

    def plan(_db, file_id):
        row = _db._get_connection().execute(
            "SELECT relative_path FROM media_files WHERE id=?", (file_id,)).fetchone()
        return plans.get(row[0].rsplit("/", 1)[-1], {"error": "not reachable"})
    monkeypatch.setattr(file_ops, "rename_plan", plan)
    client = _client(database)
    out = client.get("/api/video/manager/rename-preview?kind=movie&ids=%d,%d" % (
        ids["Alien"], ids["Zodiac"])).get_json()
    assert (out["files"], out["to_rename"], out["already_named"], out["unreachable"]) == (3, 1, 1, 1)
    assert [c["expected"] for c in out["changes"]] == ["Alien (1979).mkv"] and out["more"] == 0
    assert client.get("/api/video/manager/rename-preview?kind=movie").status_code == 400


def test_delete_preview_adds_the_selection_up(db, monkeypatch):
    from core.video import delete_title
    database, ids = db
    monkeypatch.setattr(delete_title, "plan", lambda _db, kind, item_id: {
        "title": "T%d" % item_id, "files": 2, "size_bytes": 500,
        "blocked": "seeding" if item_id == ids["Alien"] else None})
    client = _client(database)
    out = client.get("/api/video/manager/delete-preview?kind=movie&ids=%d,%d" % (
        ids["Alien"], ids["Brazil"])).get_json()
    assert (out["titles"], out["files"], out["size_bytes"]) == (2, 4, 1000)
    assert out["blocked"] == [{"title": "T%d" % ids["Alien"], "reason": "seeding"}]
