"""home videos (core/video/home_videos.py, api/player_home.py,
scrap/HOME_VIDEOS_CONTRACT.md): clips from a camera or a phone, as a
library of the player. never looked up; named and dated from the clip
itself; the person's edits survive every rescan; a file gone is marked
missing, never deleted on the first miss."""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import time

import pytest
from flask import Flask, g

import api.player as player_api
import api.player_home as home_api
from core.player import home_access, home_media
from core.player import libraries as pl
from core.video import home_videos as H
from database.video_database import VideoDatabase


# ── names and dates ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name, title", [
    ("VID_20261031_183012.mp4", "Oct 31, 2026, 6:30 PM"),
    ("PXL_20261031_183012345.mp4", "Oct 31, 2026, 6:30 PM"),
    ("20261031_183012.mp4", "Oct 31, 2026, 6:30 PM"),
    ("IMG_20240704_210500.MOV", "Jul 4, 2024, 9:05 PM"),
    ("VID-20230115-WA0003.mp4", "Jan 15, 2023"),
    ("2019-11-25 First snow.mov", "First snow"),
    ("riding_without_training-wheels.mkv", "Riding without training wheels"),
    ("Thanksgiving.at.Grandmas.mp4", "Thanksgiving at Grandmas"),
])
def test_names_are_cleaned_up(name, title):
    got = H.derive(f"/home/clips/{name}", "Clips", camera_at=None, server_at=None, mtime=None,
                   added_at="2026-11-25T10:00:00")
    assert got["title"] == title


def test_a_camera_number_takes_the_date_the_clip_was_filmed():
    got = H.derive("/x/Holidays/IMG_1234.MOV", "Holidays", camera_at="2023-11-25T14:05:00",
                   server_at=None, mtime=None, added_at=None)
    assert got == {"title": "Nov 25, 2023, 2:05 PM", "filmed_at": "2023-11-25T14:05:00", "filmed_source": "camera"}
    # no date anywhere: the folder names it
    got = H.derive("/x/Old tapes/GOPR0042.MP4", "Old tapes", camera_at=None, server_at=None, mtime=None,
                   added_at=None)
    assert got["title"] == "Old tapes" and got["filmed_source"] is None or got["filmed_source"] == "added"
    assert H.is_camera_name("DSC_0001") and H.is_camera_name("MVI_1234") and not H.is_camera_name("Beach day")


def test_the_filmed_date_comes_from_the_clip_then_the_name_then_the_server_then_the_file():
    mtime = dt.datetime(2020, 5, 6, 7, 8, 9).timestamp()
    path = "/x/VID_20190101_120000.mp4"
    assert H.derive(path, "", camera_at="2018-02-03T04:05:06", server_at="2017-01-01 00:00:00",
                    mtime=mtime, added_at=None)["filmed_source"] == "camera"
    assert H.derive(path, "", camera_at=None, server_at="2017-01-01 00:00:00", mtime=mtime,
                    added_at=None)["filmed_at"] == "2019-01-01T12:00:00"
    got = H.derive("/x/beach.mp4", "", camera_at=None, server_at="2017-01-01 10:00:00", mtime=mtime, added_at=None)
    assert (got["filmed_at"], got["filmed_source"]) == ("2017-01-01T10:00:00", "server")
    got = H.derive("/x/beach.mp4", "", camera_at=None, server_at=None, mtime=mtime, added_at=None)
    assert (got["filmed_at"], got["filmed_source"]) == ("2020-05-06T07:08:09", "file")


def test_creation_time_is_read_as_local_wall_time():
    # apple's tag carries the zone it was filmed in: that wall time is kept
    assert H.creation_time({"com.apple.quicktime.creationdate": "2026-10-31T18:30:12-0400"}) == "2026-10-31T18:30:12"
    # creation_time is utc: shown in this server's time
    utc = dt.datetime(2026, 10, 31, 22, 30, 12, tzinfo=dt.timezone.utc)
    assert H.creation_time({"creation_time": "2026-10-31T22:30:12.000000Z"}) == H.iso(
        utc.astimezone().replace(tzinfo=None))
    # an unset camera clock is no date
    assert H.creation_time({"creation_time": "1904-01-01T00:00:00.000000Z"}) is None
    assert H.creation_time({}) is None


