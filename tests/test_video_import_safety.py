"""Video import safety net — the post-processing hardening pass.

Covers the monitor-side helpers: the Soulseek byte-count backstop, the boot-time
temp-file sweep, the expected-runtime resolver, and the deferred source reclaim
(the crash-window fix: the download copy is removed only AFTER the completed row
is persisted, so a restart in between re-drives into `already_placed` instead of
a misleading 'failed').
"""

from __future__ import annotations

import json
import os
import time

import pytest

from core.video import download_monitor as mon


class _FakeDB:
    def __init__(self, settings=None):
        self._settings = settings or {}

    def get_setting(self, key):
        return self._settings.get(key)


def _dl(**kw):
    base = {"id": 1, "kind": "show", "source": "soulseek", "size_bytes": 1_000_000_000,
            "search_ctx": json.dumps({"scope": "episode", "season": 1, "episode": 1})}
    base.update(kw)
    return base


# ── soulseek byte-count backstop ──────────────────────────────────────────────
def test_size_check_rejects_short_soulseek_file(tmp_path):
    f = tmp_path / "ep.mkv"
    f.write_bytes(b"x" * 100)                       # 100 bytes of a 1000-byte download
    err = mon._soulseek_size_error(_dl(size_bytes=1000), str(f))
    assert err and "incomplete" in err


def test_size_check_passes_full_file(tmp_path):
    f = tmp_path / "ep.mkv"
    f.write_bytes(b"x" * 1000)
    assert mon._soulseek_size_error(_dl(size_bytes=1000), str(f)) is None


def test_size_check_ignores_torrents_and_unknown_size(tmp_path):
    f = tmp_path / "ep.mkv"
    f.write_bytes(b"x" * 10)
    assert mon._soulseek_size_error(_dl(source="torrent", size_bytes=1000), str(f)) is None
    assert mon._soulseek_size_error(_dl(size_bytes=0), str(f)) is None


# ── boot-time temp sweep ──────────────────────────────────────────────────────
def test_sweep_removes_only_stale_temps(tmp_path):
    lib = tmp_path / "tv"
    lib.mkdir()
    old_tmp = lib / "Show.S01E01.1080p.mkv.tmp.abc12345"
    old_tmp.write_bytes(b"x")
    old_age = time.time() - 7200
    os.utime(old_tmp, (old_age, old_age))
    fresh_tmp = lib / "Show.S01E02.1080p.mkv.tmp.def67890"
    fresh_tmp.write_bytes(b"x")                      # recent → still in flight, keep
    real = lib / "Show.S01E01.1080p.mkv"
    real.write_bytes(b"x")                           # real file → never touch
    old_part = lib / "video.mp4.part"
    old_part.write_bytes(b"x")
    os.utime(old_part, (old_age, old_age))

    db = _FakeDB({"tv_path": str(lib)})
    assert mon._sweep_import_temps(db) == 2
    assert not old_tmp.exists() and not old_part.exists()
    assert fresh_tmp.exists() and real.exists()


def test_sweep_ignores_missing_roots():
    db = _FakeDB({"tv_path": "/nonexistent/path/xyz"})
    assert mon._sweep_import_temps(db) == 0


# ── expected-runtime resolver ─────────────────────────────────────────────────
class _RTDB(_FakeDB):
    def __init__(self, seconds):
        super().__init__()
        self._seconds = seconds

    def get_expected_runtime_seconds(self, kind, media_id, season=None, episode=None):
        return self._seconds


def test_expected_duration_resolves_for_movie_and_episode():
    db = _RTDB(42 * 60)
    movie = {"kind": "movie", "media_id": "123",
             "search_ctx": json.dumps({"scope": "movie"})}
    assert mon._expected_duration_sec(db, movie) == 42 * 60
    ep = _dl(media_id="456")
    assert mon._expected_duration_sec(db, ep) == 42 * 60


def test_expected_duration_skips_youtube_and_db_errors():
    db = _RTDB(42 * 60)
    yt = {"kind": "youtube", "search_ctx": json.dumps({"scope": "youtube"})}
    assert mon._expected_duration_sec(db, yt) is None

    class _Boom(_FakeDB):
        def get_expected_runtime_seconds(self, *a, **k):
            raise RuntimeError("db down")
    assert mon._expected_duration_sec(_Boom(), _dl()) is None   # never blocks import


