"""Mass rename with preview (arr-parity P7).

Templates only ever applied at import time, so a template change forked the
library into naming eras. preview() diffs every owned file against the
current templates (via the real path resolver); apply() moves the picked
files collision-safely, carries sidecars, mirrors the DB stored path, and
never crosses library roots. All against a real tmp filesystem.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import core.video.mass_rename as mr
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def env(tmp_path):
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    movies = tmp_path / "Movies"
    movies.mkdir()
    db.set_setting("movies_path", str(movies))
    yield db, movies
    videoapi._video_db = None


def _seed_movie_file(db, movies_dir, *, name="heat.1995.x264-GRP.mkv",
                     title="Heat", year=1995, resolution="1080p",
                     server_id="m1", tmdb_id=949):
    p = movies_dir / name
    p.write_bytes(b"x" * 1024)
    mid = db.upsert_movie("plex", {
        "server_id": server_id, "title": title, "year": year, "tmdb_id": tmdb_id,
        "file": {"relative_path": str(p), "resolution": resolution,
                 "size_bytes": p.stat().st_size, "video_codec": "h264"}})
    conn = db._get_connection()
    conn.execute("UPDATE movies SET has_file=1 WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    return p


def test_preview_diffs_against_the_template(env):
    db, movies = env
    _seed_movie_file(db, movies)
    out = mr.preview()
    assert out["status"] == "completed" and len(out["entries"]) == 1
    e = out["entries"][0]
    assert e["kind"] == "movie" and "Heat" in e["proposed"]
    assert e["proposed"].startswith(str(movies))          # never leaves its root
    assert e["current"].endswith("heat.1995.x264-GRP.mkv")


def test_apply_moves_file_sidecar_and_db(env):
    db, movies = env
    src = _seed_movie_file(db, movies)
    (movies / "heat.1995.x264-GRP.en.srt").write_text("sub")
    out = mr.apply()
    assert out["renamed"] == 1 and out["skipped"] == 0
    assert not src.exists()
    proposed = mr.preview()
    assert proposed["entries"] == []                      # now clean
    # the sidecar traveled, keeping its language suffix
    moved_subs = [p for p in movies.rglob("*.srt")]
    assert len(moved_subs) == 1 and moved_subs[0].name.endswith(".en.srt")
    assert os.path.splitext(moved_subs[0].name)[0].replace(".en", "") != "heat.1995.x264-GRP"
    # the DB stored path follows (same file id, new location)
    files = db.repair_owned_movie_files()
    assert os.path.exists(files[0]["relative_path"])


def test_apply_never_overwrites_an_occupied_destination(env):
    db, movies = env
    _seed_movie_file(db, movies)
    target = mr.preview()["entries"][0]["proposed"]
    os.makedirs(os.path.dirname(target), exist_ok=True)
    Path(target).write_bytes(b"someone else lives here")
    out = mr.apply()
    assert out["renamed"] == 0 and out["skipped"] == 1
    assert out["failures"][0]["reason"] == "destination already exists"
    assert Path(target).read_bytes() == b"someone else lives here"


def test_case_only_rename_is_not_a_collision(env):
    # On a case-insensitive filesystem (Boulder's /mnt/e) a case-only rename
    # sees ITSELF at the destination. The tmp fs here is case-sensitive, so
    # simulate "dst exists and is the same file" with a hardlink — samefile()
    # is genuinely True and the two-step move branch runs for real.
    db, movies = env
    src = _seed_movie_file(db, movies)
    dst = mr.preview()["entries"][0]["proposed"]
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.link(src, dst)
    out = mr.apply()
    assert out["renamed"] == 1 and out["skipped"] == 0
    assert Path(dst).read_bytes() == b"x" * 1024
    assert not os.path.exists(str(src) + ".soulsync-rename")   # no temp litter


def test_apply_with_key_selection(env):
    db, movies = env
    _seed_movie_file(db, movies)
    keys = [e["key"] for e in mr.preview()["entries"]]
    out = mr.apply(keys=["m:999999"])                     # nothing picked matches
    assert out["renamed"] == 0
    out2 = mr.apply(keys=keys)
    assert out2["renamed"] == 1


def test_unresolved_paths_are_reported_not_renamed(env):
    db, movies = env
    mid = db.upsert_movie("plex", {"server_id": "m2", "title": "Ghost", "tmdb_id": 1,
                                   "file": {"relative_path": "/does/not/exist.mkv",
                                            "size_bytes": 5}})
    conn = db._get_connection()
    conn.execute("UPDATE movies SET has_file=1 WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    out = mr.preview()
    assert out["unresolved"] == 1 and out["entries"] == []


def test_inverse_reroot_maps_back_to_server_view():
    stored = "/data/media/Movies/old/heat.mkv"            # the server's view
    old_local = "/mnt/nas/Movies/old/heat.mkv"            # our view of the same file
    new_local = "/mnt/nas/Movies/Heat (1995)/Heat (1995).mkv"
    assert mr._inverse_reroot(new_local, old_local, stored) \
        == "/data/media/Movies/Heat (1995)/Heat (1995).mkv"
    # no shared structure → fall back to the local truth
    assert mr._inverse_reroot("/a/b.mkv", "/x/y.mkv", "/q/z.avi") == "/a/b.mkv"


def test_ui_and_api_wiring():
    index = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    repair_js = (_ROOT / "webui" / "static" / "video" / "video-repair.js").read_text(encoding="utf-8")
    dl_api = (_ROOT / "api" / "video" / "downloads.py").read_text(encoding="utf-8")
    assert "data-video-rename-card" in index
    assert "wireRename()" in repair_js and "showConfirmDialog" in repair_js
    assert '"/organization/rename/preview"' in dl_api and '"/organization/rename/apply"' in dl_api
    # background preview: the UI polls the status endpoint for a live count
    assert '"/organization/rename/preview/status"' in dl_api
    assert "rename/preview/status" in repair_js and "Scanning your library" in repair_js


# ── background preview (slow-library fix) ─────────────────────────────────────

def test_preview_reports_progress(env):
    db, movies = env
    for i in range(3):
        _seed_movie_file(db, movies, name="m%d.1999.x264-GRP.mkv" % i,
                         title="Film %d" % i, year=1999,
                         server_id="mov%d" % i, tmdb_id=1000 + i)
    seen = []
    out = mr.preview(progress=lambda done, total: seen.append((done, total)))
    assert out["status"] == "completed"
    # always a final 100% tick, and total reflects the real file count
    assert seen and seen[-1] == (3, 3)


def test_dir_cache_resolves_every_file_in_a_shared_folder(env):
    # Several files under one folder must all resolve — the per-directory cache
    # maps the rest off the first without ever hitting the wrong file.
    db, movies = env
    sub = movies / "Pack"
    sub.mkdir()
    for i in range(4):
        _seed_movie_file(db, sub, name="p%d.2000.x264-GRP.mkv" % i,
                         title="Pack %d" % i, year=2000,
                         server_id="pack%d" % i, tmdb_id=2000 + i)
    out = mr.preview()
    assert len(out["entries"]) == 4 and out["unresolved"] == 0
    # each resolves to its OWN distinct file (cache didn't collapse them)
    currents = {e["current"] for e in out["entries"]}
    assert len(currents) == 4


def test_start_preview_runs_in_background_and_finishes(env):
    db, movies = env
    _seed_movie_file(db, movies)
    # reset shared module state so a prior test's run can't leak in
    with mr._preview_lock:
        mr._preview_state.update(running=False, done=0, total=0, result=None,
                                 error=None, finished_at=0.0)
    mr.start_preview()
    for _ in range(200):                       # up to ~10s
        st = mr.preview_state()
        if not st["running"] and st["result"] is not None:
            break
        time.sleep(0.05)
    st = mr.preview_state()
    assert st["running"] is False and st["error"] is None
    assert st["result"] and st["result"]["status"] == "completed"
    assert len(st["result"]["entries"]) == 1


def test_start_preview_is_idempotent_while_running(env):
    # a second start while one is in flight must not spawn a competing run
    db, movies = env
    _seed_movie_file(db, movies)
    with mr._preview_lock:
        mr._preview_state.update(running=True, done=0, total=0, result=None,
                                 error=None, finished_at=0.0)
    st = mr.start_preview()               # should just return the running snapshot
    assert st["running"] is True
    with mr._preview_lock:                # cleanup so we don't wedge later tests
        mr._preview_state.update(running=False)
