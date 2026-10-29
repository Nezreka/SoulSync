"""Extra audiobook folders: an existing collection on a second drive.

``library.audiobooks_additional_paths`` lists folders SoulSync scans and
manages but never downloads into. New books still land in the main folder.
An unplugged extra drive must never cost the library its books.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.audiobook_library_scan import MIN_BOOK_BYTES, scan
from core.audiobook_organizer import library_roots, root_for
from core.audiobook_post_processor import OPF_NAME, build_opf


def _settings(main, extra):
    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: {
        "library.audiobooks_path": str(main),
        "library.audiobooks_additional_paths": extra,
    }.get(key, default)
    return patch("core.settings.config_manager", manager)


@pytest.fixture
def db(tmp_path):
    from core.audiobook_database import AudiobookDatabase
    database = AudiobookDatabase(str(tmp_path / "audiobooks.db"))
    yield database
    database.close()


def _book(root: Path, *parts, asin) -> Path:
    folder = root.joinpath(*parts)
    folder.mkdir(parents=True, exist_ok=True)
    for index in (1, 2):
        (folder / f"{index:02d} - Chapter.mp3").write_bytes(b"x" * (MIN_BOOK_BYTES + 1))
    (folder / OPF_NAME).write_text(build_opf({"asin": asin, "title": "T " + asin,
                                              "author_names": ["A"], "series": []}),
                                   encoding="utf-8")
    return folder


# ── the folder list ──────────────────────────────────────────────────────────

def test_main_folder_first_then_the_extra_ones(tmp_path):
    main, two, three = tmp_path / "books", tmp_path / "drive2", tmp_path / "drive3"
    with _settings(main, [str(two), "  ", str(three), 7]):
        assert library_roots() == [str(main), str(two), str(three)]


def test_no_extra_folders_is_just_the_main_one(tmp_path):
    with _settings(tmp_path / "books", None):
        assert library_roots() == [str(tmp_path / "books")]


def test_an_extra_folder_that_would_read_books_twice_is_dropped(tmp_path):
    main = tmp_path / "books"
    extra = [str(main), str(main / "sub"), str(tmp_path), str(tmp_path / "other")]
    with _settings(main, extra):
        assert library_roots() == [str(main), str(tmp_path / "other")]


def test_root_for_finds_the_folder_holding_a_book(tmp_path):
    main, two = tmp_path / "books", tmp_path / "drive2"
    with _settings(main, [str(two)]):
        assert root_for(two / "Author" / "Book") == two.resolve()
        assert root_for(main / "Author" / "Book") == main.resolve()
        assert root_for(two) is None                      # a root is not a book
        assert root_for(tmp_path / "elsewhere" / "Book") is None
        assert root_for("") is None


# ── the scan ─────────────────────────────────────────────────────────────────

def test_the_scan_reads_every_folder(tmp_path, db):
    main, two = tmp_path / "books", tmp_path / "drive2"
    _book(main, "A", "One", asin="B000000001")
    _book(two, "A", "Two", asin="B000000002")
    with _settings(main, [str(two)]):
        summary = scan(db=db)
    assert summary["adopted"] == 2 and summary["status"] == "completed"
    assert db.is_owned("B000000001") and db.is_owned("B000000002")


def test_an_unplugged_extra_drive_keeps_its_books(tmp_path, db):
    main, two = tmp_path / "books", tmp_path / "drive2"
    _book(main, "A", "One", asin="B000000001")
    on_two = _book(two, "A", "Two", asin="B000000002")
    with _settings(main, [str(two)]):
        scan(db=db)
    # the drive goes away: its files are gone from view, the books stay
    for child in on_two.iterdir():
        child.unlink()
    on_two.rmdir()
    (two / "A").rmdir()
    two.rmdir()
    with _settings(main, [str(two)]):
        summary = scan(db=db)
    assert summary["removed"] == 0 and summary["status"] == "completed"
    assert summary["unreachable"] == [str(two.resolve())]
    assert db.is_owned("B000000002")


def test_a_book_gone_from_a_reachable_extra_folder_is_forgotten(tmp_path, db):
    main, two = tmp_path / "books", tmp_path / "drive2"
    main.mkdir()
    two.mkdir()
    db.add_to_library({"asin": "B000000009", "title": "Gone"}, str(two / "A" / "Gone"))
    with _settings(main, [str(two)]):
        assert scan(db=db)["removed"] == 1
    assert not db.is_owned("B000000009")


def test_a_missing_main_folder_is_still_an_error(tmp_path, db):
    two = tmp_path / "drive2"
    _book(two, "A", "Two", asin="B000000002")
    with _settings(tmp_path / "not-mounted", [str(two)]):
        summary = scan(db=db)
    assert summary["missing_root"] is True and summary["adopted"] == 0


def test_an_explicit_root_scans_just_that_one(tmp_path, db):
    main, two = tmp_path / "books", tmp_path / "drive2"
    _book(main, "A", "One", asin="B000000001")
    _book(two, "A", "Two", asin="B000000002")
    with _settings(main, [str(two)]):
        assert scan(root=str(two), db=db)["adopted"] == 1
    assert db.is_owned("B000000002") and not db.is_owned("B000000001")
