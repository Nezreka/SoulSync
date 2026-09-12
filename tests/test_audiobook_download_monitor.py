"""Tests for core/audiobook_download_monitor.py.

``process_download`` takes all of its I/O as arguments, so the whole state
machine is exercised here without a download client, a filesystem or a network.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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


def _whole_book(source, row):
    """The completeness gate saying the book is all there."""
    return {"complete": True, "reason": "600 of 600 minutes present"}


def _short_book(reason="about 180 minutes short", expired=False):
    def check(source, row):
        return {"complete": False, "reason": reason, "expired": expired}
    return check


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


def test_a_missing_client_job_waits_without_claiming_to_download():
    # A client restarting or a momentary timeout must not mark a perfectly
    # healthy download as broken.
    assert process_download(
        _row(), get_status=lambda s, r: None,
        resolve_path=_identity_path, organize=_ok_organize(),
    ) == {"status": "unavailable", "error": "Waiting for the download client to report this job"}


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
         patch("core.audiobook_download_monitor._check_complete", side_effect=_whole_book), \
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

@pytest.fixture(autouse=True)
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
         patch("core.audiobook_download_monitor._check_complete", side_effect=_whole_book), \
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


# ---------------------------------------------------------------------------
# The completeness gate
#
# A client says "complete" when the files it was ASKED for finished. A book
# missing its last chapters plays perfectly until the listener runs out of it,
# so an incomplete download is STAGED — kept, re-checked — not imported and not
# failed. Torrents finish late and uploaders repair releases.
# ---------------------------------------------------------------------------

def test_an_incomplete_book_is_never_imported():
    organized = []

    def organize(source, row):
        organized.append(source)
        return {"ok": True, "path": "/library/x"}

    patch_out = process_download(
        _row(), get_status=lambda s, r: _status("completed"),
        resolve_path=_identity_path, organize=organize,
        check_complete=_short_book(),
    )
    assert organized == []
    assert patch_out["status"] == "staged"


def test_a_staged_book_says_why():
    patch_out = process_download(
        _row(), get_status=lambda s, r: _status("completed"),
        resolve_path=_identity_path, organize=_ok_organize(),
        check_complete=_short_book("about 180 minutes short"),
    )
    assert "180 minutes short" in patch_out["completeness"]
    # Staged is not failed: nothing here is an error the user must act on.
    assert "error" not in patch_out


def test_a_staged_book_keeps_its_files():
    # save_path is recorded even while held, so the next tick re-checks the same
    # folder rather than losing track of it.
    patch_out = process_download(
        _row(), get_status=lambda s, r: _status("completed"),
        resolve_path=_identity_path, organize=_ok_organize(),
        check_complete=_short_book(),
    )
    assert patch_out["save_path"] == "/downloads/book"


def test_a_book_that_never_completes_eventually_fails():
    # Waiting is right; waiting forever means one broken release holds a row for
    # good.
    patch_out = process_download(
        _row(), get_status=lambda s, r: _status("completed"),
        resolve_path=_identity_path, organize=_ok_organize(),
        check_complete=_short_book("still short", expired=True),
    )
    assert patch_out["status"] == "failed"
    assert "Never completed" in patch_out["error"]


def test_a_whole_book_passes_the_gate_and_imports():
    patch_out = process_download(
        _row(), get_status=lambda s, r: _status("completed"),
        resolve_path=_identity_path, organize=_ok_organize("/library/Sanderson/Book"),
        check_complete=_whole_book,
    )
    assert patch_out["status"] == "completed"
    assert patch_out["imported_path"] == "/library/Sanderson/Book"


def test_without_a_gate_the_old_behaviour_holds():
    # check_complete is optional; omitting it imports whatever the client called
    # complete, which is what every existing caller did before the gate.
    patch_out = process_download(
        _row(), get_status=lambda s, r: _status("completed"),
        resolve_path=_identity_path, organize=_ok_organize(),
    )
    assert patch_out["status"] == "completed"


def test_a_staged_book_stays_in_the_active_queue(db):
    # It has to keep being re-checked; the usual reason it is short is a torrent
    # that has not finished yet.
    db.record_download("hash-1", "B1", "The Final Empire", "torrent", client_id="hash-1")
    db.update_download("hash-1", status="staged")
    assert [d["download_id"] for d in db.get_downloads(active_only=True)] == ["hash-1"]


def test_the_staged_reason_is_stored(db):
    db.record_download("hash-1", "B1", "The Final Empire", "torrent", client_id="hash-1")
    db.update_download("hash-1", status="staged", completeness="about 180 minutes short")
    assert db.get_downloads()[0]["completeness"] == "about 180 minutes short"


def test_a_staged_book_is_not_shown_as_an_error(db, clean_runtime_state):
    """Waiting for the rest of a book is a normal state, not a failure.

    The reason belongs in the audiobook database; putting it in error_message
    would paint the card red on the shared Downloads page as though something
    had gone wrong.
    """
    from core.audiobook_download_state import register_download

    tasks, _ = clean_runtime_state
    db.record_download("hash-1", "B1", "The Final Empire", "torrent", client_id="hash-1")
    register_download("hash-1", "The Final Empire")

    with patch("core.audiobook_download_monitor._get_status",
               return_value=_status("completed")), \
         patch("core.audiobook_download_monitor._resolve_path", side_effect=_identity_path), \
         patch("core.audiobook_download_monitor._check_complete",
               side_effect=_short_book("about 180 minutes short")):
        summary = tick(db=db)

    assert summary["staged"] == 1
    assert tasks["hash-1"]["status"] == "importing"
    assert not tasks["hash-1"]["error_message"]
    # The reason is kept where the audiobook UI can read it.
    assert "180 minutes short" in db.get_downloads()[0]["completeness"]


# ---------------------------------------------------------------------------
# Context captured at grab time
#
# Importing needs the series and narrator to shelve a book and the runtime to
# check it is whole. Asking Audible again hours later makes the import depend on
# a storefront that sheds load and occasionally pulls titles outright.
# ---------------------------------------------------------------------------

def _stored_book(**overrides):
    payload = {
        "asin": "B1",
        "title": "The Final Empire",
        "author_names": ["Brandon Sanderson"],
        "narrator_names": ["Michael Kramer"],
        "series": [{"title": "The Mistborn Saga", "sequence": "1"}],
        "runtime_minutes": 1479,
        "release_date": "2006-07-17",
        "cover_url": "https://img/500.jpg",
    }
    payload.update(overrides)
    return payload


def test_the_import_uses_the_book_captured_at_grab_time(db):
    from core.audiobook_download_monitor import _book_for

    db.record_download("hash-1", "B1", "The Final Empire", "torrent",
                       client_id="hash-1", book=_stored_book())
    row = db.get_downloads()[0]

    with patch("core.audiobook_client.get_audiobook_client") as client:
        book = _book_for(row)
    # The catalogue is never asked when the payload is already in hand.
    client.assert_not_called()
    assert book["series"][0]["title"] == "The Mistborn Saga"
    assert book["runtime_minutes"] == 1479


def test_an_older_row_still_falls_back_to_the_catalogue(db):
    from core.audiobook_client import AudiobookItem
    from core.audiobook_download_monitor import _book_for

    db.record_download("hash-1", "B1", "The Final Empire", "torrent", client_id="hash-1")
    row = db.get_downloads()[0]

    fetched = AudiobookItem(
        asin="B1", title="The Final Empire", subtitle="", authors=[], narrators=[],
        series=[], publisher="", summary="", short_summary="", release_date=None,
        runtime_minutes=1479, cover_url=None, cover_url_large=None, sample_url=None,
        rating=None, genres=[], language="english", format_type="", is_adult=False,
    )
    stub = type("S", (), {"get_book": lambda self, asin, marketplace="us": fetched})()
    with patch("core.audiobook_client.get_audiobook_client", return_value=stub):
        assert _book_for(row)["runtime_minutes"] == 1479


def test_a_book_still_files_when_nothing_can_be_looked_up(db):
    # Better to shelve under the author we know than to fail an import over
    # missing cover art.
    from core.audiobook_download_monitor import _book_for

    db.record_download("hash-1", "B1", "The Final Empire", "torrent",
                       client_id="hash-1", author="Brandon Sanderson")
    row = db.get_downloads()[0]
    with patch("core.audiobook_client.get_audiobook_client", side_effect=RuntimeError("down")):
        book = _book_for(row)
    assert book["title"] == "The Final Empire"
    assert book["author_names"] == ["Brandon Sanderson"]


def test_the_completeness_gate_uses_the_captured_runtime(db, tmp_path):
    # The gate cannot measure anything if the runtime has to be fetched and the
    # storefront is shedding.
    from core.audiobook_download_monitor import _check_complete

    db.record_download("hash-1", "B1", "The Final Empire", "torrent",
                       client_id="hash-1", book=_stored_book(runtime_minutes=600))
    row = db.get_downloads()[0]
    folder = tmp_path / "dl"
    folder.mkdir()
    (folder / "01.mp3").write_bytes(b"x")

    with patch("core.audiobook_client.get_audiobook_client") as client, \
         patch("core.audiobook_completeness.measure_duration_seconds", return_value=600 * 60.0):
        verdict = _check_complete(str(folder), row)
    client.assert_not_called()
    assert verdict["expected_minutes"] == 600
    assert verdict["complete"] is True


def test_the_organizer_is_told_which_release_filled_the_folder(db, tmp_path):
    # Without this a retry could interleave a second edition's chapters.
    from core.audiobook_download_monitor import _organize

    db.record_download("hash-1", "B1", "The Final Empire", "torrent",
                       client_id="hash-1", release_title="Mistborn.M4B-GRP",
                       book=_stored_book())
    row = db.get_downloads()[0]
    with patch("core.audiobook_organizer.organize_download",
               return_value={"ok": True, "path": "/x"}) as organize:
        _organize("/downloads/book", row)
    assert organize.call_args.kwargs["release_id"] == "Mistborn.M4B-GRP"


# ---------------------------------------------------------------------------
# A failed download is still a book you want
# ---------------------------------------------------------------------------

def test_a_failed_wishlist_book_stays_on_the_wishlist(db):
    from core.audiobook_database import STATUS_FAILED

    _wishlisted(db)
    db.record_download("hash-1", "B1", "The Final Empire", "torrent", client_id="hash-1")

    with patch("core.audiobook_download_monitor._get_status",
               return_value=_status("error", error="dead torrent")), \
         patch("core.audiobook_download_monitor._resolve_path", side_effect=_identity_path):
        tick(db=db)

    rows = db.get_wishlist()
    assert len(rows) == 1
    assert rows[0]["status"] == STATUS_FAILED
    # Back in the queue, not evaporated.
    assert len(db.get_wishlist_due(retry_after_seconds=0)) == 1


def test_a_failed_manual_grab_is_added_to_the_wishlist(db):
    # The one path where a book someone asked for could otherwise be silently
    # forgotten: grabbed straight from the detail page, never wishlisted, failed.
    assert db.get_wishlist() == []
    db.record_download("hash-1", "B1", "The Final Empire", "torrent",
                       client_id="hash-1", book=_stored_book())

    with patch("core.audiobook_download_monitor._get_status",
               return_value=_status("error", error="dead torrent")), \
         patch("core.audiobook_download_monitor._resolve_path", side_effect=_identity_path):
        tick(db=db)

    rows = db.get_wishlist()
    assert [r["asin"] for r in rows] == ["B1"]
    assert "dead torrent" in rows[0]["last_error"]


def test_a_failed_grab_with_nothing_to_go_on_adds_nothing(db):
    # No captured book and no catalogue: better an empty wishlist than a row
    # nothing could ever search for.
    db.record_download("hash-1", "", "", "torrent", client_id="hash-1")

    with patch("core.audiobook_download_monitor._get_status",
               return_value=_status("error", error="dead")), \
         patch("core.audiobook_download_monitor._resolve_path", side_effect=_identity_path):
        tick(db=db)
    assert db.get_wishlist() == []


# ---------------------------------------------------------------------------
# Cancelling
#
# Cancelling a card used to remove it from the Downloads page while the torrent
# carried on downloading.
# ---------------------------------------------------------------------------

def test_cancelling_a_card_stops_the_download(db, clean_runtime_state):
    from core.audiobook_download_state import register_download

    tasks, _ = clean_runtime_state
    db.record_download("hash-1", "B1", "The Final Empire", "torrent", client_id="hash-1")
    register_download("hash-1", "The Final Empire")
    tasks["hash-1"]["cancel_requested"] = True

    with patch("core.audiobook_download_monitor._cancel_at_client") as cancel, \
         patch("core.audiobook_download_monitor._get_status") as poll:
        summary = tick(db=db)

    cancel.assert_called_once()
    # No point polling something we just cancelled.
    poll.assert_not_called()
    assert summary["cancelled"] == 1
    assert db.get_downloads()[0]["status"] == "cancelled"
    assert "hash-1" not in tasks


def test_the_client_is_told_to_delete_the_partial_data(db, clean_runtime_state):
    from core.audiobook_download_monitor import _cancel_at_client

    adapter = MagicMock()

    async def remove(ref, delete_files=False):
        adapter.removed = (ref, delete_files)
        return True

    adapter.remove = remove
    with patch("core.torrent_clients.get_active_adapter", return_value=adapter):
        _cancel_at_client({"source": "torrent", "client_id": "hash-1"})
    assert adapter.removed == ("hash-1", True)


def test_a_client_that_cannot_be_reached_still_closes_the_card(db, clean_runtime_state):
    # Otherwise a cancelled card would sit on the page forever.
    from core.audiobook_download_state import register_download

    tasks, _ = clean_runtime_state
    db.record_download("hash-1", "B1", "The Final Empire", "torrent", client_id="hash-1")
    register_download("hash-1", "The Final Empire")
    tasks["hash-1"]["cancel_requested"] = True

    with patch("core.torrent_clients.get_active_adapter", side_effect=RuntimeError("down")):
        tick(db=db)

    assert db.get_downloads()[0]["status"] == "cancelled"
    assert "hash-1" not in tasks


def test_a_cancelled_book_is_not_returned_to_the_wishlist(db, clean_runtime_state):
    # Cancelling means "stop wanting this now", not "keep trying".
    from core.audiobook_download_state import register_download

    tasks, _ = clean_runtime_state
    db.record_download("hash-1", "B1", "The Final Empire", "torrent", client_id="hash-1")
    register_download("hash-1", "The Final Empire")
    tasks["hash-1"]["cancel_requested"] = True

    with patch("core.audiobook_download_monitor._cancel_at_client"):
        tick(db=db)
    assert db.get_wishlist() == []


# ---------------------------------------------------------------------------
# Soulseek downloads
# ---------------------------------------------------------------------------

def test_a_soulseek_row_is_polled_through_the_peer_client():
    from unittest.mock import patch

    from core.audiobook_download_monitor import _get_status

    rolled = {"state": "downloading", "progress": 42.0, "size": 100,
              "transferred": 42, "finished": 1, "failed": 0, "total": 3,
              "save_path": "/downloads/Book"}
    with patch("core.audiobook_soulseek.status_for", return_value=rolled):
        status = _get_status("soulseek", "{}")

    assert status.state == "downloading"
    assert status.progress == 0.42
    assert status.save_path == "/downloads/Book"


def test_a_soulseek_row_never_reaches_a_torrent_client():
    # A book must never be handed to the client the music side is using.
    from unittest.mock import patch

    from core.audiobook_download_monitor import _get_status

    with patch("core.audiobook_soulseek.status_for", return_value=None), \
         patch("core.torrent_clients.get_active_adapter") as torrent, \
         patch("core.usenet_clients.get_active_adapter") as usenet:
        assert _get_status("soulseek", "{}") is None
    torrent.assert_not_called()
    usenet.assert_not_called()


def test_cancelling_a_soulseek_book_stops_its_transfers():
    from unittest.mock import patch

    from core.audiobook_download_monitor import _cancel_at_client

    with patch("core.audiobook_soulseek.cancel", return_value=True) as stop, \
         patch("core.torrent_clients.get_active_adapter") as torrent:
        _cancel_at_client({"source": "soulseek", "client_id": "{}"})
    stop.assert_called_once()
    torrent.assert_not_called()


# ---------------------------------------------------------------------------
# Which folder the book is actually in
# ---------------------------------------------------------------------------

def _completed(**overrides):
    payload = {"state": "completed", "progress": 1.0, "size": 100, "downloaded": 100,
               "download_speed": 0}
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _capture_path(seen):
    def check(path, row):
        seen["path"] = path
        return {"complete": True, "reason": ""}
    return check


def test_a_finished_torrent_is_measured_in_its_own_folder():
    """content_path, not save_path.

    save_path is the shared directory the client saved INTO. The completeness
    gate walks what it is handed recursively, so handing it save_path measured
    every other download in that folder against this book's runtime — the book
    then staged forever and nothing ever reached the library. This is #1139
    again; the music album flow fixed it the same way.
    """
    seen = {}
    patch_out = process_download(
        _row(),
        get_status=lambda s, r: _completed(save_path="/downloads",
                                           content_path="/downloads/The Way of Kings"),
        resolve_path=_identity_path, organize=_ok_organize(),
        check_complete=_capture_path(seen),
    )

    assert seen["path"] == "/downloads/The Way of Kings"
    assert patch_out["save_path"] == "/downloads/The Way of Kings"


def test_usenet_still_uses_its_own_save_path():
    # SAB has no content_path; its save_path is already the job's own folder.
    seen = {}
    process_download(
        _row(),
        get_status=lambda s, r: _completed(save_path="/downloads/The Way of Kings"),
        resolve_path=_identity_path, organize=_ok_organize(),
        check_complete=_capture_path(seen),
    )
    assert seen["path"] == "/downloads/The Way of Kings"


def test_a_single_file_torrent_points_at_the_file_not_the_root():
    # Walking its parent would stage every other torrent's audio with it.
    seen = {}
    process_download(
        _row(),
        get_status=lambda s, r: _completed(save_path="/downloads",
                                           content_path="/downloads/Kings.m4b"),
        resolve_path=_identity_path, organize=_ok_organize(),
        check_complete=_capture_path(seen),
    )
    assert seen["path"] == "/downloads/Kings.m4b"

@pytest.mark.parametrize('percent', [0.5, 1.0, 42.0, 100.0])
def test_soulseek_progress_units_and_bytes_reach_monitor(percent):
    from core.audiobook_download_monitor import _SoulseekStatus
    status = _SoulseekStatus({'state':'downloading','progress':percent,'size':10000,
        'transferred':int(percent*100),'speed':300,'total':20,'finished':3})
    result = process_download(_row(source='soulseek'), get_status=lambda *_:status,
        resolve_path=_identity_path, organize=_ok_organize())
    assert result['progress'] == percent
    assert result['bytes_done'] == int(percent*100)
    assert result['speed'] == 300


def test_soulseek_finished_files_enter_import_instead_of_downloading_forever():
    from core.audiobook_download_monitor import _SoulseekStatus
    status = _SoulseekStatus({'state':'done','progress':100,'size':1000,
        'transferred':1000,'total':20,'finished':20,'save_path':'/downloads/Book'})
    organize = MagicMock(return_value={'ok':True,'path':'/library/Book','files':[]})
    result = process_download(_row(source='soulseek'), get_status=lambda *_:status,
        resolve_path=_identity_path, organize=organize, check_complete=_whole_book)
    assert result['status'] == 'completed'
    organize.assert_called_once()


def test_monitor_reattaches_after_restart_and_recovers_old_watchdog_failure(db, clean_runtime_state):
    from core.audiobook_download_monitor import _SoulseekStatus
    tasks, _ = clean_runtime_state
    db.record_download('book-live', 'B1', 'Rhythm of War', 'soulseek', client_id='refs')
    live = _SoulseekStatus({'state':'downloading','progress':25,'size':1000,
        'transferred':250,'speed':75,'total':12,'finished':3})
    with patch('core.audiobook_download_monitor._get_status', return_value=live):
        tick(db=db)
        assert tasks['book-live']['progress'] == 25
        assert tasks['book-live']['bytes_transferred'] == 250
        assert tasks['book-live']['speed'] == 75
        tasks['book-live']['status'] = 'failed'
        tasks['book-live']['error_message'] = 'Task stuck in downloading state for 10 minutes'
        live.progress = .5
        live.downloaded = 500
        tick(db=db)
    assert tasks['book-live']['status'] == 'downloading'
    assert tasks['book-live']['progress'] == 50
    assert tasks['book-live']['error_message'] is None


@pytest.mark.parametrize("client_state", ["queued", "paused", "downloading", "unavailable"])
def test_client_state_is_preserved(client_state):
    result = process_download(_row(), get_status=lambda s, r: _status(client_state),
                              resolve_path=_identity_path, organize=_ok_organize())
    assert result["status"] == client_state


def test_cancel_survives_cleanup_restart_and_late_poll(db, clean_runtime_state):
    from core.audiobook_download_monitor import cancel_downloads
    from core.audiobook_download_state import register_download
    tasks, batches = clean_runtime_state
    db.add_to_wishlist({"asin": "B1", "title": "Book"})
    db.mark_wishlist_status("B1", "grabbed")
    db.record_download("d1", "B1", "Book", "torrent", client_id="h1")
    register_download("d1", "Book")
    with patch("core.audiobook_download_monitor._cancel_at_client") as cancel:
        assert cancel_downloads(db=db) == 1
        cancel.assert_called_once()
    tasks.clear()
    batches.clear()
    assert not db.update_download("d1", status="downloading", progress=50)
    assert not db.update_download("d1", status="completed")
    with patch("core.audiobook_download_monitor._get_status") as poll:
        tick(db=db)
        poll.assert_not_called()
    assert not tasks and not batches
    assert db.get_downloads(active_only=True) == []
    assert db.get_wishlist()[0]["status"] == "cancelled"
    assert db.get_wishlist()[0]["download_status"] == "cancelled"
    assert db.get_wishlist_due() == []


def test_cancel_one_book_preserves_other_jobs(db, clean_runtime_state):
    from core.audiobook_download_monitor import cancel_downloads
    for i in (1, 2):
        db.record_download(f"d{i}", f"B{i}", f"Book {i}", "torrent", client_id=f"h{i}")
    with patch("core.audiobook_download_monitor._cancel_at_client"):
        assert cancel_downloads(["d1"], db=db) == 1
    assert [r["download_id"] for r in db.get_downloads(active_only=True)] == ["d2"]


@pytest.mark.parametrize("single", [False, True])
def test_download_page_cancel_routes_persist_before_runtime_cleanup(db, clean_runtime_state, single):
    # Execute the actual route body without starting web_server's background services.
    import ast
    import threading
    from pathlib import Path
    from flask import Flask, request, jsonify
    from core.audiobook_download_state import register_download
    tasks, _ = clean_runtime_state
    db.record_download("d1", "B1", "Book", "torrent", client_id="h1")
    register_download("d1", "Book")
    name = "cancel_task_v2" if single else "cancel_batch"
    tree = ast.parse((Path(__file__).parents[1] / "web_server.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    scope = {"request": request, "jsonify": jsonify, "tasks_lock": threading.Lock(),
             "_find_task_by_playlist_track": lambda *args: ("d1", tasks["d1"])}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "web_server.py", "exec"), scope)
    with Flask(__name__).test_request_context(json={"playlist_id": "audiobooks", "track_index": 0}), \
         patch("core.audiobook_database.get_audiobook_db", return_value=db), \
         patch("core.audiobook_download_monitor._cancel_at_client"):
        result = scope[name]() if single else scope[name]("audiobooks")
    assert result.get_json()["success"] is True
    assert db.get_downloads()[0]["status"] == "cancelled"
    assert "d1" not in tasks
