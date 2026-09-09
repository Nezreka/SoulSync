"""Tests for core/audiobook_post_processor.py.

Hermetic: no network, no real config, no real audio. The mutagen paths are
exercised against real files built by mutagen itself where the format allows
it, and stubbed where building one would mean shipping a binary fixture.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.audiobook_post_processor import (
    COVER_NAME,
    NFO_NAME,
    OPF_NAME,
    book_facts,
    build_nfo,
    build_opf,
    chapter_title,
    existing_cover,
    post_process_book,
    read_asin_from_folder,
    save_cover,
    settings,
)

BOOK = {
    "asin": "B08G9PRS1K",
    "title": "Project Hail Mary",
    "subtitle": "A Novel",
    "author_names": ["Andy Weir"],
    "narrator_names": ["Ray Porter"],
    "series": [{"title": "Standalone Universe", "sequence": "2"}],
    "publisher": "Audible Studios",
    "summary": "Ryland Grace is the sole survivor.",
    "release_date": "2021-05-04",
    "genres": ["Science Fiction", "Adventure"],
    "language": "english",
    "runtime_minutes": 969,
    "cover_url": "https://img/500.jpg",
}


# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------

def test_facts_flatten_the_full_catalogue_payload():
    facts = book_facts(BOOK)
    assert facts["author"] == "Andy Weir"
    assert facts["narrator"] == "Ray Porter"
    assert facts["series"] == "Standalone Universe"
    assert facts["series_sequence"] == "2"
    assert facts["year"] == "2021"
    assert facts["genre"] == "Science Fiction"


def test_facts_accept_the_thin_row_the_monitor_falls_back_to():
    # When the catalogue could not be reached at import time the monitor
    # passes a much thinner dict. Tagging still has to work from it.
    facts = book_facts({"asin": "B1", "title": "T", "author_names": ["A"],
                        "narrator_names": [], "series": []})
    assert facts["author"] == "A"
    assert facts["narrator"] == ""
    assert facts["series"] == ""


def test_facts_accept_dicts_as_well_as_names():
    facts = book_facts({"title": "T", "authors": [{"name": "Andy Weir"}],
                        "narrators": [{"name": "Ray Porter"}]})
    assert facts["authors"] == ["Andy Weir"]
    assert facts["narrators"] == ["Ray Porter"]


def test_a_book_with_no_genre_still_gets_one():
    # An untagged file lands in a media server's uncategorised bucket.
    assert book_facts({"title": "T"})["genre"] == "Audiobook"


def test_a_book_with_no_title_is_still_taggable():
    assert book_facts({})["title"] == "Unknown Title"


def test_only_the_first_narrator_is_used():
    # One book, one narrator. Joining two would paper over a mixed folder the
    # grab side is supposed to have refused.
    facts = book_facts({"title": "T", "narrator_names": ["A", "B"]})
    assert facts["narrator"] == "A"


def test_duplicate_names_collapse():
    facts = book_facts({"title": "T", "author_names": ["Andy Weir", "Andy Weir"]})
    assert facts["authors"] == ["Andy Weir"]


def test_a_malformed_release_date_does_not_become_a_year():
    assert book_facts({"title": "T", "release_date": "soon"})["year"] == ""


def test_the_large_cover_wins_when_both_are_present():
    facts = book_facts({"title": "T", "cover_url": "small.jpg",
                        "cover_url_large": "big.jpg"})
    assert facts["cover_url"] == "big.jpg"


# ---------------------------------------------------------------------------
# Chapter titles
# ---------------------------------------------------------------------------

def test_a_single_file_book_just_uses_the_title():
    assert chapter_title(book_facts(BOOK), 1, 1) == "Project Hail Mary"


def test_a_split_book_gets_numbered_parts():
    assert chapter_title(book_facts(BOOK), 3, 12) == "Project Hail Mary - Part 03"


def test_part_numbers_widen_with_the_count():
    # Part 3 of 120 has to sort before part 12, in a player that only reads
    # the tag.
    assert chapter_title(book_facts(BOOK), 3, 120) == "Project Hail Mary - Part 003"


# ---------------------------------------------------------------------------
# Sidecars
# ---------------------------------------------------------------------------

def test_the_opf_carries_the_asin_in_a_readable_scheme():
    assert 'opf:scheme="ASIN">B08G9PRS1K<' in build_opf(BOOK)


def test_the_opf_separates_authors_from_narrators_by_role():
    opf = build_opf(BOOK)
    assert 'opf:role="aut" opf:file-as="Andy Weir">Andy Weir<' in opf
    assert 'opf:role="nrt">Ray Porter<' in opf


def test_the_opf_carries_the_series():
    opf = build_opf(BOOK)
    assert 'name="calibre:series" content="Standalone Universe"' in opf
    assert 'name="calibre:series_index" content="2"' in opf


def test_the_nfo_puts_the_narrator_in_composer():
    # Which is the convention every audiobook server settled on.
    assert "<composer>Ray Porter</composer>" in build_nfo(BOOK)


def test_the_nfo_records_the_runtime_in_seconds():
    assert "<duration>58140</duration>" in build_nfo(BOOK)


def test_the_nfo_says_it_is_an_audiobook():
    assert "<type>Audiobook</type>" in build_nfo(BOOK)


@pytest.mark.parametrize("builder", [build_opf, build_nfo])
def test_markup_in_a_title_cannot_break_the_sidecar(builder):
    document = builder({**BOOK, "title": 'Tom & "Jerry" <b>'})
    assert "&amp;" in document and "<b>" not in document


@pytest.mark.parametrize("builder", [build_opf, build_nfo])
def test_an_empty_book_still_produces_a_document(builder):
    assert builder({}).startswith("<?xml")


# ---------------------------------------------------------------------------
# Reading the asin back
# ---------------------------------------------------------------------------

def test_the_asin_survives_a_round_trip_through_the_opf(tmp_path):
    (tmp_path / OPF_NAME).write_text(build_opf(BOOK), encoding="utf-8")
    assert read_asin_from_folder(tmp_path) == "B08G9PRS1K"


def test_the_asin_survives_a_round_trip_through_the_nfo(tmp_path):
    (tmp_path / NFO_NAME).write_text(build_nfo(BOOK), encoding="utf-8")
    assert read_asin_from_folder(tmp_path) == "B08G9PRS1K"


def test_a_folder_with_no_sidecar_reports_nothing():
    assert read_asin_from_folder(Path("/definitely/not/here")) == ""


def test_a_mangled_sidecar_reports_nothing_rather_than_raising(tmp_path):
    # One hand-edited file must not stop a whole library scan.
    (tmp_path / OPF_NAME).write_text("<package", encoding="utf-8")
    assert read_asin_from_folder(tmp_path) == ""


# ---------------------------------------------------------------------------
# Artwork
# ---------------------------------------------------------------------------

def test_a_cover_the_release_shipped_is_preferred(tmp_path):
    (tmp_path / "cover.jpg").write_bytes(b"x" * 500)
    found = existing_cover(tmp_path)
    assert found is not None and found[1] == "image/jpeg"


def test_a_png_cover_reports_its_own_mime(tmp_path):
    (tmp_path / "folder.png").write_bytes(b"x" * 500)
    assert existing_cover(tmp_path)[1] == "image/png"


def test_a_truncated_cover_is_ignored(tmp_path):
    (tmp_path / "cover.jpg").write_bytes(b"xx")
    assert existing_cover(tmp_path) is None


def test_saving_a_cover_never_overwrites_one_already_there(tmp_path):
    (tmp_path / COVER_NAME).write_bytes(b"original")
    save_cover(tmp_path, (b"replacement", "image/jpeg"))
    assert (tmp_path / COVER_NAME).read_bytes() == b"original"


def test_saving_no_cover_is_not_an_error(tmp_path):
    assert save_cover(tmp_path, None) == ""


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_settings_default_on_when_the_config_predates_the_keys():
    # There is no deep merge of new defaults into an existing config row, so a
    # read without an explicit default comes back None and silently disables
    # the feature for everyone already running.
    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: default
    with patch("core.settings.config_manager", manager):
        assert settings() == {"embed_metadata": True, "embed_artwork": True,
                              "save_artwork": True, "write_nfo": True}


def test_settings_survive_an_unreadable_config():
    manager = MagicMock()
    manager.get.side_effect = RuntimeError("no config")
    with patch("core.settings.config_manager", manager):
        assert settings()["write_nfo"] is True


def test_settings_are_honoured_when_turned_off():
    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: False
    with patch("core.settings.config_manager", manager):
        assert not any(settings().values())


# ---------------------------------------------------------------------------
# The whole pass
# ---------------------------------------------------------------------------

def _all_on():
    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: True
    return patch("core.settings.config_manager", manager)


def test_a_pass_writes_both_sidecars_and_the_cover(tmp_path):
    (tmp_path / "01.mp3").write_bytes(b"not really audio")
    with _all_on(), \
         patch("core.audiobook_post_processor.fetch_cover",
               return_value=(b"cover-bytes", "image/jpeg")), \
         patch("core.audiobook_post_processor.embed_tags", return_value=True):
        report = post_process_book(str(tmp_path), BOOK)

    assert (tmp_path / OPF_NAME).is_file()
    assert (tmp_path / NFO_NAME).is_file()
    assert (tmp_path / COVER_NAME).read_bytes() == b"cover-bytes"
    assert report["tagged"] == 1


def test_a_pass_tags_every_file_in_order(tmp_path):
    for name in ("03.mp3", "01.mp3", "02.mp3"):
        (tmp_path / name).write_bytes(b"x")

    seen = []
    with _all_on(), \
         patch("core.audiobook_post_processor.fetch_cover", return_value=None), \
         patch("core.audiobook_post_processor.embed_tags",
               side_effect=lambda p, b, i, t, a, **kw: seen.append((p.name, i, t)) or True):
        post_process_book(str(tmp_path), BOOK)

    assert seen == [("01.mp3", 1, 3), ("02.mp3", 2, 3), ("03.mp3", 3, 3)]


def test_a_pass_never_reaches_the_network_when_the_folder_has_a_cover(tmp_path):
    (tmp_path / "01.mp3").write_bytes(b"x")
    (tmp_path / "cover.jpg").write_bytes(b"y" * 500)
    with _all_on(), \
         patch("core.audiobook_post_processor.fetch_cover") as fetch, \
         patch("core.audiobook_post_processor.embed_tags", return_value=True):
        post_process_book(str(tmp_path), BOOK)
    fetch.assert_not_called()


def test_everything_off_does_nothing_at_all(tmp_path):
    (tmp_path / "01.mp3").write_bytes(b"x")
    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: False
    with patch("core.settings.config_manager", manager):
        report = post_process_book(str(tmp_path), BOOK)
    assert report["skipped"] is True
    assert not (tmp_path / OPF_NAME).exists()


def test_nfo_off_still_tags(tmp_path):
    (tmp_path / "01.mp3").write_bytes(b"x")
    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: key != "audiobooks.write_nfo"
    with patch("core.settings.config_manager", manager), \
         patch("core.audiobook_post_processor.fetch_cover", return_value=None), \
         patch("core.audiobook_post_processor.embed_tags", return_value=True) as tag:
        post_process_book(str(tmp_path), BOOK)
    tag.assert_called_once()
    assert not (tmp_path / OPF_NAME).exists()


def test_a_missing_folder_is_reported_not_raised():
    assert post_process_book("/definitely/not/here", BOOK)["skipped"] is True


def test_a_tagging_failure_is_counted_not_raised(tmp_path):
    (tmp_path / "01.mp3").write_bytes(b"x")
    with _all_on(), \
         patch("core.audiobook_post_processor.fetch_cover", return_value=None), \
         patch("core.audiobook_post_processor.embed_tags", return_value=False):
        report = post_process_book(str(tmp_path), BOOK)
    assert report == {**report, "tagged": 0, "failed": 1}


def test_an_explicit_file_list_is_used_over_the_folder(tmp_path):
    (tmp_path / "01.mp3").write_bytes(b"x")
    (tmp_path / "02.mp3").write_bytes(b"x")
    with _all_on(), \
         patch("core.audiobook_post_processor.fetch_cover", return_value=None), \
         patch("core.audiobook_post_processor.embed_tags", return_value=True) as tag:
        post_process_book(str(tmp_path), BOOK, [str(tmp_path / "01.mp3")])
    assert tag.call_count == 1


def test_a_file_list_naming_something_gone_is_dropped(tmp_path):
    (tmp_path / "01.mp3").write_bytes(b"x")
    with _all_on(), \
         patch("core.audiobook_post_processor.fetch_cover", return_value=None), \
         patch("core.audiobook_post_processor.embed_tags", return_value=True) as tag:
        post_process_book(str(tmp_path), BOOK,
                          [str(tmp_path / "01.mp3"), str(tmp_path / "gone.mp3")])
    assert tag.call_count == 1


def test_an_unreachable_cover_does_not_stop_the_pass(tmp_path):
    (tmp_path / "01.mp3").write_bytes(b"x")
    with _all_on(), \
         patch("core.audiobook_post_processor.fetch_cover", return_value=None), \
         patch("core.audiobook_post_processor.embed_tags", return_value=True):
        report = post_process_book(str(tmp_path), BOOK)
    assert report["cover"] == "" and report["tagged"] == 1


# ---------------------------------------------------------------------------
# Real tagging, on a real file mutagen can build
# ---------------------------------------------------------------------------

def test_tags_land_on_a_real_flac_file(tmp_path):
    pytest.importorskip("mutagen")
    from mutagen.flac import FLAC

    # A header-only flac, built here rather than shipped as a binary fixture.
    # STREAMINFO is 34 bytes: block sizes, frame sizes, then 44100Hz / stereo /
    # 16-bit / zero samples packed across 64 bits, then an empty md5.
    import struct
    packed = (44100 << 44) | (1 << 41) | (15 << 36)
    streaminfo = (struct.pack(">H", 4096) + struct.pack(">H", 4096)
                  + b"\x00" * 6 + struct.pack(">Q", packed) + b"\x00" * 16)
    path = tmp_path / "01.flac"
    path.write_bytes(b"fLaC" + b"\x80" + struct.pack(">I", 34)[1:] + streaminfo)

    from core.audiobook_post_processor import embed_tags
    assert embed_tags(path, BOOK, 2, 5) is True

    tags = FLAC(str(path))
    assert tags["ALBUM"] == ["Project Hail Mary"]
    assert tags["COMPOSER"] == ["Ray Porter"]
    assert tags["ALBUMARTIST"] == ["Andy Weir"]
    assert tags["TITLE"] == ["Project Hail Mary - Part 02"]
    assert tags["SERIES"] == ["Standalone Universe"]
    assert tags["ASIN"] == ["B08G9PRS1K"]


def test_tagging_a_file_that_is_not_audio_fails_quietly(tmp_path):
    from core.audiobook_post_processor import embed_tags
    path = tmp_path / "01.mp3"
    path.write_bytes(b"this is not audio")
    assert embed_tags(path, BOOK) is False


def test_tagging_a_file_that_is_not_there_fails_quietly(tmp_path):
    from core.audiobook_post_processor import embed_tags
    assert embed_tags(tmp_path / "gone.mp3", BOOK) is False