def test_frames_are_a_few_seconds_in():
    assert H.auto_frame_at(200) == 20.0
    assert H.auto_frame_at(900) == 30.0
    assert H.auto_frame_at(4) == 1.0
    assert H.auto_frame_at(None) == 1.0
    assert H.candidate_frames(100) == [10.0, 30.0, 50.0, 70.0]


# ── the store ────────────────────────────────────────────────────────────

@pytest.fixture
def vdb(tmp_path, monkeypatch):
    db = VideoDatabase(database_path=str(tmp_path / "video.db"))
    monkeypatch.setattr(H, "_server_picks", lambda _db: (None, []))
    H.forget_roots()
    yield db
    H.forget_roots()


def _clip(folder, name, size=1000, when=None):
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(b"\0" * size)
    if when:
        ts = when.timestamp()
        os.utime(p, (ts, ts))
    return p


@pytest.fixture
def family(tmp_path, vdb):
    root = tmp_path / "family"
    _clip(root, "VID_20261031_183012.mp4")
    _clip(root, "VID_20261122_090000.mp4")
    _clip(root / "Holidays" / "2023", "Thanksgiving at Grandma's.mp4", when=dt.datetime(2023, 11, 25, 15, 0))
    _clip(root / "Holidays", "PXL_20191125_101500000.mp4")
    _clip(root / "Beach trip 2024", "day one.mov", when=dt.datetime(2024, 7, 2, 12, 0))
    _clip(root / ".thumbnails", "junk.mp4")
    _clip(root, "notes.txt")
    vdb.set_setting("home_paths", f'["{root.as_posix()}"]')
    H.forget_roots()
    return root


def _conn(vdb):
    return vdb._get_connection()


def _labels(vdb):
    return {r["id"]: r["label"] for r in H.roots(vdb)}


def test_a_folder_scan_finds_the_clips_and_the_albums(vdb, family):
    got = H.scan(vdb)
    assert got["found"] == 5 and got["added"] == 5 and not got["problems"]
    root = H.roots(vdb)[0]["id"]
    c = _conn(vdb)
    try:
        page, total = H.videos(c, [root], _labels(vdb))
        assert total == 5
        # newest filmed first
        assert [v["title"] for v in page] == ["Nov 22, 2026, 9:00 AM", "Oct 31, 2026, 6:30 PM", "Day one",
                                              "Thanksgiving at Grandma's", "Nov 25, 2019, 10:15 AM"]
        assert page[3]["album"] == "2023"
        over = H.overview(c, [root], _labels(vdb), today=dt.date(2026, 11, 25))
    finally:
        c.close()
    assert over["counts"] == {"videos": 5, "first_year": 2019, "last_year": 2026}
    # on this day: the 25th of november, other years, newest first
    assert [v["title"] for v in over["on_this_day"]] == ["Thanksgiving at Grandma's", "Nov 25, 2019, 10:15 AM"]
    names = {a["name"]: a for a in over["albums"]}
    assert set(names) == {"family", "2023", "Holidays", "Beach trip 2024"}
    assert names["2023"]["path"] == "Holidays/2023" and names["family"]["count"] == 2
    assert over["months"][0] == {"month": "2026-11", "count": 1}


