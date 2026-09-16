"""Tests for core/audiobook_organizer.py.

Everything runs against tmp_path. No test here writes outside its own directory,
and none of them can reach a configured library.
"""

from pathlib import Path

import pytest

from core.audiobook_organizer import (
    DEFAULT_TEMPLATE,
    chapter_filename,
    collect_audio_files,
    natural_key,
    organize_download,
    render_audiobook_path,
    sanitize_segment,
)

BOOK = {
    "asin": "B08G9PRS1K",
    "title": "The Final Empire",
    "author_names": ["Brandon Sanderson"],
    "narrator_names": ["Michael Kramer"],
    "series": [{"title": "The Mistborn Saga", "sequence": "1"}],
    "release_date": "2006-07-17",
}

STANDALONE = {
    "asin": "B1",
    "title": "Project Hail Mary",
    "author_names": ["Andy Weir"],
    "narrator_names": ["Ray Porter"],
    "series": [],
    "release_date": "2021-05-04",
}


# ---------------------------------------------------------------------------
# Sanitising
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Normal Title", "Normal Title"),
    ("Bad/Name", "Bad_Name"),
    ('Quote"Colon:Star*', "Quote_Colon_Star"),
    ("  padded  ", "padded"),
    ("trailing...", "trailing"),
])
def test_sanitize_segment(raw, expected):
    assert sanitize_segment(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "...", None])
def test_sanitize_empty_stays_empty(raw):
    # Empty must NOT become a placeholder — the caller drops empty segments,
    # which is what lets a standalone book lose its series folder rather than
    # gain one called "Unknown".
    assert sanitize_segment(raw) == ""


def test_sanitize_caps_the_length():
    assert len(sanitize_segment("x" * 400)) == 180


# ---------------------------------------------------------------------------
# Natural ordering
# ---------------------------------------------------------------------------

def test_chapter_ten_sorts_after_chapter_two():
    # A plain string sort puts "Chapter 10" before "Chapter 2" and silently
    # reorders the whole book.
    names = ["Chapter 10.mp3", "Chapter 2.mp3", "Chapter 1.mp3"]
    assert sorted(names, key=natural_key) == ["Chapter 1.mp3", "Chapter 2.mp3", "Chapter 10.mp3"]


def test_natural_key_handles_names_without_numbers():
    assert sorted(["b.mp3", "a.mp3"], key=natural_key) == ["a.mp3", "b.mp3"]


# ---------------------------------------------------------------------------
# Path template
# ---------------------------------------------------------------------------

def test_the_default_layout_for_a_series_book():
    assert render_audiobook_path(DEFAULT_TEMPLATE, BOOK) == [
        "Brandon Sanderson", "The Mistborn Saga", "01 - The Final Empire",
    ]


def test_a_standalone_book_collapses_the_series_folder():
    # The whole reason empty segments are dropped rather than filled in.
    assert render_audiobook_path(DEFAULT_TEMPLATE, STANDALONE) == [
        "Andy Weir", "Project Hail Mary",
    ]


def test_the_sequence_is_zero_padded_so_ten_sorts_after_two():
    book = dict(BOOK, series=[{"title": "Saga", "sequence": "10"}])
    assert render_audiobook_path(DEFAULT_TEMPLATE, book)[-1].startswith("10 - ")
    book = dict(BOOK, series=[{"title": "Saga", "sequence": "2"}])
    assert render_audiobook_path(DEFAULT_TEMPLATE, book)[-1].startswith("02 - ")


def test_a_half_numbered_novella_keeps_its_decimal():
    book = dict(BOOK, series=[{"title": "Saga", "sequence": "2.5"}])
    assert render_audiobook_path(DEFAULT_TEMPLATE, book)[-1].startswith("2.5 - ")


@pytest.mark.parametrize("template,expected", [
    ("$author/$title", ["Brandon Sanderson", "The Final Empire"]),
    ("$authorletter/$author/$title", ["B", "Brandon Sanderson", "The Final Empire"]),
    ("$narrator/$title", ["Michael Kramer", "The Final Empire"]),
    ("$title ($year)", ["The Final Empire (2006)"]),
    ("$asin", ["B08G9PRS1K"]),
    ("$series/$title", ["The Mistborn Saga", "The Final Empire"]),
])
def test_template_variables(template, expected):
    assert render_audiobook_path(template, BOOK) == expected


def test_an_unknown_author_still_yields_a_letter_folder():
    book = dict(STANDALONE, author_names=[])
    assert render_audiobook_path("$authorletter/$title", book) == ["#", "Project Hail Mary"]


def test_a_template_that_renders_to_nothing_still_produces_a_folder():
    # Otherwise the book would be written straight into the library root.
    assert render_audiobook_path("$series/$seriespos", STANDALONE) == ["Project Hail Mary"]