# ── real DB: runtime lookup + episode files query ─────────────────────────────
@pytest.fixture()
def vdb(tmp_path):
    from database.video_database import VideoDatabase
    db = VideoDatabase(str(tmp_path / "video.db"))
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO movies (tmdb_id, title, runtime_minutes, has_file) "
                     "VALUES (100, 'Film', 138, 0)")
        conn.execute("INSERT INTO shows (tvdb_id, tmdb_id, title, runtime_minutes) "
                     "VALUES (200, 201, 'Show', 42)")
        show_id = conn.execute("SELECT id FROM shows WHERE tvdb_id=200").fetchone()["id"]
        conn.execute("INSERT INTO seasons (show_id, season_number) VALUES (?, 2)", (show_id,))
        season_id = conn.execute("SELECT id FROM seasons WHERE show_id=?", (show_id,)).fetchone()["id"]
        conn.execute("INSERT INTO episodes (show_id, season_id, season_number, episode_number, title, "
                     "runtime_minutes, has_file) VALUES (?, ?, 2, 6, 'Ep', 55, 1)",
                     (show_id, season_id))
        ep_id = conn.execute("SELECT id FROM episodes WHERE show_id=?", (show_id,)).fetchone()["id"]
        conn.execute("INSERT INTO media_files (episode_id, relative_path, size_bytes, runtime_seconds) "
                     "VALUES (?, 'Show/S02E06.mkv', 1000, 1200)", (ep_id,))
        conn.commit()
    finally:
        conn.close()
    return db


def test_get_expected_runtime_seconds_movie(vdb):
    assert vdb.get_expected_runtime_seconds("movie", 100) == 138 * 60
    assert vdb.get_expected_runtime_seconds("movie", 999) is None


def test_get_expected_runtime_seconds_episode_prefers_episode_row(vdb):
    # the episode row says 55 min, the show default 42 → episode wins
    assert vdb.get_expected_runtime_seconds("episode", 200, season=2, episode=6) == 55 * 60
    # unknown episode → show fallback
    assert vdb.get_expected_runtime_seconds("episode", 200, season=9, episode=9) == 42 * 60
    # tmdb id works too
    assert vdb.get_expected_runtime_seconds("episode", 201, season=9, episode=9) == 42 * 60
    assert vdb.get_expected_runtime_seconds("episode", "nope", season=1, episode=1) is None


def test_repair_owned_episode_files(vdb):
    rows = vdb.repair_owned_episode_files()
    assert len(rows) == 1
    r = rows[0]
    assert r["show_title"] == "Show" and r["season_number"] == 2 and r["episode_number"] == 6
    assert r["runtime_minutes"] == 55 and r["runtime_seconds"] == 1200


# ── deferred reclaim ──────────────────────────────────────────────────────────
def test_reclaim_source_removes_and_never_raises(tmp_path):
    f = tmp_path / "dl.mkv"
    f.write_bytes(b"x")
    mon._reclaim_source(str(f))
    assert not f.exists()
    mon._reclaim_source(str(f))          # already gone → no raise
    mon._reclaim_source(None)            # nothing to do → no raise


# ── dest pre-persist (move-mode crash window) ─────────────────────────────────
def _organizer_db(tmp_path, calls):
    lib = tmp_path / "lib"
    lib.mkdir(exist_ok=True)

    class _DB(_FakeDB):
        def update_video_download(self, dl_id, **kw):
            calls.append((dl_id, kw))

        def get_expected_runtime_seconds(self, *a, **k):
            return None

    settings = {"transfer_mode": "move", "verify_with_ffprobe": False,
                "save_artwork": False, "write_nfo": False, "download_subtitles": False,
                "carry_subtitles": False}
    return _DB({"organization": json.dumps(settings), "movies_path": str(lib)}), lib


def _movie_row(lib=""):
    return {
        "id": 7, "kind": "movie", "title": "The Matrix", "year": 1999,
        "source": "soulseek", "release_title": "The.Matrix.1999.1080p.BluRay",
        "size_bytes": 100, "target_dir": str(lib),
        "search_ctx": json.dumps({"scope": "movie", "title": "The Matrix", "year": 1999}),
    }


