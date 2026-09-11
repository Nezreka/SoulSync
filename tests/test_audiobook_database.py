"""Tests for core/audiobook_database.py.

Every test runs against a temp file. Nothing here touches database/audiobooks.db
and nothing here can reach the music or video databases at all — that isolation
is the reason this subsystem has its own file in the first place.
"""

import os
import sqlite3

import pytest

from core.audiobook_database import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_GRABBED,
    STATUS_SEARCHING,
    STATUS_WANTED,
    AudiobookDatabase,
    get_audiobook_db,
)


@pytest.fixture
def db(tmp_path):
    return AudiobookDatabase(str(tmp_path / "audiobooks.db"))


def _book(asin="B08G9PRS1K", title="Project Hail Mary", **overrides):
    payload = {
        "asin": asin,
        "title": title,
        "subtitle": "A Novel",
        "author_names": ["Andy Weir"],
        "narrator_names": ["Ray Porter"],
        "series": [{"title": "The Mistborn Saga", "sequence": "1"}],
        "cover_url": "https://img/500.jpg",
        "runtime_minutes": 1479,
        "release_date": "2021-05-04",
        "language": "english",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Schema and isolation
# ---------------------------------------------------------------------------

def test_the_database_is_its_own_file(tmp_path):
    path = tmp_path / "audiobooks.db"
    AudiobookDatabase(str(path))
    assert path.exists()


def test_the_database_creates_its_directory(tmp_path):
    path = tmp_path / "nested" / "deeper" / "audiobooks.db"
    AudiobookDatabase(str(path))
    assert path.exists()


def test_the_schema_holds_only_audiobook_tables(db):
    conn = db._connect()
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if not row["name"].startswith("sqlite_")
    }
    assert tables == {
        "audiobook_wishlist", "audiobook_downloads",
        "audiobook_library", "audiobook_watchlist", "audiobook_library_scan_state",
        "audiobook_blocklist",
    }


def test_opening_twice_is_safe(tmp_path):
    # Every open re-runs CREATE TABLE IF NOT EXISTS and the column migrations.
    path = str(tmp_path / "audiobooks.db")
    AudiobookDatabase(path).add_to_wishlist(_book())
    reopened = AudiobookDatabase(path)
    assert len(reopened.get_wishlist()) == 1


# ---------------------------------------------------------------------------
# Wishlist
# ---------------------------------------------------------------------------

def test_add_and_read_back(db):
    assert db.add_to_wishlist(_book()) is True
    rows = db.get_wishlist()
    assert len(rows) == 1
    row = rows[0]
    assert row["asin"] == "B08G9PRS1K"
    assert row["title"] == "Project Hail Mary"
    assert row["authors"] == ["Andy Weir"]
    assert row["narrators"] == ["Ray Porter"]
    assert row["series_title"] == "The Mistborn Saga"
    assert row["series_sequence"] == "1"
    assert row["status"] == STATUS_WANTED
    assert row["attempt_count"] == 0


def test_adding_the_same_book_twice_is_not_an_error(db):
    assert db.add_to_wishlist(_book()) is True
    assert db.add_to_wishlist(_book()) is False
    assert len(db.get_wishlist()) == 1


def test_re_adding_does_not_reset_the_backoff(db):
    # Otherwise remove-and-re-add is a way to dodge the retry backoff and hammer
    # the indexers.
    db.add_to_wishlist(_book())
    db.mark_wishlist_status("B08G9PRS1K", STATUS_FAILED, error="nothing found", count_attempt=True)
    db.add_to_wishlist(_book())
    row = db.get_wishlist()[0]
    assert row["attempt_count"] == 1
    assert row["status"] == STATUS_FAILED


@pytest.mark.parametrize("book", [
    {}, {"asin": "B1"}, {"title": "No asin"},
    {"asin": "", "title": "Empty"}, {"asin": "B1", "title": "   "},
])
def test_unusable_books_are_refused(db, book):
    assert db.add_to_wishlist(book) is False
    assert db.get_wishlist() == []


def test_remove(db):
    db.add_to_wishlist(_book())
    assert db.remove_from_wishlist("B08G9PRS1K") is True
    assert db.get_wishlist() == []
    assert db.remove_from_wishlist("B08G9PRS1K") is False


def test_is_wishlisted(db):
    assert db.is_wishlisted("B08G9PRS1K") is False
    db.add_to_wishlist(_book())
    assert db.is_wishlisted("B08G9PRS1K") is True