def test_an_album_is_oldest_first_and_pages(vdb, family):
    H.scan(vdb)
    root = H.roots(vdb)[0]["id"]
    c = _conn(vdb)
    try:
        key = H.album_key(root, "")
        first, total = H.videos(c, [root], _labels(vdb), album=key, limit=1)
        second, _ = H.videos(c, [root], _labels(vdb), album=key, limit=1, offset=1)
        nov, n = H.videos(c, [root], _labels(vdb), month="2026-11")
        found, _ = H.videos(c, [root], _labels(vdb), q="grandma")
    finally:
        c.close()
    assert total == 2 and first[0]["title"].startswith("Oct 31") and second[0]["title"].startswith("Nov 22")
    assert n == 1 and nov[0]["title"].startswith("Nov 22")
    assert [v["title"] for v in found] == ["Thanksgiving at Grandma's"]


def test_edits_survive_a_rescan_and_null_resets_them(vdb, family):
    H.scan(vdb)
    labels = _labels(vdb)
    c = _conn(vdb)
    try:
        vid = c.execute("SELECT id FROM home_videos WHERE path LIKE '%VID_20261031%'").fetchone()[0]
        H.edit(c, vid, {"title": "Trick or treating", "filmed_at": "2026-10-31T19:00",
                        "notes": "Max as a dinosaur"}, labels)
        c.commit()
    finally:
        c.close()
    # the file changes (re-encoded): looked at again, the person's name kept
    p = next(family.glob("VID_20261031*"))
    p.write_bytes(b"\0" * 5000)
    H.scan(vdb)
    c = _conn(vdb)
    try:
        row = H.get(c, vid)
        assert row["user_title"] == "Trick or treating" and row["notes"] == "Max as a dinosaur"
        assert row["work_state"] == 1 and row["size_bytes"] == 5000
        v = H.video_json(row, labels)
        assert v["title"] == "Trick or treating" and v["filmed_at"] == "2026-10-31T19:00:00"
        assert row["sort_at"] == "2026-10-31T19:00:00"
        H.edit(c, vid, {"title": None, "filmed_at": None}, labels)
        row = H.get(c, vid)
        assert H.video_json(row, labels)["title"] == "Oct 31, 2026, 6:30 PM"
        assert row["sort_at"] == "2026-10-31T18:30:12"
        with pytest.raises(ValueError):
            H.edit(c, vid, {"filmed_at": "last tuesday"}, labels)
    finally:
        c.close()


def test_a_missing_file_is_marked_not_deleted(vdb, family):
    H.scan(vdb)
    root = H.roots(vdb)[0]["id"]
    beach = family / "Beach trip 2024" / "day one.mov"
    beach.unlink()
    got = H.scan(vdb)
    assert got["missing"] == 1
    c = _conn(vdb)
    try:
        row = c.execute("SELECT id, missing_since FROM home_videos WHERE path LIKE '%day one%'").fetchone()
        assert row is not None and row[1]
        _page, total = H.videos(c, [root], _labels(vdb))
        assert total == 4, "out of the timeline"
        album, _ = H.videos(c, [root], _labels(vdb), album=H.album_key(root, "Beach trip 2024"))
        assert album[0]["missing"] is True, "greyed in its album"
    finally:
        c.close()
    # it comes back: the same row, no longer missing
    _clip(family / "Beach trip 2024", "day one.mov")
    got = H.scan(vdb)
    assert got["back"] == 1 and got["added"] == 0
    # a folder that can't be read marks nothing missing
    vdb.set_setting("home_paths", '["/no/such/folder"]')
    H.forget_roots()
    assert H.scan(vdb)["problems"]


def test_long_missing_clips_go_unless_they_carry_your_edits(vdb, family):
    H.scan(vdb)
    c = _conn(vdb)
    try:
        ids = [r[0] for r in c.execute("SELECT id FROM home_videos ORDER BY id")]
        H.edit(c, ids[0], {"notes": "keep me"}, {})
        c.execute("UPDATE home_videos SET missing_since = '2020-01-01T00:00:00'")
        dead = H.purge_missing(c)
        c.commit()
        left = [r[0] for r in c.execute("SELECT id FROM home_videos")]
    finally:
        c.close()
    assert left == [ids[0]] and isinstance(dead, list)


