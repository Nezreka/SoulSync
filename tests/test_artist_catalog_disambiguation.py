"""Same-name artist disambiguation by owned-catalog overlap (#868).

Enrichment matched artists by NAME ONLY, so for a common name ("Rone" has ~5
artists) it grabbed whichever the source ranked first — often the wrong one,
which then drove a wrong/sparse library discography. The fix: when several
candidates clear the name gate, pick the one whose catalog overlaps the albums
the user actually OWNS. These pin the source-agnostic selector.
"""

from __future__ import annotations

from core.worker_utils import (
    catalog_overlap_score,
    normalize_release_title,
    pick_artist_by_catalog,
)


# --- normalization ---------------------------------------------------------

def test_normalize_strips_editions_and_punctuation():
    assert normalize_release_title('Tohu Bohu (Deluxe Edition)') == 'tohu bohu'
    assert normalize_release_title('Mirapolis - Remastered') == 'mirapolis'
    assert normalize_release_title('Room with a View [2020]') == 'room with a view'
    assert normalize_release_title('') == ''


# --- overlap scoring -------------------------------------------------------

def test_overlap_counts_matching_owned_titles():
    owned = ['Tohu Bohu', 'Creatures', 'Mirapolis']
    cand = ['Tohu Bohu (Deluxe)', 'Creatures', 'Spanish Breakfast', 'Motion']
    assert catalog_overlap_score(owned, cand) == 2  # Tohu Bohu + Creatures


def test_overlap_zero_for_a_different_artists_catalog():
    owned = ['Tohu Bohu', 'Creatures', 'Mirapolis']
    cand = ['Some Other Record', 'Unrelated Album']
    assert catalog_overlap_score(owned, cand) == 0


def test_overlap_zero_when_either_side_empty():
    assert catalog_overlap_score([], ['A']) == 0
    assert catalog_overlap_score(['A'], []) == 0


# --- the selector ----------------------------------------------------------

def _cand(cid, titles):
    return {'id': cid, '_titles': titles}


def _fetch(cand):
    return cand['_titles']


def test_single_candidate_returns_without_fetching():
    calls = []
    chosen, score = pick_artist_by_catalog(
        [_cand('only', ['X'])], ['Tohu Bohu'],
        lambda c: calls.append(c) or c['_titles'])
    assert chosen['id'] == 'only'
    assert calls == []  # never fetched — nothing to disambiguate


def test_no_owned_albums_keeps_name_order():
    calls = []
    chosen, score = pick_artist_by_catalog(
        [_cand('first', ['A']), _cand('second', ['B'])], [],
        lambda c: calls.append(c) or c['_titles'])
    assert chosen['id'] == 'first'  # candidates[0] — current behavior
    assert calls == []


def test_picks_the_candidate_overlapping_owned_catalog():
    # The WRONG Rone is ranked first; the right one overlaps the owned albums.
    wrong = _cand('wrong', ['Rap Mixtape Vol 1', 'Some Single'])
    right = _cand('right', ['Tohu Bohu', 'Creatures', 'Mirapolis'])
    chosen, score = pick_artist_by_catalog(
        [wrong, right], ['Tohu Bohu', 'Creatures', 'Spanish Breakfast'], _fetch)
    assert chosen['id'] == 'right'
    assert score == 2


def test_no_overlap_anywhere_falls_back_to_first():
    a = _cand('a', ['Nope']); b = _cand('b', ['Also Nope'])
    chosen, score = pick_artist_by_catalog([a, b], ['Tohu Bohu'], _fetch)
    assert chosen['id'] == 'a'
    assert score == 0


def test_fetch_failure_is_tolerated():
    def _boom(_c):
        raise RuntimeError('api down')
    chosen, score = pick_artist_by_catalog(
        [_cand('a', []), _cand('b', [])], ['Tohu Bohu'], _boom)
    assert chosen['id'] == 'a'  # both fail → fall back to first
    assert score == 0