def test_profiles_do_not_see_each_others_wishlists(db):
    db.add_to_wishlist(_book(), profile_id=1)
    db.add_to_wishlist(_book(), profile_id=2)
    assert len(db.get_wishlist(profile_id=1)) == 1
    assert len(db.get_wishlist(profile_id=2)) == 1
    db.remove_from_wishlist("B08G9PRS1K", profile_id=1)
    assert db.get_wishlist(profile_id=1) == []
    assert len(db.get_wishlist(profile_id=2)) == 1


def test_filter_by_status(db):
    db.add_to_wishlist(_book("B1", "One"))
    db.add_to_wishlist(_book("B2", "Two"))
    db.mark_wishlist_status("B2", STATUS_DONE)
    assert [r["asin"] for r in db.get_wishlist(status=STATUS_WANTED)] == ["B1"]
    assert [r["asin"] for r in db.get_wishlist(status=STATUS_DONE)] == ["B2"]


def test_status_changes_do_not_count_an_attempt_by_default(db):
    db.add_to_wishlist(_book())
    db.mark_wishlist_status("B08G9PRS1K", STATUS_SEARCHING)
    row = db.get_wishlist()[0]
    assert row["status"] == STATUS_SEARCHING
    assert row["attempt_count"] == 0


def test_a_counted_attempt_records_the_error(db):
    db.add_to_wishlist(_book())
    db.mark_wishlist_status("B08G9PRS1K", STATUS_FAILED, error="no releases", count_attempt=True)
    row = db.get_wishlist()[0]
    assert row["attempt_count"] == 1
    assert row["last_error"] == "no releases"
    assert row["last_attempt_at"] > 0


def test_an_unknown_status_is_refused(db):
    db.add_to_wishlist(_book())
    assert db.mark_wishlist_status("B08G9PRS1K", "exploded") is False
    assert db.get_wishlist()[0]["status"] == STATUS_WANTED


def test_counts(db):
    db.add_to_wishlist(_book("B1", "One"))
    db.add_to_wishlist(_book("B2", "Two"))
    db.add_to_wishlist(_book("B3", "Three"))
    db.mark_wishlist_status("B2", STATUS_DONE)
    db.mark_wishlist_status("B3", STATUS_FAILED)
    counts = db.wishlist_counts()
    assert counts["wanted"] == 1
    assert counts["done"] == 1
    assert counts["failed"] == 1
    assert counts["total"] == 3


# ---------------------------------------------------------------------------
# Retry scheduling
# ---------------------------------------------------------------------------

def test_a_new_row_is_due_immediately(db):
    db.add_to_wishlist(_book())
    assert [r["asin"] for r in db.get_wishlist_due()] == ["B08G9PRS1K"]


def test_a_just_tried_row_is_not_due_again(db):
    # The backoff is the only thing standing between a long wishlist and a
    # search landing on every indexer every few seconds.
    db.add_to_wishlist(_book())
    db.mark_wishlist_status("B08G9PRS1K", STATUS_FAILED, count_attempt=True)
    assert db.get_wishlist_due(retry_after_seconds=3600) == []


def test_a_row_becomes_due_once_the_backoff_passes(db):
    db.add_to_wishlist(_book())
    db.mark_wishlist_status("B08G9PRS1K", STATUS_FAILED, count_attempt=True)
    assert len(db.get_wishlist_due(retry_after_seconds=0)) == 1


@pytest.mark.parametrize("status", [STATUS_GRABBED, STATUS_DONE, STATUS_SEARCHING])
def test_rows_already_in_flight_or_finished_are_never_retried(db, status):
    # Re-grabbing something already handed to a download client would queue it
    # twice; re-searching a finished book is pure waste.
    db.add_to_wishlist(_book())
    db.mark_wishlist_status("B08G9PRS1K", status)
    assert db.get_wishlist_due(retry_after_seconds=0) == []


def test_due_rows_come_back_oldest_attempt_first(db):
    for asin in ("B1", "B2", "B3"):
        db.add_to_wishlist(_book(asin, asin))
    db.mark_wishlist_status("B1", STATUS_FAILED, count_attempt=True)
    due = db.get_wishlist_due(retry_after_seconds=0)
    assert due[-1]["asin"] == "B1"


def test_due_honours_the_limit(db):
    for index in range(10):
        db.add_to_wishlist(_book(f"B{index}", f"Book {index}"))
    assert len(db.get_wishlist_due(limit=3)) == 3


def test_due_is_per_profile(db):
    db.add_to_wishlist(_book(), profile_id=2)
    assert db.get_wishlist_due(profile_id=1) == []
    assert len(db.get_wishlist_due(profile_id=2)) == 1


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