# ── from a media server ──────────────────────────────────────────────────

class _Part:
    def __init__(self, file, key, size):
        self.file, self.key, self.size = file, key, size


class _Media:
    def __init__(self, part):
        self.parts = [part]
        self.width, self.height, self.videoCodec, self.audioCodec = 1920, 1080, "h264", "aac"


class _PlexVideo:
    def __init__(self, key, file, title, when):
        self.ratingKey, self.title, self.duration = key, title, 61_000
        self.originallyAvailableAt = when
        self.media = [_Media(_Part(file, f"/library/parts/{key}/file.mp4", 2048))]


class _Section:
    def __init__(self, title, kind, agent, items=(), locations=()):
        self.title, self.type, self.agent, self.totalSize = title, kind, agent, len(items)
        self._items, self.locations = list(items), list(locations)

    def all(self):
        return list(self._items)


class _Library:
    def __init__(self, sections):
        self._s = sections

    def sections(self):
        return self._s


class _PlexServer:
    def __init__(self, sections):
        self.library = _Library(sections)


def _plex_home():
    from core.video.sources import PlexVideoSource
    home = _Section("Home Videos", "movie", "tv.plex.agents.none", [
        _PlexVideo(11, "/data/home/Birthdays/2022/cake.mp4", "cake", dt.datetime(2022, 3, 4)),
        _PlexVideo(12, "/data/home/VID_20210102_030405.mp4", "VID_20210102_030405", dt.datetime(2021, 1, 2)),
    ], ["/data/home"])
    films = _Section("Movies", "movie", "tv.plex.agents.movie", [], ["/data/movies"])
    return PlexVideoSource(_PlexServer([films, home]))


def test_plex_lists_its_home_libraries_and_they_scan(vdb, monkeypatch):
    src = _plex_home()
    assert src.available_libraries()["home"] == [{"title": "Home Videos", "count": 2}]
    assert [s.title for s in src.home_sections()] == ["Home Videos"]
    monkeypatch.setattr(H, "_server_picks", lambda _db: ("plex", ["Home Videos"]))
    H.forget_roots()
    got = H.scan(vdb, server_source=src)
    assert got["added"] == 2
    root = H.server_root_id("plex", "Home Videos")
    c = _conn(vdb)
    try:
        rows = {r["source_key"]: r for r in (H._row(x) for x in c.execute(f"SELECT {H._COLS} FROM home_videos"))}
    finally:
        c.close()
    assert rows["11"]["folder"] == "Birthdays/2022" and rows["11"]["stream_key"] == "/library/parts/11/file.mp4"
    assert rows["11"]["root"] == root and rows["11"]["filmed_at"] == "2022-03-04T00:00:00"
    assert rows["12"]["title"] == "Jan 2, 2021, 3:04 AM"
    # a library that can't be read marks nothing missing
    monkeypatch.setattr(src, "iter_home", lambda title: (_ for _ in ()).throw(RuntimeError("down")))
    assert H.scan(vdb, server_source=src)["missing"] == 0


