"""Splitting a free-text song query into artist and title (core.deezer_track_query).

The typed searches read the artist from Deezer's own results and then run
``track:"title" artist``, because the plain search leaves the original of
"How Far I'll Go" (track 136340808) out of the page.
"""

from __future__ import annotations

from core.deezer_track_query import (
    exact_title_queries,
    fold,
    split_query_by_artist,
)

# ── pure helpers ─────────────────────────────────────────────────────────────

def test_fold_deletes_apostrophes_so_stripped_and_real_names_agree():
    assert fold("Auli'i Cravalho") == fold("aulii cravalho") == "aulii cravalho"
    assert fold("Auli’i Cravalho") == "aulii cravalho"
    assert fold("Beyoncé") == "beyonce"


def test_split_finds_artist_at_start():
    assert split_query_by_artist("aulii cravalho how far ill go", ["Auli'i Cravalho"]) == (
        "aulii cravalho",
        "how far ill go",
    )


def test_split_finds_artist_at_end():
    assert split_query_by_artist("how far ill go aulii cravalho", ["Auli'i Cravalho"]) == (
        "aulii cravalho",
        "how far ill go",
    )


def test_split_prefers_the_longest_artist():
    names = ["Alan", "Alan Menken"]
    assert split_query_by_artist("alan menken arabian nights", names) == ("alan menken", "arabian nights")


def test_split_ignores_an_artist_that_is_only_part_of_a_word_or_the_whole_query():
    assert split_query_by_artist("cravalhos song", ["Cravalho"]) is None
    assert split_query_by_artist("aulii cravalho", ["Auli'i Cravalho"]) is None  # nothing left for a title


def test_split_returns_none_when_no_artist_borders_the_query():
    assert split_query_by_artist("how far ill go", ["Alessia Cara"]) is None


def test_exact_title_query_with_artist_keeps_artist_as_plain_words():
    # artist:"..." returns nothing on Deezer's side, so it must not be used.
    [q] = exact_title_queries("aulii cravalho how far ill go", ["Auli'i Cravalho"])
    assert q == 'track:"how far ill go" aulii cravalho'
    assert "artist:" not in q


def test_exact_title_query_without_artist_treats_the_query_as_the_title():
    assert exact_title_queries("how far ill go", ["Alessia Cara"]) == ['track:"how far ill go"']
    assert exact_title_queries("", []) == []


def test_quotes_in_the_query_cannot_break_out_of_the_filter():
    [q] = exact_title_queries('say "hi"', [])
    assert q.count('"') == 2