def test_the_singleton_is_shared(tmp_path):
    # The connection cache and the schema init only mean anything if every
    # caller shares one instance.
    from core.audiobook_database import _reset_for_tests

    first = _reset_for_tests(str(tmp_path / "singleton.db"))
    assert get_audiobook_db() is first
    assert get_audiobook_db() is get_audiobook_db()
    first.close()


def test_reset_for_tests_never_touches_the_real_file(tmp_path):
    from core.audiobook_database import DEFAULT_DB_PATH, _reset_for_tests

    db = _reset_for_tests(str(tmp_path / "isolated.db"))
    assert db.db_path != DEFAULT_DB_PATH
    assert os.path.exists(tmp_path / "isolated.db")
    db.close()


def test_the_wishlist_survives_a_reconnect(tmp_path):
    # Each thread gets its own connection; a closed one must rebuild cleanly
    # rather than raise on the next call.
    path = str(tmp_path / "reconnect.db")
    db = AudiobookDatabase(path)
    db.add_to_wishlist(_book())
    db.close()
    assert len(db.get_wishlist()) == 1


def test_rows_are_readable_from_another_thread(tmp_path):
    # sqlite3 connections cannot cross threads; the wrapper keeps one per
    # thread, and a background search pass reads what a request handler wrote.
    import threading

    db = AudiobookDatabase(str(tmp_path / "threads.db"))
    db.add_to_wishlist(_book())
    seen = []

    def read():
        seen.append(len(db.get_wishlist()))

    worker = threading.Thread(target=read)
    worker.start()
    worker.join()
    assert seen == [1]


def test_a_corrupt_row_does_not_break_the_listing(tmp_path):
    # authors/narrators are JSON blobs written by us, but a hand-edited or
    # half-migrated row must not take the whole wishlist page down.
    path = str(tmp_path / "corrupt.db")
    db = AudiobookDatabase(path)
    db.add_to_wishlist(_book())
    conn = db._connect()
    conn.execute("UPDATE audiobook_wishlist SET authors = ?", ("not json at all",))
    conn.commit()
    rows = db.get_wishlist()
    assert len(rows) == 1
    assert rows[0]["authors"] == []