def test_illegal_characters_in_metadata_are_made_safe():
    book = dict(STANDALONE, title="Who? What: Where/When")
    assert "/" not in render_audiobook_path("$title", book)[0]


def test_an_empty_template_falls_back_to_the_default():
    assert render_audiobook_path("", BOOK) == render_audiobook_path(DEFAULT_TEMPLATE, BOOK)


# ---------------------------------------------------------------------------
# Collecting files
# ---------------------------------------------------------------------------

def _make_files(root: Path, names):
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
    return root


def test_audio_files_are_found_in_playing_order(tmp_path):
    _make_files(tmp_path, ["Chapter 10.mp3", "Chapter 2.mp3", "Chapter 1.mp3"])
    assert [p.name for p in collect_audio_files(tmp_path)] == [
        "Chapter 1.mp3", "Chapter 2.mp3", "Chapter 10.mp3",
    ]


def test_nested_disc_folders_stay_in_order(tmp_path):
    # Uploads nest: Release.Name/Disc 1/*.mp3 is normal.
    _make_files(tmp_path, ["Disc 2/01.mp3", "Disc 1/02.mp3", "Disc 1/01.mp3"])
    found = [str(p.relative_to(tmp_path)).replace("\\", "/") for p in collect_audio_files(tmp_path)]
    assert found == ["Disc 1/01.mp3", "Disc 1/02.mp3", "Disc 2/01.mp3"]


def test_non_audio_files_are_ignored(tmp_path):
    _make_files(tmp_path, ["book.mp3", "cover.jpg", "readme.txt", "sample.exe"])
    assert [p.name for p in collect_audio_files(tmp_path)] == ["book.mp3"]


def test_a_single_file_download(tmp_path):
    path = tmp_path / "book.m4b"
    path.write_bytes(b"audio")
    assert collect_audio_files(path) == [path]


