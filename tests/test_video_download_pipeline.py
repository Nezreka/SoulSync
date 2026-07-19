"""Video download pipeline — the pure seams: slskd state classification + flatten,
file location + destination resolution, and the video.db downloads CRUD. Isolated."""

from __future__ import annotations

import json

from core.video.download_pipeline import (
    basename_of,
    dest_path_for,
    find_completed_file,
    target_dir_for,
)
from core.video.slskd_download import (
    classify_state,
    find_transfer,
    flatten_downloads,
    progress_pct,
)


def test_classify_state():
    assert classify_state("Completed, Succeeded") == "completed"
    assert classify_state("InProgress") == "active"
    # Queued != downloading — waiting for a slot, not moving bytes yet.
    assert classify_state("Queued, Remotely") == "queued"
    assert classify_state("Queued, Locally") == "queued"
    assert classify_state("Requested") == "queued"
    assert classify_state("Completed, Errored") == "failed"
    assert classify_state("Completed, Cancelled") == "cancelled"
    assert classify_state("Completed, TimedOut") == "failed"
    assert classify_state("") == "active"


def test_flatten_and_find_transfer():
    data = [{"username": "neo", "directories": [
        {"files": [{"filename": r"@@a\Movie\movie.mkv", "id": "t1", "state": "InProgress",
                    "size": 100, "bytesTransferred": 25}]}]}]
    flat = flatten_downloads(data)
    assert len(flat) == 1 and flat[0]["username"] == "neo" and flat[0]["id"] == "t1"
    assert progress_pct(flat[0]) == 25.0
    assert find_transfer(flat, "neo", r"@@a\Movie\movie.mkv")["id"] == "t1"
    assert find_transfer(flat, "neo", "other") == {}
    assert flatten_downloads(None) == []


def test_progress_is_100_when_completed():
    assert progress_pct({"state": "Completed, Succeeded", "size": 100, "transferred": 0}) == 100.0


def test_basename_handles_both_separators():
    assert basename_of(r"@@x\The.Wire.S02\ep.mkv") == "ep.mkv"
    assert basename_of("a/b/c/movie.mp4") == "movie.mp4"
    assert basename_of("") == ""


def test_find_completed_file_by_basename():
    files = ["/dl/SomeFolder/movie.mkv", "/dl/Other/nope.mkv"]
    assert find_completed_file("/dl", r"@@u\Remote\movie.mkv", lambda d: files) == "/dl/SomeFolder/movie.mkv"
    assert find_completed_file("/dl", "missing.mkv", lambda d: files) is None


def test_dest_and_target_resolution():
    assert dest_path_for("/media/movies", "/dl/x/movie.mkv") == "/media/movies/movie.mkv"
    paths = {"movies_path": "/m", "tv_path": "/t", "youtube_path": "/y"}
    assert target_dir_for("movie", paths) == "/m"
    assert target_dir_for("show", paths) == "/t"
    assert target_dir_for("season", paths) == "/t"
    assert target_dir_for("youtube", paths) == "/y"
    assert target_dir_for("weird", paths) == ""


# ── DB CRUD ───────────────────────────────────────────────────────────────────
def test_video_downloads_crud(tmp_path):
    from database.video_database import VideoDatabase
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    dl_id = db.add_video_download({
        "kind": "movie", "title": "The Matrix", "release_title": "The.Matrix.1999.1080p",
        "source": "soulseek", "username": "neo", "filename": r"@@a\x\m.mkv",
        "size_bytes": 8_000_000_000, "target_dir": "/media/movies", "status": "downloading",
    })
    assert dl_id > 0
    active = db.get_active_video_downloads()
    assert len(active) == 1 and active[0]["title"] == "The Matrix"
    db.update_video_download(dl_id, status="completed", progress=100, dest_path="/media/movies/m.mkv")
    assert db.get_active_video_downloads() == []
    listed = db.list_video_downloads()
    assert listed[0]["status"] == "completed" and listed[0]["dest_path"] == "/media/movies/m.mkv"
    assert db.clear_finished_video_downloads() == 1
    assert db.list_video_downloads() == []


# ── monitor decision (pure, injected fs) ──────────────────────────────────────
def _dl(**kw):
    base = {"id": 1, "username": "neo", "filename": r"@@a\Folder\movie.mkv", "target_dir": "/media/movies"}
    base.update(kw)
    return base


def _xfer(state, **kw):
    base = {"username": "neo", "filename": r"@@a\Folder\movie.mkv", "state": state, "size": 100, "transferred": 40}
    base.update(kw)
    return base


def test_process_download_active_reports_progress():
    from core.video.download_monitor import process_download
    upd = process_download(_dl(), [_xfer("InProgress")], "/dl",
                           lister=lambda d: [], mover=lambda s, d: None)
    assert upd["status"] == "downloading" and upd["progress"] == 40.0
    assert "speed_bps" in upd and "eta_seconds" in upd   # live telemetry rides the patch


