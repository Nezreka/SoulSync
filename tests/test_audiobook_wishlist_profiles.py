"""the audiobook wishlist writes land on the row that was searched.

every status write defaulted to profile 1 and every caller left it there, so
a second profile's book never moved off "wanted": no attempt counted, no
backoff, and the same book searched and GRABBED again on every pass while its
finished import never turned "done". the row dict didn't even carry its
profile, so a caller could not have done better.

also: the way back from "cancelled" (never retried on its own), and a book
already in the library is done for everyone without waiting for its turn.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.audiobook_database import (
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_GRABBED,
    STATUS_WANTED,
    AudiobookDatabase,
)
from core.audiobook_release_search import AudiobookRelease
from core.audiobook_wishlist_worker import process_one, run_pass

from tests.test_audiobook_wishlist_worker import _book, _release


@pytest.fixture
def db(tmp_path):
    return AudiobookDatabase(str(tmp_path / "audiobooks.db"))


def _row(db, asin, profile_id):
    return next(r for r in db.get_wishlist(profile_id) if r["asin"] == asin)


# ── the row knows its owner ──────────────────────────────────────────────────

def test_rows_carry_their_profile(db):
    db.add_to_wishlist(_book(), profile_id=2)
    assert db.get_wishlist(2)[0]["profile_id"] == 2
    due = db.get_wishlist_due(profile_id=2)
    assert due and due[0]["profile_id"] == 2


# ── a second profile's pass moves its own rows ───────────────────────────────

def test_a_second_profiles_book_is_grabbed_once_not_every_pass(db):
    db.add_to_wishlist(_book(), profile_id=2)
    grabs = []

    def grab(release):
        grabs.append(release.title)
        return {"ok": True, "ref": f"h{len(grabs)}"}

    with patch("core.audiobook_release_search.search_releases", return_value=[_release()]), \
         patch("core.audiobook_grab.grab_release", side_effect=grab), \
         patch("core.audiobook_download_state.register_download"):
        run_pass(db)
        first = _row(db, "B1", 2)
        assert first["status"] == STATUS_GRABBED, "the write must land on profile 2's row"
        assert first["attempt_count"] == 1
        run_pass(db)

    assert grabs == ["The Final Empire M4B"], "grabbed rows are never searched again"


def test_a_second_profiles_miss_backs_off(db):
    db.add_to_wishlist(_book(), profile_id=2)
    with patch("core.audiobook_release_search.search_releases", return_value=[]) as search:
        run_pass(db)
        run_pass(db)
    row = _row(db, "B1", 2)
    assert row["status"] == STATUS_FAILED
    assert row["attempt_count"] == 1
    assert search.call_count == 1, "the second pass honours the backoff"


def test_profile_one_is_untouched_by_another_profiles_pass(db):
    db.add_to_wishlist(_book(), profile_id=1)
    db.add_to_wishlist(_book(), profile_id=2)
    row2 = _row(db, "B1", 2)
    with patch("core.audiobook_release_search.search_releases", return_value=[]):
        process_one(row2, db=db)
    assert _row(db, "B1", 1)["status"] == STATUS_WANTED
    assert _row(db, "B1", 1)["attempt_count"] == 0
    assert _row(db, "B1", 2)["status"] == STATUS_FAILED


def test_stale_rows_are_freed_for_every_profile(db):
    db.add_to_wishlist(_book(), profile_id=2)
    conn = db._connect()
    conn.execute("UPDATE audiobook_wishlist SET status = 'searching', last_attempt_at = 1 WHERE profile_id = 2")
    conn.commit()
    with patch("core.audiobook_release_search.search_releases", return_value=[]):
        summary = run_pass(db)
    assert summary["freed"] == 1


# ── the monitor's writes reach whoever wanted the book ───────────────────────

def test_an_import_marks_every_profiles_row_done(db):
    db.add_to_wishlist(_book(), profile_id=1)
    db.add_to_wishlist(_book(), profile_id=2)
    assert db.mark_wishlist_status("B1", STATUS_DONE, profile_id=None)
    assert _row(db, "B1", 1)["status"] == STATUS_DONE
    assert _row(db, "B1", 2)["status"] == STATUS_DONE


def test_a_scoped_write_still_scopes(db):
    db.add_to_wishlist(_book(), profile_id=1)
    db.add_to_wishlist(_book(), profile_id=2)
    assert db.mark_wishlist_status("B1", STATUS_FAILED, profile_id=2, error="x")
    assert _row(db, "B1", 1)["status"] == STATUS_WANTED
    assert _row(db, "B1", 2)["status"] == STATUS_FAILED


def test_an_unscoped_write_on_an_unknown_book_reports_false(db):
    assert db.mark_wishlist_status("NOPE", STATUS_FAILED, profile_id=None) is False


# ── look again ───────────────────────────────────────────────────────────────

def test_a_cancelled_book_can_be_wanted_again_keeping_its_choices(db):
    db.add_to_wishlist(_book(), narrator_mode="any", profile_id=2)
    db.mark_wishlist_status("B1", STATUS_FAILED, profile_id=2, error="boom", count_attempt=True)
    db.mark_wishlist_status("B1", STATUS_CANCELLED, profile_id=2)
    assert db.get_wishlist_due(profile_id=2) == []

    assert db.retry_wishlist_entry("B1", profile_id=2) is True
    row = _row(db, "B1", 2)
    assert row["status"] == STATUS_WANTED
    assert row["last_error"] == ""
    assert row["narrator_mode"] == "any"
    assert row["attempt_count"] == 1, "history is kept; only the backoff is waived"
    assert [r["asin"] for r in db.get_wishlist_due(profile_id=2)] == ["B1"]


def test_look_again_skips_the_backoff_on_a_failed_book(db):
    db.add_to_wishlist(_book())
    db.mark_wishlist_status("B1", STATUS_FAILED, error="No releases found", count_attempt=True)
    assert db.get_wishlist_due(retry_after_seconds=6 * 3600) == []
    assert db.retry_wishlist_entry("B1")
    assert len(db.get_wishlist_due(retry_after_seconds=6 * 3600)) == 1


def test_look_again_leaves_done_and_wanted_alone(db):
    db.add_to_wishlist(_book())
    assert db.retry_wishlist_entry("B1") is False              # already wanted
    db.mark_wishlist_status("B1", STATUS_DONE)
    assert db.retry_wishlist_entry("B1") is False              # in the library
    assert _row(db, "B1", 1)["status"] == STATUS_DONE
    assert db.retry_wishlist_entry("NOPE") is False


# ── owned books are done without waiting for their turn ──────────────────────

def test_a_book_already_in_the_library_is_done_for_everyone_at_pass_start(db):
    db.add_to_wishlist(_book(), profile_id=1)
    db.add_to_wishlist(_book(), profile_id=2)
    db.add_to_wishlist(_book(asin="B2", title="Other"), profile_id=1)
    db.add_to_library(dict(_book(), catalog_asin="B1"), "/books/final-empire", origin="scan")
    with patch("core.audiobook_release_search.search_releases", return_value=[]):
        summary = run_pass(db)
    assert summary["already_owned"] == 2
    assert _row(db, "B1", 1)["status"] == STATUS_DONE
    assert _row(db, "B1", 2)["status"] == STATUS_DONE
    assert _row(db, "B2", 1)["status"] == STATUS_FAILED     # still searched normally
