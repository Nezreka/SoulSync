"""iss29-D04: artist-list search must be indexable and must not treat user
input as LIKE pattern syntax.

The membership test used to read
``COALESCE(member.canonical_artist_id, member.id) = a.id``, which no index can
serve, and the search term was interpolated into the pattern without ESCAPE.
"""

from __future__ import annotations

import pytest

from core.library2 import queries as Q


def _artist(conn, name, canonical_of=None):
    cur = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, canonical_artist_id) VALUES(?,?,?)",
        (name, name, canonical_of),
    )
    return int(cur.lastrowid)


def _search(conn, term):
    """`list_artists` returns ``(rows, total)``."""
    return Q.list_artists(conn, search=term or "", include_size=False)


def _names(result):
    rows, _total = result
    return sorted(a["name"] for a in rows)


def test_search_matches_the_canonical_artist_by_its_own_name(imported_conn):
    _artist(imported_conn, "Aphex Twin")
    assert "Aphex Twin" in _names(_search(imported_conn, "Aphex"))


def test_search_matches_a_canonical_artist_through_an_alias_member(imported_conn):
    """The whole point of the EXISTS: an alias's name finds its canonical."""
    canonical = _artist(imported_conn, "Richard D. James")
    _artist(imported_conn, "AFX", canonical_of=canonical)

    assert _names(_search(imported_conn, "AFX")) == ["Richard D. James"]


def test_an_alias_member_is_never_listed_on_its_own(imported_conn):
    canonical = _artist(imported_conn, "Richard D. James")
    _artist(imported_conn, "AFX", canonical_of=canonical)

    assert _names(_search(imported_conn, "Richard")) == ["Richard D. James"]


def test_a_percent_in_the_query_is_a_literal_not_a_wildcard(imported_conn):
    _artist(imported_conn, "100% Electronica")
    _artist(imported_conn, "Boards of Canada")

    assert _names(_search(imported_conn, "100%")) == ["100% Electronica"]
    # The bug: '%' as syntax made this match everything.
    assert _names(_search(imported_conn, "%")) == ["100% Electronica"]


def test_an_underscore_in_the_query_is_a_literal_not_a_single_char_wildcard(imported_conn):
    _artist(imported_conn, "nine_inch_nails")
    _artist(imported_conn, "nineXinchXnails")

    assert _names(_search(imported_conn, "nine_inch")) == ["nine_inch_nails"]


def test_a_backslash_in_the_query_does_not_break_the_pattern(imported_conn):
    _artist(imported_conn, r"AC\DC")
    assert _names(_search(imported_conn, r"AC\D")) == [r"AC\DC"]


def test_the_count_and_the_page_agree_under_search(imported_conn):
    """The WHERE is evaluated twice — count and page must not diverge."""
    canonical = _artist(imported_conn, "Richard D. James")
    _artist(imported_conn, "AFX", canonical_of=canonical)

    rows, total = _search(imported_conn, "AFX")
    assert total == len(rows) == 1


@pytest.mark.parametrize("term", ["", None])
def test_an_empty_search_lists_everything(imported_conn, term):
    _artist(imported_conn, "Aphex Twin")
    assert "Aphex Twin" in _names(_search(imported_conn, term))
