"""Tests for core/audiobook_watchlist.py — following an author.

The design question this module answers is what "new" means. Following an
author must not dump their back catalogue into the wishlist: Brandon Sanderson
has 88 titles and someone following him wants the next one.

Hermetic: the catalogue client is always a stub.
"""

from unittest.mock import patch

import pytest

from core.audiobook_client import product_to_item
from core.audiobook_database import AudiobookDatabase
from core.audiobook_watchlist import (
    is_newer_than,
    new_books_for,
    run_scan,
    scan_author,
)


@pytest.fixture
def db(tmp_path):
    return AudiobookDatabase(str(tmp_path / "audiobooks.db"))


def _book(asin="B1", title="Wind and Truth", release_date="2026-01-01"):
    return product_to_item({
        "asin": asin,
        "title": title,
        "authors": [{"name": "Brandon Sanderson"}],
        "narrators": [{"name": "Michael Kramer"}],
        "release_date": release_date,
        "runtime_length_min": 1479,
    })


class _Catalogue:
    def __init__(self, books):
        self.books = books
        self.calls = []

    def get_by_author(self, name, limit=20, sort="newest", **kwargs):
        self.calls.append({"name": name, "limit": limit, "sort": sort})
        return self.books


# ---------------------------------------------------------------------------
# What counts as new
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("released,since,expected", [
    ("2026-05-01", "2026-01-01", True),
    ("2025-05-01", "2026-01-01", False),
    ("2026-01-01", "2026-01-01", False),
    ("2026-01-02", "2026-01-01", True),
])
def test_only_books_after_the_follow_count(released, since, expected):
    assert is_newer_than(released, since) is expected


def test_a_book_with_no_release_date_is_not_new():
    # Usually an unreleased placeholder or bad metadata; wishlisting on a guess
    # would queue things nobody asked for.
    assert is_newer_than("", "2026-01-01") is False
    assert is_newer_than(None, "2026-01-01") is False


def test_with_no_cutoff_everything_dated_counts():
    assert is_newer_than("2020-01-01", "") is True


def test_a_backlog_is_not_queued():
    # The whole point: following an author must not wishlist 88 books.
    backlog = [_book(f"B{i}", f"Old Book {i}", "2010-01-01") for i in range(10)]
    assert new_books_for(backlog, "2026-01-01", lambda asin: False) == []


def test_a_new_release_is_picked_up():
    books = [_book("NEW", "Wind and Truth", "2026-06-01"),
             _book("OLD", "The Final Empire", "2006-07-17")]
    found = new_books_for(books, "2026-01-01", lambda asin: False)
    assert [b["asin"] for b in found] == ["NEW"]


def test_books_already_wanted_or_owned_are_skipped():
    # Otherwise every daily scan re-queues the same book.
    books = [_book("KNOWN", "Wind and Truth", "2026-06-01")]
    assert new_books_for(books, "2026-01-01", lambda asin: True) == []


def test_a_failing_known_check_skips_rather_than_queues():
    def broken(asin):
        raise RuntimeError("db gone")

    books = [_book("NEW", "Wind and Truth", "2026-06-01")]
    assert new_books_for(books, "2026-01-01", broken) == []


# ---------------------------------------------------------------------------
# Scanning one author
# ---------------------------------------------------------------------------

def test_a_new_release_reaches_the_wishlist(db):
    db.follow_author("Brandon Sanderson", since_date="2026-01-01")
    row = db.get_watchlist()[0]
    catalogue = _Catalogue([_book("NEW", "Wind and Truth", "2026-06-01")])

    result = scan_author(row, db=db, client=catalogue)

    assert result["wishlisted"] == 1
    assert [w["asin"] for w in db.get_wishlist()] == ["NEW"]


def test_a_wishlisted_release_follows_the_standard(db):
    # exact on the book's own narrator, like every other route in.
    db.follow_author("Brandon Sanderson", since_date="2026-01-01")
    row = db.get_watchlist()[0]
    scan_author(row, db=db, client=_Catalogue([_book("NEW", "Wind", "2026-06-01")]))
    assert db.get_wishlist()[0]["narrator_mode"] == "exact"


def test_the_newest_titles_are_asked_for_first(db):
    db.follow_author("Brandon Sanderson")
    row = db.get_watchlist()[0]
    catalogue = _Catalogue([])
    scan_author(row, db=db, client=catalogue)
    assert catalogue.calls[0]["sort"] == "newest"