def test_organizer_persists_dest_before_the_file_moves(tmp_path):
    from core.video.download_monitor import _make_organizer
    dl_dir = tmp_path / "dl"
    dl_dir.mkdir()
    src = dl_dir / "The.Matrix.1999.1080p.BluRay.mkv"
    src.write_bytes(b"x" * 100)
    calls = []
    db, lib = _organizer_db(tmp_path, calls)
    organize = _make_organizer(db)
    patch = organize(_movie_row(lib), str(src))
    assert patch["status"] == "completed"
    # move mode: the source was relocated by the import...
    assert not src.exists()
    dest = patch["dest_path"]
    # ...but the destination hit the row BEFORE the completed persist: the first
    # update carrying dest_path must precede the tick's completed persist.
    dest_calls = [kw for _id, kw in calls if kw.get("dest_path") == dest]
    assert dest_calls, "dest_path was never pre-persisted"
    # what _tick does next: persist the completed patch...
    db.update_video_download(7, **{k: v for k, v in patch.items() if not k.startswith("_")})
    first_dest_idx = next(i for i, (_id, kw) in enumerate(calls) if kw.get("dest_path") == dest)
    completed_idx = next(i for i, (_id, kw) in enumerate(calls) if kw.get("status") == "completed")
    assert first_dest_idx < completed_idx


def test_prepersisted_dest_recovers_a_crash_before_the_completed_row(tmp_path):
    # Simulates the restart tick: the file was moved into the library, the process
    # died before the completed row persisted. dest_path was pre-persisted, the
    # source is gone — the tick must complete, not wedge at importing/100%.
    from core.video.download_monitor import _complete_via_file
    lib = tmp_path / "lib"
    lib.mkdir()
    dest = lib / "The Matrix (1999)" / "The Matrix (1999) Bluray-1080p.mkv"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"x" * 100)
    dl = dict(_movie_row(), status="importing", progress=100, dest_path=str(dest))
    out = _complete_via_file(dl, str(tmp_path / "dl"), lister=os.listdir,
                             mover=None, organizer=None)
    assert out["status"] == "completed" and out["dest_path"] == str(dest)


def test_run_import_accepts_a_precomputed_plan(tmp_path):
    # The monitor plans first (to pre-persist dest); run_import must not re-plan
    # or re-probe when handed that plan.
    from core.video import importer
    import tests.test_video_importer as ti
    lib = tmp_path / "lib"
    lib.mkdir()
    dl = _movie_row(lib)
    fs = ti.FakeFS()

    def _boom_prober(p):
        raise AssertionError("must not probe when a plan is supplied")

    plan = importer.plan_import(dl, "/dl/x/matrix.mkv", list_dir=fs.list_dir)
    assert plan["action"] == "import"
    patch = importer.run_import(dl, "/dl/x/matrix.mkv", fs=fs, prober=_boom_prober,
                                plan=plan)
    assert patch["status"] == "completed"
    assert patch["dest_path"] == plan["dest"]["path"]


def test_organizer_short_soulseek_file_fails_retryable_and_blocklists(tmp_path):
    # A truncated Soulseek transfer must retry another source (not sit as a
    # manual import_failed), and the (user, file) pair is tagged for blocklist.
    from core.video.download_monitor import _make_organizer
    dl_dir = tmp_path / "dl"
    dl_dir.mkdir()
    src = dl_dir / "Show.S01E01.1080p.WEB.mkv"
    src.write_bytes(b"x" * 100)                       # 100 bytes of 1000 advertised
    calls = []
    db, lib = _organizer_db(tmp_path, calls)
    organize = _make_organizer(db)
    dl = dict(_movie_row(lib), size_bytes=1000, username="someuser",
              filename="Show.S01E01.1080p.WEB.mkv")
    patch = organize(dl, str(src))
    assert patch["status"] == "failed"                 # retryable, via _fail_or_retry
    assert patch["_bad_release"] is True              # tick blocklists the pair
    assert "incomplete" in patch["error"]
    assert src.exists()                               # never touched on disk
