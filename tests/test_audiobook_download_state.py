"""Tests for core/audiobook_download_state.py.

The point of this module is to appear on the existing Downloads page WITHOUT
joining the music pipeline, so most of these assert the isolation rather than
the rendering — and they do it by calling the real music guard,
``core.downloads.lifecycle.is_music_batch``, rather than by re-stating what it
is believed to do.
"""

import pytest

from core.audiobook_download_state import (
    BATCH_ID,
    forget,
    is_cancelled,
    mark_status,
    register_download,
    update_progress,
)
from core.runtime_state import download_batches, download_tasks


@pytest.fixture(autouse=True)
def clean_runtime_state():
    """Leave the process-wide download state exactly as it was found."""
    tasks_before = dict(download_tasks)
    batches_before = dict(download_batches)
    yield
    download_tasks.clear()
    download_tasks.update(tasks_before)
    download_batches.clear()
    download_batches.update(batches_before)


def _register(task_id="hash-1", **overrides):
    payload = {
        "task_id": task_id,
        "title": "The Final Empire",
        "author": "Brandon Sanderson",
        "series": "The Mistborn Saga",
        "artwork_url": "https://img/500.jpg",
        "protocol": "torrent",
        "size_bytes": 700 * 1024 * 1024,
    }
    payload.update(overrides)
    return register_download(**payload)


# ---------------------------------------------------------------------------
# Isolation — the whole reason this module exists
# ---------------------------------------------------------------------------

def test_the_music_engine_refuses_the_audiobook_batch():
    # Asked of the REAL guard the music worker pool, the batch healer and the
    # music wishlist failure processor all consult.
    from core.downloads.lifecycle import is_music_batch

    _register()
    assert is_music_batch(BATCH_ID, download_batches[BATCH_ID]) is False


def test_the_batch_carries_both_isolation_flags():
    # Either alone is enough for is_music_batch; both are set so a refactor of
    # one cannot silently re-enlist audiobooks into the music pipeline.
    _register()
    batch = download_batches[BATCH_ID]
    assert batch["is_music"] is False
    assert batch["managed_externally"] is True
    assert batch["batch_type"] == "audiobook"
    assert batch["source_page"] == "Audiobooks"


def test_the_flags_are_restamped_on_every_registration():
    # A batch that lost these would be picked up by the music workers next pass.
    _register("hash-1")
    download_batches[BATCH_ID]["is_music"] = True
    download_batches[BATCH_ID]["managed_externally"] = False
    _register("hash-2")
    batch = download_batches[BATCH_ID]
    assert batch["is_music"] is False
    assert batch["managed_externally"] is True


def test_audiobooks_never_touch_the_music_batches():
    from core.downloads.lifecycle import is_music_batch

    download_batches["playlist-42"] = {"playlist_id": "pl", "playlist_name": "Favourites"}
    _register()
    # The music batch is untouched and still reads as music.
    assert is_music_batch("playlist-42", download_batches["playlist-42"]) is True
    assert download_batches["playlist-42"] == {"playlist_id": "pl", "playlist_name": "Favourites"}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_a_grabbed_book_appears_as_a_card():
    assert _register() is True
    task = download_tasks["hash-1"]
    assert task["status"] == "queued"
    assert task["batch_id"] == BATCH_ID
    assert task["track_info"]["title"] == "The Final Empire"
    # The cards read the music shape; author and series are the honest mapping.
    assert task["track_info"]["artist"] == "Brandon Sanderson"
    assert task["track_info"]["album"] == "The Mistborn Saga"
    assert task["track_info"]["artwork_url"] == "https://img/500.jpg"


def test_the_client_reference_is_the_card_id():
    # The card and the thing being polled have to be the same row, or progress
    # lands on nothing.
    _register("qbit-info-hash")
    assert "qbit-info-hash" in download_tasks
    assert "qbit-info-hash" in download_batches[BATCH_ID]["queue"]


def test_a_book_with_no_series_still_files_under_audiobooks():
    _register(series="")
    assert download_tasks["hash-1"]["track_info"]["album"] == "Audiobooks"


def test_the_source_names_the_protocol():
    _register(protocol="usenet")
    assert download_tasks["hash-1"]["download_source"] == "Audiobook (usenet)"


def test_several_books_share_one_batch():
    _register("a")
    _register("b")
    assert download_batches[BATCH_ID]["queue"] == ["a", "b"]
    assert download_tasks["b"]["track_index"] == 1


def test_registering_the_same_reference_twice_does_not_duplicate_the_card():
    _register("a")
    _register("a")
    assert download_batches[BATCH_ID]["queue"] == ["a"]


@pytest.mark.parametrize("task_id,title", [("", "Book"), ("  ", "Book"), ("id", ""), (None, "Book")])
def test_unusable_registrations_are_refused(task_id, title):
    assert register_download(task_id=task_id, title=title) is False


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------

