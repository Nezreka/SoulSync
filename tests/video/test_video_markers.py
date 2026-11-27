"""skip markers (core/video/markers): .edl read and write, which source
wins, the media servers' answers, the audio matcher, the picture's
credits, the season matcher and the background job.

nothing here needs ffmpeg or fpcalc: the audio is made in numpy and the
matcher runs on soulsync's own fingerprint. tests/video/
test_video_markers_clips.py runs the whole thing on ffmpeg-made episodes
when ffmpeg is installed.
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path

import numpy as np
import pytest

from core.video.markers import audio as A
from core.video.markers import detect, edl, merge, server_import, service, store, visual
from core.video.markers.job import MarkerJob
from database.video_database import VideoDatabase

R = A.RATE


# ── made-up soundtracks ──────────────────────────────────────────────────

def _music(seconds, seed):
    r = np.random.default_rng(seed)
    t = np.arange(int(seconds * R)) / R
    out = np.zeros_like(t)
    notes = [220, 247, 262, 294, 330, 349, 392, 440, 494, 523, 587, 659, 698, 784]
    for i in range(int(seconds * 4)):
        f = r.choice(notes)
        s, e = int(i * 0.25 * R), int((i + 1) * 0.25 * R)
        out[s:e] += np.sin(2 * np.pi * f * t[s:e]) * 0.3 + np.sin(4 * np.pi * f * t[s:e]) * 0.1
    return out


def _talk(seconds, seed):
    r = np.random.default_rng(seed)
    x = r.normal(0, 0.1, int(seconds * R))
    gate = np.repeat(r.random(int(seconds * 5) + 1) > 0.4, R // 5)[: x.size]
    return x * gate


INTRO = _music(55, 99)
OUTRO = _music(40, 77)


@functools.lru_cache(maxsize=64)
def _episode(cold, seed, total=300.0, outro=True):
    parts = [_talk(cold, seed), np.zeros(int(0.5 * R)), INTRO, np.zeros(int(0.3 * R))]
    used = cold + 0.5 + 55 + 0.3
    body = total - used - (40 + 0.4 if outro else 0)
    parts.append(_talk(body, seed + 1))
    if outro:
        parts += [np.zeros(int(0.4 * R)), OUTRO]
    return (np.concatenate(parts) * 20000).astype(np.int16)


_PRINTS: dict = {}


def _print(pcm, offset):
    """a fingerprint, made once per sound (the tests share episodes)"""
    key = (hash(pcm.tobytes()), pcm.size, offset)
    if key not in _PRINTS:
        _PRINTS[key] = A.fingerprint(pcm, offset, None)
    p = _PRINTS[key]
    return A.Print(p.fp, p.offset, p.step, p.algo, p.env)


def _features(fid, pcm, season, ep, duration=None):
    duration = duration or pcm.size / R
    head = _print(pcm[: int((min(600, duration * 0.25) + 150) * R)], 0.0)
    t0 = duration - min(420, duration * 0.25)
    tail = _print(pcm[int(t0 * R):], t0)
    return detect.Features(fid, duration, season, ep, head=head, tail=tail)


# ── .edl ─────────────────────────────────────────────────────────────────

def test_edl_parse_reads_seconds_clock_times_and_skips_junk():
    text = "\n".join(["61.2 151.0 3", "0:02:30.5\t0:03:00 0", "#100 #200 3", "garbage line",
                      "10 5 3", "400 420", "500 510 9", "1:00:00 1:00:30 2"])
    assert edl.parse(text) == [(61.2, 151.0, 3), (150.5, 180.0, 0), (400.0, 420.0, 0),
                               (3600.0, 3630.0, 2)]


def test_edl_classify_types_by_position():
    spans = [(5.0, 40.0, 3), (62.0, 150.0, 3), (900.0, 960.0, 3), (2500.0, 2590.0, 3), (1200, 1201, 1)]
    got = edl.classify(spans, 2600.0)
    assert [(m["kind"], m["start"]) for m in got] == [
        ("recap", 5.0), ("intro", 62.0), ("commercial", 900.0), ("credits", 2500.0)]
    # a movie has no intro window: its head span is a commercial
    assert [m["kind"] for m in edl.classify([(30.0, 60.0, 3)], 6000.0, is_movie=True)] == ["commercial"]


def test_edl_render_and_round_trip(tmp_path):
    markers = [{"kind": "credits", "start": 1300.0, "end": 1390.5},
               {"kind": "intro", "start": 61.25, "end": 151.0}, {"kind": "chapter?", "start": 1, "end": 2}]
    text = edl.render(markers)
    assert text == "61.250\t151.000\t3\n1300.000\t1390.500\t3\n"
    path = tmp_path / "Show - S01E01.edl"
    edl.write_atomic(str(path), text)
    got = edl.read(str(tmp_path / "Show - S01E01.mkv"))
    assert got is not None and got[1] == [(61.25, 151.0, 3), (1300.0, 1390.5, 3)]
    assert [m["kind"] for m in edl.classify(got[1], 1400.0)] == ["intro", "credits"]
    assert not list(tmp_path.glob("*.soulsync-tmp"))


# ── precedence ───────────────────────────────────────────────────────────

def _m(kind, start, end, source):
    return {"kind": kind, "start": start, "end": end, "source": source}


def test_precedence_user_sidecar_detected_server_chapter():
    cands = [_m("intro", 10, 70, "chapter"), _m("intro", 11, 71, "plex"), _m("intro", 12, 72, "jellyfin"),
             _m("intro", 13, 73, "detected"), _m("intro", 14, 74, "sidecar"), _m("intro", 15, 75, "user")]
    for drop in range(6):
        got = merge.best(cands[: 6 - drop], (), 1400)
        assert len(got) == 1
    assert merge.best(cands, (), 1400)[0]["source"] == "user"
    assert merge.best(cands[:5], (), 1400)[0]["source"] == "sidecar"
    assert merge.best(cands[:4], (), 1400)[0]["source"] == "detected"
    assert merge.best(cands[:3], (), 1400)[0]["source"] == "plex"
    assert merge.best(cands[:1], (), 1400)[0]["source"] == "chapter"


def test_a_removed_kind_hides_every_automatic_source_but_not_the_users():
    cands = [_m("intro", 13, 73, "detected"), _m("credits", 1300, 1390, "plex")]
    assert [m["kind"] for m in merge.best(cands, ["intro"], 1400)] == ["credits"]
    cands.append(_m("intro", 20, 80, "user"))
    assert [m["source"] for m in merge.best(cands, ["intro"], 1400)] == ["user", "plex"]


def test_markers_are_clamped_to_the_file_and_tiny_or_backwards_ones_dropped():
    got = merge.best([_m("credits", 1300, 9999, "detected"), _m("intro", 50, 50.5, "detected"),
                      _m("recap", 40, 30, "sidecar"), _m("intro", 1500, 1550, "plex")], (), 1400)
    assert got == [{**_m("credits", 1300, 1400, "detected")}]


def test_chapter_names_still_count():
    chapters = [{"start": 0, "end": 60, "title": "Previously on"}, {"start": 60, "end": 150, "title": "Opening"},
                {"start": 150, "end": 1300, "title": "Chapter 3"}, {"start": 1300, "end": None, "title": "End Credits"},
                {"start": 0, "end": 900, "title": "Intro"}]
    got = merge.from_chapters(chapters[:4], 1400)
    assert [(m["kind"], m["start"], m["end"]) for m in got] == [
        ("recap", 0, 60), ("intro", 60, 150), ("credits", 1300, 1400)]
    assert merge.from_chapters(chapters[4:], 1400) == []    # a 15 minute "intro" isn't one


def test_public_shape_carries_label_and_post_credits():
    m = {**_m("credits", 5000, 5300, "detected"), "confidence": 0.912,
         "extra": {"post_credits": [5300, 5400]}}
    assert merge.public(m) == {"kind": "credits", "start": 5000, "end": 5300, "source": "detected",
                               "label": "Skip credits", "confidence": 0.91,
                               "post_credits": {"start": 5300, "end": 5400}}


# ── media servers ────────────────────────────────────────────────────────

def test_plex_markers_parse():
    data = {"MediaContainer": {"Metadata": [{"Marker": [
        {"type": "intro", "startTimeOffset": 61200, "endTimeOffset": 151000},
        {"type": "credits", "startTimeOffset": 1300000, "endTimeOffset": 1390000, "final": False},
        {"type": "credits", "startTimeOffset": 1392000, "endTimeOffset": 1400000, "final": True},
        {"type": "bookmark", "startTimeOffset": 1, "endTimeOffset": 2}]}]}}
    got = server_import.parse_plex(data)
    assert [(m["kind"], m["start"], m["end"]) for m in got] == [("intro", 61.2, 151.0), ("credits", 1300.0, 1390.0)]
    assert all(m["source"] == "plex" for m in got)
    assert server_import.parse_plex({}) == [] and server_import.parse_plex(None) == []


def test_jellyfin_segments_and_intro_skipper_parse():
    seg = {"Items": [{"Type": "Intro", "StartTicks": 612000000, "EndTicks": 1510000000},
                     {"Type": "Outro", "StartTicks": 13000000000, "EndTicks": 13900000000},
                     {"Type": "Recap", "StartTicks": 0, "EndTicks": 300000000},
                     {"Type": "Unknown", "StartTicks": 1, "EndTicks": 2}]}
    assert [(m["kind"], m["start"]) for m in server_import.parse_jellyfin_segments(seg)] == [
        ("intro", 61.2), ("credits", 1300.0), ("recap", 0.0)]
    old = {"Introduction": {"Start": 61.2, "End": 151.0, "Valid": True},
           "Credits": {"Start": 1300, "End": 1390, "Valid": False}}
    assert [(m["kind"], m["end"]) for m in server_import.parse_intro_skipper(old)] == [("intro", 151.0)]


def test_a_server_that_does_not_answer_keeps_what_was_there(monkeypatch):
    monkeypatch.setattr(server_import, "_get", lambda url, headers: None)
    assert server_import.fetch_plex("http://plex", "tok", 5) is None
    assert server_import.fetch_jellyfin("http://jf", "key", "abc") is None
    calls = []

    def answer(url, headers):
        calls.append(url)
        return {"Items": []} if "MediaSegments" in url else None
    monkeypatch.setattr(server_import, "_get", answer)
    assert server_import.fetch_jellyfin("http://jf", "key", "abc") == []    # answered: none


# ── the audio matcher ────────────────────────────────────────────────────

def test_shared_intro_found_to_the_frame_wherever_the_cold_open_ends():
    a, b = _episode(30.0, 1), _episode(83.37, 2)
    pa, pb = A.fingerprint(a, 0.0, None), A.fingerprint(b, 0.0, None)
    m = A.best_match(pa, pb, min_s=10, max_s=150)
    assert m is not None
    s, e = A.refine(pa, pb, pa.time(m.a0), pa.time(m.a1 + 1), m.shift * pa.step)
    # truth: 30.5 to 85.5. a fingerprint step is 124 ms; refined is within a frame
    assert abs(s - 30.5) < 0.05 and abs(e - 85.5) < 0.05


def test_nothing_shared_finds_nothing():
    a = (_talk(200, 5) * 20000).astype(np.int16)
    b = (_talk(200, 6) * 20000).astype(np.int16)
    assert A.best_match(A.fingerprint(a, 0, None), A.fingerprint(b, 0, None), min_s=10, max_s=150) is None


def test_fingerprints_of_different_algorithms_never_compare():
    a = A.fingerprint(_episode(20, 1), 0, None)
    b = A.Print(a.fp.copy(), 0, a.step, "chromaprint", a.env)
    assert A.best_match(a, b, min_s=10, max_s=150) is None


def test_blob_round_trip():
    p = A.fingerprint(_episode(20, 1)[: R * 60], 0, None)
    assert np.array_equal(A.from_blob(A.to_blob(p.fp, "<u4"), "<u4"), p.fp)


# ── the season matcher ───────────────────────────────────────────────────

def test_season_finds_each_episodes_intro_and_credits():
    colds = [30.0, 63.37, 12.0, 47.5]
    pool = [_features(i + 1, _episode(c, 10 * i + 1), 1, i + 1) for i, c in enumerate(colds)]
    got = detect.season(pool)
    for i, c in enumerate(colds):
        kinds = {m["kind"]: m for m in got[i + 1]}
        assert abs(kinds["intro"]["start"] - (c + 0.5)) < 0.1
        assert abs(kinds["intro"]["end"] - (c + 55.5)) < 0.1
        assert kinds["intro"]["confidence"] >= 0.8
        assert abs(kinds["credits"]["start"] - (300 - 40)) < 0.2
        assert kinds["credits"]["extra"]["how"] == "audio"


def test_one_new_episode_uses_the_previous_season():
    last_season = [_features(i + 1, _episode(20.0 + 9 * i, 3 * i + 7), 1, 10 + i) for i in range(3)]
    new = _features(9, _episode(41.0, 500), 2, 1)
    pool = last_season + [new]
    for f in last_season:
        f.extra["reference_only"] = True
    got = detect.season(pool)
    assert set(got) == {9}
    intro = next(m for m in got[9] if m["kind"] == "intro")
    assert abs(intro["start"] - 41.5) < 0.1
    sibs = detect.pick_siblings(new, pool)
    assert [s.episode for s in sibs] == [12, 11, 10]    # the latest of the season before first


def test_a_show_with_no_shared_intro_gets_none():
    pool = [_features(i, (_talk(300, i) * 20000).astype(np.int16), 1, i) for i in range(1, 4)]
    assert all(not ms for ms in detect.season(pool).values())


def test_credits_from_audio_and_picture_agree_and_a_scene_after_is_kept():
    f = _features(1, _episode(30.0, 1), 1, 1)
    sib = _features(2, _episode(50.0, 2), 1, 2)
    f.visual = {"start": 258.0, "end": 290.0, "confidence": 0.8, "post_credits": [290.0, 300.0]}
    c = detect.credits(f, [sib])
    assert c["extra"]["how"] == "audio+picture" and c["confidence"] >= 0.9
    assert c["start"] == 258.0 and c["end"] == 290.0
    assert c["extra"]["post_credits"] == [290.0, 300.0]


def test_a_movie_has_credits_from_the_picture_only():
    f = detect.Features(1, 6000.0, visual={"start": 5600.0, "confidence": 0.85})
    assert detect.movie(f) == [{"kind": "credits", "start": 5600.0, "end": 6000.0, "confidence": 0.85,
                                "extra": {"how": "picture"}}]
    assert detect.movie(detect.Features(2, 6000.0)) == []


def test_recap_is_clips_of_the_last_episode_before_the_intro():
    prev_pcm = _episode(30.0, 1)
    clips = [prev_pcm[int(s * R): int((s + d) * R)] for s, d in ((120, 6), (200, 8), (150, 5))]
    gap = np.zeros(int(0.2 * R), dtype=np.int16)
    recap_audio = np.concatenate([clips[0], gap, clips[1], gap, clips[2]])
    rest = _episode(10.0, 33)
    pcm = np.concatenate([recap_audio, rest])
    f = _features(2, pcm, 1, 2)
    prev = _features(1, prev_pcm, 1, 1)
    prev.body = A.fingerprint(prev_pcm, 0.0, None)
    intro = detect.intro(f, [prev])
    assert intro is not None
    r = detect.recap(f, [prev], intro)
    assert r is not None and r["start"] < 2.0 and abs(r["end"] - intro["start"]) < 0.01


# ── the picture's credits ────────────────────────────────────────────────

def _frames(spec, step=2.0):
    """spec: [(seconds, 'scene' | 'credits' | 'black')]"""
    out, t = [], 0.0
    look = {"scene": (110.0, 50.0, 0.2, 0.25), "credits": (12.0, 30.0, 0.95, 0.04),
            "black": (2.0, 1.0, 1.0, 0.0)}
    for secs, kind in spec:
        for _ in range(int(secs / step)):
            out.append(visual.Frame(t, *look[kind]))
            t += step
    return out


def test_movie_credits_run_to_the_end():
    frames = _frames([(5000, "scene"), (4, "black"), (400, "credits")])
    got = visual.find_credits(frames, 5404.0, True)
    assert got["start"] == 5000.0 and got["end"] == 5404.0 and "post_credits" not in got


def test_a_scene_after_the_credits_is_found():
    frames = _frames([(5000, "scene"), (300, "credits"), (60, "scene"), (20, "credits")])
    got = visual.find_credits(frames, 5380.0, True)
    assert got["start"] == 5000.0 and got["post_credits"] == [5300.0, 5360.0]
    frames = _frames([(5000, "scene"), (300, "credits"), (90, "scene")])
    got = visual.find_credits(frames, 5390.0, True)
    assert got["start"] == 5000.0 and got["post_credits"] == [5300.0, 5390.0]


def test_a_dark_scene_in_the_middle_is_not_credits():
    frames = _frames([(2000, "scene"), (200, "credits"), (3000, "scene")])
    assert visual.find_credits(frames, 5200.0, True) is None


def test_sharpen_finds_the_first_frame_and_the_fade():
    frames = _frames([(5, "scene"), (0.5, "black"), (6, "credits")], step=1 / 24)
    t = visual.sharpen(frames, 5.0)
    assert abs(t - 5.0) < 1 / 24 + 1e-6


# ── the database side ────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    service.forget_libraries()
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    d.set_setting("movies_path", str(tmp_path / "Movies"))
    d.set_setting("tv_path", str(tmp_path / "TV"))
    yield d
    service.forget_libraries()


def _file(db, path: Path, *, movie_id=None, episode_id=None, size=16):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    with store.connect(db) as conn:
        cur = conn.execute("INSERT INTO media_files (movie_id, episode_id, relative_path, size_bytes, "
                           "runtime_seconds) VALUES (?,?,?,?,?)",
                           (movie_id, episode_id, str(path), size, 1400))
    return cur.lastrowid


def _show(db, tmp_path, episodes=3, season=1):
    sid = db.upsert_show_tree("plex", {
        "server_id": "s1", "title": "Tulsa King", "year": 2022, "tmdb_id": 153312,
        "seasons": [{"server_id": "s1a", "season_number": season, "episodes": [
            {"server_id": f"s1e{n}", "episode_number": n, "title": f"Ep {n}", "air_date": "2022-11-13"}
            for n in range(1, episodes + 1)]}]})
    with store.connect(db) as conn:
        eps = [r[0] for r in conn.execute("SELECT id FROM episodes WHERE show_id=? ORDER BY episode_number",
                                          (sid,))]
    folder = tmp_path / "TV" / "Tulsa King (2022)" / "Season 01"
    files = [_file(db, folder / f"Tulsa King - S01E0{n}.mkv", episode_id=e) for n, e in enumerate(eps, 1)]
    return sid, eps, files


def _set_library(db, kind, **opts):
    from core.video.libraries import list_libraries, update_library
    lib = next(x for x in list_libraries(db, kind) if x["builtin"] == "main")
    update_library(db, lib["id"], opts)
    service.forget_libraries()


def test_a_users_edl_is_read_and_never_overwritten(db, tmp_path):
    _sid, eps, files = _show(db, tmp_path)
    ctx = service.file_ctx(db, files[0])
    assert ctx.local and ctx.local.endswith("S01E01.mkv")
    side = Path(edl.sidecar_path(ctx.local))
    side.write_text("62.0 150.0 3\n1310 1400 3\n")
    assert service.read_sidecar(db, ctx, 1400)
    got = service.best_for(db, files[0], None, 1400)
    assert [(m["kind"], m["source"]) for m in got] == [("intro", "sidecar"), ("credits", "sidecar")]
    store.set_user(db, files[0], "intro", 60.0, 149.0, 1)
    assert service.write_sidecar(db, ctx) == "theirs"
    assert side.read_text() == "62.0 150.0 3\n1310 1400 3\n"


def test_soulsync_writes_its_own_edl_only_when_the_library_allows(db, tmp_path):
    _sid, eps, files = _show(db, tmp_path)
    ctx = service.file_ctx(db, files[0])
    store.replace_source(db, files[0], "detected", [{"kind": "intro", "start": 61.0, "end": 150.0}])
    side = Path(edl.sidecar_path(ctx.local))
    # a fresh install writes them; the v79 migration switches existing libraries off
    assert service.write_sidecar(db, ctx) == "written"
    assert side.read_text() == "61.000\t150.000\t3\n"
    # its own file read back is not a second source
    assert not service.read_sidecar(db, ctx, 1400)
    assert [m["source"] for m in store.segments(db, files[0])] == ["detected"]
    # updated when the markers change, removed when there are none
    store.set_user(db, files[0], "intro", 60.0, 151.0, 1)
    assert service.write_sidecar(db, ctx) == "written"
    assert side.read_text() == "60.000\t151.000\t3\n"
    store.remove(db, files[0], "intro")
    assert service.write_sidecar(db, ctx) == "removed" and not side.exists()
    # switched off: nothing is written
    _set_library(db, "show", markers_write=False)
    store.reset(db, files[0], "intro")
    assert service.write_sidecar(db, ctx) == "off" and not side.exists()


def test_the_padlock_and_a_hand_edited_edl_stop_writes(db, tmp_path):
    sid, eps, files = _show(db, tmp_path)
    store.replace_source(db, files[0], "detected", [{"kind": "intro", "start": 61.0, "end": 150.0}])
    with store.connect(db) as conn:
        conn.execute("UPDATE shows SET hands_off = 1 WHERE id = ?", (sid,))
    assert service.write_sidecar(db, service.file_ctx(db, files[0])) == "padlock"
    with store.connect(db) as conn:
        conn.execute("UPDATE shows SET hands_off = 0 WHERE id = ?", (sid,))
    ctx = service.file_ctx(db, files[0])
    assert service.write_sidecar(db, ctx) == "written"
    side = Path(edl.sidecar_path(ctx.local))
    os.utime(side, (1, 1))
    side.write_text("70.0 160.0 3\n")
    # edited by hand: read as theirs, and soulsync never writes it again
    assert service.read_sidecar(db, ctx, 1400)
    assert store.written_sidecar(db, files[0]) is None
    assert service.write_sidecar(db, ctx) == "theirs"
    assert service.best_for(db, files[0], None, 1400)[0]["source"] == "sidecar"


def test_a_read_only_folder_keeps_the_markers_in_the_database(db, tmp_path, monkeypatch):
    _sid, eps, files = _show(db, tmp_path)
    store.replace_source(db, files[0], "detected", [{"kind": "intro", "start": 61.0, "end": 150.0}])

    def refuse(path, text):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(edl, "write_atomic", refuse)
    assert service.write_sidecar(db, service.file_ctx(db, files[0])) == "failed"
    assert service.best_for(db, files[0])[0]["source"] == "detected"


def test_existing_libraries_stop_writing_after_the_upgrade(tmp_path):
    path = str(tmp_path / "video_library.db")
    VideoDatabase(database_path=path)
    conn = __import__("sqlite3").connect(path)
    conn.execute("PRAGMA user_version = 78")
    conn.commit()
    conn.close()
    from database import video_database as vdb
    vdb._initialized_paths.discard(str(Path(path).resolve()))
    VideoDatabase(database_path=path)
    conn = __import__("sqlite3").connect(path)
    opts = [json.loads(r[0]) for r in conn.execute("SELECT options FROM video_libraries")]
    conn.close()
    assert opts and all(o.get("markers_write") is False for o in opts)
    from core.video.libraries import normalize_options
    assert normalize_options({})["markers_write"] is True        # a new library writes
    assert normalize_options(opts[0])["markers_write"] is False


def test_player_markers_read_the_sidecar_and_queue_a_first_play(db, tmp_path):
    _sid, eps, files = _show(db, tmp_path)
    ctx = service.file_ctx(db, files[1])
    Path(edl.sidecar_path(ctx.local)).write_text("61.0 150.0 3\n")
    chapters = [{"start": 0, "end": 30, "title": "Recap"}, {"start": 30, "end": 1400, "title": "Episode"}]
    got = service.player_markers(db, files[1], chapters, 1400, ctx.local)
    assert [(m["kind"], m["source"], m["label"]) for m in got] == [
        ("recap", "chapter", "Skip recap"), ("intro", "sidecar", "Skip intro")]
    assert store.state(db, files[1])["priority"] == 10


# ── the job ──────────────────────────────────────────────────────────────

def _job(db, **kw):
    kw.setdefault("ffmpeg", "ffmpeg")
    kw.setdefault("fpcalc", "")
    kw.setdefault("idle_check", lambda: True)
    kw.setdefault("busy_check", lambda: None)
    return MarkerJob(db, **kw)


def _fake_analysis(monkeypatch, pcm_for, calls=None):
    def analyze(db, ctx, ffmpeg, fpcalc, *, recaps=False, should_stop=lambda: False):
        if calls is not None:
            calls.append(ctx.file_id)
        pcm = pcm_for(ctx)
        dur = pcm.size / R
        f = _features(ctx.file_id, pcm, ctx.season, ctx.episode, dur)
        store.save_print(db, ctx.file_id, "head", f.head)
        store.save_print(db, ctx.file_id, "tail", f.tail)
        return {"duration": dur, "algo": "soulsync", "visual": None}
    monkeypatch.setattr(service, "analyze", analyze)
    monkeypatch.setattr(service, "import_server", lambda db, ctx: 0)


def test_the_job_finds_a_seasons_intros_and_writes_edl(db, tmp_path, monkeypatch):
    _sid, eps, files = _show(db, tmp_path)
    colds = {1: 30.0, 2: 63.37, 3: 12.0}
    _fake_analysis(monkeypatch, lambda ctx: _episode(colds[ctx.episode], ctx.episode * 7))
    job = _job(db)
    while job.tick():
        pass
    for n, fid in enumerate(files, 1):
        best = {m["kind"]: m for m in service.best_for(db, fid)}
        assert abs(best["intro"]["start"] - (colds[n] + 0.5)) < 0.1, n
        assert best["intro"]["source"] == "detected"
        ctx = service.file_ctx(db, fid)
        assert Path(edl.sidecar_path(ctx.local)).read_text().startswith(f"{best['intro']['start']:.3f}\t")
    assert job.status()["done"] == 3


def test_the_job_resumes_after_a_crash_and_reuses_fingerprints(db, tmp_path, monkeypatch):
    _sid, eps, files = _show(db, tmp_path, episodes=2)
    calls = []
    _fake_analysis(monkeypatch, lambda ctx: _episode(20.0 + ctx.episode, ctx.episode), calls)
    store.mark(db, files[0], "markers", "running", count_attempt=True)    # the server died mid-file
    job = _job(db)
    job._run = lambda: None          # the restart, without the loop racing the test
    job.start()
    job.stop()
    assert store.state(db, files[0])["status"] == "pending"
    while job.tick():
        pass
    assert sorted(calls) == sorted(files)
    # "find intros now" re-matches; fingerprints of an unchanged file aren't decoded again
    calls.clear()
    assert store.bump(db, files, 100, redo=True) == 2
    while job.tick():
        pass
    assert calls == []
    assert all(store.state(db, f)["status"] == "done" for f in files)


def test_a_broken_file_is_parked_after_three_tries(db, tmp_path, monkeypatch):
    _sid, eps, files = _show(db, tmp_path, episodes=1)

    def broken(*a, **k):
        raise RuntimeError("moov atom not found")
    monkeypatch.setattr(service, "analyze", broken)
    monkeypatch.setattr(service, "import_server", lambda db, ctx: 0)
    job = _job(db)
    for _ in range(5):
        job.tick()
    st = store.state(db, files[0])
    assert st["status"] == "failed" and st["attempts"] == 3 and "moov" in st["last_error"]
    assert job.next_file() is None


def test_the_job_waits_for_scans_plays_and_the_night_window(db, tmp_path, monkeypatch):
    from datetime import datetime
    _sid, eps, files = _show(db, tmp_path, episodes=1)
    _fake_analysis(monkeypatch, lambda ctx: _episode(20.0, 1))
    assert not _job(db, busy_check=lambda: "a library scan is running").tick()
    assert not _job(db, idle_check=lambda: False).tick()
    db.set_setting("video_markers_when", "night")
    noon = _job(db, clock=lambda: datetime(2026, 11, 27, 12, 0))
    with store.connect(db) as conn:
        conn.execute("UPDATE media_files SET added_at = '2020-01-01 00:00:00'")
    assert not noon.tick() and noon.waiting == "waiting for the night window"
    store.bump(db, files, 100)          # "find intros now" doesn't wait for the night
    assert noon.tick()
    db.set_setting("video_markers_enabled", "0")
    assert not _job(db).tick()


def test_a_library_with_markers_off_is_skipped_and_the_queue_is_ordered(db, tmp_path, monkeypatch):
    _sid, eps, files = _show(db, tmp_path)
    job = _job(db)
    assert job.next_file() == files[-1]                  # newest first (same date: highest id)
    store.bump(db, [files[0]], 100)
    assert job.next_file() == files[0]                   # find now jumps the queue
    _set_library(db, "show", markers=False)
    assert job.next_file() is None


def test_sidecars_are_read_back_after_a_scan(db, tmp_path):
    _sid, eps, files = _show(db, tmp_path, episodes=2)
    ctx = service.file_ctx(db, files[1])
    Path(edl.sidecar_path(ctx.local)).write_text("0 45 3\n61.0 150.0 3\n")
    job = _job(db)
    job.after_scan()
    assert job.sweep_sidecars()
    assert not job.sweep_sidecars()
    assert [m["kind"] for m in service.best_for(db, files[1], None, 1400)] == ["recap", "intro"]
