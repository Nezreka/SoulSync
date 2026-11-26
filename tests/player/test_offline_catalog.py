"""player downloads: what a group is made of, its qualities and sizes, and
the commands and names the server's copies get (offline_catalog.py,
offline_jobs.py). a real video library schema, no ffmpeg."""

from __future__ import annotations

import sqlite3

import pytest

from core.player import offline_catalog as C
from core.player import offline_jobs as J
from core.player.playable import Refused


@pytest.fixture
def video(tmp_path, monkeypatch):
    path = tmp_path / "video.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(open("database/video_schema.sql", encoding="utf-8").read())
        conn.execute("INSERT INTO shows (id, title, year, content_rating) VALUES (7, 'Severance', 2022, 'TV-MA')")
        conn.execute("INSERT INTO shows (id, title, year, content_rating) VALUES (8, 'Bluey', 2018, 'TV-Y')")
        conn.execute("INSERT INTO seasons (id, show_id, season_number) VALUES (70, 7, 1), (71, 7, 2), (72, 7, 0)")
        conn.execute("INSERT INTO seasons (id, show_id, season_number) VALUES (80, 8, 1)")
        eps = [(101, 7, 71, 2, 2), (102, 7, 71, 2, 1), (103, 7, 71, 2, 3), (104, 7, 70, 1, 1),
               (105, 7, 72, 0, 1), (201, 8, 80, 1, 1)]
        for eid, sid, season_row, sn, en in eps:
            conn.execute("INSERT INTO episodes (id, show_id, season_id, season_number, episode_number, title, "
                         "has_file) VALUES (?, ?, ?, ?, ?, ?, ?)", (eid, sid, season_row, sn, en, f"Ep {en}",
                                                                   0 if eid == 103 else 1))
        for eid, res, size in ((101, "2160p", 9_000_000_000), (102, "1080p", 3_000_000_000),
                               (104, "720p", 1_000_000_000), (105, "1080p", 2_000_000_000),
                               (201, "1080p", 500_000_000)):
            conn.execute("INSERT INTO media_files (episode_id, relative_path, size_bytes, resolution, "
                         "video_codec, audio_codec, runtime_seconds) VALUES (?, ?, ?, ?, 'hevc', 'eac3', 3000)",
                         (eid, f"/tv/{eid}.mkv", size, res))
        # a smaller second version of 101: the biggest is the one counted
        conn.execute("INSERT INTO media_files (episode_id, relative_path, size_bytes, resolution, "
                     "runtime_seconds) VALUES (101, '/tv/101-small.mp4', 100, '480p', 3000)")
        conn.execute("INSERT INTO movies (id, title, year, content_rating, runtime_minutes) "
                     "VALUES (5, 'Dune: Part Two', 2024, 'PG-13', 166)")
        conn.execute("INSERT INTO media_files (movie_id, relative_path, size_bytes, resolution, video_codec, "
                     "audio_codec, runtime_seconds, dynamic_range) VALUES (5, '/m/dune.mkv', 60000000000, "
                     "'2160p', 'hevc', 'truehd', 9960, 'HDR10')")

    class DB:
        def _get_connection(self):
            return sqlite3.connect(path)

    monkeypatch.setattr(C, "_video_db", lambda: DB())
    monkeypatch.setattr(C, "check", lambda kind: None)
    caps = {"cap": None}
    monkeypatch.setattr(C, "_cap", lambda: caps["cap"])
    return caps


def test_a_season_is_its_episodes_with_files_in_order(video):
    header, items = C.expand("season", "7:2")
    assert header["title"] == "Severance" and header["subtitle"] == "Season 2"
    assert header["artwork"] == "/api/video/poster/season/71?w=342"
    assert [i["item_id"] for i in items] == ["102", "101"]
    assert items[1]["size_bytes"] == 9_000_000_000 and items[1]["source"]["height"] == 2160
    assert items[0]["title"] == "S2 E1 · Ep 1" and items[0]["group"]["id"] == "7:2"


def test_a_show_puts_specials_last(video):
    _h, items = C.expand("show", "7")
    assert [i["item_id"] for i in items] == ["104", "102", "101", "105"]


def test_the_rating_cap_holds(video):
    video["cap"] = 7
    with pytest.raises(Refused) as e:
        C.expand("season", "7:2")
    assert e.value.status == 403
    assert [i["item_id"] for i in C.expand("show", "8")[1]] == ["201"]
    with pytest.raises(Refused):
        C.expand("movie", "5")


