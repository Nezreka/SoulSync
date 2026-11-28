"""chapter pictures: which moments are tried, which frame wins (never a
black one or a fade), the worker doing the newest and most watched first,
skipping what's done and picking up where it stopped, and the routes that
serve them only to a profile that may watch the video."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess

import numpy as np
import pytest
from flask import Flask, g
from PIL import Image

from core.player import chapter_thumbs as C
from core.player.playable import Refused, Source


@pytest.fixture(autouse=True)
def own_root(tmp_path):
    C.set_root(tmp_path / "chapters")
    yield tmp_path / "chapters"
    C.set_root(None)


def ppm(arr) -> bytes:
    """a frame as ffmpeg would hand it over (ppm on stdout)"""
    buf = io.BytesIO()
    Image.fromarray(np.asarray(arr, dtype=np.uint8)).convert("RGB").save(buf, "PPM")
    return buf.getvalue()


BLACK = np.zeros((180, 320), dtype=np.uint8)
FLAT = np.full((180, 320), 120, dtype=np.uint8)
SHARP = (np.indices((180, 320)).sum(axis=0) % 2 * 160 + 40).astype(np.uint8)
SOFT = np.tile(np.linspace(40, 200, 320, dtype=np.uint8), (180, 1))


# ── the moments ──────────────────────────────────────────────────────────

def test_a_couple_of_seconds_in_then_further():
    assert C.frame_offsets(100, 400) == [102.0, 105.0, 109.0, 115.0, 175.0]
    # an open end (the last chapter) leans on the file's length
    assert C.frame_offsets(100, None, 400) == [102.0, 105.0, 109.0, 115.0, 175.0]
    assert C.frame_offsets(0, None) == [2.0, 5.0, 9.0, 15.0]


def test_never_into_the_next_chapters_fade():
    assert C.frame_offsets(0, 6) == [2.0, 5.0]
    assert C.frame_offsets(10, 19) == [12.0, 15.0]
    # too short for any: its middle
    assert C.frame_offsets(10, 12) == [11.0]


def test_a_long_chapter_also_looks_deeper_but_not_too_deep():
    assert C.frame_offsets(0, 3600)[-1] == 90.0


# ── the frame ────────────────────────────────────────────────────────────

def test_black_flat_and_blown_out_frames_are_not_usable():
    assert C.score_frame(BLACK)[0] is False
    assert C.score_frame(FLAT)[0] is False
    assert C.score_frame(np.full((180, 320), 250, dtype=np.uint8))[0] is False
    usable, score = C.score_frame(SHARP)
    assert usable and C.good_enough(usable, score)


def test_sharp_beats_soft():
    assert C.score_frame(SHARP)[1] > C.score_frame(SOFT)[1]


def test_letterbox_bars_dont_make_a_frame_black():
    framed = SHARP.copy()
    framed[:22] = 0
    framed[-22:] = 0
    assert C.score_frame(framed)[0] is True


def test_the_first_clearly_good_frame_wins_past_black_and_fades():
    asked = []
    frames = {2.0: BLACK, 5.0: FLAT, 9.0: SHARP, 15.0: SHARP}
    picked = C.best_frame(lambda at: asked.append(at) or ppm(frames[at]), [2.0, 5.0, 9.0, 15.0])
    assert picked is not None and picked[1] == 9.0
    assert asked == [2.0, 5.0, 9.0]   # stopped at the good one


def test_nothing_good_means_the_best_of_the_bad():
    frames = {2.0: BLACK, 5.0: SOFT, 9.0: FLAT}
    picked = C.best_frame(lambda at: ppm(frames[at]), [2.0, 5.0, 9.0])
    assert picked[1] == 5.0


def test_a_moment_with_no_frame_is_skipped():
    def grab(at):
        if at == 2.0:
            raise RuntimeError("past the end")
        return ppm(SHARP)
    assert C.best_frame(grab, [2.0, 5.0])[1] == 5.0
    assert C.best_frame(lambda at: b"not a picture", [2.0]) is None


def test_the_grab_command_seeks_first_and_scales_to_320():
    cmd = C.grab_command("ffmpeg", "/m/a.mkv", 12.5)
    assert cmd.index("-ss") < cmd.index("-i") and cmd[cmd.index("-ss") + 1] == "12.500"
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=320:-2" in vf and "setsar=1" in vf and "tonemap" not in vf
    assert cmd[cmd.index("-threads") + 1] == "1" and cmd[cmd.index("-frames:v") + 1] == "1"
    hdr = C.grab_command("ffmpeg", "/m/a.mkv", 1, hdr=True, tonemap=True)
    assert "tonemap=hable" in hdr[hdr.index("-vf") + 1]


# ── one file ─────────────────────────────────────────────────────────────

CHAPTERS = [{"start": 0.0, "end": 300.0, "title": "Opening"},
            {"start": 300.0, "end": 900.0, "title": "Chapter 2"},
            {"start": 900.0, "end": None, "title": "Credits"}]


def fake_run(calls, frame=SHARP):
    def run(cmd):
        calls.append(float(cmd[cmd.index("-ss") + 1]))
        return ppm(frame)
    return run


def test_one_picture_per_chapter_and_a_manifest():
    calls = []
    key = C.file_key("/movies/Alien (1979).mkv", 123)
    m = C.make_for_file("ffmpeg", "/m/Alien (1979).mkv", key, chapters=CHAPTERS, duration=7000,
                        run=fake_run(calls))
    assert [c["thumb"] for c in m["chapters"]] == ["000.webp", "001.webp", "002.webp"]
    assert calls == [2.0, 302.0, 902.0]
    p = C.thumb_path(key, 1)
    assert p is not None and Image.open(p).size == (320, 180)
    assert C.is_done(C.read_manifest(key))
    assert C.thumb_path(key, 3) is None and C.thumb_path(key, -1) is None and C.thumb_path(key, "x") is None


def test_a_file_without_chapters_gets_none_and_is_done():
    key = C.file_key("a.mkv", 5)
    m = C.make_for_file("ffmpeg", "/m/a.mkv", key, chapters=[CHAPTERS[0]], run=fake_run([]))
    assert m["chapters"] == [] and C.is_done(C.read_manifest(key))
    assert C.thumb_path(key, 0) is None


def test_an_interrupted_file_keeps_what_it_made():
    calls = []
    key = C.file_key("b.mkv", 9)
    stops = iter([False, False, True])
    assert C.make_for_file("ffmpeg", "/m/b.mkv", key, chapters=CHAPTERS, run=fake_run(calls),
                           should_stop=lambda: next(stops)) is None
    assert C.read_manifest(key) is None and calls == [2.0, 302.0]
    calls.clear()
    m = C.make_for_file("ffmpeg", "/m/b.mkv", key, chapters=CHAPTERS, run=fake_run(calls))
    assert calls == [902.0] and len([c for c in m["chapters"] if c["thumb"]]) == 3


def test_pictures_from_an_older_picker_are_made_again():
    key = C.file_key("c.mkv", 9)
    C.make_for_file("ffmpeg", "/m/c.mkv", key, chapters=CHAPTERS, run=fake_run([]))
    path = C.folder(key) / "manifest.json"
    data = json.loads(path.read_text())
    data["v"] = C.VERSION - 1
    path.write_text(json.dumps(data))
    assert not C.is_done(C.read_manifest(key))
    calls = []
    C.make_for_file("ffmpeg", "/m/c.mkv", key, chapters=CHAPTERS, run=fake_run(calls))
    assert calls == [2.0, 302.0, 902.0]


def test_a_failing_file_is_tried_three_times_then_left():
    key = C.file_key("d.mkv", 9)
    for n in range(1, 4):
        assert not C.is_done(C.read_manifest(key))
        assert C.record_failure(key, "d.mkv", "boom")["attempts"] == n
    assert C.is_done(C.read_manifest(key))


def test_pictures_land_on_the_chapter_they_were_made_for():
    key = C.file_key("e.mkv", 9)
    m = C.make_for_file("ffmpeg", "/m/e.mkv", key, chapters=CHAPTERS, run=fake_run([]))
    # the file's chapters as the player reads them now: one moved, one new
    now = [{"start": 0.2, "title": "Opening"}, {"start": 450.0, "title": "New"},
           {"start": 900.0, "title": "Credits"}]
    out = C.attach(now, m, item_type="movie", item_id=7, file_id=3)
    assert out[0]["thumb"] == f"/api/player/chapter-thumb/movie/7/0?part=3&v={m['made_at']}"
    assert out[1]["thumb"] is None
    assert out[2]["thumb"].startswith("/api/player/chapter-thumb/movie/7/2?")
    assert C.attach(now, None, item_type="movie", item_id=7, file_id=3)[0]["thumb"] is None


def test_prune_drops_pictures_of_files_that_are_gone(own_root):
    keep = C.file_key("keep.mkv", 1)
    gone = C.file_key("gone.mkv", 1)
    for k in (keep, gone):
        C.make_for_file("ffmpeg", "/m/x.mkv", k, chapters=CHAPTERS, run=fake_run([]))
    assert C.prune([keep]) == 0   # too fresh: might be in the making
    old = 1_000_000
    os.utime(C.folder(gone), (old, old))
    os.utime(C.folder(keep), (old, old))
    assert C.prune([keep]) == 1
    assert C.folder(keep).is_dir() and not C.folder(gone).exists()


# ── the worker ───────────────────────────────────────────────────────────

@pytest.fixture
def vdb(tmp_path):
    from database.video_database import VideoDatabase
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _movie(db, mid, tmdb, path, *, size=1000, added="2026-01-01 00:00:00", plays=0):
    conn = db._get_connection()
    conn.execute("INSERT INTO movies (id, server_source, server_id, tmdb_id, title, has_file) "
                 "VALUES (?, 'plex', ?, ?, ?, 1)", (mid, f"s{mid}", tmdb, f"M{mid}"))
    conn.execute("INSERT INTO media_files (id, movie_id, relative_path, size_bytes, added_at) "
                 "VALUES (?, ?, ?, ?, ?)", (mid, mid, path, size, added))
    if plays:
        conn.execute("INSERT INTO video_watch_state (profile_id, kind, item_id, play_count) "
                     "VALUES (1, 'movie', ?, ?)", (mid, plays))
    conn.commit()
    conn.close()


def _worker(vdb, *, busy=lambda: None, probe=None, done=None, calls=None):
    done = done if done is not None else []
    return C.ChapterThumbWorker(
        lambda: vdb, ffmpeg=lambda: "ffmpeg",
        resolve=lambda db, f: f["path"],
        probe=probe or (lambda ff, path: done.append(path) or {"chapters": CHAPTERS,
                                                                "format": {"duration": 7000}}),
        busy_check=busy, run=fake_run(calls if calls is not None else []))


def test_newest_then_most_watched_first(vdb):
    _movie(vdb, 1, 101, "/m/old-unwatched.mkv", added="2020-01-01 00:00:00")
    _movie(vdb, 2, 102, "/m/old-favourite.mkv", added="2020-01-01 00:00:00", plays=9)
    _movie(vdb, 3, 103, "/m/old-once.mkv", added="2021-01-01 00:00:00", plays=1)
    _movie(vdb, 4, 104, "/m/tonight.mkv", added="2099-01-01 00:00:00")
    order = [os.path.basename(f["path"]) for f in C.candidates(vdb)]
    assert order == ["tonight.mkv", "old-favourite.mkv", "old-once.mkv", "old-unwatched.mkv"]


def test_a_library_switched_off_is_skipped(vdb):
    from core.video import libraries as L
    _movie(vdb, 1, 101, "/m/a.mkv")
    movies = next(lib for lib in L.list_libraries(vdb, "movie") if lib["is_default"])
    assert movies["options"]["chapter_thumbs"] is True
    L.update_library(vdb, movies["id"], {"chapter_thumbs": False})
    assert C.candidates(vdb) == []


def test_the_worker_skips_done_files_and_resumes(vdb):
    for i in range(1, 4):
        _movie(vdb, i, 100 + i, f"/m/{i}.mkv", size=1000 + i)
    done = []
    w = _worker(vdb, done=done)
    assert w.run_pass(limit=2) is False          # stopped after two
    assert len(done) == 2
    assert w.run_pass() is True                  # the third, then the end
    assert len(done) == 3
    assert w.run_pass() is True and len(done) == 3   # nothing left: nothing read
    assert w.status["made"] == 3


def test_the_worker_waits_while_busy(vdb):
    _movie(vdb, 1, 101, "/m/a.mkv")
    done = []
    w = _worker(vdb, busy=lambda: "someone is watching", done=done)
    assert w.run_pass() is False and done == []
    assert w.status["waiting_for"] == "someone is watching"


def test_a_scan_starting_mid_file_stops_it_and_the_next_pass_finishes(vdb):
    _movie(vdb, 1, 101, "/m/a.mkv")
    state = {"busy": None, "grabs": 0}
    calls = []

    def run(cmd):
        state["grabs"] += 1
        if state["grabs"] == 2:
            state["busy"] = "a scan or sync is running"
        return fake_run(calls)(cmd)

    w = _worker(vdb, busy=lambda: state["busy"])
    w._run_cmd = run
    assert w.run_pass() is False
    key = C.file_key("/m/a.mkv", 1000)
    assert C.read_manifest(key) is None and len(calls) == 2
    state["busy"] = None
    assert w.run_pass() is True
    assert len(calls) == 3 and C.is_done(C.read_manifest(key))


def test_an_unreadable_file_is_recorded_not_fatal(vdb):
    _movie(vdb, 1, 101, "/m/bad.mkv", size=1)
    _movie(vdb, 2, 102, "/m/good.mkv", size=2)

    def probe(ff, path):
        if "bad" in path:
            raise RuntimeError("ffprobe couldn't read the file")
        return {"chapters": CHAPTERS, "format": {}}

    w = _worker(vdb, probe=probe)
    assert w.run_pass() is True
    assert C.read_manifest(C.file_key("bad.mkv", 1))["attempts"] == 1
    assert C.is_done(C.read_manifest(C.file_key("good.mkv", 2)))


def test_a_scan_or_sync_hold_means_busy(monkeypatch):
    from core import background_pause
    monkeypatch.setattr(C, "_server_scan", lambda: None)
    monkeypatch.setattr(C, "_folder_scan", lambda: None)
    monkeypatch.setattr(C, "_watching", lambda: None)
    monkeypatch.setattr(C, "_converting", lambda: None)
    assert C.busy() is None
    with background_pause.paused_for("database scan"):
        assert C.busy() == "a scan or sync is running"
    assert C.busy() is None
    monkeypatch.setattr(C, "_watching", lambda: "someone is watching")
    assert C.busy() == "someone is watching"

    def broken():
        raise RuntimeError("no db")
    monkeypatch.setattr(C, "_watching", broken)
    assert C.busy() is None


def test_no_ffmpeg_means_nothing_happens(vdb):
    _movie(vdb, 1, 101, "/m/a.mkv")
    w = _worker(vdb)
    w._ffmpeg = lambda: None
    assert w.run_pass() is True and w.status["state"] == "no ffmpeg"


# ── the routes ───────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    import api.player_tracks as R
    import core.player.playable as P
    from core.player import tracks as K
    key = C.file_key("Alien.mkv", 10)
    manifest = C.make_for_file("ffmpeg", "/m/Alien.mkv", key, chapters=CHAPTERS, run=fake_run([]))
    src = Source("file", "video/x-matroska", path=str(tmp_path / "Alien.mkv"),
                 extra={"file_id": 3, "file": {"size_bytes": 10}})
    state = {"refuse": None}

    def resolve(t, i, part=None):
        if state["refuse"]:
            raise state["refuse"]
        if t not in ("movie", "episode", "youtube", "home"):
            raise Refused(400, "not_video", "Only video has tracks.")
        return src
    monkeypatch.setattr(P, "resolve", resolve)
    monkeypatch.setattr(R, "_thumb_key", lambda item_type, source: key if item_type == "movie" else None)
    tracks = {"audio": [], "subtitles": [], "chapters": [dict(c) for c in CHAPTERS]}
    monkeypatch.setattr(R, "source_tracks", lambda s: (tracks, "/m/Alien.mkv", "ffmpeg", "k"))
    monkeypatch.setattr(K, "cached", lambda k: None)
    monkeypatch.setattr(K, "cached_like", lambda name, size: None)
    app = Flask(__name__)
    app.register_blueprint(R.create_blueprint())

    @app.before_request
    def perms():
        if state.get("perms") is not None:
            g.perms = state["perms"]
    c = app.test_client()
    c.state, c.manifest, c.key = state, manifest, key
    return c


def test_tracks_carry_each_chapters_picture(client):
    body = client.get("/api/player/tracks/movie/7").get_json()
    thumbs = [c["thumb"] for c in body["chapters"]]
    assert thumbs[0] == f"/api/player/chapter-thumb/movie/7/0?part=3&v={client.manifest['made_at']}"
    assert all(thumbs)


def test_the_picture_is_served_and_kept_forever(client):
    res = client.get("/api/player/chapter-thumb/movie/7/1?part=3&v=5")
    assert res.status_code == 200 and res.mimetype == "image/webp"
    assert "immutable" in res.headers["Cache-Control"] and res.headers["Cache-Control"].startswith("private")
    assert Image.open(io.BytesIO(res.data)).size == (320, 180)
    assert "max-age=3600" in client.get("/api/player/chapter-thumb/movie/7/1").headers["Cache-Control"]


def test_a_missing_picture_is_404(client):
    assert client.get("/api/player/chapter-thumb/movie/7/9").status_code == 404
    # a youtube video has no pictures
    assert client.get("/api/player/chapter-thumb/youtube/abc/0").status_code == 404
    # made, but the file went missing since
    os.remove(C.folder(client.key) / "002.webp")
    assert client.get("/api/player/chapter-thumb/movie/7/2").get_json()["error"] == "not_found"


def test_a_title_this_profile_cant_watch_shows_no_frame_of_it(client):
    client.state["refuse"] = Refused(403, "restricted", "That isn't available on this profile.")
    res = client.get("/api/player/chapter-thumb/movie/7/0")
    assert res.status_code == 403 and res.get_json()["error"] == "restricted"
    assert client.get("/api/player/chapters/movie/7").status_code == 403
    client.state["refuse"] = Refused(401, "no_profile", "Pick a profile first.")
    assert client.get("/api/player/chapter-thumb/movie/7/0").status_code == 401


def test_a_profile_without_video_is_refused_before_anything(client):
    client.state["perms"] = {"music.access": True}
    assert client.get("/api/player/chapter-thumb/movie/7/0").status_code == 403
    assert client.get("/api/player/chapters/movie/7").status_code == 403
    client.state["perms"] = {"movies.access": True}
    assert client.get("/api/player/chapter-thumb/movie/7/0").status_code == 200


def test_the_detail_page_chapters_never_read_the_file(client, monkeypatch):
    import api.player_tracks as R
    monkeypatch.setattr(R, "source_tracks", lambda s: pytest.fail("read the file"))
    body = client.get("/api/player/chapters/movie/7").get_json()
    assert [c["title"] for c in body["chapters"]] == ["Opening", "Chapter 2", "Credits"]
    assert all(c["thumb"] for c in body["chapters"]) and body["file_id"] == 3
    # nothing known yet: none, still without touching the file
    shutil.rmtree(C.folder(client.key))
    assert client.get("/api/player/chapters/movie/7").get_json()["chapters"] == []


def test_the_detail_page_uses_a_probe_the_player_already_made(client, monkeypatch):
    from core.player import tracks as K
    shutil.rmtree(C.folder(client.key))
    monkeypatch.setattr(K, "cached_like", lambda name, size: {"chapters": CHAPTERS[:2]})
    body = client.get("/api/player/chapters/movie/7").get_json()
    assert [c["thumb"] for c in body["chapters"]] == [None, None]


# ── a real ffmpeg ────────────────────────────────────────────────────────

def _ffmpeg():
    """an ffmpeg that reads this os's paths (wsl can see a windows .exe, which can't)"""
    from core.player import transcode
    try:
        found = transcode.ffmpeg_path() or shutil.which("ffmpeg")
    except Exception:  # noqa: BLE001
        found = shutil.which("ffmpeg")
    if found and os.name == "posix" and found.lower().endswith(".exe"):
        return None
    return found


@pytest.mark.skipif(not _ffmpeg(), reason="no ffmpeg on this machine")
def test_real_ffmpeg_skips_a_black_opening(tmp_path):
    """a 24 s clip in two chapters. the first opens on 6 s of black, so the
    usual 2 s and 5 s moments are black: its picture must come from later"""
    ffmpeg = _ffmpeg()
    meta = tmp_path / "chapters.txt"
    meta.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=12000\ntitle=Cold open\n"
                    "[CHAPTER]\nTIMEBASE=1/1000\nSTART=12000\nEND=24000\ntitle=Main\n")
    clip = tmp_path / "clip.mkv"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=640x360:d=6:r=24",
                    "-f", "lavfi", "-i", "testsrc2=s=640x360:d=18:r=24",
                    "-i", str(meta),
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]",
                    "-map_metadata", "2", "-map_chapters", "2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "24", str(clip)],
                   check=True, capture_output=True, timeout=120)
    from core.player import tracks, transcode
    info = tracks.probe(transcode.ffprobe_for(ffmpeg), str(clip), ("test", str(clip)))
    assert [c["title"] for c in info["chapters"]] == ["Cold open", "Main"]
    key = C.file_key(str(clip), clip.stat().st_size)
    m = C.make_for_file(ffmpeg, str(clip), key, chapters=info["chapters"],
                        duration=info["format"]["duration"])
    assert all(c["thumb"] for c in m["chapters"])
    for i in range(2):
        img = Image.open(C.thumb_path(key, i))
        assert img.size == (320, 180)
        usable, _score = C.score_frame(np.asarray(img.convert("L")))
        assert usable, f"chapter {i} got a black or flat frame"
