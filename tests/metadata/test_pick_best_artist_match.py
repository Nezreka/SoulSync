"""#988: the Deezer (and any source) artist name-search must not hand back an
unrelated artist when its results don't actually contain the one we asked for.

A browsed-but-not-owned artist (searched via a Spotify URL, primary source =
Deezer) has no stored Deezer id, so the discography/top-tracks flow name-searches
Deezer for "The Outfield". The old ``_pick_best_artist_match`` returned
``search_results[0]`` when no exact match was found — so a search that surfaced
The Beatles first stamped the whole page with Beatles albums/tracks.

The strict, name-gated matcher lives in ``album_tracks`` (used by the
discography/top-tracks lookup for a KNOWN artist). The ``discography`` copy stays
intentionally loose — its only caller is similar-artist/musicmap enrichment,
which resolves a suggestion to a source's canonical entry whose name differs on
purpose. So this only tests the strict one.
"""

from __future__ import annotations

from core.metadata.album_tracks import _pick_best_artist_match as pick


def _r(name, aid):
    return {"name": name, "id": aid}


def test_no_name_match_returns_none_not_first_result():
    # The exact #988 shape: asked for The Outfield, Deezer returned Beatles.
    results = [_r("The Beatles", "1"), _r("Beatles Tribute Band", "2")]
    assert pick(results, "The Outfield") is None


def test_exact_match_wins_even_if_not_first():
    results = [_r("The Beatles", "1"), _r("The Outfield", "99")]
    assert pick(results, "The Outfield")["id"] == "99"


def test_close_variant_still_matches():
    # casing / normalization variants are the same artist
    assert pick([_r("THE OUTFIELD", "99")], "The Outfield")["id"] == "99"


def test_empty_results_is_none():
    assert pick([], "The Outfield") is None


def test_among_exact_duplicates_prefers_largest_catalog():
    """§62.5: Deezer has five artists ALL named exactly "Hiroyuki Sawano" —
    fragments with 1-4 albums plus the real entry with the full catalog.
    First-exact-match used to pick a fragment; the catalog-size signal must
    break exact-name ties."""
    results = [
        _r("Hiroyuki Sawano", "234170331") | {"nb_album": 4, "nb_fan": 50},
        _r("Hiroyuki Sawano", "1315147") | {"nb_album": 104, "nb_fan": 24000},
        _r("Hiroyuki Sawano", "218685045") | {"nb_album": 1},
    ]
    assert pick(results, "Hiroyuki Sawano")["id"] == "1315147"


def test_spotify_follower_shape_breaks_exact_ties():
    results = [
        _r("Hiroyuki Sawano", "fragment") | {"followers": {"total": 10}},
        _r("Hiroyuki Sawano", "real") | {"followers": {"total": 500000}},
    ]
    assert pick(results, "Hiroyuki Sawano")["id"] == "real"


def test_single_exact_match_without_signals_still_wins():
    results = [_r("The Beatles", "1"), _r("The Outfield", "99")]
    assert pick(results, "The Outfield")["id"] == "99"
