"""Tests for core/audiobook_recycle.py — deleting a book moves it aside.

Hermetic: every test works inside tmp_path with the library root patched.
"""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.audiobook_recycle import (
    TRASH_DIRNAME,
    discard,
    entry_age_seconds,
    list_bin,
    purge_old,
    restore,
)


@pytest.fixture
def library(tmp_path):
    """A library root with the recycle bin turned on."""
    root = tmp_path / "audiobooks"
    root.mkdir()
    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: {
        "library.audiobooks_path": str(root),
        "audiobooks.recycle_deletes": True,
        "audiobooks.recycle_keep_days": 7,
    }.get(key, default)
    with patch("core.settings.config_manager", manager):
        yield root


def _book(root, name="Andy Weir/Project Hail Mary", files=3):
    folder = root / name
    folder.mkdir(parents=True)
    for index in range(files):
        (folder / f"{index + 1:02d}.mp3").write_bytes(b"x" * 1000)
    return folder


# ---------------------------------------------------------------------------
# Discarding
# ---------------------------------------------------------------------------

def test_a_deleted_book_goes_to_the_bin_not_away(library):
    folder = _book(library)
    outcome = discard(str(folder))

    assert outcome["ok"] is True
    assert outcome["permanent"] is False
    assert not folder.exists()
    assert (library / TRASH_DIRNAME).is_dir()
    assert len(list_bin()) == 1


def test_the_whole_folder_moves_not_just_some_files(library):
    # A book is ninety chapter files plus artwork; half-deleting it is worse
    # than not deleting it.
    folder = _book(library, files=9)
    discard(str(folder))
    moved = Path(list_bin()[0]["path"])
    assert len(list(moved.glob("*.mp3"))) == 9


def test_a_recycled_entry_is_stamped_with_when_it_happened(library):
    discard(str(_book(library)))
    name = list_bin()[0]["name"]
    assert entry_age_seconds(name) is not None
    assert name.endswith("Project Hail Mary")


def test_deleting_something_already_gone_is_success_not_failure(library):
    # It is the outcome the caller wanted. Reporting failure makes a cleanup
    # pass retry something that is already finished.
    outcome = discard(str(library / "nothing here"))
    assert outcome["ok"] is True and outcome["error"] == ""


def test_with_the_bin_off_the_delete_is_permanent(tmp_path):
    root = tmp_path / "audiobooks"
    root.mkdir()
    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: {
        "library.audiobooks_path": str(root),
        "audiobooks.recycle_deletes": False,
    }.get(key, default)

    folder = _book(root)
    with patch("core.settings.config_manager", manager):
        outcome = discard(str(folder))

    assert outcome["ok"] is True and outcome["permanent"] is True
    assert not folder.exists()
    assert not (root / TRASH_DIRNAME).exists()


# ---------------------------------------------------------------------------
# Age comes from the stamp
# ---------------------------------------------------------------------------

def test_age_is_read_from_the_name_not_the_mtime():
    """Moving a folder keeps its original mtime.

    A book imported in 2019 would land in the bin already older than any keep
    window and be purged on the spot — a zero-second undo window for exactly
    the thing somebody most wants back.
    """
    now = time.time()
    name = time.strftime("%Y%m%d_%H%M%S", time.localtime(now - 3600)) + "_Book"
    age = entry_age_seconds(name, now=now)
    assert 3500 < age < 3700


def test_something_we_did_not_put_there_has_no_age():
    # Another tool's files are not ours to delete.
    assert entry_age_seconds("random-folder") is None
    assert entry_age_seconds("") is None


def test_a_malformed_stamp_has_no_age():
    assert entry_age_seconds("99999999_999999_Book") is None


# ---------------------------------------------------------------------------
# Purging
# ---------------------------------------------------------------------------

def test_entries_past_the_window_are_purged(library):
    trash = library / TRASH_DIRNAME
    trash.mkdir()
    old = trash / (time.strftime("%Y%m%d_%H%M%S",
                                 time.localtime(time.time() - 10 * 86400)) + "_Old Book")
    old.mkdir()
    (old / "01.mp3").write_bytes(b"x" * 5000)

    summary = purge_old(days=7)

    assert summary["removed"] == 1
    assert summary["freed_bytes"] == 5000
    assert not old.exists()


def test_entries_inside_the_window_are_kept(library):
    trash = library / TRASH_DIRNAME
    trash.mkdir()
    fresh = trash / (time.strftime("%Y%m%d_%H%M%S",
                                   time.localtime(time.time() - 3600)) + "_New Book")
    fresh.mkdir()

    summary = purge_old(days=7)

    assert summary["removed"] == 0 and summary["kept"] == 1
    assert fresh.exists()


def test_a_purge_leaves_anything_it_did_not_recycle(library):
    trash = library / TRASH_DIRNAME
    trash.mkdir()
    stranger = trash / "someone-elses-folder"
    stranger.mkdir()

    purge_old(days=0.0001)

    assert stranger.exists()