def test_jellyfin_home_videos_scan(vdb, monkeypatch):
    from core.video.sources import JellyfinVideoSource

    class _Client:
        user_id = "u1"

        def _make_request(self, path, params=None):
            if path.endswith("/Views"):
                return {"Items": [{"Name": "Family", "Id": "v1", "CollectionType": "homevideos"},
                                  {"Name": "Films", "Id": "v2", "CollectionType": "movies"}]}
            if path == "/Library/VirtualFolders":
                return [{"Name": "Family", "Locations": ["/srv/family"]}]
            return {"Items": [{"Id": "abc", "Name": "beach", "Path": "/srv/family/Summer/beach.mp4",
                               "RunTimeTicks": 300_000_000, "PremiereDate": "2024-07-02T00:00:00.0000000Z",
                               "MediaSources": [{"Path": "/srv/family/Summer/beach.mp4", "Size": 99,
                                                 "MediaStreams": [{"Type": "Video", "Codec": "hevc",
                                                                   "Width": 3840, "Height": 2160}]}]}],
                    "TotalRecordCount": 1}

    src = JellyfinVideoSource(_Client())
    assert [x["title"] for x in src.available_libraries()["home"]] == ["Family"]
    monkeypatch.setattr(H, "_server_picks", lambda _db: ("jellyfin", ["Family"]))
    H.forget_roots()
    assert H.scan(vdb, server_source=src)["added"] == 1
    c = _conn(vdb)
    try:
        row = H._row(c.execute(f"SELECT {H._COLS} FROM home_videos").fetchone())
    finally:
        c.close()
    assert (row["folder"], row["duration_s"], row["height"], row["title"]) == ("Summer", 30.0, 2160, "Beach")


# ── libraries, the guide ─────────────────────────────────────────────────

@pytest.fixture
def pdb(tmp_path):
    from database.player_database import PlayerDatabase
    return PlayerDatabase(str(tmp_path / "player.db"))


def test_the_home_library_comes_with_home_videos_and_stays_out_of_the_guide(pdb, vdb, family):
    assert pl.ensure_home(pdb, vdb) is not None
    libs = pl.libraries(pdb)
    home = next(x for x in libs if x["kind"] == "home")
    assert home["name"] == "Home videos" and home["is_default"] and home["in_guide"] is False
    assert all(x["in_guide"] for x in libs if x["kind"] != "home")
    seen = {x["kind"]: x for x in pl.visible(pdb, 3)}
    assert seen["home"]["own_page"] is True and seen["home"]["in_guide"] is False
    # an app that saves movies and TV only leaves the home library alone
    pl.save(pdb, vdb, [{"id": 1, "kind": "movie", "name": "Movies", "folders": [], "in_guide": False},
                       {"id": 2, "kind": "show", "name": "TV Shows", "folders": []}])
    after = {x["kind"]: x for x in pl.libraries(pdb)}
    assert after["home"]["id"] == home["id"] and after["movie"]["in_guide"] is False
    assert pl.guide_off(pdb) == frozenset({1}), "a home library is never in the guide's catalog"
    # a split: a second home library with the holidays folder... the root is the whole folder
    root = H.roots(vdb)[0]["id"]
    saved = pl.save(pdb, vdb, [{"id": 1, "kind": "movie", "name": "Movies", "folders": []},
                               {"id": 2, "kind": "show", "name": "TV Shows", "folders": []},
                               {"id": home["id"], "kind": "home", "name": "Family", "folders": []},
                               {"kind": "home", "name": "Kids", "folders": [root], "in_guide": True}])
    kids = next(x for x in saved if x["name"] == "Kids")
    assert kids["in_guide"] is True and pl.home_library_of(saved, root) == kids["id"]
    with pytest.raises(pl.LibraryError):
        pl.save(pdb, vdb, [{"id": 1, "kind": "movie", "name": "Movies", "folders": []},
                           {"id": 2, "kind": "show", "name": "TV Shows", "folders": []}, kids])


def test_the_guide_pool_skips_libraries_kept_out_of_it():
    cat = {"movies": [[1, "A", 5], [2, "B", 9]], "shows": [[10, "S", 9]],
           "recent_episodes": [{"show_id": 10}], "youtube": []}
    out = player_api.guide_pool(cat, frozenset({9}))
    assert [m[0] for m in out["movies"]] == [1] and out["shows"] == [] and out["recent_episodes"] == []
    assert player_api.guide_pool(cat, frozenset()) is cat


