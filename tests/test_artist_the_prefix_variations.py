"""Leading-"The" duplicate fix.

A user wanted "I Gotta Feeling" by "The Black Eyed Peas" but owned it under
"Black Eyed Peas" (or vice-versa). The dedup gate (check_track_exists) fetches
candidates via _get_artist_variations(), which had no "The" toggle — so the
owned track was never fetched, the request "failed to match", and a duplicate
was downloaded. The toggle widens the fetch to both forms; the scorer still
decides, so it can't merge genuinely different artists.
"""

from __future__ import annotations

from database.music_database import MusicDatabase


def _variations(name):
    db = object.__new__(MusicDatabase)          # no DB / network init needed
    return db._get_artist_variations(name)


def test_leading_the_is_stripped_to_search_the_bare_form():
    v = _variations("The Black Eyed Peas")
    assert "Black Eyed Peas" in v               # owned-bare form now gets fetched
    assert "The Black Eyed Peas" in v           # original kept


def test_bare_name_also_searches_the_the_prefixed_form():
    v = _variations("Black Eyed Peas")
    assert "The The Black Eyed Peas" not in v   # no double-"The"
    assert "The Black Eyed Peas" in v           # the "The"-variant gets fetched
    assert "Black Eyed Peas" in v


def test_the_band_named_just_the_never_produces_an_empty_search():
    # "The" alone must not collapse to an empty artist search (which would match
    # the entire library). Adding "The The" is harmless — the scorer still gates.
    v = _variations("The")
    assert "" not in v
    assert "The" in v


def test_a_leading_the_word_is_required_not_a_mid_word_the():
    # "Theory of a Deadman" starts with "The" but not the WORD "The" — it must not
    # be stripped mid-word. (It does still get the harmless "The "-prefixed widen.)
    v = _variations("Theory of a Deadman")
    assert "ory of a Deadman" not in v          # NOT mangled mid-word
    assert "Theory of a Deadman" in v


def test_the_toggle_lands_the_match_through_the_real_scorer():
    # End-to-end on the confidence scorer: requesting one variant against the
    # other owned variant must clear the 0.8 dedup threshold (50/50 title/artist
    # → 1.0*0.5 + 0.882*0.5 = 0.94), so it's recognized as already owned.
    db = object.__new__(MusicDatabase)

    class _Track:
        title = "I Gotta Feeling"
        artist_name = "Black Eyed Peas"
        track_artist = "Black Eyed Peas"
        album = "The E.N.D."

    conf = db._calculate_track_confidence("I Gotta Feeling", "The Black Eyed Peas", _Track())
    assert conf >= 0.8, conf

    # …and the reverse direction too.
    class _Track2:
        title = "I Gotta Feeling"
        artist_name = "The Black Eyed Peas"
        track_artist = "The Black Eyed Peas"
        album = "The E.N.D."

    conf2 = db._calculate_track_confidence("I Gotta Feeling", "Black Eyed Peas", _Track2())
    assert conf2 >= 0.8, conf2


def test_toggle_does_not_falsely_merge_different_the_artists():
    # "The Police" and "Police" are arguably the same band, but "The Weeknd" vs
    # "Weeknd" etc. — the toggle only WIDENS the fetch; the scorer still gates.
    # A clearly different artist must not score as a match just because both
    # share no "The". (Title differs too — this is the real safety net.)
    db = object.__new__(MusicDatabase)

    class _Other:
        title = "Some Other Song"
        artist_name = "Black Eyed Peas"
        track_artist = "Black Eyed Peas"
        album = "Whatever"

    conf = db._calculate_track_confidence("I Gotta Feeling", "The Killers", _Other())
    assert conf < 0.8, conf
