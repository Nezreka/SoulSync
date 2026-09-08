"""Tests for core/audiobook_download_monitor.py.

``process_download`` takes all of its I/O as arguments, so the whole state
machine is exercised here without a download client, a filesystem or a network.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.audiobook_database import STATUS_DONE, STATUS_FAILED, AudiobookDatabase
from core.audiobook_download_monitor import (
    AudiobookDownloadMonitor,
    normalize_state,
    process_download,
    tick,
)


def _row(**overrides):
    payload = {
        "download_id": "d1",
        "asin": "B1",
        "title": "The Final Empire",
        "author": "Brandon Sanderson",
        "source": "torrent",
        "client_id": "hash-1",
        "status": "downloading",
    }
    payload.update(overrides)
    return payload


def _status(state="downloading", **overrides):
    payload = {"state": state, "progress": 0.5, "downloaded": 50, "size": 100,
               "save_path": "/downloads/book"}
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _ok_organize(path="/library/Author/Book"):
    return lambda source, row: {"ok": True, "path": path, "files": [], "skipped": []}


def _failed_organize(error="no audio"):
    return lambda source, row: {"ok": False, "error": error}


def _identity_path(reported):
    return reported


# ---------------------------------------------------------------------------
# State vocabulary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", ["error", "failed", "ERROR"])
def test_failure_states(state):
    assert normalize_state(SimpleNamespace(state=state)) == "failed"


@pytest.mark.parametrize("state", ["completed", "complete", "finished", "succeeded"])
def test_completion_states(state):
    assert normalize_state(SimpleNamespace(state=state)) == "completed"


def test_a_seeding_torrent_counts_as_finished():
    # The files are on disk; waiting for it to stop seeding would hold the book
    # hostage to a ratio.
    assert normalize_state(SimpleNamespace(state="seeding")) == "completed"


@pytest.mark.parametrize("state", ["downloading", "queued", "stalled", "", None])
def test_everything_else_is_still_downloading(state):
    assert normalize_state(SimpleNamespace(state=state)) == "downloading"


# ---------------------------------------------------------------------------
# One tick
# ---------------------------------------------------------------------------

def test_progress_is_recorded_while_downloading():
    patch_out = process_download(
        _row(), get_status=lambda s, r: _status(),
        resolve_path=_identity_path, organize=_ok_organize(),
    )
    assert patch_out["status"] == "downloading"
    assert patch_out["progress"] == 50.0
    assert patch_out["bytes_done"] == 50
    assert patch_out["bytes_total"] == 100


def test_progress_reported_as_a_percentage_is_not_doubled():
    # Adapters disagree: some report 0-1, some 0-100.
    patch_out = process_download(
        _row(), get_status=lambda s, r: _status(progress=75),
        resolve_path=_identity_path, organize=_ok_organize(),
    )
    assert patch_out["progress"] == 75.0


def test_a_finished_download_is_organized_and_completed():
    patch_out = process_download(
        _row(), get_status=lambda s, r: _status("completed"),
        resolve_path=_identity_path, organize=_ok_organize("/library/Sanderson/Book"),
    )
    assert patch_out["status"] == "completed"
    assert patch_out["progress"] == 100.0
    assert patch_out["imported_path"] == "/library/Sanderson/Book"
    assert patch_out["save_path"] == "/downloads/book"


def test_a_client_failure_is_recorded():
    patch_out = process_download(
        _row(), get_status=lambda s, r: _status("error", error="tracker gone"),
        resolve_path=_identity_path, organize=_ok_organize(),
    )
    assert patch_out["status"] == "failed"
    assert "tracker gone" in patch_out["error"]


def test_a_failed_organize_fails_the_download():
    patch_out = process_download(
        _row(), get_status=lambda s, r: _status("completed"),
        resolve_path=_identity_path, organize=_failed_organize("No audio files found"),
    )
    assert patch_out["status"] == "failed"
    assert "No audio files" in patch_out["error"]


def test_a_poll_that_returns_nothing_changes_nothing():
    # A client restarting or a momentary timeout must not mark a perfectly
    # healthy download as broken.
    assert process_download(
        _row(), get_status=lambda s, r: None,
        resolve_path=_identity_path, organize=_ok_organize(),
    ) == {}


def test_a_complete_download_with_no_visible_path_waits():
    # Finished, but the path is not mounted here yet — try again next tick
    # rather than failing a book that is actually on disk.
    patch_out = process_download(
        _row(), get_status=lambda s, r: _status("completed"),
        resolve_path=lambda reported: None, organize=_ok_organize(),
    )
    assert patch_out["status"] == "downloading"
    assert "error" not in patch_out


def test_a_row_with_no_client_reference_fails_immediately():
    patch_out = process_download(
        _row(client_id=""), get_status=lambda s, r: _status(),
        resolve_path=_identity_path, organize=_ok_organize(),
    )
    assert patch_out["status"] == "failed"


def test_organize_is_never_called_before_completion():
    calls = []

    def organize(source, row):
        calls.append(source)
        return {"ok": True, "path": "/x"}

    process_download(_row(), get_status=lambda s, r: _status("downloading"),
                     resolve_path=_identity_path, organize=organize)
    assert calls == []


# ---------------------------------------------------------------------------
# A pass over the database
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    return AudiobookDatabase(str(tmp_path / "audiobooks.db"))


def _wishlisted(db, asin="B1"):
    db.add_to_wishlist({"asin": asin, "title": "The Final Empire",
                        "author_names": ["Brandon Sanderson"]})


def test_a_completed_download_reaches_the_library(db):
    _wishlisted(db)
    db.record_download("d1", "B1", "The Final Empire", "torrent", client_id="hash-1")

    with patch("core.audiobook_download_monitor._get_status",
               return_value=_status("completed")), \
         patch("core.audiobook_download_monitor._resolve_path", side_effect=_identity_path), \
         patch("core.audiobook_download_monitor._organize",
               return_value={"ok": True, "path": "/library/Sanderson/Book"}):
        summary = tick(db=db)

    assert summary["completed"] == 1
    assert db.get_downloads()[0]["status"] == "completed"
    assert db.get_wishlist()[0]["status"] == STATUS_DONE
    assert db.is_owned("B1") is True


def test_a_failed_download_marks_the_wishlist_row(db):
    # So the wishlist retries it later instead of believing it was handled.
    _wishlisted(db)
    db.record_download("d1", "B1", "The Final Empire", "torrent", client_id="hash-1")

    with patch("core.audiobook_download_monitor._get_status",
               return_value=_status("error", error="dead torrent")), \
         patch("core.audiobook_download_monitor._resolve_path", side_effect=_identity_path):
        summary = tick(db=db)

    assert summary["failed"] == 1
    assert db.get_wishlist()[0]["status"] == STATUS_FAILED
    assert "dead torrent" in db.get_wishlist()[0]["last_error"]


def test_only_active_downloads_are_polled(db):
    db.record_download("d1", "B1", "One", "torrent", client_id="h1")
    db.update_download("d1", status="completed")
    with patch("core.audiobook_download_monitor._get_status") as poll:
        summary = tick(db=db)
    poll.assert_not_called()
    assert summary["checked"] == 0


def test_an_empty_queue_is_a_no_op(db):
    with patch("core.audiobook_download_monitor._get_status") as poll:
        assert tick(db=db)["checked"] == 0
    poll.assert_not_called()


def test_a_broken_database_does_not_raise():
    class Broken:
        def get_downloads(self, active_only=False):
            raise RuntimeError("db gone")

    assert tick(db=Broken())["checked"] == 0


# ---------------------------------------------------------------------------
# The monitor thread
# ---------------------------------------------------------------------------

def test_starting_twice_does_not_start_two_threads():
    monitor = AudiobookDownloadMonitor()
    try:
        with patch("core.audiobook_download_monitor.tick", return_value={}):
            assert monitor.start() is True
            assert monitor.start() is False
    finally:
        monitor.stop(timeout=1)


def test_stop_is_safe_when_never_started():
    AudiobookDownloadMonitor().stop(timeout=1)


def test_the_poll_interval_cannot_be_set_to_a_hammer():
    monitor = AudiobookDownloadMonitor()
    with patch("core.settings.config_manager.get", return_value=0):
        assert monitor.poll_seconds() >= 5.0


def test_status_reports_the_loop_state():
    status = AudiobookDownloadMonitor().status()
    for key in ("running", "poll_seconds", "last_run_at", "last_summary"):
        assert key in status


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------

def test_the_monitor_never_touches_music_download_state():
    import ast
    import inspect

    import core.audiobook_download_monitor as module

    tree = ast.parse(inspect.getsource(module))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    for name in imported:
        for forbidden in ("core.downloads", "core.runtime_state", "core.wishlist",
                          "core.download_engine", "core.download_orchestrator",
                          "database", "core.video", "web_server"):
            assert not name.startswith(forbidden), f"the monitor imports {name}"


def test_the_monitor_reuses_the_music_path_resolver():
    # The downloader reports paths from inside its own container. The music side
    # already solved that; re-deriving it would drift.
    import inspect

    import core.audiobook_download_monitor as module

    assert "resolve_reported_save_path" in inspect.getsource(module)


# ---------------------------------------------------------------------------
# The Downloads page
#
# Audiobooks appear on the existing page with the existing cards, the way
# podcasts do — by writing into the shared runtime state with the flags
# is_music_batch() already honours. No music file changes for this to work.
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_runtime_state():
    from core.runtime_state import download_batches, download_tasks

    tasks_before, batches_before = dict(download_tasks), dict(download_batches)
    yield download_tasks, download_batches
    download_tasks.clear()
    download_tasks.update(tasks_before)
    download_batches.clear()
    download_batches.update(batches_before)


def test_progress_reaches_the_downloads_card(db, clean_runtime_state):
    from core.audiobook_download_state import register_download

    tasks, _ = clean_runtime_state
    db.record_download("hash-1", "B1", "The Final Empire", "torrent", client_id="hash-1")
    register_download("hash-1", "The Final Empire", author="Brandon Sanderson")

    with patch("core.audiobook_download_monitor._get_status",
               return_value=_status(progress=0.4, downloaded=40, size=100)), \
         patch("core.audiobook_download_monitor._resolve_path", side_effect=_identity_path):
        tick(db=db)

    assert tasks["hash-1"]["progress"] == 40.0
    assert tasks["hash-1"]["bytes_transferred"] == 40


def test_a_finished_book_clears_its_card(db, clean_runtime_state):
    # The history lives in the audiobook database; runtime state is only what
    # is happening now.
    from core.audiobook_download_state import register_download

    tasks, batches = clean_runtime_state
    _wishlisted(db)
    db.record_download("hash-1", "B1", "The Final Empire", "torrent", client_id="hash-1")
    register_download("hash-1", "The Final Empire")

    with patch("core.audiobook_download_monitor._get_status",
               return_value=_status("completed")), \
         patch("core.audiobook_download_monitor._resolve_path", side_effect=_identity_path), \
         patch("core.audiobook_download_monitor._organize",
               return_value={"ok": True, "path": "/library/Sanderson/Book"}):
        tick(db=db)

    assert "hash-1" not in tasks
    assert db.get_downloads()[0]["status"] == "completed"


def test_a_failed_book_says_so_on_its_card(db, clean_runtime_state):
    from core.audiobook_download_state import register_download

    tasks, _ = clean_runtime_state
    _wishlisted(db)
    db.record_download("hash-1", "B1", "The Final Empire", "torrent", client_id="hash-1")
    register_download("hash-1", "The Final Empire")

    with patch("core.audiobook_download_monitor._get_status",
               return_value=_status("error", error="dead torrent")), \
         patch("core.audiobook_download_monitor._resolve_path", side_effect=_identity_path):
        tick(db=db)

    assert tasks["hash-1"]["status"] == "failed"
    assert "dead torrent" in tasks["hash-1"]["error_message"]


def test_the_music_engine_still_refuses_the_batch_while_downloading(db, clean_runtime_state):
    # Asked of the real guard, with a live audiobook download in flight.
    from core.audiobook_download_state import BATCH_ID, register_download
    from core.downloads.lifecycle import is_music_batch

    _, batches = clean_runtime_state
    register_download("hash-1", "The Final Empire")
    assert is_music_batch(BATCH_ID, batches[BATCH_ID]) is False
