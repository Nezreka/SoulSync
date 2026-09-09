"""Tests for core/audiobook_library_scan.py.

Hermetic: a temp library root and a temp database, no catalogue, no network.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.audiobook_library_scan import (
    MIN_BOOK_BYTES,
    folder_stats,
    is_book_folder,
    iter_book_folders,
    scan,
)
from core.audiobook_post_processor import OPF_NAME, build_opf

BOOK = {
    "asin": "B08G9PRS1K",
    "title": "Project Hail Mary",
    "author_names": ["Andy Weir"],
    "narrator_names": ["Ray Porter"],
    "series": [],
}


@pytest.fixture
def db(tmp_path):
    from core.audiobook_database import AudiobookDatabase
    database = AudiobookDatabase(str(tmp_path / "audiobooks.db"))
    yield database
    database.close()


def make_book_folder(root: Path, *parts, asin="B08G9PRS1K", files=2, sidecar=True) -> Path:
    folder = root.joinpath(*parts)
    folder.mkdir(parents=True, exist_ok=True)
    for index in range(1, files + 1):
        (folder / f"{index:02d} - Chapter.mp3").write_bytes(b"x" * (MIN_BOOK_BYTES + 1))
    if sidecar:
        (folder / OPF_NAME).write_text(build_opf({**BOOK, "asin": asin}), encoding="utf-8")
    return folder


# ---------------------------------------------------------------------------
# Measuring a folder
# ---------------------------------------------------------------------------

def test_stats_count_only_the_audio(tmp_path):
    folder = make_book_folder(tmp_path, "Andy Weir", "Project Hail Mary", files=3)
    (folder / "notes.txt").write_bytes(b"x" * 5000)
    stats = folder_stats(folder)
    assert stats["file_count"] == 3
    assert stats["audio_format"] == "mp3"


def test_stats_do_not_reach_into_subfolders(tmp_path):
    # A series folder must not look like one enormous book.
    parent = tmp_path / "Series"
    make_book_folder(parent, "Book One")
    assert folder_stats(parent)["file_count"] == 0


def test_a_mixed_folder_reports_a_stable_format_string(tmp_path):
    folder = tmp_path / "Book"
    folder.mkdir()
    for name in ("02.mp3", "01.m4b"):
        (folder / name).write_bytes(b"x" * 2000)
    assert folder_stats(folder)["audio_format"] == "m4b/mp3"


def test_stats_of_a_folder_that_is_not_there_are_zero():
    assert folder_stats(Path("/definitely/not/here"))["file_count"] == 0


def test_a_folder_of_leftovers_is_not_a_book(tmp_path):
    folder = tmp_path / "Book"
    folder.mkdir()
    (folder / "01.mp3").write_bytes(b"x" * 100)
    assert is_book_folder(folder) is False


def test_a_folder_of_real_audio_is_a_book(tmp_path):
    assert is_book_folder(make_book_folder(tmp_path, "A", "B")) is True


# ---------------------------------------------------------------------------
# Walking
# ---------------------------------------------------------------------------

def test_the_walk_finds_books_at_the_template_depth(tmp_path):
    make_book_folder(tmp_path, "Andy Weir", "Project Hail Mary")
    make_book_folder(tmp_path, "Brandon Sanderson", "Stormlight", "01 - The Way of Kings")
    found = {p.name for p in iter_book_folders(tmp_path)}
    assert found == {"Project Hail Mary", "01 - The Way of Kings"}


def test_the_walk_stops_at_the_book_and_does_not_go_deeper(tmp_path):
    book = make_book_folder(tmp_path, "Author", "Book")
    make_book_folder(book, "Extras")
    assert [p.name for p in iter_book_folders(tmp_path)] == ["Book"]


def test_the_walk_never_yields_the_root_itself(tmp_path):
    # Otherwise a flat dump of chapters at the top would be adopted as a book
    # named after the library folder.
    for index in range(3):
        (tmp_path / f"{index}.mp3").write_bytes(b"x" * (MIN_BOOK_BYTES + 1))
    assert list(iter_book_folders(tmp_path)) == []


def test_the_walk_respects_its_depth_limit(tmp_path):
    make_book_folder(tmp_path, "a", "b", "c", "d", "e", "f")
    assert list(iter_book_folders(tmp_path, max_depth=2)) == []


def test_walking_somewhere_that_is_not_there_yields_nothing():
    assert list(iter_book_folders(Path("/definitely/not/here"))) == []


# ---------------------------------------------------------------------------
# Reconciling
# ---------------------------------------------------------------------------

def test_a_book_still_on_disk_is_left_alone(tmp_path, db):
    folder = make_book_folder(tmp_path, "Andy Weir", "Project Hail Mary")
    db.add_to_library(BOOK, str(folder), file_count=2,
                      size_bytes=folder_stats(folder)["size_bytes"], audio_format="mp3")

    summary = scan(root=str(tmp_path), db=db)

    assert summary["removed"] == 0
    assert db.is_owned("B08G9PRS1K") is True


def test_a_book_deleted_off_disk_is_forgotten(tmp_path, db):
    # The whole point: an Owned badge must never outlive the book.
    db.add_to_library(BOOK, str(tmp_path / "gone"), file_count=2)

    summary = scan(root=str(tmp_path), db=db)

    assert summary["removed"] == 1
    assert db.is_owned("B08G9PRS1K") is False


def test_a_book_emptied_but_not_deleted_is_forgotten(tmp_path, db):
    # A folder left behind with a stray cover in it is not a book.
    folder = tmp_path / "Andy Weir" / "Project Hail Mary"
    folder.mkdir(parents=True)
    (folder / "cover.jpg").write_bytes(b"x" * 5000)
    db.add_to_library(BOOK, str(folder), file_count=2)

    assert scan(root=str(tmp_path), db=db)["removed"] == 1


def test_forgetting_a_book_leaves_the_wishlist_alone(tmp_path, db):
    # Deleting a copy says something about the copy, not about wanting the
    # book. Re-queueing it would start a download nobody asked for.
    db.add_to_wishlist(BOOK, narrator_mode="exact")
    db.add_to_library(BOOK, str(tmp_path / "gone"))

    scan(root=str(tmp_path), db=db)

    assert [row["asin"] for row in db.get_wishlist()] == ["B08G9PRS1K"]


def test_a_changed_file_count_is_refreshed(tmp_path, db):
    folder = make_book_folder(tmp_path, "Andy Weir", "Project Hail Mary", files=2)
    db.add_to_library(BOOK, str(folder), file_count=99, size_bytes=1)

    assert scan(root=str(tmp_path), db=db)["updated"] == 1
    assert db.get_library()[0]["file_count"] == 2


def test_a_book_with_a_sidecar_is_adopted(tmp_path, db):
    make_book_folder(tmp_path, "Andy Weir", "Project Hail Mary")

    summary = scan(root=str(tmp_path), db=db)

    assert summary["adopted"] == 1
    assert db.is_owned("B08G9PRS1K") is True


def test_an_adopted_book_keeps_its_title_and_author(tmp_path, db):
    make_book_folder(tmp_path, "Andy Weir", "Project Hail Mary")
    scan(root=str(tmp_path), db=db)
    row = db.get_library()[0]
    assert row["title"] == "Project Hail Mary"
    assert row["author"] == "Andy Weir"
    assert row["narrator"] == "Ray Porter"


def test_a_book_with_no_sidecar_is_left_alone(tmp_path, db):
    # Guessing an identity from a folder name would mark a book owned that the
    # user does not have, and the wishlist would then refuse to fetch it.
    make_book_folder(tmp_path, "Andy Weir", "Project Hail Mary", sidecar=False)

    summary = scan(root=str(tmp_path), db=db)

    assert summary["adopted"] == 0
    assert db.get_library() == []


def test_adoption_never_duplicates_a_book_already_recorded(tmp_path, db):
    folder = make_book_folder(tmp_path, "Andy Weir", "Project Hail Mary")
    db.add_to_library(BOOK, str(folder), file_count=2,
                      size_bytes=folder_stats(folder)["size_bytes"])

    scan(root=str(tmp_path), db=db)

    assert len(db.get_library()) == 1


def test_a_book_moved_on_disk_is_adopted_at_its_new_home(tmp_path, db):
    db.add_to_library(BOOK, str(tmp_path / "old" / "Project Hail Mary"))
    make_book_folder(tmp_path, "Andy Weir", "Project Hail Mary")

    summary = scan(root=str(tmp_path), db=db)

    assert summary["removed"] == 1 and summary["adopted"] == 1
    assert db.get_library()[0]["path"].endswith("Project Hail Mary")
    assert db.is_owned("B08G9PRS1K") is True


def test_an_unreachable_root_changes_nothing(tmp_path, db):
    # An unmounted share must not empty the whole library.
    db.add_to_library(BOOK, str(tmp_path / "book"))

    summary = scan(root=str(tmp_path / "not-mounted"), db=db)

    assert summary["missing_root"] is True
    assert summary["removed"] == 0
    assert db.is_owned("B08G9PRS1K") is True


def test_an_empty_library_and_an_empty_folder_are_not_an_error(tmp_path, db):
    summary = scan(root=str(tmp_path), db=db)
    assert summary["checked"] == 0 and summary["adopted"] == 0


def test_a_database_that_cannot_be_read_is_reported_not_raised(tmp_path):
    broken = MagicMock()
    broken.get_library.side_effect = RuntimeError("locked")
    assert scan(root=str(tmp_path), db=broken)["checked"] == 0


def test_a_row_that_will_not_delete_does_not_stop_the_scan(tmp_path):
    stubborn = MagicMock()
    stubborn.get_library.return_value = [
        {"asin": "A", "path": str(tmp_path / "gone-a"), "file_count": 1},
        {"asin": "B", "path": str(tmp_path / "gone-b"), "file_count": 1},
    ]
    stubborn.remove_from_library.side_effect = [RuntimeError("locked"), True]

    summary = scan(root=str(tmp_path), db=stubborn)

    assert summary["checked"] == 2 and summary["removed"] == 1


def test_the_root_falls_back_to_the_configured_library(tmp_path, db):
    make_book_folder(tmp_path, "Andy Weir", "Project Hail Mary")
    with patch("core.audiobook_organizer.library_root", return_value=str(tmp_path)):
        assert scan(db=db)["adopted"] == 1


# ---------------------------------------------------------------------------
# The recycle bin is not the library
# ---------------------------------------------------------------------------

def test_a_deleted_book_is_not_adopted_back_out_of_the_bin(tmp_path, db):
    """The bin lives INSIDE the library root.

    Without skipping it, a book deleted yesterday is found in .deleted, read
    from its own sidecar, and adopted straight back — the delete would
    silently undo itself on the next daily scan.
    """
    make_book_folder(tmp_path, ".deleted", "20260909_120000_Project Hail Mary")

    summary = scan(root=str(tmp_path), db=db)

    assert summary["adopted"] == 0
    assert db.get_library() == []


def test_hidden_folders_are_left_alone_generally(tmp_path, db):
    # Somebody else's dot-folder is not the library's business either.
    make_book_folder(tmp_path, ".stfolder", "Book")
    assert scan(root=str(tmp_path), db=db)["adopted"] == 0


def test_a_real_book_beside_the_bin_is_still_found(tmp_path, db):
    make_book_folder(tmp_path, ".deleted", "20260909_120000_Deleted Book", asin="OLD")
    make_book_folder(tmp_path, "Andy Weir", "Project Hail Mary", asin="KEEP")

    scan(root=str(tmp_path), db=db)

    assert [r["asin"] for r in db.get_library()] == ["KEEP"]