def test_playback_downloads_and_devices_know_home():
    from core.connect.pick import KIND_ITEM_TYPES
    from core.player import downloads, offline_catalog as C
    from core.player.playable import KINDS
    assert KINDS["home"].permissions == ("movies.access", "tv.access")
    assert "home" in C.VIDEO_KINDS and C.GROUP_ITEM["home_album"] == "home" and C.kind_of("home") == "video"
    assert any("home" in kinds for kinds in KIND_ITEM_TYPES.values())
    assert "home" in downloads.VALID_TYPES
    assert C.valid_quality("home", "phone")


# ── the routes ───────────────────────────────────────────────────────────

@pytest.fixture
def wired(pdb, vdb, family, monkeypatch):
    H.scan(vdb)
    home_id = pl.ensure_home(pdb, vdb)
    monkeypatch.setattr(home_api, "_vdb", lambda: vdb)
    monkeypatch.setattr(home_api, "_pdb", lambda: pdb)
    monkeypatch.setattr(player_api, "player_db", lambda: pdb)
    import api.video as videoapi
    monkeypatch.setattr(videoapi, "get_video_db", lambda: vdb)
    hidden = {"ids": frozenset()}
    monkeypatch.setattr(home_access, "hidden_now", lambda: hidden["ids"])
    return {"home": home_id, "hidden": hidden, "vdb": vdb, "pdb": pdb}


def _client(profile_id=2, admin=False):
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        g.profile_id = profile_id
        g.is_admin = admin

    app.register_blueprint(home_api.create_blueprint())
    return app.test_client()


def test_the_page_reads_and_pages(wired):
    c = _client()
    over = c.get("/api/player/home/overview").get_json()
    assert over["success"] and over["library"] == {"id": wired["home"], "name": "Home videos", "in_guide": False}
    assert over["counts"]["videos"] == 5 and over["can_edit"] is False
    assert {"key", "name", "count", "first", "last", "covers"} <= set(over["albums"][0])
    first = c.get(f"/api/player/home/videos?library={wired['home']}&limit=2").get_json()
    rest = c.get(f"/api/player/home/videos?library={wired['home']}&limit=2&offset=2").get_json()
    assert first["total"] == 5 and len(first["videos"]) == 2 and len(rest["videos"]) == 2
    assert not {v["id"] for v in first["videos"]} & {v["id"] for v in rest["videos"]}
    v = first["videos"][0]
    assert set(v) >= {"id", "title", "filmed_at", "duration_s", "width", "height", "album", "album_key",
                      "thumb_url", "progress", "missing"}
    assert c.get("/api/player/home/videos?month=nope").status_code == 400
    one = c.get(f"/api/player/home/video/{v['id']}").get_json()
    assert one["file_name"].startswith("VID_20261122") and len(one["frames"]) == 4
    assert c.get("/api/player/home/overview?library=999").status_code == 404


def test_a_library_hidden_from_a_profile_is_gone_for_it(wired):
    wired["hidden"]["ids"] = frozenset({wired["home"]})
    c = _client()
    assert c.get("/api/player/home/overview").status_code == 404
    vid = wired["vdb"]._get_connection().execute("SELECT MIN(id) FROM home_videos").fetchone()[0]
    assert c.get(f"/api/player/home/video/{vid}").status_code == 404
    assert c.get(f"/api/player/home/thumb/{vid}").status_code == 404


def test_only_admins_edit_unless_setup_says_everyone(wired):
    from core.player import setup as player_setup
    vid = wired["vdb"]._get_connection().execute("SELECT MIN(id) FROM home_videos").fetchone()[0]
    member, admin = _client(), _client(1, admin=True)
    assert member.put(f"/api/player/home/video/{vid}", json={"title": "Mine"}).status_code == 403
    r = admin.put(f"/api/player/home/video/{vid}", json={"title": "Trick or treating", "notes": "Max"})
    assert r.status_code == 200 and r.get_json()["title"] == "Trick or treating"
    assert r.get_json()["edited"]["title"] is True
    assert admin.put(f"/api/player/home/video/{vid}", json={"filmed_at": "soon"}).status_code == 400
    assert admin.put(f"/api/player/home/video/{vid}", json={"thumb_at_s": -4}).status_code == 400
    player_setup.put(wired["pdb"], {"home_editors": "everyone"})
    assert member.put(f"/api/player/home/video/{vid}", json={"title": None}).status_code == 200
    # the sources are an admin's
    assert member.get("/api/player/home/sources").status_code == 403
    assert admin.get("/api/player/home/sources").get_json()["folders"][0]["videos"] == 5