def test_scanning_records_the_result(db):
    db.follow_author("Brandon Sanderson", since_date="2026-01-01")
    row = db.get_watchlist()[0]
    scan_author(row, db=db, client=_Catalogue([_book("NEW", "Wind", "2026-06-01")]))
    stored = db.get_watchlist()[0]
    assert stored["found_total"] == 1
    assert stored["last_scanned_at"] > 0


def test_a_second_scan_does_not_queue_the_same_book(db):
    db.follow_author("Brandon Sanderson", since_date="2026-01-01")
    catalogue = _Catalogue([_book("NEW", "Wind and Truth", "2026-06-01")])
    scan_author(db.get_watchlist()[0], db=db, client=catalogue)
    second = scan_author(db.get_watchlist()[0], db=db, client=catalogue)
    assert second["wishlisted"] == 0
    assert len(db.get_wishlist()) == 1


def test_a_book_already_in_the_library_is_not_queued(db):
    db.follow_author("Brandon Sanderson", since_date="2026-01-01")
    db.add_to_library({"asin": "NEW", "title": "Wind and Truth"}, "/library/x")
    result = scan_author(
        db.get_watchlist()[0], db=db,
        client=_Catalogue([_book("NEW", "Wind and Truth", "2026-06-01")]),
    )
    assert result["wishlisted"] == 0


def test_a_failing_lookup_is_recorded_not_raised(db):
    # One author whose lookup fails must not stop the rest of the pass.
    class Broken:
        def get_by_author(self, *args, **kwargs):
            raise RuntimeError("catalogue unreachable")

    db.follow_author("Brandon Sanderson")
    result = scan_author(db.get_watchlist()[0], db=db, client=Broken())
    assert "unreachable" in result["error"]
    assert "unreachable" in db.get_watchlist()[0]["last_error"]


def test_a_row_with_no_name_is_skipped(db):
    assert scan_author({"name": ""}, db=db)["wishlisted"] == 0


# ---------------------------------------------------------------------------
# A pass
# ---------------------------------------------------------------------------

def test_a_pass_walks_the_authors_due(db):
    for name in ("Brandon Sanderson", "Andy Weir"):
        db.follow_author(name, since_date="2026-01-01")

    with patch("core.audiobook_client.get_audiobook_client",
               return_value=_Catalogue([])):
        summary = run_scan(db=db)
    assert summary["authors"] == 2


def test_a_pass_respects_the_daily_spacing(db):
    db.follow_author("Brandon Sanderson", since_date="2026-01-01")
    with patch("core.audiobook_client.get_audiobook_client",
               return_value=_Catalogue([])):
        run_scan(db=db)
        second = run_scan(db=db)
    assert second["authors"] == 0


def test_an_empty_watchlist_is_a_no_op(db):
    with patch("core.audiobook_client.get_audiobook_client") as client:
        assert run_scan(db=db)["authors"] == 0
    client.assert_not_called()


def test_a_broken_database_does_not_raise():
    class Broken:
        def get_watchlist_due(self, **kwargs):
            raise RuntimeError("db gone")

    assert run_scan(db=Broken())["authors"] == 0


# ---------------------------------------------------------------------------
# The automation
# ---------------------------------------------------------------------------

def test_the_scan_is_a_seeded_daily_automation():
    from core.automation_engine import SYSTEM_AUTOMATIONS

    spec = next(s for s in SYSTEM_AUTOMATIONS
                if s["action_type"] == "audiobook_scan_watchlist")
    assert spec["trigger_config"] == {"interval": 24, "unit": "hours"}
    # Audio-side automations sit on the same page as music, like the podcast scan.
    assert "owned_by" not in spec


def test_the_automation_has_a_handler():
    from core.automation.handlers import auto_scan_audiobook_watchlist

    assert callable(auto_scan_audiobook_watchlist)


def test_the_handler_skips_an_unused_install():
    from core.automation.handlers.audiobook_scan_watchlist import (
        auto_scan_audiobook_watchlist,
    )

    with patch("core.audiobook_database.subsystem_in_use", return_value=False), \
         patch("core.audiobook_watchlist.run_scan") as scan:
        result = auto_scan_audiobook_watchlist({}, deps=None)
    scan.assert_not_called()
    assert result["status"] == "completed"


def test_the_handler_never_raises_into_the_engine():
    from core.automation.handlers.audiobook_scan_watchlist import (
        auto_scan_audiobook_watchlist,
    )

    with patch("core.audiobook_database.subsystem_in_use", return_value=True), \
         patch("core.audiobook_watchlist.run_scan", side_effect=RuntimeError("boom")):
        result = auto_scan_audiobook_watchlist({}, deps=None)
    assert result["status"] == "error"