def test_a_missing_source_yields_nothing(tmp_path):
    assert collect_audio_files(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# Chapter naming
# ---------------------------------------------------------------------------

def test_renumbering_keeps_the_uploaders_chapter_title():
    # That name is usually the chapter title; throwing it away loses real
    # information.
    name = chapter_filename(3, Path("The Well of Ascension.mp3"), 12)
    assert name == "03 - The Well of Ascension.mp3"


def test_renumbering_does_not_double_up_an_existing_number():
    # Otherwise the file ends up called "01 - 01 - Chapter One".
    assert chapter_filename(1, Path("01 - Chapter One.mp3"), 9) == "01 - Chapter One.mp3"
    assert chapter_filename(2, Path("02. Chapter Two.mp3"), 9) == "02 - Chapter Two.mp3"


def test_padding_widens_for_a_long_book():
    assert chapter_filename(7, Path("Ch.mp3"), 120).startswith("007 - ")


def test_renumbering_can_be_turned_off():
    assert chapter_filename(3, Path("Original Name.mp3"), 12, renumber=False) == "Original Name.mp3"


# ---------------------------------------------------------------------------
# Organizing
# ---------------------------------------------------------------------------

def test_a_download_lands_in_the_templated_folder(tmp_path):
    source = _make_files(tmp_path / "dl", ["Chapter 1.mp3", "Chapter 2.mp3"])
    library = tmp_path / "library"
    result = organize_download(str(source), BOOK, str(library))

    assert result["ok"] is True
    expected = library / "Brandon Sanderson" / "The Mistborn Saga" / "01 - The Final Empire"
    assert expected.is_dir()
    assert sorted(p.name for p in expected.iterdir()) == [
        "01 - Chapter 1.mp3", "02 - Chapter 2.mp3",
    ]


def test_the_source_download_is_never_deleted(tmp_path):
    # Copy, not move: a failed organize stays retryable and a half-moved book
    # cannot lose chapters.
    source = _make_files(tmp_path / "dl", ["Chapter 1.mp3"])
    organize_download(str(source), BOOK, str(tmp_path / "library"))
    assert (source / "Chapter 1.mp3").exists()


def test_organizing_twice_is_a_no_op(tmp_path):
    source = _make_files(tmp_path / "dl", ["Chapter 1.mp3"])
    library = tmp_path / "library"
    organize_download(str(source), BOOK, str(library))
    again = organize_download(str(source), BOOK, str(library))
    assert again["ok"] is True
    assert again["files"] == []
    assert len(again["skipped"]) == 1


def test_a_download_with_no_audio_is_an_error(tmp_path):
    source = _make_files(tmp_path / "dl", ["readme.txt"])
    result = organize_download(str(source), BOOK, str(tmp_path / "library"))
    assert result["ok"] is False
    assert "No audio files" in result["error"]


def test_the_cover_comes_along(tmp_path):
    source = _make_files(tmp_path / "dl", ["Chapter 1.mp3", "cover.jpg"])
    result = organize_download(str(source), BOOK, str(tmp_path / "library"))
    assert (Path(result["path"]) / "cover.jpg").exists()


def test_a_standalone_book_gets_a_two_level_path(tmp_path):
    source = _make_files(tmp_path / "dl", ["book.m4b"])
    result = organize_download(str(source), STANDALONE, str(tmp_path / "library"))
    relative = Path(result["path"]).relative_to(tmp_path / "library")
    assert relative.parts == ("Andy Weir", "Project Hail Mary")


def test_even_a_single_file_book_gets_its_own_folder(tmp_path):
    # So a cover, an NFO or a chapters file has somewhere to live later.
    source = tmp_path / "dl"
    source.mkdir()
    (source / "book.m4b").write_bytes(b"audio")
    result = organize_download(str(source), STANDALONE, str(tmp_path / "library"))
    assert Path(result["path"]).is_dir()
    assert (Path(result["path"]) / "01 - book.m4b").exists()


# ---------------------------------------------------------------------------
# One book, one release
#
# Audible sells the same book read by different narrators as separate editions.
# A folder that took chapters 1-6 from one and 7-12 from another is not a book,
# and the damage is silent: every chapter file is individually fine.
# ---------------------------------------------------------------------------

def test_a_book_folder_records_the_release_that_filled_it(tmp_path):
    from core.audiobook_organizer import read_release_marker

    source = _make_files(tmp_path / "dl", ["Chapter 1.mp3"])
    result = organize_download(str(source), BOOK, str(tmp_path / "library"),
                               release_id="kramer-m4b")
    assert result["ok"] is True
    assert read_release_marker(Path(result["path"])) == "kramer-m4b"


def test_a_second_release_is_refused(tmp_path):
    # The whole guarantee: never interleave two narrators' chapters.
    library = tmp_path / "library"
    first = _make_files(tmp_path / "dl1", ["Chapter 1.mp3", "Chapter 2.mp3"])
    organize_download(str(first), BOOK, str(library), release_id="kramer-m4b")

    second = _make_files(tmp_path / "dl2", ["Chapter 3.mp3"])
    result = organize_download(str(second), BOOK, str(library), release_id="full-cast-mp3")

    assert result["ok"] is False
    assert "different release" in result["error"]
    assert result["files"] == []


def test_the_refused_release_leaves_the_folder_untouched(tmp_path):
    library = tmp_path / "library"
    first = _make_files(tmp_path / "dl1", ["Chapter 1.mp3"])
    ok = organize_download(str(first), BOOK, str(library), release_id="kramer")
    before = sorted(p.name for p in Path(ok["path"]).iterdir())

    second = _make_files(tmp_path / "dl2", ["Chapter 9.mp3"])
    organize_download(str(second), BOOK, str(library), release_id="someone-else")

    assert sorted(p.name for p in Path(ok["path"]).iterdir()) == before


def test_re_running_the_same_release_is_still_idempotent(tmp_path):
    # A retry of the SAME release must keep working; only a different one is
    # refused.
    library = tmp_path / "library"
    source = _make_files(tmp_path / "dl", ["Chapter 1.mp3"])
    organize_download(str(source), BOOK, str(library), release_id="kramer")
    again = organize_download(str(source), BOOK, str(library), release_id="kramer")
    assert again["ok"] is True
    assert again["files"] == []


def test_an_unmarked_folder_still_accepts_a_release(tmp_path):
    # Shipping this must not make an existing library un-writable.
    from core.audiobook_organizer import folder_accepts_release

    folder = tmp_path / "existing"
    folder.mkdir()
    assert folder_accepts_release(folder, "anything") is True


def test_organizing_without_a_release_id_claims_nothing(tmp_path):
    from core.audiobook_organizer import read_release_marker

    source = _make_files(tmp_path / "dl", ["Chapter 1.mp3"])
    result = organize_download(str(source), BOOK, str(tmp_path / "library"))
    assert result["ok"] is True
    assert read_release_marker(Path(result["path"])) == ""


def test_the_marker_is_not_copied_out_of_a_download(tmp_path):
    # A download that happens to contain one must not claim the library folder
    # for the wrong release.
    from core.audiobook_organizer import RELEASE_MARKER, read_release_marker

    source = _make_files(tmp_path / "dl", ["Chapter 1.mp3"])
    (source / RELEASE_MARKER).write_text("some-other-release", encoding="utf-8")
    result = organize_download(str(source), BOOK, str(tmp_path / "library"),
                               release_id="mine")
    assert read_release_marker(Path(result["path"])) == "mine"