def test_folder_check_and_sources_save(wired, tmp_path):
    admin = _client(1, admin=True)
    new = tmp_path / "camcorder"
    _clip(new, "tape1.avi")
    r = admin.post("/api/player/home/folder-check", json={"path": str(new)}).get_json()
    assert r["ok"] and r["videos"] == 1 and r["message"] == "Found 1 video."
    assert admin.post("/api/player/home/folder-check", json={"path": str(tmp_path / "nope")}).get_json()["ok"] is False
    started = []
    home_api_H = H
    orig = home_api_H.scan_async
    try:
        home_api_H.scan_async = lambda db, only=None: started.append(only) or True
        folders = [f["path"] for f in admin.get("/api/player/home/sources").get_json()["folders"]]
        out = admin.put("/api/player/home/sources", json={"folders": [*folders, str(new)]}).get_json()
    finally:
        home_api_H.scan_async = orig
    assert len(out["folders"]) == 2 and started == [H._norm(str(new))], "just the new folder is read"


# ── playing one ──────────────────────────────────────────────────────────

def test_a_home_video_plays_from_disk_for_who_can_see_it(wired, monkeypatch):
    from core.player.playable import Refused
    from core.player.sources import home as home_source
    vid = wired["vdb"]._get_connection().execute("SELECT MIN(id) FROM home_videos").fetchone()[0]
    app = Flask(__name__)
    with app.test_request_context():
        src = home_source.locate(str(vid))
        assert src.mode == "file" and os.path.isfile(src.path)
        wired["hidden"]["ids"] = frozenset({wired["home"]})
        with pytest.raises(Refused) as e:
            home_source.locate(str(vid))
        assert e.value.status == 404
        with pytest.raises(Refused):
            home_source.locate("abc")


def test_offline_takes_a_home_album(wired):
    from core.player import offline_catalog as C
    root = H.roots(wired["vdb"])[0]["id"]
    app = Flask(__name__)
    with app.test_request_context():
        g.profile_id = 2
        g.is_admin = True
        header, items = C.expand("home_album", H.album_key(root, "Holidays"))
        assert header["title"] == "Holidays" and [i["item_type"] for i in items] == ["home"]
        assert items[0]["group"]["type"] == "home_album"
        one_header, one = C.expand("home", items[0]["item_id"])
        assert one[0]["item_id"] == items[0]["item_id"]


# ── real clips, real ffmpeg ──────────────────────────────────────────────

def _ffmpeg():
    return home_media.ffmpeg()


