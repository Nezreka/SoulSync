"""Video download pipeline — the pure seams: slskd state classification + flatten,
file location + destination resolution, and the video.db downloads CRUD. Isolated."""

from __future__ import annotations

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
    assert classify_state("Queued, Remotely") == "active"
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
    assert upd == {"status": "downloading", "progress": 40.0}


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


def test_process_download_move_failure_marks_failed():
    from core.video.download_monitor import process_download

    def boom(s, d):
        raise OSError("disk full")

    upd = process_download(_dl(), [_xfer("Completed, Succeeded")], "/dl",
                           lister=lambda d: ["/dl/Folder/movie.mkv"], mover=boom)
    assert upd["status"] == "failed" and "disk full" in upd["error"]