def test_a_zero_keep_window_purges_nothing(library):
    """0 means the bin is off, not "delete everything".

    Purging on that basis would destroy what was recycled while it was on.
    """
    trash = library / TRASH_DIRNAME
    trash.mkdir()
    entry = trash / (time.strftime("%Y%m%d_%H%M%S",
                                   time.localtime(time.time() - 400 * 86400)) + "_Ancient")
    entry.mkdir()

    assert purge_old(days=0)["removed"] == 0
    assert entry.exists()


def test_purging_with_no_bin_is_not_an_error(library):
    assert purge_old(days=7)["removed"] == 0


# ---------------------------------------------------------------------------
# Restoring
# ---------------------------------------------------------------------------

def test_a_recycled_book_can_be_put_back(library):
    folder = _book(library, "Project Hail Mary")
    discard(str(folder))
    name = list_bin()[0]["name"]

    outcome = restore(name)

    assert outcome["ok"] is True
    assert (library / "Project Hail Mary").is_dir()


def test_restoring_over_something_already_there_is_refused(library):
    folder = _book(library, "Project Hail Mary")
    discard(str(folder))
    name = list_bin()[0]["name"]
    _book(library, "Project Hail Mary")

    outcome = restore(name)

    assert outcome["ok"] is False and "already back" in outcome["error"]


def test_restoring_something_the_bin_did_not_put_there_is_refused(library):
    trash = library / TRASH_DIRNAME
    trash.mkdir()
    (trash / "stranger").mkdir()
    assert restore("stranger")["ok"] is False


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_the_bin_reports_what_is_recoverable(library):
    discard(str(_book(library, "Project Hail Mary")))
    entries = list_bin()
    assert len(entries) == 1
    assert entries[0]["original"] == "Project Hail Mary"
    assert entries[0]["age_days"] == 0.0


def test_an_empty_bin_lists_nothing(library):
    assert list_bin() == []


# ---------------------------------------------------------------------------
# The library view
# ---------------------------------------------------------------------------

def _read(rel):
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / rel).read_text(
        encoding="utf-8", errors="ignore")


def test_the_library_is_reachable_from_the_audiobooks_page():
    page = _read("webui/src/routes/audiobooks/-ui/audiobooks-page.tsx")
    assert "AudiobookLibraryModal" in page
    assert "setShowLibrary" in page


def test_deleting_a_book_says_it_is_recoverable():
    # A destructive action that does not say it can be undone reads as final.
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-library-modal.tsx")
    assert "recycle bin settings apply" in modal
    assert "Confirm delete" in modal
    assert "Keep book" in modal
    assert "window.confirm" not in modal


def test_the_library_view_reads_the_record_the_scan_keeps_honest():
    # Not a fresh walk of the folder: this is what SoulSync BELIEVES it has,
    # which is the same record that decides the Owned badges, so anything
    # wrong is visible and fixable rather than hidden.
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-library-modal.tsx")
    assert "fetchLibrary" in modal


# ---------------------------------------------------------------------------
# Things the video bin already knew, found by reading it
# ---------------------------------------------------------------------------

def test_a_book_comes_back_to_the_folder_it_came_from(library):
    """Not to the library root.

    A book lives at <root>/Author/Series/Title and the trash entry keeps only
    the last segment, so reconstructing from the name put it back as
    <root>/Title with the author and series folders gone. The video bin
    records the original path for exactly this reason.
    """
    book = _book(library, "Brandon Sanderson/The Stormlight Archive/01 - The Way of Kings")
    original = book.relative_to(library)

    discard(str(book))
    restore(list_bin()[0]["name"])

    assert (library / original).is_dir()
    assert not (library / "01 - The Way of Kings").exists()


def test_two_books_deleted_in_the_same_second_do_not_collide(library):
    """shutil.move puts a directory INSIDE an existing one rather than failing.

    Two volumes both called "Book One" under different authors would bury the
    first inside the second, silently.
    """
    first = _book(library, "Author A/Book One", files=2)
    second = _book(library, "Author B/Book One", files=5)

    discard(str(first))
    discard(str(second))

    entries = list_bin()
    assert len(entries) == 2
    # Both survive intact, neither nested in the other.
    counts = sorted(len(list(Path(e["path"]).glob("*.mp3"))) for e in entries)
    assert counts == [2, 5]


def test_a_failed_move_leaves_the_book_alone_rather_than_deleting_it(library):
    """The bin exists to protect against a mis-click.

    A share that blinks is the common cause of a failed move; turning that into
    a permanent delete destroys exactly what the bin was there for.
    """
    book = _book(library)

    with patch("core.audiobook_recycle.shutil.move", side_effect=OSError("share gone")):
        outcome = discard(str(book))

    assert outcome["ok"] is False
    assert outcome["permanent"] is False
    assert book.is_dir(), "the book was deleted after a failed recycle"


