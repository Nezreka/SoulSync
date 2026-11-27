"""the marker job end to end on real files: ffmpeg makes a short season
whose episodes share an intro (a tune over colour bars) after cold opens of
different lengths, and end on the same credits (white lines on black, an
end tune). the job decodes, fingerprints (chromaprint when fpcalc is
installed, soulsync's own otherwise), matches and writes the .edl files.

skipped without ffmpeg.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from core.video.markers import edl, service, store
from core.video.markers.job import MarkerJob
from database.video_database import VideoDatabase

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(not FFMPEG, reason="ffmpeg isn't installed")

INTRO_S, CREDITS_S = 40.0, 30.0
# two note patterns with periods that don't line up: a tune, not a loop (a
# loop of a second and a half matches itself one bar later too)
INTRO_TUNE = "0.4*sin(2*PI*(330+110*floor(mod(t*3\\,5))+55*floor(mod(t*1.3\\,3)))*t)"
END_TUNE = "0.4*sin(2*PI*(523-60*floor(mod(t*2\\,4))+40*floor(mod(t*0.7\\,3)))*t)"


def _episode(path: Path, cold: float, body: float, seed: int) -> None:
    def v(src, d):
        return ["-f", "lavfi", "-i", f"{src}=s=320x180:r=24:d={d}"]

    def a(src, d):
        return ["-f", "lavfi", "-i", f"{src}:d={d}"]
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
    cmd += v("testsrc2", cold) + a(f"anoisesrc=c=pink:seed={seed}:a=0.15:r=44100", cold)
    cmd += v("smptebars", INTRO_S) + a(f"aevalsrc='{INTRO_TUNE}':s=44100", INTRO_S)
    cmd += v("testsrc2", body) + a(f"anoisesrc=c=brown:seed={seed + 1}:a=0.15:r=44100", body)
    cmd += ["-f", "lavfi", "-i",
            f"color=c=black:s=320x180:r=24:d={CREDITS_S},"
            "drawbox=x=40:y=60:w=240:h=6:color=white:t=fill,drawbox=x=60:y=90:w=200:h=6:color=white:t=fill"]
    cmd += a(f"aevalsrc='{END_TUNE}':s=44100", CREDITS_S)
    cmd += ["-filter_complex", "[0:v][1:a][2:v][3:a][4:v][5:a][6:v][7:a]concat=n=4:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-g", "48",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", str(path)]
    subprocess.run(cmd, check=True, timeout=300)  # noqa: S603 - our own argv


@pytest.fixture()
def season(tmp_path):
    service.forget_libraries()
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.set_setting("tv_path", str(tmp_path / "TV"))
    folder = tmp_path / "TV" / "Clip Show (2026)" / "Season 01"
    folder.mkdir(parents=True)
    colds = {1: 8.0, 2: 23.5, 3: 15.25}
    body = 120.0
    sid = db.upsert_show_tree("plex", {
        "server_id": "c1", "title": "Clip Show", "year": 2026, "tmdb_id": 999001,
        "seasons": [{"server_id": "c1s", "season_number": 1, "episodes": [
            {"server_id": f"c1e{n}", "episode_number": n, "title": f"Ep {n}", "air_date": "2026-01-0%d" % n}
            for n in colds]}]})
    files = {}
    with store.connect(db) as conn:
        eps = {r[1]: r[0] for r in conn.execute(
            "SELECT id, episode_number FROM episodes WHERE show_id = ?", (sid,))}
    for n, cold in colds.items():
        path = folder / f"Clip Show - S01E0{n}.mp4"
        _episode(path, cold, body, seed=n * 11)
        with store.connect(db) as conn:
            files[n] = conn.execute(
                "INSERT INTO media_files (episode_id, relative_path, size_bytes) VALUES (?, ?, ?)",
                (eps[n], str(path), path.stat().st_size)).lastrowid
    yield db, files, colds, body
    service.forget_libraries()


def test_a_season_of_real_files_gets_intros_credits_and_edl(season):
    db, files, colds, body = season
    job = MarkerJob(db, ffmpeg=FFMPEG, fpcalc=shutil.which("fpcalc") or "",
                    idle_check=lambda: True, busy_check=lambda: None)
    for _ in range(10):
        if not job.tick():
            break
    for n, fid in files.items():
        st = store.state(db, fid)
        assert st["status"] == "done", (n, st)
        best = {m["kind"]: m for m in service.best_for(db, fid)}
        intro_at = colds[n]
        credits_at = colds[n] + INTRO_S + body
        # aac priming and the fingerprint window: within half a second
        assert abs(best["intro"]["start"] - intro_at) < 0.5, (n, best["intro"])
        assert abs(best["intro"]["end"] - (intro_at + INTRO_S)) < 0.5, (n, best["intro"])
        assert abs(best["credits"]["start"] - credits_at) < 1.0, (n, best["credits"])
        ctx = service.file_ctx(db, fid)
        spans = edl.parse(Path(edl.sidecar_path(ctx.local)).read_text())
        assert [round(s) for s, _e, _a in spans] == [round(best["intro"]["start"]),
                                                     round(best["credits"]["start"])]


def test_one_new_episode_alone_uses_what_the_season_already_knows(season):
    db, files, colds, body = season
    job = MarkerJob(db, ffmpeg=FFMPEG, fpcalc=shutil.which("fpcalc") or "",
                    idle_check=lambda: True, busy_check=lambda: None)
    # the first two are analysed; the third arrives later and is found on its own pass
    with store.connect(db) as conn:
        conn.execute("UPDATE media_files SET added_at = '2020-01-01' WHERE id = ?", (files[3],))
    job.process(files[1], FFMPEG)
    job.process(files[2], FFMPEG)
    calls = []
    real = service.analyze

    def counting(db_, ctx, *a, **k):
        calls.append(ctx.file_id)
        return real(db_, ctx, *a, **k)
    service.analyze = counting
    try:
        job.process(files[3], FFMPEG)
    finally:
        service.analyze = real
    assert calls == [files[3]]           # the others weren't decoded again
    intro = next(m for m in service.best_for(db, files[3]) if m["kind"] == "intro")
    assert abs(intro["start"] - colds[3]) < 0.5