def test_progress_updates_the_card():
    _register()
    update_progress("hash-1", percent=42.5, bytes_done=1024, bytes_total=4096)
    task = download_tasks["hash-1"]
    assert task["progress"] == 42.5
    assert task["bytes_transferred"] == 1024
    assert task["size"] == 4096


def test_progress_leaves_out_what_the_poll_did_not_report():
    # A client that reports a percentage but no byte counts must not blank them.
    _register()
    update_progress("hash-1", bytes_done=50, bytes_total=100)
    update_progress("hash-1", percent=80)
    task = download_tasks["hash-1"]
    assert task["progress"] == 80
    assert task["bytes_transferred"] == 50


def test_progress_is_clamped():
    _register()
    update_progress("hash-1", percent=140)
    assert download_tasks["hash-1"]["progress"] == 100.0
    update_progress("hash-1", percent=-10)
    assert download_tasks["hash-1"]["progress"] == 0.0


def test_progress_for_an_unknown_card_is_ignored():
    update_progress("never-registered", percent=50)
    assert "never-registered" not in download_tasks


# ---------------------------------------------------------------------------
# Terminal states
# ---------------------------------------------------------------------------

def test_completion_fills_the_card_in():
    _register()
    mark_status("hash-1", "completed", file_path="/library/Sanderson/Book")
    task = download_tasks["hash-1"]
    assert task["status"] == "completed"
    assert task["progress"] == 100.0
    assert task["final_file_path"] == "/library/Sanderson/Book"


def test_importing_is_its_own_state():
    # The download finished but the book is still being checked and filed; a
    # card that jumped straight to complete would claim a library entry that
    # does not exist yet.
    _register()
    mark_status("hash-1", "importing")
    assert download_tasks["hash-1"]["status"] == "importing"
    assert download_tasks["hash-1"]["progress"] != 100.0


def test_failure_records_the_reason():
    _register()
    mark_status("hash-1", "failed", error="dead torrent")
    assert download_tasks["hash-1"]["error_message"] == "dead torrent"


def test_cancellation_from_the_downloads_page_is_visible():
    _register()
    assert is_cancelled("hash-1") is False
    download_tasks["hash-1"]["cancel_requested"] = True
    assert is_cancelled("hash-1") is True


def test_a_cancelled_status_also_counts():
    _register()
    download_tasks["hash-1"]["status"] = "cancelled"
    assert is_cancelled("hash-1") is True


def test_is_cancelled_for_an_unknown_card():
    assert is_cancelled("nope") is False


def test_forget_clears_only_the_live_row():
    _register("a")
    _register("b")
    forget("a")
    assert "a" not in download_tasks
    assert download_batches[BATCH_ID]["queue"] == ["b"]


def test_forget_is_safe_for_an_unknown_card():
    _register()
    forget("nope")
    assert "hash-1" in download_tasks


# ---------------------------------------------------------------------------
# The batch goes when its last card does
# ---------------------------------------------------------------------------

def test_the_batch_is_removed_once_its_last_card_is_forgotten():
    """Otherwise an empty "Audiobooks" card sits on the Downloads page forever.

    Nothing else will clean it up: the music side's batch healer skips this
    batch by design because is_music_batch() returns False for it. The same
    isolation that keeps the music worker pool off our downloads also opts us
    out of its housekeeping, so the housekeeping has to be done here.
    """
    _register("a")
    assert BATCH_ID in download_batches

    forget("a")

    assert BATCH_ID not in download_batches


def test_the_batch_survives_while_any_card_remains():
    _register("a")
    _register("b")
    forget("a")
    assert BATCH_ID in download_batches
    assert download_batches[BATCH_ID]["queue"] == ["b"]

    forget("b")
    assert BATCH_ID not in download_batches


def test_a_new_download_recreates_the_batch():
    # Removing it must not stop the next book from appearing.
    _register("a")
    forget("a")
    _register("c")
    assert BATCH_ID in download_batches
    assert download_batches[BATCH_ID]["queue"] == ["c"]


def test_a_queue_entry_whose_task_vanished_does_not_hold_the_batch_open():
    # A card removed by any other path would otherwise leave the queue looking
    # occupied and the empty batch on screen.
    _register("a")
    _register("b")
    download_tasks.pop("b", None)

    forget("a")

    assert BATCH_ID not in download_batches


def test_the_recreated_batch_still_carries_the_isolation_flags():
    # It must not come back as something the music pipeline will adopt.
    from core.downloads.lifecycle import is_music_batch

    _register("a")
    forget("a")
    _register("b")
    assert is_music_batch(BATCH_ID, download_batches[BATCH_ID]) is False