def test_the_manifest_is_not_mistaken_for_a_recycled_book(library):
    discard(str(_book(library)))
    assert all(not e["name"].startswith(".") for e in list_bin())


def test_purging_forgets_the_entry_it_removed(library):
    # A manifest that grows forever is a slow leak.
    from core.audiobook_recycle import MANIFEST_NAME, _manifest_read

    trash = library / TRASH_DIRNAME
    discard(str(_book(library)))
    entry = list_bin()[0]["name"]
    assert entry in _manifest_read(trash)

    purge_old(days=0.0000001)

    assert entry not in _manifest_read(trash)
    assert (trash / MANIFEST_NAME).exists()


def test_the_bin_reports_where_each_entry_would_go_back_to(library):
    book = _book(library, "Andy Weir/Project Hail Mary")
    discard(str(book))
    assert list_bin()[0]["original_path"].endswith("Andy Weir/Project Hail Mary")


def test_the_bin_is_hidden_from_media_servers(library):
    """It sits inside the folder Audiobookshelf and Plex scan.

    A visible bin means every deleted book keeps showing up in the media
    server for the whole keep window. The dot prefix is what those scanners
    skip, and it is the spelling the music side already uses.
    """
    from core.audiobook_recycle import TRASH_DIRNAME, trash_dir

    assert TRASH_DIRNAME.startswith(".")
    discard(str(_book(library)))
    assert Path(trash_dir()).name.startswith(".")


def test_the_bin_sits_under_the_library_root(library):
    from core.audiobook_recycle import trash_dir

    assert Path(trash_dir()).parent == library


# ---------------------------------------------------------------------------
# Managing what has been removed
# ---------------------------------------------------------------------------

def test_one_book_can_be_erased_early(library):
    # Waiting the whole keep window is not always what somebody wants.
    from core.audiobook_recycle import purge_entry

    discard(str(_book(library)))
    entry = list_bin()[0]

    outcome = purge_entry(entry["name"])

    assert outcome["ok"] is True and outcome["freed_bytes"] > 0
    assert list_bin() == []


def test_erasing_something_the_bin_did_not_put_there_is_refused(library):
    from core.audiobook_recycle import purge_entry

    trash = library / TRASH_DIRNAME
    trash.mkdir()
    (trash / "stranger").mkdir()
    assert purge_entry("stranger")["ok"] is False
    assert (trash / "stranger").exists()


def test_the_bin_can_be_emptied_regardless_of_age(library):
    """A person deciding is not subject to the keep window.

    purge_old refuses to act when the window is 0 because that means the bin
    is off; emptying by hand is a different question and must still work.
    """
    from core.audiobook_recycle import empty_bin

    discard(str(_book(library, "A/Book One")))
    discard(str(_book(library, "B/Book Two")))

    summary = empty_bin()

    assert summary["removed"] == 2
    assert list_bin() == []


def test_emptying_leaves_anything_it_did_not_recycle(library):
    from core.audiobook_recycle import empty_bin

    discard(str(_book(library)))
    stranger = library / TRASH_DIRNAME / "not-ours"
    stranger.mkdir()

    empty_bin()

    assert stranger.exists()


def test_everything_can_be_restored_at_once(library):
    from core.audiobook_recycle import restore_all

    discard(str(_book(library, "A/Book One")))
    discard(str(_book(library, "B/Book Two")))

    summary = restore_all()

    assert summary["restored"] == 2
    assert (library / "A" / "Book One").is_dir()
    assert (library / "B" / "Book Two").is_dir()


def test_the_bin_and_the_blocklist_share_one_view():
    # Two answers to the same question — what did I remove, and can I undo it.
    # Splitting them across two entry points made neither discoverable.
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-review-modal.tsx")
    assert "Recycle bin" in modal and "Blocklist" in modal
    assert "restoreRecycledBook" in modal and "unblockRelease" in modal


def test_the_bin_says_the_space_is_not_freed_yet():
    # The behaviour most likely to surprise somebody on a full disk.
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-review-modal.tsx")
    assert "space is still used" in modal


def test_erasing_for_good_asks_first():
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-review-modal.tsx")
    assert "cannot be undone" in modal
    assert "window.showConfirmDialog" in modal


def test_a_loose_book_file_can_be_recycled_and_restored(library):
    file = library / 'Book.m4b'
    file.write_bytes(b'book audio')
    result = discard(str(file))
    assert result['ok'] and not file.exists()
    restored = restore(Path(result['moved_to']).name)
    assert restored['ok'] and file.read_bytes() == b'book audio'


def test_a_loose_book_file_can_be_deleted_with_recycling_disabled(library):
    file = library / 'Book.m4b'
    file.write_bytes(b'book audio')
    with patch('core.audiobook_recycle.recycling_enabled', return_value=False):
        result = discard(str(file))
    assert result['ok'] and result['permanent'] and not file.exists()