def test_a_movie_and_its_qualities(video):
    header, items = C.expand("movie", "5")
    assert header["title"] == "Dune: Part Two" and items[0]["year"] == 2024
    qs = C.qualities("video", items)
    assert [q["id"] for q in qs] == ["original", "high", "phone", "small"]
    assert qs[0]["sub"] == "4K HDR, as it is on the server" and qs[0]["total_bytes"] == 60_000_000_000
    assert qs[1]["total_bytes"] == int(9960 * (8_000_000 + 160_000) / 8)


def test_bad_ids_are_refused(video):
    for gtype, gid in (("season", "x"), ("nope", "1"), ("movie", ""), ("show", "999")):
        with pytest.raises(Refused):
            C.expand(gtype, gid)


def test_describe_finds_items_without_checking(video):
    video["cap"] = 7
    out = C.describe([("episode", "101"), ("movie", "5"), ("episode", "999")])
    assert set(out) == {"episode:101", "movie:5"}


def test_a_copy_is_never_bigger_than_the_original():
    item = {"item_type": "track", "item_id": "1", "duration_s": 200, "size_bytes": 1000, "source": {}}
    assert C.estimate(item, "320") == 1000
    item["size_bytes"] = 0
    assert C.estimate(item, "128") == 200 * 128_000 // 8


def test_names_are_clean():
    assert J.filename({"item_type": "movie", "title": 'Dune: Part Two?', "year": 2024}, "high", ".mp4") \
        == "Dune Part Two (2024) [1080p].mp4"
    assert J.filename({"item_type": "track", "title": "Song/One", "subtitle": "Band · Album"}, "256", ".m4a") \
        == "Band - Song One [256k].m4a"
    assert J.clean_name("..\\..//") == "Download"


def test_progress_lines():
    assert J.parse_progress("out_time_us=1500000", 3.0) == 0.5
    assert J.parse_progress("out_time_ms=3000000", 3.0) == 1.0
    assert J.parse_progress("frame=10", 3.0) is None
    assert J.parse_progress("out_time_us=N/A", 3.0) is None


def test_audio_copies_keep_chapters():
    cmd = J._audio_cmd("ffmpeg", "/b/book.m4b", "/c/out.part", "128", ".m4b")
    assert cmd[cmd.index("-b:a") + 1] == "128k" and cmd[cmd.index("-map_chapters") + 1] == "0"
    assert cmd[cmd.index("-i") + 1] == "/b/book.m4b" and cmd[-1] == "/c/out.part"


def test_a_book_in_many_files_is_joined_with_its_chapters(tmp_path, monkeypatch):
    parts = []
    for n in range(2):
        p = tmp_path / f"part {n}'s.mp3"
        p.write_bytes(b"x")
        parts.append(str(p))
    import core.player.sources.audiobook as A
    monkeypatch.setattr(A, "timeline", lambda files: (
        [], [{"start": 0.0, "title": "Opening"}, {"start": 600.0, "title": "Chapter = 2"}], 1200.0))
    monkeypatch.setattr(J, "_ffmpeg_bin", lambda: "ffmpeg")
    seen = {}

    def run(cmd, duration, progress):
        seen["cmd"] = cmd
        seen["list"] = open(cmd[cmd.index("-i") + 1], encoding="utf-8").read()
        seen["meta"] = open(cmd[cmd.index("-i", cmd.index("-i") + 1) + 1], encoding="utf-8").read()
        seen["duration"] = duration

    monkeypatch.setattr(J, "_ffmpeg", run)
    job = J.Job(id="b1", kind="join", media="audio", quality="original", out=str(tmp_path / "book.mp3"),
                item_type="audiobook", item_id="B01", parts=parts, ext=".mp3")
    J._join(job, str(tmp_path / "book.mp3.part"), lambda f: None)
    cmd = seen["cmd"]
    assert cmd[cmd.index("-c:a") + 1] == "copy" and "mp3" in cmd and cmd[cmd.index("-map_chapters") + 1] == "1"
    assert "'\\''s.mp3'" in seen["list"]
    assert "START=600000" in seen["meta"] and "title=Chapter \\= 2" in seen["meta"]
    assert seen["duration"] == 1200.0
    # the helper files are gone after
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                                                                for p in parts)