def test_the_module_cannot_reach_another_database():
    """The isolation claim, checked against the code rather than the prose.

    Scanned as an AST, not as text: the module's own docstring explains why it
    does not touch music_library.db, and a substring search would trip on that
    explanation.
    """
    import ast
    import inspect

    import core.audiobook_database as module

    tree = ast.parse(inspect.getsource(module))

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for name in imported:
        assert not name.startswith("database")
        assert not name.startswith("core.video")

    called = {
        getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert "get_database" not in called
    assert "get_video_db" not in called


def test_the_default_path_is_its_own_file():
    from core.audiobook_database import DEFAULT_DB_PATH

    assert "audiobooks" in DEFAULT_DB_PATH
    assert "music_library" not in DEFAULT_DB_PATH
    assert "video_library" not in DEFAULT_DB_PATH


def test_a_download_stores_the_book_as_it_was(db):
    # The import needs the series to shelve it and the runtime to check it is
    # whole; asking Audible again hours later may find a shedding storefront.
    book = {"asin": "B1", "title": "The Final Empire", "runtime_minutes": 1479,
            "series": [{"title": "The Mistborn Saga", "sequence": "1"}]}
    db.record_download("hash-1", "B1", "The Final Empire", "torrent", book=book)
    stored = db.stored_book(db.get_downloads()[0])
    assert stored["runtime_minutes"] == 1479
    assert stored["series"][0]["title"] == "The Mistborn Saga"


def test_a_download_without_context_stores_nothing(db):
    db.record_download("hash-1", "B1", "The Final Empire", "torrent")
    assert db.stored_book(db.get_downloads()[0]) == {}


def test_unserializable_context_never_fails_the_grab(db):
    # The release is already downloading by the time this runs.
    class Unserializable:
        pass

    assert db.record_download(
        "hash-1", "B1", "The Final Empire", "torrent",
        book={"bad": Unserializable()},
    ) is True
    assert db.stored_book(db.get_downloads()[0]) == {}


def test_a_corrupt_stored_book_reads_as_empty(db):
    db.record_download("hash-1", "B1", "The Final Empire", "torrent")
    conn = db._connect()
    conn.execute("UPDATE audiobook_downloads SET book_json = ?", ("not json",))
    conn.commit()
    assert db.stored_book(db.get_downloads()[0]) == {}


# ---------------------------------------------------------------------------
# Watchlist — followed authors
# ---------------------------------------------------------------------------

def test_following_an_author(db):
    assert db.follow_author("Brandon Sanderson") is True
    rows = db.get_watchlist()
    assert [r["name"] for r in rows] == ["Brandon Sanderson"]
    assert db.is_following("Brandon Sanderson") is True


def test_following_twice_is_not_an_error(db):
    db.follow_author("Brandon Sanderson")
    assert db.follow_author("Brandon Sanderson") is False
    assert len(db.get_watchlist()) == 1


def test_following_defaults_to_watching_from_today(db):
    # Following an author means "tell me about the next one", not "download the
    # 88 books they already wrote".
    import time

    db.follow_author("Brandon Sanderson")
    since = db.get_watchlist()[0]["since_date"]
    assert since == time.strftime("%Y-%m-%d", time.gmtime())


def test_the_cutoff_can_be_set_explicitly(db):
    db.follow_author("Brandon Sanderson", since_date="2020-01-01")
    assert db.get_watchlist()[0]["since_date"] == "2020-01-01"


def test_unfollowing(db):
    db.follow_author("Brandon Sanderson")
    assert db.unfollow_author("Brandon Sanderson") is True
    assert db.get_watchlist() == []
    assert db.unfollow_author("Brandon Sanderson") is False


@pytest.mark.parametrize("name", ["", "   ", None])
def test_an_empty_author_is_refused(db, name):
    assert db.follow_author(name) is False


def test_profiles_do_not_share_followed_authors(db):
    db.follow_author("Brandon Sanderson", profile_id=1)
    db.follow_author("Andy Weir", profile_id=2)
    assert [r["name"] for r in db.get_watchlist(profile_id=1)] == ["Brandon Sanderson"]
    assert [r["name"] for r in db.get_watchlist(profile_id=2)] == ["Andy Weir"]


def test_a_new_follow_is_due_immediately(db):
    db.follow_author("Brandon Sanderson")
    assert [r["name"] for r in db.get_watchlist_due()] == ["Brandon Sanderson"]


def test_a_just_scanned_author_is_not_due_again(db):
    # A book is announced weeks ahead and published on a date; checking more
    # than daily spends effort to learn nothing.
    db.follow_author("Brandon Sanderson")
    db.mark_author_scanned("Brandon Sanderson", found=2)
    assert db.get_watchlist_due(rescan_after_seconds=3600) == []


def test_an_author_becomes_due_again_later(db):
    db.follow_author("Brandon Sanderson")
    db.mark_author_scanned("Brandon Sanderson")
    assert len(db.get_watchlist_due(rescan_after_seconds=0)) == 1


def test_scanning_records_what_was_found(db):
    db.follow_author("Brandon Sanderson")
    db.mark_author_scanned("Brandon Sanderson", found=2)
    db.mark_author_scanned("Brandon Sanderson", found=1)
    row = db.get_watchlist()[0]
    assert row["found_total"] == 3
    assert row["last_scanned_at"] > 0


def test_a_failed_scan_records_why(db):
    db.follow_author("Brandon Sanderson")
    db.mark_author_scanned("Brandon Sanderson", error="catalogue unreachable")
    assert db.get_watchlist()[0]["last_error"] == "catalogue unreachable"


def test_existing_library_migrates_without_losing_books(tmp_path):
    import sqlite3
    path = str(tmp_path / 'existing.db')
    with sqlite3.connect(path) as conn:
        conn.execute('''CREATE TABLE audiobook_library (
            id INTEGER PRIMARY KEY AUTOINCREMENT, asin TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL, author TEXT DEFAULT '', narrator TEXT DEFAULT '',
            series_title TEXT DEFAULT '', series_sequence TEXT DEFAULT '',
            path TEXT NOT NULL, file_count INTEGER DEFAULT 0, size_bytes INTEGER DEFAULT 0,
            audio_format TEXT DEFAULT '', runtime_minutes INTEGER DEFAULT 0, imported_at REAL NOT NULL)''')
        conn.execute("INSERT INTO audiobook_library (asin, title, path, imported_at) VALUES ('B000000001', 'Existing Book', '/books/Existing', 1)")
    migrated = AudiobookDatabase(path)
    try:
        row = migrated.get_library()[0]
        assert row['title'] == 'Existing Book' and row['source'] == 'download'
        assert row['cover_url'] == '' and row['scan_signature'] == ''
        migrated.set_library_scan_state({'status': 'completed', 'checked': 1})
        assert migrated.get_library_scan_state()['checked'] == 1
    finally:
        migrated.close()
