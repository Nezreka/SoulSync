"""Tests for the audiobook blocklist.

The point of it: a release that failed must not be found and grabbed again on
the wishlist's next pass. Without this a broken posting loops forever — grab,
fail, return to the wishlist, find the same one, grab.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.audiobook_database import AudiobookDatabase
from core.audiobook_release_search import AudiobookRelease, rank_releases

BOOK = {
    "asin": "B08G9PRS1K",
    "title": "Project Hail Mary",
    "author_names": ["Andy Weir"],
    "narrator_names": ["Ray Porter"],
    "runtime_minutes": 970,
    "series": [],
}


@pytest.fixture
def db(tmp_path):
    database = AudiobookDatabase(str(tmp_path / "audiobooks.db"))
    yield database
    database.close()


def _release(title="Project Hail Mary M4B", guid="guid-1", indexer="TPB"):
    return AudiobookRelease(source="prowlarr", protocol="torrent", title=title,
                            indexer=indexer, size_bytes=465 * 1024 * 1024, guid=guid)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def test_a_release_is_identified_by_its_guid():
    assert AudiobookDatabase.release_key({"guid": "abc", "title": "x"}) == "abc"


def test_a_release_without_a_guid_falls_back_to_indexer_and_title():
    # Enough to recognise the same posting without blocking a different upload
    # that merely shares a name.
    key = AudiobookDatabase.release_key({"indexer": "TPB", "title": "The Book"})
    assert key == "TPB::The Book"


def test_a_release_with_no_identity_at_all_cannot_be_blocked(db):
    assert db.block_release({}) is False


def test_the_soulseek_key_already_encodes_the_peer_and_folder():
    from types import SimpleNamespace

    from core.audiobook_soulseek import album_to_release

    tracks = [SimpleNamespace(filename=f"x\\Book\\{i}.mp3", size=80_000_000)
              for i in range(6)]
    album = SimpleNamespace(username="peer", album_path="x\\Book", album_title="Book",
                            artist="a", track_count=6, total_size=480_000_000,
                            tracks=tracks, dominant_quality="mp3", year=None,
                            free_upload_slots=1, upload_speed=0, queue_length=0)
    release = album_to_release(album, BOOK)
    key = AudiobookDatabase.release_key(release.to_dict())
    assert "peer" in key and "Book" in key


# ---------------------------------------------------------------------------
# Blocking
# ---------------------------------------------------------------------------

def test_a_blocked_release_is_remembered(db):
    db.block_release(_release().to_dict(), asin="B1", book_title="PHM", reason="bad rip")
    rows = db.get_blocklist()
    assert len(rows) == 1
    assert rows[0]["reason"] == "bad rip"
    assert rows[0]["release_title"] == "Project Hail Mary M4B"


def test_blocking_the_same_release_twice_is_one_row(db):
    db.block_release(_release().to_dict(), reason="first")
    db.block_release(_release().to_dict(), reason="second")
    assert len(db.get_blocklist()) == 1


def test_a_blocked_release_can_be_unblocked(db):
    db.block_release(_release().to_dict())
    assert db.unblock_release("guid-1") is True
    assert db.get_blocklist() == []


def test_unblocking_something_that_was_never_blocked(db):
    assert db.unblock_release("nope") is False


def test_the_whole_blocklist_can_be_cleared(db):
    db.block_release(_release(guid="a").to_dict())
    db.block_release(_release(guid="b").to_dict())
    assert db.clear_blocklist() == 2
    assert db.get_blocklist() == []


# ---------------------------------------------------------------------------
# The search honours it
# ---------------------------------------------------------------------------

def test_a_blocked_release_never_appears_in_results(db):
    blocked = _release(guid="bad", title="Project Hail Mary bad rip")
    good = _release(guid="good", title="Project Hail Mary M4B")
    db.block_release(blocked.to_dict(), reason="failed")

    with patch("core.audiobook_database.get_audiobook_db", return_value=db):
        ranked = rank_releases([blocked, good], BOOK, 0.0, "any")

    assert [r.guid for r in ranked] == ["good"]


def test_an_empty_blocklist_hides_nothing(db):
    with patch("core.audiobook_database.get_audiobook_db", return_value=db):
        assert len(rank_releases([_release()], BOOK, 0.0, "any")) == 1


def test_an_unreadable_blocklist_never_empties_the_results():
    # A blocklist that cannot be read must not look like everything is blocked.
    broken = MagicMock()
    broken.blocked_keys.side_effect = RuntimeError("locked")
    with patch("core.audiobook_database.get_audiobook_db", return_value=broken):
        assert len(rank_releases([_release()], BOOK, 0.0, "any")) == 1


# ---------------------------------------------------------------------------
# Failing a download blocks the release, never the book
# ---------------------------------------------------------------------------

def test_a_failed_download_blocks_the_release_it_came_from(db):
    """The loop this exists to break.

    The wishlist searches again next pass; without blocking it finds the same
    broken posting, grabs it, fails, and goes round forever — exactly what a
    release that is really part 1 of 5 does.
    """
    from core.audiobook_download_monitor import _return_to_wishlist

    row = {"asin": "B08G9PRS1K", "title": "Project Hail Mary",
           "release_title": "Project Hail Mary (1 of 5)", "release_guid": "part-1",
           "indexer": "TPB", "source": "torrent"}

    with patch("core.audiobook_download_monitor._book_for", return_value=BOOK):
        _return_to_wishlist(db, row, "Only 449 of 2730 minutes present")

    blocked = db.get_blocklist()
    assert len(blocked) == 1
    assert blocked[0]["key"] == "part-1"
    assert "449" in blocked[0]["reason"]


def test_a_failed_download_keeps_the_book_wanted(db):
    # The RELEASE is blocked, not the book. The book is still wanted.
    from core.audiobook_database import STATUS_FAILED
    from core.audiobook_download_monitor import _return_to_wishlist

    row = {"asin": "B08G9PRS1K", "title": "Project Hail Mary",
           "release_title": "bad rip", "release_guid": "bad", "source": "torrent"}

    with patch("core.audiobook_download_monitor._book_for", return_value=BOOK):
        _return_to_wishlist(db, row, "failed")

    wishlist = db.get_wishlist()
    assert [w["asin"] for w in wishlist] == ["B08G9PRS1K"]
    assert wishlist[0]["status"] == STATUS_FAILED


def test_the_next_search_cannot_return_the_blocked_release(db):
    # End to end: fail it, then search again and it is gone.
    from core.audiobook_download_monitor import _return_to_wishlist

    bad = _release(guid="part-1", title="Project Hail Mary (1 of 5)")
    good = _release(guid="whole", title="Project Hail Mary M4B")
    row = {"asin": "B08G9PRS1K", "title": "Project Hail Mary",
           "release_title": bad.title, "release_guid": "part-1", "source": "torrent"}

    with patch("core.audiobook_download_monitor._book_for", return_value=BOOK):
        _return_to_wishlist(db, row, "short")

    with patch("core.audiobook_database.get_audiobook_db", return_value=db):
        ranked = rank_releases([bad, good], BOOK, 0.0, "any")

    assert [r.guid for r in ranked] == ["whole"]


def test_a_failure_with_no_release_identity_does_not_block_everything(db):
    from core.audiobook_download_monitor import _return_to_wishlist

    row = {"asin": "B08G9PRS1K", "title": "Project Hail Mary", "source": "torrent"}
    with patch("core.audiobook_download_monitor._book_for", return_value=BOOK):
        _return_to_wishlist(db, row, "failed")
    assert db.get_blocklist() == []


# ---------------------------------------------------------------------------
# Reachable from the page
# ---------------------------------------------------------------------------

def _read(rel):
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / rel).read_text(
        encoding="utf-8", errors="ignore")


def test_the_blocklist_is_reachable_from_the_audiobooks_page():
    page = _read("webui/src/routes/audiobooks/-ui/audiobooks-page.tsx")
    assert "AudiobookReviewModal" in page
    assert "setShowBlocklist" in page


def test_a_release_can_be_blocked_from_the_results_list():
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-releases-modal.tsx")
    assert "blockRelease" in modal
    assert "styles.blockBtn" in modal


def test_the_ui_says_it_blocks_a_release_not_a_book():
    # The distinction matters: the book stays wanted.
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-releases-modal.tsx")
    assert "The book stays on your wishlist" in modal
    listing = _read("webui/src/routes/audiobooks/-ui/audiobook-review-modal.tsx")
    assert "not books" in listing


def test_clearing_the_blocklist_asks_first():
    # Never window.confirm — the app's own dialog.
    listing = _read("webui/src/routes/audiobooks/-ui/audiobook-review-modal.tsx")
    assert "window.showConfirmDialog" in listing
    assert "window.confirm" not in listing