def test_process_download_queued_reports_queued():
    """A slskd-queued transfer must read as 'queued', not 'downloading' (the disconnect
    where a whole batch waiting for slots showed as actively downloading)."""
    from core.video.download_monitor import process_download
    upd = process_download(_dl(), [_xfer("Queued, Remotely")], "/dl",
                           lister=lambda d: [], mover=lambda s, d: None)
    assert upd["status"] == "queued"


class _WlDB:
    def __init__(self):
        self.eps = None
        self.movie = None
        self.removed = None
    def add_episodes_to_wishlist(self, tmdb_id, title, episodes, *, poster_url=None, library_id=None, server_source=None):
        self.eps = (tmdb_id, title, episodes, library_id)
        return len(episodes)
    def add_movie_to_wishlist(self, tmdb_id, title, *, year=None, poster_url=None, library_id=None, server_source=None):
        self.movie = (tmdb_id, title, library_id)
        return True
    def remove_from_wishlist(self, scope, *, tmdb_id, season_number=None, episode_number=None):
        self.removed = (scope, tmdb_id, season_number, episode_number)
        return 1
    def show_tmdb_id(self, show_id):
        return 999
    def movie_tmdb_id(self, movie_id):
        return 888


def test_wishlist_obtained_removes_episode():
    """Bug 1: a wished episode that downloads is REMOVED from the wishlist (else it
    re-grabs every hourly run forever)."""
    from core.video.download_monitor import _wishlist_obtained
    db = _WlDB()
    _wishlist_obtained(db, {"id": 1, "kind": "show", "title": "T", "media_id": "123",
                            "media_source": "tmdb", "search_ctx": json.dumps({"season": 1, "episode": 3})})
    assert db.removed == ("episode", 123, 1, 3)


def test_wishlist_obtained_removes_movie_and_resolves_library_tmdb():
    from core.video.download_monitor import _wishlist_obtained
    db = _WlDB()
    _wishlist_obtained(db, {"id": 2, "kind": "movie", "title": "M", "media_id": "42",
                            "media_source": "library"})
    assert db.removed == ("movie", 888, None, None)   # tmdb resolved from the library id


def test_wishlist_failed_episode_tmdb_source():
    """A gave-up TMDB episode grab goes back on the wishlist under the show's tmdb_id."""
    from core.video.download_monitor import _wishlist_failed
    db = _WlDB()
    _wishlist_failed(db, {"id": 1, "kind": "show", "title": "The Show", "media_id": "123",
                          "media_source": "tmdb", "search_ctx": json.dumps({"season": 1, "episode": 3})})
    assert db.eps == (123, "The Show", [{"season_number": 1, "episode_number": 3}], None)


def test_wishlist_failed_episode_library_resolves_tmdb():
    """A library episode grab resolves the show's tmdb_id from the DB and keeps library_id."""
    from core.video.download_monitor import _wishlist_failed
    db = _WlDB()
    _wishlist_failed(db, {"id": 2, "kind": "show", "title": "Lib Show", "media_id": "42",
                          "media_source": "library", "search_ctx": {"season": 2, "episode": 5}})
    assert db.eps == (999, "Lib Show", [{"season_number": 2, "episode_number": 5}], "42")


def test_complete_via_file_marks_done_when_already_placed():
    """#4: a completed transfer whose file is gone but was already placed (dest_path set)
    resolves to 'completed' instead of looping at importing/100%."""
    from core.video.download_monitor import _complete_via_file
    upd = _complete_via_file({"id": 1, "filename": "x.mkv", "dest_path": "/lib/x.mkv"},
                             "/dl", lambda d: [], lambda s, d: None, None)
    assert upd == {"status": "completed", "progress": 100.0, "dest_path": "/lib/x.mkv"}


def test_complete_via_file_waits_when_no_file_and_no_dest():
    from core.video.download_monitor import _complete_via_file
    upd = _complete_via_file({"id": 2, "filename": "x.mkv"}, "/dl", lambda d: [], lambda s, d: None, None)
    assert upd == {"progress": 100.0}


def test_tick_readopts_orphaned_searching_row(monkeypatch):
    """#1: a 'searching' row whose requery thread is gone (restart) gets re-adopted."""
    import core.video.download_monitor as m
    spawned = []
    monkeypatch.setattr(m, "_spawn_requery", lambda dl_id: spawned.append(dl_id))
    m._requerying.clear()

    class _DB:
        def get_active_video_downloads(self):
            return [{"id": 7, "status": "searching", "source": "soulseek"}]

    m._tick(_DB())
    assert spawned == [7]


def test_active_episode_keys_dedups_by_title():
    """#3: only same-show (by title) episode downloads count toward in-flight dedup."""
    from api.video.downloads import _active_episode_keys

    class _DB:
        def get_active_video_downloads(self):
            return [
                {"kind": "show", "title": "The Show", "search_ctx": json.dumps({"season": 1, "episode": 1})},
                {"kind": "show", "title": "The Show", "search_ctx": {"season": 1, "episode": 2}},
                {"kind": "show", "title": "Other Show", "search_ctx": {"season": 1, "episode": 9}},
                {"kind": "movie", "title": "The Show", "search_ctx": {}},
            ]

    assert _active_episode_keys(_DB(), "The Show") == {(1, 1), (1, 2)}


