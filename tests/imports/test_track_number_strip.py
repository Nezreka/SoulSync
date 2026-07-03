"""#890: a leading track number leaking from a filename stem into the title
("01 - Sun It Rises") makes the track never match the canonical "Sun It Rises",
so it reads as a false "missing". strip_leading_track_number removes the prefix —
conservatively, so titles that merely START with a number are left alone.
"""

from __future__ import annotations

import pytest

from core.imports.context import get_import_clean_title
from core.imports.paths import strip_leading_track_number


# ── the bug: track-number prefixes get stripped ───────────────────────────────
@pytest.mark.parametrize("dirty,clean", [
    ("01 - Sun It Rises", "Sun It Rises"),          # the screenshot
    ("04 - Tiger Mountain Peasant Song", "Tiger Mountain Peasant Song"),
    ("05 - Quiet Houses", "Quiet Houses"),
    ("07 - Heard Them Stirring", "Heard Them Stirring"),
    ("01 Sun It Rises", "Sun It Rises"),            # zero-padded, no separator
    ("3 - Title", "Title"),                          # plain number + separator + space
    ("12. Some Song", "Some Song"),                  # dot separator
    ("10 - Track Ten", "Track Ten"),
    ("09) Closing Time", "Closing Time"),            # paren separator
    ("  02 -  Spaced Out ", "Spaced Out"),           # messy whitespace
])
def test_strips_track_number_prefix(dirty, clean):
    assert strip_leading_track_number(dirty) == clean


# ── the guard: titles that legitimately start with a number are UNTOUCHED ──────
@pytest.mark.parametrize("title", [
    "7 Rings",
    "99 Luftballons",
    "50 Ways to Leave Your Lover",
    "1-800-273-8255",          # number-with-dashes is part of the title
    "1979",
    "9 to 5",
    "4 Minutes",
    "8 Mile",
    "21 Guns",
    "24 Hour Party People",     # no separator → not a track number
    "0 to 100",
    "Sun It Rises",             # no leading number at all
])
def test_preserves_real_titles(title):
    assert strip_leading_track_number(title) == title


# ── degenerate inputs ─────────────────────────────────────────────────────────
def test_never_reduces_to_empty_or_bare_number():
    assert strip_leading_track_number("01") == "01"      # bare number → keep
    assert strip_leading_track_number("01 - ") == "01 -"  # nothing left → keep original (trimmed)
    assert strip_leading_track_number("") == ""
    assert strip_leading_track_number(None) == ""


def test_only_strips_one_prefix():
    # A title that legitimately follows the number keeps its own leading number.
    assert strip_leading_track_number("01 - 24 Hour Party People") == "24 Hour Party People"


# ── the chokepoint: every import path resolves its title through here ──────────
def test_get_import_clean_title_strips_filename_leak():
    # original_search['title'] came from the filename stem (no embedded tag).
    ctx = {"original_search_result": {"title": "01 - Sun It Rises"}}
    assert get_import_clean_title(ctx) == "Sun It Rises"


def test_get_import_clean_title_leaves_clean_source_title():
    ctx = {"original_search_result": {"title": "7 Rings"}}
    assert get_import_clean_title(ctx) == "7 Rings"


def test_get_import_clean_title_default_untouched():
    assert get_import_clean_title({}, default="Unknown Track") == "Unknown Track"