def test_frames_and_creation_time_from_real_clips(vdb, tmp_path, monkeypatch):
    import subprocess
    ff = _ffmpeg()
    if not ff:
        pytest.skip("no ffmpeg here")
    folder = tmp_path / "phone"
    folder.mkdir()
    clip = folder / "VID_20200101_000000.mp4"
    run = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                          "testsrc=duration=3:size=320x240:rate=10", "-pix_fmt", "yuv420p",
                          "-metadata", "creation_time=2021-06-05T14:30:00.000000Z", "-y", str(clip)],
                         capture_output=True, timeout=120)
    if run.returncode != 0:
        pytest.skip("ffmpeg couldn't make a clip here")
    vdb.set_setting("home_paths", f'["{folder.as_posix()}"]')
    H.forget_roots()
    H.scan(vdb)
    assert home_media.run_all(vdb) == 1
    c = vdb._get_connection()
    try:
        row = H._row(c.execute(f"SELECT {H._COLS} FROM home_videos").fetchone())
    finally:
        c.close()
    expected = H.creation_time({"creation_time": "2021-06-05T14:30:00Z"})
    # the clip's own creation time beats the date in its name
    assert row["filmed_at"] == expected and row["filmed_source"] == "camera"
    assert (row["width"], row["height"]) == (320, 240) and 2.5 < row["duration_s"] < 3.5
    assert row["work_state"] == 0 and os.path.getsize(row["thumb_path"]) > 500
    assert row["thumb_at_s"] == 1.0
    # a candidate frame for the edit sheet, then picked: the picture at once
    frame = home_media.candidate(vdb, row, 2.0)
    assert frame is not None and frame.exists()
    c = vdb._get_connection()
    try:
        H.edit(c, row["id"], {"thumb_at_s": 2.0}, {})
        c.commit()
        picked = H.get(c, row["id"])
    finally:
        c.close()
    assert home_media.use_frame(vdb, picked)
    c = vdb._get_connection()
    try:
        assert H.get(c, row["id"])["thumb_at_s"] == 2.0
    finally:
        c.close()


def test_probe_reads_a_sideways_phone_clip_upright():
    got = home_media.parse_probe({
        "format": {"duration": "12.5", "tags": {"com.apple.quicktime.creationdate": "2024-07-04T21:05:00-0700"}},
        "streams": [{"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080,
                     "side_data_list": [{"rotation": -90}]},
                    {"codec_type": "audio", "codec_name": "aac"}]})
    assert (got["width"], got["height"], got["duration_s"]) == (1080, 1920, 12.5)
    assert got["camera_at"] == "2024-07-04T21:05:00" and got["video_codec"] == "hevc"


def test_the_background_worker_gives_up_after_three_tries(vdb, family, monkeypatch):
    H.scan(vdb)
    monkeypatch.setattr(home_media, "ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(home_media, "ffprobe", lambda ff=None: "ffprobe")
    monkeypatch.setattr(home_media, "probe", lambda b, s: None)
    t = time.monotonic()
    assert home_media.run_all(vdb) == 0 and time.monotonic() - t < 30
    c = vdb._get_connection()
    try:
        assert c.execute("SELECT COUNT(*) FROM home_videos WHERE work_state > 0").fetchone()[0] == 0
        assert {r[0] for r in c.execute("SELECT work_tries FROM home_videos")} == {3}
    finally:
        c.close()


def test_reading_the_libraries_never_makes_a_table(tmp_path):
    from database.player_database import PlayerDatabase
    db = PlayerDatabase(str(tmp_path / "p.db"))
    assert {x["kind"] for x in pl.libraries(db)} == {"movie", "show"}
    with db._get_connection() as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert pl.LIBS not in names
    assert isinstance(sqlite3.sqlite_version, str)


def test_a_server_home_library_knows_the_folder_its_clips_share():
    import sqlite3

    from core.video import home_videos as H
    conn = sqlite3.connect(":memory:")
    conn.execute(f"CREATE TABLE {H.TABLE} (root TEXT, path TEXT, missing_since TEXT)")
    conn.executemany(f"INSERT INTO {H.TABLE} VALUES (?, ?, NULL)", [
        ("plex:1", "/mnt/easystore3/PLEX/Home Movies/1994/a.mpg"),
        ("plex:1", "/mnt/easystore3/PLEX/Home Movies/2001/b.mp4"),
        ("plex:2", "E:\\Clips\\Home Mov1.mp4"),
        ("plex:2", "E:\\Clips\\Home Mov2.mp4"),
        ("plex:3", "/one/only/clip.mp4"),
    ])
    got = H.root_places(conn, ["plex:1", "plex:2", "plex:3"])
    assert got == {"plex:1": "/mnt/easystore3/PLEX/Home Movies", "plex:2": "E:/Clips",
                   "plex:3": "/one/only"}