def test_process_download_failed():
    from core.video.download_monitor import process_download
    upd = process_download(_dl(), [_xfer("Completed, Errored")], "/dl",
                           lister=lambda d: [], mover=lambda s, d: None)
    assert upd["status"] == "failed"


def test_process_download_cancelled():
    from core.video.download_monitor import process_download
    upd = process_download(_dl(), [_xfer("Completed, Cancelled")], "/dl",
                           lister=lambda d: [], mover=lambda s, d: None)
    assert upd["status"] == "cancelled"


def test_process_download_missing_transfer_signals_missing():
    from core.video.download_monitor import process_download
    # slskd forgot it AND no file on disk → _missing (caller decides when to give up)
    upd = process_download(_dl(), [], "/dl", lister=lambda d: [], mover=lambda s, d: None)
    assert upd == {"_missing": True}


def test_process_download_missing_but_file_present_completes():
    from core.video.download_monitor import process_download
    moved = {}
    # slskd cleared the completed transfer (the music auto-clear) but the file is there
    upd = process_download(_dl(), [], "/dl",
                           lister=lambda d: ["/dl/Folder/movie.mkv"],
                           mover=lambda s, d: moved.update(ok=True))
    assert upd["status"] == "completed" and moved.get("ok") is True


def test_process_download_completed_moves_file():
    from core.video.download_monitor import process_download
    moved = {}
    upd = process_download(
        _dl(), [_xfer("Completed, Succeeded")], "/dl",
        lister=lambda d: ["/dl/Folder/movie.mkv"],
        mover=lambda s, d: moved.update(src=s, dest=d))
    assert upd == {"status": "completed", "progress": 100.0, "dest_path": "/media/movies/movie.mkv"}
    assert moved == {"src": "/dl/Folder/movie.mkv", "dest": "/media/movies/movie.mkv"}


def test_process_download_completed_but_file_not_settled():
    from core.video.download_monitor import process_download
    upd = process_download(_dl(), [_xfer("Completed, Succeeded")], "/dl",
                           lister=lambda d: [], mover=lambda s, d: None)
    assert upd == {"progress": 100.0}   # no status change — retries next tick


def test_fail_or_retry_starts_next_candidate(tmp_path, monkeypatch):
    import json
    import core.video.download_monitor as mon
    import core.video.slskd_download as slskd
    from database.video_database import VideoDatabase

    monkeypatch.setattr(slskd, "start_download", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(mon, "start_download", lambda *a, **k: {"ok": True})

    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    dl_id = db.add_video_download({
        "kind": "movie", "title": "Dune", "release_title": "Dune.2021.1080p.x265-A",
        "source": "soulseek", "username": "alice", "filename": r"@@a\A\dune.mkv",
        "target_dir": "/m", "status": "downloading", "attempts": 0,
        "tried_files": json.dumps([r"@@a\A\dune.mkv"]),
        "candidates": json.dumps([{"username": "bob", "filename": r"@@b\B\dune.mkv",
                                   "size_bytes": 9, "quality_label": "1080p", "release_title": "Dune.2021.1080p.x265-B"}]),
        "search_ctx": json.dumps({"scope": "movie", "title": "Dune", "year": 2021}),
        "tried_queries": json.dumps(["Dune 2021"]),
    })
    row = db.get_video_download(dl_id)
    mon._fail_or_retry(db, row, "Soulseek transfer Errored")
    after = db.get_video_download(dl_id)
    # rolled onto the next candidate (bob's release), back to downloading, attempt counted
    assert after["status"] == "downloading" and after["username"] == "bob"
    assert after["release_title"] == "Dune.2021.1080p.x265-B" and after["attempts"] == 1
    assert json.loads(after["candidates"]) == []   # pool consumed


def test_fail_or_retry_marks_failed_when_exhausted(tmp_path, monkeypatch):
    import json
    import core.video.download_monitor as mon
    from database.video_database import VideoDatabase
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    dl_id = db.add_video_download({
        "kind": "movie", "title": "Dune", "source": "soulseek", "username": "a",
        "filename": "x.mkv", "target_dir": "/m", "status": "downloading", "attempts": 0,
        "candidates": "[]", "tried_files": json.dumps(["x.mkv"]),
        "search_ctx": json.dumps({"scope": "movie", "title": "Dune"}),   # no year → only one query
        "tried_queries": json.dumps(["Dune"]),
    })
    mon._fail_or_retry(db, db.get_video_download(dl_id), "boom")
    assert db.get_video_download(dl_id)["status"] == "failed"


def test_process_download_move_failure_marks_failed():
    from core.video.download_monitor import process_download

    def boom(s, d):
        raise OSError("disk full")

    upd = process_download(_dl(), [_xfer("Completed, Succeeded")], "/dl",
                           lister=lambda d: ["/dl/Folder/movie.mkv"], mover=boom)
    assert upd["status"] == "failed" and "disk full" in upd["error"]
