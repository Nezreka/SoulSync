"""Punctuated titles were unfindable in the manual-match search (#1159, AfonsoG6).

    "In some weird edge cases I'm unable to find the tracks I know I have. When
    searching for the song "Would've, Could've, Should've" by Taylor Swift, only
    other songs with the word "Should've" appear. The commas may be interfering
    somehow."

He was right that commas were involved, though not where he'd expect. The basic
LIKE handles them fine — it is the FUZZY fallback, which only runs when the basic
search found nothing at all (``if basic_rows: return`` in ``api_search_tracks``),
so it is exactly the path a user hits when the library's tag differs from the
source title.

It split on whitespace and stripped only whitespace, leaving punctuation glued
to each token:

    "Would've, Could've, Should've"  ->  %would've,%  %could've,%  %should've%

``%would've,%`` matches only a title that has the comma too. A library file
tagged **without** the commas — which taggers and filesystems routinely do —
therefore matched on ``%should've%`` alone, scoring 1, the same as every
unrelated song containing that one word. The tie then fell back to alphabetical
order and the real track landed at rank 3, behind the noise. That is his report
exactly.

Trimming ends only. Internal punctuation has to survive (N.W.A, P!nk,
"pepper's"), and the column side keeps its own punctuation because
``unidecode_lower`` only lowercases — so a trimmed term matches everything the
untrimmed one did and more. It can raise a row's score, never lower it.
"""

import sqlite3
import types

import pytest

from core.library2.schema import ensure_library_v2_schema
from core.text.normalize import normalize_for_comparison
from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track

try:
    from unidecode import unidecode as _ud
except ImportError:                                   # pragma: no cover
    def _ud(x):
        return x


def _library(titles):
    """A minimal lib2 catalogue with the same unidecode_lower the real connection
    registers. Track 1 (the reported track) is Taylor Swift/Midnights; every
    other track is Various/Other — the fuzzy search joins through the album's
    ``primary_artist_id``, not a per-track artist column."""
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.create_function("unidecode_lower", 1, lambda x: _ud(x).lower() if x else "")
    ensure_library_v2_schema(db)
    taylor = seed_artist(db, server_id='ar-taylor', name='Taylor Swift')
    various = seed_artist(db, server_id='ar-various', name='Various')
    midnights = seed_album(db, server_id='al-midnights', title='Midnights', artist_id=taylor)
    other = seed_album(db, server_id='al-other', title='Other', artist_id=various)
    for i, title in enumerate(titles, start=1):
        is_first = i == 1
        seed_track(
            db, server_id=f'tr-{i}', title=title,
            album_id=midnights if is_first else other,
            artist_id=taylor if is_first else various,
            server_source='jellyfin',
        )
    db.commit()
    return db


def _search(db, title, artist='', limit=15):
    """The REAL fuzzy method, bound to a stub that supplies only what it uses."""
    stub = types.SimpleNamespace(
        _normalize_for_comparison=normalize_for_comparison,
        _fuzzy_terms=MusicDatabase._fuzzy_terms,
    )
    rows = MusicDatabase._search_tracks_fuzzy_rows(
        stub, db.cursor(), title, artist, limit, None)
    return [r['title'] for r in rows]


# ── tokenising ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("would've, could've, should've", ["would've", "could've", "should've"]),
    ("stop! in the name of love", ['stop', 'the', 'name', 'love']),
    ("marry you (bruno mars)", ['marry', 'you', 'bruno', 'mars']),
    ("hey jude", ['hey', 'jude']),
])
def test_punctuation_is_trimmed_from_the_ends(text, expected):
    assert MusicDatabase._fuzzy_terms(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("n.w.a", ['n.w.a']),        # a band name that IS punctuation
    ("p!nk", ['p!nk']),
    ("pepper's", ["pepper's"]),  # possessive, mid-word
    ("ac/dc", ['ac/dc']),
])
def test_internal_punctuation_survives(text, expected):
    """Trimming the ends must not reach inside a word — these are real names."""
    assert MusicDatabase._fuzzy_terms(text) == expected


def test_short_words_are_still_dropped():
    """The >= 3 rule is unchanged; it just applies to the trimmed word now."""
    assert MusicDatabase._fuzzy_terms("a, of the mad") == ['the', 'mad']


@pytest.mark.parametrize("junk", ["", "   ", ",,, !!! ---"])
def test_nothing_usable_yields_no_terms(junk):
    assert MusicDatabase._fuzzy_terms(junk) == []


# ── the reported failure ────────────────────────────────────────────────────

HIS_QUERY = "Would've, Could've, Should've"
HIS_LIBRARY = [
    "Would've Could've Should've",      # the track he has — tagged WITHOUT commas
    "Should've Been Us",
    "Should've Said No",
    "You Should've Known",
    "Could've Been",
]


