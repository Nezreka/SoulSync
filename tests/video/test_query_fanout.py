"""Title variants in the search query ladder.

Roadmap: "adaptive TV query fanout: canonical title, aliases, year/no-year,
SxxExx, season x episode y, and scene-number variants."

The reason it matters, from the live install: a wishlist title is a DATABASE
title — "Insomnia (2024)", "The Voice (AU)", "How Did They Build That? (2021)".
Scene releases are named `Insomnia.2024.S02E02`, never `Insomnia (2024) S02E02`,
so every query in the ladder inherited a dead parenthetical. Six such shows had
burnt 5,071 fruitless searches between them without a single grab — 15% of all
the searching that wishlist had ever done.
"""

from __future__ import annotations

import pytest

from core.video.retry import next_query, title_variants


def _ladder(ctx, limit=12):
    """Every query the ladder offers, in order."""
    tried, out = [], []
    while len(out) < limit:
        q = next_query(ctx, tried)
        if not q:
            break
        tried.append(q)
        out.append(q)
    return out


EP = {"scope": "episode", "season": 2, "episode": 2}


# ── the variants themselves ──────────────────────────────────────────────────

def test_a_bracketed_year_becomes_the_scene_form_and_then_disappears():
    assert title_variants({"title": "Insomnia (2024)"}) == [
        "Insomnia (2024)",      # never regress the query that exists today
        "Insomnia 2024",        # the scene form
        "Insomnia",
    ]


def test_a_country_tag_gets_the_same_treatment():
    """'The Voice (AU)' — 2,243 fruitless searches on the live install."""
    assert title_variants({"title": "The Voice (AU)"}) == [
        "The Voice (AU)", "The Voice AU", "The Voice",
    ]


def test_an_ordinary_title_produces_exactly_one_variant():
    """No brackets, nothing to vary — the shows that already worked must not
    suddenly cost extra searches out of a budget of six."""
    assert title_variants({"title": "Silo"}) == ["Silo"]


def test_aliases_come_after_the_primary_spellings():
    got = title_variants({"title": "Insomnia (2024)", "titles": ["Insomnia", "Insomnie"]})
    assert got == ["Insomnia (2024)", "Insomnia 2024", "Insomnia", "Insomnie"]


def test_variants_are_deduped_case_insensitively():
    assert title_variants({"title": "Silo", "titles": ["silo", "SILO"]}) == ["Silo"]


def test_whitespace_from_stripping_is_collapsed():
    assert "Gabby's Dollhouse" in title_variants({"title": "Gabby's Dollhouse (2021)"})


@pytest.mark.parametrize("junk", [None, "", "   ", 0])
def test_junk_titles_do_not_produce_junk_queries(junk):
    assert title_variants({"title": junk}) == []


# ── the ladder built from them ───────────────────────────────────────────────

def test_the_episode_identity_is_tried_across_every_spelling_first():
    """Identity is the strong signal and spelling the weak one, so SxxExx is
    tried against each name before falling back to other numbering."""
    got = _ladder({**EP, "title": "Insomnia (2024)"})
    assert got[:3] == ["Insomnia (2024) S02E02", "Insomnia 2024 S02E02", "Insomnia S02E02"]
    assert "Insomnia (2024) 2x02" in got


def test_an_ordinary_show_keeps_exactly_the_ladder_it_had():
    got = _ladder({**EP, "title": "Silo"})
    assert got[:2] == ["Silo S02E02", "Silo 2x02"]


def test_a_daily_show_still_leads_with_its_air_date():
    """Series type decides the identity the scene uses; title variants must not
    push a daily show's date form down the ladder."""
    got = _ladder({**EP, "title": "The Voice (AU)", "series_type": "daily",
                   "air_date": "2026-08-02"})
    assert got[0] == "The Voice (AU) 2026.08.02"


def test_season_queries_get_the_variants_too():
    got = _ladder({"scope": "season", "season": 2, "title": "Insomnia (2024)"})
    assert got[:3] == ["Insomnia (2024) S02", "Insomnia 2024 S02", "Insomnia S02"]
    assert "Insomnia (2024) Season 2" in got


def test_a_movie_falls_back_through_its_spellings():
    got = _ladder({"scope": "movie", "title": "Tower of Terror (1999)", "year": 1999})
    assert got[0] == "Tower of Terror (1999) 1999"
    assert "Tower of Terror 1999" in got and "Tower of Terror" in got


def test_the_ladder_still_ends():
    """next_query returning None is what stops the retry loop."""
    ctx = {**EP, "title": "Insomnia (2024)", "air_date": "2026-08-13"}
    got = _ladder(ctx, limit=40)
    assert next_query(ctx, got) is None


def test_nothing_is_offered_twice():
    got = _ladder({**EP, "title": "Insomnia (2024)", "air_date": "2026-08-13"}, limit=40)
    assert len(got) == len(set(got))
