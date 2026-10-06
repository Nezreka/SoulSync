"""Issue #1450 fix-round: edition preference on library pages and gap-fill.

Finding 1 — upstream ``_dedup_variant_releases`` silently defeated the
preference on the library path: ``get_artist_detail`` called
``get_artist_detail_discography`` with the default ``dedup_variants=True``,
whose hardcoded plain-title-wins policy collapses same-year variants BEFORE
``_annotate_edition_preference`` runs (Cracker Island standard (10 tracks) +
deluxe (15 tracks), same year -> only the standard survives, so a user's
``one_complete`` choice was a no-op on library pages). The fix passes
``dedup_variants=False`` when the stored preference is ``one_standard`` /
``one_complete``.

Finding 2 — gap-fill cards bypassed edition grouping: the gap endpoint never
called ``_annotate_edition_preference`` and a missing flag reads as preferred
in the modal, so a gap "Album (Deluxe)" was pre-checked even under
``one_standard``. The fix stamps gap cards from the combined base+gap
edition group and reports ``edition_superseded`` base ids whose page stamp
the combined pick overturns.

``api/artist_detail.py`` cannot be imported in the test env (transitive
``spotipy`` import), so the annotation functions are exec'd from the
committed source with the real ``core.edition_grouping`` — the tests run the
exact code the server runs.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.edition_grouping import (
    EDITION_PREFERENCE_ALL,
    EDITION_PREFERENCE_ONE_COMPLETE,
    EDITION_PREFERENCE_ONE_STANDARD,
    edition_group_key,
    reduce_edition_group,
)
from core.metadata.discography import _dedup_variant_releases

_ROOT = Path(__file__).resolve().parent.parent
_WANTED = {
    "_read_edition_settings",
    "_annotate_edition_preference",
    "_annotate_gap_edition_preference",
}


class _Cfg:
    def __init__(self, preference, prefer_explicit=True):
        self._preference = preference
        self._prefer_explicit = prefer_explicit

    def get(self, key, default=None):
        if key == "watchlist.edition_preference":
            return self._preference
        if key == "watchlist.prefer_explicit_edition":
            return self._prefer_explicit
        return default


@pytest.fixture()
def adns():
    """Namespace with the real annotation functions, config injectable."""
    src = (_ROOT / "api" / "artist_detail.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fns = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in _WANTED
    ]
    assert {n.name for n in fns} == _WANTED
    ns = {
        "EDITION_PREFERENCE_ALL": EDITION_PREFERENCE_ALL,
        "EDITION_PREFERENCE_ONE_STANDARD": EDITION_PREFERENCE_ONE_STANDARD,
        "EDITION_PREFERENCE_ONE_COMPLETE": EDITION_PREFERENCE_ONE_COMPLETE,
        "edition_group_key": edition_group_key,
        "reduce_edition_group": reduce_edition_group,
        "config_manager": None,
    }
    exec(compile(ast.Module(body=fns, type_ignores=[]),
                 "api/artist_detail.py", "exec"), ns)
    return ns


def _disc(*albums):
    return {"albums": list(albums), "eps": [], "singles": []}


def _card(card_id, title, tracks, year="2023"):
    return {"id": card_id, "title": title, "name": title,
            "track_count": tracks, "year": year,
            "release_date": f"{year}-01-01"}


# ── Finding 1: the upstream dedup defeated the preference ────────────────────

def test_variant_dedup_collapses_cracker_island_pair():
    """The empirical premise: same-year Cracker Island standard (10) +
    deluxe (15) -> only the plain-title standard survives the default
    dedup, so annotation could never pick the deluxe."""
    pair = [_card("std", "Cracker Island", 10),
            _card("dlx", "Cracker Island (Deluxe)", 15)]
    survivors = _dedup_variant_releases(pair)
    assert [r["id"] for r in survivors] == ["std"]


def test_library_path_disables_dedup_under_edition_preferences():
    """Source pin: get_artist_detail must pass dedup_variants=False when the
    stored preference is one_standard/one_complete, keeping today's default
    (True) under "all"."""
    src = (_ROOT / "api" / "artist_detail.py").read_text(encoding="utf-8")
    fn = src[src.index("def get_artist_detail("):]
    fn = fn[:fn.index("\n@bp.route")]
    assert "dedup_variants=_read_edition_settings()[0] not in (" in fn
    assert "EDITION_PREFERENCE_ONE_STANDARD" in fn
    assert "EDITION_PREFERENCE_ONE_COMPLETE" in fn


def test_annotation_picks_one_edition_when_both_survive(adns):
    """With dedup disabled both editions reach annotation and only the
    picked edition gets edition_preferred=true (one_complete -> deluxe)."""
    adns["config_manager"] = _Cfg(EDITION_PREFERENCE_ONE_COMPLETE)
    disc = _disc(_card("std", "Cracker Island", 10),
                 _card("dlx", "Cracker Island (Deluxe)", 15))
    adns["_annotate_edition_preference"](disc)
    flags = {r["id"]: r["edition_preferred"] for r in disc["albums"]}
    assert flags == {"std": False, "dlx": True}


def test_annotation_one_standard_picks_plain_title(adns):
    adns["config_manager"] = _Cfg(EDITION_PREFERENCE_ONE_STANDARD)
    disc = _disc(_card("std", "Cracker Island", 10),
                 _card("dlx", "Cracker Island (Deluxe)", 15))
    adns["_annotate_edition_preference"](disc)
    flags = {r["id"]: r["edition_preferred"] for r in disc["albums"]}
    assert flags == {"std": True, "dlx": False}


def test_annotation_all_stamps_everything_true(adns):
    adns["config_manager"] = _Cfg(EDITION_PREFERENCE_ALL)
    disc = _disc(_card("std", "Cracker Island", 10),
                 _card("dlx", "Cracker Island (Deluxe)", 15))
    adns["_annotate_edition_preference"](disc)
    assert all(r["edition_preferred"] is True for r in disc["albums"])


# ── Finding 2: gap-fill cards bypassed edition grouping ──────────────────────

def _run_gap(adns, preference, base_cards, gap_cards):
    adns["config_manager"] = _Cfg(preference)
    base = _disc(*base_cards)
    gaps = _disc(*gap_cards)
    superseded = adns["_annotate_gap_edition_preference"](base, gaps)
    return gaps["albums"], superseded


def test_gap_deluxe_unchecked_under_one_standard(adns):
    gaps, superseded = _run_gap(
        adns, EDITION_PREFERENCE_ONE_STANDARD,
        [_card("b1", "Album", 10)], [_card("g1", "Album (Deluxe)", 15)])
    assert gaps[0]["edition_preferred"] is False
    # the base pick stays correct under one_standard — nothing to overturn
    assert superseded == []


def test_gap_deluxe_checked_and_base_superseded_under_one_complete(adns):
    gaps, superseded = _run_gap(
        adns, EDITION_PREFERENCE_ONE_COMPLETE,
        [_card("b1", "Album", 10)], [_card("g1", "Album (Deluxe)", 15)])
    assert gaps[0]["edition_preferred"] is True
    # the base response stamped the standard true; the combined group
    # picked the gap deluxe, so the base stamp must flip
    assert superseded == ["b1"]


def test_gap_everything_true_under_all(adns):
    gaps, superseded = _run_gap(
        adns, EDITION_PREFERENCE_ALL,
        [_card("b1", "Album", 10)], [_card("g1", "Album (Deluxe)", 15)])
    assert gaps[0]["edition_preferred"] is True
    assert superseded == []


def test_gap_alone_in_group_is_preferred(adns):
    """A gap release with no same-key base releases is its own group pick."""
    gaps, superseded = _run_gap(
        adns, EDITION_PREFERENCE_ONE_COMPLETE,
        [_card("b1", "Other Album", 10)], [_card("g1", "Missing Album", 8)])
    assert gaps[0]["edition_preferred"] is True
    assert superseded == []


def test_gap_standard_edition_keeps_base_pick_under_one_complete(adns):
    """Base deluxe beats gap standard under one_complete: gap unchecked,
    base deluxe was the page's pick too, so nothing is superseded."""
    gaps, superseded = _run_gap(
        adns, EDITION_PREFERENCE_ONE_COMPLETE,
        [_card("b1", "Album (Deluxe)", 15)], [_card("g1", "Album", 10)])
    assert gaps[0]["edition_preferred"] is False
    assert superseded == []


def test_gap_endpoint_returns_superseded_list():
    """Source pin: the gap-fill response carries edition_superseded on the
    main path and both early returns, so the client can read it uniformly."""
    src = (_ROOT / "api" / "artist_detail.py").read_text(encoding="utf-8")
    fn = src[src.index("def get_artist_discography_gap_fill("):]
    fn = fn[:fn.index("@bp.route")]
    assert '"edition_superseded": edition_superseded' in fn
    assert fn.count('"edition_superseded"') == 3  # main + 2 early returns
    assert "_annotate_gap_edition_preference(base, gaps)" in fn


def test_gap_base_fetch_mirrors_page_dedup():
    """Source pin: the gap endpoint's base fetch uses the page's dedup
    setting so the supersede computation sees the release set the page
    annotated; other-source fetches keep the deduped default."""
    src = (_ROOT / "api" / "artist_detail.py").read_text(encoding="utf-8")
    fn = src[src.index("def get_artist_discography_gap_fill("):]
    fn = fn[:fn.index("@bp.route")]
    assert "base_dedup_variants = _read_edition_settings()[0] not in (" in fn
    assert fn.count("dedup_variants=base_dedup_variants") == 2