def test_the_track_he_owns_is_the_first_result():
    """It used to come back third, behind two songs that merely share one word."""
    results = _search(_library(HIS_LIBRARY), HIS_QUERY)
    assert results[0] == "Would've Could've Should've", results


def test_it_beats_the_noise_because_it_matches_more_words():
    """Not luck of the alphabet — it matches all three terms where the others
    match one, and the scoring counts that."""
    results = _search(_library(HIS_LIBRARY), HIS_QUERY)
    for noise in ("Should've Been Us", "Should've Said No", "You Should've Known"):
        assert results.index("Would've Could've Should've") < results.index(noise)


def test_the_comma_form_still_matches_when_the_library_has_commas_too():
    """The case that already worked must keep working."""
    lib = ["Would've, Could've, Should've", "Should've Said No"]
    assert _search(_library(lib), HIS_QUERY)[0] == "Would've, Could've, Should've"


# ── nothing that worked before is disturbed ─────────────────────────────────

@pytest.mark.parametrize("query,library", [
    ("Hey Jude", ["Hey Jude", "Hey Joe", "Hey You", "Jude Law Theme"]),
    ("Marry You (Bruno Mars)", ["Marry You", "Marry Me", "You And I"]),
    ("Stop! In the Name of Love", ["Stop In the Name of Love", "Stop Crying"]),
    ("Sunday Bloody Sunday", ["Sunday Bloody Sunday", "Sunday Morning"]),
])
def test_an_unpunctuated_or_already_working_query_still_ranks_first(query, library):
    assert _search(_library(library), query)[0] == library[0]


def test_a_band_name_made_of_punctuation_still_finds_itself():
    """N.W.A must not be trimmed into something that matches everything."""
    lib = ["Straight Outta Compton", "Compton Anthem"]
    assert _search(_library(lib), "Straight Outta Compton")[0] == "Straight Outta Compton"


def test_trimming_never_removes_a_term_that_used_to_match():
    """The column keeps its punctuation, so a trimmed term is strictly broader —
    it matches everything the untrimmed one did. This is what makes the change
    safe rather than merely better on the reported case."""
    for text in ["would've, could've, should've", "stop! in the name of love",
                 "marry you (bruno mars)", "sgt. pepper's lonely hearts club band"]:
        old = [w.strip() for w in text.split() if len(w.strip()) >= 3]
        new = MusicDatabase._fuzzy_terms(text)
        for old_term in old:
            trimmed = old_term.strip('!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~')
            if len(trimmed) >= 3:
                assert trimmed in new, f"{old_term!r} lost its match"


# ── the artist half, which the manual-match search also uses ────────────────
#
# search_library_candidates queries api_search_tracks(title=q) AND
# (artist=q), so an artist typed into that one box goes through the same
# tokeniser. Band names with commas are not exotic — "Crosby, Stills & Nash",
# "Emerson, Lake & Palmer", "Blood, Sweat & Tears".

def _library_by_artist(pairs):
    """Each artist gets its own album, so the search join (through the album's
    ``primary_artist_id``) resolves to the right one per track."""
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.create_function("unidecode_lower", 1, lambda x: _ud(x).lower() if x else "")
    ensure_library_v2_schema(db)
    for i, (artist, title) in enumerate(pairs, start=1):
        artist_id = seed_artist(db, server_id=f'ar-{i}', name=artist)
        album_id = seed_album(db, server_id=f'al-{i}', title='A', artist_id=artist_id)
        seed_track(db, server_id=f'tr-{i}', title=title, album_id=album_id,
                  artist_id=artist_id, server_source='jellyfin')
    db.commit()
    return db


def test_a_comma_in_a_band_name_does_not_bury_it():
    """The library has the band tagged without the comma — the same asymmetry
    as the title case, on the other half of the query."""
    db = _library_by_artist([
        ("Crosby Stills & Nash", "Suite: Judy Blue Eyes"),
        ("Stills Solo Project", "Some Other Song"),
        ("Nash Ensemble", "Another Song"),
    ])
    stub = types.SimpleNamespace(
        _normalize_for_comparison=normalize_for_comparison,
        _fuzzy_terms=MusicDatabase._fuzzy_terms,
    )
    rows = MusicDatabase._search_tracks_fuzzy_rows(
        stub, db.cursor(), '', "Crosby, Stills & Nash", 15, None)
    assert [r['artist_name'] for r in rows][0] == "Crosby Stills & Nash"


def test_the_artist_path_uses_the_same_tokeniser():
    """Both halves must trim, or one of them keeps the bug."""
    import inspect
    src = inspect.getsource(MusicDatabase._search_tracks_fuzzy_rows)
    assert src.count('self._fuzzy_terms(') == 2, "title AND artist"
